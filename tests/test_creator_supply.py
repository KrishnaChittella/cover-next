"""Offline YouTube creator supply (U6.1). Nothing here has ever been a YouTube response.

MetaSpike has never called YouTube. Every body in this file is SYNTHETIC: written in the
shape the YouTube Data API documents, with invented ids, titles, channels and counters.
No test opens a socket, reads a credential or touches a cloud resource.
"""

import json
import socket
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from metaspike import cli
from metaspike.catalogue.generate import generate_catalogue
from metaspike.creator_sources import synthetic as scenario_module
from metaspike.creator_sources.association import (
    ASSOCIATION_RULES_VERSION,
    AssociationState,
    GameNames,
    Reason,
    associate,
    gaming_classification,
    is_game_topic,
)
from metaspike.creator_sources.collect import MAX_PAGES_PER_QUERY, collect_creator_supply
from metaspike.creator_sources.reading import (
    CreatorSupplyReading,
    QueryCompleteness,
    SampledVideo,
    SupplyState,
    read_creator_supply,
)
from metaspike.creator_sources.store import CreatorSupplyStore
from metaspike.creator_sources.synthetic import (
    SYNTHETIC_ETAG,
    SyntheticCreatorSupply,
    _details,
    _search,
    _Video,
    _write,
    build_synthetic_creator_supply,
)
from metaspike.creator_sources.youtube import (
    API_HOST,
    MAX_IDS_PER_VIDEOS_CALL,
    OTHER_UNITS_PER_DAY,
    RETENTION_DAYS,
    SEARCH_CALL_COST,
    SEARCH_CALLS_PER_DAY,
    VIDEOS_LIST_COST,
    YOUTUBE_DATA_POLICY,
    FetchStatus,
    InvalidResponse,
    SearchQuery,
    VideoDetail,
    YouTubeAdapter,
    YouTubeQuota,
    api_error,
    parse_search,
    parse_videos,
    replay_youtube_client,
)
from metaspike.domain.families import SourceFamily
from metaspike.domain.signals import SourceErrorCode
from metaspike.domain.source_policy import DeploymentStage, Permission, SourcePurpose
from metaspike.evidence.assemble import (
    CREATOR_SUPPLY,
    EvidenceInputs,
    assess_evidence,
    publication_refusal,
)
from metaspike.evidence.model import (
    EvidenceKind,
    EvidenceReport,
    EvidenceState,
    InputProvenance,
    Origin,
    Standing,
)
from metaspike.evidence.provenance import local_store_provenance
from metaspike.evidence.render import render
from metaspike.publishing.artifacts import SCHEMA_VERSION
from metaspike.sources.credentials import Secret
from metaspike.sources.registry import SourcePolicyError
from metaspike.sources.wikimedia import WIKIMEDIA_PAGEVIEWS_POLICY
from tests.test_evidence import SYNTHETIC_AS_OF, World

REPO = Path(__file__).parent.parent
AS_OF = SYNTHETIC_AS_OF
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
YOUTUBE = "youtube_data"
GAME = "780b4850-6248-4a81-8fe0-7f0762376cfc"
OTHER = "9abc9607-45ea-4441-9da2-3eb00af9d4a3"
DECLARED = InputProvenance(origin=Origin.SYNTHETIC, reference=scenario_module.SCENARIO_NAME)
PRIVATE = DeploymentStage.PRIVATE_DEVELOPMENT


def item(video_id: str = "SYNvid00001", **snippet: object) -> dict[str, object]:
    base = {"publishedAt": "2026-10-19T08:00:00Z", "channelId": "UCsynthetic", "title": "A title"}
    return {"id": {"kind": "youtube#video", "videoId": video_id}, "snippet": {**base, **snippet}}


def search_body(items: list[object], **extra: object) -> dict[str, object]:
    return {"kind": "youtube#searchListResponse", "items": items, **extra}


def resource(video_id: str = "SYNvid00001", **parts: object) -> dict[str, object]:
    snippet = {"publishedAt": "2026-10-19T08:00:00Z", "channelId": "UCsynthetic", "title": "T"}
    return {"kind": "youtube#video", "id": video_id, "snippet": snippet, **parts}


def videos_body(items: list[object]) -> dict[str, object]:
    return {"kind": "youtube#videoListResponse", "items": items}


class Counting:
    """A replay directory behind a counter of the requests that actually went out."""

    def __init__(self, directory: Path) -> None:
        self.requests: list[httpx.Request] = []
        inner = replay_youtube_client(directory)

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return inner.send(request)

        self.client = httpx.Client(transport=httpx.MockTransport(handler))

    def adapter(
        self, quota: YouTubeQuota, moment: datetime = AS_OF, run: str = "run-1", **options: object
    ) -> YouTubeAdapter:
        return YouTubeAdapter(self.client, quota, run_id=run, now=lambda: moment, **options)  # type: ignore[arg-type]


def ask(text: str, game: str = GAME, **options: object) -> SearchQuery:
    return SearchQuery(canonical_game_id=game, text=text, max_results=5, **options)  # type: ignore[arg-type,unused-ignore]


# --- the documented shapes ------------------------------------------------------------------


def test_a_search_response_is_read_field_by_field_and_nothing_is_guessed() -> None:
    retrieved = datetime(2026, 10, 20, 12, tzinfo=UTC)
    body = search_body(
        [
            item("SYNvid00001", title="Kept"),
            {"id": {"kind": "youtube#channel", "channelId": "UCx"}, "snippet": {}},
            {"id": {"kind": "youtube#video"}, "snippet": item()["snippet"]},  # no videoId
            item("SYNvid00002", publishedAt="yesterday"),
            item("SYNvid00003", publishedAt="2026-10-19T08:00:00"),  # no offset
            item("SYNvid00004", publishedAt="2026-10-20T14:00:00Z"),  # after it was retrieved
            item("SYNvid00005", title="   "),
            item("SYNvid00006", channelId=None),
            "not an object",
        ],
        etag="an-etag",
        nextPageToken="NEXT",
        pageInfo={"totalResults": 1_000_000, "resultsPerPage": 5},
    )
    parsed = parse_search(body, retrieved)
    (kept,) = parsed.results
    assert (kept.video_id, kept.channel_id, kept.title) == ("SYNvid00001", "UCsynthetic", "Kept")
    assert kept.published_at == datetime(2026, 10, 19, 8, tzinfo=UTC)
    assert parsed.rejected == 8  # counted, never repaired
    assert parsed.next_page_token == "NEXT" and parsed.etag == "an-etag"
    # The source's approximation is kept as what the source said, under that name.
    assert parsed.reported_total == 1_000_000
    assert parse_search(search_body([]), retrieved).next_page_token is None
    assert (
        parse_search(search_body([], pageInfo={"totalResults": True}), retrieved).reported_total
        is None
    )
    wrongs: list[object] = [None, [], {"items": []}, videos_body([])]
    for wrong in wrongs:
        with pytest.raises(InvalidResponse):
            parse_search(wrong, retrieved)
    with pytest.raises(InvalidResponse):
        parse_search({"kind": "youtube#searchListResponse", "items": {}}, retrieved)
    # A search result carries no category, tag or counter, and none is invented for it.
    assert set(type(kept).model_fields) == {"video_id", "channel_id", "title", "published_at"}


def test_video_details_keep_a_missing_statistic_missing() -> None:
    retrieved = datetime(2026, 10, 20, 12, tzinfo=UTC)
    whole = resource(
        "SYNvid00001",
        statistics={"viewCount": "1234", "likeCount": "0", "commentCount": "7"},
        topicDetails={"topicCategories": ["https://en.wikipedia.org/wiki/Action_game"]},
    )
    whole["snippet"] = {**whole["snippet"], "categoryId": "20", "tags": ["rust", "", 5]}  # type: ignore[dict-item]
    parsed = parse_videos(
        videos_body(
            [
                whole,
                resource("SYNvid00002"),  # no statistics part at all
                resource("SYNvid00003", statistics={"viewCount": "55"}),  # likes hidden
                resource("SYNvid00004", statistics={"viewCount": "many"}),
                resource("SYNvid00005", statistics={"viewCount": -1}),
                resource("SYNvid00006", statistics=[]),
                {"kind": "youtube#channel", "id": "UCx", "snippet": {}},
            ]
        ),
        retrieved,
    )
    first, absent, partial = parsed.details
    assert (first.view_count, first.like_count, first.comment_count) == (1234, 0, 7)
    assert first.category_id == "20" and first.tags == ("rust",)
    assert first.topic_categories == ("https://en.wikipedia.org/wiki/Action_game",)
    # Absent is None. A reported zero is 0. They are never the same thing.
    assert (absent.view_count, absent.like_count, absent.comment_count) == (None, None, None)
    assert absent.category_id is None and absent.tags == () and absent.topic_categories == ()
    assert (partial.view_count, partial.like_count) == (55, None)
    assert first.like_count == 0 and first.like_count is not None
    assert parsed.rejected == 4
    with pytest.raises(InvalidResponse):
        parse_videos(search_body([]), retrieved)
    # Only what coverage needs is read: no description, thumbnail, comment or transcript.
    assert set(VideoDetail.model_fields) == {
        "video_id",
        "channel_id",
        "title",
        "published_at",
        "category_id",
        "tags",
        "topic_categories",
        "view_count",
        "like_count",
        "comment_count",
    }


def test_a_query_sends_only_documented_parameters_and_never_a_key() -> None:
    query = SearchQuery(
        canonical_game_id=GAME,
        text="Rust",
        max_results=50,
        published_after=datetime(2026, 10, 13, 12, tzinfo=UTC),
        video_category_id="20",
    )
    assert query.parameters(None) == {
        "part": "snippet",
        "type": "video",
        "q": "Rust",
        "maxResults": 50,
        "order": "date",
        "publishedAfter": "2026-10-13T12:00:00Z",
        "videoCategoryId": "20",
    }
    assert query.parameters("TOKEN")["pageToken"] == "TOKEN"
    assert "key" not in query.parameters("TOKEN")
    # The identity of a question does not depend on the page, and does on everything else.
    assert query.query_id == query.model_copy().query_id and query.query_id.startswith("ytq_")
    different = [
        query.model_copy(update={"text": "rust"}),
        query.model_copy(update={"max_results": 25}),
        query.model_copy(update={"order": "relevance"}),
        query.model_copy(update={"canonical_game_id": OTHER}),
        query.model_copy(update={"published_after": None}),
    ]
    assert len({query.query_id, *(q.query_id for q in different)}) == 6
    for size in (0, 51):  # the documented range is 0-50; nothing is asked for with 0
        with pytest.raises(ValueError):
            SearchQuery(canonical_game_id=GAME, text="Rust", max_results=size)
    with pytest.raises(ValueError):
        SearchQuery(canonical_game_id=GAME, text="  ")


def test_documented_errors_are_named_and_only_a_quota_error_is_a_quota_error() -> None:
    def error(reason: str) -> dict[str, object]:
        return {"error": {"code": 403, "errors": [{"reason": reason}]}}

    assert api_error(403, error("quotaExceeded")) == (
        SourceErrorCode.QUOTA_EXHAUSTED,
        "quotaExceeded",
    )
    assert api_error(403, error("dailyLimitExceeded"))[0] is SourceErrorCode.QUOTA_EXHAUSTED
    assert api_error(403, error("rateLimitExceeded"))[0] is SourceErrorCode.RATE_LIMITED
    assert api_error(429, None)[0] is SourceErrorCode.RATE_LIMITED
    assert api_error(403, error("forbidden")) == (SourceErrorCode.UNAUTHORIZED, "forbidden")
    assert api_error(401, {"error": "text"}) == (SourceErrorCode.UNAUTHORIZED, None)
    assert api_error(400, error("invalidSearchFilter")) == (
        SourceErrorCode.HTTP_ERROR,
        "invalidSearchFilter",
    )
    assert api_error(404, error("videoNotFound"))[0] is SourceErrorCode.NOT_FOUND
    assert api_error(500, "garbage") == (SourceErrorCode.HTTP_ERROR, None)


# --- quota ----------------------------------------------------------------------------------


def test_the_quota_model_is_the_documented_one(tmp_path: Path) -> None:
    """search.list: its own bucket, 1 call each, 100 a day. videos.list: 1 unit of 10,000."""
    assert (SEARCH_CALLS_PER_DAY, SEARCH_CALL_COST) == (100, 1)
    assert (OTHER_UNITS_PER_DAY, VIDEOS_LIST_COST) == (10_000, 1)
    budget = YOUTUBE_DATA_POLICY.operations.quota
    assert budget is not None and (budget.limit, budget.unit) == (100, "search.list calls")
    default = YouTubeQuota()
    assert (default.search_calls_left, default.other_units_left) == (100, 10_000)
    # A limit may be set lower and never higher than what is documented.
    for too_many in ({"search_calls": 101}, {"other_units": 10_001}, {"search_calls": -1}):
        with pytest.raises(ValueError):
            YouTubeQuota(**too_many)

    videos = [_Video(f"SYNpage{n:04d}", "UCa", f"Rust clip {n}", AS_OF - DAY) for n in range(6)]
    _search(tmp_path, "one", "Rust", videos[:2], next_token="P2")
    _search(tmp_path, "two", "Rust", videos[2:4], token="P2", next_token="P3")
    _search(tmp_path, "three", "Rust", videos[4:], token="P3")
    _details(tmp_path, "details", videos)
    replay = Counting(tmp_path)
    quota = YouTubeQuota(search_calls=10, other_units=10)
    store = CreatorSupplyStore(tmp_path / "store")
    summary = collect_creator_supply(
        replay.adapter(quota), YOUTUBE_DATA_POLICY, [ask("Rust")], store, stage=PRIVATE, max_pages=3
    )
    # Three pages are three search calls. Six details in one videos.list call: one unit.
    assert (summary.search_calls_spent, summary.other_units_spent) == (3, 1)
    assert (quota.search_calls_used, quota.other_units_used) == (3, 1)
    assert (summary.search_calls_left, summary.other_units_left) == (7, 9)
    assert [r.url.path.rsplit("/", 1)[-1] for r in replay.requests] == ["search"] * 3 + ["videos"]
    assert [f.search_calls_spent for f in store.searches()] == [1, 1, 1]
    assert [r.url.params.get("pageToken") for r in replay.requests[:3]] == [None, "P2", "P3"]
    # The buckets are separate: a search never draws on units, nor details on calls.
    assert store.videos()[0].units_spent == 1 and summary.video_details_retrieved == 6


def test_an_exhausted_account_sends_nothing_and_says_so(tmp_path: Path) -> None:
    videos = [_Video("SYNq0000001", "UCa", "Rust one", AS_OF - DAY)]
    _search(tmp_path, "a", "Rust", videos, next_token="P2")
    _search(tmp_path, "b", "Rust", videos, token="P2")
    _search(tmp_path, "c", "Dota 2", [])
    _details(tmp_path, "d", videos)
    replay = Counting(tmp_path)
    quota = YouTubeQuota(search_calls=1, other_units=0)
    store = CreatorSupplyStore(tmp_path / "store")
    summary = collect_creator_supply(
        replay.adapter(quota),
        YOUTUBE_DATA_POLICY,
        [ask("Rust"), ask("Dota 2", OTHER)],
        store,
        stage=PRIVATE,
        max_pages=2,
    )
    # One search call was affordable. The second page, the second query and the details
    # were never sent, and each is on record as not sent.
    assert len(replay.requests) == 1
    assert summary.search_requests_sent == 1 and summary.search_requests_not_sent == 2
    assert summary.videos_requests_sent == 0 and summary.videos_requests_not_sent == 1
    first, second, other = store.searches()
    assert first.status is FetchStatus.OK and first.search_calls_spent == 1
    for refused in (second, other):
        assert refused.status is FetchStatus.NOT_SENT and refused.search_calls_spent == 0
        assert refused.error_code is SourceErrorCode.QUOTA_EXHAUSTED and refused.http_status is None
        assert refused.results == () and refused.etag is None
    (details,) = store.videos()
    assert details.status is FetchStatus.NOT_SENT and details.units_spent == 0
    assert (quota.search_calls_left, quota.other_units_left) == (0, 0)
    names = [GameNames.of(GAME, "Rust"), GameNames.of(OTHER, "Dota 2")]
    reading = read_creator_supply(store, names[0], names, [], AS_OF)
    assert reading.state is SupplyState.INCOMPLETE and not reading.sample_complete
    assert reading.queries[0].completeness is QueryCompleteness.QUOTA_LIMITED
    # Never asked is not "no videos": the second game is incomplete, not empty.
    unasked = read_creator_supply(store, names[1], names, [], AS_OF)
    assert unasked.state is SupplyState.INCOMPLETE and unasked.candidate_videos == 0
    assert "not evidence of low competition" not in unasked.summary


def test_requests_are_paid_for_whatever_comes_back(tmp_path: Path) -> None:
    """A failed or refused request still costs; a quota error closes only its own bucket."""
    quota_error = {"error": {"code": 403, "errors": [{"reason": "quotaExceeded"}]}}
    _write(tmp_path, "quota", {"method": "search", "q": "Rust"}, 403, quota_error)
    _write(tmp_path, "broken", {"method": "search", "q": "Dota 2"}, 200, {"kind": "nonsense"})
    _write(tmp_path, "server", {"method": "search", "q": "Elden Ring"}, 500, "oops")
    video = _Video("SYNq0000002", "UCa", "T", AS_OF - DAY)
    _details(tmp_path, "details", [video])
    replay = Counting(tmp_path)
    quota = YouTubeQuota(search_calls=10, other_units=10)
    adapter = replay.adapter(quota)

    broken = adapter.search(ask("Dota 2"), 0, None)
    assert (broken.status, broken.error_code) == (
        FetchStatus.FAILED,
        SourceErrorCode.INVALID_RESPONSE,
    )
    server = adapter.search(ask("Elden Ring"), 0, None)
    assert (server.error_code, server.http_status) == (SourceErrorCode.HTTP_ERROR, 500)
    unrecorded = adapter.search(ask("Something never recorded"), 0, None)
    # An unrecorded question is a failed fetch, never an invented empty answer.
    assert (unrecorded.status, unrecorded.error_code) == (
        FetchStatus.FAILED,
        SourceErrorCode.NETWORK_ERROR,
    )
    assert unrecorded.results == () and unrecorded.http_status is None
    assert [f.search_calls_spent for f in (broken, server, unrecorded)] == [1, 1, 1]
    assert quota.search_calls_used == 3

    refused = adapter.search(ask("Rust"), 0, None)
    assert refused.error_code is SourceErrorCode.QUOTA_EXHAUSTED
    assert (refused.api_error_reason, refused.http_status) == ("quotaExceeded", 403)
    assert refused.search_calls_spent == 1 and quota.search_calls_used == 4
    # The source said the search bucket is spent: nothing more is sent from it.
    sent = len(replay.requests)
    after = adapter.search(ask("Dota 2"), 0, None)
    assert after.status is FetchStatus.NOT_SENT and len(replay.requests) == sent
    assert quota.search_calls_left == 0 and quota.search_calls_used == 4
    # The other bucket is a different allowance and still answers.
    details = adapter.videos([video.video_id])
    assert details.status is FetchStatus.OK and quota.other_units_used == 1


def test_video_details_are_asked_for_in_bounded_batches(tmp_path: Path) -> None:
    videos = [_Video(f"SYNb{n:07d}", "UCa", f"Rust {n}", AS_OF - DAY) for n in range(60)]
    for page in range(2):
        chunk = videos[page * 50 : page * 50 + 50]
        token = "" if page == 0 else "P2"
        _search(tmp_path, f"s{page}", "Rust", chunk, token=token, next_token=None if page else "P2")
    _details(tmp_path, "d0", videos[:50])
    gone = videos[59]
    _details(
        tmp_path,
        "d1",
        [
            v if v is not gone else _Video(v.video_id, "UCa", "x", AS_OF - DAY, in_details=False)
            for v in videos[50:]
        ],
    )
    replay = Counting(tmp_path)
    store = CreatorSupplyStore(tmp_path / "store")
    query = SearchQuery(canonical_game_id=GAME, text="Rust", max_results=50)
    summary = collect_creator_supply(
        replay.adapter(YouTubeQuota()),
        YOUTUBE_DATA_POLICY,
        [query],
        store,
        stage=PRIVATE,
        max_pages=2,
    )
    assert MAX_IDS_PER_VIDEOS_CALL == 50
    assert (summary.candidate_videos, summary.videos_requests_sent, summary.other_units_spent) == (
        60,
        2,
        2,
    )
    # Both calls carry the same retrieval time, so their order in the store is not meaningful.
    first, second = sorted(store.videos(), key=lambda fetch: -len(fetch.requested_ids))
    assert (len(first.requested_ids), len(second.requested_ids)) == (50, 10)
    # A video asked for and not returned is recorded as missing, not as empty details.
    assert second.missing_ids == (gone.video_id,) and summary.video_details_retrieved == 59
    with pytest.raises(ValueError):
        replay.adapter(YouTubeQuota()).videos([v.video_id for v in videos[:51]])
    with pytest.raises(ValueError):
        replay.adapter(YouTubeQuota()).videos([])


# --- the adapter and the replay -------------------------------------------------------------


def test_a_key_never_reaches_a_record_and_the_replay_never_reaches_a_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_socket(*_: object, **__: object) -> None:
        raise AssertionError("a creator-supply test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", no_socket)
    video = _Video("SYNk0000001", "UCa", "Rust", AS_OF - DAY)
    _search(tmp_path / "anything", "a-name-that-says-nothing", "Rust", [video])
    _details(tmp_path / "anything", "another-name", [video])
    replay = Counting(tmp_path / "anything")
    store = CreatorSupplyStore(tmp_path / "store")
    secret = "k-SECRET-not-a-real-key"
    adapter = replay.adapter(YouTubeQuota(), api_key=Secret(secret))
    collect_creator_supply(adapter, YOUTUBE_DATA_POLICY, [ask("Rust")], store, stage=PRIVATE)
    # A key, if there ever is one, travels as the documented parameter and goes nowhere else.
    assert all(request.url.params["key"] == secret for request in replay.requests)
    stored = "".join(p.read_text() for p in (tmp_path / "store").rglob("*.ndjson"))
    assert stored and secret not in stored and "key" not in store.searches()[0].model_dump_json()
    assert secret not in repr(adapter.__dict__)
    assert all(request.url.host == API_HOST for request in replay.requests)
    # Without a key no `key` parameter is sent at all.
    keyless = Counting(tmp_path / "anything")
    keyless.adapter(YouTubeQuota()).search(ask("Rust"), 0, None)
    assert "key" not in keyless.requests[0].url.params
    # The replay answers only the documented host, and only what was recorded.
    with replay_youtube_client(tmp_path / "anything") as client:
        with pytest.raises(httpx.ConnectError, match="unexpected host"):
            client.get("https://example.invalid/youtube/v3/search", params={"q": "Rust"})
        with pytest.raises(httpx.ConnectError, match="no recording"):
            client.get(f"https://{API_HOST}/youtube/v3/search", params={"q": "Tetris"})
    # Two files answering the same request is a mistake, not a choice.
    _search(tmp_path / "anything", "a-second-answer", "Rust", [])
    with pytest.raises(ValueError, match="second recording answers the same request"):
        replay_youtube_client(tmp_path / "anything")
    # Nothing in the package reads the environment or builds a live client.
    package = REPO / "src" / "metaspike" / "creator_sources"
    source = "\n".join(path.read_text() for path in sorted(package.glob("*.py")))
    for forbidden in (
        "os.environ",
        "getenv",
        "build_client",
        "oauth",
        "OAuth",
        "youtube.com/watch",
    ):
        assert forbidden not in source, forbidden
    assert "import os" not in source and "subprocess" not in source


def test_the_collector_is_bounded_and_refuses_before_it_sends(tmp_path: Path) -> None:
    video = _Video("SYNc0000001", "UCa", "Rust", AS_OF - DAY)
    _search(tmp_path, "a", "Rust", [video], next_token="P2")
    _write(tmp_path, "b", {"method": "search", "q": "Rust", "pageToken": "P2"}, 500, "oops")
    _details(tmp_path, "d", [video])
    store = CreatorSupplyStore(tmp_path / "store")
    # Every stage but private development is refused, and nothing is sent or stored.
    for stage in DeploymentStage:
        if stage is PRIVATE:
            continue
        replay = Counting(tmp_path)
        with pytest.raises(SourcePolicyError, match="youtube_data"):
            collect_creator_supply(
                replay.adapter(YouTubeQuota()),
                YOUTUBE_DATA_POLICY,
                [ask("Rust")],
                store,
                stage=stage,
            )
        assert replay.requests == [] and store.searches() == ()
    replay = Counting(tmp_path)
    with pytest.raises(SourcePolicyError, match="policy is for 'wikimedia_pageviews'"):
        collect_creator_supply(
            replay.adapter(YouTubeQuota()),
            WIKIMEDIA_PAGEVIEWS_POLICY,
            [ask("Rust")],
            store,
            stage=PRIVATE,
        )
    for pages in (0, MAX_PAGES_PER_QUERY + 1):
        with pytest.raises(ValueError, match="max_pages"):
            collect_creator_supply(
                replay.adapter(YouTubeQuota()),
                YOUTUBE_DATA_POLICY,
                [ask("Rust")],
                store,
                stage=PRIVATE,
                max_pages=pages,
            )
    with pytest.raises(ValueError, match="more than once"):
        collect_creator_supply(
            replay.adapter(YouTubeQuota()),
            YOUTUBE_DATA_POLICY,
            [ask("Rust"), ask("Rust")],
            store,
            stage=PRIVATE,
        )
    assert replay.requests == [] and store.searches() == ()

    # One page allowed and more exist: the sample says it stopped at its limit.
    collect_creator_supply(
        replay.adapter(YouTubeQuota(), run="one-page"),
        YOUTUBE_DATA_POLICY,
        [ask("Rust")],
        store,
        stage=PRIVATE,
    )
    (limited,) = store.searches()
    assert limited.stopped_by == "page_limit" and limited.next_page_token == "P2"
    names = [GameNames.of(GAME, "Rust")]
    reading = read_creator_supply(store, names[0], names, [], AS_OF)
    assert reading.queries[0].completeness is QueryCompleteness.PAGE_LIMITED
    assert reading.state is SupplyState.INCOMPLETE and reading.supported_videos == 1
    # A later run whose second page fails is a failed sample, with what it did get kept.
    later = AS_OF + HOUR
    collect_creator_supply(
        replay.adapter(YouTubeQuota(), later, "two-pages"),
        YOUTUBE_DATA_POLICY,
        [ask("Rust")],
        store,
        stage=PRIVATE,
        max_pages=2,
    )
    failed = read_creator_supply(store, names[0], names, [], later)
    (sample,) = failed.queries
    assert sample.completeness is QueryCompleteness.FAILED and sample.pages_fetched == 1
    assert sample.error_code is SourceErrorCode.HTTP_ERROR and failed.candidate_videos == 1
    assert failed.state is SupplyState.INCOMPLETE and sample.run_id == "two-pages"


def test_the_store_is_append_only_and_knows_what_has_passed_its_retention(tmp_path: Path) -> None:
    video = _Video("SYNs0000001", "UCa", "Rust", AS_OF - DAY)
    _search(tmp_path, "a", "Rust", [video])
    _details(tmp_path, "d", [video])
    store = CreatorSupplyStore(tmp_path / "store")
    replay = Counting(tmp_path)
    collect_creator_supply(
        replay.adapter(YouTubeQuota()), YOUTUBE_DATA_POLICY, [ask("Rust")], store, stage=PRIVATE
    )
    files = {p: p.read_bytes() for p in (tmp_path / "store").rglob("*.ndjson")}
    assert len(files) == 2
    # The same fetches again add nothing and rewrite nothing.
    assert store.add_searches(store.searches()) == 0 and store.add_videos(store.videos()) == 0
    assert {p: p.read_bytes() for p in (tmp_path / "store").rglob("*.ndjson")} == files
    # Only what had been retrieved by a moment is returned for it.
    assert store.searches(AS_OF - timedelta(seconds=1)) == ()
    assert store.videos(AS_OF - timedelta(seconds=1)) == ()
    assert len(store.searches(AS_OF)) == 1 and len(store.videos(AS_OF)) == 1
    # YouTube's limit for stored, non-authorised API data is 30 calendar days.
    assert RETENTION_DAYS == 30 == YOUTUBE_DATA_POLICY.retention_days
    assert store.expired(AS_OF + 30 * DAY) == ()
    expired = store.expired(AS_OF + 30 * DAY + timedelta(seconds=1))
    assert len(expired) == 2 and all(fetch_id.startswith("ytf_") for fetch_id in expired)
    names = [GameNames.of(GAME, "Rust")]
    assert (
        read_creator_supply(store, names[0], names, [], AS_OF + 30 * DAY).state
        is SupplyState.OBSERVED
    )
    past = read_creator_supply(store, names[0], names, [], AS_OF + 31 * DAY)
    assert past.state is SupplyState.EXPIRED and past.expired_searches == 1
    assert past.candidate_videos == 0 and past.videos == () and past.queries == ()
    (tmp_path / "store" / "searches" / "2026-10-20" / "torn.ndjson").write_text("{not json\n")
    with pytest.raises(Exception, match="stored searches are unreadable"):
        store.searches()


# --- which game is a video about ------------------------------------------------------------

RUST = GameNames.of(GAME, "Rust")
DOTA = GameNames.of(OTHER, "Dota 2", aliases=("DotA II",))
CONSOLE = GameNames.of("bdea9df5-8fdd-4bcb-8acb-9e9fe2a2d8d2", "Rust Console Edition")
CATALOGUE = [RUST, DOTA, CONSOLE]


def detail(title: str, category: str | None = "20", **fields: object) -> VideoDetail:
    return VideoDetail(
        video_id="SYNa0000001",
        channel_id="UCa",
        title=title,
        published_at=AS_OF - DAY,
        category_id=category,
        **fields,  # type: ignore[arg-type,unused-ignore]
    )


def verdict(
    title: str, game: GameNames = RUST, related: tuple[str, ...] = (), **fields: object
) -> tuple[AssociationState, tuple[Reason, ...]]:
    result = associate(game, CATALOGUE, related, title, detail(title, **fields))  # type: ignore[arg-type]
    return result.state, result.reasons


def test_a_title_match_alone_never_supports_an_association() -> None:
    supported = (AssociationState.SUPPORTED, (Reason.NAME_AND_GAMING_CLASSIFICATION,))
    assert ASSOCIATION_RULES_VERSION == "creator_association_rules_v1"
    # Supported: the name as whole words AND YouTube's own gaming classification.
    assert verdict("Rust wipe day solo base") == supported
    assert verdict("RUST: Wipe Day!") == supported  # case and punctuation are not evidence
    game_topic = "https://en.wikipedia.org/wiki/Action-adventure_game"
    assert verdict("Rust first night", category=None, topic_categories=(game_topic,)) == supported
    assert verdict("Rust review", category="24", topic_categories=(game_topic,)) == supported
    # The name as an exact tag supports it where the title does not name the game.
    assert verdict("Wipe day solo base", tags=("Rust",)) == supported
    assert verdict("Wipe day solo base", tags=("rust gameplay",))[0] is AssociationState.UNCERTAIN

    # Rust the language, rust on tools, a film: YouTube classifies them as not gaming.
    excluded = (AssociationState.EXCLUDED, (Reason.CLASSIFIED_NOT_GAMING,))
    tech = "https://en.wikipedia.org/wiki/Technology"
    assert verdict("Rust programming tutorial", category="28", topic_categories=(tech,)) == excluded
    assert verdict("How to remove rust from old tools", category="26") == excluded
    assert verdict("Rust (2010) full movie review", category="1") == excluded
    # The same title with no details at all proves nothing either way.
    bare = associate(RUST, CATALOGUE, (), "Rust programming tutorial", None)
    assert (bare.state, bare.reasons) == (AssociationState.UNCERTAIN, (Reason.NO_VIDEO_DETAILS,))

    # Part of a longer catalogue name: the title names a different game.
    longer = (AssociationState.EXCLUDED, (Reason.PART_OF_A_LONGER_GAME_NAME,))
    assert verdict("Rust Console Edition wipe day") == longer
    assert verdict("Rust Console Edition wipe day", game=CONSOLE)[0] is AssociationState.SUPPORTED
    # Both named separately: several games, so neither is supported.
    both = verdict("Rust vs Rust Console Edition: which to buy")
    assert both == (AssociationState.UNCERTAIN, (Reason.SEVERAL_GAMES_NAMED,))
    assert verdict("Rust and Dota 2 funny moments")[1] == (Reason.SEVERAL_GAMES_NAMED,)
    assert verdict("Rust and DotA II funny moments")[1] == (Reason.SEVERAL_GAMES_NAMED,)  # an alias
    assert verdict("Rust and Dota 2 funny moments", game=DOTA)[0] is AssociationState.UNCERTAIN

    # Whole words only: a name inside another word is not the name.
    for title in ("Trust issues in gaming", "Rusty Lake walkthrough", "Best survival games"):
        assert verdict(title) == (AssociationState.UNCERTAIN, (Reason.NAME_NOT_IN_TITLE_OR_TAGS,))
    # A sequel's numeral is a different word: "Dota" is not "Dota 2", "2" is not "II".
    assert verdict("Dota funny moments", game=DOTA)[1] == (Reason.NAME_NOT_IN_TITLE_OR_TAGS,)
    assert verdict("Dota 20 funny moments", game=DOTA)[1] == (Reason.NAME_NOT_IN_TITLE_OR_TAGS,)
    # A DLC or an edition of the game is named: not counted as the game itself.
    related = ("Rust: Voice Props Pack",)
    assert verdict("Rust Voice Props Pack showcase", related=related) == (
        AssociationState.UNCERTAIN,
        (Reason.RELATED_PRODUCT_NAMED,),
    )
    # No classification from YouTube at all: uncertain, never assumed to be gaming.
    assert verdict("Rust lore explained", category=None) == (
        AssociationState.UNCERTAIN,
        (Reason.NO_STRUCTURED_CLASSIFICATION,),
    )
    # Doubts accumulate and are all reported.
    several = associate(RUST, CATALOGUE, related, "Dota 2 and Rust Voice Props Pack", None)
    assert several.state is AssociationState.UNCERTAIN
    assert set(several.reasons) == {
        Reason.NO_VIDEO_DETAILS,
        Reason.SEVERAL_GAMES_NAMED,
        Reason.RELATED_PRODUCT_NAMED,
    }
    # Topic categories: a video game topic, and nothing that merely contains "game".
    assert is_game_topic("https://en.wikipedia.org/wiki/Role-playing_video_game")
    assert is_game_topic("https://en.wikipedia.org/wiki/Action_game")
    assert is_game_topic("https://en.wikipedia.org/wiki/Video_game_culture")
    assert not is_game_topic("https://en.wikipedia.org/wiki/Game_show")
    assert not is_game_topic("https://en.wikipedia.org/wiki/Technology")
    assert gaming_classification(detail("x", category=None)) is None
    assert gaming_classification(detail("x", category="28")) is False
    # There is no number in a verdict: no probability, no score, no weight.
    assert not any(
        isinstance(value, float)
        for value in vars(associate(RUST, CATALOGUE, (), "Rust", None)).values()
    )


def test_association_uses_the_catalogue_and_changes_nothing_in_it(world: World) -> None:
    """Names come from the catalogue state as it stands. No game is merged, made or renamed."""
    before = world.state.model_dump_json()
    names = [
        GameNames.of(g.canonical_game_id, g.name, g.aliases) for g in world.state.catalogue.games
    ]
    by_id = {entry.canonical_game_id: entry for entry in names}
    assert len(names) == 234
    checked = 0
    for shorter in names:
        for longer in names:
            contained = any(
                len(a) < len(b) and any(b[i : i + len(a)] == a for i in range(len(b) - len(a) + 1))
                for a in shorter.names
                for b in longer.names
            )
            if shorter is longer or not contained:
                continue
            title = world.state.catalogue.get(longer.canonical_game_id).name
            result = associate(shorter, names, (), f"{title} gameplay", detail(f"{title} gameplay"))
            # A title naming the longer-named game never supports the shorter one.
            assert result.state is not AssociationState.SUPPORTED, (shorter, longer)
            checked += 1
    assert checked > 0
    rust = by_id[world.ids["rust"]]
    assert associate(rust, names, (), "Rust wipe day", detail("Rust wipe day")).state is (
        AssociationState.SUPPORTED
    )
    assert world.state.model_dump_json() == before
    assembled = (REPO / "src/metaspike/creator_sources/association.py").read_text()
    assert "resolver" not in assembled.replace("frozen\nresolver takes no part", "")
    assert "import" in assembled and "metaspike.resolution" not in assembled


# --- the scenario, read at several moments --------------------------------------------------


@pytest.fixture(scope="module")
def world() -> World:
    return World()


@pytest.fixture(scope="module")
def supply(world: World, tmp_path_factory: pytest.TempPathFactory) -> SyntheticCreatorSupply:
    games = {g.slug: (g.canonical_game_id, g.name) for g in world.state.catalogue.games}
    return build_synthetic_creator_supply(tmp_path_factory.mktemp("creator-supply"), games, AS_OF)


Read = Callable[..., CreatorSupplyReading]


@pytest.fixture(scope="module")
def read(world: World, supply: SyntheticCreatorSupply) -> Read:
    names = [
        GameNames.of(g.canonical_game_id, g.name, g.aliases) for g in world.state.catalogue.games
    ]
    by_id = {entry.canonical_game_id: entry for entry in names}

    def reading(slug: str, moment: datetime = AS_OF) -> CreatorSupplyReading:
        return read_creator_supply(supply.store, by_id[world.ids[slug]], names, [], moment)

    return reading


def test_a_sample_is_described_and_never_called_the_market(read: Read) -> None:
    """A-L of the demonstration, on the synthetic scenario."""
    rust = read("rust")
    # D. The first query stopped at its page limit; the second ran to its end.
    assert rust.state is SupplyState.INCOMPLETE and not rust.sample_complete
    first, second = rust.queries
    assert (first.text, first.completeness, first.pages_fetched, first.videos_returned) == (
        "Rust",
        QueryCompleteness.PAGE_LIMITED,
        2,
        8,
    )
    assert (second.text, second.completeness, second.pages_fetched) == (
        "Rust gameplay",
        QueryCompleteness.COMPLETE,
        1,
    )
    # H. Two retrieval times, each kept.
    assert (first.retrieved_at, second.retrieved_at) == (AS_OF - 2 * HOUR, AS_OF - HOUR)
    assert (rust.earliest_retrieved_at, rust.latest_retrieved_at) == (
        AS_OF - 2 * HOUR,
        AS_OF - HOUR,
    )
    # A, B, K. Nine distinct videos: four supported, three uncertain, two excluded.
    assert (
        rust.candidate_videos,
        rust.supported_videos,
        rust.uncertain_videos,
        rust.excluded_videos,
    ) == (9, 4, 3, 2)
    by_id = {video.video_id: video for video in rust.videos}
    states = {
        video_id: (video.association.value, video.reasons) for video_id, video in by_id.items()
    }
    assert states == {
        "SYNrust0001": ("supported", ("name_and_gaming_classification",)),
        "SYNrust0002": ("supported", ("name_and_gaming_classification",)),
        "SYNrust0003": ("excluded", ("classified_not_gaming",)),  # the language
        "SYNrust0004": ("excluded", ("classified_not_gaming",)),  # rust on tools
        "SYNrust0005": ("uncertain", ("several_games_named",)),
        "SYNrust0006": ("uncertain", ("name_not_in_title_or_tags",)),
        "SYNrust0007": ("supported", ("name_and_gaming_classification",)),
        "SYNrust0008": ("uncertain", ("no_video_details",)),
        "SYNrust0009": ("supported", ("name_and_gaming_classification",)),
    }
    # F. One video came back from both queries and is counted once.
    assert rust.duplicate_results == 1 and by_id["SYNrust0001"].returned_by_queries == 2
    assert sum(sample.videos_returned for sample in rust.queries) == 10
    # G. Four supported videos from three channels; one channel made two of them.
    assert (rust.supported_channels, rust.most_videos_from_one_channel) == (3, 2)
    assert len({v.channel_id for v in rust.videos if v.association.value == "supported"}) == 3
    # Recency, of supported videos only, measured from the moment read at.
    assert (rust.supported_published_last_24h, rust.supported_published_last_7d) == (2, 3)
    assert rust.newest_supported_published_at == AS_OF - 5 * HOUR
    # J. A counter the response did not carry is absent. It is not zero.
    assert (rust.supported_with_view_count, rust.supported_without_view_count) == (3, 1)
    hidden = by_id["SYNrust0007"]
    assert (hidden.view_count, hidden.like_count, hidden.comment_count) == (None, None, None)
    assert (
        hidden.details_retrieved_at == AS_OF - 2 * HOUR and by_id["SYNrust0001"].view_count == 1000
    )
    assert by_id["SYNrust0008"].details_retrieved_at is None  # search snippet only
    # L. What was sampled is a lower bound, and the total is never claimed.
    assert rust.total_competition_known is False
    assert "Total competition is unknown" in rust.summary
    document = rust.model_dump_json()
    # The API's "totalResults" (a million, in these responses) appears nowhere.
    assert "1000000" not in document and "total_results" not in document
    assert "wipe day" not in document and "programming" not in document  # no titles either

    # C. A complete search that returned nothing. Not low competition.
    dota = read("dota-2")
    assert dota.state is SupplyState.NO_RESULTS and dota.sample_complete
    assert dota.candidate_videos == 0 and dota.videos == ()
    assert "not evidence of low competition" in dota.summary
    # E. The API said the quota was exceeded: incomplete, and not "no results".
    cs = read("counter-strike-2")
    assert cs.state is SupplyState.INCOMPLETE and cs.candidate_videos == 0
    (refused,) = cs.queries
    assert refused.completeness is QueryCompleteness.QUOTA_LIMITED and refused.pages_fetched == 0
    assert (refused.error_code, refused.api_error_reason) == (
        SourceErrorCode.QUOTA_EXHAUSTED,
        "quotaExceeded",
    )
    # B, K. Videos came back and none is supported: ambiguous, never a count.
    elden = read("elden-ring")
    assert elden.state is SupplyState.AMBIGUOUS and elden.sample_complete
    assert (
        elden.candidate_videos,
        elden.supported_videos,
        elden.uncertain_videos,
        elden.excluded_videos,
    ) == (3, 0, 2, 1)
    assert elden.supported_channels == 0 and "not low competition" in elden.summary
    # Never asked about at all.
    untouched = read("minecraft")
    assert untouched.state is SupplyState.NOT_QUERIED and untouched.queries == ()
    assert "That is not low competition" in untouched.summary
    # Six different things, six different states. None of them is a number of competitors.
    assert {s.value for s in SupplyState} == {
        "not_queried",
        "expired",
        "incomplete",
        "no_results",
        "ambiguous",
        "observed",
    }
    # Deterministic: the same store read again is the same reading.
    assert read("rust") == rust and read("rust").model_dump_json() == document


def test_a_reading_uses_only_what_had_been_retrieved_by_its_moment(
    read: Read, supply: SyntheticCreatorSupply
) -> None:
    # Before anything was asked: nothing is known, for every game.
    for slug in ("rust", "dota-2", "counter-strike-2", "elden-ring"):
        assert read(slug, AS_OF - 3 * HOUR).state is SupplyState.NOT_QUERIED

    # Between the first and second runs only the first is known.
    early = read("rust", AS_OF - 100 * timedelta(minutes=1))
    assert [q.text for q in early.queries] == ["Rust"]
    assert early.candidate_videos == 8 and early.duplicate_results == 0
    seen = {video.video_id: video for video in early.videos}
    assert seen["SYNrust0001"].returned_by_queries == 1
    # A CURRENT SEARCH CANNOT RECONSTRUCT A HISTORICAL ONE. This video was published thirty
    # hours before the moment, well before this reading, but the only search that returned
    # it was made an hour later. It is not part of what was known here.
    assert "SYNrust0009" not in seen
    published = AS_OF - 30 * HOUR
    assert published < AS_OF - 100 * timedelta(minutes=1)
    later = {video.video_id: video for video in read("rust").videos}
    assert later["SYNrust0009"].published_at == published
    # The game that was not sent in the first run is incomplete then, and complete after
    # the run that did ask.
    waiting = read("elden-ring", AS_OF - 100 * timedelta(minutes=1))
    assert waiting.state is SupplyState.INCOMPLETE
    assert waiting.queries[0].completeness is QueryCompleteness.QUOTA_LIMITED
    assert waiting.queries[0].pages_fetched == 0 and waiting.candidate_videos == 0
    assert read("elden-ring").queries[0].run_id == "synthetic-run-3"

    # I. A search made after the moment, holding a video published after the moment.
    now = read("rust")
    assert "SYNrust0010" not in now.model_dump_json()
    assert all(video.published_at <= AS_OF for video in now.videos)
    assert now.latest_retrieved_at is not None and now.latest_retrieved_at <= AS_OF
    stored = [f for f in supply.store.searches() if f.retrieved_at > AS_OF]
    assert len(stored) == 1 and "SYNrust0010" in stored[0].model_dump_json()  # it is in the store
    after = read("rust", AS_OF + 2 * HOUR)
    newest = {video.video_id: video for video in after.videos}
    assert newest["SYNrust0010"].published_at == AS_OF + HOUR / 2
    # The later run replaces the earlier sample of the same question; it is one page, whole.
    assert after.state is SupplyState.OBSERVED and after.sample_complete
    assert "at least 3 channel" not in after.summary and "At least 3 video(s)" in after.summary
    with pytest.raises(ValueError, match="timezone-aware"):
        read("rust", datetime(2026, 10, 20, 12))


def test_counters_and_details_are_never_backdated(tmp_path: Path) -> None:
    """Details retrieved after a moment are not known at it, whatever the video's age."""
    names = [GameNames.of(GAME, "Rust")]
    video = _Video("SYNt0000001", "UCa", "Rust wipe day", AS_OF - 3 * DAY, views=500)
    grown = _Video("SYNt0000001", "UCa", "Rust wipe day", AS_OF - 3 * DAY, views=90_000)
    store = CreatorSupplyStore(tmp_path / "store")
    searched_at, detailed_at, refreshed_at = AS_OF - 6 * HOUR, AS_OF - 4 * HOUR, AS_OF - HOUR
    _search(tmp_path / "s", "a", "Rust", [video])
    _details(tmp_path / "s", "unused", [_Video("SYNt0000009", "UCa", "x", AS_OF - DAY)])
    search = (
        Counting(tmp_path / "s").adapter(YouTubeQuota(), searched_at).search(ask("Rust"), 0, None)
    )
    store.add_searches([search])
    _details(tmp_path / "d1", "d", [video])
    store.add_videos(
        [Counting(tmp_path / "d1").adapter(YouTubeQuota(), detailed_at).videos([video.video_id])]
    )
    _details(tmp_path / "d2", "d", [grown])
    store.add_videos(
        [Counting(tmp_path / "d2").adapter(YouTubeQuota(), refreshed_at).videos([video.video_id])]
    )

    def only(moment: datetime) -> SampledVideo:
        (found,) = read_creator_supply(store, names[0], names, [], moment).videos
        return found

    # Searched, details not yet retrieved: uncertain, and no counter is shown.
    before = only(searched_at + HOUR)
    assert (before.association, before.reasons) == (
        AssociationState.UNCERTAIN,
        ("no_video_details",),
    )
    assert before.view_count is None and before.details_retrieved_at is None
    # The first retrieval: its counter, stamped with when it was retrieved.
    first = only(detailed_at + HOUR)
    assert first.association is AssociationState.SUPPORTED
    assert (first.view_count, first.details_retrieved_at) == (500, detailed_at)
    # The later, larger counter is never shown for the earlier moment.
    assert (
        "90000"
        not in read_creator_supply(store, names[0], names, [], detailed_at + HOUR).model_dump_json()
    )
    latest = only(AS_OF)
    assert (latest.view_count, latest.details_retrieved_at) == (90_000, refreshed_at)
    # Four moments are kept apart on every record.
    assert search.results[0].published_at == AS_OF - 3 * DAY  # when the video was published
    assert search.retrieved_at == searched_at  # when the search was made
    assert latest.details_retrieved_at == refreshed_at  # when its details were observed
    assert read_creator_supply(store, names[0], names, [], AS_OF).as_of == AS_OF  # when it is read


# --- inside the game evidence reading -------------------------------------------------------


def with_supply(world: World, supply: SyntheticCreatorSupply, **changes: object) -> EvidenceInputs:
    return world.inputs(creator_supply=supply.store, creator_supply_provenance=DECLARED, **changes)


@pytest.fixture(scope="module")
def report(world: World, supply: SyntheticCreatorSupply) -> EvidenceReport:
    return assess_evidence(with_supply(world, supply), AS_OF, world.scope)


def test_the_game_evidence_reading_carries_creator_supply_when_it_is_given(
    world: World, supply: SyntheticCreatorSupply, report: EvidenceReport, read: Read
) -> None:
    assert report.report_version == "game_evidence_v2"
    assert report.inputs[YOUTUBE].standing is Standing.SYNTHETIC and report.contains_synthetic
    expected = {
        "rust": (SupplyState.INCOMPLETE, EvidenceState.INCOMPLETE),
        "dota-2": (SupplyState.NO_RESULTS, EvidenceState.AVAILABLE),
        "counter-strike-2": (SupplyState.INCOMPLETE, EvidenceState.INCOMPLETE),
        "elden-ring": (SupplyState.AMBIGUOUS, EvidenceState.UNRESOLVED),
        "minecraft": (SupplyState.NOT_QUERIED, EvidenceState.MISSING),
    }
    for slug, (supply_state, state) in expected.items():
        game = report.game(world.ids[slug])
        row = game.source(YOUTUBE)
        assert game.creator_supply is not None and game.creator_supply == read(slug)
        assert (game.creator_supply.state, row.state) == (supply_state, state), slug
        assert row.kind is EvidenceKind.CREATOR_SUPPLY and row.family is SourceFamily.CREATOR_SUPPLY
        assert row.latest_observed_at == game.creator_supply.latest_retrieved_at
        # Synthetic, so never usable, never verified, and labelled on the game.
        assert row.provenance.standing is Standing.SYNTHETIC and not row.usable, slug
        assert YOUTUBE in game.synthetic_sources and YOUTUBE not in game.usable_sources
        assert not [s for s in game.sources if s.source == CREATOR_SUPPLY]  # one row, not two
        assert len(game.sources) == 7
        codes = [gap.code for gap in game.gaps]
        wanted = (
            "creator_supply_not_sampled" if slug == "minecraft" else "creator_supply_is_a_sample"
        )
        assert codes[0] == wanted and "creator_supply_not_integrated" not in codes
        assert "opportunity_score_not_defined" in codes  # still no opportunity score
        # Creator supply is not demand: no demand family, no count, no score moved.
        assert SourceFamily.CREATOR_SUPPLY not in {r.family for r in game.demand.families}
    view = next(policy for policy in report.policies if policy.source == YOUTUBE)
    assert view.purpose is SourcePurpose.CREATOR_SUPPLY and not view.publishable
    assert view.cleared_stages == (PRIVATE,) and view.public_launch_refusal
    rust = report.game(world.ids["rust"])
    assert YOUTUBE in rust.restricted_sources  # incomplete data is still restricted data
    assert "evidence_restricted_to_private_development" in {gap.code for gap in rust.gaps}

    text = render(report)
    assert "youtube_data            SYNTHETIC" in text
    assert (
        "Creator supply: a SAMPLE, not the market (incomplete)  [SYNTHETIC]. "
        "Total competition: UNKNOWN." in text
    )
    assert "Creator supply: a SAMPLE, not the market (no_results)  [SYNTHETIC]" in text
    assert "quota_limited (quota_exhausted: quotaExceeded)" in text
    assert "SYNrust0003  excluded  classified_not_gaming" in text
    assert "view count held for 3, absent for 1" in text
    assert (
        "youtube_data          creator_supply      cleared: private_development; publishable: NO"
        in text
    )
    # No title of a video and no raw counter is printed.
    for hidden in ("wipe day", "programming tutorial", "funny moments", "views 1000", "1000 views"):
        assert hidden not in text, hidden
    assert "it holds synthetic evidence" in publication_refusal(report)
    assert report.distribution == "private_development_only"


def test_nothing_else_in_the_reading_moves_when_creator_supply_is_given_or_absent(
    world: World, report: EvidenceReport
) -> None:
    """Existing game evidence is unchanged without the input, and unchanged around it."""
    without = assess_evidence(world.inputs(), AS_OF, world.scope)
    assert YOUTUBE not in without.inputs and len(without.inputs) == 4
    assert [policy.source for policy in without.policies] == [
        "igdb",
        "steam_catalogue",
        "wikimedia_pageviews",
        "steam_current_players",
        "steam_news",
    ]
    assert not without.contains_synthetic
    for game in without.games:
        absent = game.source(CREATOR_SUPPLY)
        # Exactly the row there was before this checkpoint.
        assert absent.state is EvidenceState.NOT_INTEGRATED and absent.policy is None
        assert absent.reason == "MetaSpike has no source of this kind yet; this is not a zero"
        assert game.creator_supply is None and len(game.sources) == 7
        assert game.gaps[0].code == "creator_supply_not_integrated"
        with pytest.raises(StopIteration):
            game.source(YOUTUBE)
        # And with the input, everything that is not creator supply is identical.
        given = report.game(game.canonical_game_id)
        assert given.identity == game.identity and given.demand == game.demand
        assert given.movements == game.movements and given.events == game.events
        assert (given.score_v0, given.confidence_v0) == (game.score_v0, game.confidence_v0)
        assert given.independent_demand_families == game.independent_demand_families
        assert given.sources[:5] == game.sources[:5] and given.sources[6] == game.sources[6]
        assert given.gaps[1:3] == game.gaps[1:3]
    text = render(without)
    assert "creator supply        -                   not_integrated: no source, no policy" in text
    assert "Creator supply:" not in text and "youtube" not in text


def test_synthetic_and_unverified_creator_supply_is_never_confirmed(
    world: World, supply: SyntheticCreatorSupply, report: EvidenceReport, tmp_path: Path
) -> None:
    rust = world.ids["rust"]
    # The scenario's store, handed over as an ordinary local store: read as unverified,
    # then recognised as synthetic from what is stored in it.
    reimported = assess_evidence(
        world.inputs(
            creator_supply=supply.store,
            creator_supply_provenance=local_store_provenance("a-local-store"),
        ),
        AS_OF,
        [rust, world.ids["minecraft"]],
    )
    assert reimported.inputs[YOUTUBE].standing is Standing.UNVERIFIED
    assert reimported.contains_unverified and reimported.contains_synthetic
    row = reimported.game(rust).source(YOUTUBE)
    assert row.provenance.standing is Standing.SYNTHETIC and not row.usable
    assert row.provenance.note == "detected in the store: its responses carry a SYNTHETIC etag"
    assert reimported.game(rust).creator_supply == report.game(rust).creator_supply
    # A game the store says nothing about gives no sign either way: unverified.
    untouched = reimported.game(world.ids["minecraft"]).source(YOUTUBE)
    assert untouched.state is EvidenceState.MISSING
    assert untouched.provenance.standing is Standing.UNVERIFIED
    assert all(fetch.etag == SYNTHETIC_ETAG for fetch in supply.store.searches() if fetch.etag)

    # A local store with nothing synthetic about it: shown, and not counted as real.
    plain = tmp_path / "responses"
    video = _Video("dQw4w9WgXcQ", "UCuAXFkgsw1L7xaCfnd5JJOw", "Rust wipe day", AS_OF - DAY)
    _search(plain, "s", "Rust", [video])
    _details(plain, "d", [video])
    for path in plain.glob("*.json"):
        path.write_text(path.read_text().replace(SYNTHETIC_ETAG, "Dn5xIderbhAnUk5TAW0qkFFir0M"))
    store = CreatorSupplyStore(tmp_path / "store")
    with replay_youtube_client(plain) as client:
        adapter = YouTubeAdapter(client, YouTubeQuota(), run_id="r", now=lambda: AS_OF - HOUR)
        collect_creator_supply(
            adapter, YOUTUBE_DATA_POLICY, [ask("Rust", rust)], store, stage=PRIVATE
        )
    unverified = assess_evidence(
        world.inputs(
            creator_supply=store,
            creator_supply_provenance=local_store_provenance(str(tmp_path / "store")),
        ),
        AS_OF,
        [rust],
    ).game(rust)
    row = unverified.source(YOUTUBE)
    assert unverified.creator_supply is not None
    assert unverified.creator_supply.state is SupplyState.OBSERVED
    assert unverified.creator_supply.supported_videos == 1
    assert row.state is EvidenceState.AVAILABLE and row.provenance.standing is Standing.UNVERIFIED
    assert not row.usable and not row.provenance.synthetic
    assert YOUTUBE in unverified.unverified_sources and YOUTUBE not in unverified.usable_sources
    assert "unverified_evidence" in {gap.code for gap in unverified.gaps}
    assert "[UNVERIFIED ORIGIN]. Total competition: UNKNOWN." in render(
        assess_evidence(
            world.inputs(
                creator_supply=store,
                creator_supply_provenance=local_store_provenance("x"),
            ),
            AS_OF,
            [rust],
        )
    )
    # Nothing can be declared verified: no YouTube recording is pinned, so none can be.
    claim = InputProvenance(origin=Origin.RECORDED_RESPONSE, reference="trust me")
    with pytest.raises(ValueError, match="verified only with the digest of a pinned"):
        assess_evidence(
            world.inputs(creator_supply=store, creator_supply_provenance=claim), AS_OF, [rust]
        )


def test_youtube_is_cleared_for_private_development_and_nothing_else(
    world: World, supply: SyntheticCreatorSupply
) -> None:
    policy = YOUTUBE_DATA_POLICY
    assert policy.source == YOUTUBE == cli.EVIDENCE_POLICIES[YOUTUBE].source
    # No permission is read as granted. Derived figures are unanswered, not allowed.
    assert policy.derived_metrics_allowed is Permission.UNKNOWN
    assert policy.commercial_use_allowed is Permission.UNKNOWN
    assert policy.redistribution_allowed is Permission.RESTRICTED
    assert policy.raw_storage_allowed is Permission.RESTRICTED and policy.retention_days == 30
    assert policy.publication_refusal() is not None and not policy.derived_output_permitted
    assert not policy.commercial_production_permitted
    assert {c.stage: c.cleared for c in policy.stage_clearance} == {
        DeploymentStage.PRIVATE_DEVELOPMENT: True,
        DeploymentStage.PRIVATE_COLLECTION: False,
        DeploymentStage.PUBLIC_LAUNCH: False,
        DeploymentStage.COMMERCIAL_PRODUCTION: False,
    }
    purpose = SourcePurpose.CREATOR_SUPPLY
    assert policy.registration_refusal(purpose, PRIVATE) is None
    assert "not cleared for private_collection" in (
        policy.registration_refusal(purpose, DeploymentStage.PRIVATE_COLLECTION) or ""
    )
    for stage in (DeploymentStage.PUBLIC_LAUNCH, DeploymentStage.COMMERCIAL_PRODUCTION):
        assert "derived creator-supply figures are not explicitly allowed (unknown)" in (
            policy.registration_refusal(purpose, stage) or ""
        )
    assert policy.display.branding_required.value == "required"
    assert policy.display.link_back_required.value == "required"
    assert all(
        reference.startswith("https://developers.google.com/youtube/")
        for reference in policy.references
    )
    # A granted derived-metrics permission alone would still not clear a public stage.
    opened = policy.model_copy(update={"derived_metrics_allowed": Permission.ALLOWED})
    assert "showing the source's video data is not explicitly allowed" in (
        opened.registration_refusal(purpose, DeploymentStage.PUBLIC_LAUNCH) or ""
    )
    forbidden = policy.model_copy(update={"derived_metrics_allowed": Permission.PROHIBITED})
    assert "derived figures are prohibited" in (
        forbidden.registration_refusal(purpose, PRIVATE) or ""
    )

    # The reading refuses a creator-supply input at any other stage, by this policy.
    bootstrap_only = world.inputs(
        state=generate_catalogue(),
        records=[],
        wikimedia=None,
    )
    for stage in (DeploymentStage.PRIVATE_COLLECTION, DeploymentStage.PUBLIC_LAUNCH):
        given = world.inputs(
            state=bootstrap_only.state,
            records=[],
            wikimedia=None,
            creator_supply=supply.store,
            creator_supply_provenance=DECLARED,
            stage=stage,
        )
        with pytest.raises(SourcePolicyError, match="youtube_data"):
            assess_evidence(given, AS_OF)
    without_policy = {k: v for k, v in cli.EVIDENCE_POLICIES.items() if k != YOUTUBE}
    with pytest.raises(SourcePolicyError, match="youtube_data: no source policy"):
        assess_evidence(with_supply(world, supply, policies=without_policy), AS_OF, world.scope[:1])
    # With no creator-supply input the YouTube policy is not consulted at all.
    assert assess_evidence(world.inputs(policies=without_policy), AS_OF, world.scope[:1]).games

    # The other sources' policies are exactly what they were.
    assert sorted(cli.EVIDENCE_POLICIES) == [
        "igdb",
        "steam_catalogue",
        "steam_current_players",
        "steam_news",
        "wikimedia_pageviews",
        "youtube_data",
    ]
    assert [purpose.value for purpose in SourcePurpose] == [
        "demand_signals",
        "catalogue_metadata",
        "event_intelligence",
        "creator_supply",
    ]


def test_youtube_data_cannot_reach_a_published_artifact() -> None:
    assert SCHEMA_VERSION == "1.5.0"
    publishing = REPO / "src" / "metaspike" / "publishing"
    for path in publishing.glob("*.py"):
        text = path.read_text()
        assert "creator_sources" not in text and "youtube" not in text.lower(), path.name
    assert not [
        p.name for p in (REPO / "schemas").iterdir() if "youtube" in p.name or "creator" in p.name
    ]
    assert not any("youtube" in p.read_text().lower() for p in (REPO / "schemas").glob("*.json"))
    # Only the evidence layer, the command line and the package itself know of it.
    users = sorted(
        path.relative_to(REPO / "src" / "metaspike").as_posix()
        for path in (REPO / "src" / "metaspike").rglob("*.py")
        if "creator_sources" in path.read_text() and "creator_sources" not in path.parts
    )
    assert users == ["cli.py", "evidence/assemble.py", "evidence/model.py"]
    # No job, schedule or warehouse table collects it.
    for area in ("jobs", "scheduling", "warehouse", "pipeline", "sources"):
        for path in (REPO / "src" / "metaspike" / area).glob("*.py"):
            assert "youtube" not in path.read_text().lower(), path
    assert not any("youtube" in p.read_text().lower() for p in (REPO / ".github").rglob("*.yml"))


# --- the command ----------------------------------------------------------------------------


def test_the_command_demonstrates_creator_supply_offline_and_deterministically(
    supply: SyntheticCreatorSupply,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_socket(*_: object, **__: object) -> None:
        raise AssertionError("the evidence command tried to open a network connection")

    def no_client(*_: object, **__: object) -> None:
        raise AssertionError("the evidence command must never build a live client")

    monkeypatch.setattr(socket.socket, "connect", no_socket)
    monkeypatch.setattr(cli, "build_client", no_client)
    for name in ("YOUTUBE_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"):
        monkeypatch.delenv(name, raising=False)

    def run(*arguments: str) -> tuple[int, str, str]:
        code = cli.main(["evidence", "--as-of", AS_OF.isoformat(), *arguments])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    games = ("--game", "rust", "--game", "dota-2", "--game", "elden-ring", "--game", "minecraft")
    code, text, _ = run("--synthetic-scenario", *games)
    assert code == cli.EXIT_OK
    assert run("--synthetic-scenario", *games)[1] == text  # byte for byte
    assert "*** THIS REPORT CONTAINS SYNTHETIC EVIDENCE. ***" in text
    assert "youtube_data            SYNTHETIC" in text
    assert '"Rust": a synthetic search dated AFTER the moment asked about, never read' in text
    assert "Creator supply: a SAMPLE, not the market (incomplete)  [SYNTHETIC]" in text
    assert "Creator supply: a SAMPLE, not the market (ambiguous)  [SYNTHETIC]" in text
    assert "Creator supply: a SAMPLE, not the market (not_queried)  [SYNTHETIC]" in text
    assert "SYNrust0010" not in text  # retrieved after the moment asked about
    assert "No opportunity score exists." in text

    code, printed, _ = run("--synthetic-scenario", "--game", "rust", "--json")
    document = json.loads(printed)
    assert document["report_version"] == "game_evidence_v2"
    assert document["inputs"][YOUTUBE]["standing"] == "synthetic"
    reading = document["games"][0]["creator_supply"]
    assert reading["state"] == "incomplete" and reading["total_competition_known"] is False
    assert (reading["candidate_videos"], reading["supported_videos"]) == (9, 4)
    assert reading["association_rules_version"] == "creator_association_rules_v1"

    # A stored creator-supply directory is read as found: unverified, then detected.
    code, printed, _ = run(
        "--creator-supply-dir", str(supply.store.root), "--game", "rust", "--json"
    )
    stored = json.loads(printed)
    assert code == cli.EXIT_OK and stored["inputs"][YOUTUBE]["standing"] == "unverified"
    assert stored["games"][0]["creator_supply"] == reading
    row = next(s for s in stored["games"][0]["sources"] if s["source"] == YOUTUBE)
    assert (
        row["provenance"]["standing"] == "synthetic"
        and YOUTUBE not in stored["games"][0]["usable_sources"]
    )
    # Synthetic and stored evidence are never mixed, and no option declares anything real.
    code, out, err = run("--synthetic-scenario", "--creator-supply-dir", str(supply.store.root))
    assert (code, out) == (cli.EXIT_INVALID_CATALOGUE, "") and "never mixed" in err
    code, out, err = run("--creator-supply-dir", str(supply.store.root), "--stage", "public_launch")
    assert (code, out) == (cli.EXIT_SOURCE_POLICY, "")
    # Without the input the reading says what it always said.
    code, plain, _ = run("--game", "rust")
    assert "creator supply        -                   not_integrated: no source, no policy" in plain
    assert "youtube" not in plain and "Creator supply:" not in plain
    with pytest.raises(SystemExit):
        cli.main(["evidence", "--help"])
    options = capsys.readouterr().out
    assert "--creator-supply-dir" in options
    for word in ("--trust", "--verified", "--real", "--live", "--api-key", "--youtube-key"):
        assert word not in options, word


# --- U6.1.1: the videos.list request, and nothing from the future ---------------------------

# Every parameter `videos.list` documents as usable together with `id`, and the key.
# `maxResults` and `pageToken` are documented as NOT supported with `id`.
VIDEOS_PARAMETERS_ALLOWED_WITH_ID = frozenset(
    {
        "part",
        "id",
        "hl",
        "maxHeight",
        "maxWidth",
        "onBehalfOfContentOwner",
        "regionCode",
        "videoCategoryId",
        "key",
    }
)


def test_videos_list_is_asked_by_id_with_part_and_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The outgoing request itself, captured. Not what a replay file happened to match."""

    def no_socket(*_: object, **__: object) -> None:
        raise AssertionError("a videos.list test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", no_socket)
    sent: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        ids = request.url.params["id"].split(",")
        return httpx.Response(200, json=videos_body([resource(video_id) for video_id in ids]))

    client = httpx.Client(transport=httpx.MockTransport(answer))
    quota = YouTubeQuota()
    adapter = YouTubeAdapter(client, quota, run_id="r", now=lambda: AS_OF)
    ids = ["SYNv0000001", "SYNv0000002", "SYNv0000003"]
    fetch = adapter.videos(ids)
    (request,) = sent
    assert request.method == "GET" and request.url.host == API_HOST
    assert request.url.path == "/youtube/v3/videos"
    # Exactly two parameters: the parts that are read, and the ids, in the order given.
    assert dict(request.url.params) == {
        "part": "snippet,statistics,topicDetails",
        "id": "SYNv0000001,SYNv0000002,SYNv0000003",
    }
    assert "maxResults" not in request.url.params and "pageToken" not in request.url.params
    assert set(request.url.params) <= VIDEOS_PARAMETERS_ALLOWED_WITH_ID
    assert fetch.status is FetchStatus.OK and [d.video_id for d in fetch.details] == ids
    assert fetch.requested_ids == tuple(ids) and fetch.missing_ids == ()
    assert (fetch.units_spent, quota.other_units_used, quota.search_calls_used) == (1, 1, 0)
    # With a key the only addition is the documented `key`.
    keyed = YouTubeAdapter(
        client, YouTubeQuota(), run_id="r", now=lambda: AS_OF, api_key=Secret("k")
    )
    keyed.videos(ids[:1])
    assert set(sent[-1].url.params) == {"part", "id", "key"}
    assert "maxResults" not in str(sent[-1].url)

    # Through the collector: bounded batches of at most fifty, each id asked for once.
    videos = [_Video(f"SYNw{n:07d}", "UCa", f"Rust {n}", AS_OF - DAY) for n in range(60)]
    _search(tmp_path, "one", "Rust", videos[:50], next_token="P2")
    _search(tmp_path, "two", "Rust", [*videos[40:], videos[0]], token="P2")  # eleven repeats
    replayed = replay_youtube_client(tmp_path)
    sent.clear()

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/videos"):
            return answer(request)
        sent.append(request)
        return replayed.send(request)

    routed = httpx.Client(transport=httpx.MockTransport(route))
    store = CreatorSupplyStore(tmp_path / "store")
    query = SearchQuery(canonical_game_id=GAME, text="Rust", max_results=50)
    summary = collect_creator_supply(
        YouTubeAdapter(routed, YouTubeQuota(), run_id="r", now=lambda: AS_OF),
        YOUTUBE_DATA_POLICY,
        [query],
        store,
        stage=PRIVATE,
        max_pages=2,
    )
    searches = [r for r in sent if r.url.path.endswith("/search")]
    details = [r for r in sent if r.url.path.endswith("/videos")]
    assert (len(searches), len(details)) == (2, 2)
    asked = [request.url.params["id"].split(",") for request in details]
    assert [len(batch) for batch in asked] == [50, 10]
    flat = [video_id for batch in asked for video_id in batch]
    assert flat == [video.video_id for video in videos] and len(set(flat)) == 60
    for request in details:
        assert set(request.url.params) == {"part", "id"}
        assert request.url.params["part"] == "snippet,statistics,topicDetails"
    assert (summary.other_units_spent, summary.videos_requests_sent) == (2, 2)
    assert summary.video_details_retrieved == 60 and summary.candidate_videos == 60
    # search.list is unchanged: it does take maxResults, and a page token when paging.
    assert dict(searches[0].url.params) == {
        "part": "snippet",
        "type": "video",
        "q": "Rust",
        "maxResults": "50",
        "order": "date",
    }
    assert searches[1].url.params["pageToken"] == "P2"
    assert searches[1].url.params["maxResults"] == "50"
    # Replay still answers deterministically, by id.
    again = CreatorSupplyStore(tmp_path / "again")
    collect_creator_supply(
        YouTubeAdapter(routed, YouTubeQuota(), run_id="r", now=lambda: AS_OF),
        YOUTUBE_DATA_POLICY,
        [query],
        again,
        stage=PRIVATE,
        max_pages=2,
    )
    assert [f.model_dump_json() for f in again.videos()] == [
        f.model_dump_json() for f in store.videos()
    ]


def temporal_store(root: Path) -> CreatorSupplyStore:
    """SYNTHETIC. One store holding evidence from before and after the moment AS_OF.

    search   retrieved T-2h    V1 published T-1d, V2 published T-3h
    details  retrieved T-90m   V1 as published, 100 views
    search   retrieved T-30m   V4, claiming publication at T+10m (inside the
                               parser's tolerance of its own retrieval)
    details  retrieved T-30m   V2, claiming publication at T+20m, from another
                               channel, classified as gaming, 777 views
    details  retrieved T+1h    V1 again, 999,999 views
    search   retrieved T+2h    V5 published T+90m
    """
    store = CreatorSupplyStore(root / "store")
    names = "Rust"

    def at(moment: datetime, run: str, directory: Path) -> YouTubeAdapter:
        return Counting(directory).adapter(YouTubeQuota(), moment, run)

    one = _Video("SYNtime0001", "UCearly000000000000001", "Rust wipe day", AS_OF - DAY, views=100)
    two = _Video("SYNtime0002", "UCearly000000000000002", "Rust raid night", AS_OF - 3 * HOUR)
    _search(root / "a", "s", names, [one, two])
    store.add_searches([at(AS_OF - 2 * HOUR, "run-a", root / "a").search(ask(names), 0, None)])
    _details(root / "b", "d", [one])
    store.add_videos([at(AS_OF - 90 * MINUTE, "run-a", root / "b").videos([one.video_id])])

    four = _Video("SYNtime0004", "UCearly000000000000004", "Rust first look", AS_OF + 10 * MINUTE)
    _search(root / "c", "s", "Rust gameplay", [four])
    store.add_searches(
        [at(AS_OF - 30 * MINUTE, "run-c", root / "c").search(ask("Rust gameplay"), 0, None)]
    )
    future = _Video(
        "SYNtime0002", "UCfuture00000000000000", "Rust raid night", AS_OF + 20 * MINUTE, views=777
    )
    _details(root / "d", "d", [future])
    store.add_videos([at(AS_OF - 30 * MINUTE, "run-c", root / "d").videos([future.video_id])])

    grown = _Video(
        "SYNtime0001", "UCearly000000000000001", "Rust wipe day", AS_OF - DAY, views=999_999
    )
    _details(root / "e", "d", [grown])
    store.add_videos([at(AS_OF + HOUR, "run-e", root / "e").videos([grown.video_id])])
    five = _Video("SYNtime0005", "UCearly000000000000005", "Rust patch day", AS_OF + 90 * MINUTE)
    _search(root / "f", "s", "Rust update", [five])
    _details(root / "f", "d", [five])
    later = at(AS_OF + 2 * HOUR, "run-f", root / "f")
    store.add_searches([later.search(ask("Rust update"), 0, None)])
    store.add_videos([later.videos([five.video_id])])
    return store


MINUTE = timedelta(minutes=1)


def test_nothing_dated_after_the_moment_contributes_to_a_reading(tmp_path: Path) -> None:
    store = temporal_store(tmp_path)
    names = [GameNames.of(GAME, "Rust")]
    # The parser kept the two near-future claims: they are within tolerance of their own
    # retrieval. Its check is unchanged. Keeping them out of an earlier reading is the
    # reading's job.
    kept = {r.video_id: r for f in store.searches() for r in f.results}
    assert kept["SYNtime0004"].published_at == AS_OF + 10 * MINUTE
    stored_details = [d for f in store.videos() for d in f.details if d.video_id == "SYNtime0002"]
    assert [d.published_at for d in stored_details] == [AS_OF + 20 * MINUTE]

    now = read_creator_supply(store, names[0], names, [], AS_OF)
    videos = {video.video_id: video for video in now.videos}
    # A, B. Published before the moment, searched and detailed before it: used in full.
    first = videos["SYNtime0001"]
    assert first.association is AssociationState.SUPPORTED
    assert first.published_at == AS_OF - DAY and first.details_retrieved_at == AS_OF - 90 * MINUTE
    # G. The counter is the one retrieved by the moment, never the later, larger one.
    assert first.view_count == 100

    # C. Its details were retrieved before the moment and claim a publication time after
    # it. Nothing of them is used; the search snippet stands, and the video is uncertain.
    second = videos["SYNtime0002"]
    assert second.association is AssociationState.UNCERTAIN
    assert second.reasons == ("video_details_dated_after_the_moment_read_at",)
    assert second.published_at == AS_OF - 3 * HOUR  # the snippet's, not a corrected one
    assert second.channel_id == "UCearly000000000000002"  # the snippet's channel
    assert second.details_retrieved_at is None
    assert (second.view_count, second.like_count, second.comment_count) == (None, None, None)
    assert now.details_withheld_as_future_dated == 1
    # D, E, F. A result published after the moment, details retrieved after it, and a
    # search made after it: none is in the reading.
    assert set(videos) == {"SYNtime0001", "SYNtime0002"}
    document = now.model_dump_json()
    for absent in ("SYNtime0004", "SYNtime0005", "UCfuture", "999999", "777"):
        assert absent not in document, absent
    # I. One supported video from one channel. The future-dated detail adds neither.
    assert (now.supported_videos, now.supported_channels, now.uncertain_videos) == (1, 1, 1)
    assert now.most_videos_from_one_channel == 1 and now.candidate_videos == 2
    assert (now.supported_with_view_count, now.supported_without_view_count) == (1, 0)
    assert now.newest_supported_published_at == AS_OF - DAY
    assert all(video.published_at <= AS_OF for video in now.videos)
    assert all(
        video.details_retrieved_at is None or video.details_retrieved_at <= AS_OF
        for video in now.videos
    )
    assert now.latest_retrieved_at == AS_OF - 30 * MINUTE

    # H. The same store, read after everything in it had happened and been retrieved.
    later = read_creator_supply(store, names[0], names, [], AS_OF + 3 * HOUR)
    after = {video.video_id: video for video in later.videos}
    assert set(after) == {"SYNtime0001", "SYNtime0002", "SYNtime0004", "SYNtime0005"}
    assert after["SYNtime0001"].view_count == 999_999
    assert after["SYNtime0001"].details_retrieved_at == AS_OF + HOUR
    # The detail is no longer from the future, so now it is used, as it was retrieved.
    assert after["SYNtime0002"].association is AssociationState.SUPPORTED
    assert after["SYNtime0002"].published_at == AS_OF + 20 * MINUTE
    assert after["SYNtime0002"].channel_id == "UCfuture00000000000000"
    assert after["SYNtime0002"].view_count == 777
    assert later.details_withheld_as_future_dated == 0 and later.supported_channels == 3
    # And between the two: the boundary itself. One second before the claimed time the
    # detail is still withheld; at the claimed time it is used.
    edge = AS_OF + 20 * MINUTE
    just_before = read_creator_supply(store, names[0], names, [], edge - timedelta(seconds=1))
    assert just_before.details_withheld_as_future_dated == 1
    at_edge = read_creator_supply(store, names[0], names, [], edge)
    assert at_edge.details_withheld_as_future_dated == 0
    # The earlier reading is unchanged by having read the later ones, and nothing was rewritten.
    assert read_creator_supply(store, names[0], names, [], AS_OF).model_dump_json() == document

    # An earlier, well-dated detail is not put in the place of a later contradicting one.
    earlier = _Video("SYNtime0002", "UCearly000000000000002", "Rust raid night", AS_OF - 3 * HOUR)
    _details(tmp_path / "g", "d", [earlier])
    store.add_videos(
        [
            Counting(tmp_path / "g")
            .adapter(YouTubeQuota(), AS_OF - 100 * MINUTE, "run-g")
            .videos([earlier.video_id])
        ]
    )
    contradicted = read_creator_supply(store, names[0], names, [], AS_OF)
    again = next(video for video in contradicted.videos if video.video_id == "SYNtime0002")
    assert again.association is AssociationState.UNCERTAIN and again.view_count is None
    assert contradicted.supported_videos == 1
    # Before the contradicting detail was retrieved, the earlier one is what was known.
    before_conflict = read_creator_supply(store, names[0], names, [], AS_OF - 45 * MINUTE)
    known = next(video for video in before_conflict.videos if video.video_id == "SYNtime0002")
    assert known.association is AssociationState.SUPPORTED
    assert known.details_retrieved_at == AS_OF - 100 * MINUTE


def test_the_synthetic_scenario_is_still_deterministic(
    world: World, supply: SyntheticCreatorSupply, read: Read, tmp_path: Path
) -> None:
    games = {g.slug: (g.canonical_game_id, g.name) for g in world.state.catalogue.games}
    rebuilt = build_synthetic_creator_supply(tmp_path, games, AS_OF)
    assert [f.model_dump_json() for f in rebuilt.store.searches()] == [
        f.model_dump_json() for f in supply.store.searches()
    ]
    assert [f.model_dump_json() for f in rebuilt.store.videos()] == [
        f.model_dump_json() for f in supply.store.videos()
    ]
    rust = read("rust")
    # Unchanged by the closeout: nothing in the scenario is dated after its moment.
    assert (
        rust.candidate_videos,
        rust.supported_videos,
        rust.uncertain_videos,
        rust.excluded_videos,
    ) == (9, 4, 3, 2)
    assert rust.details_withheld_as_future_dated == 0
    # No stored videos.list request carries a parameter it should not.
    for fetch in supply.store.videos():
        assert 1 <= len(fetch.requested_ids) <= MAX_IDS_PER_VIDEOS_CALL
