"""signal_semantics_v2: how a normalised signal is read. The engine is not involved.

Rule versions (`baseline_rules_v1`) fix what the feature engine COMPUTES. Semantics
versions fix how the result is PRESENTED: what "fresh" means and what a family's
availability is called. They are versioned separately so that presentation can be
corrected without changing a single engine output, and so that an old reading can
always be reproduced.

    signal_semantics_v1   `normalise` and `entity_evidence`, kept exactly as they were
    signal_semantics_v2   this module

What v2 changes:

1. Two kinds of freshness, never merged.
   RAW freshness is about the source: how old is the latest valid raw observation?
   For a sampled source it is stale when that observation is older than two sampling
   slots (30 minutes for 15-minute Steam samples). AGGREGATE freshness is about a
   bucket: when the latest valid hourly or daily bucket ended, and how old it is.
   A source can be perfectly fresh while its current hour is not complete yet.

2. Six availability states. Never sampled, stale, and unresolved are three different
   conditions and are never reported as one.

3. An entity with several eligible apps has an UNRESOLVED Steam family. The per-app
   histories exist and stay available internally, but the family contributes nothing
   to the entity's observed evidence breadth until a combination rule exists.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, Literal

from pydantic import AwareDatetime

from metaspike.domain import baseline_rules as rules
from metaspike.domain.families import ACTIVE_FAMILIES, SourceFamily
from metaspike.domain.games import GameId
from metaspike.domain.model import FrozenModel
from metaspike.domain.samples import SlotResolution
from metaspike.domain.timeseries import Granularity
from metaspike.features.config import SignalFeatureConfig
from metaspike.features.engine import BaselineState
from metaspike.features.normalise import Freshness, NormalisedSignal

SIGNAL_SEMANTICS_V1: Final = "signal_semantics_v1"
SIGNAL_SEMANTICS_V2: Final = "signal_semantics_v2"

# A sampled source is stale once its latest valid observation is older than this many
# of its own sampling slots.
RAW_STALE_AFTER_SLOTS = 2


class Availability(StrEnum):
    """What can be said about one family for one entity."""

    APPLICABLE_AND_AVAILABLE = "applicable_and_available"
    # The family can observe the entity, and never has.
    APPLICABLE_BUT_MISSING = "applicable_but_missing"
    # It has observed the entity, but not recently enough.
    APPLICABLE_BUT_STALE = "applicable_but_stale"
    INAPPLICABLE = "inapplicable"
    UNKNOWN = "unknown"
    # Observations exist, but there is no rule yet for one entity-level value.
    UNRESOLVED = "unresolved"


class Applicability(StrEnum):
    """What is known about whether a family CAN observe an entity, before any data."""

    APPLICABLE = "applicable"
    INAPPLICABLE = "inapplicable"
    UNKNOWN = "unknown"
    UNRESOLVED = "unresolved"


class RawFreshnessBasis(StrEnum):
    RAW_SLOTS = "raw_slots"  # measured on the source's own sampling slots
    SOURCE_PERIODS = "source_periods"  # the source's native period is the bucket


class RawFreshness(FrozenModel):
    """How recent the source's own data is. Says nothing about any aggregate."""

    status: Freshness
    basis: RawFreshnessBasis
    latest_observed_at: AwareDatetime | None = None
    age_seconds: float | None = None
    # Older than this is stale. None when staleness is not measured as an age.
    stale_after_seconds: float | None = None


class AggregateFreshness(FrozenModel):
    """How recent the latest valid bucket is. Says nothing about the raw source."""

    status: Freshness
    granularity: Granularity
    # End of the last bucket folded into the state.
    valid_through: AwareDatetime | None = None
    latest_valid_bucket_start: AwareDatetime | None = None
    # Time since the latest VALID bucket ended.
    bucket_age_seconds: float | None = None


class SignalV2(FrozenModel):
    """One signal under signal_semantics_v2."""

    semantics_version: Literal["signal_semantics_v2"] = SIGNAL_SEMANTICS_V2
    rule_version: str
    # The engine's evaluation, exactly as baseline rules produced it. Its own
    # `freshness` field is the aggregate freshness below.
    signal: NormalisedSignal
    raw_freshness: RawFreshness
    aggregate_freshness: AggregateFreshness


def raw_freshness_from_slots(
    slots: Iterable[SlotResolution],
    as_of: datetime,
    slot: Granularity = Granularity.FIFTEEN_MINUTES,
    stale_after_slots: int = RAW_STALE_AFTER_SLOTS,
) -> RawFreshness:
    """Raw freshness of a sampled source, from its resolved slots as known at `as_of`."""
    limit: timedelta = stale_after_slots * slot.step
    observed = [
        s.selected.observed_at
        for s in slots
        if s.selected is not None and s.selected.observed_at <= as_of
    ]
    if not observed:
        return RawFreshness(
            status=Freshness.NO_DATA,
            basis=RawFreshnessBasis.RAW_SLOTS,
            stale_after_seconds=limit.total_seconds(),
        )
    latest = max(observed)
    age = as_of - latest
    return RawFreshness(
        status=Freshness.STALE if age > limit else Freshness.FRESH,
        basis=RawFreshnessBasis.RAW_SLOTS,
        latest_observed_at=latest,
        age_seconds=age.total_seconds(),
        stale_after_seconds=limit.total_seconds(),
    )


def raw_freshness_from_periods(signal: NormalisedSignal, state: BaselineState) -> RawFreshness:
    """Raw freshness of a source whose native period IS the bucket (daily pageviews).

    There is no finer raw layer, so the source is stale exactly when a due period is
    missing, which is what the aggregate already reports.
    """
    step = state.granularity.step
    latest = None if state.last_valid_start is None else state.last_valid_start + step
    return RawFreshness(
        status=signal.freshness,
        basis=RawFreshnessBasis.SOURCE_PERIODS,
        latest_observed_at=latest,
        age_seconds=None if latest is None else (signal.as_of - latest).total_seconds(),
    )


def aggregate_freshness(signal: NormalisedSignal, state: BaselineState) -> AggregateFreshness:
    step = state.granularity.step
    ended = None if state.last_valid_start is None else state.last_valid_start + step
    return AggregateFreshness(
        status=signal.freshness,
        granularity=state.granularity,
        valid_through=state.valid_through,
        latest_valid_bucket_start=state.last_valid_start,
        bucket_age_seconds=None if ended is None else (signal.as_of - ended).total_seconds(),
    )


def signal_v2(
    config: SignalFeatureConfig,
    signal: NormalisedSignal,
    state: BaselineState,
    raw: RawFreshness | None = None,
) -> SignalV2:
    """Wrap an engine evaluation in v2 semantics. Nothing in `signal` is recomputed."""
    if (signal.rule_version, state.config_fingerprint) != (
        config.rule_version,
        config.fingerprint,
    ):
        raise ValueError("the signal, its state and the configuration do not belong together")
    return SignalV2(
        rule_version=signal.rule_version,
        signal=signal,
        raw_freshness=raw or raw_freshness_from_periods(signal, state),
        aggregate_freshness=aggregate_freshness(signal, state),
    )


# --- an entity's evidence under v2 ----------------------------------------------------


class FamilyReadingV2(FrozenModel):
    family: SourceFamily
    availability: Availability
    # The family's entity-level signal(s). Empty when there is none to give.
    signals: tuple[SignalV2, ...] = ()
    # Per source-native id, kept for internal use when they cannot be combined.
    per_native: tuple[SignalV2, ...] = ()
    reason: str | None = None


class EvidenceBreadthV2(FrozenModel):
    """Which independent families observe the entity now. Each family counts once."""

    observed: tuple[SourceFamily, ...]
    observed_count: int
    missing: tuple[SourceFamily, ...]
    stale: tuple[SourceFamily, ...]
    inapplicable: tuple[SourceFamily, ...]
    unknown: tuple[SourceFamily, ...]
    # Applicable, with data, and still contributing nothing to `observed`.
    unresolved: tuple[SourceFamily, ...]
    # Families that could observe the entity: everything except inapplicable and unknown.
    applicable_count: int


class EntityEvidenceV2(FrozenModel):
    semantics_version: Literal["signal_semantics_v2"] = SIGNAL_SEMANTICS_V2
    rule_version: str
    canonical_game_id: GameId
    as_of: AwareDatetime
    families: tuple[FamilyReadingV2, ...]
    evidence_breadth: EvidenceBreadthV2

    def family(self, family: SourceFamily) -> FamilyReadingV2:
        return next(reading for reading in self.families if reading.family is family)


def family_reading_v2(
    family: SourceFamily,
    applicability: Applicability,
    signals: tuple[SignalV2, ...] = (),
    per_native: tuple[SignalV2, ...] = (),
    reason: str | None = None,
) -> FamilyReadingV2:
    """Name a family's availability from what is known and from RAW freshness.

    Availability is about the source, so it follows raw freshness: a source that is
    being sampled on time is available even while its current bucket is incomplete.
    """
    everything = (*signals, *per_native)
    if any(item.signal.source_family is not family for item in everything):
        raise ValueError("a signal from another family cannot speak for this one")
    if applicability is Applicability.INAPPLICABLE:
        return FamilyReadingV2(family=family, availability=Availability.INAPPLICABLE, reason=reason)
    if applicability is Applicability.UNKNOWN:
        return FamilyReadingV2(family=family, availability=Availability.UNKNOWN, reason=reason)
    if applicability is Applicability.UNRESOLVED:
        if signals:
            raise ValueError("an unresolved family has no entity-level signal")
        return FamilyReadingV2(
            family=family,
            availability=Availability.UNRESOLVED,
            per_native=per_native,
            reason=reason or rules.MULTI_APP_UNRESOLVED_REASON,
        )
    statuses = {item.raw_freshness.status for item in signals}
    if Freshness.FRESH in statuses:
        availability = Availability.APPLICABLE_AND_AVAILABLE
    elif Freshness.STALE in statuses:
        availability = Availability.APPLICABLE_BUT_STALE
    else:
        availability = Availability.APPLICABLE_BUT_MISSING
    return FamilyReadingV2(
        family=family,
        availability=availability,
        signals=signals,
        per_native=per_native,
        reason=reason,
    )


def entity_evidence_v2(
    canonical_game_id: str, as_of: datetime, readings: tuple[FamilyReadingV2, ...]
) -> EntityEvidenceV2:
    families = [reading.family for reading in readings]
    if len(set(families)) != len(families):
        raise ValueError("a family can be read only once per entity")
    if set(families) != set(ACTIVE_FAMILIES):
        raise ValueError("every active family must be read, even if only to say it cannot observe")
    ordered = tuple(sorted(readings, key=lambda reading: ACTIVE_FAMILIES.index(reading.family)))

    def those(state: Availability) -> tuple[SourceFamily, ...]:
        return tuple(r.family for r in ordered if r.availability is state)

    observed = those(Availability.APPLICABLE_AND_AVAILABLE)
    missing = those(Availability.APPLICABLE_BUT_MISSING)
    stale = those(Availability.APPLICABLE_BUT_STALE)
    unresolved = those(Availability.UNRESOLVED)
    return EntityEvidenceV2(
        rule_version=rules.BASELINE_RULES_VERSION,
        canonical_game_id=canonical_game_id,
        as_of=as_of,
        families=ordered,
        evidence_breadth=EvidenceBreadthV2(
            observed=observed,
            observed_count=len(observed),  # an unresolved family is never in here
            missing=missing,
            stale=stale,
            inapplicable=those(Availability.INAPPLICABLE),
            unknown=those(Availability.UNKNOWN),
            unresolved=unresolved,
            applicable_count=len(observed) + len(missing) + len(stale) + len(unresolved),
        ),
    )
