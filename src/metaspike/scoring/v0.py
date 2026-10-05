"""metaspike_score_v0 and confidence v0.

v0 measures one thing: how unusual a game's current attention is relative to its
own recent normal, plus whether that attention is still climbing. It is built on a
single source and is NOT a complete measure of creator demand or supply.

    significance = clamp(robust_z_score / 7, 0, 1)
    magnitude    = clamp(log2(lift_ratio) / 3, 0, 1)
    anomaly      = sqrt(significance * magnitude)
    momentum     = clamp(log2(velocity_ratio), 0, 1)
    score        = 100 * (0.7 * anomaly + 0.3 * momentum)

    confidence   = 100 * ceiling(n_sources) * freshness * history * completeness * samples
"""

import math

from metaspike.domain.model import FrozenModel
from metaspike.domain.scores import (
    ConfidenceBand,
    ConfidenceFactors,
    ConfidenceResult,
    ScoreComponents,
    ScoreResult,
    ScoreStatus,
    UnscoredReason,
)
from metaspike.features.attention import (
    BASELINE_DAYS,
    HISTORY_DAYS,
    LONG_WINDOW_DAYS,
    AttentionFeatures,
    FeatureFlag,
)

SCORE_VERSION = "metaspike_score_v0"

# 3.5 is the conventional outlier threshold for a modified (median/MAD) z-score
# (Iglewicz & Hoaglin, 1993). Saturating at twice that gives half credit exactly at
# the point where a day first counts as an outlier.
ROBUST_Z_OUTLIER_THRESHOLD = 3.5
ROBUST_Z_SATURATION = 2 * ROBUST_Z_OUTLIER_THRESHOLD
# Attention is multiplicative, so lift is measured in doublings. Three doublings
# (8x normal) earns full magnitude credit.
LIFT_SATURATION_DOUBLINGS = 3.0
# One doubling of the 3-day average within three days earns full momentum credit.
MOMENTUM_SATURATION_DOUBLINGS = 1.0
# The score is primarily the anomaly and secondarily momentum. These two weights are
# stated priors, not fitted values: no outcome data exists yet to calibrate them.
ANOMALY_WEIGHT = 0.7
MOMENTUM_WEIGHT = 0.3

# Each independent source removes 60% of the remaining doubt: 1 source -> 0.60,
# 2 -> 0.84, 3 -> 0.936. One uncorroborated source cannot tell real interest in a
# game apart from an artefact of that source, so it can never exceed 60.
DOUBT_REMAINING_PER_SOURCE = 0.4
STALENESS_ZERO_CONFIDENCE_DAYS = 4

CONFIDENCE_MEDIUM_FROM = 30.0
CONFIDENCE_HIGH_ABOVE = 60.0


class ScoreInvariantError(Exception):
    """The scoring maths produced a value that should be impossible."""


class TermDefinition(FrozenModel):
    name: str
    formula: str
    rationale: str


SCORE_TERMS: tuple[TermDefinition, ...] = (
    TermDefinition(
        name="significance",
        formula=f"clamp(robust_z_score / {ROBUST_Z_SATURATION:g}, 0, 1)",
        rationale=(
            "How unusual the latest day is against the game's own day-to-day variability. "
            f"A robust z of {ROBUST_Z_OUTLIER_THRESHOLD} is the conventional outlier threshold "
            "and earns half credit; twice that saturates."
        ),
    ),
    TermDefinition(
        name="magnitude",
        formula=f"clamp(log2(lift_ratio) / {LIFT_SATURATION_DOUBLINGS:g}, 0, 1)",
        rationale=(
            "How far the 3-day average sits above the game's own 28-day median, in "
            "doublings because attention grows multiplicatively. 8x normal saturates."
        ),
    ),
    TermDefinition(
        name="anomaly",
        formula="sqrt(significance * magnitude)",
        rationale=(
            "Geometric mean, so an anomaly must be BOTH statistically unusual and large. "
            "Significance alone over-rewards tiny moves in very stable series; magnitude "
            "alone over-rewards noise in small, volatile ones."
        ),
    ),
    TermDefinition(
        name="momentum",
        formula=f"clamp(log2(velocity_ratio) / {MOMENTUM_SATURATION_DOUBLINGS:g}, 0, 1)",
        rationale=(
            "Whether attention is still accelerating: the 3-day average against the "
            "preceding 3 days. Doubling saturates. Undefined velocity counts as 0."
        ),
    ),
    TermDefinition(
        name="score",
        formula=f"100 * ({ANOMALY_WEIGHT} * anomaly + {MOMENTUM_WEIGHT} * momentum)",
        rationale=(
            "Anomaly is the primary signal and momentum the secondary one. The weights are "
            "stated priors, not fitted values. Declines clamp to 0: v0 scores upward "
            "attention only."
        ),
    ),
)

CONFIDENCE_TERMS: tuple[TermDefinition, ...] = (
    TermDefinition(
        name="source_ceiling",
        formula=f"1 - {DOUBT_REMAINING_PER_SOURCE} ^ n_sources",
        rationale=(
            "Independent corroboration is the main driver of confidence. With one source "
            "the ceiling is 0.60, so confidence can never exceed 60."
        ),
    ),
    TermDefinition(
        name="freshness",
        formula=f"max(0, 1 - staleness_days / {STALENESS_ZERO_CONFIDENCE_DAYS})",
        rationale="Data that lags the expected latest day says less about 'right now'.",
    ),
    TermDefinition(
        name="history",
        formula=f"min(1, history_span_days / {HISTORY_DAYS})",
        rationale="A short history gives a weaker picture of what is normal for the game.",
    ),
    TermDefinition(
        name="completeness",
        formula="observed_days / history_span_days",
        rationale="Missing days inside the history weaken every statistic built on it.",
    ),
    TermDefinition(
        name="samples",
        formula=(
            f"min(baseline_samples / {BASELINE_DAYS}, recent_7d_samples / {LONG_WINDOW_DAYS})"
        ),
        rationale="Whether the windows the score depends on were actually fully populated.",
    ),
    TermDefinition(
        name="confidence",
        formula="100 * source_ceiling * freshness * history * completeness * samples",
        rationale=(
            "Multiplicative, so any single weakness lowers confidence and none can be "
            "averaged away by the others."
        ),
    ),
)


def score_v0(features: AttentionFeatures) -> ScoreResult:
    reason = _unscored_reason(features)
    if reason is not None:
        return ScoreResult(
            score_version=SCORE_VERSION, status=ScoreStatus.UNSCORED, unscored_reason=reason
        )
    assert features.robust_z_score is not None
    assert features.lift_ratio is not None

    significance = _clamp01(features.robust_z_score / ROBUST_Z_SATURATION)
    magnitude = _clamp01(_log2_or_floor(features.lift_ratio) / LIFT_SATURATION_DOUBLINGS)
    anomaly = math.sqrt(significance * magnitude)
    momentum = 0.0
    if features.velocity_ratio is not None:
        momentum = _clamp01(_log2_or_floor(features.velocity_ratio) / MOMENTUM_SATURATION_DOUBLINGS)
    value = 100 * (ANOMALY_WEIGHT * anomaly + MOMENTUM_WEIGHT * momentum)
    if not (math.isfinite(value) and 0 <= value <= 100):
        raise ScoreInvariantError(f"score {value!r} is outside 0-100")
    return ScoreResult(
        score_version=SCORE_VERSION,
        status=ScoreStatus.SCORED,
        value=round(value, 1),
        components=ScoreComponents(
            significance=round(significance, 4),
            magnitude=round(magnitude, 4),
            anomaly=round(anomaly, 4),
            momentum=round(momentum, 4),
        ),
    )


def source_ceiling(independent_sources: int) -> float:
    return 1 - DOUBT_REMAINING_PER_SOURCE ** max(independent_sources, 0)


def confidence_v0(features: AttentionFeatures, independent_sources: int) -> ConfidenceResult:
    ceiling = source_ceiling(independent_sources)
    if features.staleness_days is None or features.history_span_days == 0:
        factors = ConfidenceFactors(
            source_ceiling=ceiling, freshness=0, history=0, completeness=0, samples=0
        )
    else:
        factors = ConfidenceFactors(
            source_ceiling=round(ceiling, 4),
            freshness=round(
                max(0.0, 1 - features.staleness_days / STALENESS_ZERO_CONFIDENCE_DAYS), 4
            ),
            history=round(min(1.0, features.history_span_days / HISTORY_DAYS), 4),
            completeness=round(features.observed_days / features.history_span_days, 4),
            samples=round(
                min(
                    features.baseline_samples / BASELINE_DAYS,
                    features.recent_7d_samples / LONG_WINDOW_DAYS,
                ),
                4,
            ),
        )
    value = round(
        100
        * factors.source_ceiling
        * factors.freshness
        * factors.history
        * factors.completeness
        * factors.samples,
        1,
    )
    if not (math.isfinite(value) and 0 <= value <= 100 * ceiling + 1e-9):
        raise ScoreInvariantError(f"confidence {value!r} breaks its ceiling")
    return ConfidenceResult(
        value=value,
        band=_band(value),
        ceiling=round(100 * ceiling, 1),
        independent_sources=independent_sources,
        factors=factors,
    )


def _unscored_reason(features: AttentionFeatures) -> UnscoredReason | None:
    flags = features.flags
    if FeatureFlag.NO_OBSERVATIONS in flags:
        return UnscoredReason.NO_OBSERVATIONS
    if FeatureFlag.INSUFFICIENT_HISTORY in flags:
        return UnscoredReason.INSUFFICIENT_HISTORY
    if FeatureFlag.INSUFFICIENT_RECENT_DATA in flags:
        return UnscoredReason.INSUFFICIENT_RECENT_DATA
    if FeatureFlag.ZERO_BASELINE in flags:
        return UnscoredReason.ZERO_BASELINE
    if FeatureFlag.ZERO_DISPERSION in flags:
        return UnscoredReason.ZERO_DISPERSION
    return None


def _band(value: float) -> ConfidenceBand:
    if value > CONFIDENCE_HIGH_ABOVE:
        return ConfidenceBand.HIGH
    if value >= CONFIDENCE_MEDIUM_FROM:
        return ConfidenceBand.MEDIUM
    return ConfidenceBand.LOW


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


def _log2_or_floor(ratio: float) -> float:
    """log2 of a ratio; a ratio of 0 (attention fell to nothing) is the floor, not an error."""
    return math.log2(ratio) if ratio > 0 else -math.inf
