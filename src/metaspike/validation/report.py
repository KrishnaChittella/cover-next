"""The validation report: what was tested, what was found, and how much it is worth.

A report is a typed, deterministic summary of one backtest. It states its own evidence
status by an explicit rule, lists every limitation it knows of, and keeps the checks
that were run for leakage. It reports what the data shows, including when that is
nothing, or bad.
"""

import statistics
from collections.abc import Sequence
from datetime import date

from pydantic import AwareDatetime, Field

from metaspike.domain.model import FrozenModel
from metaspike.validation.metrics import (
    STRATEGIES,
    LeadTime,
    Pair,
    StrategyResult,
    Stratum,
    Summary,
    by_day,
    confidence_bands,
    evaluate_strategy,
    lead_time,
    score_bins,
    summarise,
)
from metaspike.validation.outcomes import OutcomeAvailability
from metaspike.validation.rules import (
    EvidenceStatus,
    OutcomeRules,
    ValidationProtocol,
)
from metaspike.validation.snapshot import content_id

REPORT_FORMAT = "validation_report_v1"


class Requirement(FrozenModel):
    name: str
    required: int
    actual: int
    met: bool


class StatusCheck(FrozenModel):
    status: EvidenceStatus
    reached: bool
    requirements: tuple[Requirement, ...]
    rationale: str


class Evidence(FrozenModel):
    """How much the result is worth, by the protocol's stated thresholds."""

    status: EvidenceStatus
    checks: tuple[StatusCheck, ...]
    holdout_supported: bool
    holdout_rationale: str
    conclusion: str


class LeakageCheck(FrozenModel):
    name: str
    description: str
    checked: int = Field(ge=0)
    passed: bool


class Spread(FrozenModel):
    count: int = Field(ge=0)
    mean: float | None = None
    median: float | None = None


class EventBacktest(FrozenModel):
    """Whether events took any part. With no real event history they cannot be judged."""

    real_event_history: bool
    snapshots_with_event_intelligence: int = Field(ge=0)
    note: str


class InspectionEntry(FrozenModel):
    rank: int
    slug: str
    name: str
    score: float
    confidence: float
    outcome_availability: str
    unavailable_reason: str | None = None
    realized_uplift_72h: float | None = None
    movement_24h: float | None = None
    persistence_7d: float | None = None
    lead_time_days: int | None = None


class InspectionUnranked(FrozenModel):
    slug: str
    name: str
    reason: str


class InspectionDay(FrozenModel):
    """One past day's leaderboard, and what followed for each game on it."""

    day: date
    as_of: AwareDatetime
    ranked: tuple[InspectionEntry, ...]
    unranked: tuple[InspectionUnranked, ...]


class ValidationReport(FrozenModel):
    report_id: str = Field(pattern=r"^report_[0-9a-f]{32}$")
    format: str
    protocol_version: str
    outcome_rules_version: str
    score_version: str
    event_rules_version: str
    first_snapshot_day: date
    last_snapshot_day: date
    data_through: date
    snapshot_dates: int
    universe_size: int
    games_ranked: int
    games_with_outcome: int
    independent_sources: int
    recommendations: int
    ranked: int
    unranked: int
    primary_evaluable: int
    primary_unavailable: dict[str, int]
    overall: Summary
    strategies: tuple[StrategyResult, ...]
    score_bins: tuple[Stratum, ...]
    confidence_bands: tuple[Stratum, ...]
    movement_24h: Spread
    persistence_7d: Spread
    lead_time: LeadTime
    evidence: Evidence
    leakage_checks: tuple[LeakageCheck, ...]
    events: EventBacktest
    limitations: tuple[str, ...]
    inspection: tuple[InspectionDay, ...]
    inspection_truncated: bool
    # When the backtest was run. Not part of the report's identity.
    created_at: AwareDatetime


def assess_evidence(
    protocol: ValidationProtocol,
    snapshot_dates: int,
    games: int,
    evaluable_outcomes: int,
    independent_sources: int,
) -> Evidence:
    """The highest status whose every requirement, and every lower status's, is met."""
    actual = {
        "snapshot_dates": snapshot_dates,
        "games": games,
        "evaluable_outcomes": evaluable_outcomes,
        "independent_sources": independent_sources,
    }
    status = EvidenceStatus.FRAMEWORK_ONLY
    still_climbing = True
    checks = []
    for candidate, threshold in protocol.thresholds.items():
        requirements = tuple(
            Requirement(
                name=name,
                required=getattr(threshold, name),
                actual=value,
                met=value >= getattr(threshold, name),
            )
            for name, value in actual.items()
        )
        met = all(requirement.met for requirement in requirements)
        if candidate is EvidenceStatus.VALIDATED:
            requirements = (
                *requirements,
                Requirement(
                    name="held_out_period",
                    required=1,
                    actual=int(protocol.supports_holdout),
                    met=protocol.supports_holdout,
                ),
            )
            met = met and protocol.supports_holdout
        reached = still_climbing and met
        if reached:
            status = candidate
        else:
            still_climbing = False
        checks.append(
            StatusCheck(
                status=candidate,
                reached=reached,
                requirements=requirements,
                rationale=threshold.rationale,
            )
        )
    conclusions = {
        EvidenceStatus.FRAMEWORK_ONLY: (
            "Framework only. This sample shows that the backtest runs, that it does not "
            "look ahead and that its numbers are reproducible. It is too small to say "
            "anything about whether the score predicts demand, and nothing here should "
            "be read as evidence for or against it."
        ),
        EvidenceStatus.INSUFFICIENT_SAMPLE: (
            "Insufficient sample. Every metric can be computed, but the sample is too "
            "small for any of them to support a conclusion about the score."
        ),
        EvidenceStatus.EXPLORATORY: (
            "Exploratory. Large enough to look for patterns worth testing. It is still "
            "in-sample and has no held-out period, so it confirms nothing."
        ),
        EvidenceStatus.VALIDATED: "Validated on a held-out period under this protocol.",
    }
    return Evidence(
        status=status,
        checks=tuple(checks),
        holdout_supported=protocol.supports_holdout,
        holdout_rationale=protocol.holdout_rationale,
        conclusion=conclusions[status],
    )


def _spread(values: Sequence[float]) -> Spread:
    if not values:
        return Spread(count=0)
    return Spread(
        count=len(values), mean=statistics.fmean(values), median=statistics.median(values)
    )


def _inspection(pairs: Sequence[Pair], limit: int) -> tuple[tuple[InspectionDay, ...], bool]:
    days = by_day(pairs)
    kept = list(days.items())[-limit:] if limit else []
    result = []
    for day, day_pairs in kept:
        ranked = sorted(
            (pair for pair in day_pairs if pair[0].rank is not None),
            key=lambda pair: pair[0].rank or 0,
        )
        unranked = sorted(
            (pair[0] for pair in day_pairs if pair[0].rank is None), key=lambda item: item.slug
        )
        result.append(
            InspectionDay(
                day=day,
                as_of=day_pairs[0][0].as_of,
                ranked=tuple(
                    InspectionEntry(
                        rank=snapshot.rank or 0,
                        slug=snapshot.slug,
                        name=snapshot.name,
                        score=snapshot.score.value or 0.0,
                        confidence=snapshot.confidence.value if snapshot.confidence else 0.0,
                        outcome_availability=outcome.availability.value,
                        unavailable_reason=(
                            None
                            if outcome.unavailable_reason is None
                            else outcome.unavailable_reason.value
                        ),
                        realized_uplift_72h=outcome.realized_uplift_72h,
                        movement_24h=outcome.movement_24h,
                        persistence_7d=outcome.persistence_7d,
                        lead_time_days=outcome.lead_time_days,
                    )
                    for snapshot, outcome in ranked
                ),
                unranked=tuple(
                    InspectionUnranked(
                        slug=snapshot.slug,
                        name=snapshot.name,
                        reason=(
                            snapshot.score.unscored_reason.value
                            if snapshot.score.unscored_reason is not None
                            else "unscored"
                        ),
                    )
                    for snapshot in unranked
                ),
            )
        )
    return tuple(result), len(days) > len(kept)


def build_report(
    pairs: Sequence[Pair],
    rules: OutcomeRules,
    protocol: ValidationProtocol,
    event_rules_version: str,
    leakage_checks: Sequence[LeakageCheck],
    real_event_history: bool,
    data_through: date,
    created_at: AwareDatetime,
) -> ValidationReport:
    """Summarise a backtest. Deterministic: the same pairs give the same report."""
    if not pairs:
        raise ValueError("a report needs at least one snapshot")
    snapshots = [snapshot for snapshot, _ in pairs]
    ranked = [pair for pair in pairs if pair[0].rank is not None]
    evaluable = [
        outcome.realized_uplift_72h
        for _, outcome in ranked
        if outcome.realized_uplift_72h is not None
    ]
    unavailable: dict[str, int] = {}
    for _, outcome in pairs:
        if outcome.availability is OutcomeAvailability.UNAVAILABLE and outcome.unavailable_reason:
            reason = outcome.unavailable_reason.value
            unavailable[reason] = unavailable.get(reason, 0) + 1
    days = sorted({snapshot.data_cutoff_day for snapshot in snapshots})
    games_with_outcome = len(
        {s.canonical_game_id for s, o in ranked if o.realized_uplift_72h is not None}
    )
    sources = len({s.scored_signal.source for s in snapshots if s.scored_signal is not None})
    with_events = sum(1 for snapshot in snapshots if snapshot.events.availability == "available")
    inspection, truncated = _inspection(pairs, protocol.inspection_days)
    limitations = [
        "In-sample only: one period, one protocol, no held-out data.",
        "One demand source (Wikimedia daily pageviews). The outcome is measured on the "
        "same signal the score is computed from, so it measures whether attention "
        "continued, not whether a creator would have gained anything.",
        "Readership of an encyclopedia article is a proxy for demand. It is not views, "
        "players, search interest or creator supply.",
        "Point in time is by day: a value is taken as known once its day has ended. A "
        "recording made afterwards cannot show values the source later revised.",
        "Outcomes on neighbouring days overlap (each uses the next three days), so the "
        "observations are not independent and the counts overstate the evidence.",
        "No confidence intervals are reported. With this sample they would imply a "
        "precision that does not exist.",
        "Event intelligence took no part. There is no real historical event source, so "
        "event_heat_rules_v1 has not been backtested and nothing is claimed for it.",
        "metaspike_score_v0 is an internal attention measure, not the final "
        "creator-opportunity score. A result here is about v0 only.",
    ]
    fields: dict[str, object] = {
        "format": REPORT_FORMAT,
        "protocol_version": protocol.version,
        "outcome_rules_version": rules.version,
        "score_version": snapshots[0].score_version,
        "event_rules_version": event_rules_version,
        "first_snapshot_day": days[0],
        "last_snapshot_day": days[-1],
        "data_through": data_through,
        "snapshot_dates": len(days),
        "universe_size": max(snapshot.universe_size for snapshot in snapshots),
        "games_ranked": len({snapshot.canonical_game_id for snapshot, _ in ranked}),
        "games_with_outcome": games_with_outcome,
        "independent_sources": sources,
        "recommendations": len(pairs),
        "ranked": len(ranked),
        "unranked": len(pairs) - len(ranked),
        "primary_evaluable": len(evaluable),
        "primary_unavailable": dict(sorted(unavailable.items())),
        "overall": summarise(evaluable, rules),
        "strategies": tuple(
            evaluate_strategy(strategy, pairs, rules, protocol) for strategy in STRATEGIES
        ),
        "score_bins": score_bins(pairs, rules, protocol),
        "confidence_bands": confidence_bands(pairs, rules),
        "movement_24h": _spread([o.movement_24h for _, o in ranked if o.movement_24h is not None]),
        "persistence_7d": _spread(
            [o.persistence_7d for _, o in ranked if o.persistence_7d is not None]
        ),
        "lead_time": lead_time(pairs),
        "evidence": assess_evidence(
            protocol, len(days), games_with_outcome, len(evaluable), sources
        ),
        "leakage_checks": tuple(leakage_checks),
        "events": EventBacktest(
            real_event_history=real_event_history,
            snapshots_with_event_intelligence=with_events,
            note=(
                "Real event backtesting is unavailable: no historical event observations "
                "exist. Event Heat is carried in a snapshot when it was available at that "
                "moment, is not part of score v0, and is neither evaluated nor tuned here."
            ),
        ),
        "limitations": tuple(limitations),
        "inspection": inspection,
        "inspection_truncated": truncated,
        "created_at": created_at,
    }
    draft = ValidationReport.model_validate({**fields, "report_id": "report_" + "0" * 32})
    payload = draft.model_dump(mode="json", exclude={"report_id", "created_at"})
    return draft.model_copy(update={"report_id": content_id("report", payload)})
