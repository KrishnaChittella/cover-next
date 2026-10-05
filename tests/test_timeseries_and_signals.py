from datetime import UTC, date, datetime, timedelta, timezone

import httpx
import pytest

from metaspike.domain.metrics import (
    PAGEVIEWS,
    Aggregation,
    MetricDefinition,
    MetricRegistry,
    MetricRole,
    UnknownMetricError,
    default_metrics,
)
from metaspike.domain.observations import (
    Freshness,
    Observation,
    ObservationStatus,
    QualityFlag,
    TimeSeries,
)
from metaspike.domain.signals import (
    CollectionStatus,
    EntitySignals,
    Signal,
    SignalKey,
    SignalSpec,
    SourceErrorCode,
)
from metaspike.domain.timeseries import Granularity, TimeWindow, start_of_day
from metaspike.features.attention import compute_attention_features
from metaspike.pipeline.assess import assess_entity
from metaspike.pipeline.collection import signal_window, window_ending
from metaspike.pipeline.normalize import build_time_series
from metaspike.scoring.v0 import V0_INPUT_SIGNAL
from metaspike.sources.registry import SourceRegistrationError, SourceRegistry
from metaspike.sources.wikimedia import WIKIMEDIA_PAGEVIEWS_POLICY, WikimediaPageviewsAdapter
from tests.conftest import GAME_ID, RECORDED_END, RUN_TIME, series_of, spiking

PLAYERS = MetricDefinition(
    metric_id="concurrent_players",
    display_name="Concurrent players",
    unit="players",
    description="Test-only intraday metric.",
    role=MetricRole.DEMAND,
    aggregation=Aggregation.MEAN,
    granularities=(Granularity.FIFTEEN_MINUTES, Granularity.HOURLY, Granularity.DAILY),
)
HOURLY_PLAYERS = SignalKey(
    source="test_players", metric="concurrent_players", granularity=Granularity.HOURLY
)
DAY = start_of_day(RECORDED_END)


def point(key: SignalKey, period: datetime, value: float) -> Observation:
    return Observation(
        canonical_game_id=GAME_ID,
        source=key.source,
        metric=key.metric,
        granularity=key.granularity,
        period_start=period,
        value=value,
        status=ObservationStatus.OBSERVED,
        observed_at=RUN_TIME,
        freshness=Freshness(period_end=period + key.granularity.step, lag_hours=0),
    )


def series(key: SignalKey, start: datetime, values: list[float | None]) -> TimeSeries:
    step = key.granularity.step
    window = TimeWindow(start=start, end=start + (len(values) - 1) * step)
    observations = [
        point(key, start + index * step, value)
        for index, value in enumerate(values)
        if value is not None
    ]
    return build_time_series(GAME_ID, key, window, observations, RUN_TIME)


def collected(key: SignalKey, values: list[float | None], start: datetime = DAY) -> Signal:
    return Signal(
        canonical_game_id=GAME_ID,
        key=key,
        status=CollectionStatus.COLLECTED,
        series=series(key, start, values),
    )


# --- granularity and windows -------------------------------------------------------


@pytest.mark.parametrize(
    ("granularity", "step"),
    [
        (Granularity.FIFTEEN_MINUTES, timedelta(minutes=15)),
        (Granularity.HOURLY, timedelta(hours=1)),
        (Granularity.DAILY, timedelta(days=1)),
    ],
)
def test_granularity_floors_to_utc_aligned_periods(
    granularity: Granularity, step: timedelta
) -> None:
    moment = datetime(2026, 10, 3, 14, 37, 12, tzinfo=UTC)
    floored = granularity.floor(moment)
    assert granularity.step == step
    assert floored <= moment < floored + step
    assert granularity.is_aligned(floored) and not granularity.is_aligned(moment)
    assert granularity.periods_between(floored, floored + 5 * step) == 5


def test_periods_are_aligned_in_utc_whatever_the_input_timezone() -> None:
    tokyo = timezone(timedelta(hours=9))
    moment = datetime(2026, 10, 4, 8, 30, tzinfo=tokyo)  # 23:30 UTC on the 3rd
    assert Granularity.DAILY.floor(moment) == start_of_day(date(2026, 10, 3))
    assert Granularity.HOURLY.floor(moment) == datetime(2026, 10, 3, 23, tzinfo=UTC)


def test_one_window_of_days_covers_each_granularity_completely() -> None:
    days = window_ending(RECORDED_END)
    assert len(days.period_starts(Granularity.DAILY)) == 90
    hourly = signal_window(days, Granularity.HOURLY)
    assert len(hourly.period_starts(Granularity.HOURLY)) == 90 * 24
    assert hourly.end == start_of_day(RECORDED_END) + timedelta(hours=23)
    quarter = signal_window(days, Granularity.FIFTEEN_MINUTES)
    assert len(quarter.period_starts(Granularity.FIFTEEN_MINUTES)) == 90 * 96
    assert signal_window(days, Granularity.DAILY) == days


def test_a_window_must_be_ordered_and_aligned_to_be_enumerated() -> None:
    with pytest.raises(ValueError, match="must not be after"):
        TimeWindow(start=DAY, end=DAY - timedelta(days=1))
    ragged = TimeWindow(start=DAY + timedelta(minutes=5), end=DAY + timedelta(hours=3))
    with pytest.raises(ValueError, match="not aligned"):
        ragged.period_starts(Granularity.HOURLY)
    assert ragged.aligned(Granularity.HOURLY).period_starts(Granularity.HOURLY) == [
        DAY + timedelta(hours=1),
        DAY + timedelta(hours=2),
        DAY + timedelta(hours=3),
    ]


# --- daily and intraday series -----------------------------------------------------


def test_daily_series_is_one_point_per_day() -> None:
    daily = series_of(spiking())
    assert daily.granularity is Granularity.DAILY
    assert len(daily.points) == 90
    assert daily.points[-1].period_day == RECORDED_END
    assert daily.latest_due == daily.window_end == start_of_day(RECORDED_END)
    assert daily.points[1].period_start - daily.points[0].period_start == timedelta(days=1)


def test_hourly_series_keeps_gaps_explicit() -> None:
    hourly = series(HOURLY_PLAYERS, DAY, [None, 100.0, None, 120.0, 130.0, None])
    assert hourly.granularity is Granularity.HOURLY
    assert [p.period_start.hour for p in hourly.points] == [0, 1, 2, 3, 4, 5]
    assert hourly.values() == [None, 100.0, None, 120.0, 130.0, None]
    assert [p.quality.flags for p in hourly.points if p.value is None] == [
        (QualityFlag.BEFORE_FIRST_OBSERVATION,),
        (QualityFlag.SOURCE_GAP,),
        (QualityFlag.PENDING_PUBLICATION,),
    ]
    assert hourly.points[2].freshness.period_end == DAY + timedelta(hours=3)


def test_fifteen_minute_series_spans_a_day_in_96_points() -> None:
    key = HOURLY_PLAYERS.model_copy(update={"granularity": Granularity.FIFTEEN_MINUTES})
    intraday = series(key, DAY, [float(index) for index in range(96)])
    assert len(intraday.points) == 96
    assert intraday.points[-1].period_start == DAY + timedelta(hours=23, minutes=45)
    assert intraday.period_start_at(5) == DAY + timedelta(minutes=75)


def test_observations_and_series_reject_misaligned_or_mixed_data() -> None:
    with pytest.raises(ValueError, match="not aligned"):
        point(HOURLY_PLAYERS, DAY + timedelta(minutes=10), 1.0)

    daily_point = point(V0_INPUT_SIGNAL, DAY, 1.0)
    with pytest.raises(ValueError, match="different signal"):
        build_time_series(
            GAME_ID,
            HOURLY_PLAYERS,
            TimeWindow(start=DAY, end=DAY),
            [daily_point],
            RUN_TIME,
        )
    hourly = series(HOURLY_PLAYERS, DAY, [1.0, 2.0])
    with pytest.raises(ValueError, match="one point per period"):
        TimeSeries.model_validate(hourly.model_dump() | {"points": hourly.points[:1]})
    with pytest.raises(ValueError, match="contiguous"):
        TimeSeries.model_validate(hourly.model_dump() | {"points": hourly.points[::-1]})


def test_daily_feature_definitions_refuse_intraday_series() -> None:
    hourly = series(HOURLY_PLAYERS, DAY, [float(100 + index) for index in range(72)])
    with pytest.raises(ValueError, match="defined for daily series, not hourly"):
        compute_attention_features(hourly)


def test_the_same_metric_can_exist_at_two_granularities_for_one_entity() -> None:
    daily_key = HOURLY_PLAYERS.model_copy(update={"granularity": Granularity.DAILY})
    signals = EntitySignals(
        canonical_game_id=GAME_ID,
        signals=(collected(HOURLY_PLAYERS, [1.0] * 48), collected(daily_key, [1.0, 2.0])),
    )
    assert [s.key.granularity for s in signals.for_metric("concurrent_players")] == [
        Granularity.HOURLY,
        Granularity.DAILY,
    ]


# --- metric registry ---------------------------------------------------------------


def test_metric_registry_defines_what_a_metric_is_independent_of_granularity() -> None:
    registry = default_metrics()
    assert [m.metric_id for m in registry.definitions()] == ["pageviews"]
    pageviews = registry.get("pageviews")
    assert pageviews is PAGEVIEWS
    assert (pageviews.role, pageviews.aggregation) == (MetricRole.DEMAND, Aggregation.SUM)
    assert pageviews.supports(Granularity.DAILY) and not pageviews.supports(Granularity.HOURLY)
    assert "pageviews" in registry and "concurrent_players" not in registry


def test_metric_registry_rejects_duplicates_and_unknown_lookups() -> None:
    registry = MetricRegistry((PAGEVIEWS, PLAYERS))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(PLAYERS)
    with pytest.raises(UnknownMetricError):
        registry.get("watch_hours")
    with pytest.raises(ValueError):
        MetricDefinition.model_validate(PLAYERS.model_dump() | {"metric_id": "Bad Id"})
    with pytest.raises(ValueError):
        MetricDefinition.model_validate(PLAYERS.model_dump() | {"granularities": ()})


def test_creator_supply_is_a_distinct_role_from_demand() -> None:
    supply = MetricDefinition.model_validate(
        PLAYERS.model_dump() | {"metric_id": "live_channels", "role": "creator_supply"}
    )
    assert supply.role is MetricRole.CREATOR_SUPPLY
    assert default_metrics().get(V0_INPUT_SIGNAL.metric).role is MetricRole.DEMAND


class _Adapter(WikimediaPageviewsAdapter):
    def __init__(self, *signals: SignalSpec) -> None:
        super().__init__(httpx.Client())
        self._signals = signals

    @property
    def signals(self) -> tuple[SignalSpec, ...]:
        return self._signals


@pytest.mark.parametrize(
    ("signals", "message"),
    [
        ((), "declares no signals"),
        ((SignalSpec(metric="watch_hours", granularity=Granularity.DAILY),), "not in the metric"),
        ((SignalSpec(metric="pageviews", granularity=Granularity.HOURLY),), "not defined at"),
        (
            (
                SignalSpec(metric="pageviews", granularity=Granularity.DAILY),
                SignalSpec(metric="pageviews", granularity=Granularity.DAILY),
            ),
            "signal twice",
        ),
    ],
)
def test_adapters_can_only_register_signals_the_metric_registry_defines(
    signals: tuple[SignalSpec, ...], message: str
) -> None:
    registry = SourceRegistry()
    with pytest.raises(SourceRegistrationError, match=message):
        registry.register(_Adapter(*signals), WIKIMEDIA_PAGEVIEWS_POLICY)
    assert registry.entries() == ()


# --- many signals, some missing ----------------------------------------------------


def v0_signal(values: list[float | None]) -> Signal:
    return Signal(
        canonical_game_id=GAME_ID,
        key=V0_INPUT_SIGNAL,
        status=CollectionStatus.COLLECTED,
        series=series_of(values),
    )


def absent(key: SignalKey, status: CollectionStatus) -> Signal:
    code = SourceErrorCode.TIMEOUT if status is CollectionStatus.FAILED else None
    return Signal(canonical_game_id=GAME_ID, key=key, status=status, error_code=code)


def test_an_entity_holds_many_metrics_and_reports_which_are_missing() -> None:
    news = SignalKey(source="test_news", metric="articles", granularity=Granularity.DAILY)
    signals = EntitySignals(
        canonical_game_id=GAME_ID,
        signals=(
            v0_signal(spiking()),
            collected(HOURLY_PLAYERS, [50.0] * 48),
            absent(news, CollectionStatus.FAILED),
        ),
    )

    assert signals.metrics_available() == {"pageviews", "concurrent_players"}
    assert signals.sources_available() == {"wikimedia_pageviews", "test_players"}
    assert [s.key for s in signals.missing()] == [news]
    assert signals.missing()[0].error_code is SourceErrorCode.TIMEOUT
    found = signals.get(HOURLY_PLAYERS)
    assert found is not None and found.series is not None and len(found.series.points) == 48
    assert signals.get(news.model_copy(update={"metric": "nope"})) is None


def test_signal_invariants() -> None:
    with pytest.raises(ValueError, match="exactly when it was collected"):
        Signal(canonical_game_id=GAME_ID, key=HOURLY_PLAYERS, status=CollectionStatus.COLLECTED)
    with pytest.raises(ValueError, match="does not belong"):
        Signal(
            canonical_game_id=GAME_ID,
            key=HOURLY_PLAYERS,
            status=CollectionStatus.COLLECTED,
            series=series_of(spiking()),
        )
    twice = (v0_signal(spiking()), v0_signal(spiking()))
    with pytest.raises(ValueError, match="same signal twice"):
        EntitySignals(canonical_game_id=GAME_ID, signals=twice)
    with pytest.raises(ValueError, match="belong to the entity"):
        EntitySignals(canonical_game_id="msg_0099", signals=(v0_signal(spiking()),))


def test_assessment_scores_from_its_input_signal_and_ignores_the_others() -> None:
    alone = assess_entity(EntitySignals(canonical_game_id=GAME_ID, signals=(v0_signal(spiking()),)))
    crowded = assess_entity(
        EntitySignals(
            canonical_game_id=GAME_ID,
            signals=(
                collected(HOURLY_PLAYERS, [9e9] * 48),
                v0_signal(spiking()),
                absent(
                    SignalKey(source="test_news", metric="articles", granularity=Granularity.DAILY),
                    CollectionStatus.FAILED,
                ),
            ),
        )
    )

    assert crowded.score == alone.score and crowded.score.value == 80.9
    assert crowded.confidence == alone.confidence
    assert crowded.confidence is not None and crowded.confidence.independent_sources == 1
    assert crowded.explanation == alone.explanation
    assert crowded.scored_signal == V0_INPUT_SIGNAL
    assert crowded.scored_features == alone.scored_features
    assert [entry.key for entry in crowded.signal_features] == [V0_INPUT_SIGNAL]
    assert crowded.features_for(HOURLY_PLAYERS) is None


@pytest.mark.parametrize(
    ("signals", "summary"),
    [
        ((), "no source currently covers"),
        ((absent(V0_INPUT_SIGNAL, CollectionStatus.NOT_MAPPED),), "no source currently covers"),
        ((absent(V0_INPUT_SIGNAL, CollectionStatus.FAILED),), "data collection failed"),
    ],
)
def test_a_missing_input_signal_leaves_the_game_unscored_with_a_reason(
    signals: tuple[Signal, ...], summary: str
) -> None:
    assessment = assess_entity(EntitySignals(canonical_game_id=GAME_ID, signals=signals))
    assert assessment.score.value is None and assessment.confidence is None
    assert assessment.scored_signal is None and assessment.scored_series is None
    assert summary in assessment.explanation.summary


def test_other_signals_cannot_stand_in_for_a_missing_input_signal() -> None:
    assessment = assess_entity(
        EntitySignals(
            canonical_game_id=GAME_ID,
            signals=(
                absent(V0_INPUT_SIGNAL, CollectionStatus.FAILED),
                collected(HOURLY_PLAYERS, [5.0] * 48),
            ),
        )
    )
    assert assessment.score.value is None
    assert assessment.signals.metrics_available() == {"concurrent_players"}
