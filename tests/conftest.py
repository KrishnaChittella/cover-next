"""Shared test helpers. Nothing here touches the network."""

import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from metaspike.catalogue.legacy_ids import canonical_id_for_legacy
from metaspike.catalogue.seed import load_seed_catalogue
from metaspike.domain.games import CanonicalGame, Catalogue, LifecycleState, SourceIdentifier
from metaspike.domain.observations import (
    Freshness,
    Observation,
    ObservationStatus,
    TimeSeries,
)
from metaspike.domain.source_policy import SourceOperations
from metaspike.domain.timeseries import Granularity, TimeWindow, start_of_day
from metaspike.pipeline.collection import window_ending
from metaspike.pipeline.normalize import build_time_series
from metaspike.pipeline.run import RunResult, run_pipeline
from metaspike.scoring.v0 import V0_INPUT_SIGNAL
from metaspike.sources.http import LimitedHttpClient, RetryConfig
from metaspike.sources.limiter import Clock, QuotaLedger, RateLimiter
from metaspike.sources.registry import default_registry
from metaspike.sources.replay import replay_client
from metaspike.sources.wikimedia import ENGLISH_WIKIPEDIA, SOURCE_ID

RECORDINGS = Path(__file__).parent / "fixtures" / "wikimedia"
RECORDED_END = date(2026, 10, 3)
RUN_TIME = datetime(2026, 10, 4, 6, 0, tzinfo=UTC)
GAME_ID = canonical_id_for_legacy("msg_0001")

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
    period_end = start_of_day(day) + timedelta(days=1)
    return Observation(
        canonical_game_id=GAME_ID,
        source=SOURCE_ID,
        metric=V0_INPUT_SIGNAL.metric,
        granularity=Granularity.DAILY,
        period_start=start_of_day(day),
        value=value,
        status=ObservationStatus.OBSERVED,
        observed_at=observed_at,
        freshness=Freshness(period_end=period_end, lag_hours=0),
    )


def series_of(values: Sequence[float | None], end: date = RECORDED_END) -> TimeSeries:
    """A daily series ending on `end`; None entries become explicit missing days."""
    window = TimeWindow.of_days(end - timedelta(days=len(values) - 1), end)
    observations = [
        observation(window.start + timedelta(days=offset), value)
        for offset, value in enumerate(values)
        if value is not None
    ]
    return build_time_series(GAME_ID, V0_INPUT_SIGNAL, window, observations, RUN_TIME)


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


def make_id(number: int) -> str:
    """A deterministic, valid canonical id for tests."""
    return str(uuid.UUID(int=number, version=4))


def mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def catalogue() -> Catalogue:
    return load_seed_catalogue()


@pytest.fixture
def artifacts_dir(tmp_path: Path) -> Path:
    return tmp_path / "artifacts"


def limited_client(
    handler: Callable[[httpx.Request], httpx.Response],
    operations: SourceOperations,
    sleeps: list[float] | None = None,
    ledger: QuotaLedger | None = None,
    retry: RetryConfig | None = None,
) -> LimitedHttpClient:
    """A limited client over a mock transport, with virtual time recorded in `sleeps`."""
    recorded = sleeps if sleeps is not None else []
    limiter = RateLimiter(operations, ledger=ledger, clock=Clock(recorded.append))
    return LimitedHttpClient(mock_client(handler), limiter, retry)
