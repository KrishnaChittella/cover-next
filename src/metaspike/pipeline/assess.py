"""Assessment: features, score, confidence and explanation for every catalogue game.

An entity may have any number of signals, and any of them may be missing. Features
are computed per signal; each score version then names the signals it consumes.
"""

from metaspike.domain.games import Catalogue, GameId
from metaspike.domain.model import FrozenModel
from metaspike.domain.observations import TimeSeries
from metaspike.domain.scores import (
    ConfidenceResult,
    Explanation,
    ScoreResult,
    ScoreStatus,
    UnscoredReason,
)
from metaspike.domain.signals import CollectionStatus, EntitySignals, SignalKey
from metaspike.domain.timeseries import Granularity
from metaspike.features.attention import AttentionFeatures, compute_attention_features
from metaspike.pipeline.collection import CollectionReport
from metaspike.scoring.explanations import explain, explain_unavailable
from metaspike.scoring.v0 import SCORE_VERSION, V0_INPUT_SIGNAL, confidence_v0, score_v0

_NOT_COVERED = "Not scored: no source currently covers this game."
_COLLECTION_FAILED = "Not scored: data collection failed for this game in the latest run."


class SignalFeatures(FrozenModel):
    key: SignalKey
    features: AttentionFeatures


class GameAssessment(FrozenModel):
    canonical_game_id: GameId
    signals: EntitySignals
    signal_features: tuple[SignalFeatures, ...] = ()
    # The signal the score was computed from; None when the game is unscored for lack of it.
    scored_signal: SignalKey | None = None
    score: ScoreResult
    confidence: ConfidenceResult | None = None
    explanation: Explanation

    def features_for(self, key: SignalKey) -> AttentionFeatures | None:
        for entry in self.signal_features:
            if entry.key == key:
                return entry.features
        return None

    @property
    def scored_features(self) -> AttentionFeatures | None:
        return None if self.scored_signal is None else self.features_for(self.scored_signal)

    @property
    def scored_series(self) -> TimeSeries | None:
        if self.scored_signal is None:
            return None
        signal = self.signals.get(self.scored_signal)
        return None if signal is None else signal.series


def assess(catalogue: Catalogue, report: CollectionReport) -> tuple[GameAssessment, ...]:
    return tuple(
        assess_entity(report.signals_for(game.canonical_game_id)) for game in catalogue.games
    )


def assess_entity(signals: EntitySignals) -> GameAssessment:
    signal_features = tuple(
        SignalFeatures(key=signal.key, features=compute_attention_features(signal.series))
        for signal in signals.available()
        if signal.series is not None and signal.key.granularity is Granularity.DAILY
    )
    scored = signals.get(V0_INPUT_SIGNAL)
    if scored is None or not scored.available:
        failed = scored is not None and scored.status is CollectionStatus.FAILED
        return GameAssessment(
            canonical_game_id=signals.canonical_game_id,
            signals=signals,
            signal_features=signal_features,
            score=ScoreResult(
                score_version=SCORE_VERSION,
                status=ScoreStatus.UNSCORED,
                unscored_reason=UnscoredReason.NO_OBSERVATIONS,
            ),
            explanation=explain_unavailable(_COLLECTION_FAILED if failed else _NOT_COVERED),
        )

    features = next(entry.features for entry in signal_features if entry.key == V0_INPUT_SIGNAL)
    score = score_v0(features)
    confidence = None
    if score.status is ScoreStatus.SCORED:
        # v0 consumes exactly one signal, so exactly one source can vouch for it,
        # however many other signals the entity happens to have.
        confidence = confidence_v0(features, independent_sources=1)
    return GameAssessment(
        canonical_game_id=signals.canonical_game_id,
        signals=signals,
        signal_features=signal_features,
        scored_signal=V0_INPUT_SIGNAL,
        score=score,
        confidence=confidence,
        explanation=explain(features, score, confidence),
    )
