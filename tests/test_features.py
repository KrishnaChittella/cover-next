import math
import random
from collections.abc import Sequence
from datetime import timedelta

import pytest

from metaspike.domain.observations import Metric, Observation, ObservationStatus, QualityFlag
from metaspike.features.attention import (
    MAD_TO_SIGMA,
    AttentionFeatures,
    FeatureFlag,
    compute_attention_features,
    rolling_mean,
)
from metaspike.pipeline.normalize import build_daily_series
from metaspike.sources.base import FetchWindow
from metaspike.sources.wikimedia import SOURCE_ID
from tests.conftest import GAME_ID, RECORDED_END, RUN_TIME, observation, series_of, spiking, steady


def features_of(values: Sequence[float | None]) -> AttentionFeatures:
    return compute_attention_features(series_of(values))


def test_recent_averages_and_baseline_use_the_documented_windows() -> None:
    features = features_of(spiking())

    assert features.as_of_day == RECORDED_END
    assert features.latest_views == 400
    assert features.avg_3d == pytest.approx((200 + 300 + 400) / 3)
    assert features.avg_7d == pytest.approx((110 + 100 + 100 + 100 + 200 + 300 + 400) / 7)
    assert features.prior_avg_3d == 100
    # Baseline is [t-34, t-7]: 28 days alternating 90/110, untouched by the spike.
    assert features.baseline_samples == 28
    assert features.baseline_mean_28d == pytest.approx(100)
    assert features.baseline_median_28d == 100
    assert features.baseline_mad_28d == 10
    assert features.baseline_std_28d == pytest.approx(math.sqrt(28 * 100 / 27))
    assert features.flags == ()


def test_lift_and_velocity() -> None:
    features = features_of(spiking())

    assert features.lift_ratio == pytest.approx(3.0)
    assert features.lift_pct == pytest.approx(200.0)
    assert features.lift_7d_pct == pytest.approx((1310 / 7 / 100 - 1) * 100)
    assert features.velocity_ratio == pytest.approx(3.0)
    assert features.velocity_pct == pytest.approx(200.0)
    assert features.consecutive_rising_days == 3


def test_z_scores_and_percentile() -> None:
    features = features_of(spiking())

    assert features.z_score == pytest.approx(300 / math.sqrt(28 * 100 / 27))
    assert features.robust_z_score == pytest.approx(300 / (MAD_TO_SIGMA * 10))
    assert features.robust_scale_method == "mad"
    assert features.history_percentile == 100
    assert features.percentile_samples == 89


def test_percentile_uses_mid_rank_for_ties() -> None:
    values = steady()  # the latest day is 110; of 89 prior days, 45 are 90 and 44 are 110
    features = features_of(values)
    assert features.history_percentile == pytest.approx(100 * (45 + 0.5 * 44) / 89)


def test_a_game_is_compared_with_its_own_normal_not_with_raw_popularity() -> None:
    huge_but_normal: list[float | None] = [
        490_000.0 if i % 2 == 0 else 510_000.0 for i in range(90)
    ]
    small_but_spiking: list[float | None] = [4_500.0 if i % 2 == 0 else 5_500.0 for i in range(90)]
    small_but_spiking[-3:] = [40_000.0, 40_000.0, 40_000.0]

    big, small = features_of(huge_but_normal), features_of(small_but_spiking)

    assert big.latest_views is not None and small.latest_views is not None
    assert big.latest_views > 10 * small.latest_views
    assert small.lift_ratio == pytest.approx(8.0)
    assert big.lift_ratio == pytest.approx(1.0, abs=0.05)
    assert small.robust_z_score is not None and big.robust_z_score is not None
    assert small.robust_z_score > 10 * big.robust_z_score


def test_an_old_spike_in_the_baseline_does_not_distort_robust_statistics() -> None:
    clean = steady()
    clean[-1] = 300.0
    polluted = list(clean)
    polluted[60] = 100_000.0  # an extreme outlier inside the baseline window

    clean_features, polluted_features = features_of(clean), features_of(polluted)

    # One extreme day can move the median by at most one rank, so the robust view of
    # "normal" and of today's anomaly barely changes.
    assert clean_features.baseline_median_28d == 100
    assert polluted_features.baseline_median_28d == 110
    assert polluted_features.baseline_mad_28d == clean_features.baseline_mad_28d == 10
    assert polluted_features.robust_z_score == pytest.approx(clean_features.robust_z_score, rel=0.1)
    assert polluted_features.lift_ratio == pytest.approx(clean_features.lift_ratio, rel=0.1)
    # The classical z-score is what breaks: the outlier inflates mean and std and masks today.
    assert clean_features.z_score is not None and clean_features.z_score > 15
    assert polluted_features.z_score is not None and polluted_features.z_score < 1


def test_missing_days_are_excluded_never_counted_as_zero() -> None:
    values = spiking()
    for index in (58, 60, 62, 64):  # four 90-view days inside the baseline window
        values[index] = None

    features = features_of(values)

    assert features.baseline_samples == 24
    assert features.baseline_mean_28d == pytest.approx((10 * 90 + 14 * 110) / 24)
    assert features.missing_days == 4 and features.observed_days == 86
    assert FeatureFlag.HAS_MISSING_DAYS in features.flags
    assert features.lift_ratio is not None


def test_missing_days_end_a_rising_run_and_thin_out_recent_windows() -> None:
    values = spiking()
    values[-2] = None
    features = features_of(values)
    assert features.consecutive_rising_days == 0
    assert features.avg_3d == pytest.approx(300)  # 2 of 3 observed is enough
    assert features.recent_7d_samples == 6

    values[-3] = None
    assert features_of(values).avg_3d is None
    assert FeatureFlag.INSUFFICIENT_RECENT_DATA in features_of(values).flags


def test_trailing_missing_days_make_the_data_stale() -> None:
    values = [*spiking(), None, None]
    features = features_of(values)
    assert features.staleness_days == 2
    assert features.as_of_day == RECORDED_END - timedelta(days=2)
    assert features.latest_views == 400
    assert FeatureFlag.STALE in features.flags
    assert features.missing_days == 0  # trailing days are late, not gaps


def test_insufficient_history_yields_no_baseline() -> None:
    features = features_of(steady(20))
    assert FeatureFlag.INSUFFICIENT_HISTORY in features.flags
    assert features.latest_views == 110 and features.avg_7d is not None
    assert features.baseline_median_28d is None and features.baseline_samples == 13
    assert features.lift_ratio is None and features.z_score is None
    assert features.robust_z_score is None and features.history_percentile is None


def test_zero_baseline_does_not_divide_by_zero() -> None:
    values = [0.0] * 87 + [5.0, 10.0, 20.0]
    features = features_of(values)
    assert features.baseline_median_28d == 0
    assert features.lift_ratio is None and features.lift_pct is None
    assert features.z_score is None and features.robust_z_score is None
    assert {FeatureFlag.ZERO_BASELINE, FeatureFlag.ZERO_DISPERSION} <= set(features.flags)


def test_zero_prior_average_leaves_velocity_undefined() -> None:
    values = [0.0] * 87 + [5.0, 10.0, 20.0]
    features = features_of(values)
    assert features.prior_avg_3d == 0
    assert features.velocity_ratio is None and features.velocity_pct is None
    assert FeatureFlag.VELOCITY_UNDEFINED in features.flags


def test_constant_baseline_has_no_dispersion_to_measure_against() -> None:
    features = features_of([100.0] * 89 + [500.0])
    assert features.lift_ratio == pytest.approx((100 + 100 + 500) / 3 / 100)
    assert features.z_score is None and features.robust_z_score is None
    assert features.robust_scale_method is None
    assert FeatureFlag.ZERO_DISPERSION in features.flags


def test_robust_z_falls_back_to_mean_absolute_deviation_when_mad_is_zero() -> None:
    values: list[float | None] = [100.0] * 90
    for index in (56, 60, 64, 68):  # too few to move the median or the MAD
        values[index] = 140.0
    values[-1] = 300.0
    features = features_of(values)
    assert features.baseline_mad_28d == 0
    assert features.robust_scale_method == "mean_ad"
    assert features.robust_z_score == pytest.approx(200 / (1.2533 * (4 * 40 / 28)))


def test_no_observations_at_all() -> None:
    features = features_of([None] * 90)
    assert features.flags == (FeatureFlag.NO_OBSERVATIONS,)
    assert features.as_of_day is None and features.latest_views is None
    assert features.observed_days == 0 and features.staleness_days is None


def test_extreme_and_random_inputs_never_produce_non_finite_features() -> None:
    generator = random.Random(7)
    cases: list[Sequence[float | None]] = [
        [1e15] * 89 + [1e18],
        [0.0] * 90,
        [0.0] * 89 + [1e12],
        [1.0],
        steady(36),
    ]
    for _ in range(200):
        length = generator.randint(1, 90)
        cases.append(
            [
                None if generator.random() < 0.15 else float(generator.choice([0, 1, 50, 10**6]))
                for _ in range(length)
            ]
        )
    for values in cases:
        for name, value in features_of(values):
            if isinstance(value, float):
                assert math.isfinite(value), f"{name} for {values}"


def test_features_are_deterministic_and_independent_of_input_order() -> None:
    assert features_of(spiking()) == features_of(spiking())

    window = FetchWindow(start=RECORDED_END - timedelta(days=89), end=RECORDED_END)
    observations = [
        observation(window.start + timedelta(days=offset), value)
        for offset, value in enumerate(spiking())
        if value is not None
    ]
    shuffled = list(observations)
    random.Random(1).shuffle(shuffled)

    def build(points: list[Observation]) -> AttentionFeatures:
        series = build_daily_series(
            GAME_ID, SOURCE_ID, Metric.DAILY_PAGEVIEWS, window, points, RUN_TIME
        )
        return compute_attention_features(series)

    assert build(observations) == build(shuffled)


def test_normalisation_labels_each_kind_of_gap() -> None:
    series = series_of([None, 5.0, None, 7.0, None])
    assert [point.status for point in series.points] == [
        ObservationStatus.MISSING,
        ObservationStatus.OBSERVED,
        ObservationStatus.MISSING,
        ObservationStatus.OBSERVED,
        ObservationStatus.MISSING,
    ]
    assert [point.quality.flags for point in series.points] == [
        (QualityFlag.BEFORE_FIRST_OBSERVATION,),
        (),
        (QualityFlag.SOURCE_GAP,),
        (),
        (QualityFlag.PENDING_PUBLICATION,),
    ]
    assert series.values() == [None, 5.0, None, 7.0, None]


def test_normalisation_rejects_duplicates_and_out_of_window_points() -> None:
    window = FetchWindow(start=RECORDED_END, end=RECORDED_END)
    same_day = [observation(RECORDED_END, 1), observation(RECORDED_END, 2)]
    with pytest.raises(ValueError, match="duplicate"):
        build_daily_series(GAME_ID, SOURCE_ID, Metric.DAILY_PAGEVIEWS, window, same_day, RUN_TIME)
    outside = [observation(RECORDED_END - timedelta(days=1), 1)]
    with pytest.raises(ValueError, match="outside"):
        build_daily_series(GAME_ID, SOURCE_ID, Metric.DAILY_PAGEVIEWS, window, outside, RUN_TIME)


def test_rolling_mean_needs_enough_observed_days() -> None:
    values: list[float | None] = [10.0, None, 30.0, 50.0]
    assert rolling_mean(values, window=3, min_samples=2) == [None, None, 20.0, 40.0]
