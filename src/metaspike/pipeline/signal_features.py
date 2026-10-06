"""From stored history to normalised signals. Internal development features only.

This is the reference path: history read point-in-time, bucketed, folded through the
one feature engine, and read out as a normalised signal. A later fast path resumes
from a serialised `BaselineState` and a warehouse path builds that state from full
history; both feed the same engine, so there is one implementation of every feature.

Steam-derived output from here is unpublishable under the current policy. Nothing in
this module writes an artifact.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from metaspike.catalogue.eligibility import STEAM_RECORD_SOURCE, SourceEligibility, SteamEligibility
from metaspike.domain import baseline_rules as rules
from metaspike.domain.families import FamilyApplicability, SourceFamily
from metaspike.domain.identity import MappingHistory
from metaspike.domain.observations import TimeSeries
from metaspike.domain.samples import SlotResolution
from metaspike.domain.signals import SignalKey
from metaspike.domain.timeseries import Granularity, TimeWindow
from metaspike.features.aggregate import current_level, hourly_aggregates, hourly_buckets
from metaspike.features.config import SignalFeatureConfig, feature_config
from metaspike.features.engine import BaselineState, Bucket, initial_state, run
from metaspike.features.normalise import (
    FamilyReading,
    NormalisedSignal,
    family_reading,
    normalise,
    unavailable_signal,
)
from metaspike.pipeline.history import read_entity_history
from metaspike.storage.samples import SampleStore

STEAM_SOURCE = "steam_current_players"
STEAM_METRIC = "concurrent_players"
STEAM_SLOTS = SignalKey(
    source=STEAM_SOURCE, metric=STEAM_METRIC, granularity=Granularity.FIFTEEN_MINUTES
)
WIKIMEDIA_SOURCE = "wikimedia_pageviews"
WIKIMEDIA_METRIC = "pageviews"
_HOUR = timedelta(hours=1)
_LAST_SLOT_OF_HOUR = timedelta(minutes=45)


def steam_config(rule_version: str = rules.BASELINE_RULES_VERSION) -> SignalFeatureConfig:
    return feature_config(STEAM_SOURCE, STEAM_METRIC, Granularity.HOURLY, rule_version)


def wikimedia_config(rule_version: str = rules.BASELINE_RULES_VERSION) -> SignalFeatureConfig:
    return feature_config(WIKIMEDIA_SOURCE, WIKIMEDIA_METRIC, Granularity.DAILY, rule_version)


def last_complete_hour(as_of: datetime) -> datetime:
    """Start of the latest hour that had fully ended by `as_of`."""
    return Granularity.HOURLY.floor(as_of) - _HOUR


def steam_slot_window(config: SignalFeatureConfig, as_of: datetime) -> TimeWindow:
    """The slots a full evaluation at `as_of` reads: the bounded history, whole hours."""
    last = last_complete_hour(as_of)
    first = last - (config.history_buckets - 1) * _HOUR
    return TimeWindow(start=first, end=last + _LAST_SLOT_OF_HOUR)


def features_from_slots(
    config: SignalFeatureConfig,
    subject: str,
    slots: Iterable[SlotResolution],
    as_of: datetime,
    state: BaselineState | None = None,
) -> tuple[NormalisedSignal, BaselineState]:
    """Fold resolved slots into a state and read the signal at `as_of`.

    With no state, the fold starts at the beginning of the bounded history. With a
    state, only the hours after it are folded: the slots must cover them.
    """
    resolved = [slot for slot in slots if slot.key.slot_start + _HOUR / 4 <= as_of]
    last = last_complete_hour(as_of)
    if state is None:
        state = initial_state(config, subject)
        first = last - (config.history_buckets - 1) * _HOUR
    else:
        first = state.valid_through or last - (config.history_buckets - 1) * _HOUR
    if first <= last:
        hours = hourly_aggregates(
            (slot for slot in resolved if first <= slot.key.slot_start < last + _HOUR), first, last
        )
        state, _ = run(config, state, hourly_buckets(hours))
    now = current_level(resolved)
    signal = normalise(
        config,
        state,
        as_of,
        current_level=None if now is None else float(now.value),
        current_level_observed_at=None if now is None else now.observed_at,
    )
    return signal, state


def steam_app_features(
    store: SampleStore,
    source_native_id: str,
    as_of: datetime,
    rule_version: str = rules.BASELINE_RULES_VERSION,
) -> tuple[NormalisedSignal, BaselineState]:
    """One app's own history, whichever entity it belongs to."""
    config = steam_config(rule_version)
    window = steam_slot_window(config, as_of)
    # Slots of the hour in progress are read too: they give the current level only.
    reading = TimeWindow(start=window.start, end=Granularity.FIFTEEN_MINUTES.floor(as_of))
    slots = store.read(STEAM_SLOTS, source_native_id, reading, as_of)
    return features_from_slots(config, source_native_id, slots, as_of)


def steam_family(
    store: SampleStore,
    mapping: MappingHistory,
    eligibility: SteamEligibility,
    as_of: datetime,
    rule_version: str = rules.BASELINE_RULES_VERSION,
) -> FamilyReading:
    """What Steam's players say about an entity, or why they cannot say anything."""
    family = SourceFamily.STEAM_PLAYERS
    if eligibility.state is SourceEligibility.INELIGIBLE:
        return family_reading(family, FamilyApplicability.INAPPLICABLE, reason=eligibility.reason)
    if eligibility.state is SourceEligibility.UNKNOWN:
        return family_reading(family, FamilyApplicability.UNKNOWN, reason=eligibility.reason)
    config = steam_config(rule_version)
    window = steam_slot_window(config, as_of)
    reading = TimeWindow(start=window.start, end=Granularity.FIFTEEN_MINUTES.floor(as_of))
    history = read_entity_history(
        store,
        mapping,
        eligibility.canonical_game_id,
        STEAM_SLOTS,
        STEAM_RECORD_SOURCE,
        reading,
        as_of,
    )
    subject = eligibility.canonical_game_id
    if history.combined is not None:
        signal, _ = features_from_slots(config, subject, history.combined, as_of)
        return family_reading(family, FamilyApplicability.APPLICABLE_AND_AVAILABLE, (signal,))
    reason = history.unavailable_reason or rules.NO_HISTORY
    per_native = tuple(
        features_from_slots(config, native.source_native_id, native.slots, as_of)[0]
        for native in history.by_native_id
    )
    return family_reading(
        family,
        FamilyApplicability.APPLICABLE_AND_AVAILABLE,
        (unavailable_signal(config, subject, as_of, reason),),
        per_native,
        reason=reason,
    )


def daily_buckets(series: TimeSeries) -> list[Bucket]:
    """A daily series as engine buckets, up to the last day the source was due to publish.

    Days after that are not late, merely not published yet, so they are not gaps.
    """
    if series.granularity is not Granularity.DAILY:
        raise ValueError("daily buckets need a daily series")
    return [
        Bucket(
            start=point.period_start,
            value=point.value,
            samples=0 if point.value is None else 1,
            expected_samples=1,
        )
        for point in series.points
        if point.period_start <= series.latest_due
    ]


def wikimedia_features(
    series: TimeSeries, as_of: datetime, rule_version: str = rules.BASELINE_RULES_VERSION
) -> tuple[NormalisedSignal, BaselineState]:
    """Daily pageviews through the same engine. Parallel to score v0; changes nothing in it."""
    config = wikimedia_config(rule_version)
    state, _ = run(config, initial_state(config, series.canonical_game_id), daily_buckets(series))
    return normalise(config, state, as_of), state


def wikimedia_family(series: TimeSeries | None, as_of: datetime) -> FamilyReading:
    family = SourceFamily.WIKIMEDIA_READERSHIP
    if series is None:
        return family_reading(
            family, FamilyApplicability.UNKNOWN, reason="no source identifier is mapped"
        )
    signal, _ = wikimedia_features(series, as_of)
    return family_reading(family, FamilyApplicability.APPLICABLE_AND_AVAILABLE, (signal,))
