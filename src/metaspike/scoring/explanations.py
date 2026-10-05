"""Deterministic, template-based 'why now?' explanations. No LLM, no hype.

Every sentence is rendered from computed features; a sentence appears only when the
feature that backs it crosses its stated threshold.
"""

from metaspike.domain.scores import (
    ConfidenceResult,
    Explanation,
    Reason,
    ScoreResult,
    UnscoredReason,
)
from metaspike.features.attention import BASELINE_DAYS, AttentionFeatures
from metaspike.scoring.v0 import ROBUST_Z_OUTLIER_THRESHOLD

ELEVATED_LIFT_RATIO = 1.25
DEPRESSED_LIFT_RATIO = 0.8
NOTABLE_VELOCITY_PCT = 20.0
NOTABLE_RISING_DAYS = 3
NOTABLE_PERCENTILE = 95.0
MAX_SIGNAL_REASONS_IN_TOP = 2

_UNSCORED_TEXT: dict[UnscoredReason, str] = {
    UnscoredReason.NO_OBSERVATIONS: "Not scored: no observations are available for this game.",
    UnscoredReason.INSUFFICIENT_HISTORY: (
        "Not scored: there is not enough history to establish a 28-day baseline."
    ),
    UnscoredReason.INSUFFICIENT_RECENT_DATA: (
        "Not scored: too few recent days have data to measure current attention."
    ),
    UnscoredReason.ZERO_BASELINE: (
        "Not scored: the 28-day baseline is zero, so relative lift is undefined."
    ),
    UnscoredReason.ZERO_DISPERSION: (
        "Not scored: the baseline has no variation, so an anomaly cannot be measured."
    ),
}


def explain_unavailable(text: str) -> Explanation:
    """Explanation for a game that has no usable series at all."""
    reason = Reason(code="no_data", text=text)
    return Explanation(summary=text, reasons=(reason,), top_reasons=(text,))


def explain(
    features: AttentionFeatures, score: ScoreResult, confidence: ConfidenceResult | None
) -> Explanation:
    """Explain a result. An unscored game has no confidence, so pass None for it."""
    caveats = _caveats(features, confidence)
    if score.unscored_reason is not None:
        signals = [Reason(code="not_scored", text=_UNSCORED_TEXT[score.unscored_reason])]
    else:
        signals = _signals(features)
    top = [*signals[:MAX_SIGNAL_REASONS_IN_TOP], *caveats[:1]]
    return Explanation(
        summary=signals[0].text,
        reasons=(*signals, *caveats),
        top_reasons=tuple(reason.text for reason in top),
    )


def _signals(features: AttentionFeatures) -> list[Reason]:
    assert features.lift_ratio is not None and features.lift_pct is not None
    assert features.avg_3d is not None and features.baseline_median_28d is not None
    assert features.robust_z_score is not None
    levels = (
        f"a 3-day average of {_views(features.avg_3d)} daily views against a typical "
        f"{_views(features.baseline_median_28d)}"
    )
    if features.lift_ratio >= ELEVATED_LIFT_RATIO:
        headline = Reason(
            code="lift_elevated",
            text=f"Attention is {features.lift_ratio:.1f}× its {BASELINE_DAYS}-day baseline: "
            f"{levels}.",
        )
    elif features.lift_ratio <= DEPRESSED_LIFT_RATIO:
        headline = Reason(
            code="lift_depressed",
            text=f"Attention is {abs(features.lift_pct):.0f}% below its {BASELINE_DAYS}-day "
            f"baseline: {levels}.",
        )
    else:
        headline = Reason(
            code="lift_normal",
            text=f"Attention is in line with its {BASELINE_DAYS}-day baseline: {levels}.",
        )

    signals = [headline]
    if features.robust_z_score >= ROBUST_Z_OUTLIER_THRESHOLD:
        signals.append(
            Reason(
                code="statistical_outlier",
                text=f"The latest day ({features.as_of_day}) is a statistical outlier against "
                f"this game's own history (robust z-score {features.robust_z_score:.1f}).",
            )
        )
    if features.consecutive_rising_days >= NOTABLE_RISING_DAYS:
        signals.append(
            Reason(
                code="rising_streak",
                text=f"Daily views have risen for {features.consecutive_rising_days} "
                "consecutive days.",
            )
        )
    velocity = features.velocity_pct
    if velocity is not None and velocity >= NOTABLE_VELOCITY_PCT:
        signals.append(
            Reason(
                code="momentum_building",
                text=f"Momentum is building: the 3-day average is up {velocity:.0f}% on the "
                "preceding 3 days.",
            )
        )
    elif velocity is not None and velocity <= -NOTABLE_VELOCITY_PCT:
        signals.append(
            Reason(
                code="momentum_fading",
                text=f"Momentum is fading: the 3-day average is down {abs(velocity):.0f}% on "
                "the preceding 3 days.",
            )
        )
    percentile = features.history_percentile
    if percentile is not None and percentile >= NOTABLE_PERCENTILE:
        signals.append(
            Reason(
                code="high_percentile",
                text=f"The latest day is higher than {percentile:.0f}% of the previous "
                f"{features.percentile_samples} observed days.",
            )
        )
    return signals


def _caveats(features: AttentionFeatures, confidence: ConfidenceResult | None) -> list[Reason]:
    caveats: list[Reason] = []
    if confidence is None:
        pass
    elif confidence.independent_sources <= 1:
        caveats.append(
            Reason(
                code="single_source",
                text=f"Confidence is limited ({confidence.value:.0f}/100) because only one "
                "source, Wikimedia pageviews, is currently available.",
            )
        )
    else:
        caveats.append(
            Reason(
                code="confidence",
                text=f"Confidence is {confidence.value:.0f}/100 across "
                f"{confidence.independent_sources} independent sources.",
            )
        )
    if features.staleness_days:
        caveats.append(
            Reason(
                code="stale_data",
                text=f"The latest available data is {_days(features.staleness_days)} behind "
                "the expected date.",
            )
        )
    if features.missing_days:
        caveats.append(
            Reason(
                code="missing_days",
                text=f"{features.missing_days} of {features.history_span_days} days in the "
                "history have no data.",
            )
        )
    return caveats


def _views(value: float) -> str:
    return f"{round(value):,}"


def _days(count: int) -> str:
    return "1 day" if count == 1 else f"{count} days"
