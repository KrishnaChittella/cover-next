"""Normalised signals: each signal described against its own history, nothing else.

A `NormalisedSignal` is the engine's latest evaluation of one signal, read at a
moment. Its lift, robust z, percentile, velocity and acceleration are all relative to
that signal's own past, which is what makes pageviews and concurrent players
comparable later without ever averaging one raw unit with the other.

Two descriptions of an entity's evidence are produced and deliberately kept apart:

    signal quality    how good each signal is: maturity, freshness, coverage
    evidence breadth  which independent source families actually observe the entity

Neither is the public Confidence, which is not defined here. Whatever it becomes, it
must take breadth into account: one mature, fresh family is not the same evidence as
several independent families that agree. Breadth is counted over the families that
CAN observe the entity. A family that cannot is inapplicable; it is not a missing
source, and it lowers nothing.

Everything in this module is internal. Nothing here is a published contract.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import AwareDatetime

from metaspike.domain import baseline_rules as rules
from metaspike.domain.families import ACTIVE_FAMILIES, FamilyApplicability, SourceFamily
from metaspike.domain.games import GameId
from metaspike.domain.model import FrozenModel
from metaspike.domain.timeseries import Granularity
from metaspike.features.config import SeasonalityBasis, SignalFeatureConfig
from metaspike.features.engine import (
    BaselineStat,
    BaselineState,
    BucketEvaluation,
    ChangePointView,
    FeatureValue,
    Maturity,
    have,
    lack,
)

VELOCITY_UNIT = "log lift per day"
ACCELERATION_UNIT = "log lift per day squared"
TREND_UNIT = "log level per day"


class Freshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    NO_DATA = "no_data"


class NormalisedSignal(FrozenModel):
    subject: str
    source: str
    source_family: SourceFamily
    metric: str
    granularity: Granularity
    rule_version: str
    as_of: AwareDatetime
    # End of the last bucket this record reflects. Nothing later was used.
    valid_through: AwareDatetime | None
    bucket_start: AwareDatetime | None
    # The latest single observation, where the caller supplies one; else the bucket level.
    current_level: FeatureValue
    current_level_observed_at: AwareDatetime | None = None
    bucket_level: FeatureValue
    expected_level: FeatureValue
    absolute_change: FeatureValue
    log_lift: FeatureValue
    percentage_lift: FeatureValue
    robust_z: FeatureValue
    trailing_percentile: FeatureValue
    percentile_samples: int = 0
    anomaly_strength: FeatureValue
    lift_fast: FeatureValue
    lift_slow: FeatureValue
    velocity: FeatureValue
    acceleration: FeatureValue
    short_trend: FeatureValue
    long_trend: FeatureValue
    change_point: ChangePointView
    maturity: Maturity
    freshness: Freshness
    coverage: float  # of the evaluated bucket
    baseline_coverage: float  # of the longest configured baseline window
    seasonality_basis: SeasonalityBasis | None = None
    baselines: tuple[BaselineStat, ...] = ()
    reason_codes: tuple[str, ...] = ()
    unsupported: tuple[str, ...] = ()

    @property
    def observed(self) -> bool:
        """Whether this signal currently has a fresh level to speak of."""
        return self.freshness is Freshness.FRESH and self.bucket_level.value is not None


_FEATURES = (
    "expected_level",
    "absolute_change",
    "log_lift",
    "percentage_lift",
    "robust_z",
    "trailing_percentile",
    "anomaly_strength",
    "lift_fast",
    "lift_slow",
    "velocity",
    "acceleration",
)


def _freshness(config: SignalFeatureConfig, state: BaselineState, as_of: datetime) -> Freshness:
    if not state.observed_ever or state.valid_through is None:
        return Freshness.NO_DATA
    behind = (as_of - state.valid_through) // config.granularity.step
    if state.gap_run + max(0, behind) >= config.stale_after_buckets:
        return Freshness.STALE
    return Freshness.FRESH


def normalise(
    config: SignalFeatureConfig,
    state: BaselineState,
    as_of: datetime,
    *,
    current_level: float | None = None,
    current_level_observed_at: datetime | None = None,
) -> NormalisedSignal:
    """The signal as it stood at `as_of`, from a state that holds nothing later."""
    if state.valid_through is not None and state.valid_through > as_of:
        raise ValueError("the state already contains buckets that end after as_of")
    freshness = _freshness(config, state, as_of)
    latest: BucketEvaluation | None = state.latest
    unsupported = lack(rules.UNSUPPORTED_FOR_GRANULARITY)

    def feature(name: str) -> FeatureValue:
        if name in config.unsupported:
            return unsupported
        if latest is None:
            return lack(rules.NO_HISTORY)
        found: FeatureValue = getattr(latest, name)
        if freshness is Freshness.STALE and found.reason != rules.UNSUPPORTED_FOR_GRANULARITY:
            return lack(rules.STALE)  # what was true of an old bucket is not true of now
        return found

    values = {name: feature(name) for name in _FEATURES}
    if latest is None:
        bucket_level = lack(rules.NO_HISTORY)
        trends = {"short_trend": lack(rules.NO_HISTORY), "long_trend": lack(rules.NO_HISTORY)}
        if "trend_short" in config.unsupported:
            trends["short_trend"] = unsupported
        if "trend_long" in config.unsupported:
            trends["long_trend"] = unsupported
    else:
        if freshness is Freshness.STALE:
            bucket_level = lack(rules.STALE)
        elif latest.level is None:
            bucket_level = lack(rules.GAP if state.observed_ever else rules.NO_HISTORY)
        else:
            bucket_level = have(latest.level)
        trends = {"short_trend": latest.trend_short, "long_trend": latest.trend_long}
    if current_level is not None and freshness is Freshness.FRESH:
        level = have(current_level)
    else:
        level, current_level_observed_at = bucket_level, None
    reported = [bucket_level, level, *values.values(), *trends.values()]
    reasons = sorted({item.reason for item in reported if item.reason is not None})
    longest = latest.baselines[-1] if latest and latest.baselines else None
    return NormalisedSignal(
        subject=state.subject,
        source=state.source,
        source_family=state.source_family,
        metric=state.metric,
        granularity=state.granularity,
        rule_version=state.rule_version,
        as_of=as_of,
        valid_through=state.valid_through,
        bucket_start=None if latest is None else latest.bucket_start,
        current_level=level,
        current_level_observed_at=current_level_observed_at,
        bucket_level=bucket_level,
        percentile_samples=0 if latest is None else latest.percentile_samples,
        change_point=(
            latest.change_point
            if latest is not None
            else ChangePointView(
                supported=config.cusum is not None,
                reason=None if config.cusum else rules.UNSUPPORTED_FOR_GRANULARITY,
            )
        ),
        maturity=Maturity.NO_HISTORY if latest is None else latest.maturity,
        freshness=freshness,
        coverage=0.0 if latest is None else latest.coverage,
        baseline_coverage=0.0 if longest is None else longest.coverage,
        seasonality_basis=None if latest is None else latest.seasonality_basis,
        baselines=() if latest is None else latest.baselines,
        reason_codes=tuple(reasons),
        unsupported=config.unsupported,
        **values,
        **trends,
    )


def unavailable_signal(
    config: SignalFeatureConfig, subject: str, as_of: datetime, reason: str
) -> NormalisedSignal:
    """A signal that has no value for a stated reason that is not about missing data."""
    gone = lack(reason)
    return NormalisedSignal(
        subject=subject,
        source=config.source,
        source_family=config.source_family,
        metric=config.metric,
        granularity=config.granularity,
        rule_version=config.rule_version,
        as_of=as_of,
        valid_through=None,
        bucket_start=None,
        current_level=gone,
        bucket_level=gone,
        short_trend=gone,
        long_trend=gone,
        change_point=ChangePointView(supported=config.cusum is not None, reason=reason),
        maturity=Maturity.NO_HISTORY,
        freshness=Freshness.NO_DATA,
        coverage=0.0,
        baseline_coverage=0.0,
        reason_codes=(reason,),
        unsupported=config.unsupported,
        **dict.fromkeys(_FEATURES, gone),
    )


# --- an entity's evidence -------------------------------------------------------------


class FamilyReading(FrozenModel):
    """What one source family says about one entity, and whether it can say anything."""

    family: SourceFamily
    applicability: FamilyApplicability
    # The family's signal(s) for the entity. Several signals are still ONE family.
    signals: tuple[NormalisedSignal, ...] = ()
    # Histories kept per source-native id when they could not be combined.
    per_native: tuple[NormalisedSignal, ...] = ()
    reason: str | None = None


class SignalQuality(FrozenModel):
    """How good one signal is. A description, not a score: no formula is defined yet."""

    source: str
    source_family: SourceFamily
    metric: str
    maturity: Maturity
    freshness: Freshness
    bucket_coverage: float
    baseline_coverage: float
    seasonality_basis: SeasonalityBasis | None
    source_quality_notes: tuple[str, ...] = ()


class EvidenceBreadth(FrozenModel):
    """Which independent families observe the entity. Each family counts once."""

    observed: tuple[SourceFamily, ...]
    observed_count: int
    missing: tuple[SourceFamily, ...]
    inapplicable: tuple[SourceFamily, ...]
    unknown: tuple[SourceFamily, ...]
    # Observed, but with no single entity-level value yet (for example several apps).
    unresolved: tuple[SourceFamily, ...]
    # How many families could observe this entity at all. Inapplicable ones are not in it.
    applicable_count: int


class EntityEvidence(FrozenModel):
    canonical_game_id: GameId
    as_of: AwareDatetime
    rule_version: str
    families: tuple[FamilyReading, ...]
    signal_quality_confidence: tuple[SignalQuality, ...]
    evidence_breadth: EvidenceBreadth

    def family(self, family: SourceFamily) -> FamilyReading:
        return next(reading for reading in self.families if reading.family is family)


def family_reading(
    family: SourceFamily,
    applicable: FamilyApplicability,
    signals: tuple[NormalisedSignal, ...] = (),
    per_native: tuple[NormalisedSignal, ...] = (),
    reason: str | None = None,
) -> FamilyReading:
    """Classify a family from whether it can observe the entity and whether it does.

    `applicable` is what is known about the entity: INAPPLICABLE and UNKNOWN pass
    through untouched. For an applicable family the reading is AVAILABLE only when a
    signal is fresh and has a level; otherwise it is applicable but missing.
    """
    if any(signal.source_family is not family for signal in (*signals, *per_native)):
        raise ValueError("a signal from another family cannot speak for this one")
    if applicable in (FamilyApplicability.INAPPLICABLE, FamilyApplicability.UNKNOWN):
        return FamilyReading(family=family, applicability=applicable, reason=reason)
    seen = any(signal.observed for signal in (*signals, *per_native))
    return FamilyReading(
        family=family,
        applicability=(
            FamilyApplicability.APPLICABLE_AND_AVAILABLE
            if seen
            else FamilyApplicability.APPLICABLE_BUT_MISSING
        ),
        signals=signals,
        per_native=per_native,
        reason=reason,
    )


def entity_evidence(
    canonical_game_id: str,
    as_of: datetime,
    readings: tuple[FamilyReading, ...],
    quality_notes: dict[str, tuple[str, ...]] | None = None,
) -> EntityEvidence:
    families = [reading.family for reading in readings]
    if len(set(families)) != len(families):
        raise ValueError("a family can be read only once per entity")
    if set(families) != set(ACTIVE_FAMILIES):
        raise ValueError("every active family must be read, even if only to say it cannot observe")
    ordered = tuple(sorted(readings, key=lambda reading: ACTIVE_FAMILIES.index(reading.family)))

    def those(state: FamilyApplicability) -> tuple[SourceFamily, ...]:
        return tuple(r.family for r in ordered if r.applicability is state)

    observed = those(FamilyApplicability.APPLICABLE_AND_AVAILABLE)
    missing = those(FamilyApplicability.APPLICABLE_BUT_MISSING)
    notes = quality_notes or {}
    return EntityEvidence(
        canonical_game_id=canonical_game_id,
        as_of=as_of,
        rule_version=rules.BASELINE_RULES_VERSION,
        families=ordered,
        signal_quality_confidence=tuple(
            SignalQuality(
                source=signal.source,
                source_family=signal.source_family,
                metric=signal.metric,
                maturity=signal.maturity,
                freshness=signal.freshness,
                bucket_coverage=signal.coverage,
                baseline_coverage=signal.baseline_coverage,
                seasonality_basis=signal.seasonality_basis,
                source_quality_notes=notes.get(signal.source, ()),
            )
            for reading in ordered
            for signal in reading.signals
            if signal.freshness is not Freshness.NO_DATA
        ),
        evidence_breadth=EvidenceBreadth(
            observed=observed,
            observed_count=len(observed),
            missing=missing,
            inapplicable=those(FamilyApplicability.INAPPLICABLE),
            unknown=those(FamilyApplicability.UNKNOWN),
            unresolved=tuple(
                r.family
                for r in ordered
                if r.reason == rules.MULTI_APP_UNRESOLVED_REASON
                and r.applicability is FamilyApplicability.APPLICABLE_AND_AVAILABLE
            ),
            applicable_count=len(observed) + len(missing),
        ),
    )
