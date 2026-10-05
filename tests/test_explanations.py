from collections.abc import Sequence

from metaspike.domain.scores import Explanation
from metaspike.features.attention import compute_attention_features
from metaspike.scoring.explanations import explain
from metaspike.scoring.v0 import confidence_v0, score_v0
from tests.conftest import series_of, spiking, steady


def explanation_of(values: Sequence[float | None]) -> Explanation:
    features = compute_attention_features(series_of(values))
    score = score_v0(features)
    confidence = confidence_v0(features, 1) if score.value is not None else None
    return explain(features, score, confidence)


def codes(explanation: Explanation) -> list[str]:
    return [reason.code for reason in explanation.reasons]


def test_spike_explanation_states_the_computed_numbers() -> None:
    explanation = explanation_of(spiking())

    assert explanation.summary == (
        "Attention is 3.0× its 28-day baseline: a 3-day average of 300 daily views "
        "against a typical 100."
    )
    assert codes(explanation) == [
        "lift_elevated",
        "statistical_outlier",
        "rising_streak",
        "momentum_building",
        "high_percentile",
        "single_source",
    ]
    texts = [reason.text for reason in explanation.reasons]
    assert "The latest day (2026-10-03) is a statistical outlier" in texts[1]
    assert "robust z-score 20.2" in texts[1]
    assert texts[2] == "Daily views have risen for 3 consecutive days."
    assert texts[3] == "Momentum is building: the 3-day average is up 200% on the preceding 3 days."
    assert texts[4] == "The latest day is higher than 100% of the previous 89 observed days."


def test_top_reasons_always_carry_the_single_source_caveat() -> None:
    for values in (spiking(), steady()):
        explanation = explanation_of(values)
        assert 2 <= len(explanation.top_reasons) <= 3
        assert explanation.top_reasons[-1] == (
            "Confidence is limited (60/100) because only one source, Wikimedia pageviews, "
            "is currently available."
        )


def test_normal_attention_is_described_as_normal_without_hype() -> None:
    explanation = explanation_of(steady())
    assert codes(explanation) == ["lift_normal", "single_source"]
    assert explanation.summary.startswith("Attention is in line with its 28-day baseline")


def test_declining_attention_is_reported_as_a_decline() -> None:
    values = steady()
    values[-3:] = [50.0, 40.0, 30.0]
    explanation = explanation_of(values)
    assert codes(explanation)[:2] == ["lift_depressed", "momentum_fading"]
    assert explanation.summary.startswith("Attention is 60% below its 28-day baseline")


def test_data_problems_are_stated() -> None:
    values = spiking()
    values[50] = None
    explanation = explanation_of([*values[2:], None, None])
    texts = " ".join(reason.text for reason in explanation.reasons)
    assert "The latest available data is 2 days behind the expected date." in texts
    assert "1 of 88 days in the history have no data." in texts


def test_unscored_games_say_why_and_claim_no_confidence() -> None:
    explanation = explanation_of(steady(20))
    assert codes(explanation) == ["not_scored"]
    assert explanation.summary == (
        "Not scored: there is not enough history to establish a 28-day baseline."
    )


def test_explanations_are_deterministic() -> None:
    assert explanation_of(spiking()) == explanation_of(spiking())
