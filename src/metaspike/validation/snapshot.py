"""A recommendation snapshot: exactly what MetaSpike knew and said about one game at a
moment. And `predict`, the only function that makes one.

A snapshot is enough to audit a past recommendation by itself, including when it was
made (`as_of`) and the newest demand day that could be seen at that moment
(`latest_due_day`, and `latest_observed_day` for the game). It holds computed
values and the versions that produced them, never a source's raw payload, and never
anything about what happened next. Outcomes live in a separate object (`outcomes.py`)
that points back at a snapshot by id.

`snapshot_id` is a hash of the snapshot's content. `created_at`, the moment the
evaluation was run, is deliberately outside it: the same prediction rebuilt later has
the same id, which is how reproduction is checked.
"""

import hashlib
import json
from collections.abc import Iterable, Sequence
from datetime import date, datetime

from pydantic import AwareDatetime, Field

from metaspike.domain.games import Catalogue
from metaspike.domain.model import FrozenModel
from metaspike.domain.scores import ConfidenceResult, Explanation, ScoreResult
from metaspike.domain.signals import CollectionStatus, SignalKey
from metaspike.domain.timeseries import Granularity
from metaspike.events.heat import Availability, EventIntelligence
from metaspike.events.intelligence import assess_events
from metaspike.events.model import CoverageCheck, EventRecord
from metaspike.events.rules import EVENT_HEAT_RULES_V1, EventHeatRules
from metaspike.features.attention import AttentionFeatures
from metaspike.pipeline.assess import GameAssessment, assess
from metaspike.scoring.v0 import SCORE_VERSION, V0_INPUT_SIGNAL
from metaspike.validation.history import (
    LeakageError,
    PredictionInputs,
    ensure_available,
    latest_due_at,
)

SNAPSHOT_FORMAT = "recommendation_snapshot_v2"


class SourceCoverageAt(FrozenModel):
    source: str
    status: str
    latest_observed_day: date | None = None
    observed_days: int = 0


class EventSummaryAt(FrozenModel):
    """Event intelligence as it stood at the snapshot. Unavailable carries no heat."""

    availability: str
    rules_version: str
    event_heat: float | None = None
    top_event_id: str | None = None
    unavailable_reasons: tuple[str, ...] = ()
    summary: str


class RecommendationSnapshot(FrozenModel):
    snapshot_id: str = Field(pattern=r"^snap_[0-9a-f]{32}$")
    format: str
    # When the recommendation was simulated.
    as_of: AwareDatetime
    # The last whole UTC day that had ended by `as_of`. A calendar fact: it says nothing
    # about what data existed.
    evaluation_day: date
    # The newest day the score's input source had published by `as_of`, under its
    # publication lag. Nothing later than this could be seen.
    latest_due_day: date
    # The newest day this game was actually observed on, among those visible. None when
    # it had no visible observation. Never later than `latest_due_day`.
    latest_observed_day: date | None = None
    # How long after a day ends the input source publishes it, in hours.
    publication_lag_hours: float = Field(ge=0)
    input_window_start: date
    input_window_end: date
    canonical_game_id: str
    slug: str
    name: str
    lifecycle_state: str
    score_version: str
    score: ScoreResult
    # Position among the games scored at this moment. None when unscored.
    rank: int | None = Field(default=None, ge=1)
    universe_size: int = Field(ge=1)
    ranked_count: int = Field(ge=0)
    confidence: ConfidenceResult | None = None
    # The features of the scored signal, as computed from the inputs at this moment.
    scored_signal: SignalKey | None = None
    features: AttentionFeatures | None = None
    explanation: Explanation
    source_coverage: tuple[SourceCoverageAt, ...]
    events: EventSummaryAt
    # When the evaluation that produced this record was run. Not part of the identity.
    created_at: AwareDatetime


def content_id(prefix: str, payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return f"{prefix}_{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:32]}"


def snapshot_id(fields: dict[str, object]) -> str:
    """Identity from content: everything except the id itself and `created_at`."""
    payload = {k: v for k, v in fields.items() if k not in ("snapshot_id", "created_at")}
    return content_id("snap", payload)


def rank_scored(catalogue: Catalogue, assessments: Iterable[GameAssessment]) -> dict[str, int]:
    """Rank the games scored at this moment: score, then confidence, then slug.

    The same order the published leaderboard uses. A game that is unscored now is not
    ranked, whatever became of it later.
    """
    slugs = {game.canonical_game_id: game.slug for game in catalogue.games}
    scored = [
        (
            -item.score.value,
            -item.confidence.value,
            slugs[item.canonical_game_id],
            item.canonical_game_id,
        )
        for item in assessments
        if item.score.value is not None and item.confidence is not None
    ]
    return {game_id: rank for rank, (*_, game_id) in enumerate(sorted(scored), start=1)}


def _coverage(assessment: GameAssessment) -> tuple[SourceCoverageAt, ...]:
    coverage = []
    for signal in assessment.signals.signals:
        observed = (
            [point.period_day for point in signal.series.points if point.value is not None]
            if signal.series is not None
            else []
        )
        status = signal.status
        coverage.append(
            SourceCoverageAt(
                source=signal.key.source,
                status="covered"
                if status is CollectionStatus.COLLECTED and observed
                else status.value,
                latest_observed_day=max(observed, default=None),
                observed_days=len(observed),
            )
        )
    return tuple(sorted(coverage, key=lambda item: item.source))


def _events(result: EventIntelligence) -> EventSummaryAt:
    return EventSummaryAt(
        availability=result.availability.value,
        rules_version=result.rules_version,
        event_heat=result.event_heat if result.availability is Availability.AVAILABLE else None,
        top_event_id=result.top_event_id,
        unavailable_reasons=tuple(reason.value for reason in result.unavailable_reasons),
        summary=result.summary,
    )


def predict(
    catalogue: Catalogue,
    inputs: PredictionInputs,
    created_at: datetime,
    event_records: Sequence[EventRecord] = (),
    coverage_checks: Sequence[CoverageCheck] = (),
    event_rules: EventHeatRules = EVENT_HEAT_RULES_V1,
) -> tuple[RecommendationSnapshot, ...]:
    """One snapshot per catalogue game, from what could be seen at `inputs.as_of`.

    This is the whole prediction side. It takes inputs holding only what each source had
    published by the simulated moment, and event records as known then. It takes no
    outcome, no label and no future value, and there is no parameter through which one
    could be passed.
    """
    ensure_available(inputs)
    source = V0_INPUT_SIGNAL.source
    if source not in inputs.publication_lag:
        raise ValueError(f"no publication lag is known for the score's input source {source}")
    lag = inputs.publication_lag[source]
    due_day = latest_due_at(inputs.as_of, Granularity.DAILY, lag).date()
    for record in event_records:
        if record.last_observed_at > inputs.as_of:
            raise ValueError("an event record carries an observation from after the snapshot")
    assessments = assess(catalogue, inputs.report)
    ranks = rank_scored(catalogue, assessments)
    by_game = {item.canonical_game_id: item for item in assessments}
    checks = [check for check in coverage_checks if check.checked_at <= inputs.as_of]
    window = inputs.report.window
    snapshots = []
    for game in catalogue.games:
        assessment = by_game[game.canonical_game_id]
        events = assess_events(
            game.canonical_game_id, event_records, checks, inputs.as_of, event_rules
        )
        fields: dict[str, object] = {
            "format": SNAPSHOT_FORMAT,
            "as_of": inputs.as_of,
            "evaluation_day": inputs.evaluation_day,
            "latest_due_day": due_day,
            "latest_observed_day": (
                None if assessment.scored_features is None else assessment.scored_features.as_of_day
            ),
            "publication_lag_hours": lag.total_seconds() / 3600,
            "input_window_start": window.start.date(),
            "input_window_end": window.end.date(),
            "canonical_game_id": game.canonical_game_id,
            "slug": game.slug,
            "name": game.name,
            "lifecycle_state": game.lifecycle_state.value,
            "score_version": SCORE_VERSION,
            "score": assessment.score,
            "rank": ranks.get(game.canonical_game_id),
            "universe_size": len(catalogue.games),
            "ranked_count": len(ranks),
            "confidence": assessment.confidence,
            "scored_signal": assessment.scored_signal,
            "features": assessment.scored_features,
            "explanation": assessment.explanation,
            "source_coverage": _coverage(assessment),
            "events": _events(events),
            "created_at": created_at,
        }
        draft = RecommendationSnapshot.model_validate({**fields, "snapshot_id": "snap_" + "0" * 32})
        identity = snapshot_id(draft.model_dump(mode="json"))
        if draft.latest_observed_day is not None and draft.latest_observed_day > due_day:
            # Cannot happen while `ensure_available` holds; checked again where it would show.
            raise LeakageError(f"{game.slug}: scored on a day its source had not published")
        snapshots.append(draft.model_copy(update={"snapshot_id": identity}))
    return tuple(snapshots)
