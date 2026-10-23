"""Twitch Helix live sums, offline. Every payload here is SYNTHETIC, in the documented shape."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from metaspike.domain.signals import SourceErrorCode
from metaspike.domain.source_policy import DeploymentStage, SourcePurpose
from metaspike.sources.credentials import Secret
from metaspike.sources.http import LimitedHttpClient, RetryConfig
from metaspike.sources.igdb_auth import TOKEN_URL, TWITCH_OAUTH_OPERATIONS, IgdbAuth, TokenCache
from metaspike.sources.limiter import Clock, RateLimiter
from metaspike.sources.twitch import (
    MAX_PAGES,
    TWITCH_HELIX_POLICY,
    TwitchHelixAdapter,
    TwitchLive,
    TwitchLiveStore,
)
from tests.conftest import mock_client

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)
GAME = "780b4850-6248-4a81-8fe0-7f0762376cfc"


def stream(stream_id: str, viewers: int) -> dict[str, object]:
    return {"id": stream_id, "viewer_count": viewers, "user_login": "synthetic", "title": "x"}


def build(handler: object, tmp_path: Path) -> TwitchHelixAdapter:
    def routed(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(TOKEN_URL):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 5_000_000})
        return handler(request)  # type: ignore[operator,no-any-return]

    client = mock_client(routed)
    silent = Clock(lambda seconds: None)
    auth = IgdbAuth(
        LimitedHttpClient(client, RateLimiter(TWITCH_OAUTH_OPERATIONS, clock=silent)),
        Secret("synthetic-client-id"),
        Secret("synthetic-client-secret"),
        TokenCache(tmp_path / "token.json"),
        now=lambda: NOW,
    )
    http = LimitedHttpClient(
        client,
        RateLimiter(TWITCH_HELIX_POLICY.operations, clock=silent),
        RetryConfig(max_attempts=1),
    )
    return TwitchHelixAdapter(http, auth, now=lambda: NOW)


def test_categories_are_matched_by_the_igdb_id_twitch_echoes_back(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        rows = [
            {"id": "33214", "name": "Fortnite", "igdb_id": "1905"},
            {"id": "999", "name": "Not asked for", "igdb_id": "5"},
            {"id": "1", "name": "No IGDB id", "igdb_id": ""},
        ]
        return httpx.Response(200, json={"data": rows})

    assert build(handler, tmp_path).categories(["1905", "7"]) == {"1905": "33214"}
    assert seen[0].url.params.get_list("igdb_id") == ["1905", "7"]


def test_live_sums_streams_once_and_marks_a_cut_off_reading_as_a_lower_bound(
    tmp_path: Path,
) -> None:
    pages = 0

    def endless(request: httpx.Request) -> httpx.Response:
        nonlocal pages
        pages += 1
        # The same stream on every page: paging may repeat streams.
        body = {"data": [stream("s1", 100), stream(f"p{pages}", 10)], "pagination": {"cursor": "c"}}
        return httpx.Response(200, json=body)

    reading = build(endless, tmp_path).live(GAME, "33214")
    assert pages == MAX_PAGES
    assert (reading.viewers, reading.streams, reading.complete) == (130, 4, False)

    def one_page(request: httpx.Request) -> httpx.Response:
        assert request.url.params["game_id"] == "33214" and request.url.params["first"] == "100"
        return httpx.Response(200, json={"data": [stream("a", 5)], "pagination": {}})

    reading = build(one_page, tmp_path).live(GAME, "33214")
    assert (reading.viewers, reading.streams, reading.complete) == (5, 1, True)


def test_a_failure_is_a_state_never_zero_viewers(tmp_path: Path) -> None:
    for status, code in ((403, SourceErrorCode.UNAUTHORIZED), (404, SourceErrorCode.HTTP_ERROR)):
        adapter = build(lambda _, s=status: httpx.Response(s, json={}), tmp_path)
        reading = adapter.live(GAME, "1")
        assert reading.error_code is code and reading.viewers is None and reading.streams is None
    garbled = build(lambda _: httpx.Response(200, json={"nope": 1}), tmp_path).live(GAME, "1")
    assert garbled.error_code is SourceErrorCode.INVALID_RESPONSE


def test_the_store_keeps_nothing_past_24_hours(tmp_path: Path) -> None:
    store = TwitchLiveStore(tmp_path)
    reading = TwitchLive(canonical_game_id=GAME, twitch_game_id="1", retrieved_at=NOW, viewers=3)
    store.write([reading], NOW - timedelta(hours=25))
    store.write([reading], NOW - timedelta(hours=2))
    assert len(list(tmp_path.glob("*.json"))) == 2
    assert store.latest(NOW) == (reading,)
    assert store.latest(NOW + timedelta(hours=23)) == ()  # 25 hours old: not read
    assert store.purge(NOW) == 1
    assert store.purge(NOW + timedelta(hours=23)) == 1
    assert not list(tmp_path.glob("*.json"))


def test_the_policy_clears_only_private_development() -> None:
    policy = TWITCH_HELIX_POLICY
    assert (
        policy.registration_refusal(
            SourcePurpose.DEMAND_SIGNALS, DeploymentStage.PRIVATE_DEVELOPMENT
        )
        is None
    )
    assert policy.cleared_for(DeploymentStage.PRIVATE_DEVELOPMENT)
    for stage in (DeploymentStage.PRIVATE_COLLECTION, DeploymentStage.PUBLIC_LAUNCH):
        assert not policy.cleared_for(stage)
    assert policy.registration_refusal(SourcePurpose.DEMAND_SIGNALS, DeploymentStage.PUBLIC_LAUNCH)
