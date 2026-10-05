import math
import random
from collections.abc import Sequence

import pytest

from metaspike.domain.scores import ConfidenceBand, ScoreStatus, UnscoredReason
from metaspike.features.attention import compute_attention_features
from metaspike.scoring.explanations import explain
from metaspike.scoring.v0 import SCORE_VERSION, confidence_v0, score_v0, source_ceiling
from tests.conftest import series_of, spiking, steady


def scored(values: Sequence[float | None]) -> float:
    value = score_v0(compute_attention_features(series_of(values))).value
    assert value is not None
    return value


def varied_series() -> list[Sequence[float | None]]:
    generator = random.Random(11)
    cases: list[Sequence[float | None]] = [
        spiking(),
        steady(),
        steady(20),
        [None] * 90,
        [0.0] * 90,
        [1.0] * 89 + [1e15],
        [1e15] * 89 + [0.0],
        [float(i) for i in range(90)],
        [float(90 - i) for i in range(90)],
        [*spiking(), *[None] * 5],
    ]
    for _ in range(300):
        scale = generator.choice([1, 100, 10_000, 1_000_000])
        cases.append(
            [
                None if generator.random() < 0.1 else float(generator.randint(0, 50) * scale)
                for _ in range(generator.randint(1, 90))
            ]
        )
    return cases


def test_score_matches_the_documented_formula() -> None:
    result = score_v0(compute_attention_features(series_of(spiking())))

    assert result.score_version == SCORE_VERSION == "metaspike_score_v0"
    assert result.status is ScoreStatus.SCORED and result.components is not None
    # robust z = 300 / 14.826 = 20.2 (saturated); lift = 3x; velocity = 3x (saturated).
    magnitude = math.log2(3) / 3
    assert result.components.significance == 1
    assert result.components.magnitude == pytest.approx(magnitude, abs=1e-4)
    assert result.components.anomaly == pytest.approx(math.sqrt(magnitude), abs=1e-4)
    assert result.components.momentum == 1
    assert result.value == pytest.approx(100 * (0.7 * math.sqrt(magnitude) + 0.3), abs=0.05)


def test_score_and_confidence_stay_within_bounds_for_any_input() -> None:
    for values in varied_series():
        features = compute_attention_features(series_of(values))
        score = score_v0(features)
        confidence = confidence_v0(features, independent_sources=1)
        if score.value is not None:
            assert 0 <= score.value <= 100 and math.isfinite(score.value)
        else:
            assert score.status is ScoreStatus.UNSCORED and score.unscored_reason is not None
        assert 0 <= confidence.value <= 60
        explain(features, score, confidence if score.value is not None else None)


def test_bigger_and_faster_spikes_score_higher() -> None:
    mild, strong = steady(), steady()
    mild[-3:] = [130.0, 140.0, 150.0]
    strong[-3:] = [300.0, 500.0, 800.0]
    assert 0 <= scored(steady()) < scored(mild) < scored(strong) <= 100


def test_declining_attention_scores_zero_not_negative() -> None:
    values = steady()
    values[-6:] = [100.0, 100.0, 100.0, 40.0, 30.0, 20.0]
    assert scored(values) == 0


def test_attention_falling_to_zero_scores_zero() -> None:
    values = steady()
    values[-3:] = [0.0, 0.0, 0.0]
    assert scored(values) == 0


def test_a_level_shift_that_is_no_longer_accelerating_loses_its_momentum_term() -> None:
    climbing, plateaued = steady(), steady()
    climbing[-3:] = [400.0, 400.0, 400.0]
    plateaued[-6:] = [400.0] * 6
    assert scored(plateaued) < scored(climbing)
    result = score_v0(compute_attention_features(series_of(plateaued)))
    assert result.components is not None and result.components.momentum == 0


@pytest.mark.parametrize(
    ("values", "reason"),
    [
        ([None] * 90, UnscoredReason.NO_OBSERVATIONS),
        (steady(20), UnscoredReason.INSUFFICIENT_HISTORY),
        ([*steady()[:-3], None, None, 110.0], UnscoredReason.INSUFFICIENT_RECENT_DATA),
        ([0.0] * 87 + [5.0, 10.0, 20.0], UnscoredReason.ZERO_BASELINE),
        ([100.0] * 89 + [500.0], UnscoredReason.ZERO_DISPERSION),
    ],
)
def test_games_without_enough_evidence_are_unscored_not_zero(
    values: Sequence[float | None], reason: UnscoredReason
) -> None:
    result = score_v0(compute_attention_features(series_of(values)))
    assert result.status is ScoreStatus.UNSCORED
    assert result.value is None and result.components is None
    assert result.unscored_reason is reason


def test_scoring_is_deterministic() -> None:
    first = compute_attention_features(series_of(spiking()))
    second = compute_attention_features(series_of(spiking()))
    assert score_v0(first) == score_v0(second)
    assert confidence_v0(first, 1) == confidence_v0(second, 1)
    assert score_v0(first).model_dump_json() == score_v0(second).model_dump_json()


def test_confidence_with_one_source_is_capped_at_sixty() -> None:
    perfect = confidence_v0(compute_attention_features(series_of(spiking())), 1)
    assert perfect.value == 60.0 and perfect.ceiling == 60.0
    assert perfect.band is ConfidenceBand.MEDIUM  # 'high' is unreachable with one source
    assert perfect.factors.model_dump() == {
        "source_ceiling": 0.6,
        "freshness": 1.0,
        "history": 1.0,
        "completeness": 1.0,
        "samples": 1.0,
    }


def test_source_ceiling_rises_with_corroboration_but_never_reaches_certainty() -> None:
    assert source_ceiling(0) == 0
    assert source_ceiling(1) == pytest.approx(0.6)
    assert source_ceiling(2) == pytest.approx(0.84)
    assert source_ceiling(1) < source_ceiling(2) < source_ceiling(3) < 1


def test_stale_data_lowers_confidence() -> None:
    fresh = confidence_v0(compute_attention_features(series_of(spiking())), 1)
    stale = confidence_v0(compute_attention_features(series_of(spiking()[2:] + [None] * 2)), 1)
    dead = confidence_v0(compute_attention_features(series_of(spiking()[4:] + [None] * 4)), 1)
    assert stale.factors.freshness == 0.5
    assert dead.value == 0 < stale.value < fresh.value
    assert stale.band is ConfidenceBand.LOW


def test_missing_days_and_short_history_lower_confidence() -> None:
    gappy = spiking()
    for index in range(40, 80, 4):
        gappy[index] = None
    full = confidence_v0(compute_attention_features(series_of(spiking())), 1)
    with_gaps = confidence_v0(compute_attention_features(series_of(gappy)), 1)
    short = confidence_v0(compute_attention_features(series_of(spiking()[-45:])), 1)

    assert with_gaps.factors.completeness == pytest.approx(80 / 90, abs=1e-4)
    assert with_gaps.factors.samples < 1
    assert with_gaps.value < full.value
    assert short.factors.history == 0.5 and short.value == 30.0


def test_confidence_is_zero_without_observations() -> None:
    confidence = confidence_v0(compute_attention_features(series_of([None] * 90)), 1)
    assert confidence.value == 0 and confidence.band is ConfidenceBand.LOW
