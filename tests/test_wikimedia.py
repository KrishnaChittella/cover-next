import json
from datetime import date

import httpx
import pytest

from metaspike.domain.games import Catalogue
from metaspike.domain.observations import ObservationStatus
from metaspike.domain.signals import CollectionStatus, SourceErrorCode
from metaspike.domain.timeseries import Granularity, TimeWindow
from metaspike.pipeline.collection import collect, window_ending
from metaspike.sources.base import SourceError
from metaspike.sources.registry import default_registry
from metaspike.sources.replay import replay_client
from metaspike.sources.wikimedia import (
    DAILY_PAGEVIEWS,
    SOURCE_ID,
    RetryConfig,
    WikimediaPageviewsAdapter,
    build_client,
)
from tests.conftest import (
    GAME,
    RECORDED_END,
    RECORDINGS,
    RUN_TIME,
    WIKI_IDENTIFIER,
    mock_client,
)

WINDOW = TimeWindow.of_days(date(2026, 10, 1), date(2026, 10, 3))


def item(timestamp: str, views: int) -> dict[str, object]:
    return {
        "project": "en.wikipedia",
        "article": "Grand_Theft_Auto_V",
        "granularity": "daily",
        "timestamp": timestamp,
        "access": "all-access",
        "agent": "user",
        "views": views,
    }


def ok(*items: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, json={"items": list(items)})


def fetch_with(
    *responses: httpx.Response | Exception,
) -> tuple[WikimediaPageviewsAdapter, list[httpx.Request], list[float]]:
    """An adapter that answers successive requests with the given responses."""
    queue = list(responses)
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    adapter = WikimediaPageviewsAdapter(mock_client(handler), sleep=sleeps.append)
    return adapter, requests, sleeps


def fetch_error(*responses: httpx.Response | Exception) -> SourceError:
    adapter, _, _ = fetch_with(*responses)
    with pytest.raises(SourceError) as caught:
        adapter.fetch(GAME, WIKI_IDENTIFIER, DAILY_PAGEVIEWS, WINDOW, RUN_TIME)
    return caught.value


def test_recorded_response_parses_into_typed_observations() -> None:
    window = window_ending(RECORDED_END)
    with replay_client(RECORDINGS) as client:
        observations = WikimediaPageviewsAdapter(client).fetch(
            GAME, WIKI_IDENTIFIER, DAILY_PAGEVIEWS, window, RUN_TIME
        )
    recorded = json.loads((RECORDINGS / "Grand_Theft_Auto_V.json").read_text())["items"]

    assert len(observations) == len(recorded) == 90
    assert [o.value for o in observations] == [float(i["views"]) for i in recorded]
    assert [o.period_start for o in observations] == sorted(o.period_start for o in observations)
    latest = observations[-1]
    assert latest.period_day == RECORDED_END
    assert latest.source == SOURCE_ID and latest.metric == "pageviews"
    assert latest.granularity is Granularity.DAILY
    assert latest.status is ObservationStatus.OBSERVED
    assert latest.canonical_game_id == GAME.canonical_game_id
    assert latest.source_timestamp is not None and latest.source_timestamp.date() == RECORDED_END
    assert latest.freshness.lag_hours == 6.0  # run at 06:00 UTC, the day closed at 00:00
    assert latest.reference == "en.wikipedia.org/Grand_Theft_Auto_V"


def test_request_uses_the_documented_endpoint_and_an_identifying_user_agent() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return ok(item("2026100300", 5))

    with build_client() as real:
        headers = dict(real.headers)
    client = httpx.Client(transport=httpx.MockTransport(handler), headers=headers)
    identifier = WIKI_IDENTIFIER.model_copy(update={"value": "No_Man's_Sky"})
    WikimediaPageviewsAdapter(client).fetch(GAME, identifier, DAILY_PAGEVIEWS, WINDOW, RUN_TIME)

    request = captured[0]
    assert request.url.host == "wikimedia.org"
    assert request.url.raw_path.decode() == (
        "/api/rest_v1/metrics/pageviews/per-article/en.wikipedia.org/all-access/user/"
        "No_Man%27s_Sky/daily/20261001/20261003"
    )
    assert request.headers["User-Agent"].startswith("MetaSpike/")
    assert "https://" in request.headers["User-Agent"]
    assert real.timeout.connect is not None and real.timeout.read is not None


def test_missing_article_is_an_error_not_zero_views_and_is_not_retried() -> None:
    adapter, requests, sleeps = fetch_with(httpx.Response(404, json={"status": 404}))
    with pytest.raises(SourceError) as caught:
        adapter.fetch(GAME, WIKI_IDENTIFIER, DAILY_PAGEVIEWS, WINDOW, RUN_TIME)
    assert caught.value.code is SourceErrorCode.NOT_FOUND
    assert len(requests) == 1 and sleeps == []


def test_transient_failure_is_retried_with_bounded_exponential_backoff() -> None:
    adapter, requests, sleeps = fetch_with(httpx.Response(503))
    with pytest.raises(SourceError) as caught:
        adapter.fetch(GAME, WIKI_IDENTIFIER, DAILY_PAGEVIEWS, WINDOW, RUN_TIME)
    assert caught.value.code is SourceErrorCode.HTTP_ERROR
    assert len(requests) == RetryConfig().max_attempts == 4
    assert sleeps == [0.5, 1.0, 2.0]


def test_backoff_never_exceeds_its_cap_even_when_the_server_asks_for_more() -> None:
    retry = RetryConfig()
    assert retry.delay(attempt=10) == retry.max_delay_seconds
    assert retry.delay(attempt=1, retry_after=3600) == retry.max_delay_seconds


def test_recovers_when_a_retry_succeeds() -> None:
    adapter, requests, sleeps = fetch_with(
        httpx.Response(429, headers={"Retry-After": "2"}),
        httpx.ConnectError("connection reset"),
        ok(item("2026100300", 42)),
    )
    observations = adapter.fetch(GAME, WIKI_IDENTIFIER, DAILY_PAGEVIEWS, WINDOW, RUN_TIME)
    assert [o.value for o in observations] == [42.0]
    assert len(requests) == 3 and sleeps == [2.0, 1.0]


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.ReadTimeout("slow"), SourceErrorCode.TIMEOUT),
        (httpx.ConnectError("down"), SourceErrorCode.NETWORK_ERROR),
        (httpx.Response(429), SourceErrorCode.RATE_LIMITED),
        (httpx.Response(403), SourceErrorCode.HTTP_ERROR),
        (httpx.Response(200, content=b"<html>not json</html>"), SourceErrorCode.INVALID_RESPONSE),
        (httpx.Response(200, json={"items": []}), SourceErrorCode.INVALID_RESPONSE),
        (httpx.Response(200, json={"unexpected": True}), SourceErrorCode.INVALID_RESPONSE),
        (ok(item("2026100300", -5)), SourceErrorCode.INVALID_RESPONSE),
        (ok(item("2026100300", 5) | {"views": "5"}), SourceErrorCode.INVALID_RESPONSE),
        (ok(item("20261003", 5)), SourceErrorCode.INVALID_RESPONSE),
        (ok(item("2026109900", 5)), SourceErrorCode.INVALID_RESPONSE),
        (ok(item("2026090100", 5)), SourceErrorCode.INVALID_RESPONSE),
        (ok(item("2026100300", 5), item("2026100300", 6)), SourceErrorCode.INVALID_RESPONSE),
        (ok(item("2026100300", 5) | {"granularity": "hourly"}), SourceErrorCode.INVALID_RESPONSE),
    ],
)
def test_failures_surface_as_clean_source_errors(
    response: httpx.Response | Exception, code: SourceErrorCode
) -> None:
    error = fetch_error(response)
    assert error.code is code
    assert "Traceback" not in error.message and "\n" not in error.message


def test_zero_views_reported_by_the_source_is_a_real_observation() -> None:
    adapter, _, _ = fetch_with(ok(item("2026100300", 0)))
    observations = adapter.fetch(GAME, WIKI_IDENTIFIER, DAILY_PAGEVIEWS, WINDOW, RUN_TIME)
    assert observations[0].value == 0.0
    assert observations[0].status is ObservationStatus.OBSERVED


def test_one_failed_game_does_not_stop_the_run_or_become_zero(catalogue: Catalogue) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "Star_Citizen" in request.url.path:
            return httpx.Response(404)
        return ok(item("2026100300", 100))

    client = mock_client(handler)
    report = collect(catalogue, default_registry(client), WINDOW, RUN_TIME, clock=lambda: RUN_TIME)
    (run,) = report.runs
    failed = run.with_status(CollectionStatus.FAILED)

    assert [catalogue.get(o.canonical_game_id).slug for o in failed] == ["star-citizen"]
    assert failed[0].series is None and failed[0].error_code is SourceErrorCode.NOT_FOUND
    assert len(run.with_status(CollectionStatus.COLLECTED)) == 13
    assert [catalogue.get(o.canonical_game_id).slug for o in run.outcomes if o.series is None] == [
        "fivem",
        "star-citizen",
    ]
