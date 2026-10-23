"""YouTube Data API v3, as far as creator supply needs it: `search.list`, `videos.list`.

Live on the owner's machine only (`metaspike collect-youtube`, key from
METASPIKE_YOUTUBE_API_KEY). Tests use a replay transport, and every file in the
repository that looks like a YouTube response is synthetic: live answers stay in the
git-ignored local store. The request and response shapes, costs and limits below follow the official
documentation as read on 2026-10-14:

    search.list   1 call in its own "Search Queries" bucket; default 100 calls a day.
                  Every page is another call. maxResults 0-50, default 5.
    videos.list   1 unit of the general bucket; default 10,000 units a day. Asked by
                  `id`, with `part` and nothing else: `maxResults` and `pageToken` are
                  documented as not supported together with `id`.
    any request   costs at least one, even when it is invalid.
    daily quotas  "reset at midnight Pacific Time (PT)".

`pageInfo.totalResults` is, in the documentation's words, "an approximation and may not
represent an exact value", capped at 1,000,000. It is kept on the fetch record as what
the source said and is never read as a number of competing videos.

Only the fields creator coverage needs are read: ids, title, publication time, channel
id, category, tags, topic categories and the three public counters. Descriptions,
thumbnails, channel titles, comments, captions and video content are never read.
"""

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal

import httpx
from pydantic import AwareDatetime, Field, ValidationError

from metaspike.domain.games import GameId, NonEmptyStr
from metaspike.domain.model import FrozenModel
from metaspike.domain.signals import SourceErrorCode
from metaspike.domain.source_policy import (
    ApprovalStatus,
    DeploymentStage,
    DisplayRules,
    LimitBasis,
    Permission,
    QuotaBudget,
    QuotaPeriod,
    RateLimit,
    Requirement,
    SourceOperations,
    SourcePolicy,
    StageClearance,
)
from metaspike.sources.credentials import Secret

SOURCE_ID: Final = "youtube_data"
API_KEY_ENV: Final = "METASPIKE_YOUTUBE_API_KEY"
API_HOST: Final = "www.googleapis.com"
SEARCH_URL: Final = f"https://{API_HOST}/youtube/v3/search"
VIDEOS_URL: Final = f"https://{API_HOST}/youtube/v3/videos"

# Documented defaults (developers.google.com/youtube/v3/determine_quota_cost).
SEARCH_CALLS_PER_DAY: Final = 100
OTHER_UNITS_PER_DAY: Final = 10_000
SEARCH_CALL_COST: Final = 1
VIDEOS_LIST_COST: Final = 1
MAX_RESULTS: Final = 50  # documented upper bound of maxResults
DEFAULT_RESULTS: Final = 25  # self-imposed
# Self-imposed: ids are sent in batches no larger than a page of results.
MAX_IDS_PER_VIDEOS_CALL: Final = 50
# The three documented parts of a video resource that are read. Nothing else is asked for.
VIDEOS_PARTS: Final = "snippet,statistics,topicDetails"
# The "Gaming" video category. A structured field of the video resource.
GAMING_CATEGORY_ID: Final = "20"
# Developer Policies III.E.4: API Data that is not user-authorised may be stored no
# longer than 30 calendar days, after which it must be deleted or refreshed.
RETENTION_DAYS: Final = 30
# A video dated later than its own retrieval, beyond this, is not believed.
_CLOCK_TOLERANCE: Final = timedelta(hours=1)
_SEARCH_KIND: Final = "youtube#searchListResponse"
_VIDEOS_KIND: Final = "youtube#videoListResponse"
_VIDEO_KIND: Final = "youtube#video"

YOUTUBE_DATA_POLICY = SourcePolicy(
    source=SOURCE_ID,
    # Storable, with a hard limit: 30 calendar days, then delete or refresh.
    raw_storage_allowed=Permission.RESTRICTED,
    retention_days=RETENTION_DAYS,
    historical_storage_allowed=Permission.RESTRICTED,
    # The Developer Policies forbid using API Data "to create new or derived data or
    # metrics" (III.E.4.h) and forbid aggregating API Data across channels (III.E.2.a).
    # A separate Derived Metrics Policy permits some derived metrics to clients that
    # accept an amendment. Whether counting sampled videos per game falls inside it has
    # not been established with YouTube. Unanswered is not allowed.
    derived_metrics_allowed=Permission.UNKNOWN,
    commercial_use_allowed=Permission.UNKNOWN,
    # Shown only with YouTube's brand features, links and required notices.
    redistribution_allowed=Permission.RESTRICTED,
    attribution_required=Requirement.REQUIRED,
    attribution_text="Video data from YouTube, via YouTube API Services.",
    # The default quota needs no approval; a larger one needs a compliance audit.
    approval_required=Requirement.NOT_REQUIRED,
    approval_status=ApprovalStatus.NOT_REQUIRED,
    commercial_approval_required=Requirement.UNKNOWN,
    commercial_approval_status=ApprovalStatus.PENDING,
    stage_clearance=(
        StageClearance(
            stage=DeploymentStage.PRIVATE_DEVELOPMENT,
            cleared=True,
            reason=(
                "Bounded live searches from the owner's machine with the project's own "
                "key, kept in a git-ignored local store for at most 30 days. Nothing is "
                "collected unattended and nothing is shown publicly."
            ),
        ),
        StageClearance(
            stage=DeploymentStage.PRIVATE_COLLECTION,
            cleared=False,
            reason=(
                "Not cleared. Calling the API needs one API project for this client and "
                "acceptance of the YouTube API Services Terms; storing what it returns "
                "needs deletion or refresh within 30 calendar days and deletion on "
                "request; and counting videos per game needs YouTube's answer on the "
                "aggregation and derived-metrics clauses. None of that exists."
            ),
        ),
        StageClearance(
            stage=DeploymentStage.PUBLIC_LAUNCH,
            cleared=False,
            reason=(
                "Not cleared. Showing anything from the API needs YouTube brand "
                "features, a link to YouTube's Terms of Service, a privacy policy that "
                "references Google's, and an explicit basis for any derived figure. "
                "Being noncommercial does not waive any of these."
            ),
        ),
        StageClearance(
            stage=DeploymentStage.COMMERCIAL_PRODUCTION,
            cleared=False,
            reason="Not cleared. Nothing above is settled, and commercial terms are unread.",
        ),
    ),
    operations=SourceOperations(
        budget_group="youtube_data_api",
        rate_limit=RateLimit(
            max_requests=1,
            per_seconds=1,
            max_concurrency=1,
            basis=LimitBasis.SELF_IMPOSED,
            note="No per-second limit is documented; one request at a time.",
        ),
        # The scarce bucket. videos.list is counted separately against 10,000 units a
        # day by `YouTubeQuota`; the policy model has room for one figure.
        quota=QuotaBudget(
            limit=SEARCH_CALLS_PER_DAY,
            unit="search.list calls",
            period=QuotaPeriod.DAY,
            basis=LimitBasis.DOCUMENTED,
            note="Its own bucket. Each page of results is one call.",
        ),
        cache_max_age_seconds=RETENTION_DAYS * 86_400,
        # A search answers for the moment it is asked. Nothing is published "late".
        expected_publication_lag_seconds=0,
        publication_lag_basis=LimitBasis.SELF_IMPOSED,
    ),
    display=DisplayRules(
        link_back_required=Requirement.REQUIRED,
        branding_required=Requirement.REQUIRED,
        note=(
            "YouTube must be shown as the source with its brand features; content not "
            "from YouTube must not be shown as if it were."
        ),
    ),
    reviewed_at=date(2026, 10, 14),
    references=(
        "https://developers.google.com/youtube/v3/docs/search/list",
        "https://developers.google.com/youtube/v3/docs/videos/list",
        "https://developers.google.com/youtube/v3/determine_quota_cost",
        "https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits",
        "https://developers.google.com/youtube/terms/developer-policies",
        "https://developers.google.com/youtube/terms/derived-metrics-policy",
    ),
    notes=(
        "Read from the documentation on 2026-10-14. No legal review. Policy text was "
        "read in summary; the clauses cited should be re-read in full before any call."
    ),
)


class FetchStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"  # a request was sent and produced no usable answer
    NOT_SENT = "not_sent"  # nothing was sent: the quota account refused


def digest(prefix: str, *parts: object) -> str:
    canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}_{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:32]}"


class SearchQuery(FrozenModel):
    """One bounded question to `search.list`, asked on behalf of one canonical game."""

    canonical_game_id: GameId
    text: NonEmptyStr
    max_results: int = Field(default=DEFAULT_RESULTS, ge=1, le=MAX_RESULTS)
    order: Literal["date", "relevance"] = "date"
    # Only videos published at or after this moment. A filter on the source's side.
    published_after: AwareDatetime | None = None
    video_category_id: str | None = None

    @property
    def query_id(self) -> str:
        """Identity of the question, whenever it is asked. Excludes the page."""
        return digest(
            "ytq",
            self.canonical_game_id,
            self.text,
            self.max_results,
            self.order,
            self.published_after,
            self.video_category_id,
        )

    def parameters(self, page_token: str | None) -> dict[str, str | int]:
        """The documented request parameters. Never the key."""
        parameters: dict[str, str | int] = {
            "part": "snippet",
            "type": "video",
            "q": self.text,
            "maxResults": self.max_results,
            "order": self.order,
        }
        if self.published_after is not None:
            moment = self.published_after.astimezone(UTC)
            parameters["publishedAfter"] = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
        if self.video_category_id is not None:
            parameters["videoCategoryId"] = self.video_category_id
        if page_token is not None:
            parameters["pageToken"] = page_token
        return parameters


class SearchResult(FrozenModel):
    """One video a search returned. Being returned is not being relevant."""

    video_id: NonEmptyStr
    channel_id: NonEmptyStr
    title: NonEmptyStr
    published_at: AwareDatetime


class SearchFetch(FrozenModel):
    """One `search.list` request and what came of it. One page."""

    fetch_id: str
    run_id: NonEmptyStr
    query: SearchQuery
    page_index: int = Field(ge=0)
    page_token: str | None = None
    # When the request was made. A search answers for this moment and no other.
    retrieved_at: AwareDatetime
    status: FetchStatus
    error_code: SourceErrorCode | None = None
    http_status: int | None = None
    # The API's own reason for an error, such as "quotaExceeded".
    api_error_reason: str | None = None
    # Why paging stopped here with more to fetch: "page_limit". None otherwise.
    stopped_by: str | None = None
    etag: str | None = None
    results: tuple[SearchResult, ...] = ()
    # Entries that could not be read as a video result. Counted, never guessed at.
    rejected: int = Field(default=0, ge=0)
    next_page_token: str | None = None
    # The source's stated approximation. Never a count of anything.
    reported_total_approximate: int | None = None
    search_calls_spent: int = Field(default=0, ge=0)


class VideoDetail(FrozenModel):
    """One video resource from `videos.list`, as retrieved at one moment."""

    video_id: NonEmptyStr
    channel_id: NonEmptyStr
    title: NonEmptyStr
    published_at: AwareDatetime
    category_id: str | None = None
    tags: tuple[str, ...] = ()
    topic_categories: tuple[str, ...] = ()
    # Each is None when the response did not carry it. None is never zero.
    view_count: int | None = Field(default=None, ge=0)
    like_count: int | None = Field(default=None, ge=0)
    comment_count: int | None = Field(default=None, ge=0)


class VideosFetch(FrozenModel):
    """One `videos.list` request and what came of it."""

    fetch_id: str
    run_id: NonEmptyStr
    requested_ids: tuple[str, ...]
    retrieved_at: AwareDatetime
    status: FetchStatus
    error_code: SourceErrorCode | None = None
    http_status: int | None = None
    api_error_reason: str | None = None
    etag: str | None = None
    details: tuple[VideoDetail, ...] = ()
    rejected: int = Field(default=0, ge=0)
    # Asked for and not returned: removed, private, or never existed.
    missing_ids: tuple[str, ...] = ()
    units_spent: int = Field(default=0, ge=0)


class InvalidResponse(Exception):
    """A response body was not the documented envelope."""


def _moment(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("not a timestamp")
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError("a timestamp without an offset")
    return moment


def _count(value: object) -> int | None:
    """A counter as the API sends it: a string of digits. Absent stays absent."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise ValueError("not a counter")
    text = str(value)
    if not text.isascii() or not text.isdigit():
        raise ValueError("not a counter")
    return int(text)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("not text")
    return value


def _envelope(body: object, kind: str) -> tuple[dict[str, Any], list[Any]]:
    if not isinstance(body, dict) or body.get("kind") != kind:
        raise InvalidResponse(f"not a {kind}")
    items = body.get("items")
    if not isinstance(items, list):
        raise InvalidResponse("no list of items")
    return body, items


@dataclass(frozen=True)
class ParsedSearch:
    results: tuple[SearchResult, ...]
    rejected: int
    next_page_token: str | None
    reported_total: int | None
    etag: str | None


def parse_search(body: object, retrieved_at: datetime) -> ParsedSearch:
    """Read a `search.list` body. Entries that are not readable videos are counted."""
    envelope, items = _envelope(body, _SEARCH_KIND)
    results: list[SearchResult] = []
    rejected = 0
    for item in items:
        try:
            identity, snippet = item["id"], item["snippet"]
            if identity.get("kind") != _VIDEO_KIND:
                raise ValueError("not a video")
            published = _moment(snippet["publishedAt"])
            if published > retrieved_at + _CLOCK_TOLERANCE:
                raise ValueError("published after it was retrieved")
            results.append(
                SearchResult(
                    video_id=_text(identity["videoId"]),
                    channel_id=_text(snippet["channelId"]),
                    title=_text(snippet["title"]),
                    published_at=published,
                )
            )
        except (KeyError, TypeError, AttributeError, ValueError, ValidationError):
            rejected += 1
    token = envelope.get("nextPageToken")
    page_info = envelope.get("pageInfo")
    total = page_info.get("totalResults") if isinstance(page_info, dict) else None
    etag = envelope.get("etag")
    return ParsedSearch(
        results=tuple(results),
        rejected=rejected,
        next_page_token=token if isinstance(token, str) and token else None,
        reported_total=total if isinstance(total, int) and not isinstance(total, bool) else None,
        etag=etag if isinstance(etag, str) else None,
    )


@dataclass(frozen=True)
class ParsedVideos:
    details: tuple[VideoDetail, ...]
    rejected: int
    etag: str | None


def parse_videos(body: object, retrieved_at: datetime) -> ParsedVideos:
    """Read a `videos.list` body. A missing statistic stays missing."""
    envelope, items = _envelope(body, _VIDEOS_KIND)
    details: list[VideoDetail] = []
    rejected = 0
    for item in items:
        try:
            if item.get("kind") != _VIDEO_KIND:
                raise ValueError("not a video")
            snippet = item["snippet"]
            published = _moment(snippet["publishedAt"])
            if published > retrieved_at + _CLOCK_TOLERANCE:
                raise ValueError("published after it was retrieved")
            statistics = item.get("statistics")
            if statistics is not None and not isinstance(statistics, dict):
                raise ValueError("statistics is not an object")
            counters = statistics or {}
            topics = (item.get("topicDetails") or {}).get("topicCategories") or ()
            tags = snippet.get("tags") or ()
            category = snippet.get("categoryId")
            details.append(
                VideoDetail(
                    video_id=_text(item["id"]),
                    channel_id=_text(snippet["channelId"]),
                    title=_text(snippet["title"]),
                    published_at=published,
                    category_id=category if isinstance(category, str) and category else None,
                    tags=tuple(tag for tag in tags if isinstance(tag, str) and tag),
                    topic_categories=tuple(t for t in topics if isinstance(t, str) and t),
                    view_count=_count(counters.get("viewCount")),
                    like_count=_count(counters.get("likeCount")),
                    comment_count=_count(counters.get("commentCount")),
                )
            )
        except (KeyError, TypeError, AttributeError, ValueError, ValidationError):
            rejected += 1
    etag = envelope.get("etag")
    return ParsedVideos(
        details=tuple(details), rejected=rejected, etag=etag if isinstance(etag, str) else None
    )


_QUOTA_REASONS: Final = frozenset({"quotaExceeded", "dailyLimitExceeded"})
_RATE_REASONS: Final = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})


def api_error(status: int, body: object) -> tuple[SourceErrorCode, str | None]:
    """What a non-200 answer means, from the documented error envelope."""
    reason = None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        errors = body["error"].get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            found = errors[0].get("reason")
            reason = found if isinstance(found, str) else None
    if reason in _QUOTA_REASONS:
        return SourceErrorCode.QUOTA_EXHAUSTED, reason
    if reason in _RATE_REASONS or status == httpx.codes.TOO_MANY_REQUESTS:
        return SourceErrorCode.RATE_LIMITED, reason
    if status in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
        return SourceErrorCode.UNAUTHORIZED, reason
    if status == httpx.codes.NOT_FOUND:
        return SourceErrorCode.NOT_FOUND, reason
    return SourceErrorCode.HTTP_ERROR, reason


class YouTubeQuota:
    """One day's allowance in both buckets, spent before each request is sent.

    OFFLINE ONLY. This is an in-memory account for one run. It does not persist, does
    not know what earlier runs spent, and does not track the documented reset at
    midnight Pacific Time. It is not a production quota manager and must not be used
    as one: real collection needs a persistent ledger keyed to the Pacific day.

    The limits default to the documented defaults and may be set lower, never higher.
    A request that cannot be paid for is not sent. Every request that is sent is paid
    for whatever comes back, because the API charges for invalid requests too. The two
    buckets are separate: once the API itself says one is spent, nothing further is
    sent from that bucket on this account, and the other is unaffected.
    """

    def __init__(
        self,
        search_calls: int = SEARCH_CALLS_PER_DAY,
        other_units: int = OTHER_UNITS_PER_DAY,
    ) -> None:
        if not 0 <= search_calls <= SEARCH_CALLS_PER_DAY:
            raise ValueError("search calls must be within the documented daily default")
        if not 0 <= other_units <= OTHER_UNITS_PER_DAY:
            raise ValueError("units must be within the documented daily default")
        self.search_calls_limit = search_calls
        self.other_units_limit = other_units
        self.search_calls_used = 0
        self.other_units_used = 0
        self.search_refused_by_source = False
        self.units_refused_by_source = False

    @property
    def search_calls_left(self) -> int:
        if self.search_refused_by_source:
            return 0
        return self.search_calls_limit - self.search_calls_used

    @property
    def other_units_left(self) -> int:
        if self.units_refused_by_source:
            return 0
        return self.other_units_limit - self.other_units_used

    def take_search(self) -> bool:
        if self.search_calls_left < SEARCH_CALL_COST:
            return False
        self.search_calls_used += SEARCH_CALL_COST
        return True

    def take_videos(self) -> bool:
        if self.other_units_left < VIDEOS_LIST_COST:
            return False
        self.other_units_used += VIDEOS_LIST_COST
        return True


def _utc_now() -> datetime:
    return datetime.now(UTC)


class YouTubeAdapter:
    """`search.list` and `videos.list` over a client it is handed. One attempt each.

    The client is always a replay client today. A key, if one is ever given, travels
    as the documented `key` parameter and is never written to a fetch record.
    """

    def __init__(
        self,
        client: httpx.Client,
        quota: YouTubeQuota,
        *,
        run_id: str,
        now: Callable[[], datetime] = _utc_now,
        api_key: Secret | None = None,
    ) -> None:
        self._client = client
        self._quota = quota
        self._run_id = run_id
        self._now = now
        self._api_key = api_key

    @property
    def source(self) -> str:
        return SOURCE_ID

    @property
    def quota(self) -> YouTubeQuota:
        return self._quota

    def _get(self, url: str, parameters: Mapping[str, str | int]) -> httpx.Response | None:
        sent = dict(parameters)
        if self._api_key is not None:
            sent["key"] = self._api_key.reveal()
        try:
            return self._client.get(url, params=sent)
        except httpx.HTTPError:
            return None

    def search(self, query: SearchQuery, page_index: int, page_token: str | None) -> SearchFetch:
        retrieved_at = self._now()
        parameters = query.parameters(page_token)
        base: dict[str, Any] = {
            "fetch_id": digest("ytf", SEARCH_URL, parameters, retrieved_at, self._run_id),
            "run_id": self._run_id,
            "query": query,
            "page_index": page_index,
            "page_token": page_token,
            "retrieved_at": retrieved_at,
        }
        if not self._quota.take_search():
            return SearchFetch(
                **base, status=FetchStatus.NOT_SENT, error_code=SourceErrorCode.QUOTA_EXHAUSTED
            )
        base["search_calls_spent"] = SEARCH_CALL_COST
        response = self._get(SEARCH_URL, parameters)
        if response is None:
            return SearchFetch(
                **base, status=FetchStatus.FAILED, error_code=SourceErrorCode.NETWORK_ERROR
            )
        body = _json(response)
        if response.status_code != httpx.codes.OK:
            code, reason = api_error(response.status_code, body)
            if code is SourceErrorCode.QUOTA_EXHAUSTED:
                self._quota.search_refused_by_source = True
            return SearchFetch(
                **base,
                status=FetchStatus.FAILED,
                error_code=code,
                http_status=response.status_code,
                api_error_reason=reason,
            )
        try:
            parsed = parse_search(body, retrieved_at)
        except InvalidResponse:
            return SearchFetch(
                **base,
                status=FetchStatus.FAILED,
                error_code=SourceErrorCode.INVALID_RESPONSE,
                http_status=response.status_code,
            )
        return SearchFetch(
            **base,
            status=FetchStatus.OK,
            http_status=response.status_code,
            etag=parsed.etag,
            results=parsed.results,
            rejected=parsed.rejected,
            next_page_token=parsed.next_page_token,
            reported_total_approximate=parsed.reported_total,
        )

    def videos(self, video_ids: Sequence[str]) -> VideosFetch:
        if not 1 <= len(video_ids) <= MAX_IDS_PER_VIDEOS_CALL:
            raise ValueError(f"between 1 and {MAX_IDS_PER_VIDEOS_CALL} video ids a call")
        retrieved_at = self._now()
        requested = tuple(video_ids)
        # `part` and `id`, and nothing else. The method documents `maxResults` and
        # `pageToken` as "not supported for use in conjunction with the `id` parameter",
        # so neither is sent: the batch is bounded by how many ids are asked for.
        parameters: dict[str, str | int] = {
            "part": VIDEOS_PARTS,
            "id": ",".join(requested),
        }
        base: dict[str, Any] = {
            "fetch_id": digest("ytf", VIDEOS_URL, parameters, retrieved_at, self._run_id),
            "run_id": self._run_id,
            "requested_ids": requested,
            "retrieved_at": retrieved_at,
        }
        if not self._quota.take_videos():
            return VideosFetch(
                **base, status=FetchStatus.NOT_SENT, error_code=SourceErrorCode.QUOTA_EXHAUSTED
            )
        base["units_spent"] = VIDEOS_LIST_COST
        response = self._get(VIDEOS_URL, parameters)
        if response is None:
            return VideosFetch(
                **base, status=FetchStatus.FAILED, error_code=SourceErrorCode.NETWORK_ERROR
            )
        body = _json(response)
        if response.status_code != httpx.codes.OK:
            code, reason = api_error(response.status_code, body)
            if code is SourceErrorCode.QUOTA_EXHAUSTED:
                self._quota.units_refused_by_source = True
            return VideosFetch(
                **base,
                status=FetchStatus.FAILED,
                error_code=code,
                http_status=response.status_code,
                api_error_reason=reason,
            )
        try:
            parsed = parse_videos(body, retrieved_at)
        except InvalidResponse:
            return VideosFetch(
                **base,
                status=FetchStatus.FAILED,
                error_code=SourceErrorCode.INVALID_RESPONSE,
                http_status=response.status_code,
            )
        returned = {detail.video_id for detail in parsed.details}
        return VideosFetch(
            **base,
            status=FetchStatus.OK,
            http_status=response.status_code,
            etag=parsed.etag,
            details=tuple(d for d in parsed.details if d.video_id in set(requested)),
            rejected=parsed.rejected,
            missing_ids=tuple(i for i in requested if i not in returned),
        )


def _json(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return None


def replay_youtube_client(recordings: Path) -> httpx.Client:
    """Serve recorded-shaped answers from a directory. No request leaves the machine.

    Each file holds `{"request": {...}, "status": int, "body": ...}`. A request is
    answered by the file whose `request` names the same method and the same `q` and
    `pageToken`, or the same `id`. A file's name plays no part. A request with no
    matching file gets a transport error, which is a failed fetch: an unrecorded
    question is never answered with an invented empty result.
    """
    answers: dict[tuple[str, str, str], tuple[int, object]] = {}
    for path in sorted(recordings.glob("*.json")):
        recorded = json.loads(path.read_text(encoding="utf-8"))
        request = recorded["request"]
        key = (
            request["method"],
            request.get("q", request.get("id", "")),
            request.get("pageToken", ""),
        )
        if key in answers:
            raise ValueError(f"{path.name}: a second recording answers the same request")
        answers[key] = (recorded["status"], recorded["body"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != API_HOST:
            raise httpx.ConnectError("replay: unexpected host", request=request)
        method = request.url.path.rsplit("/", 1)[-1]
        params = request.url.params
        key = (method, params.get("q", params.get("id", "")), params.get("pageToken", ""))
        if key not in answers:
            raise httpx.ConnectError("replay: no recording for this request", request=request)
        status, body = answers[key]
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))
