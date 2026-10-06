"""The generic feature engine, on deterministic SYNTHETIC histories.

Every series here is made up. The tests fix the mathematics and its guarantees:
nothing is evidence of how any real game behaves.
"""

import functools
import math
import statistics
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from metaspike.domain import baseline_rules as rules
from metaspike.domain.families import SourceFamily
from metaspike.domain.samples import SampleStatus, SlotResolution, resolve_slot
from metaspike.domain.timeseries import Granularity
from metaspike.features import aggregate
from metaspike.features import config as config_module
from metaspike.features.aggregate import (
    WORDING,
    current_level,
    daily_aggregates,
    hourly_aggregates,
    hourly_buckets,
)
from metaspike.features.config import (
    SeasonalityBasis,
    UnknownFeatureConfigError,
    feature_config,
    register_config,
)
from metaspike.features.engine import (
    FREEZE_EXPIRED,
    GAP_RESET,
    BaselineState,
    Bucket,
    BucketEvaluation,
    ChangePhase,
    Direction,
    FeatureValue,
    Maturity,
    ResidualBasis,
    StateMismatchError,
    contiguous,
    initial_state,
    run,
)
from metaspike.features.normalise import Freshness, normalise
from metaspike.features.robust import (
    median_and_mad,
    mid_rank_percentile,
    robust_scale,
    theil_sen_slope,
)
from tests.test_sampling import attempt, slot_key

STEAM = feature_config("steam_current_players", "concurrent_players", Granularity.HOURLY)
WIKI = feature_config("wikimedia_pageviews", "pageviews", Granularity.DAILY)
MONDAY = datetime(2026, 6, 1, tzinfo=UTC)
HOUR, DAY = timedelta(hours=1), timedelta(days=1)
SLOT = Granularity.FIFTEEN_MINUTES


def hours(days: float) -> int:
    return int(days * 24)


def cycle(index: int, base: float = 10_000, weekend: float = 1.5, noise: float = 0.004) -> float:
    """A game with a daily rhythm, a busier weekend and a little deterministic jitter."""
    moment = MONDAY + index * HOUR
    level = base * (1 + 0.3 * math.sin(2 * math.pi * moment.hour / 24))
    if moment.weekday() >= 5:
        level *= weekend
    return level * (1 + noise * (((index * 7919) % 13) - 6) / 6)


def series(length: int, **shape: float) -> list[float | None]:
    return [cycle(index, **shape) for index in range(length)]


def fold(
    values: Sequence[float | None], config: config_module.SignalFeatureConfig = STEAM
) -> tuple[BaselineState, tuple[BucketEvaluation, ...]]:
    return run(config, initial_state(config, "app"), contiguous(MONDAY, values, config))


@functools.cache
def steady() -> tuple[BaselineState, tuple[BucketEvaluation, ...]]:
    """Forty days of the plain weekly cycle."""
    return fold(series(hours(40)))


def shifted(
    log_shift: float, at_day: int = 31, days: int = 40, **shape: float
) -> list[float | None]:
    return [
        cycle(index, **shape) * (math.exp(log_shift) if index >= hours(at_day) else 1.0)
        for index in range(hours(days))
    ]


def confirmations(evaluations: tuple[BucketEvaluation, ...]) -> list[BucketEvaluation]:
    return [e for e in evaluations if e.change_point.confirmed_now]


# --- robust statistics -------------------------------------------------------------------


def test_median_and_mad_are_exact_and_unmoved_by_an_outlier() -> None:
    assert median_and_mad([1.0, 2.0, 3.0, 4.0, 100.0]) == (3.0, 1.0)
    clean = median_and_mad([10.0, 11.0, 12.0, 13.0, 14.0])
    dirty = median_and_mad([10.0, 11.0, 12.0, 13.0, 9_999.0])
    assert clean == dirty == (12.0, 1.0)  # the mean would have moved by thousands
    assert median_and_mad([7.0, 7.0, 7.0]) == (7.0, 0.0)


def test_the_scale_floor_stops_a_flat_history_making_infinite_anomalies() -> None:
    assert robust_scale(0.0, 0.05) == 0.05
    assert robust_scale(1.0, 0.05) == pytest.approx(1.4826)
    flat = [1000.0] * hours(3) + [1100.0]
    _, evaluations = fold(flat)
    bump = evaluations[-1]
    assert bump.baseline("24h").mad == 0.0
    assert bump.baseline("24h").robust_scale == STEAM.scale_floor
    assert bump.residual_scale == STEAM.scale_floor
    assert bump.robust_z.value == pytest.approx(
        (math.log1p(1100) - math.log1p(1000)) / STEAM.scale_floor
    )
    assert math.isfinite(bump.robust_z.value or math.inf)


def test_percentile_is_a_mid_rank_among_other_values() -> None:
    assert mid_rank_percentile(5.0, [1.0, 2.0, 3.0, 4.0]) == 100.0
    assert mid_rank_percentile(0.0, [1.0, 2.0, 3.0, 4.0]) == 0.0
    assert mid_rank_percentile(2.0, [1.0, 2.0, 2.0, 3.0]) == 50.0


def test_theil_sen_is_exact_on_a_line_and_resists_a_wild_point() -> None:
    line = [(float(x), 2.0 + 0.5 * x) for x in range(8)]
    assert theil_sen_slope(line) == pytest.approx(0.5)
    wild = [*line[:-1], (7.0, 500.0)]
    assert theil_sen_slope(wild) == pytest.approx(0.5)
    least_squares = statistics.linear_regression(*zip(*wild, strict=True)).slope
    assert least_squares > 20  # what the robust slope avoided
    assert theil_sen_slope([(0.0, 1.0), (0.0, 9.0), (2.0, 5.0)]) == pytest.approx(0.0)
    with pytest.raises(ValueError, match="two points"):
        theil_sen_slope([(1.0, 1.0), (1.0, 2.0)])


# --- aggregation -------------------------------------------------------------------------


def slots(start: datetime, values: Sequence[int | None]) -> list[SlotResolution]:
    """Resolved 15-minute slots from `start`; None is a slot whose only attempt failed."""
    resolved = []
    for index, value in enumerate(values):
        at = start + index * SLOT.step
        made = attempt(
            at + timedelta(minutes=1),
            SampleStatus.OBSERVED if value is not None else SampleStatus.FAILED,
            value,
            attempt_id=f"a{index}",
        )
        resolved.append(resolve_slot(slot_key(at), SLOT, (made,)))
    return resolved


def test_an_hour_is_valid_with_three_of_its_four_samples() -> None:
    full, three, two = (
        hourly_aggregates(slots(MONDAY, values), MONDAY, MONDAY)[0]
        for values in ([100, 200, 300, 400], [100, None, 300, 500], [100, None, None, 400])
    )
    assert (full.mean, full.maximum, full.valid_count, full.coverage, full.valid) == (
        250.0,
        400.0,
        4,
        1.0,
        True,
    )
    assert (three.mean, three.maximum, three.valid_count, three.coverage) == (300.0, 500.0, 3, 0.75)
    assert three.valid
    assert not two.valid and two.mean is None and two.maximum is None  # 2 of 4 is not an hour
    assert (two.valid_count, two.expected_count, two.coverage) == (2, 4, 0.5)
    assert (full.start, full.end, full.rule_version) == (MONDAY, MONDAY + HOUR, "baseline_rules_v1")
    empty = hourly_aggregates([], MONDAY, MONDAY)[0]
    assert not empty.valid and empty.mean is None and empty.valid_count == 0


def day_of(per_hour: Sequence[Sequence[int | None]]) -> list[SlotResolution]:
    return [
        slot for hour, values in enumerate(per_hour) for slot in slots(MONDAY + hour * HOUR, values)
    ]


def test_a_day_needs_eighteen_valid_hours_and_seventy_two_samples() -> None:
    eighteen = [[100 + hour] * 4 for hour in range(18)] + [[None] * 4] * 6
    (valid,) = daily_aggregates(hourly_aggregates(day_of(eighteen), MONDAY, MONDAY + 23 * HOUR))
    assert valid.valid and (valid.valid_hours, valid.valid_samples) == (18, 72)
    assert (valid.expected_hours, valid.expected_samples) == (24, 96)
    assert (valid.hour_coverage, valid.coverage) == (0.75, 0.75)

    # Twenty-three hours each with 3 samples: plenty of valid hours, only 69 samples.
    thin = [[100, 100, 100, None]] * 23 + [[None] * 4]
    (starved,) = daily_aggregates(hourly_aggregates(day_of(thin), MONDAY, MONDAY + 23 * HOUR))
    assert (starved.valid_hours, starved.valid_samples) == (23, 69)
    assert not starved.valid
    assert starved.peak is None and starved.typical is None and starved.mean is None

    seventeen = [[100] * 4] * 17 + [[100, 100, None, None]] * 7  # 82 samples, 17 valid hours
    (short,) = daily_aggregates(hourly_aggregates(day_of(seventeen), MONDAY, MONDAY + 23 * HOUR))
    assert (short.valid_hours, short.valid_samples, short.valid) == (17, 82, False)


def test_daily_peak_typical_and_mean_come_from_valid_hourly_means() -> None:
    per_hour = [[100 * (hour + 1)] * 4 for hour in range(24)]  # hourly means 100 .. 2400
    per_hour[5] = [600, 600, 600, 50_000]  # one wild sample inside one hour
    (day,) = daily_aggregates(hourly_aggregates(day_of(per_hour), MONDAY, MONDAY + 23 * HOUR))
    means = [100.0 * (hour + 1) for hour in range(24)]
    means[5] = (600 * 3 + 50_000) / 4
    assert day.peak == max(means) and day.typical == statistics.median(means)
    assert day.mean == pytest.approx(statistics.fmean(means))
    assert day.peak < 50_000  # the peak is of hourly means, not of raw samples


def test_concurrency_is_never_summed_across_time() -> None:
    constant = [[100] * 4] * 24
    hourly = hourly_aggregates(day_of(constant), MONDAY, MONDAY + 23 * HOUR)
    (day,) = daily_aggregates(hourly)
    assert day.peak == day.typical == day.mean == 100.0  # not 400 per hour, not 9,600 per day
    assert all(hour.mean == 100.0 for hour in hourly)
    assert WORDING == "concurrent players sampled over time"
    names = set(aggregate.DailyAggregate.model_fields) | set(aggregate.HourlyAggregate.model_fields)
    assert not any(word in name for name in names for word in ("total", "active", "sum", "players"))


def test_the_current_level_is_the_latest_selected_observation() -> None:
    resolved = slots(MONDAY, [100, 200, None, 250])
    now = current_level(resolved)
    assert now is not None and now.value == 250 and now.slot_start == MONDAY + 3 * SLOT.step
    assert current_level(slots(MONDAY, [None, None])) is None


def test_hourly_buckets_carry_missing_hours_as_missing() -> None:
    hourly = hourly_aggregates(
        slots(MONDAY, [10, 10, 10, 10, 5, None, None, None]), MONDAY, MONDAY + HOUR
    )
    buckets = hourly_buckets(hourly)
    assert [(b.value, b.samples, b.expected_samples) for b in buckets] == [
        (10.0, 4, 4),
        (None, 1, 4),
    ]
    with pytest.raises(ValueError, match="15-minute slots"):
        hourly_aggregates([resolve_slot(slot_key(MONDAY), Granularity.HOURLY, ())], MONDAY, MONDAY)


# --- configuration and rule versions -----------------------------------------------------


def test_each_signal_has_its_own_configuration_and_no_borrowed_numbers() -> None:
    assert STEAM.source_family is SourceFamily.STEAM_PLAYERS
    assert WIKI.source_family is SourceFamily.WIKIMEDIA_READERSHIP
    assert STEAM.rule_version == WIKI.rule_version == "baseline_rules_v1"
    assert STEAM.ewma is not None and STEAM.cusum is not None
    assert (STEAM.ewma.fast_half_life_buckets, STEAM.ewma.slow_half_life_buckets) == (6, 24)
    assert STEAM.velocity_span_buckets == 3
    cusum = STEAM.cusum
    assert (cusum.clip, cusum.slack, cusum.threshold) == (4.0, 0.5, 5.0)
    assert (cusum.confirm_count, cusum.confirm_of) == (3, 4)
    assert (cusum.reset_after_gap_buckets, cusum.freeze_max_buckets) == (24, 168)
    assert STEAM.low_base_floor == 50 and STEAM.unsupported == ()
    assert [p.basis for p in STEAM.seasonality] == [
        SeasonalityBasis.HOUR_OF_WEEK,
        SeasonalityBasis.HOUR_OF_DAY,
    ]
    # Daily pageviews get no hourly constants: those features simply do not exist there.
    assert WIKI.ewma is None and WIKI.velocity_span_buckets is None and WIKI.cusum is None
    assert WIKI.unsupported == (
        "lift_fast",
        "lift_slow",
        "velocity",
        "acceleration",
        "change_point",
    )
    assert [p.basis for p in WIKI.seasonality] == [SeasonalityBasis.DAY_OF_WEEK]
    with pytest.raises(UnknownFeatureConfigError):
        feature_config("steam_current_players", "concurrent_players", Granularity.DAILY)
    with pytest.raises(UnknownFeatureConfigError):
        feature_config("wikimedia_pageviews", "pageviews", Granularity.DAILY, "baseline_rules_v9")


def test_a_rule_version_cannot_be_redefined_and_a_new_one_leaves_the_old_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config_module, "_REGISTRY", dict(config_module._REGISTRY))
    values = series(hours(26))
    before_state, before = fold(values)

    with pytest.raises(ValueError, match="needs a new rule version"):
        register_config(STEAM.model_copy(update={"scale_floor": 0.2}))
    register_config(STEAM)  # registering the identical configuration again is harmless

    v2 = STEAM.model_copy(update={"rule_version": "baseline_rules_v2", "scale_floor": 0.2})
    register_config(v2)
    assert feature_config(STEAM.source, STEAM.metric, Granularity.HOURLY) == STEAM
    after_state, after = fold(values)
    assert after == before and after_state == before_state  # v1 reproduces exactly
    assert all(e.rule_version == "baseline_rules_v1" for e in after)

    newer = feature_config(STEAM.source, STEAM.metric, Granularity.HOURLY, "baseline_rules_v2")
    _, under_v2 = fold(values, newer)
    assert all(e.rule_version == "baseline_rules_v2" for e in under_v2)
    assert under_v2[-1].residual_scale != before[-1].residual_scale
    with pytest.raises(StateMismatchError, match="baseline_rules_v1"):
        run(newer, before_state, contiguous(MONDAY + hours(26) * HOUR, [1.0], newer))


def test_every_evaluation_says_what_it_is_and_what_it_covers() -> None:
    _, evaluations = steady()
    last = evaluations[-1]
    assert last.rule_version == "baseline_rules_v1"
    assert last.valid_through == last.bucket_start + HOUR
    assert (last.samples, last.expected_samples, last.coverage) == (4, 4, 1.0)
    for stat in last.baselines:
        assert stat.window_end == last.bucket_start  # ends before the bucket it judges
        assert stat.window_start is not None and stat.window_start < stat.window_end
        assert stat.expected_count == {"24h": 24, "7d": 168, "28d": 672}[stat.name]
        assert stat.coverage == 1.0 and stat.reason is None
    with pytest.raises(ValidationError):
        FeatureValue()
    with pytest.raises(ValidationError):
        FeatureValue(value=1.0, reason="gap")


# --- maturity and freshness --------------------------------------------------------------


def test_maturity_follows_valid_history_not_the_calendar() -> None:
    _, evaluations = steady()
    assert evaluations[hours(3)].maturity is Maturity.WARMING_UP  # fewer than 5 valid days
    assert evaluations[hours(5) - 1].maturity is Maturity.WARMING_UP
    assert evaluations[hours(5)].maturity is Maturity.PARTIAL_BASELINE  # 5 valid days of 7
    almost = evaluations[hours(21) - 1]  # 20 valid days, and no hour-of-week profile yet
    assert almost.maturity is Maturity.PARTIAL_BASELINE
    assert almost.baseline("28d").valid_days == 20
    mature = evaluations[hours(21)]  # 21 valid days AND the seasonal comparison is available
    assert mature.maturity is Maturity.MATURE
    assert mature.baseline("28d").valid_days == 21
    assert mature.seasonality_basis is SeasonalityBasis.HOUR_OF_WEEK

    empty = normalise(STEAM, initial_state(STEAM, "app"), MONDAY)
    assert (empty.maturity, empty.freshness) == (Maturity.NO_HISTORY, Freshness.NO_DATA)
    assert empty.log_lift.reason == "no_history" and empty.current_level.reason == "no_history"
    _, only_gaps = fold([None, None, None])
    assert all(e.maturity is Maturity.NO_HISTORY for e in only_gaps)
    assert only_gaps[-1].log_lift.reason == "no_history"


def test_twenty_eight_days_on_the_calendar_are_not_twenty_eight_days_of_history() -> None:
    # Sampled on alternate days only: 35 calendar days, about 17 valid ones.
    patchy = [cycle(index) if (index // 24) % 2 == 0 else None for index in range(hours(35))]
    _, evaluations = fold(patchy)
    last_valid = next(e for e in reversed(evaluations) if e.level is not None)
    assert last_valid.baseline("28d").valid_days == 14
    assert not last_valid.baseline("28d").available
    assert last_valid.baseline("28d").reason == "insufficient_28d_history"
    assert last_valid.maturity is not Maturity.MATURE
    assert last_valid.baseline("7d").valid_days in (3, 4)
    assert last_valid.maturity is Maturity.WARMING_UP


def test_freshness_is_separate_from_maturity() -> None:
    values = [*series(hours(30)), None, None]
    state, evaluations = fold(values)
    assert evaluations[-1].maturity is Maturity.MATURE  # history is as good as it was
    as_of = MONDAY + len(values) * HOUR
    signal = normalise(STEAM, state, as_of)
    assert (signal.maturity, signal.freshness) == (Maturity.MATURE, Freshness.STALE)
    assert signal.log_lift.reason == signal.velocity.reason == signal.bucket_level.reason == "stale"
    assert "stale" in signal.reason_codes and not signal.observed

    fresh_state, _ = fold(values[:-1])  # one missing hour is a gap, not yet stale
    one_gap = normalise(STEAM, fresh_state, as_of - HOUR)
    assert one_gap.freshness is Freshness.FRESH and one_gap.bucket_level.reason == "gap"

    # A state that simply was not advanced is stale too: nothing recent was looked at.
    current, _ = fold(series(hours(30)))
    assert normalise(STEAM, current, MONDAY + hours(30) * HOUR).freshness is Freshness.FRESH
    assert (
        normalise(STEAM, current, MONDAY + hours(30) * HOUR + 2 * HOUR).freshness is Freshness.STALE
    )
    with pytest.raises(ValueError, match="after as_of"):
        normalise(STEAM, current, MONDAY)


# --- baselines and seasonality -----------------------------------------------------------


def test_baseline_windows_have_their_own_minimum_history_and_reasons() -> None:
    _, evaluations = steady()
    early = evaluations[10]
    assert [s.reason for s in early.baselines] == [
        "insufficient_24h_history",
        "insufficient_7d_history",
        "insufficient_28d_history",
    ]
    assert early.expected_level.reason == early.log_lift.reason == "insufficient_24h_history"
    assert evaluations[17].baseline("24h").reason == "insufficient_24h_history"
    assert evaluations[18].baseline("24h").available  # 18 valid hours
    day_six = evaluations[hours(6)]
    assert day_six.baseline("7d").available and day_six.baseline("7d").valid_days == 6
    assert not day_six.baseline("28d").available
    assert day_six.baseline("28d").mean is None  # mean and std only where the window is valid
    mature = evaluations[-1]
    long = mature.baseline("28d")
    assert long.median is not None and long.mad is not None and long.mad > 0
    assert long.robust_scale == pytest.approx(max(rules.MAD_TO_SIGMA * long.mad, STEAM.scale_floor))
    assert long.mean is not None and long.std is not None and long.std > 0
    assert long.median_level == pytest.approx(math.expm1(long.median))
    assert mature.baseline("24h").mean is None  # not configured to emit them


def test_the_expected_value_uses_the_best_comparison_available_and_says_which() -> None:
    _, evaluations = steady()
    assert evaluations[10].seasonality_basis is None
    assert evaluations[hours(2)].seasonality_basis is SeasonalityBasis.TRAILING_LEVEL
    assert evaluations[hours(7) - 1].seasonality_basis is SeasonalityBasis.TRAILING_LEVEL
    week_two = evaluations[hours(7)]
    assert week_two.seasonality_basis is SeasonalityBasis.HOUR_OF_DAY
    assert week_two.seasonal_instances == 7
    assert evaluations[hours(21) - 1].seasonality_basis is SeasonalityBasis.HOUR_OF_DAY
    week_four = evaluations[hours(21)]
    assert week_four.seasonality_basis is SeasonalityBasis.HOUR_OF_WEEK
    assert week_four.seasonal_instances == 3
    assert evaluations[-1].seasonal_instances == 5


def test_a_regular_weekend_peak_is_expected_not_anomalous() -> None:
    state, evaluations = steady()
    saturdays = [
        e
        for e in evaluations[hours(21) :]
        if e.bucket_start.weekday() == 5 and e.robust_z.value is not None
    ]
    assert len(saturdays) >= 48
    for saturday in saturdays:
        assert saturday.seasonality_basis is SeasonalityBasis.HOUR_OF_WEEK
        assert abs(saturday.log_lift.value or 0) < 0.02  # 50% busier, and fully expected
        assert abs(saturday.robust_z.value or 0) < STEAM.cusum.slack  # type: ignore[union-attr]
        assert (saturday.anomaly_strength.value or 0) < 0.1
    # Against the coarser hour-of-day comparison the same weekend looks far above normal,
    # which is exactly why change points are not looked for under it.
    coarse = next(
        e
        for e in evaluations[hours(7) : hours(21)]
        if e.bucket_start.weekday() == 5 and e.seasonality_basis is SeasonalityBasis.HOUR_OF_DAY
    )
    assert (coarse.log_lift.value or 0) > 0.3 and not coarse.change_point.updated
    assert confirmations(evaluations) == [] and state.cusum.history == ()


def test_daily_signals_use_day_of_week_and_never_an_hourly_profile() -> None:
    weekly = [1000.0 * (2.0 if day % 7 >= 5 else 1.0) + (day * 37 % 11) for day in range(70)]
    _, evaluations = run(WIKI, initial_state(WIKI, "game"), contiguous(MONDAY, weekly, WIKI))
    assert evaluations[20].seasonality_basis is SeasonalityBasis.TRAILING_LEVEL
    assert evaluations[21].seasonality_basis is SeasonalityBasis.DAY_OF_WEEK
    assert {e.seasonality_basis for e in evaluations} <= {
        None,
        SeasonalityBasis.TRAILING_LEVEL,
        SeasonalityBasis.DAY_OF_WEEK,
    }
    weekend = evaluations[-1]  # day 69 is a Sunday
    assert weekend.bucket_start.weekday() == 6
    assert abs(weekend.log_lift.value or 9) < 0.02 and abs(weekend.robust_z.value or 9) < 0.5
    assert weekend.maturity is Maturity.MATURE
    for name in ("lift_fast", "lift_slow", "velocity", "acceleration"):
        assert getattr(weekend, name).reason == "unsupported_for_granularity"
    assert not weekend.change_point.supported
    assert weekend.change_point.reason == "unsupported_for_granularity"
    assert weekend.trend_long.value is not None and weekend.trailing_percentile.value is not None


# --- lift, low base, percentile and anomaly strength --------------------------------------


def test_lift_is_measured_in_log_space_against_the_expected_level() -> None:
    values = series(hours(30))
    values[-1] = (values[-1] or 0) * 1.5
    _, evaluations = fold(values)
    raised, expected = evaluations[-1], evaluations[-1].expected_level.value
    assert expected is not None and raised.level is not None
    assert raised.log_lift.value == pytest.approx(math.log1p(raised.level) - math.log1p(expected))
    assert raised.log_lift.value == pytest.approx(math.log(1.5), abs=0.02)
    assert raised.absolute_change.value == pytest.approx(raised.level - expected)
    assert raised.percentage_lift.value == pytest.approx(100 * (raised.level - expected) / expected)
    z = raised.robust_z.value
    assert (
        z is not None and raised.residual_scale is not None and raised.residual_centre is not None
    )
    assert raised.log_lift.value is not None
    assert z == pytest.approx(
        (raised.log_lift.value - raised.residual_centre) / raised.residual_scale
    )
    assert raised.anomaly_strength.value == pytest.approx(min(1.0, z / STEAM.anomaly_z_saturation))
    assert raised.residual_basis is ResidualBasis.SAME


def test_a_fall_keeps_its_signed_z_and_has_no_positive_anomaly_strength() -> None:
    values = series(hours(30))
    values[-1] = (values[-1] or 0) * 0.5
    _, evaluations = fold(values)
    fallen = evaluations[-1]
    assert (fallen.robust_z.value or 0) < -5  # the fall is recorded, signed
    assert fallen.anomaly_strength.value == 0.0  # and is not an opportunity signal
    assert (fallen.log_lift.value or 0) < 0 and (fallen.absolute_change.value or 0) < 0
    huge = series(hours(30))
    huge[-1] = (huge[-1] or 0) * 50
    assert fold(huge)[1][-1].anomaly_strength.value == 1.0  # clipped at one


def test_the_low_base_floor_only_withholds_the_percentage() -> None:
    """A game with about 20 players stays fully observable."""
    small = shifted(0.6, base=20, noise=0.0)
    state, evaluations = fold(small)
    after = evaluations[hours(31) + 1]
    assert (after.expected_level.value or 0) < STEAM.low_base_floor
    assert after.percentage_lift.value is None and after.percentage_lift.reason == "low_base"
    for name in ("log_lift", "absolute_change", "robust_z", "anomaly_strength", "velocity"):
        assert getattr(after, name).value is not None, name
    assert after.log_lift.value == pytest.approx(0.6, abs=0.05)
    assert evaluations[hours(31) + 6].acceleration.value is not None
    (confirmed,) = confirmations(evaluations)  # and its shift is still detected
    assert confirmed.change_point.current is not None
    assert confirmed.change_point.current.direction is Direction.UP
    assert normalise(STEAM, state, MONDAY + hours(40) * HOUR).maturity is Maturity.MATURE
    # Above the floor the percentage is reported.
    assert steady()[1][-1].percentage_lift.value is not None


def test_the_percentile_ranks_a_bucket_among_earlier_buckets_only() -> None:
    values = series(hours(30))
    values[-1] = 10 * max(v for v in values if v is not None)  # a new all-time high
    _, evaluations = fold(values)
    record = evaluations[-1]
    assert record.trailing_percentile.value == 100.0  # were it counted among them, it would be less
    assert record.percentile_samples == STEAM.percentile.lookback_buckets  # type: ignore[union-attr]
    assert record.percentile_samples == 672
    early = evaluations[hours(6)]
    assert early.trailing_percentile.reason == "insufficient_history"
    assert early.percentile_samples == hours(6) < STEAM.percentile.min_samples  # type: ignore[union-attr]
    assert evaluations[hours(7)].trailing_percentile.value is not None
    # The minimum is per signal: 30 daily buckets for pageviews, 168 hourly for players.
    assert (WIKI.percentile.min_samples, STEAM.percentile.min_samples) == (30, 168)  # type: ignore[union-attr]


# --- velocity, acceleration and trend ----------------------------------------------------


def test_velocity_is_the_rate_of_change_of_smoothed_lift_per_day() -> None:
    _, evaluations = fold(shifted(0.4))
    at = hours(31) + 4
    now, before = evaluations[at], evaluations[at - 3]
    assert now.lift_fast.value is not None and before.lift_fast.value is not None
    assert now.velocity.value == pytest.approx(
        (now.lift_fast.value - before.lift_fast.value) / (3 / 24)
    )
    assert now.velocity.value is not None
    assert now.velocity.value > 0.5  # lift is climbing fast, in log lift per day
    earlier = evaluations[at - 3]
    assert now.acceleration.value == pytest.approx(
        (now.velocity.value - (earlier.velocity.value or 0)) / (3 / 24)
    )
    quiet = evaluations[hours(30)]
    assert abs(quiet.velocity.value or 9) < 0.05 and abs(quiet.acceleration.value or 9) < 0.5
    # The smoothed lift needs two half-lives of valid buckets before it is reported.
    warm = evaluations[18 + 10]
    assert (
        warm.lift_fast.reason == "insufficient_history"
        and warm.velocity.reason == "insufficient_history"
    )
    assert evaluations[18 + 11].lift_fast.value is not None  # 12 updates = 2 x 6 hours
    assert evaluations[18 + 46].lift_slow.reason == "insufficient_history"
    assert evaluations[18 + 47].lift_slow.value is not None  # 48 updates = 2 x 24 hours


def test_a_gap_makes_velocity_and_acceleration_unavailable_and_is_never_bridged() -> None:
    values = series(hours(30))
    gap_at = hours(29)
    values[gap_at] = None
    _, evaluations = fold(values)
    assert evaluations[gap_at].velocity.reason == "gap"
    assert evaluations[gap_at].log_lift.reason == "gap" and evaluations[gap_at].level is None
    for offset in (1, 2, 3):  # the missing bucket is inside the three-hour span
        assert evaluations[gap_at + offset].velocity.reason == "gap", offset
        assert evaluations[gap_at + offset].acceleration.reason == "gap"
        assert evaluations[gap_at + offset].log_lift.value is not None  # lift itself is fine
    recovered = evaluations[gap_at + 4]
    assert recovered.velocity.value is not None
    assert recovered.acceleration.reason == "insufficient_history"  # no velocity 3 hours back
    assert evaluations[gap_at + 7].acceleration.value is not None


def test_trend_is_a_robust_slope_of_daily_typical_levels_in_log_units_per_day() -> None:
    growing = [cycle(index) * math.exp(0.01 * index / 24) for index in range(hours(35))]
    _, evaluations = fold(growing)
    last = evaluations[-1]
    assert last.trend_short.value == pytest.approx(0.01, abs=0.004)  # about 1% a day
    assert last.trend_long.value == pytest.approx(0.01, abs=0.002)
    assert (last.trend_short_points, last.trend_long_points) == (7, 28)
    assert evaluations[hours(4) + 5].trend_short.reason == "insufficient_history"  # 4 points
    assert evaluations[hours(5)].trend_short.value is not None  # 5 valid days
    assert evaluations[hours(20) + 23].trend_long.reason == "insufficient_history"
    assert evaluations[hours(21)].trend_long.value is not None  # 21 valid days
    assert evaluations[3].trend_short.reason == "no_history"
    flat = steady()[1][-1]
    assert abs(flat.trend_long.value or 9) < 0.002


# --- change points: the six cases --------------------------------------------------------


def test_case_1_a_persistent_positive_shift_fires_with_its_direction_and_start() -> None:
    state, evaluations = fold(shifted(0.3))
    (confirmed,) = confirmations(evaluations)
    change = confirmed.change_point.current
    assert change is not None and change.direction is Direction.UP
    assert change.started_at == MONDAY + hours(31) * HOUR  # the bucket the level moved in
    assert change.confirmed_at == change.started_at + 2 * HOUR  # third bucket: 3 of 4 agree
    assert change.evidence_sum >= STEAM.cusum.threshold  # type: ignore[union-attr]
    assert sum(1 for z in change.evidence_z if z > 0.5) >= 3
    assert change.freeze_until == change.started_at + 7 * DAY
    assert confirmed.change_point.phase is ChangePhase.ACTIVE
    assert state.cusum.history[0].started_at == change.started_at  # reproducible later
    before = evaluations[hours(31) - 1].change_point
    assert before.phase is ChangePhase.NONE and before.sum_up == 0 and before.updated


def test_case_2_a_persistent_negative_shift_fires_downward() -> None:
    _, evaluations = fold(shifted(-0.3))
    (confirmed,) = confirmations(evaluations)
    change = confirmed.change_point.current
    assert change is not None and change.direction is Direction.DOWN
    assert change.started_at == MONDAY + hours(31) * HOUR
    assert all(z < -0.5 for z in change.evidence_z[-3:])
    assert (confirmed.robust_z.value or 0) < -4 and confirmed.anomaly_strength.value == 0.0


def test_case_3_a_single_spike_does_not_fire_however_large() -> None:
    values = series(hours(40))
    spike_at = hours(31) + 5
    values[spike_at] = (values[spike_at] or 0) * 20
    state, evaluations = fold(values)
    spike = evaluations[spike_at]
    assert (spike.robust_z.value or 0) > 20  # enormous
    assert spike.change_point.sum_up == pytest.approx(3.5)  # clipped to 4, less the slack
    assert spike.change_point.sum_up < STEAM.cusum.threshold  # type: ignore[union-attr]
    assert spike.change_point.candidate_up_since == spike.bucket_start
    assert evaluations[spike_at + 8].change_point.sum_up == 0  # it drained away
    assert confirmations(evaluations) == [] and state.cusum.history == ()
    assert state.cusum.phase is ChangePhase.NONE


def test_case_3b_one_wild_raw_sample_inside_an_hour_does_not_fire() -> None:
    """The same case from the bottom: one 15-minute sample, through aggregation."""
    start = MONDAY
    resolved = []
    wild_slot = hours(31) * 4 + 9
    for index in range(hours(33) * 4):
        level = int(cycle(index // 4, noise=0.0))
        value = level * 30 if index == wild_slot else level
        at = start + index * SLOT.step
        made = attempt(at + timedelta(minutes=1), value=value, attempt_id=f"s{index}")
        resolved.append(resolve_slot(slot_key(at), SLOT, (made,)))
    hourly = hourly_aggregates(resolved, start, start + (hours(33) - 1) * HOUR)
    state, evaluations = run(STEAM, initial_state(STEAM, "app"), hourly_buckets(hourly))
    assert max(e.robust_z.value or 0 for e in evaluations) > 4
    assert confirmations(evaluations) == [] and state.cusum.history == ()


def test_case_4_a_regular_weekend_pattern_does_not_fire() -> None:
    state, evaluations = fold(series(hours(49), weekend=2.0))  # weekends twice as busy
    assert confirmations(evaluations) == [] and state.cusum.history == ()
    fed = [e for e in evaluations if e.change_point.updated]
    assert len(fed) > hours(20)  # the detector really was running, on every weekend since
    assert {e.seasonality_basis for e in fed} == {SeasonalityBasis.HOUR_OF_WEEK}
    assert (
        max(e.change_point.sum_up for e in fed) < 1
        and max(e.change_point.sum_down for e in fed) < 1
    )


def test_case_5_a_gradual_trend_is_a_trend_not_a_shift() -> None:
    drifting = [cycle(index) * math.exp(0.005 * index / 24) for index in range(hours(56))]
    state, evaluations = fold(drifting)
    assert confirmations(evaluations) == [] and state.cusum.history == ()
    last = evaluations[-1]
    assert last.trend_long.value == pytest.approx(0.005, abs=0.001)  # reported as a slope
    assert (last.log_lift.value or 0) > 0.05  # the level really is above its old profile
    assert abs(last.robust_z.value or 9) < STEAM.cusum.slack  # type: ignore[union-attr]
    assert abs(last.residual_centre or 0) > 0.05  # the steady bias was centred away


def test_case_6_an_outage_resets_the_sums_and_reports_why() -> None:
    values = series(hours(40))
    start, length = hours(31), 30
    values[start - 1] = (values[start - 1] or 0) * 20  # a spike leaves the sums non-zero
    for index in range(start, start + length):
        values[index] = None
    state, evaluations = fold(values)
    assert evaluations[start - 1].change_point.sum_up == pytest.approx(3.5)
    held = evaluations[start + 22].change_point  # 23 missing hours: state is still held
    assert held.sum_up == pytest.approx(3.5) and held.last_reset_reason is None
    reset = evaluations[start + 23].change_point  # the 24th missing hour
    assert reset.sum_up == 0 and reset.candidate_up_since is None
    assert reset.last_reset_reason == GAP_RESET
    assert reset.last_reset_at == MONDAY + (start + 23) * HOUR
    for missing in evaluations[start : start + length]:
        assert missing.level is None and missing.log_lift.reason == "gap"  # never a zero
        assert not missing.change_point.updated
    back = evaluations[start + length]
    assert back.level is not None and back.log_lift.value is not None
    assert abs(back.robust_z.value or 9) < 1  # the return is not a crash-and-recovery
    assert confirmations(evaluations) == [] and state.cusum.history == ()
    assert state.cusum.last_reset_reason == GAP_RESET


# --- surge exclusion ---------------------------------------------------------------------


def test_a_confirmed_surge_is_kept_out_of_later_baselines_for_at_most_seven_days() -> None:
    state, evaluations = fold(shifted(0.3, at_day=31, days=50))
    (confirmed,) = confirmations(evaluations)
    change = confirmed.change_point.current
    assert change is not None
    start = hours(31)

    # While frozen, the expected level stays where it was, so the lift stays visible.
    for day in (1, 3, 6):
        during = evaluations[start + hours(day)]
        assert during.change_point.phase is ChangePhase.ACTIVE
        assert during.log_lift.value == pytest.approx(0.3, abs=0.02)
        assert during.baseline("24h").excluded_count == 24
        assert not during.change_point.updated  # the sums rest while a surge is active
    assert evaluations[start + hours(6)].baseline("7d").excluded_count == hours(6)

    # On the seventh day the freeze ends and the new level is allowed in.
    thawed = evaluations[start + hours(7)]
    assert thawed.change_point.phase is ChangePhase.ABSORBING
    assert thawed.change_point.current is not None
    assert thawed.change_point.current.ended_reason == FREEZE_EXPIRED
    assert thawed.change_point.current.ended_at == change.started_at + 7 * DAY
    assert not state.window[-1].excluded
    excluded = [entry for entry in state.window if entry.excluded]
    assert len(excluded) == hours(7)
    assert excluded[0].start == change.started_at
    assert excluded[-1].start == change.started_at + 7 * DAY - HOUR

    # A week later the persistent level is the baseline, and it is not re-announced.
    settled = evaluations[start + hours(14) + 12]
    assert settled.change_point.phase is ChangePhase.NONE
    assert abs(settled.robust_z.value or 9) < 1
    assert evaluations[-1].baseline("24h").excluded_count == 0
    assert len(state.cusum.history) == 1 and len(confirmations(evaluations)) == 1


def test_a_surge_that_ends_early_stops_being_excluded() -> None:
    values = shifted(0.3, at_day=31, days=40)
    back_at = hours(33)
    for index in range(back_at, len(values)):
        values[index] = cycle(index)  # two days later the level is back to normal
    state, evaluations = fold(values)
    (ended,) = state.cusum.history
    assert ended.ended_reason == "reverted"
    assert ended.ended_at is not None and ended.ended_at <= MONDAY + (back_at + 4) * HOUR
    assert evaluations[back_at + 6].change_point.phase is ChangePhase.NONE
    assert not state.window[-1].excluded
    assert sum(entry.excluded for entry in state.window) <= hours(2) + 3
    assert len(confirmations(evaluations)) == 1  # the return to normal is not a new shift


# --- the state contract ------------------------------------------------------------------


def test_the_state_is_bounded_and_carries_what_cannot_be_summarised() -> None:
    state, evaluations = fold(series(hours(50)))
    assert len(state.window) == STEAM.history_buckets == 1008  # 42 days, never more
    assert state.window[-1].start == MONDAY + (hours(50) - 1) * HOUR
    assert state.valid_through == MONDAY + hours(50) * HOUR
    assert (state.source, state.metric, state.granularity) == (
        "steam_current_players",
        "concurrent_players",
        Granularity.HOURLY,
    )
    assert state.rule_version == "baseline_rules_v1"
    assert state.config_fingerprint == STEAM.fingerprint
    assert state.source_family is SourceFamily.STEAM_PLAYERS
    assert state.ewma.fast is not None and state.ewma.slow is not None and state.ewma.updates > 0
    assert state.latest == evaluations[-1] and state.observed_ever and state.gap_run == 0
    entry = state.window[-1]
    assert entry.value is not None and entry.residual is not None and entry.lift_fast is not None
    assert entry.basis is SeasonalityBasis.HOUR_OF_WEEK


def test_a_state_round_trips_exactly_and_continues_to_the_same_answers() -> None:
    values = shifted(0.3, at_day=31, days=40)
    full_state, full = fold(values)

    cut = hours(31) + 1  # mid-candidate: the sums are non-zero and nothing is confirmed yet
    head_state, head = fold(values[:cut])
    assert head_state.cusum.sum_up > 0 and head_state.cusum.candidate_up_since is not None

    restored = BaselineState.model_validate_json(head_state.model_dump_json())
    assert restored == head_state
    assert restored.model_dump_json() == head_state.model_dump_json()

    recent = contiguous(MONDAY + cut * HOUR, values[cut:], STEAM)
    resumed_state, tail = run(STEAM, restored, recent)
    assert head + tail == full  # every evaluation identical, bit for bit
    assert resumed_state == full_state
    assert resumed_state.model_dump_json() == full_state.model_dump_json()
    assert normalise(STEAM, resumed_state, MONDAY + hours(40) * HOUR) == normalise(
        STEAM, full_state, MONDAY + hours(40) * HOUR
    )
    # Resuming one bucket at a time, serialising between each, gives the same again.
    stepped = restored
    for bucket in recent[:30]:
        stepped, _ = run(
            STEAM, BaselineState.model_validate_json(stepped.model_dump_json()), [bucket]
        )
    assert stepped.latest == full[cut + 29]


def test_a_state_refuses_to_be_continued_out_of_order_or_off_the_grid() -> None:
    state, _ = fold(series(48))
    later = MONDAY + 50 * HOUR
    with pytest.raises(StateMismatchError, match="contiguous"):
        run(STEAM, state, contiguous(later, [1.0], STEAM))
    with pytest.raises(StateMismatchError, match="contiguous"):
        run(STEAM, state, contiguous(MONDAY, [1.0], STEAM))  # replaying the past
    with pytest.raises(StateMismatchError, match="period boundary"):
        run(
            STEAM,
            initial_state(STEAM, "app"),
            [Bucket(start=MONDAY + timedelta(minutes=7), samples=0, expected_samples=4)],
        )
    with pytest.raises(StateMismatchError):
        run(WIKI, state, [])


# --- no future leakage -------------------------------------------------------------------


def test_appending_the_future_never_changes_the_past() -> None:
    values = shifted(0.3, at_day=31, days=40)
    _, full = fold(values)
    for cut in (hours(10), hours(25), hours(31), hours(31) + 2, hours(35)):
        _, prefix = fold(values[:cut])
        assert prefix == full[:cut], cut  # what was computed then is what is computed now


def test_a_bucket_never_influences_the_baseline_it_is_judged_against() -> None:
    history = series(hours(30))
    judged = [fold([*history, value])[1][-1] for value in (1.0, 9_000.0, 5_000_000.0, None)]
    reference = judged[0]
    for other in judged[1:]:
        assert other.baselines == reference.baselines
        assert other.expected_level == reference.expected_level
        assert other.seasonality_basis is reference.seasonality_basis
        assert other.trend_long == reference.trend_long and other.maturity is reference.maturity
    assert len({e.log_lift.value for e in judged}) == 4  # only the comparison differs
    assert all(stat.window_end == reference.bucket_start for stat in reference.baselines)


def test_a_surge_changes_only_what_later_buckets_are_compared_with() -> None:
    values = shifted(0.3, at_day=31, days=40)
    _, full = fold(values)
    (confirmed,) = confirmations(full)
    at = full.index(confirmed)
    _, before_confirmation = fold(values[:at])
    assert before_confirmation == full[:at]  # nothing already evaluated was revised
    candidates = full[at - 2 : at]
    assert all(e.baseline("24h").excluded_count == 0 for e in candidates)
    assert confirmed.baseline("24h").excluded_count == 0  # judged before it was marked
    assert full[at + 1].baseline("24h").excluded_count == 3  # the next bucket sees the mark
    unshifted = fold(series(hours(40)))[1]
    assert full[: hours(31)] == unshifted[: hours(31)]


def test_the_engine_never_fills_interpolates_or_zeroes_a_missing_bucket() -> None:
    values = series(hours(30))
    for index in range(hours(26), hours(26) + 5):
        values[index] = None
    state, evaluations = fold(values)
    missing = [entry for entry in state.window if entry.value is None]
    assert len(missing) == 5 and all(entry.residual is None for entry in missing)
    assert all(e.level is None for e in evaluations[hours(26) : hours(26) + 5])
    after = evaluations[hours(27)]
    assert after.baseline("24h").valid_count == 19 and after.baseline("24h").coverage == round(
        19 / 24, 4
    )
    zeros = fold([*series(hours(26)), 0.0, 0.0])[1][-1]  # a real zero is a value, far below normal
    assert zeros.level == 0.0 and (zeros.log_lift.value or 0) < -5
