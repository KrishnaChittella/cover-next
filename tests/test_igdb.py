"""IGDB auth and adapter, offline.

The payloads here are SYNTHETIC: hand-built to the shape the IGDB documentation
describes, to test parsing. They are not recorded responses and say nothing about
real games. Recorded fixtures are a separate step that needs credentials.
"""

import json
import logging
import stat
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from metaspike.domain.signals import SourceErrorCode
from metaspike.domain.source_records import Admissibility, DatePrecision, ProductType
from metaspike.sources.base import SourceError
from metaspike.sources.credentials import Secret
from metaspike.sources.http import LimitedHttpClient
from metaspike.sources.igdb import GAME_FIELDS, IGDB_POLICY, IgdbCatalogueAdapter
from metaspike.sources.igdb_auth import TOKEN_URL, TWITCH_OAUTH_OPERATIONS, IgdbAuth, TokenCache
from metaspike.sources.limiter import Clock, RateLimiter
from tests.conftest import mock_client

CLIENT_ID = "synthetic-client-id-0001"
CLIENT_SECRET = "synthetic-client-secret-000000001"
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
SIXTY_DAYS = 60 * 24 * 3600

GAME = {
    "id": 1001,
    "name": "Synthetic Quest",
    "updated_at": 1790000000,
    "game_type": {"id": 0, "type": "Main Game"},
    "game_status": {"id": 4, "status": "Early Access"},
    "alternative_names": [{"id": 1, "name": "SQ"}, {"id": 2, "name": "Synthetic Quest I"}],
    "release_dates": [
        {
            "id": 1,
            "date": 1767225600,  # 2026-01-01
            "date_format": {"id": 0, "format": "YYYYMMDD"},
            "platform": {"id": 6, "name": "PC (Microsoft Windows)"},
            "release_region": {"id": 8, "region": "worldwide"},
            "status": {"id": 3, "name": "Early Access"},
        },
        {"id": 2, "date": 1798761600, "date_format": {"id": 2, "format": "YYYY"}},
        {"id": 3, "date_format": {"id": 7, "format": "TBD"}},
        {"id": 4, "date": 1767225600, "date_format": {"id": 99, "format": "SOMETHING_NEW"}},
    ],
    "platforms": [
        {"id": 6, "name": "PC (Microsoft Windows)"},
        {"id": 167, "name": "PlayStation 5"},
    ],
    "involved_companies": [
        {"id": 1, "developer": True, "publisher": False, "company": {"id": 9, "name": "Dev Co"}},
        {"id": 2, "developer": False, "publisher": True, "company": {"id": 8, "name": "Pub Co"}},
        {"id": 3, "developer": True, "publisher": True, "company": {"id": 7, "name": "Both Co"}},
    ],
    "franchises": [{"id": 5, "name": "Synthetic"}],
    "external_games": [
        {"id": 1, "uid": "424242", "external_game_source": {"id": 1, "name": "Steam"}},
        {"id": 2, "uid": "abc", "external_game_source": {"id": 5, "name": "GOG"}},
        {"id": 3, "uid": "no-source"},
    ],
    "websites": [{"id": 1, "url": "https://synthetic.example/"}],
    "parent_game": None,
    "version_parent": None,
}
EDITION = {
    "id": 1002,
    "name": "Synthetic Quest: Deluxe Edition",
    "game_type": {"type": "Bundle"},
    "version_parent": 1001,
}
DLC = {
    "id": 1003,
    "name": "Synthetic Quest: Lost Levels",
    "game_type": {"type": "DLC"},
    "parent_game": 1001,
}
PACK = {"id": 1006, "name": "Synthetic Quest: Hat Pack", "game_type": {"type": "Pack / Addon"}}
MYSTERY = {"id": 1004, "name": "Synthetic Mystery", "game_type": {"type": "Hologram"}}
UNTYPED = {"id": 1005, "name": "Synthetic Untyped"}


class FakeIgdb:
    """A stand-in for Twitch OAuth and the IGDB API that records what it was sent."""

    def __init__(self) -> None:
        self.token_requests: list[httpx.Request] = []
        self.api_requests: list[httpx.Request] = []
        self.tokens_issued = 0
        self.reject_tokens: set[str] = set()
        self.games: list[dict[str, Any]] = [GAME, EDITION, DLC, MYSTERY, UNTYPED, PACK]
        self.external: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(TOKEN_URL):
            self.token_requests.append(request)
            self.tokens_issued += 1
            return httpx.Response(
                200,
                json={
                    "access_token": f"synthetic-token-{self.tokens_issued}",
                    "expires_in": SIXTY_DAYS,
                    "token_type": "bearer",
                },
            )
        self.api_requests.append(request)
        bearer = request.headers["Authorization"].removeprefix("Bearer ")
        if bearer in self.reject_tokens:
            return httpx.Response(401, json={"message": "invalid token"})
        if request.url.path.endswith("/external_games"):
            return httpx.Response(200, json=self.external)
        return httpx.Response(200, json=self.games)


def build(
    server: FakeIgdb, cache_path: Path, now: list[datetime] | None = None
) -> tuple[IgdbCatalogueAdapter, IgdbAuth]:
    moment = now if now is not None else [NOW]
    client = mock_client(server)
    silent = Clock(lambda seconds: None)
    auth = IgdbAuth(
        LimitedHttpClient(client, RateLimiter(TWITCH_OAUTH_OPERATIONS, clock=silent)),
        Secret(CLIENT_ID),
        Secret(CLIENT_SECRET),
        TokenCache(cache_path),
        now=lambda: moment[0],
    )
    api = LimitedHttpClient(client, RateLimiter(IGDB_POLICY.operations, clock=silent))
    return IgdbCatalogueAdapter(api, auth, now=lambda: moment[0]), auth


@pytest.fixture
def cache_path(tmp_path: Path) -> Path:
    return tmp_path / ".metaspike" / "tokens" / "igdb.json"


@pytest.fixture
def server() -> FakeIgdb:
    return FakeIgdb()


# --- authentication ----------------------------------------------------------------


def test_token_is_requested_with_client_credentials_in_the_body(
    server: FakeIgdb, cache_path: Path
) -> None:
    _, auth = build(server, cache_path)
    assert auth.token().reveal() == "synthetic-token-1"

    (request,) = server.token_requests
    assert request.method == "POST" and str(request.url) == TOKEN_URL  # nothing in the URL
    body = request.content.decode()
    assert "grant_type=client_credentials" in body
    assert f"client_id={CLIENT_ID}" in body and f"client_secret={CLIENT_SECRET}" in body


def test_token_is_cached_on_disk_owner_only_and_reused_across_runs(
    server: FakeIgdb, cache_path: Path
) -> None:
    _, first = build(server, cache_path)
    first.token()
    first.token()
    assert server.tokens_issued == 1

    assert stat.S_IMODE(cache_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(cache_path.parent.stat().st_mode) == 0o700
    stored = json.loads(cache_path.read_text())
    assert set(stored) == {"access_token", "expires_at"}

    _, next_run = build(server, cache_path)  # a new process
    assert next_run.token().reveal() == "synthetic-token-1"
    assert server.tokens_issued == 1  # reused, not minted again


def test_expiry_is_respected_with_a_safety_margin(server: FakeIgdb, cache_path: Path) -> None:
    now = [NOW]
    _, auth = build(server, cache_path, now)
    auth.token()

    now[0] = NOW + timedelta(days=58)
    assert auth.token().reveal() == "synthetic-token-1"
    now[0] = NOW + timedelta(days=59, hours=12)  # inside the one-day refresh margin
    assert auth.token().reveal() == "synthetic-token-2"
    assert server.tokens_issued == 2

    _, later_run = build(server, cache_path, [NOW + timedelta(days=200)])
    assert later_run.token().reveal() == "synthetic-token-3"  # a stale cache is not trusted


def test_a_corrupt_cache_is_ignored_not_trusted(server: FakeIgdb, cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text('{"access_token": ')
    _, auth = build(server, cache_path)
    assert auth.token().reveal() == "synthetic-token-1"


def test_concurrent_callers_cause_a_single_token_request(
    server: FakeIgdb, cache_path: Path
) -> None:
    _, auth = build(server, cache_path)
    seen: list[str] = []
    threads = [
        threading.Thread(target=lambda: seen.append(auth.token().reveal())) for _ in range(16)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert server.tokens_issued == 1 and set(seen) == {"synthetic-token-1"}


def test_a_rejected_token_is_refreshed_once_and_the_request_retried(
    server: FakeIgdb, cache_path: Path
) -> None:
    adapter, auth = build(server, cache_path)
    auth.token()
    server.reject_tokens.add("synthetic-token-1")

    records = adapter.games_by_ids([1001])
    assert len(records) == 6 and server.tokens_issued == 2
    assert [r.headers["Authorization"] for r in server.api_requests] == [
        "Bearer synthetic-token-1",
        "Bearer synthetic-token-2",
    ]
    assert json.loads(cache_path.read_text())["access_token"] == "synthetic-token-2"


def test_a_second_rejection_is_a_credential_error_not_an_endless_loop(
    server: FakeIgdb, cache_path: Path
) -> None:
    adapter, _ = build(server, cache_path)
    server.reject_tokens.update({"synthetic-token-1", "synthetic-token-2"})
    with pytest.raises(SourceError) as failed:
        adapter.games_by_ids([1001])
    assert failed.value.code is SourceErrorCode.UNAUTHORIZED
    assert len(server.api_requests) == 2 and server.tokens_issued == 2


def test_invalidating_an_already_replaced_token_keeps_the_new_one(
    server: FakeIgdb, cache_path: Path
) -> None:
    _, auth = build(server, cache_path)
    old = auth.token()
    auth.invalidate(old)
    new = auth.token()
    auth.invalidate(old)  # a slower caller reports the old token late
    assert auth.token() == new and server.tokens_issued == 2


def test_a_refused_token_request_reports_no_secret(
    cache_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": f"invalid client secret {CLIENT_SECRET}"})

    client = mock_client(refuse)
    auth = IgdbAuth(
        LimitedHttpClient(client, RateLimiter(TWITCH_OAUTH_OPERATIONS)),
        Secret(CLIENT_ID),
        Secret(CLIENT_SECRET),
        TokenCache(cache_path),
    )
    with caplog.at_level(logging.DEBUG), pytest.raises(SourceError) as failed:
        auth.token()
    assert failed.value.code is SourceErrorCode.UNAUTHORIZED
    assert failed.value.message == "twitch oauth: token request rejected (HTTP 400)"
    everything = caplog.text + str(failed.value) + repr(auth.__dict__)
    assert CLIENT_SECRET not in everything and CLIENT_ID not in everything
    assert not cache_path.exists()


def test_tokens_and_credentials_never_reach_the_log(
    server: FakeIgdb, cache_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    adapter, _ = build(server, cache_path)
    with caplog.at_level(logging.DEBUG):
        adapter.games_by_ids([1001])
    assert "obtained a token valid until" in caplog.text
    for secret in (CLIENT_ID, CLIENT_SECRET, "synthetic-token-1"):
        assert secret not in caplog.text


# --- adapter -----------------------------------------------------------------------


def test_requests_use_current_field_names_and_only_identity_fields(
    server: FakeIgdb, cache_path: Path
) -> None:
    adapter, _ = build(server, cache_path)
    adapter.games_by_ids([1001, 1002, 1001])

    (request,) = server.api_requests
    assert request.method == "POST" and str(request.url) == "https://api.igdb.com/v4/games"
    assert request.headers["Client-ID"] == CLIENT_ID
    body = request.content.decode()
    assert body.endswith("where id = (1001,1002); limit 500;")
    requested = body.split(";")[0].removeprefix("fields ").split(",")
    assert requested == list(GAME_FIELDS)
    for current in (
        "game_type.type",
        "game_status.status",
        "external_games.external_game_source.name",
    ):
        assert current in requested
    for unwanted in ("category", "status", "summary", "cover", "rating", "screenshots", "*"):
        assert unwanted not in requested


def test_a_game_is_parsed_into_a_source_native_record(server: FakeIgdb, cache_path: Path) -> None:
    adapter, _ = build(server, cache_path)
    record = adapter.games_by_ids([1001])[0]

    assert (record.key.source, record.key.source_id) == ("igdb", "1001")
    assert record.title == "Synthetic Quest"
    assert record.alternative_titles == ("SQ", "Synthetic Quest I")
    assert record.product_type is ProductType.MAIN_GAME and record.source_category == "Main Game"
    assert record.admissibility is Admissibility.ENTITY_ELIGIBLE
    assert record.status == "early_access"
    assert record.platforms == ("PC (Microsoft Windows)", "PlayStation 5")
    assert record.developers == ("Dev Co", "Both Co")
    assert record.publishers == ("Pub Co", "Both Co")
    assert record.franchises == ("Synthetic",)
    assert [(e.source, e.value) for e in record.external_ids] == [
        ("steam", "424242"),
        ("gog", "abc"),
    ]
    assert record.websites == ("https://synthetic.example/",)
    assert record.parent is None and record.version_parent is None
    assert record.source_updated_at == datetime.fromtimestamp(1790000000, UTC)
    assert record.fetched_at == NOW
    assert not hasattr(record, "canonical_game_id")  # source identity only


def test_release_dates_keep_their_precision_and_drop_what_is_not_understood(
    server: FakeIgdb, cache_path: Path
) -> None:
    adapter, _ = build(server, cache_path)
    releases = adapter.games_by_ids([1001])[0].release_dates

    assert [(r.starts_on, r.precision) for r in releases] == [
        (date(2026, 1, 1), DatePrecision.DAY),
        (date(2027, 1, 1), DatePrecision.YEAR),
        (None, DatePrecision.TBD),
    ]  # the fourth has an unknown format and is left out, not guessed
    first = releases[0]
    assert (first.platform, first.region, first.status) == (
        "PC (Microsoft Windows)",
        "worldwide",
        "Early Access",
    )


def test_product_types_parents_and_unknowns(server: FakeIgdb, cache_path: Path) -> None:
    adapter, _ = build(server, cache_path)
    by_id = {record.key.source_id: record for record in adapter.games_by_ids([1001])}

    edition, dlc, mystery, untyped = (by_id[i] for i in ("1002", "1003", "1004", "1005"))
    assert edition.product_type is ProductType.BUNDLE
    assert edition.version_parent is not None and edition.version_parent.source_id == "1001"
    assert dlc.product_type is ProductType.DLC and dlc.parent is not None
    assert dlc.admissibility is Admissibility.RELATED_PRODUCT
    # A type IGDB adds later, or omits, is never read as "a game".
    assert mystery.product_type is ProductType.UNKNOWN and mystery.source_category == "Hologram"
    assert untyped.product_type is ProductType.UNKNOWN and untyped.source_category is None
    assert mystery.admissibility is untyped.admissibility is Admissibility.UNDETERMINED
    assert by_id["1006"].product_type is ProductType.PACK  # the live API's "Pack / Addon"


def test_updated_since_pages_until_a_short_page(server: FakeIgdb, cache_path: Path) -> None:
    adapter, _ = build(server, cache_path)
    full_page = [{"id": n, "name": f"Game {n}"} for n in range(1, 501)]
    pages = [full_page, full_page, [{"id": 9001, "name": "Last"}]]
    server.games = []

    def paged(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(TOKEN_URL):
            return server(request)
        server.api_requests.append(request)
        return httpx.Response(200, json=pages.pop(0))

    adapter._http._client = mock_client(paged)
    adapter._auth._http._client = mock_client(paged)
    records = list(adapter.games_updated_since(NOW - timedelta(days=1)))

    assert len(records) == 1001 and records[-1].title == "Last"
    bodies = [request.content.decode() for request in server.api_requests]
    since = int((NOW - timedelta(days=1)).timestamp())
    assert [body.split("; ", 1)[1] for body in bodies] == [
        f"where updated_at > {since}; sort updated_at asc; limit 500; offset {offset};"
        for offset in (0, 500, 1000)
    ]


def test_steam_links_are_followed_through_igdbs_own_external_ids(
    server: FakeIgdb, cache_path: Path
) -> None:
    adapter, _ = build(server, cache_path)
    server.external = [
        {"uid": "424242", "game": 1001, "external_game_source": {"name": "Steam"}},
        {"uid": "424242", "game": 7777, "external_game_source": {"name": "GOG"}},
        {"uid": "424242", "external_game_source": {"name": "Steam"}},
    ]
    adapter.games_for_steam_apps([424242])

    lookup, games = server.api_requests
    assert lookup.url.path.endswith("/external_games")
    assert 'where uid = ("424242")' in lookup.content.decode()
    assert "where id = (1001);" in games.content.decode()  # the GOG row and the orphan are ignored


def test_search_text_cannot_inject_query_syntax(server: FakeIgdb, cache_path: Path) -> None:
    adapter, _ = build(server, cache_path)
    adapter.search_games('Rust"; where id = 1; fields *', limit=5000)
    body = server.api_requests[0].content.decode()
    assert body.endswith('search "Rust   where id = 1  fields *"; limit 500;')
    assert adapter.search_games('";') == [] and len(server.api_requests) == 1


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(403), SourceErrorCode.UNAUTHORIZED),
        (httpx.Response(400, json=[{"title": "Syntax Error"}]), SourceErrorCode.HTTP_ERROR),
        (httpx.Response(200, content=b"<html>"), SourceErrorCode.INVALID_RESPONSE),
        (httpx.Response(200, json={"not": "a list"}), SourceErrorCode.INVALID_RESPONSE),
        (httpx.Response(200, json=[{"name": "no id"}]), SourceErrorCode.INVALID_RESPONSE),
    ],
)
def test_failures_surface_as_clean_source_errors(
    response: httpx.Response, code: SourceErrorCode, server: FakeIgdb, cache_path: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return server(request) if str(request.url).startswith(TOKEN_URL) else response

    adapter, _ = build(FakeIgdb(), cache_path)
    adapter._http._client = mock_client(handler)
    adapter._auth._http._client = mock_client(handler)
    with pytest.raises(SourceError) as failed:
        adapter.games_by_ids([1])
    assert failed.value.code is code
    assert "synthetic-token" not in failed.value.message and CLIENT_ID not in failed.value.message


def test_lookup_vocabularies_can_be_read_for_verification(
    server: FakeIgdb, cache_path: Path
) -> None:
    adapter, _ = build(server, cache_path)
    server.games = [{"id": 1, "type": "Main Game"}, {"id": 2, "type": "DLC"}, {"id": 3}]
    assert adapter.vocabulary("game_types") == ("DLC", "Main Game")
    assert server.api_requests[0].content.decode() == "fields type; limit 500;"
    with pytest.raises(KeyError):
        adapter.vocabulary("games")
