"""Event intelligence as published: the engine's results turned into contract views.

Like `mapping.py`, this is a boundary. The engine's models never reach an artifact
directly, so a change to them cannot silently change what is published.
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta

from metaspike.domain.games import Catalogue
from metaspike.events.explain import explain_event, type_label
from metaspike.events.heat import Availability, EventContribution, EventIntelligence
from metaspike.events.intelligence import assess_events
from metaspike.events.model import CoverageCheck, EventRecord
from metaspike.events.radar import Radar, upcoming_radar
from metaspike.events.rules import EventHeatRules
from metaspike.pipeline.collection import CollectionReport
from metaspike.publishing.artifacts import (
    EVENT_SCOPE_NOTE,
    RADAR_SCOPE_NOTE,
    EventContributionView,
    EventHeatMethodology,
    EventIntelligenceView,
    EventTypeRuleView,
    EventView,
    ParameterView,
    RadarArtifact,
    RadarItemView,
    ReasonView,
    TermView,
    game_file,
)

# Event values are published exactly as computed. Nothing here is rounded: a heat one
# second into a ramp is tiny but positive, and rounding it would publish 0, which means
# "a source looked and nothing contributes". How a number is shown is the interface's
# concern, never the contract's.


def event_as_of(report: CollectionReport) -> datetime:
    """The moment a run's events are evaluated at.

    The end of the last day the run's window covers, or the moment of collection if
    that is earlier. A replay of a past day therefore evaluates events as of that day,
    not as of whenever the replay happens to be run.
    """
    return min(report.observed_at, report.window.end + timedelta(days=1))


def assess_catalogue(
    catalogue: Catalogue,
    records: Sequence[EventRecord],
    checks: Iterable[CoverageCheck],
    as_of: datetime,
    rules: EventHeatRules,
) -> dict[str, EventIntelligence]:
    """Event intelligence for every game in the catalogue, keyed by canonical id.

    A record for a game the catalogue does not hold is refused: an event is never
    published against a game that is not there.
    """
    known = {game.canonical_game_id for game in catalogue.games}
    strays = sorted({record.canonical_game_id for record in records} - known)
    if strays:
        raise ValueError(f"event records for games not in the catalogue: {', '.join(strays)}")
    kept = tuple(checks)
    return {
        game.canonical_game_id: assess_events(game.canonical_game_id, records, kept, as_of, rules)
        for game in catalogue.games
    }


def _contribution_view(item: EventContribution, as_of: datetime) -> EventContributionView:
    evaluation = item.evaluation
    sentence = explain_event(evaluation, as_of)
    return EventContributionView(
        event_id=evaluation.event_id,
        event_type=evaluation.event_type.value,
        title=evaluation.title,
        starts_at=evaluation.starts_at,
        ends_at=evaluation.ends_at,
        end_assumed=evaluation.end_assumed,
        time_precision=evaluation.time_precision.value,
        status=evaluation.status.value,
        phase=evaluation.phase.value,
        in_ramp=evaluation.in_ramp,
        strength=evaluation.strength,
        importance=evaluation.importance,
        contribution=evaluation.contribution,
        counted=item.counted,
        rank=item.rank,
        not_counted_because=item.not_counted_because,
        sources=evaluation.sources,
        text=None if sentence is None else sentence.text,
    )


def event_intelligence_view(result: EventIntelligence) -> EventIntelligenceView:
    heat = result.event_heat
    return EventIntelligenceView(
        availability=result.availability.value,
        rules_version=result.rules_version,
        rules_status=result.rules_status,
        as_of=result.as_of,
        event_heat=heat,
        top_event_id=result.top_event_id,
        summary=result.summary,
        reasons=tuple(ReasonView(code=r.code, text=r.text) for r in result.reasons),
        unavailable_reasons=tuple(reason.value for reason in result.unavailable_reasons),
        coverage_checked_at=result.coverage_checked_at,
        coverage_sources=result.coverage_sources,
        contributions=tuple(
            _contribution_view(item, result.as_of) for item in result.contributions
        ),
    )


def event_views(result: EventIntelligence) -> tuple[EventView, ...]:
    """The older, date-level list of a game's events, kept for existing consumers."""
    if result.availability is not Availability.AVAILABLE:
        return ()
    views = []
    for item in result.contributions:
        evaluation = item.evaluation
        ends_on = None
        if not evaluation.end_assumed and evaluation.ends_at > evaluation.starts_at:
            ends_on = (evaluation.ends_at - timedelta(microseconds=1)).date()
        views.append(
            EventView(
                canonical_game_id=evaluation.canonical_game_id,
                event_type=evaluation.event_type.value,
                title=evaluation.title,
                starts_on=evaluation.starts_at.date(),
                ends_on=ends_on,
                source=evaluation.sources[0],
            )
        )
    return tuple(views)


def build_radar(
    catalogue: Catalogue,
    intelligence: Mapping[str, EventIntelligence],
    as_of: datetime,
    rules: EventHeatRules,
    header: Mapping[str, object],
) -> RadarArtifact:
    radar: Radar = upcoming_radar(intelligence, as_of, rules)
    by_id = {game.canonical_game_id: game for game in catalogue.games}
    items = []
    for item in radar.items:
        game = by_id[item.canonical_game_id]
        items.append(
            RadarItemView(
                canonical_game_id=game.canonical_game_id,
                slug=game.slug,
                name=game.name,
                event_id=item.event_id,
                event_type=item.event_type.value,
                title=item.title,
                starts_at=item.starts_at,
                time_precision=item.time_precision.value,
                hours_until_start=item.hours_until_start,
                window_hours=item.window_hours,
                in_ramp=item.in_ramp,
                strength=item.strength,
                contribution=item.contribution,
                game_event_heat=item.game_event_heat,
                sources=item.sources,
                text=item.text,
                artifact_path=game_file(game.slug),
            )
        )
    return RadarArtifact.model_validate(
        {
            **header,
            "scope_note": RADAR_SCOPE_NOTE,
            "availability": radar.availability.value,
            "rules_version": radar.rules_version,
            "rules_status": radar.rules_status,
            "as_of": radar.as_of,
            "windows_hours": radar.windows_hours,
            "games_with_coverage": radar.games_with_coverage,
            "games_without_coverage": radar.games_without_coverage,
            "unavailable_reasons": tuple(reason.value for reason in radar.unavailable_reasons),
            "summary": radar.summary,
            "items": tuple(items),
        }
    )


def _number(value: float) -> str:
    return f"{value:g}"


def event_heat_methodology(rules: EventHeatRules) -> EventHeatMethodology:
    """The rules as published: every constant, with its rationale."""
    return EventHeatMethodology(
        version=rules.version,
        status=rules.status,
        scope_note=EVENT_SCOPE_NOTE,
        range=(0, rules.scale),
        terms=(
            TermView(
                name="strength, before the start",
                formula="pre_event_peak * (1 - days_to_start / ramp_days), inside the ramp; else 0",
                rationale=(
                    "Anticipation builds as a dated event approaches. Outside the ramp an "
                    "event is scheduled and has no strength yet."
                ),
            ),
            TermView(
                name="strength, while running",
                formula="max(0.5 ^ (days_since_start / half_life_days), active_floor)",
                rationale=(
                    "The start is the peak. Novelty then halves every half-life, but an "
                    "event that is still running never falls below its floor."
                ),
            ),
            TermView(
                name="strength, after the end",
                formula="strength_at_end * 0.5 ^ (days_since_end / half_life_days), "
                "until decay_window_days; then 0",
                rationale=(
                    "Influence fades from where it stood when the event ended and stops "
                    "counting once the window has passed."
                ),
            ),
            TermView(
                name="contribution",
                formula="importance * strength",
                rationale="A type's importance is also the most one event of it can contribute.",
            ),
            TermView(
                name="event_heat",
                formula="scale * (1 - product(1 - contribution)), over the largest "
                "max_contributing_events contributions",
                rationale=rules.aggregation,
            ),
        ),
        aggregation=rules.aggregation,
        availability=(
            TermView(
                name="available, heat 0",
                formula="a source looked within coverage_max_age_days; no relevant event",
                rationale="A finding: someone looked and there is nothing.",
            ),
            TermView(
                name="available, heat above 0",
                formula="a source looked within coverage_max_age_days; relevant events exist",
                rationale="The events and each one's contribution are listed.",
            ),
            TermView(
                name="unavailable",
                formula="no source has looked, or the last look is older than "
                "coverage_max_age_days",
                rationale=(
                    "Not knowing is never reported as zero. The heat is null and the reason "
                    "is given."
                ),
            ),
        ),
        parameters=(
            ParameterView(
                name="scale", value=_number(rules.scale), rationale=rules.scale_rationale
            ),
            ParameterView(
                name="max_contributing_events",
                value=str(rules.max_contributing_events),
                rationale=rules.max_contributing_events_rationale,
            ),
            ParameterView(
                name="coverage_max_age_days",
                value=_number(rules.coverage_max_age_days),
                rationale=rules.coverage_max_age_rationale,
            ),
            ParameterView(
                name="horizon_days",
                value=_number(rules.horizon_days),
                rationale=rules.horizon_rationale,
            ),
            ParameterView(
                name="radar_windows_hours",
                value=", ".join(str(hours) for hours in rules.radar_windows_hours),
                rationale=rules.radar_windows_rationale,
            ),
        ),
        types=tuple(
            EventTypeRuleView(
                event_type=event_type.value,
                label=type_label(event_type),
                importance=rule.importance,
                ramp_days=rule.ramp_days,
                pre_event_peak=rule.pre_event_peak,
                default_duration_days=rule.default_duration_days,
                half_life_days=rule.half_life_days,
                active_floor=rule.active_floor,
                decay_window_days=rule.decay_window_days,
                rationale=rule.rationale,
            )
            for event_type, rule in rules.types.items()
        ),
        radar_windows_hours=rules.radar_windows_hours,
        limitations=(
            "Internal and uncalibrated: every value is an engineering prior, not fitted to "
            "outcomes and not backtested.",
            "No event source is collected yet. Until one is, event intelligence is "
            "unavailable for every game.",
            "Event Heat is not part of metaspike_score_v0 and does not affect any rank.",
            "Events are matched by exact rules only. Two reports of one event that share no "
            "id, reference or title stay separate; heat counts one event per type and "
            "start day so that such a duplicate cannot count twice.",
            "A source without native event ids that moves a date is seen as reporting a new event.",
            "Importance is per type, not per game: a small game's release and a large "
            "game's are treated alike.",
            "All days are UTC. An event known only to the day starts at 00:00 UTC.",
        ),
    )
