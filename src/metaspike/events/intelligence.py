"""A game's event intelligence at a moment: available with a heat, or unavailable.

This is the one place availability is decided, so the three cases can never be
confused:

    A. a source has looked recently and nothing is relevant  -> available, heat 0
    B. no source has looked, or not recently enough          -> unavailable, no number
    C. events are known and current                          -> available, heat above 0
"""

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta

from metaspike.domain.scores import Reason
from metaspike.events.explain import NO_RELEVANT_EVENT, explain_event
from metaspike.events.heat import (
    Availability,
    EventIntelligence,
    UnavailableReason,
    aggregate,
    coverage_of,
    evaluate_event,
    relevant,
)
from metaspike.events.model import CoverageCheck, EventRecord
from metaspike.events.rules import EVENT_HEAT_RULES_V1, EventHeatRules

_UNAVAILABLE_TEXT = {
    UnavailableReason.NO_EVENT_COVERAGE: (
        "Event intelligence is unavailable: no source has looked for this game's events. "
        "That is not the same as no events."
    ),
    UnavailableReason.EVENT_COVERAGE_STALE: (
        "Event intelligence is unavailable: the last look at this game's events is too old "
        "to rely on. That is not the same as no events."
    ),
}


def assess_events(
    canonical_game_id: str,
    records: Sequence[EventRecord],
    checks: Iterable[CoverageCheck],
    as_of: datetime,
    rules: EventHeatRules = EVENT_HEAT_RULES_V1,
) -> EventIntelligence:
    """Event intelligence for one game. `records` must already be as known at `as_of`."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    own = [record for record in records if record.canonical_game_id == canonical_game_id]
    checked_at, sources = coverage_of(canonical_game_id, own, checks, as_of)
    reason: UnavailableReason | None = None
    if checked_at is None:
        reason = UnavailableReason.NO_EVENT_COVERAGE
    elif as_of - checked_at > timedelta(days=rules.coverage_max_age_days):
        reason = UnavailableReason.EVENT_COVERAGE_STALE
    if reason is not None:
        return EventIntelligence(
            canonical_game_id=canonical_game_id,
            availability=Availability.UNAVAILABLE,
            rules_version=rules.version,
            rules_status=rules.status,
            as_of=as_of,
            summary=_UNAVAILABLE_TEXT[reason],
            reasons=(Reason(code=reason.value, text=_UNAVAILABLE_TEXT[reason]),),
            unavailable_reasons=(reason,),
            coverage_checked_at=checked_at,
            coverage_sources=sources,
        )
    evaluations = [evaluate_event(record, rules, as_of) for record in own]
    heat, contributions = aggregate(
        [item for item in evaluations if relevant(item, rules, as_of)], rules
    )
    reasons: list[Reason] = []
    for contribution in contributions:
        sentence = explain_event(contribution.evaluation, as_of)
        if sentence is not None:
            reasons.append(sentence)
    if not reasons:
        reasons.append(NO_RELEVANT_EVENT)
    top = next((item for item in contributions if item.counted), None)
    return EventIntelligence(
        canonical_game_id=canonical_game_id,
        availability=Availability.AVAILABLE,
        rules_version=rules.version,
        rules_status=rules.status,
        as_of=as_of,
        event_heat=heat,
        contributions=contributions,
        top_event_id=None if top is None else top.evaluation.event_id,
        summary=reasons[0].text,
        reasons=tuple(reasons),
        coverage_checked_at=checked_at,
        coverage_sources=sources,
    )
