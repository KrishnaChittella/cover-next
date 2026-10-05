"""Steam catalogue adapter, offline.

The response bodies here are SYNTHETIC and PROVISIONAL. Valve does not document the
response of IStoreService/GetAppList, so these tests fix only how the adapter
behaves for the shapes it tolerates. They are not evidence of what Steam returns;
that is established by the first authenticated call.
"""

import logging
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from metaspike.domain.signals import SourceErrorCode
from metaspike.domain.source_records import Admissibility, ProductType
from metaspike.sources.base import SourceError
from metaspike.sources.credentials import Secret
from metaspike.sources.http import LimitedHttpClient
from metaspike.sources.limiter import Clock, InMemoryQuotaLedger, RateLimiter
from metaspike.sources.steam_catalogue import (
    APP_LIST_URL,
    STEAM_CATALOGUE_POLICY,
    SteamAppKind,
    SteamCatalogueAdapter,
)
from tests.conftest import mock_client

KEY = "0123456789ABCDEF0123456789ABCDEF"
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def app(app_id: int, name: str | None = "Synthetic App", **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"appid": app_id, **extra}
    if name is not None:
        body["name"] = name
    return body


def adapter_for(
    *responses: httpx.Response,
) -> tuple[SteamCatalogueAdapter, list[httpx.Request], InMemoryQuotaLedger]:
    queue = list(responses)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    ledger = InMemoryQuotaLedger()
    limiter = RateLimiter(
        STEAM_CATALOGUE_POLICY.operations, ledger=ledger, clock=Clock(lambda seconds: None)
    )
    http = LimitedHttpClient(mock_client(handler), limiter)
    return SteamCatalogueAdapter(http, Secret(KEY), now=lambda: NOW), requests, ledger


def page(apps: list[dict[str, Any]], **envelope: Any) -> httpx.Response:
    return httpx.Response(200, json={"response": {"apps": apps, **envelope}})


def test_the_documented_endpoint_and_parameters_are_used() -> None:
    adapter, requests, _ = adapter_for(page([app(10)]))
    adapter.fetch_page(
        SteamAppKind.GAMES,
        modified_since=datetime(2026, 10, 1, tzinfo=UTC),
        after_appid=570,
        max_results=2500,
    )

    (request,) = requests
    assert request.method == "GET"
    assert str(request.url).split("?")[0] == APP_LIST_URL
    assert request.url.host == "api.steampowered.com"  # not the publisher-only partner host
    assert "IStoreService/GetAppList/v1" in request.url.path
    assert "ISteamApps" not in str(request.url)
    assert dict(request.url.params) == {
        "key": KEY,
        "max_results": "2500",
        "include_games": "true",
        "include_dlc": "false",
        "include_software": "false",
        "include_videos": "false",
        "include_hardware": "false",
        "if_modified_since": str(int(datetime(2026, 10, 1, tzinfo=UTC).timestamp())),
        "last_appid": "570",
    }


def test_optional_parameters_are_omitted_and_one_kind_is_requested_at_a_time() -> None:
    adapter, requests, _ = adapter_for(page([]))
    adapter.fetch_page(SteamAppKind.DLC)
    params = dict(requests[0].url.params)
    assert "if_modified_since" not in params and "last_appid" not in params
    assert params["max_results"] == "10000"  # the documented default
    assert [k for k, v in params.items() if v == "true"] == ["include_dlc"]


def test_page_size_must_stay_within_the_documented_maximum() -> None:
    adapter, requests, _ = adapter_for(page([]))
    for bad in (0, 50_001):
        with pytest.raises(ValueError, match="between 1 and 50000"):
            adapter.fetch_page(SteamAppKind.GAMES, max_results=bad)
    assert requests == []


def test_apps_become_source_native_records_and_observed_fields_are_reported() -> None:
    adapter, _, _ = adapter_for(
        page(
            [
                app(730, "Synthetic Shooter", last_modified=1790000000, price_change_number=9),
                app(731, None),
                app(732, "   "),
            ],
            have_more_results=True,
            last_appid=732,
        )
    )
    result = adapter.fetch_page(SteamAppKind.GAMES)

    (record,) = result.records
    assert (record.key.source, record.key.source_id) == ("steam_catalogue", "730")
    assert record.title == "Synthetic Shooter" and record.source_category == "games"
    assert record.source_updated_at == datetime.fromtimestamp(1790000000, UTC)
    assert record.fetched_at == NOW and record.external_ids == ()
    assert (result.apps_returned, result.apps_without_a_name) == (3, 2)
    assert result.observed_envelope_fields == {"apps", "have_more_results", "last_appid"}
    assert result.observed_app_fields == {"appid", "name", "last_modified", "price_change_number"}
    assert result.has_more is True and result.next_after_appid == 732


def test_being_listed_under_games_does_not_prove_something_is_a_game() -> None:
    games, _, _ = adapter_for(page([app(1)]))
    dlc, _, _ = adapter_for(page([app(2)]))
    software, _, _ = adapter_for(page([app(3)]))

    listed = games.fetch_page(SteamAppKind.GAMES).records[0]
    assert listed.product_type is ProductType.UNKNOWN
    assert listed.admissibility is Admissibility.UNDETERMINED  # a demo could be listed here

    addon = dlc.fetch_page(SteamAppKind.DLC).records[0]
    assert addon.product_type is ProductType.DLC
    assert addon.admissibility is Admissibility.RELATED_PRODUCT
    tool = software.fetch_page(SteamAppKind.SOFTWARE).records[0]
    assert tool.admissibility is Admissibility.NOT_A_GAME


def test_pagination_follows_the_continuation_until_the_source_says_stop() -> None:
    adapter, requests, ledger = adapter_for(
        page([app(1), app(2)], have_more_results=True, last_appid=2),
        page([app(3), app(4)], have_more_results=True, last_appid=4),
        page([app(5)]),
    )
    records = list(adapter.iter_apps(SteamAppKind.GAMES, page_size=2))

    assert [record.key.source_id for record in records] == ["1", "2", "3", "4", "5"]
    assert [request.url.params.get("last_appid") for request in requests] == [None, "2", "4"]
    today = datetime.now(UTC).date().isoformat()
    assert ledger.used("steam_web_api", today) == 3  # one unit of quota per page


def test_pagination_without_continuation_fields_stops_on_a_short_page() -> None:
    adapter, requests, _ = adapter_for(page([app(10), app(20)]), page([app(30)]))
    records = list(adapter.iter_apps(SteamAppKind.GAMES, page_size=2))
    assert [record.key.source_id for record in records] == ["10", "20", "30"]
    assert [request.url.params.get("last_appid") for request in requests] == [None, "20"]


def test_pagination_that_never_ends_is_stopped() -> None:
    adapter, requests, _ = adapter_for(page([app(1), app(2)], have_more_results=True))
    assert len(list(adapter.iter_apps(SteamAppKind.GAMES, page_size=2))) == 2
    assert len(requests) == 2  # the cursor did not advance, so it stops

    counter = iter(range(1, 10_000))
    runaway, _, _ = adapter_for()
    runaway._http._client = mock_client(
        lambda request: page([app(next(counter))], have_more_results=True)
    )
    with pytest.raises(SourceError, match="did not finish in 3 pages"):
        list(runaway.iter_apps(SteamAppKind.GAMES, page_size=1, max_pages=3))


def test_incremental_retrieval_passes_the_since_time_on_every_page() -> None:
    since = datetime(2026, 10, 3, tzinfo=UTC)
    adapter, requests, _ = adapter_for(
        page([app(1)], have_more_results=True, last_appid=1), page([])
    )
    list(adapter.iter_apps(SteamAppKind.GAMES, modified_since=since, page_size=1))
    assert [r.url.params["if_modified_since"] for r in requests] == [
        str(int(since.timestamp()))
    ] * 2


def test_an_empty_or_bare_response_is_an_empty_page() -> None:
    bodies: list[dict[str, Any]] = [{"response": {}}, {"apps": []}, {}]
    for body in bodies:
        adapter, _, _ = adapter_for(httpx.Response(200, json=body))
        result = adapter.fetch_page(SteamAppKind.GAMES)
        assert result.records == () and result.next_after_appid is None
        assert result.has_more is None


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(403, text="Forbidden"), SourceErrorCode.UNAUTHORIZED),
        (httpx.Response(401), SourceErrorCode.UNAUTHORIZED),
        (httpx.Response(400), SourceErrorCode.HTTP_ERROR),
        (httpx.Response(200, content=b"<html>"), SourceErrorCode.INVALID_RESPONSE),
        (httpx.Response(200, json=[1, 2]), SourceErrorCode.INVALID_RESPONSE),
        (httpx.Response(200, json={"response": {"apps": "x"}}), SourceErrorCode.INVALID_RESPONSE),
        (
            httpx.Response(200, json={"response": {"apps": [{"name": "n"}]}}),
            SourceErrorCode.INVALID_RESPONSE,
        ),
        (
            httpx.Response(200, json={"response": {"apps": [{"appid": "7"}]}}),
            SourceErrorCode.INVALID_RESPONSE,
        ),
        (
            httpx.Response(200, json={"response": {"apps": [{"appid": True}]}}),
            SourceErrorCode.INVALID_RESPONSE,
        ),
    ],
)
def test_failures_are_clean_errors_that_never_contain_the_key(
    response: httpx.Response, code: SourceErrorCode
) -> None:
    adapter, _, _ = adapter_for(response)
    with pytest.raises(SourceError) as failed:
        adapter.fetch_page(SteamAppKind.GAMES)
    assert failed.value.code is code
    assert KEY not in failed.value.message and "://" not in failed.value.message
    assert failed.value.message.startswith("steam app list (games):")


def test_the_key_never_reaches_logs_even_when_requests_are_retried(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, requests, _ = adapter_for(httpx.Response(503), httpx.Response(429), page([app(1)]))
    with caplog.at_level(logging.DEBUG):
        adapter.fetch_page(SteamAppKind.GAMES)
    assert len(requests) == 3 and "retrying" in caplog.text
    assert KEY not in caplog.text and KEY not in repr(adapter.__dict__)


def test_an_exhausted_steam_quota_stops_before_sending() -> None:
    adapter, requests, ledger = adapter_for(page([app(1)]))
    today = datetime.now(UTC).date().isoformat()
    assert ledger.try_consume("steam_web_api", today, 90_000, 100_000)  # 90% of the daily quota
    with pytest.raises(SourceError) as refused:
        adapter.fetch_page(SteamAppKind.GAMES)
    assert refused.value.code is SourceErrorCode.QUOTA_EXHAUSTED and requests == []
