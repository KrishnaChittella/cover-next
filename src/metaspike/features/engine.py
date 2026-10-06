"""The feature engine: one implementation for every bucketed signal.

The engine folds a contiguous series of buckets, one at a time, through a state. It
knows nothing about Steam, Wikimedia or where the buckets came from; everything
signal-specific is in a `SignalFeatureConfig`.

Each bucket t is handled in a fixed order, so that nothing can depend on itself:

    1. the state holds only buckets before t
    2. the baselines and the expected value for t are computed from that state
    3. bucket t is compared with them: lift, residual, robust z, percentile
    4. the smoothed lift and the change-point sums are updated with t
    5. if a change point is confirmed, its buckets are marked as a surge
    6. t is appended to the state, which is now valid through t

A bucket therefore never influences the baseline it is judged against, and a surge
changes only what LATER buckets are compared with. An evaluation, once produced, is
never revisited.

The state is honest about what it must carry. A median, a MAD and a percentile have
no compact sufficient statistic, so the state holds the bounded window of buckets
they are computed from, and seasonal profiles are recomputed from that window. The
same fold therefore gives the same answer whether it runs over full history or
resumes from a serialised state.

Mathematics is done on log1p(value). Missing buckets are carried as missing: they
are never zero, never interpolated, and never filled.
"""

import math
import statistics
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from metaspike.domain import baseline_rules as rules
from metaspike.domain.families import SourceFamily
from metaspike.domain.model import FrozenModel
from metaspike.domain.timeseries import Granularity
from metaspike.features.config import (
    BaselineWindow,
    SeasonalityBasis,
    SignalFeatureConfig,
    TrendConfig,
)
from metaspike.features.robust import (
    clip,
    median_and_mad,
    mid_rank_percentile,
    robust_scale,
    theil_sen_slope,
)

GAP_RESET = "gap_reset"
REVERTED = "reverted"
FREEZE_EXPIRED = "freeze_expired"


class Maturity(StrEnum):
    NO_HISTORY = "no_history"
    WARMING_UP = "warming_up"
    PARTIAL_BASELINE = "partial_baseline"
    MATURE = "mature"


class Direction(StrEnum):
    UP = "up"
    DOWN = "down"


class ChangePhase(StrEnum):
    NONE = "none"
    # A confirmed shift is in progress; its buckets are kept out of later baselines.
    ACTIVE = "active"
    # The freeze has ended and the new level is entering the baseline.
    ABSORBING = "absorbing"


class ResidualBasis(StrEnum):
    SAME = "same"  # residuals measured against the same kind of expectation
    MIXED = "mixed"  # too few of those yet: residuals against any expectation


class Bucket(FrozenModel):
    """One period of one signal. `value` is None when the period has no valid value."""

    start: AwareDatetime
    value: float | None = Field(default=None, ge=0)
    samples: int = Field(ge=0)
    expected_samples: int = Field(ge=1)

    @property
    def coverage(self) -> float:
        return round(min(1.0, self.samples / self.expected_samples), 4)


class FeatureValue(FrozenModel):
    """A number, or the reason there is none. Never both and never neither."""

    value: float | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _one_or_the_other(self) -> Self:
        if (self.value is None) == (self.reason is None):
            raise ValueError("a feature has a value or a reason, exactly one")
        return self


def have(value: float) -> FeatureValue:
    return FeatureValue(value=value)


def lack(reason: str) -> FeatureValue:
    return FeatureValue(reason=reason)


class BaselineStat(FrozenModel):
    """Robust statistics of one trailing window, in log1p units."""

    name: str
    window_start: AwareDatetime | None
    window_end: AwareDatetime | None  # exclusive: always at or before the evaluated bucket
    valid_count: int
    expected_count: int
    coverage: float
    valid_days: int | None = None
    excluded_count: int = 0
    median: float | None = None
    mad: float | None = None
    robust_scale: float | None = None
    mean: float | None = None
    std: float | None = None
    # The median back in the signal's own unit.
    median_level: float | None = None
    reason: str | None = None

    @property
    def available(self) -> bool:
        return self.median is not None


class ChangePoint(FrozenModel):
    """One confirmed shift, with what was seen when it was confirmed."""

    direction: Direction
    started_at: AwareDatetime
    confirmed_at: AwareDatetime
    evidence_sum: float
    evidence_z: tuple[float, ...]
    freeze_until: AwareDatetime
    ended_at: AwareDatetime | None = None
    ended_reason: str | None = None


class ChangePointView(FrozenModel):
    supported: bool
    phase: ChangePhase = ChangePhase.NONE
    sum_up: float = 0.0
    sum_down: float = 0.0
    candidate_up_since: AwareDatetime | None = None
    candidate_down_since: AwareDatetime | None = None
    # The shift in progress, or the one being absorbed.
    current: ChangePoint | None = None
    # A change confirmed while evaluating this very bucket.
    confirmed_now: bool = False
    updated: bool = False  # whether this bucket was fed to the sums
    last_reset_reason: str | None = None
    last_reset_at: AwareDatetime | None = None
    reason: str | None = None


class BucketEvaluation(FrozenModel):
    """Everything computed for one bucket, against history strictly before it."""

    rule_version: str
    bucket_start: AwareDatetime
    valid_through: AwareDatetime
    level: float | None
    samples: int
    expected_samples: int
    coverage: float
    maturity: Maturity
    baselines: tuple[BaselineStat, ...]
    expected_level: FeatureValue
    seasonality_basis: SeasonalityBasis | None
    seasonal_instances: int = 0
    absolute_change: FeatureValue
    log_lift: FeatureValue
    percentage_lift: FeatureValue
    robust_z: FeatureValue
    residual_basis: ResidualBasis | None = None
    residual_scale: float | None = None
    residual_centre: float | None = None
    anomaly_strength: FeatureValue
    trailing_percentile: FeatureValue
    percentile_samples: int = 0
    lift_fast: FeatureValue
    lift_slow: FeatureValue
    velocity: FeatureValue
    acceleration: FeatureValue
    trend_short: FeatureValue
    trend_long: FeatureValue
    trend_short_points: int = 0
    trend_long_points: int = 0
    change_point: ChangePointView

    def baseline(self, name: str) -> BaselineStat:
        return next(stat for stat in self.baselines if stat.name == name)


class WindowEntry(FrozenModel):
    """One past bucket as the state remembers it."""

    start: AwareDatetime
    value: float | None = None
    samples: int = 0
    # Part of a confirmed surge: a valid observation that later baselines leave out.
    excluded: bool = False
    basis: SeasonalityBasis | None = None
    residual: float | None = None
    lift_fast: float | None = None
    velocity: float | None = None


class EwmaState(FrozenModel):
    fast: float | None = None
    slow: float | None = None
    updates: int = 0
    last_start: AwareDatetime | None = None


class CusumState(FrozenModel):
    sum_up: float = 0.0
    sum_down: float = 0.0
    candidate_up_since: AwareDatetime | None = None
    candidate_down_since: AwareDatetime | None = None
    recent_z: tuple[float, ...] = ()
    phase: ChangePhase = ChangePhase.NONE
    current: ChangePoint | None = None
    absorb_until: AwareDatetime | None = None
    last_reset_reason: str | None = None
    last_reset_at: AwareDatetime | None = None
    # Confirmed changes, oldest first, so a past decision can be reproduced.
    history: tuple[ChangePoint, ...] = ()


class BaselineState(FrozenModel):
    """Everything needed to continue evaluating a signal without its full history.

    The window is bounded by `config.history_buckets`. It is carried in full because
    medians, MADs and percentiles cannot be continued from summary numbers; seasonal
    profiles and their counts are recomputed from it. EWMA and change-point state are
    true running state and are carried as such.
    """

    subject: str
    source: str
    metric: str
    granularity: Granularity
    source_family: SourceFamily
    rule_version: str
    config_fingerprint: str
    # End of the last bucket folded in (exclusive). None before the first bucket.
    valid_through: AwareDatetime | None = None
    window: tuple[WindowEntry, ...] = ()
    ewma: EwmaState = EwmaState()
    cusum: CusumState = CusumState()
    gap_run: int = 0
    observed_ever: bool = False
    last_valid_start: AwareDatetime | None = None
    latest: BucketEvaluation | None = None


class StateMismatchError(ValueError):
    """A state was continued under a different configuration, or out of order."""


def initial_state(config: SignalFeatureConfig, subject: str) -> BaselineState:
    return BaselineState(
        subject=subject,
        source=config.source,
        metric=config.metric,
        granularity=config.granularity,
        source_family=config.source_family,
        rule_version=config.rule_version,
        config_fingerprint=config.fingerprint,
    )


@dataclass
class _Entry:
    start: datetime
    value: float | None
    samples: int
    excluded: bool = False
    basis: SeasonalityBasis | None = None
    residual: float | None = None
    lift_fast: float | None = None
    velocity: float | None = None
    y: float | None = field(init=False)

    def __post_init__(self) -> None:
        self.y = None if self.value is None else math.log1p(self.value)


@dataclass
class _Work:
    """The state while a fold is running. Converted back to a BaselineState at the end."""

    window: deque[_Entry]
    ewma_fast: float | None
    ewma_slow: float | None
    ewma_updates: int
    ewma_last: datetime | None
    sum_up: float
    sum_down: float
    candidate_up: datetime | None
    candidate_down: datetime | None
    recent_z: deque[float]
    phase: ChangePhase
    current: ChangePoint | None
    absorb_until: datetime | None
    reset_reason: str | None
    reset_at: datetime | None
    history: list[ChangePoint]
    gap_run: int
    observed_ever: bool
    last_valid_start: datetime | None
    valid_through: datetime | None


def run(
    config: SignalFeatureConfig, state: BaselineState, buckets: Iterable[Bucket]
) -> tuple[BaselineState, tuple[BucketEvaluation, ...]]:
    """Fold `buckets` into `state`. Returns the new state and one evaluation per bucket.

    Buckets must be contiguous and continue exactly where the state ends. The state
    must have been produced under this same configuration.
    """
    if state.config_fingerprint != config.fingerprint or state.rule_version != config.rule_version:
        raise StateMismatchError(
            f"state was built under {state.rule_version}/{state.config_fingerprint}, "
            f"not {config.rule_version}/{config.fingerprint}"
        )
    work = _thaw(config, state)
    evaluations = []
    latest = state.latest
    for bucket in buckets:
        latest = _step(config, work, bucket)
        evaluations.append(latest)
    return _freeze(state, work, latest), tuple(evaluations)


def _thaw(config: SignalFeatureConfig, state: BaselineState) -> _Work:
    confirm_of = config.cusum.confirm_of if config.cusum else 1
    cusum = state.cusum
    return _Work(
        window=deque(
            (
                _Entry(
                    start=e.start,
                    value=e.value,
                    samples=e.samples,
                    excluded=e.excluded,
                    basis=e.basis,
                    residual=e.residual,
                    lift_fast=e.lift_fast,
                    velocity=e.velocity,
                )
                for e in state.window
            ),
            maxlen=config.history_buckets,
        ),
        ewma_fast=state.ewma.fast,
        ewma_slow=state.ewma.slow,
        ewma_updates=state.ewma.updates,
        ewma_last=state.ewma.last_start,
        sum_up=cusum.sum_up,
        sum_down=cusum.sum_down,
        candidate_up=cusum.candidate_up_since,
        candidate_down=cusum.candidate_down_since,
        recent_z=deque(cusum.recent_z, maxlen=confirm_of),
        phase=cusum.phase,
        current=cusum.current,
        absorb_until=cusum.absorb_until,
        reset_reason=cusum.last_reset_reason,
        reset_at=cusum.last_reset_at,
        history=list(cusum.history),
        gap_run=state.gap_run,
        observed_ever=state.observed_ever,
        last_valid_start=state.last_valid_start,
        valid_through=state.valid_through,
    )


def _freeze(state: BaselineState, work: _Work, latest: BucketEvaluation | None) -> BaselineState:
    return state.model_copy(
        update={
            "valid_through": work.valid_through,
            "window": tuple(
                WindowEntry(
                    start=e.start,
                    value=e.value,
                    samples=e.samples,
                    excluded=e.excluded,
                    basis=e.basis,
                    residual=e.residual,
                    lift_fast=e.lift_fast,
                    velocity=e.velocity,
                )
                for e in work.window
            ),
            "ewma": EwmaState(
                fast=work.ewma_fast,
                slow=work.ewma_slow,
                updates=work.ewma_updates,
                last_start=work.ewma_last,
            ),
            "cusum": CusumState(
                sum_up=work.sum_up,
                sum_down=work.sum_down,
                candidate_up_since=work.candidate_up,
                candidate_down_since=work.candidate_down,
                recent_z=tuple(work.recent_z),
                phase=work.phase,
                current=work.current,
                absorb_until=work.absorb_until,
                last_reset_reason=work.reset_reason,
                last_reset_at=work.reset_at,
                history=tuple(work.history),
            ),
            "gap_run": work.gap_run,
            "observed_ever": work.observed_ever,
            "last_valid_start": work.last_valid_start,
            "latest": latest,
        }
    )


# --- one bucket -----------------------------------------------------------------------


def _step(config: SignalFeatureConfig, work: _Work, bucket: Bucket) -> BucketEvaluation:
    step = config.granularity.step
    start = bucket.start
    if not config.granularity.is_aligned(start):
        raise StateMismatchError("a bucket does not start on a period boundary")
    if work.valid_through is not None and start != work.valid_through:
        raise StateMismatchError(
            f"buckets must be contiguous: expected {work.valid_through.isoformat()}, "
            f"got {start.isoformat()}"
        )
    _advance_phase(config, work, start)

    # Steps 1-2: everything here reads only buckets before `start`.
    history = list(work.window)
    days = _day_table(config, history, start)
    baselines = tuple(
        _baseline(config, window, history, days, start) for window in config.baseline_windows
    )
    expected_y, basis, instances = _expectation(config, history, baselines, start)
    maturity = _maturity(config, work, bucket, baselines, basis)
    no_expectation = _why_no_expectation(config, work, baselines)
    trend_short, short_points = _trend(config.trend_short, days)
    trend_long, long_points = _trend(config.trend_long, days)

    entry = _Entry(start=start, value=bucket.value, samples=bucket.samples)
    unsupported = lack(rules.UNSUPPORTED_FOR_GRANULARITY)
    confirmed_now = updated = False

    if bucket.value is None or entry.y is None:
        work.gap_run += 1
        _reset_after_long_gap(config, work, start)
        gone = lack(rules.GAP if work.observed_ever else rules.NO_HISTORY)
        absolute = log_lift = percentage = robust_z = strength = percentile = gone
        lift_fast = lift_slow = gone if config.ewma else unsupported
        velocity = acceleration = (
            gone if config.ewma and config.velocity_span_buckets else unsupported
        )
        if config.percentile is None:
            percentile = unsupported
        percentile_samples = 0
        residual_basis = scale = centre = None
    else:
        # Step 3: compare the bucket with what was expected of it.
        work.gap_run = 0
        value, y = bucket.value, entry.y
        if expected_y is None:
            absolute = log_lift = percentage = robust_z = strength = lack(no_expectation)
            residual_basis = scale = centre = None
            z = None
        else:
            expected = math.expm1(expected_y)
            lift = y - expected_y
            entry.basis, entry.residual = basis, lift
            absolute, log_lift = have(value - expected), have(lift)
            percentage = (
                have(100 * (value - expected) / expected)
                if expected > config.low_base_floor
                else lack(rules.LOW_BASE_REASON)
            )
            z, residual_basis, centre, scale = _standardise(config, history, basis, lift)
            if z is None:
                robust_z = strength = lack(rules.INSUFFICIENT_HISTORY)
            else:
                robust_z = have(z)
                strength = have(max(0.0, min(1.0, z / config.anomaly_z_saturation)))
        percentile, percentile_samples = _percentile(config, history, value)

        # Step 4: update the running state with this bucket.
        lift_fast, lift_slow = _update_ewma(config, work, entry, log_lift.value)
        velocity = _velocity(config, history, entry)
        acceleration = _acceleration(config, history, entry, velocity)
        # Steps 4-5: change-point sums, confirmation, and marking the surge.
        updated, confirmed_now = _update_cusum(
            config, work, entry, z, basis, residual_basis, history
        )
        work.observed_ever = True
        work.last_valid_start = start

    # Step 6: only now does the bucket join the history.
    work.window.append(entry)
    work.valid_through = start + step
    return BucketEvaluation(
        rule_version=config.rule_version,
        bucket_start=start,
        valid_through=start + step,
        level=bucket.value,
        samples=bucket.samples,
        expected_samples=bucket.expected_samples,
        coverage=bucket.coverage,
        maturity=maturity,
        baselines=baselines,
        expected_level=(
            have(math.expm1(expected_y)) if expected_y is not None else lack(no_expectation)
        ),
        seasonality_basis=basis,
        seasonal_instances=instances,
        absolute_change=absolute,
        log_lift=log_lift,
        percentage_lift=percentage,
        robust_z=robust_z,
        residual_basis=residual_basis,
        residual_scale=scale,
        residual_centre=centre,
        anomaly_strength=strength,
        trailing_percentile=percentile,
        percentile_samples=percentile_samples,
        lift_fast=lift_fast,
        lift_slow=lift_slow,
        velocity=velocity,
        acceleration=acceleration,
        trend_short=trend_short,
        trend_long=trend_long,
        trend_short_points=short_points,
        trend_long_points=long_points,
        change_point=_change_view(config, work, confirmed_now, updated),
    )


# --- days, baselines and expectation --------------------------------------------------


@dataclass
class _Day:
    valid_buckets: int = 0
    samples: int = 0
    values: list[float] = field(default_factory=list)
    valid: bool = False


def _day_table(
    config: SignalFeatureConfig, history: list[_Entry], start: datetime
) -> dict[int, _Day]:
    """Whole UTC days before the day of `start`, keyed by how many days back they are."""
    today = Granularity.DAILY.floor(start)
    table: dict[int, _Day] = {}
    for entry in history:
        back = (today - Granularity.DAILY.floor(entry.start)).days
        if back < 1:
            continue  # the evaluated bucket's own day is not complete
        day = table.setdefault(back, _Day())
        day.samples += entry.samples
        if entry.value is not None:
            day.valid_buckets += 1
            day.values.append(entry.value)
    for day in table.values():
        day.valid = (
            day.valid_buckets >= config.min_valid_buckets_per_day
            and day.samples >= config.min_samples_per_valid_day
        )
    return table


def _valid_days(days: dict[int, _Day], span: int) -> int:
    return sum(1 for back, day in days.items() if back <= span and day.valid)


def _baseline(
    config: SignalFeatureConfig,
    window: BaselineWindow,
    history: list[_Entry],
    days: dict[int, _Day],
    start: datetime,
) -> BaselineStat:
    entries = history[-window.buckets :]
    valid = [entry for entry in entries if entry.y is not None]
    usable = [entry.y for entry in valid if not entry.excluded and entry.y is not None]
    valid_days = None if window.days is None else _valid_days(days, window.days)
    enough = (
        len(valid) >= window.min_valid_buckets
        if window.min_valid_buckets is not None
        else valid_days is not None
        and window.min_valid_days is not None
        and valid_days >= window.min_valid_days
    )
    common = {
        "name": window.name,
        "window_start": start - window.buckets * config.granularity.step,
        "window_end": start,
        "valid_count": len(valid),
        "expected_count": window.buckets,
        "coverage": round(len(valid) / window.buckets, 4),
        "valid_days": valid_days,
        "excluded_count": len(valid) - len(usable),
    }
    if not enough or len(usable) < 2:
        reason = window.unavailable_reason if history else rules.NO_HISTORY
        return BaselineStat(**common, reason=reason)
    median, mad = median_and_mad(usable)
    mean = std = None
    if window.emit_mean_std and len(usable) == len(valid):
        mean, std = statistics.fmean(usable), statistics.stdev(usable)
    return BaselineStat(
        **common,
        median=median,
        mad=mad,
        robust_scale=robust_scale(mad, config.scale_floor),
        mean=mean,
        std=std,
        median_level=math.expm1(median),
    )


def _slot(basis: SeasonalityBasis, moment: datetime) -> tuple[int, int]:
    if basis is SeasonalityBasis.HOUR_OF_WEEK:
        return moment.weekday(), moment.hour
    if basis is SeasonalityBasis.HOUR_OF_DAY:
        return 0, moment.hour
    return moment.weekday(), 0


def _expectation(
    config: SignalFeatureConfig,
    history: list[_Entry],
    baselines: tuple[BaselineStat, ...],
    start: datetime,
) -> tuple[float | None, SeasonalityBasis | None, int]:
    """The expected log level for the bucket at `start`, and what it rests on."""
    for profile in config.seasonality:
        slot = _slot(profile.basis, start)
        instances = [
            entry.y
            for entry in history[-profile.lookback_buckets :]
            if entry.y is not None
            and not entry.excluded
            and _slot(profile.basis, entry.start) == slot
        ]
        if len(instances) >= profile.min_instances:
            return statistics.median(instances), profile.basis, len(instances)
    for stat in reversed(baselines):  # the longest available window speaks first
        if stat.median is not None:
            return stat.median, SeasonalityBasis.TRAILING_LEVEL, stat.valid_count
    return None, None, 0


def _why_no_expectation(
    config: SignalFeatureConfig, work: _Work, baselines: tuple[BaselineStat, ...]
) -> str:
    if not work.observed_ever:
        return rules.NO_HISTORY
    return baselines[0].reason or config.baseline_windows[0].unavailable_reason


def _gate_met(baselines: tuple[BaselineStat, ...], name: str) -> bool:
    return next(stat for stat in baselines if stat.name == name).available


def _maturity(
    config: SignalFeatureConfig,
    work: _Work,
    bucket: Bucket,
    baselines: tuple[BaselineStat, ...],
    basis: SeasonalityBasis | None,
) -> Maturity:
    if not work.observed_ever and bucket.value is None:
        return Maturity.NO_HISTORY
    if not _gate_met(baselines, config.maturity_short_window):
        return Maturity.WARMING_UP
    preferred = config.seasonality[0].basis if config.seasonality else basis
    if _gate_met(baselines, config.maturity_long_window) and basis is preferred:
        return Maturity.MATURE
    return Maturity.PARTIAL_BASELINE


def _standardise(
    config: SignalFeatureConfig,
    history: list[_Entry],
    basis: SeasonalityBasis | None,
    residual: float,
) -> tuple[float | None, ResidualBasis | None, float | None, float | None]:
    """The residual measured against the recent residuals that came before it.

    Centring on the trailing median of residuals removes a steady bias, which is what
    a gradual trend produces, so a trend is not mistaken for a shift.
    """
    recent = [
        entry
        for entry in history[-config.residual_window_buckets :]
        if entry.residual is not None and not entry.excluded
    ]
    same = [
        entry.residual for entry in recent if entry.basis is basis and entry.residual is not None
    ]
    if len(same) >= config.min_residuals:
        chosen, kind = same, ResidualBasis.SAME
    elif len(recent) >= config.min_residuals:
        chosen = [entry.residual for entry in recent if entry.residual is not None]
        kind = ResidualBasis.MIXED
    else:
        return None, None, None, None
    centre, mad = median_and_mad(chosen)
    scale = robust_scale(mad, config.scale_floor)
    return (residual - centre) / scale, kind, centre, scale


def _percentile(
    config: SignalFeatureConfig, history: list[_Entry], value: float
) -> tuple[FeatureValue, int]:
    if config.percentile is None:
        return lack(rules.UNSUPPORTED_FOR_GRANULARITY), 0
    prior = [
        entry.value
        for entry in history[-config.percentile.lookback_buckets :]
        if entry.value is not None
    ]
    if len(prior) < config.percentile.min_samples:
        return lack(rules.INSUFFICIENT_HISTORY if prior else rules.NO_HISTORY), len(prior)
    return have(mid_rank_percentile(value, prior)), len(prior)


def _trend(config: TrendConfig | None, days: dict[int, _Day]) -> tuple[FeatureValue, int]:
    """Theil-Sen slope of the daily typical level, in log units per day."""
    if config is None:
        return lack(rules.UNSUPPORTED_FOR_GRANULARITY), 0
    points = [
        (float(-back), math.log1p(statistics.median(day.values)))
        for back, day in sorted(days.items(), reverse=True)
        if back <= config.days and day.valid
    ]
    if len(points) < config.min_points:
        return lack(rules.INSUFFICIENT_HISTORY if points else rules.NO_HISTORY), len(points)
    return have(theil_sen_slope(points)), len(points)


# --- dynamics -------------------------------------------------------------------------


def _update_ewma(
    config: SignalFeatureConfig, work: _Work, entry: _Entry, lift: float | None
) -> tuple[FeatureValue, FeatureValue]:
    if config.ewma is None:
        unsupported = lack(rules.UNSUPPORTED_FOR_GRANULARITY)
        return unsupported, unsupported
    if lift is None:
        missing = lack(rules.INSUFFICIENT_HISTORY)
        return missing, missing
    # Time-aware: after a gap the old value has decayed for every bucket that passed.
    elapsed = (
        1
        if work.ewma_last is None
        else max(1, (entry.start - work.ewma_last) // config.granularity.step)
    )

    current: float = lift

    def blend(previous: float | None, half_life: float) -> float:
        if previous is None:
            return current
        keep: float = 0.5 ** (elapsed / half_life)
        return keep * previous + (1 - keep) * current

    work.ewma_fast = blend(work.ewma_fast, config.ewma.fast_half_life_buckets)
    work.ewma_slow = blend(work.ewma_slow, config.ewma.slow_half_life_buckets)
    work.ewma_updates += 1
    work.ewma_last = entry.start

    def ready(value: float, half_life: float) -> FeatureValue:
        assert config.ewma is not None
        needed = math.ceil(config.ewma.min_half_lives * half_life)
        return have(value) if work.ewma_updates >= needed else lack(rules.INSUFFICIENT_HISTORY)

    fast = ready(work.ewma_fast, config.ewma.fast_half_life_buckets)
    entry.lift_fast = fast.value
    return fast, ready(work.ewma_slow, config.ewma.slow_half_life_buckets)


def _span(
    config: SignalFeatureConfig, history: list[_Entry]
) -> tuple[list[_Entry] | None, str | None]:
    """The buckets one comparison span back, if every one of them has a value."""
    span = config.velocity_span_buckets
    assert span is not None
    if len(history) < span:
        return None, rules.INSUFFICIENT_HISTORY
    back = history[-span:]
    if any(entry.value is None for entry in back):
        return None, rules.GAP
    return back, None


def _velocity(config: SignalFeatureConfig, history: list[_Entry], entry: _Entry) -> FeatureValue:
    """Change in smoothed lift over the comparison span, per day."""
    if config.ewma is None or config.velocity_span_buckets is None:
        return lack(rules.UNSUPPORTED_FOR_GRANULARITY)
    back, reason = _span(config, history)
    if back is None:
        return lack(reason or rules.GAP)
    if entry.lift_fast is None or back[0].lift_fast is None:
        return lack(rules.INSUFFICIENT_HISTORY)
    span_days = config.velocity_span_buckets / config.buckets_per_day
    entry.velocity = (entry.lift_fast - back[0].lift_fast) / span_days
    return have(entry.velocity)


def _acceleration(
    config: SignalFeatureConfig, history: list[_Entry], entry: _Entry, velocity: FeatureValue
) -> FeatureValue:
    """Change in velocity over the same span, per day squared."""
    if config.ewma is None or config.velocity_span_buckets is None:
        return lack(rules.UNSUPPORTED_FOR_GRANULARITY)
    if velocity.value is None:
        return lack(velocity.reason or rules.INSUFFICIENT_HISTORY)
    back, reason = _span(config, history)
    if back is None:
        return lack(reason or rules.GAP)
    if back[0].velocity is None:
        return lack(rules.INSUFFICIENT_HISTORY)
    span_days = config.velocity_span_buckets / config.buckets_per_day
    return have((velocity.value - back[0].velocity) / span_days)


# --- change points --------------------------------------------------------------------


def _advance_phase(config: SignalFeatureConfig, work: _Work, start: datetime) -> None:
    """Time-based transitions, checked before every bucket, valid or not."""
    if config.cusum is None:
        return
    if work.phase is ChangePhase.ACTIVE and work.current is not None:
        if start >= work.current.freeze_until:
            _end_current(work, start, FREEZE_EXPIRED)
            work.phase = ChangePhase.ABSORBING
            work.absorb_until = start + config.residual_window_buckets * config.granularity.step
    elif (
        work.phase is ChangePhase.ABSORBING
        and work.absorb_until is not None
        and start >= work.absorb_until
    ):
        work.phase, work.current, work.absorb_until = ChangePhase.NONE, None, None


def _end_current(work: _Work, at: datetime, reason: str) -> None:
    assert work.current is not None
    ended = work.current.model_copy(update={"ended_at": at, "ended_reason": reason})
    work.history[-1] = ended
    work.current = ended


def _clear_sums(work: _Work) -> None:
    work.sum_up = work.sum_down = 0.0
    work.candidate_up = work.candidate_down = None


def _reset_after_long_gap(config: SignalFeatureConfig, work: _Work, start: datetime) -> None:
    if config.cusum is None or work.gap_run != config.cusum.reset_after_gap_buckets:
        return
    if not work.observed_ever:
        return  # nothing has been accumulated yet, so there is nothing to reset
    _clear_sums(work)
    work.recent_z.clear()
    work.reset_reason, work.reset_at = GAP_RESET, start


def _update_cusum(
    config: SignalFeatureConfig,
    work: _Work,
    entry: _Entry,
    z: float | None,
    basis: SeasonalityBasis | None,
    residual_basis: ResidualBasis | None,
    history: list[_Entry],
) -> tuple[bool, bool]:
    """Feed one standardised residual to the sums. Returns (updated, confirmed_now)."""
    cusum = config.cusum
    if cusum is None:
        return False, False
    usable = z is not None and basis in cusum.allowed_bases and residual_basis is ResidualBasis.SAME
    if work.phase is ChangePhase.ACTIVE:
        assert work.current is not None
        if usable and z is not None:
            work.recent_z.append(z)
            up = work.current.direction is Direction.UP
            back = sum(
                1 for seen in work.recent_z if (seen <= cusum.slack if up else seen >= -cusum.slack)
            )
            if len(work.recent_z) == cusum.confirm_of and back >= cusum.confirm_count:
                _end_current(work, entry.start, REVERTED)
                work.phase, work.current = ChangePhase.NONE, None
                return False, False
        entry.excluded = True  # kept out of every LATER baseline
        return False, False
    if work.phase is ChangePhase.ABSORBING or not usable or z is None:
        return False, False

    clipped = clip(z, cusum.clip)
    work.recent_z.append(z)
    for direction in (Direction.UP, Direction.DOWN):
        signed = clipped if direction is Direction.UP else -clipped
        total = max(
            0.0,
            (work.sum_up if direction is Direction.UP else work.sum_down) + signed - cusum.slack,
        )
        since = work.candidate_up if direction is Direction.UP else work.candidate_down
        since = None if total == 0 else (since or entry.start)
        if direction is Direction.UP:
            work.sum_up, work.candidate_up = total, since
        else:
            work.sum_down, work.candidate_down = total, since

    for direction, total, since in (
        (Direction.UP, work.sum_up, work.candidate_up),
        (Direction.DOWN, work.sum_down, work.candidate_down),
    ):
        same_side = sum(
            1
            for seen in work.recent_z
            if (seen > cusum.slack if direction is Direction.UP else seen < -cusum.slack)
        )
        if total >= cusum.threshold and same_side >= cusum.confirm_count and since is not None:
            change = ChangePoint(
                direction=direction,
                started_at=since,
                confirmed_at=entry.start,
                evidence_sum=total,
                evidence_z=tuple(work.recent_z),
                freeze_until=since + cusum.freeze_max_buckets * config.granularity.step,
            )
            for past in history:
                if past.start >= since:
                    past.excluded = True
            entry.excluded = True
            work.phase, work.current = ChangePhase.ACTIVE, change
            work.history.append(change)
            del work.history[:-16]
            _clear_sums(work)
            return True, True
    return True, False


def _change_view(
    config: SignalFeatureConfig, work: _Work, confirmed_now: bool, updated: bool
) -> ChangePointView:
    if config.cusum is None:
        return ChangePointView(supported=False, reason=rules.UNSUPPORTED_FOR_GRANULARITY)
    return ChangePointView(
        supported=True,
        phase=work.phase,
        sum_up=work.sum_up,
        sum_down=work.sum_down,
        candidate_up_since=work.candidate_up,
        candidate_down_since=work.candidate_down,
        current=work.current,
        confirmed_now=confirmed_now,
        updated=updated,
        last_reset_reason=work.reset_reason,
        last_reset_at=work.reset_at,
    )


def contiguous(
    start: datetime, values: Iterable[float | None], config: SignalFeatureConfig
) -> list[Bucket]:
    """Buckets from `start`, one per value. None is a missing bucket, not a zero."""
    step: timedelta = config.granularity.step
    return [
        Bucket(
            start=start + index * step,
            value=value,
            samples=0 if value is None else config.expected_samples_per_bucket,
            expected_samples=config.expected_samples_per_bucket,
        )
        for index, value in enumerate(values)
    ]
