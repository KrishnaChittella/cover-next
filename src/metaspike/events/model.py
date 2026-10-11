"""Events as observed and as reconciled.

Two layers, kept apart. An `EventObservation` is what one source said at one moment
about one event of one game; it is immutable and append-only. An `EventRecord` is what
those observations add up to at a point in time: one real-world event, with its current
dates and the history of how they changed. A record is always derived, never stored, so
nothing is ever overwritten.

Time is explicit everywhere. An observation carries when it was observed, and a record
is always "as of" a moment; nothing in this package reads a clock.
"""

import hashlib
import json
from datetime import UTC, datetime, time, timedelta
from datetime import date as Date
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from metaspike.domain.events import EventType
from metaspike.domain.games import Catalogue, GameId, NonEmptyStr, SourceIdentifier
from metaspike.domain.model import FrozenModel


class EventStatus(StrEnum):
    """What the source currently says about the event taking place."""

    # Dated and expected to happen, or to have happened, as stated.
    SCHEDULED = "scheduled"
    # Called off by its publisher. It is kept, and contributes nothing.
    CANCELLED = "cancelled"
    # The source no longer stands by the report (it was wrong). Kept; contributes nothing.
    WITHDRAWN = "withdrawn"


class TimePrecision(StrEnum):
    # Only the UTC day is known. The event is taken to start at 00:00 UTC that day.
    DAY = "day"
    # The moment is known.
    EXACT = "exact"


class MatchRule(StrEnum):
    """Why an observation was taken to be about an event already known."""

    # Nothing matched: this observation is the first of a new event.
    NEW_EVENT = "new_event"
    SAME_SOURCE_NATIVE_ID = "same_source_native_id"
    SAME_SOURCE_TYPE_AND_DAY = "same_source_type_and_day"
    CROSS_SOURCE_SAME_REFERENCE = "cross_source_same_reference"
    CROSS_SOURCE_SAME_TYPE_DAY_AND_TITLE = "cross_source_same_type_day_and_title"


def day_start(day: Date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def day_end_exclusive(day: Date) -> datetime:
    """The first moment after an inclusive end day."""
    return day_start(day) + timedelta(days=1)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(*parts: object) -> str:
    return hashlib.sha256(_canonical(parts).encode("utf-8")).hexdigest()


class EventReport(FrozenModel):
    """What a source says about an event, before the game is resolved.

    The game is named the way the source names it. It becomes an observation only
    through `observe`, which is where an unresolved game is refused.
    """

    game: SourceIdentifier
    event_type: EventType
    title: NonEmptyStr
    starts_at: AwareDatetime
    ends_at: AwareDatetime | None = None
    time_precision: TimePrecision = TimePrecision.DAY
    status: EventStatus = EventStatus.SCHEDULED
    # The source's own id for the event, when it has one. The strongest identity there is.
    source_event_id: NonEmptyStr | None = None
    # A URL or citation, kept only where the source's policy allows it.
    reference: NonEmptyStr | None = None


class EventObservation(FrozenModel):
    """One source's statement about one event of one resolved game, at one moment."""

    observation_id: str = Field(pattern=r"^obs_[0-9a-f]{32}$")
    canonical_game_id: GameId
    source: NonEmptyStr
    observed_at: AwareDatetime
    event_type: EventType
    title: NonEmptyStr
    starts_at: AwareDatetime
    ends_at: AwareDatetime | None = None
    time_precision: TimePrecision
    status: EventStatus
    source_event_id: NonEmptyStr | None = None
    reference: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.ends_at is not None and self.ends_at < self.starts_at:
            raise ValueError("an event cannot end before it starts")
        expected = observation_id(self)
        if self.observation_id != expected:
            raise ValueError("observation_id does not match the observation's content")
        return self

    @property
    def start_day(self) -> Date:
        return self.starts_at.astimezone(UTC).date()


def observation_id(observation: "EventObservation | dict[str, object]") -> str:
    """A content hash: the same statement at the same moment is the same observation."""
    data = (
        observation.model_dump(mode="json", exclude={"observation_id"})
        if isinstance(observation, EventObservation)
        else {key: value for key, value in observation.items() if key != "observation_id"}
    )
    return f"obs_{digest(data)[:32]}"


class UnresolvedGameError(Exception):
    """An event was reported for a game the catalogue cannot name exactly."""


def resolve_game(catalogue: Catalogue, identifier: SourceIdentifier) -> GameId:
    """The canonical game a source identifier is bound to, or a refusal.

    This is the resolver boundary: an exact match on the binding the catalogue already
    holds. There is no title matching here, fuzzy or otherwise, and no fallback.
    """
    matches = [
        game.canonical_game_id
        for game in catalogue.games
        if game.identifier_for(identifier.source) == identifier
    ]
    if len(matches) != 1:
        raise UnresolvedGameError(
            f"{identifier.source}:{identifier.namespace}:{identifier.value} is not bound to "
            "exactly one canonical game"
        )
    return matches[0]


def _utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC)


def observe(report: EventReport, catalogue: Catalogue, observed_at: datetime) -> EventObservation:
    """Turn a source's report into an observation of a resolved game, or refuse."""
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    starts_at = _utc(report.starts_at)
    ends_at = None if report.ends_at is None else _utc(report.ends_at)
    if report.time_precision is TimePrecision.DAY:
        # A day is a day: normalised to its first moment so identity never depends on
        # the hour a source happened to attach.
        starts_at = day_start(starts_at.date())
        ends_at = None if ends_at is None else day_start(ends_at.date())
    data: dict[str, object] = {
        "canonical_game_id": resolve_game(catalogue, report.game),
        "source": report.game.source,
        "observed_at": _utc(observed_at).isoformat().replace("+00:00", "Z"),
        "event_type": report.event_type.value,
        "title": report.title,
        "starts_at": starts_at.isoformat().replace("+00:00", "Z"),
        "ends_at": None if ends_at is None else ends_at.isoformat().replace("+00:00", "Z"),
        "time_precision": report.time_precision.value,
        "status": report.status.value,
        "source_event_id": report.source_event_id,
        "reference": report.reference,
    }
    return EventObservation.model_validate({**data, "observation_id": observation_id(data)})


class CoverageCheck(FrozenModel):
    """A source looked for a game's events at a moment, whatever it found.

    This is what separates "no relevant event" from "nobody looked". Without a check a
    game's event intelligence is unavailable; it is never zero.
    """

    canonical_game_id: GameId
    source: NonEmptyStr
    checked_at: AwareDatetime


class EventRevision(FrozenModel):
    """One observation's effect on an event: what it matched on and what it changed."""

    observation_id: str
    source: str
    observed_at: AwareDatetime
    matched_by: MatchRule
    changed: tuple[str, ...]


class EventRecord(FrozenModel):
    """One real-world event of one game, as known at a moment."""

    event_id: str = Field(pattern=r"^evt_[0-9a-f]{24}$")
    canonical_game_id: GameId
    event_type: EventType
    title: NonEmptyStr
    starts_at: AwareDatetime
    ends_at: AwareDatetime | None = None
    time_precision: TimePrecision
    status: EventStatus
    first_observed_at: AwareDatetime
    last_observed_at: AwareDatetime
    sources: tuple[str, ...]
    source_event_ids: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    revisions: tuple[EventRevision, ...]

    @property
    def start_day(self) -> Date:
        return self.starts_at.astimezone(UTC).date()

    @property
    def end_day(self) -> Date | None:
        """The last day of the event, inclusive, for day-level display."""
        if self.ends_at is None:
            return None
        end = self.ends_at.astimezone(UTC)
        if self.time_precision is TimePrecision.DAY:
            return end.date()
        # An exact end at midnight ends the day before.
        return (end - timedelta(microseconds=1)).date() if end > self.starts_at else end.date()

    @property
    def effective_end(self) -> datetime | None:
        """The first moment the event is over, when its end is known."""
        if self.ends_at is None:
            return None
        if self.time_precision is TimePrecision.DAY:
            return day_end_exclusive(self.ends_at.astimezone(UTC).date())
        return self.ends_at
