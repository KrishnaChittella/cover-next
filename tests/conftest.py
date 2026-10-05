"""Shared test helpers. Nothing here touches the network."""

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from metaspike.catalogue.seed import load_seed_catalogue
from metaspike.domain.games import CanonicalGame, Catalogue, LifecycleState, SourceIdentifier
from metaspike.domain.observations import (
    DailySeries,
    Freshness,
    Metric,
    Observation,
    ObservationStatus,
)
from metaspike.pipeline.collection import window_ending
from metaspike.pipeline.normalize import build_daily_series
from metaspike.pipeline.run import RunResult, run_pipeline
from metaspike.sources.base import FetchWindow
from metaspike.sources.registry import default_registry
from metaspike.sources.replay import replay_client
from metaspike.sources.wikimedia import ENGLISH_WIKIPEDIA, SOURCE_ID

RECORDINGS = Path(__file__).parent / "fixtures" / "wikimedia"
RECORDED_END = date(2026, 10, 3)
RUN_TIME = datetime(2026, 10, 4, 6, 0, tzinfo=UTC)
GAME_ID = "msg_0001"

WIKI_IDENTIFIER = SourceIdentifier(
    source=SOURCE_ID, namespace=ENGLISH_WIKIPEDIA, value="Grand_Theft_Auto_V"
)
GAME = CanonicalGame(
    canonical_game_id=GAME_ID,
    slug="gta-v",
    name="Grand Theft Auto V",
    source_identifiers=(WIKI_IDENTIFIER,),
    lifecycle_state=LifecycleState.EVERGREEN,
)


def observation(day: date, value: float, observed_at: datetime = RUN_TIME) -> Observation:
    period_end = datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(days=1)
    return Observation(
        canonical_game_id=GAME_ID,
        source=SOURCE_ID,
        metric=Metric.DAILY_PAGEVIEWS,
        period_start=day,
        value=value,
        status=ObservationStatus.OBSERVED,
        observed_at=observed_at,
        freshness=Freshness(period_end=period_end, lag_hours=0),
    )


def series_of(values: Sequence[float | None], end: date = RECORDED_END) -> DailySeries:
    """A daily series ending on `end`; None entries become explicit missing days."""
    window = FetchWindow(start=end - timedelta(days=len(values) - 1), end=end)
    observations = [
        observation(window.start + timedelta(days=offset), value)
        for offset, value in enumerate(values)
        if value is not None
    ]
    return build_daily_series(
        GAME_ID, SOURCE_ID, Metric.DAILY_PAGEVIEWS, window, observations, RUN_TIME
    )


def steady(days: int = 90) -> list[float | None]:
    """A flat series around 100 views/day with a little day-to-day variation."""
    return [90.0 if index % 2 == 0 else 110.0 for index in range(days)]


def spiking() -> list[float | None]:
    """Steady at ~100/day, then 200, 300, 400 on the last three days."""
    values = steady()
    values[-6:] = [100.0, 100.0, 100.0, 200.0, 300.0, 400.0]
    return values


def run_fixture_pipeline(artifacts_dir: Path, when: datetime = RUN_TIME) -> RunResult:
    with replay_client(RECORDINGS) as client:
        return run_pipeline(
            load_seed_catalogue(),
            default_registry(client),
            window_ending(RECORDED_END),
            artifacts_dir,
            clock=lambda: when,
        )


def mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def catalogue() -> Catalogue:
    return load_seed_catalogue()


@pytest.fixture
def artifacts_dir(tmp_path: Path) -> Path:
    return tmp_path / "artifacts"
