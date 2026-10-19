"""The Upcoming Opportunity Radar's deterministic core: dated events that have not begun.

It reports timing and context: which events start within the next day, week and month,
how far off each is, and whether its ramp has begun. It does not rank opportunities and
makes no recommendation. An event being on the radar means it is scheduled; whether it
is worth making content about is for the final opportunity model, which does not exist
yet.

Only games whose event intelligence is available appear. If no game's is, the radar
itself is unavailable, which is different from an empty radar.
"""

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta

from pydantic import AwareDatetime, Field

from metaspike.domain.events import EventType
from metaspike.domain.model import FrozenModel
from metaspike.events.explain import explain_event
from metaspike.events.heat import (
    Availability,
    EventIntelligence,
    Phase,
    UnavailableReason,
)
from metaspike.events.model import TimePrecision
from metaspike.events.rules import EVENT_HEAT_RULES_V1, EventHeatRules


class RadarItem(FrozenModel):
    canonical_game_id: str
    event_id: str
    event_type: EventType
    title: str
    starts_at: AwareDatetime
    time_precision: TimePrecision
    hours_until_start: float = Field(gt=0)
    # The shortest window the event falls in, as hours ahead.
    window_hours: int
    in_ramp: bool
    strength: float = Field(ge=0, le=1)
    contribution: float = Field(ge=0, le=1)
    # The game's event heat at `as_of`, from all of its events. Context, not a ranking.
    game_event_heat: float = Field(ge=0)
    sources: tuple[str, ...]
    text: str


class Radar(FrozenModel):
    availability: Availability
    rules_version: str
    rules_status: str
    as_of: AwareDatetime
    windows_hours: tuple[int, ...]
    items: tuple[RadarItem, ...] = ()
    games_with_coverage: int = Field(ge=0)
    games_without_coverage: int = Field(ge=0)
    unavailable_reasons: tuple[UnavailableReason, ...] = ()
    summary: str


def window_for(hours_until_start: float, rules: EventHeatRules) -> int | None:
    """The shortest window that holds an event this far off. A window includes its end:
    an event exactly 24 hours away is in the 24-hour window."""
    if hours_until_start <= 0:
        return None
    for window in rules.radar_windows_hours:
        if hours_until_start <= window:
            return window
    return None


def upcoming_radar(
    intelligence: Mapping[str, EventIntelligence] | Iterable[EventIntelligence],
    as_of: datetime,
    rules: EventHeatRules = EVENT_HEAT_RULES_V1,
) -> Radar:
    """Every upcoming event within the horizon, soonest first, for games with coverage."""
    results = list(intelligence.values() if isinstance(intelligence, Mapping) else intelligence)
    for result in results:
        if result.as_of != as_of or result.rules_version != rules.version:
            raise ValueError("every result must be for the same as_of and rules version")
    covered = [item for item in results if item.availability is Availability.AVAILABLE]
    without = len(results) - len(covered)
    if not covered:
        return Radar(
            availability=Availability.UNAVAILABLE,
            rules_version=rules.version,
            rules_status=rules.status,
            as_of=as_of,
            windows_hours=rules.radar_windows_hours,
            games_with_coverage=0,
            games_without_coverage=without,
            unavailable_reasons=(UnavailableReason.NO_EVENT_COVERAGE,),
            summary=(
                "The radar is unavailable: no game has current event coverage, so nothing "
                "is known about upcoming events. That is not the same as nothing upcoming."
            ),
        )
    items: list[RadarItem] = []
    for result in covered:
        for contribution in result.contributions:
            evaluation = contribution.evaluation
            if evaluation.phase is not Phase.UPCOMING:
                continue
            hours = (evaluation.starts_at - as_of) / timedelta(hours=1)
            window = window_for(hours, rules)
            sentence = explain_event(evaluation, as_of)
            if window is None or sentence is None or result.event_heat is None:
                continue
            items.append(
                RadarItem(
                    canonical_game_id=result.canonical_game_id,
                    event_id=evaluation.event_id,
                    event_type=evaluation.event_type,
                    title=evaluation.title,
                    starts_at=evaluation.starts_at,
                    time_precision=evaluation.time_precision,
                    hours_until_start=hours,
                    window_hours=window,
                    in_ramp=evaluation.in_ramp,
                    strength=evaluation.strength,
                    contribution=evaluation.contribution,
                    game_event_heat=result.event_heat,
                    sources=evaluation.sources,
                    text=sentence.text,
                )
            )
    items.sort(key=lambda item: (item.starts_at, -item.contribution, item.event_id))
    count = len(items)
    summary = (
        "No dated event starts within the horizon for any game with event coverage."
        if count == 0
        else f"{count} dated event{'' if count == 1 else 's'} start"
        f"{'s' if count == 1 else ''} within the horizon."
    )
    return Radar(
        availability=Availability.AVAILABLE,
        rules_version=rules.version,
        rules_status=rules.status,
        as_of=as_of,
        windows_hours=rules.radar_windows_hours,
        items=tuple(items),
        games_with_coverage=len(covered),
        games_without_coverage=without,
        summary=summary,
    )
