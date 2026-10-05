"""Attention features from a DAILY series, each game measured against its own normal.

These definitions are specific to daily granularity; an intraday series is rejected
rather than silently read as if each period were a day.

All windows are anchored on `t`, the latest day that has an observation. Missing
days are never treated as zero: they are left out of every window, and a window
with too few observed days yields no value (None) rather than a guess.

    recent short window   [t-2,  t]     3 days
    recent long window    [t-6,  t]     7 days
    prior short window    [t-5,  t-3]   3 days
    baseline window       [t-34, t-7]   28 days, ending before the recent long window

The baseline deliberately excludes the most recent 7 days so that a spike in
progress cannot inflate the "normal" it is being compared against.
"""

import statistics
from datetime import date
from enum import StrEnum
from typing import Literal

from metaspike.domain.model import FrozenModel
from metaspike.domain.observations import TimeSeries
from metaspike.domain.timeseries import Granularity

SHORT_WINDOW_DAYS = 3
LONG_WINDOW_DAYS = 7
BASELINE_DAYS = 28
HISTORY_DAYS = 90

MIN_SHORT_SAMPLES = 2
MIN_LONG_SAMPLES = 5
MIN_BASELINE_SAMPLES = 14
MIN_PERCENTILE_SAMPLES = 30

# Constants that make MAD and mean absolute deviation consistent estimators of the
# standard deviation for normally distributed data.
MAD_TO_SIGMA = 1.4826
MEAN_AD_TO_SIGMA = 1.2533


class FeatureFlag(StrEnum):
    NO_OBSERVATIONS = "no_observations"
    INSUFFICIENT_RECENT_DATA = "insufficient_recent_data"
    INSUFFICIENT_HISTORY = "insufficient_history"
    ZERO_BASELINE = "zero_baseline"
    ZERO_DISPERSION = "zero_dispersion"
    VELOCITY_UNDEFINED = "velocity_undefined"
    STALE = "stale"
    HAS_MISSING_DAYS = "has_missing_days"


class AttentionFeatures(FrozenModel):
    as_of_day: date | None = None
    window_days: int
    observed_days: int
    history_span_days: int = 0
    missing_days: int = 0
    staleness_days: int | None = None

    latest_views: float | None = None
    avg_3d: float | None = None
    avg_7d: float | None = None
    prior_avg_3d: float | None = None
    recent_7d_samples: int = 0

    baseline_samples: int = 0
    baseline_mean_28d: float | None = None
    baseline_median_28d: float | None = None
    baseline_std_28d: float | None = None
    baseline_mad_28d: float | None = None

    lift_ratio: float | None = None
    lift_pct: float | None = None
    lift_7d_pct: float | None = None
    velocity_ratio: float | None = None
    velocity_pct: float | None = None
    consecutive_rising_days: int = 0

    z_score: float | None = None
    robust_z_score: float | None = None
    robust_scale_method: Literal["mad", "mean_ad"] | None = None
    history_percentile: float | None = None
    percentile_samples: int = 0

    flags: tuple[FeatureFlag, ...] = ()


class FeatureDefinition(FrozenModel):
    name: str
    unit: str
    definition: str
    valid_when: str


FEATURE_DEFINITIONS: tuple[FeatureDefinition, ...] = (
    FeatureDefinition(
        name="latest_views",
        unit="views/day",
        definition="x[t]: pageviews on t, the latest day with an observation.",
        valid_when="at least one observed day in the window",
    ),
    FeatureDefinition(
        name="avg_3d",
        unit="views/day",
        definition="Mean of observed days in [t-2, t].",
        valid_when=f">= {MIN_SHORT_SAMPLES} observed days of 3",
    ),
    FeatureDefinition(
        name="avg_7d",
        unit="views/day",
        definition="Mean of observed days in [t-6, t].",
        valid_when=f">= {MIN_LONG_SAMPLES} observed days of 7",
    ),
    FeatureDefinition(
        name="baseline_mean_28d / baseline_std_28d",
        unit="views/day",
        definition=(
            "Rolling mean and sample standard deviation (n-1) of observed days in the "
            "baseline window [t-34, t-7]."
        ),
        valid_when=f">= {MIN_BASELINE_SAMPLES} observed days of 28",
    ),
    FeatureDefinition(
        name="baseline_median_28d / baseline_mad_28d",
        unit="views/day",
        definition=(
            "Median m of the baseline window, and MAD = median(|x_i - m|) over the same "
            "days. The median is the game's 'normal' level; it is unaffected by old spikes."
        ),
        valid_when=f">= {MIN_BASELINE_SAMPLES} observed days of 28",
    ),
    FeatureDefinition(
        name="lift_ratio / lift_pct",
        unit="ratio / percent",
        definition="lift_ratio = avg_3d / baseline_median_28d; lift_pct = (lift_ratio - 1) * 100.",
        valid_when="avg_3d valid and baseline_median_28d > 0",
    ),
    FeatureDefinition(
        name="lift_7d_pct",
        unit="percent",
        definition="(avg_7d / baseline_median_28d - 1) * 100.",
        valid_when="avg_7d valid and baseline_median_28d > 0",
    ),
    FeatureDefinition(
        name="velocity_ratio / velocity_pct",
        unit="ratio / percent",
        definition=(
            "velocity_ratio = avg_3d / prior_avg_3d, where prior_avg_3d is the mean of "
            "observed days in [t-5, t-3]; velocity_pct = (velocity_ratio - 1) * 100."
        ),
        valid_when=f"both 3-day windows have >= {MIN_SHORT_SAMPLES} observed days and "
        "prior_avg_3d > 0",
    ),
    FeatureDefinition(
        name="consecutive_rising_days",
        unit="days",
        definition="Number of consecutive day-over-day increases ending at t. A missing day "
        "ends the run.",
        valid_when="always (0 when there is no run)",
    ),
    FeatureDefinition(
        name="z_score",
        unit="standard deviations",
        definition="(x[t] - baseline_mean_28d) / baseline_std_28d.",
        valid_when="baseline valid and baseline_std_28d > 0",
    ),
    FeatureDefinition(
        name="robust_z_score",
        unit="robust standard deviations",
        definition=(
            f"(x[t] - baseline_median_28d) / s, with s = {MAD_TO_SIGMA} * MAD. If MAD is 0, "
            f"s = {MEAN_AD_TO_SIGMA} * mean(|x_i - m|) instead (robust_scale_method = "
            "'mean_ad'). Unlike z_score it is not inflated or masked by earlier outliers."
        ),
        valid_when="baseline valid and s > 0",
    ),
    FeatureDefinition(
        name="history_percentile",
        unit="percentile (0-100)",
        definition=(
            "Mid-rank percentile of x[t] among all observed days before t in the fetched "
            "window: 100 * (count(x_i < x[t]) + 0.5 * count(x_i = x[t])) / n."
        ),
        valid_when=f"n >= {MIN_PERCENTILE_SAMPLES} prior observed days",
    ),
    FeatureDefinition(
        name="staleness_days",
        unit="days",
        definition=(
            "Days by which t trails the latest day the source was due to have published, "
            "given its expected publication lag. Never negative."
        ),
        valid_when="at least one observed day in the window",
    ),
    FeatureDefinition(
        name="missing_days",
        unit="days",
        definition="Days with no observation between the first observed day and t.",
        valid_when="always",
    ),
)


def compute_attention_features(series: TimeSeries) -> AttentionFeatures:
    if series.granularity is not Granularity.DAILY:
        raise ValueError(
            f"attention features are defined for daily series, not {series.granularity.value}"
        )
    values = series.values()
    observed = [index for index, value in enumerate(values) if value is not None]
    if not observed:
        return AttentionFeatures(
            window_days=len(values), observed_days=0, flags=(FeatureFlag.NO_OBSERVATIONS,)
        )

    first, t = observed[0], observed[-1]
    flags: list[FeatureFlag] = []

    latest = _observed(values, t, t)[0]
    short = _observed(values, t - SHORT_WINDOW_DAYS + 1, t)
    long = _observed(values, t - LONG_WINDOW_DAYS + 1, t)
    prior_short = _observed(values, t - 2 * SHORT_WINDOW_DAYS + 1, t - SHORT_WINDOW_DAYS)
    baseline = _observed(values, t - LONG_WINDOW_DAYS - BASELINE_DAYS + 1, t - LONG_WINDOW_DAYS)
    prior_history = _observed(values, 0, t - 1)

    avg_3d = _mean_if(short, MIN_SHORT_SAMPLES)
    avg_7d = _mean_if(long, MIN_LONG_SAMPLES)
    prior_avg_3d = _mean_if(prior_short, MIN_SHORT_SAMPLES)
    if avg_3d is None:
        flags.append(FeatureFlag.INSUFFICIENT_RECENT_DATA)

    mean = median = std = mad = None
    lift_ratio = lift_7d_ratio = z_score = robust_z = None
    scale_method: Literal["mad", "mean_ad"] | None = None
    if len(baseline) < MIN_BASELINE_SAMPLES:
        flags.append(FeatureFlag.INSUFFICIENT_HISTORY)
    else:
        mean = statistics.fmean(baseline)
        median = statistics.median(baseline)
        std = statistics.stdev(baseline)
        deviations = [abs(value - median) for value in baseline]
        mad = statistics.median(deviations)
        if median > 0:
            lift_ratio = None if avg_3d is None else avg_3d / median
            lift_7d_ratio = None if avg_7d is None else avg_7d / median
        else:
            flags.append(FeatureFlag.ZERO_BASELINE)
        if std > 0:
            z_score = (latest - mean) / std
        if mad > 0:
            scale, scale_method = MAD_TO_SIGMA * mad, "mad"
        else:
            scale, scale_method = MEAN_AD_TO_SIGMA * statistics.fmean(deviations), "mean_ad"
        if scale > 0:
            robust_z = (latest - median) / scale
        else:
            scale_method = None
            flags.append(FeatureFlag.ZERO_DISPERSION)

    velocity_ratio = None
    if avg_3d is not None and prior_avg_3d is not None and prior_avg_3d > 0:
        velocity_ratio = avg_3d / prior_avg_3d
    else:
        flags.append(FeatureFlag.VELOCITY_UNDEFINED)

    percentile = None
    if len(prior_history) >= MIN_PERCENTILE_SAMPLES:
        below = sum(1 for value in prior_history if value < latest)
        equal = sum(1 for value in prior_history if value == latest)
        percentile = 100 * (below + 0.5 * equal) / len(prior_history)

    span = t - first + 1
    missing = span - len(observed)
    due = Granularity.DAILY.periods_between(series.period_start_at(t), series.latest_due)
    staleness = max(0, due)
    if missing:
        flags.append(FeatureFlag.HAS_MISSING_DAYS)
    if staleness:
        flags.append(FeatureFlag.STALE)

    # AttentionFeatures rejects NaN and Infinity, so a non-finite value fails here.
    return AttentionFeatures(
        as_of_day=series.points[t].period_day,
        window_days=len(values),
        observed_days=len(observed),
        history_span_days=span,
        missing_days=missing,
        staleness_days=staleness,
        latest_views=latest,
        avg_3d=avg_3d,
        avg_7d=avg_7d,
        prior_avg_3d=prior_avg_3d,
        recent_7d_samples=len(long),
        baseline_samples=len(baseline),
        baseline_mean_28d=mean,
        baseline_median_28d=median,
        baseline_std_28d=std,
        baseline_mad_28d=mad,
        lift_ratio=lift_ratio,
        lift_pct=_pct(lift_ratio),
        lift_7d_pct=_pct(lift_7d_ratio),
        velocity_ratio=velocity_ratio,
        velocity_pct=_pct(velocity_ratio),
        consecutive_rising_days=_rising_run(values, t),
        z_score=z_score,
        robust_z_score=robust_z,
        robust_scale_method=scale_method,
        history_percentile=percentile,
        percentile_samples=len(prior_history),
        flags=tuple(flags),
    )


def rolling_mean(values: list[float | None], window: int, min_samples: int) -> list[float | None]:
    """Trailing mean per day over observed values only; None where too few are observed."""
    return [
        _mean_if(_observed(values, index - window + 1, index), min_samples)
        for index in range(len(values))
    ]


def _observed(values: list[float | None], start: int, end: int) -> list[float]:
    """Observed values at indices start..end inclusive; indices before 0 do not exist."""
    return [value for value in values[max(start, 0) : max(end + 1, 0)] if value is not None]


def _mean_if(samples: list[float], minimum: int) -> float | None:
    return statistics.fmean(samples) if len(samples) >= minimum else None


def _pct(ratio: float | None) -> float | None:
    return None if ratio is None else (ratio - 1) * 100


def _rising_run(values: list[float | None], t: int) -> int:
    run = 0
    index = t
    while index > 0:
        current, previous = values[index], values[index - 1]
        if current is None or previous is None or current <= previous:
            break
        run += 1
        index -= 1
    return run
