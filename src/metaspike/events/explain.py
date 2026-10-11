"""Sentences about events, rendered from an evaluation by fixed templates.

Every sentence is backed by one evaluated event: its type, its dates and its phase at
`as_of`. A sentence cannot be produced without one. No language model, no free text.

Time is told in whole UTC calendar days, the way a person reads a date: an event on
tomorrow's date is "tomorrow" whether it is 3 or 30 hours away.
"""

from datetime import UTC, datetime

from metaspike.domain.events import EventType
from metaspike.domain.scores import Reason
from metaspike.events.heat import EventEvaluation, Phase

# How each type is named, and its verb before and after the start.
_WORDS: dict[EventType, tuple[str, str, str]] = {
    EventType.RELEASE: ("Release", "launches", "launched"),
    EventType.MAJOR_PATCH: ("Major update", "launches", "launched"),
    EventType.MINOR_PATCH: ("Minor update", "launches", "launched"),
    EventType.SEASON: ("New season", "starts", "started"),
    EventType.WIPE: ("Wipe", "happens", "happened"),
    EventType.EXPANSION: ("Expansion", "launches", "launched"),
    EventType.DLC: ("DLC", "launches", "launched"),
    EventType.EXPEDITION: ("Expedition", "starts", "started"),
    EventType.BETA: ("Beta", "starts", "started"),
    EventType.PLAYTEST: ("Playtest", "starts", "started"),
    EventType.FREE_WEEKEND: ("Free weekend", "starts", "started"),
    EventType.TRAILER: ("Trailer", "premieres", "premiered"),
    EventType.SHOWCASE: ("Showcase", "airs", "aired"),
    EventType.PLATFORM_RELEASE: ("Platform release", "launches", "launched"),
    EventType.MAJOR_IN_GAME_EVENT: ("In-game event", "starts", "started"),
}

NO_RELEVANT_EVENT = Reason(
    code="no_relevant_event",
    text=(
        "No event is upcoming, running or recent for this game. Event coverage is current, "
        "so this is a finding, not a gap."
    ),
)


def type_label(event_type: EventType) -> str:
    return _WORDS[event_type][0]


def _day_gap(earlier: datetime, later: datetime) -> int:
    return (later.astimezone(UTC).date() - earlier.astimezone(UTC).date()).days


def _ahead(days: int) -> str:
    if days <= 0:
        return "later today"
    return "tomorrow" if days == 1 else f"in {days} days"


def _ago(days: int) -> str:
    if days <= 0:
        return "today"
    return "yesterday" if days == 1 else f"{days} days ago"


def explain_event(evaluation: EventEvaluation, as_of: datetime) -> Reason | None:
    """One sentence for one evaluated event, or None when it has nothing to say now."""
    noun, future, past = _WORDS[evaluation.event_type]
    title = evaluation.title
    since_start = _day_gap(evaluation.starts_at, as_of)
    if evaluation.phase is Phase.UPCOMING:
        when = _ahead(_day_gap(as_of, evaluation.starts_at))
        if evaluation.in_ramp:
            return Reason(code="event_upcoming", text=f"{noun} {future} {when}: {title}.")
        return Reason(
            code="event_scheduled",
            text=f"{noun} {future} {when}: {title}. It is not yet adding to event heat.",
        )
    if evaluation.phase is Phase.ACTIVE:
        if since_start <= 0:
            text = f"{noun} {past} today: {title}."
        else:
            text = f"{noun} {past} {_ago(since_start)} and is still running: {title}."
        if not evaluation.end_assumed:
            text += f" It ends {_ahead(_day_gap(as_of, evaluation.ends_at))}."
        return Reason(code="event_active", text=text)
    if evaluation.phase is Phase.DECAYING:
        instantaneous = evaluation.ends_at <= evaluation.starts_at
        if instantaneous or evaluation.end_assumed:
            if since_start <= 0:
                return Reason(code="event_started_today", text=f"{noun} {past} today: {title}.")
            return Reason(
                code="event_recent",
                text=(
                    f"{noun} {past} {_ago(since_start)}; event influence is still elevated: "
                    f"{title}."
                ),
            )
        ended = _ago(_day_gap(evaluation.ends_at, as_of))
        return Reason(
            code="event_ended_recently",
            text=f"{noun} ended {ended}; event influence is fading: {title}.",
        )
    return None
