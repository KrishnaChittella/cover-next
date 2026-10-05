"""Assessment: features, score, confidence and explanation for every catalogue game."""

from metaspike.domain.games import Catalogue, GameId
from metaspike.domain.model import FrozenModel
from metaspike.domain.observations import DailySeries, Metric
from metaspike.domain.scores import (
    ConfidenceResult,
    Explanation,
    ScoreResult,
    ScoreStatus,
    UnscoredReason,
)
from metaspike.features.attention import AttentionFeatures, compute_attention_features
from metaspike.pipeline.collection import CollectionReport, CollectionStatus
from metaspike.scoring.explanations import explain, explain_unavailable
from metaspike.scoring.v0 import SCORE_VERSION, confidence_v0, score_v0

_NOT_COVERED = "Not scored: no source currently covers this game."
_COLLECTION_FAILED = "Not scored: data collection failed for this game in the latest run."


class GameAssessment(FrozenModel):
    canonical_game_id: GameId
    series: DailySeries | None = None
    features: AttentionFeatures | None = None
    score: ScoreResult
    confidence: ConfidenceResult | None = None
    explanation: Explanation


def assess(catalogue: Catalogue, report: CollectionReport) -> tuple[GameAssessment, ...]:
    return tuple(_assess_game(game.canonical_game_id, report) for game in catalogue.games)


def _assess_game(canonical_game_id: str, report: CollectionReport) -> GameAssessment:
    outcomes = [
        outcome
        for run in report.runs
        for outcome in run.outcomes
        if outcome.canonical_game_id == canonical_game_id
    ]
    attention = [
        outcome.series
        for outcome in outcomes
        if outcome.series is not None and outcome.series.metric is Metric.DAILY_PAGEVIEWS
    ]
    if not attention:
        failed = any(outcome.status is CollectionStatus.FAILED for outcome in outcomes)
        return GameAssessment(
            canonical_game_id=canonical_game_id,
            score=ScoreResult(
                score_version=SCORE_VERSION,
                status=ScoreStatus.UNSCORED,
                unscored_reason=UnscoredReason.NO_OBSERVATIONS,
            ),
            explanation=explain_unavailable(_COLLECTION_FAILED if failed else _NOT_COVERED),
        )

    # v0 has exactly one attention source, so there is exactly one series to score.
    series = attention[0]
    features = compute_attention_features(series)
    score = score_v0(features)
    confidence = None
    if score.status is ScoreStatus.SCORED:
        confidence = confidence_v0(features, independent_sources=len(attention))
    return GameAssessment(
        canonical_game_id=canonical_game_id,
        series=series,
        features=features,
        score=score,
        confidence=confidence,
        explanation=explain(features, score, confidence),
    )
