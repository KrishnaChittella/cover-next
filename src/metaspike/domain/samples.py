"""Samples: what a SAMPLE_NOW source said at one instant, and what a slot resolves to.

Two identities are kept apart on purpose.

    logical slot   (source, metric, source_native_id, slot_start): one bucket of time
                   for one source-native target. This is what a feature reads.
    attempt        one request that was transmitted, or one refusal to transmit. Every
                   attempt is kept, so a slot can hold a failure and the retry that
                   then succeeded.

Four times are kept apart as well.

    scheduled_for  the slot the run was aimed at
    observed_at    when the source's answer was observed (for a refusal, when we
                   declined to ask)
    slot_start     the slot that contains observed_at. A measurement is never moved
                   backward into the slot it was merely intended for.
    received_at    when the attempt was durably stored. Point-in-time reads use this.

Missing is never zero. A slot with no attempt is NOT_SAMPLED; a slot whose attempts
all failed has no value; only a successful answer that reports zero is a zero.
"""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from metaspike.domain.games import GameId, NonEmptyStr
from metaspike.domain.metrics import MetricId
from metaspike.domain.model import FrozenModel
from metaspike.domain.signals import SourceErrorCode
from metaspike.domain.timeseries import Granularity


class SampleStatus(StrEnum):
    """What one attempt came to."""

    OBSERVED = "observed"  # the source answered with a value
    FAILED = "failed"  # a request was sent and produced no usable answer
    REFUSED = "refused"  # no request was sent: quota, limiter or a halted run
    UNAVAILABLE = "unavailable"  # the source answered that it has no value for the target


class SlotState(StrEnum):
    """What a logical slot resolves to once all its attempts are considered."""

    OBSERVED = "observed"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    REFUSED = "refused"
    NOT_SAMPLED = "not_sampled"  # no attempt exists; reconstructed on read, never stored


class SampleFlag(StrEnum):
    # The observation came more than the tolerated delay after its scheduled slot began.
    LATE = "late"
    # The source successfully reported a value of zero.
    ZERO_REPORTED = "zero_reported"
    # The response said it was served from a cache, so the value may predate observed_at.
    EDGE_CACHED = "edge_cached"
    # No canonical entity was bound to the source-native id when the sample was taken.
    BINDING_UNKNOWN = "binding_unknown"


class SampleReading(FrozenModel):
    """What a sampling adapter returns: one outcome, with no storage or identity fields."""

    status: SampleStatus
    value: int | None = Field(default=None, ge=0)
    observed_at: AwareDatetime
    error_code: SourceErrorCode | None = None
    # Seconds the response says it spent in a cache, when the source reports it.
    cache_age_seconds: int | None = Field(default=None, ge=0)
    # Seconds the response says it may be cached for. A value can be this much older
    # than observed_at, and no later call can recover what this one did not record.
    cache_ttl_seconds: int | None = Field(default=None, ge=0)
    # The run must not send another request to this source (credentials or rate refused).
    halt_run: bool = False

    @model_validator(mode="after")
    def _shape_matches_status(self) -> Self:
        _check_shape(self.status, self.value, self.error_code)
        return self


class LogicalSlotKey(FrozenModel):
    source: NonEmptyStr
    metric: MetricId
    source_native_id: NonEmptyStr
    slot_start: AwareDatetime


class SampleAttempt(FrozenModel):
    """One append-only event: an attempt to sample one target, and how it ended."""

    source: NonEmptyStr
    metric: MetricId
    source_native_id: NonEmptyStr
    value: int | None = Field(default=None, ge=0)
    granularity: Granularity
    scheduled_for: AwareDatetime
    slot_start: AwareDatetime
    observed_at: AwareDatetime
    received_at: AwareDatetime
    # The binding believed when the sample was taken. An audit stamp only: reads
    # resolve the entity from the mapping history at observed_at.
    canonical_game_id: GameId | None = None
    mapping_version: int | None = Field(default=None, ge=1)
    binding_ref: str | None = None
    collection_run_id: NonEmptyStr
    attempt_id: NonEmptyStr
    status: SampleStatus
    quality_flags: tuple[SampleFlag, ...] = ()
    error_code: SourceErrorCode | None = None
    cache_age_seconds: int | None = Field(default=None, ge=0)
    cache_ttl_seconds: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check_attempt(self) -> Self:
        _check_shape(self.status, self.value, self.error_code)
        if not self.granularity.is_aligned(self.scheduled_for):
            raise ValueError("scheduled_for is not the start of a slot")
        if self.slot_start != self.granularity.floor(self.observed_at):
            raise ValueError("slot_start must be the slot that contains observed_at")
        if self.received_at < self.observed_at:
            raise ValueError("an attempt cannot be stored before it was observed")
        if len(set(self.quality_flags)) != len(self.quality_flags):
            raise ValueError("a quality flag is repeated")
        if (SampleFlag.ZERO_REPORTED in self.quality_flags) != (self.value == 0):
            raise ValueError("zero_reported is set exactly when a value of zero was observed")
        bound = self.canonical_game_id is not None
        if bound != (self.mapping_version is not None) or bound != (self.binding_ref is not None):
            raise ValueError("a binding stamp is recorded whole or not at all")
        if bound == (SampleFlag.BINDING_UNKNOWN in self.quality_flags):
            raise ValueError("binding_unknown is set exactly when no entity was bound")
        return self

    @property
    def slot_key(self) -> LogicalSlotKey:
        return LogicalSlotKey(
            source=self.source,
            metric=self.metric,
            source_native_id=self.source_native_id,
            slot_start=self.slot_start,
        )

    @property
    def is_success(self) -> bool:
        return self.status is SampleStatus.OBSERVED

    @property
    def crossed_slot(self) -> bool:
        """The observation landed in a later slot than the one the run was aimed at."""
        return self.slot_start != self.scheduled_for

    def delay(self) -> timedelta:
        return self.observed_at - self.scheduled_for


# With no success in a slot, the most informative outcome names the slot: the source
# answered, else we asked and got nothing, else we never asked.
_UNSUCCESSFUL_PRECEDENCE = (
    (SampleStatus.UNAVAILABLE, SlotState.UNAVAILABLE),
    (SampleStatus.FAILED, SlotState.FAILED),
    (SampleStatus.REFUSED, SlotState.REFUSED),
)


class SlotResolution(FrozenModel):
    """Everything known about one logical slot, and the one observation that speaks for it."""

    key: LogicalSlotKey
    granularity: Granularity
    state: SlotState
    # The earliest valid observed success in the slot, if there is one.
    selected: SampleAttempt | None = None
    # Every attempt recorded for the slot, in the order they were observed.
    attempts: tuple[SampleAttempt, ...] = ()

    @model_validator(mode="after")
    def _selection_matches_state(self) -> Self:
        if (self.selected is not None) != (self.state is SlotState.OBSERVED):
            raise ValueError("a slot has a selected observation exactly when it is observed")
        if (self.state is SlotState.NOT_SAMPLED) != (not self.attempts):
            raise ValueError("a slot is not_sampled exactly when it has no attempts")
        return self

    @property
    def value(self) -> int | None:
        """The slot's value, or None. None never means zero."""
        return None if self.selected is None else self.selected.value

    @property
    def duplicates(self) -> tuple[SampleAttempt, ...]:
        """Successful observations that were recorded but do not speak for the slot."""
        chosen = None if self.selected is None else self.selected.attempt_id
        return tuple(a for a in self.attempts if a.is_success and a.attempt_id != chosen)


def attempt_order(attempt: SampleAttempt) -> tuple[datetime, str]:
    return attempt.observed_at, attempt.attempt_id


def resolve_slot(
    key: LogicalSlotKey, granularity: Granularity, attempts: tuple[SampleAttempt, ...]
) -> SlotResolution:
    """The deterministic meaning of a slot, whatever order its attempts were stored in."""
    ordered = tuple(sorted(attempts, key=attempt_order))
    if any(attempt.slot_key != key for attempt in ordered):
        raise ValueError("an attempt does not belong to this slot")
    selected = next((attempt for attempt in ordered if attempt.is_success), None)
    if selected is not None:
        state = SlotState.OBSERVED
    elif not ordered:
        state = SlotState.NOT_SAMPLED
    else:
        statuses = {attempt.status for attempt in ordered}
        state = next(slot for status, slot in _UNSUCCESSFUL_PRECEDENCE if status in statuses)
    return SlotResolution(
        key=key, granularity=granularity, state=state, selected=selected, attempts=ordered
    )


def _check_shape(status: SampleStatus, value: int | None, error: SourceErrorCode | None) -> None:
    if (value is not None) != (status is SampleStatus.OBSERVED):
        raise ValueError("a value is present exactly when the status is 'observed'")
    if status is SampleStatus.OBSERVED and error is not None:
        raise ValueError("an observed sample carries no error code")
    if status in (SampleStatus.FAILED, SampleStatus.REFUSED) and error is None:
        raise ValueError("a failed or refused attempt records why")
