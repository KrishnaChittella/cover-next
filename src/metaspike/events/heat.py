"""Event Heat: how much the events known for a game matter at a given moment.

Pure arithmetic over reconciled events, a rules version and an explicit `as_of`. There
is no clock here, no I/O and no constant: every number comes from the rules.

One event's strength over time, on 0 to 1:

    before the ramp      0
    in the ramp          pre_event_peak x (1 - time_to_start / ramp_days)      rising
    at the start         1
    while running        max(0.5 ^ (time_since_start / half_life), active_floor)
    after the end        strength_at_end x 0.5 ^ (time_since_end / half_life)   falling
    past the window      0

It never falls before the start and never rises after it. An event with no stated end
is taken to last its type's default duration, and says so. A cancelled or withdrawn
event has no strength.

An event's contribution is `importance x strength`. A game's heat combines its largest
few contributions; see `aggregate`.

Availability is decided first and separately. Heat is a number only when a source has
looked at the game's events recently enough. Otherwise the result is unavailable and
carries no number at all: not knowing is never reported as zero.
"""

import math
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from metaspike.domain.events import EventType
from metaspike.domain.model import FrozenModel
from metaspike.domain.scores import Reason
from metaspike.events.model import (
    CoverageCheck,
    EventRecord,
    EventStatus,
    TimePrecision,
)
from metaspike.events.rules import EventHeatRules

_DAY = timedelta(days=1)


class Phase(StrEnum):
    UPCOMING = "upcoming"
    ACTIVE = "active"
    DECAYING = "decaying"
    EXPIRED = "expired"
    # Cancelled or withdrawn. Listed for transparency; it has no strength.
    CALLED_OFF = "called_off"


class Availability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class UnavailableReason(StrEnum):
    # No source has ever looked for this game's events.
    NO_EVENT_COVERAGE = "no_event_coverage"
    # A source looked, but too long ago for what it found to be trusted now.
    EVENT_COVERAGE_STALE = "event_coverage_stale"


class EventEvaluation(FrozenModel):
    """One event at one moment: where it is in its life and what it is worth."""

    event_id: str
    canonical_game_id: str
    event_type: EventType
    title: str
    starts_at: AwareDatetime
    # The end used: the stated one, or the start plus the type's default duration.
    ends_at: AwareDatetime
    end_assumed: bool
    time_precision: TimePrecision
    status: EventStatus
    phase: Phase
    # True while an upcoming event is inside its ramp and so already has strength.
    in_ramp: bool
    strength: float = Field(ge=0, le=1)
    importance: float = Field(gt=0, le=1)
    contribution: float = Field(ge=0, le=1)
    sources: tuple[str, ...]


def _days(delta: timedelta) -> float:
    return delta / _DAY


def evaluate_event(record: EventRecord, rules: EventHeatRules, as_of: datetime) -> EventEvaluation:
    """Where one event stands at `as_of`, under `rules`."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    rule = rules.rule(record.event_type)
    start = record.starts_at
    stated_end = record.effective_end
    end = start + timedelta(days=rule.default_duration_days) if stated_end is None else stated_end
    in_ramp = False
    if record.status is not EventStatus.SCHEDULED:
        phase, strength = Phase.CALLED_OFF, 0.0
    elif as_of < start:
        phase = Phase.UPCOMING
        to_start = _days(start - as_of)
        in_ramp = rule.ramp_days > 0 and to_start < rule.ramp_days
        strength = rule.pre_event_peak * (1 - to_start / rule.ramp_days) if in_ramp else 0.0
    elif as_of < end:
        phase = Phase.ACTIVE
        strength = max(0.5 ** (_days(as_of - start) / rule.half_life_days), rule.active_floor)
    else:
        at_end = (
            max(0.5 ** (_days(end - start) / rule.half_life_days), rule.active_floor)
            if end > start
            else 1.0
        )
        since_end = _days(as_of - end)
        if since_end >= rule.decay_window_days:
            phase, strength = Phase.EXPIRED, 0.0
        else:
            phase = Phase.DECAYING
            strength = at_end * 0.5 ** (since_end / rule.half_life_days)
    strength = min(1.0, max(0.0, strength))
    return EventEvaluation(
        event_id=record.event_id,
        canonical_game_id=record.canonical_game_id,
        event_type=record.event_type,
        title=record.title,
        starts_at=start,
        ends_at=end,
        end_assumed=stated_end is None,
        time_precision=record.time_precision,
        status=record.status,
        phase=phase,
        in_ramp=in_ramp,
        strength=strength,
        importance=rule.importance,
        contribution=rule.importance * strength,
        sources=record.sources,
    )


class EventContribution(FrozenModel):
    """An evaluated event and its part in a game's heat."""

    evaluation: EventEvaluation
    # True for the events whose contributions were combined into the heat.
    counted: bool
    # Position among counted events, strongest first. None when not counted.
    rank: int | None = None
    # Why an event with strength was not counted, when it was not.
    not_counted_because: str | None = None


def aggregate(
    evaluations: Sequence[EventEvaluation], rules: EventHeatRules
) -> tuple[float, tuple[EventContribution, ...]]:
    """A game's heat from its events, and each event's part in it.

    Bounded by construction. Of events of the same type starting on the same UTC day
    only the strongest counts (a duplicate the reconciler could not safely merge must
    not count twice). Of what remains, only the `max_contributing_events` strongest
    count. Their contributions c, each between 0 and 1, combine as

        heat = scale x (1 - product(1 - c))

    so heat is at least the largest contribution, is below the scale unless a
    contribution is 1, and never falls when an event is added. The order of the
    result is counted events strongest first, then every other event by start.
    """

    def strongest_first(item: EventEvaluation) -> tuple[float, datetime, str]:
        return (-item.contribution, item.starts_at, item.event_id)

    live = sorted((item for item in evaluations if item.contribution > 0), key=strongest_first)
    slots: dict[tuple[EventType, str], EventEvaluation] = {}
    shadowed: dict[str, str] = {}
    for item in live:
        slot = (item.event_type, item.starts_at.astimezone(UTC).date().isoformat())
        if slot in slots:
            shadowed[item.event_id] = slots[slot].event_id
        else:
            slots[slot] = item
    leaders = sorted(slots.values(), key=strongest_first)
    counted = leaders[: rules.max_contributing_events]
    remainder = math.prod(1 - item.contribution for item in counted)
    heat = rules.scale * (1 - remainder)

    counted_ids = {item.event_id: rank for rank, item in enumerate(counted, start=1)}
    contributions: list[EventContribution] = []
    for item in counted:
        contributions.append(
            EventContribution(evaluation=item, counted=True, rank=counted_ids[item.event_id])
        )
    rest = sorted(
        (item for item in evaluations if item.event_id not in counted_ids),
        key=lambda item: (item.starts_at, item.event_id),
    )
    for item in rest:
        reason: str | None = None
        if item.event_id in shadowed:
            reason = "same_type_and_start_day_as_a_stronger_event"
        elif item.contribution > 0:
            reason = "beyond_max_contributing_events"
        contributions.append(
            EventContribution(evaluation=item, counted=False, not_counted_because=reason)
        )
    return heat, tuple(contributions)


class EventIntelligence(FrozenModel):
    """What is known about a game's events at a moment, or that it is not known."""

    canonical_game_id: str
    availability: Availability
    rules_version: str
    rules_status: str
    as_of: AwareDatetime
    # A number only when available. Zero then means: looked, and nothing relevant.
    event_heat: float | None = Field(default=None, ge=0)
    contributions: tuple[EventContribution, ...] = ()
    top_event_id: str | None = None
    summary: str
    reasons: tuple[Reason, ...] = ()
    unavailable_reasons: tuple[UnavailableReason, ...] = ()
    # When a source last looked at this game's events, and which sources have.
    coverage_checked_at: AwareDatetime | None = None
    coverage_sources: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _availability_is_consistent(self) -> Self:
        available = self.availability is Availability.AVAILABLE
        if available != (self.event_heat is not None):
            raise ValueError("event_heat is a number exactly when intelligence is available")
        if available == bool(self.unavailable_reasons):
            raise ValueError("unavailable_reasons are given exactly when unavailable")
        if not available and (self.contributions or self.top_event_id is not None):
            raise ValueError("an unavailable result carries no events")
        return self


def relevant(evaluation: EventEvaluation, rules: EventHeatRules, as_of: datetime) -> bool:
    """Worth listing: running, recently over, or starting within the horizon."""
    if evaluation.phase in (Phase.ACTIVE, Phase.DECAYING):
        return True
    return evaluation.phase is Phase.UPCOMING and evaluation.starts_at - as_of <= timedelta(
        days=rules.horizon_days
    )


def coverage_of(
    canonical_game_id: str,
    records: Iterable[EventRecord],
    checks: Iterable[CoverageCheck],
    as_of: datetime,
) -> tuple[datetime | None, tuple[str, ...]]:
    """When a source last looked at the game's events, as known at `as_of`.

    A check counts, and so does an observation: a source that reported an event had
    looked. Anything dated after `as_of` is not yet known and is ignored.
    """
    seen: list[tuple[datetime, str]] = [
        (check.checked_at, check.source)
        for check in checks
        if check.canonical_game_id == canonical_game_id and check.checked_at <= as_of
    ]
    for record in records:
        if record.canonical_game_id != canonical_game_id:
            continue
        seen.extend(
            (revision.observed_at, revision.source)
            for revision in record.revisions
            if revision.observed_at <= as_of
        )
    if not seen:
        return None, ()
    return max(moment for moment, _ in seen), tuple(sorted({source for _, source in seen}))
