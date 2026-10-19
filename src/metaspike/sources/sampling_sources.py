"""Wiring for SAMPLE_NOW sources: adapter, limiter, registry and offline replay.

Sampled sources live in their own registry with their own metric vocabulary. The
registry of the published pipeline never contains them, so nothing sampled can reach
an artifact by being registered in the wrong place.
"""

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from metaspike.domain.metrics import sampled_metrics
from metaspike.domain.source_policy import DeploymentStage
from metaspike.sources.credentials import STEAM_WEB_API_KEY_ENV, Secret, secret_from_env
from metaspike.sources.http import LimitedHttpClient
from metaspike.sources.limiter import LimiterPool
from metaspike.sources.registry import SourceRegistry
from metaspike.sources.steam_players import (
    PUBLIC_HOST,
    SAMPLING_RETRY,
    STEAM_CURRENT_PLAYERS_POLICY,
    SteamCurrentPlayersAdapter,
)

REPLAY_FILE_PREFIX = "current_players_"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def build_steam_players(
    client: httpx.Client,
    pool: LimiterPool,
    environ: Mapping[str, str] | None = None,
    now: Callable[[], datetime] = _utc_now,
    api_key: Secret | None = None,
) -> SteamCurrentPlayersAdapter:
    """Raises SourceError(missing_credentials) naming the variable that is not set."""
    http = LimitedHttpClient(client, pool.for_policy(STEAM_CURRENT_PLAYERS_POLICY), SAMPLING_RETRY)
    key = api_key or secret_from_env(STEAM_WEB_API_KEY_ENV, environ)
    return SteamCurrentPlayersAdapter(http, key, now)


def sampling_registry(
    steam_players: SteamCurrentPlayersAdapter,
    stage: DeploymentStage = DeploymentStage.PRIVATE_DEVELOPMENT,
) -> SourceRegistry:
    """Refuses the source at any stage its policy does not clear."""
    registry = SourceRegistry(metrics=sampled_metrics(), stage=stage)
    registry.register_sampler(steam_players, STEAM_CURRENT_PLAYERS_POLICY)
    return registry


def replay_sampling_client(recordings: Path) -> httpx.Client:
    """Serve recorded player-count responses from disk. No request leaves the machine.

    An app with no recording gets a transport error, which a run records as a failed
    attempt: an unrecorded app is never answered with an invented value.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != PUBLIC_HOST:
            raise httpx.ConnectError("replay: unexpected host", request=request)
        app_id = request.url.params.get("appid", "")
        recording = recordings / f"{REPLAY_FILE_PREFIX}{app_id}.json"
        if not app_id.isdigit() or not recording.is_file():
            raise httpx.ConnectError("replay: no recording for this app", request=request)
        recorded = json.loads(recording.read_text(encoding="utf-8"))["response"]
        return httpx.Response(
            recorded["status"], json=recorded["body"], headers=recorded.get("headers", {})
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def replay_recorded_apps(recordings: Path) -> frozenset[str]:
    """The app ids a replay directory holds a recorded response for."""
    return frozenset(
        path.stem.removeprefix(REPLAY_FILE_PREFIX)
        for path in recordings.glob(f"{REPLAY_FILE_PREFIX}*.json")
        if path.stem.removeprefix(REPLAY_FILE_PREFIX).isdigit()
    )


def replay_clock(recordings: Path) -> Callable[[], datetime]:
    """A replay's clock: it starts at the newest `recorded_at` and moves 1 ms a reading.

    A replayed answer is dated when the source gave it, never when it is replayed: read
    at the wall clock, a response recorded days earlier would be stored as a current
    observation. Each reading moves the clock on, so a run's moments stay ordered, and
    the same recordings give the same times on every run.
    """
    moments = [
        datetime.fromisoformat(json.loads(path.read_text(encoding="utf-8"))["recorded_at"])
        for path in sorted(recordings.glob(f"{REPLAY_FILE_PREFIX}*.json"))
    ]
    if not moments:
        raise ValueError(f"no recorded player-count response in {recordings}")
    start = max(moments).astimezone(UTC)
    ticks = 0

    def now() -> datetime:
        nonlocal ticks
        ticks += 1
        return start + timedelta(milliseconds=ticks)

    return now
