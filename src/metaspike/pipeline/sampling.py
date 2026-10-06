"""One sampling run: one intended slot, every target, every outcome stored.

A run does three things per target and nothing else: decide whether a request is
still needed, take one reading, and append the attempt to the store. It returns a
report of what it did. It does not return values, and nothing downstream may use a
run's return value as data: history and assessment read the store, point-in-time.

Rerunning is safe. A slot that already holds a valid observation is never requested
again. A slot whose attempts failed may be retried, up to a fixed number of
transmissions per slot. Once the source refuses the key or the rate, or the quota is
spent, the run stops sending and records the remaining targets as refused.
"""

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import AwareDatetime

from metaspike.catalogue.eligibility import SamplingTarget
from metaspike.domain.model import FrozenModel
from metaspike.domain.samples import (
    SampleAttempt,
    SampleFlag,
    SampleReading,
    SampleStatus,
    SlotState,
)
from metaspike.domain.signals import SignalKey, SourceErrorCode
from metaspike.domain.timeseries import TimeWindow
from metaspike.sources.base import SamplingAdapter, SourceMode
from metaspike.storage.samples import SampleStore

# An observation this long after its slot began is flagged late.
LATE_TOLERANCE = timedelta(minutes=5)
# Requests that may be transmitted for one logical slot, across all runs.
MAX_TRANSMISSIONS_PER_SLOT = 3


class TargetAction(StrEnum):
    SAMPLED = "sampled"  # an attempt was made and stored
    ALREADY_OBSERVED = "already_observed"  # the slot has its observation; nothing sent
    SOURCE_UNAVAILABLE = "source_unavailable"  # the source already answered: no value
    RETRY_BUDGET_SPENT = "retry_budget_spent"  # the slot has used its transmissions
    HALTED = "halted"  # the run had stopped sending; a refusal was stored


class TargetOutcome(FrozenModel):
    source_native_id: str
    action: TargetAction
    slot_start: AwareDatetime
    status: SampleStatus | None = None
    error_code: SourceErrorCode | None = None
    attempt_id: str | None = None
    late: bool = False
    crossed_slot: bool = False


class SamplingRunReport(FrozenModel):
    """What a run did. Deliberately holds no sampled values."""

    collection_run_id: str
    source: str
    scheduled_for: AwareDatetime
    started_at: AwareDatetime
    finished_at: AwareDatetime
    outcomes: tuple[TargetOutcome, ...]
    requests_sent: int
    halted_by: SourceErrorCode | None = None

    def count(self, status: SampleStatus) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status is status)


def _new_id() -> str:
    return uuid.uuid4().hex


def run_sampling_slot(
    targets: tuple[SamplingTarget, ...],
    adapter: SamplingAdapter,
    store: SampleStore,
    *,
    now: Callable[[], datetime],
    scheduled_for: datetime | None = None,
    new_id: Callable[[], str] = _new_id,
) -> SamplingRunReport:
    if adapter.mode is not SourceMode.SAMPLE_NOW:
        raise ValueError("a sampling run needs a sample-now source")
    signal = adapter.signal
    key = SignalKey(source=adapter.source, metric=signal.metric, granularity=signal.granularity)
    started_at = now()
    intended = signal.granularity.floor(started_at) if scheduled_for is None else scheduled_for
    if not signal.granularity.is_aligned(intended):
        raise ValueError("scheduled_for must be the start of a slot")
    run_id = f"{started_at:%Y%m%dT%H%M%SZ}-{new_id()[:8]}"

    outcomes: list[TargetOutcome] = []
    requests_sent = 0
    halted_by: SourceErrorCode | None = None
    for target in sorted(targets, key=lambda t: (len(t.source_native_id), t.source_native_id)):
        moment = now()
        slot_start = signal.granularity.floor(moment)
        slot = store.resolve(key, target.source_native_id, slot_start, moment)
        skip = _skip_reason(slot.state, _transmissions(slot.attempts))
        if skip is not None:
            outcomes.append(
                TargetOutcome(
                    source_native_id=target.source_native_id, action=skip, slot_start=slot_start
                )
            )
            continue
        if halted_by is None:
            reading = adapter.sample(target.source_native_id)
            if reading.status is not SampleStatus.REFUSED:
                requests_sent += 1
            action = TargetAction.SAMPLED
            if reading.halt_run:
                halted_by = reading.error_code
        else:
            reading = SampleReading(
                status=SampleStatus.REFUSED, observed_at=now(), error_code=halted_by
            )
            action = TargetAction.HALTED
        attempt = _attempt(reading, target, key, intended, run_id, new_id(), now)
        store.append(attempt)
        outcomes.append(
            TargetOutcome(
                source_native_id=target.source_native_id,
                action=action,
                slot_start=attempt.slot_start,
                status=attempt.status,
                error_code=attempt.error_code,
                attempt_id=attempt.attempt_id,
                late=SampleFlag.LATE in attempt.quality_flags,
                crossed_slot=attempt.crossed_slot,
            )
        )
    return SamplingRunReport(
        collection_run_id=run_id,
        source=adapter.source,
        scheduled_for=intended,
        started_at=started_at,
        finished_at=now(),
        outcomes=tuple(outcomes),
        requests_sent=requests_sent,
        halted_by=halted_by,
    )


def _transmissions(attempts: tuple[SampleAttempt, ...]) -> int:
    """Attempts that actually reached the source. A refusal sent nothing."""
    return sum(1 for attempt in attempts if attempt.status is not SampleStatus.REFUSED)


def _skip_reason(state: SlotState, transmissions: int) -> TargetAction | None:
    if state is SlotState.OBSERVED:
        return TargetAction.ALREADY_OBSERVED
    if state is SlotState.UNAVAILABLE:
        return TargetAction.SOURCE_UNAVAILABLE
    if transmissions >= MAX_TRANSMISSIONS_PER_SLOT:
        return TargetAction.RETRY_BUDGET_SPENT
    return None


def _attempt(
    reading: SampleReading,
    target: SamplingTarget,
    key: SignalKey,
    scheduled_for: datetime,
    run_id: str,
    attempt_id: str,
    now: Callable[[], datetime],
) -> SampleAttempt:
    flags = []
    if reading.observed_at - scheduled_for > LATE_TOLERANCE:
        flags.append(SampleFlag.LATE)
    if reading.value == 0:
        flags.append(SampleFlag.ZERO_REPORTED)
    if reading.cache_age_seconds:
        flags.append(SampleFlag.EDGE_CACHED)
    if target.canonical_game_id is None:
        flags.append(SampleFlag.BINDING_UNKNOWN)
    return SampleAttempt(
        source=key.source,
        metric=key.metric,
        source_native_id=target.source_native_id,
        value=reading.value,
        granularity=key.granularity,
        scheduled_for=scheduled_for,
        slot_start=key.granularity.floor(reading.observed_at),
        observed_at=reading.observed_at,
        received_at=max(now(), reading.observed_at),
        canonical_game_id=target.canonical_game_id,
        mapping_version=target.mapping_version,
        binding_ref=target.binding_ref,
        collection_run_id=run_id,
        attempt_id=attempt_id,
        status=reading.status,
        quality_flags=tuple(flags),
        error_code=reading.error_code,
        cache_age_seconds=reading.cache_age_seconds,
        cache_ttl_seconds=reading.cache_ttl_seconds,
    )


class TargetHealth(FrozenModel):
    """Health of one sampled target over a window. Metadata only: no values."""

    source_native_id: str
    slots: int
    observed: int
    unavailable: int
    failed: int
    refused: int
    not_sampled: int
    coverage: float
    duplicates: int
    late: int
    last_observed_at: AwareDatetime | None
    # Slots since the last observation, counting back from the end of the window.
    slots_since_last_observation: int | None


def target_health(
    store: SampleStore,
    key: SignalKey,
    source_native_id: str,
    window_end: datetime,
    slots: int,
    as_of: datetime,
) -> TargetHealth:
    end = key.granularity.floor(window_end)
    window = TimeWindow(start=end - (slots - 1) * key.granularity.step, end=end)
    resolved = store.read(key, source_native_id, window, as_of)
    states = [slot.state for slot in resolved]
    observed = [slot for slot in resolved if slot.selected is not None]
    last = observed[-1] if observed else None
    return TargetHealth(
        source_native_id=source_native_id,
        slots=len(resolved),
        observed=states.count(SlotState.OBSERVED),
        unavailable=states.count(SlotState.UNAVAILABLE),
        failed=states.count(SlotState.FAILED),
        refused=states.count(SlotState.REFUSED),
        not_sampled=states.count(SlotState.NOT_SAMPLED),
        coverage=round(len(observed) / len(resolved), 4),
        duplicates=sum(len(slot.duplicates) for slot in resolved),
        late=sum(
            1
            for slot in observed
            if slot.selected is not None and SampleFlag.LATE in slot.selected.quality_flags
        ),
        last_observed_at=None
        if last is None or last.selected is None
        else last.selected.observed_at,
        slots_since_last_observation=(
            None if last is None else len(resolved) - 1 - resolved.index(last)
        ),
    )
