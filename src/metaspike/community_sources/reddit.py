"""Reddit, as far as community interest needs it: one listing endpoint, read offline.

OFFLINE ONLY. Nothing here has ever called Reddit. There is no Reddit account, no app
registration, no OAuth client and no token. Every Reddit-shaped response in the
repository is synthetic and is served by a replay transport.

What the shapes and rules below rest on, and how far they were checked (2026-10-14):

    NOT VERIFIED against Reddit's own pages. reddit.com/dev/api and Reddit's help and
    policy sites refused to be fetched from this environment (HTTP 403 and blocked
    hosts). What follows is from Reddit's long-standing public API shape and from
    secondary descriptions of the current terms, and must be confirmed from the
    official pages before any call:

    - search    GET https://oauth.reddit.com/r/{subreddit}/search with `q`,
                `restrict_sr`, `sort`, `t`, `limit` (default 25, maximum 100) and
                `after` (the fullname of the last item of the previous page).
    - a Listing {"kind": "Listing", "data": {"after", "before", "children": [...]}},
                each child {"kind": "t3", "data": {...}} for a post.
    - rate      reported as 100 queries a minute per OAuth client, averaged over ten
                minutes, with `X-Ratelimit-Used`, `-Remaining` and `-Reset` headers.
    - access    reported as requiring an approved application under Reddit's
                Responsible Builder Policy, even for non-commercial use.
    - deletion  reported as requiring removal of content deleted on Reddit, with a
                recommendation to delete stored content within 48 hours.

Read from a post, and nothing more: its id, subreddit, title, permalink, creation time,
score, comment count and flair text, and whether its link points at the reserved
`.invalid` domain (used only to recognise synthetic data; the link itself is not kept).
Never read: author names or ids, post bodies, comments, awards, media, user profiles.
"""

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal, Self
from urllib.parse import urlsplit

import httpx
from pydantic import AwareDatetime, Field, ValidationError, model_validator

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

SOURCE_ID: Final = "reddit_data"
API_HOST: Final = "oauth.reddit.com"
MAX_LIMIT: Final = 100  # reported documented maximum of `limit`; NOT VERIFIED
DEFAULT_LIMIT: Final = 25  # self-imposed (and the reported default)
# Self-imposed, far below the reported 100 queries a minute.
DEFAULT_REQUEST_BUDGET: Final = 20
# Reported recommendation: delete stored content within 48 hours. Applied as a hard
# limit on what a reading may use. NOT VERIFIED.
RETENTION_HOURS: Final = 48
_CLOCK_TOLERANCE: Final = timedelta(hours=1)
_POST_KIND: Final = "t3"

REDDIT_DATA_POLICY = SourcePolicy(
    source=SOURCE_ID,
    raw_storage_allowed=Permission.RESTRICTED,
    retention_days=2,
    historical_storage_allowed=Permission.RESTRICTED,
    # A theme or a count drawn from users' posts is derived from their content. Not
    # established as allowed for this use.
    derived_metrics_allowed=Permission.UNKNOWN,
    commercial_use_allowed=Permission.UNKNOWN,
    redistribution_allowed=Permission.RESTRICTED,
    attribution_required=Requirement.UNKNOWN,
    # Reported: every application needs Reddit's approval before it may pull data,
    # non-commercial ones included. Not requested; not denied.
    approval_required=Requirement.REQUIRED,
    approval_status=ApprovalStatus.PENDING,
    commercial_approval_required=Requirement.REQUIRED,
    commercial_approval_status=ApprovalStatus.PENDING,
    stage_clearance=(
        StageClearance(
            stage=DeploymentStage.PRIVATE_DEVELOPMENT,
            cleared=True,
            reason=(
                "Parsing Reddit-shaped responses offline. No account, app, token or "
                "request exists and no Reddit data is held: every response in the "
                "repository is synthetic. Because access is not approved, real Reddit "
                "data is refused at every stage, this one included."
            ),
        ),
        StageClearance(
            stage=DeploymentStage.PRIVATE_COLLECTION,
            cleared=False,
            reason=(
                "Not cleared. Reported to need an approved application under Reddit's "
                "Responsible Builder Policy, a deletion process for content removed on "
                "Reddit, and short retention. None exists, and the terms have not been "
                "read from Reddit's own pages."
            ),
        ),
        StageClearance(
            stage=DeploymentStage.PUBLIC_LAUNCH,
            cleared=False,
            reason=(
                "Not cleared. Showing anything derived from users' posts needs Reddit's "
                "approval for that use and an established basis for derived output. "
                "Being noncommercial does not grant either."
            ),
        ),
        StageClearance(
            stage=DeploymentStage.COMMERCIAL_PRODUCTION,
            cleared=False,
            reason="Not cleared. Commercial use is reported to need a separate agreement.",
        ),
    ),
    operations=SourceOperations(
        budget_group="reddit_data_api",
        rate_limit=RateLimit(
            max_requests=1,
            per_seconds=1,
            max_concurrency=1,
            basis=LimitBasis.SELF_IMPOSED,
            note="Reported limit: 100 queries a minute per OAuth client. NOT VERIFIED.",
        ),
        quota=QuotaBudget(
            limit=DEFAULT_REQUEST_BUDGET,
            unit="requests per run",
            period=QuotaPeriod.DAY,
            basis=LimitBasis.SELF_IMPOSED,
            note="A local run budget. Not Reddit's quota, which is per minute.",
        ),
        cache_max_age_seconds=RETENTION_HOURS * 3600,
        expected_publication_lag_seconds=0,
        publication_lag_basis=LimitBasis.SELF_IMPOSED,
    ),
    display=DisplayRules(
        link_back_required=Requirement.UNKNOWN,
        branding_required=Requirement.UNKNOWN,
        note="Not established: the display terms were not read from Reddit's pages.",
    ),
    reviewed_at=date(2026, 10, 14),
    references=(
        "https://www.reddit.com/dev/api/",
        "https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki",
        "https://redditinc.com/policies/data-api-terms",
        "https://redditinc.com/policies/developer-terms",
    ),
    notes=(
        "None of the references could be fetched on 2026-10-14 (403 or blocked). The "
        "policy is built from secondary descriptions and is deliberately restrictive. "
        "Every figure in it must be confirmed from Reddit's own pages."
    ),
)


class FetchStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"  # the source said: not now
    NOT_SENT = "not_sent"  # the local budget refused


class CommunityScope(StrEnum):
    """What the query's subreddit is, as DECLARED by whoever wrote the query plan."""

    DEDICATED = "dedicated"  # the game's own community
    GENERAL_GAMING = "general_gaming"  # a community about games in general
    SITE_WIDE = "site_wide"  # no subreddit: a search of all of Reddit


def digest(prefix: str, *parts: object) -> str:
    canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}_{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:32]}"


class CommunityQuery(FrozenModel):
    """One bounded question, asked on behalf of one canonical game, and why."""

    canonical_game_id: GameId
    text: NonEmptyStr
    scope: CommunityScope
    # Required for a dedicated or general scope; absent for a site-wide search.
    subreddit: str | None = None
    # Subreddits the plan declares as NOT about this game (a programming language, a
    # film). A post from one is excluded, whatever its title says.
    unrelated_subreddits: tuple[str, ...] = ()
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    sort: Literal["new", "relevance"] = "new"
    window: Literal["day", "week", "month"] = "week"
    reason: NonEmptyStr = "community discussion of this game"

    @property
    def query_id(self) -> str:
        return digest(
            "rdq",
            self.canonical_game_id,
            self.text,
            self.scope,
            self.subreddit,
            self.unrelated_subreddits,
            self.limit,
            self.sort,
            self.window,
        )

    def path(self) -> str:
        return "/search" if self.subreddit is None else f"/r/{self.subreddit}/search"

    def parameters(self, after: str | None) -> dict[str, str | int]:
        parameters: dict[str, str | int] = {
            "q": self.text,
            "sort": self.sort,
            "t": self.window,
            "limit": self.limit,
            "type": "link",
            "raw_json": 1,
        }
        if self.subreddit is not None:
            parameters["restrict_sr"] = 1
        if after is not None:
            parameters["after"] = after
        return parameters

    @model_validator(mode="after")
    def _scope_names_its_subreddit(self) -> Self:
        if (self.scope is CommunityScope.SITE_WIDE) != (self.subreddit is None):
            raise ValueError("a site-wide query names no subreddit, and only it")
        return self


class RedditPost(FrozenModel):
    """One post a listing returned, as observed at one retrieval."""

    post_id: NonEmptyStr  # the source's own id (the fullname without "t3_")
    subreddit: NonEmptyStr
    title: NonEmptyStr
    permalink: NonEmptyStr
    created_at: AwareDatetime
    # Each is None when the response did not carry it. None is never zero.
    score: int | None = None
    num_comments: int | None = Field(default=None, ge=0)
    flair: str | None = None
    # Whether the post's link pointed at the reserved `.invalid` domain.
    reserved_link: bool = False


class ListingFetch(FrozenModel):
    """One listing request and what came of it. One page."""

    fetch_id: str
    run_id: NonEmptyStr
    query: CommunityQuery
    page_index: int = Field(ge=0)
    after: str | None = None
    retrieved_at: AwareDatetime
    status: FetchStatus
    error_code: SourceErrorCode | None = None
    http_status: int | None = None
    stopped_by: str | None = None
    posts: tuple[RedditPost, ...] = ()
    rejected: int = Field(default=0, ge=0)
    next_after: str | None = None
    # The rate-limit headers as the response gave them. None when absent.
    ratelimit_remaining: float | None = None
    ratelimit_reset_seconds: float | None = None
    requests_spent: int = Field(default=0, ge=0)


class InvalidListing(Exception):
    """A body was not a Listing."""


def _number(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("not an integer")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("not text")
    return value


@dataclass(frozen=True)
class ParsedListing:
    posts: tuple[RedditPost, ...]
    rejected: int
    after: str | None


def parse_listing(body: object, retrieved_at: datetime) -> ParsedListing:
    """Read a Listing of posts. Anything that is not a readable post is counted."""
    if not isinstance(body, dict) or body.get("kind") != "Listing":
        raise InvalidListing("not a Listing")
    data = body.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("children"), list):
        raise InvalidListing("a Listing without children")
    posts: list[RedditPost] = []
    rejected = 0
    for child in data["children"]:
        try:
            if child.get("kind") != _POST_KIND:
                raise ValueError("not a post")
            post = child["data"]
            created = post["created_utc"]
            if isinstance(created, bool) or not isinstance(created, int | float):
                raise ValueError("no creation time")
            created_at = datetime.fromtimestamp(created, UTC)
            if created_at > retrieved_at + _CLOCK_TOLERANCE:
                raise ValueError("created after it was retrieved")
            flair = post.get("link_flair_text")
            link = post.get("url")
            host = urlsplit(link).hostname or "" if isinstance(link, str) else ""
            posts.append(
                RedditPost(
                    post_id=_text(post["id"]),
                    subreddit=_text(post["subreddit"]),
                    title=_text(post["title"]),
                    permalink=_text(post["permalink"]),
                    created_at=created_at,
                    score=_number(post.get("score")),
                    num_comments=_number(post.get("num_comments")),
                    flair=flair if isinstance(flair, str) and flair.strip() else None,
                    reserved_link=host == "invalid" or host.endswith(".invalid"),
                )
            )
        except (KeyError, TypeError, AttributeError, ValueError, ValidationError, OverflowError):
            rejected += 1
    after = data.get("after")
    return ParsedListing(
        posts=tuple(posts),
        rejected=rejected,
        after=after if isinstance(after, str) and after else None,
    )


def _header(headers: Mapping[str, str], name: str) -> float | None:
    value = headers.get(name)
    try:
        return None if value is None else float(value)
    except ValueError:
        return None


class RequestBudget:
    """A local, per-run request budget. OFFLINE ONLY; not Reddit's rate limit.

    Spent before each request. Once a response says no requests remain, or the source
    answers 429, nothing further is sent on this budget.
    """

    def __init__(self, requests: int = DEFAULT_REQUEST_BUDGET) -> None:
        if not 0 <= requests <= DEFAULT_REQUEST_BUDGET:
            raise ValueError(f"a run budget is between 0 and {DEFAULT_REQUEST_BUDGET} requests")
        self.limit = requests
        self.used = 0
        self.halted_by_source = False

    @property
    def left(self) -> int:
        return 0 if self.halted_by_source else self.limit - self.used

    def take(self) -> bool:
        if self.left < 1:
            return False
        self.used += 1
        return True


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RedditAdapter:
    """Listing searches over a client it is handed. One attempt each. Replay only today."""

    def __init__(
        self,
        client: httpx.Client,
        budget: RequestBudget,
        *,
        run_id: str,
        now: Callable[[], datetime] = _utc_now,
        token: Secret | None = None,
    ) -> None:
        self._client = client
        self._budget = budget
        self._run_id = run_id
        self._now = now
        self._token = token

    @property
    def source(self) -> str:
        return SOURCE_ID

    @property
    def budget(self) -> RequestBudget:
        return self._budget

    def search(self, query: CommunityQuery, page_index: int, after: str | None) -> ListingFetch:
        retrieved_at = self._now()
        parameters = query.parameters(after)
        url = f"https://{API_HOST}{query.path()}"
        base: dict[str, Any] = {
            "fetch_id": digest("rdf", url, parameters, retrieved_at, self._run_id),
            "run_id": self._run_id,
            "query": query,
            "page_index": page_index,
            "after": after,
            "retrieved_at": retrieved_at,
        }
        if not self._budget.take():
            return ListingFetch(
                **base, status=FetchStatus.NOT_SENT, error_code=SourceErrorCode.QUOTA_EXHAUSTED
            )
        base["requests_spent"] = 1
        headers = {"User-Agent": "metaspike-offline/1 (replay only)"}
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token.reveal()}"
        try:
            response = self._client.get(url, params=parameters, headers=headers)
        except httpx.HTTPError:
            return ListingFetch(
                **base, status=FetchStatus.FAILED, error_code=SourceErrorCode.NETWORK_ERROR
            )
        remaining = _header(response.headers, "x-ratelimit-remaining")
        reset = _header(response.headers, "x-ratelimit-reset")
        base.update(ratelimit_remaining=remaining, ratelimit_reset_seconds=reset)
        if remaining is not None and remaining <= 0:
            self._budget.halted_by_source = True
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            self._budget.halted_by_source = True
            return ListingFetch(
                **base,
                status=FetchStatus.RATE_LIMITED,
                error_code=SourceErrorCode.RATE_LIMITED,
                http_status=response.status_code,
            )
        if response.status_code != httpx.codes.OK:
            code = {
                401: SourceErrorCode.UNAUTHORIZED,
                403: SourceErrorCode.UNAUTHORIZED,
                404: SourceErrorCode.NOT_FOUND,
            }.get(response.status_code, SourceErrorCode.HTTP_ERROR)
            return ListingFetch(
                **base, status=FetchStatus.FAILED, error_code=code, http_status=response.status_code
            )
        try:
            parsed = parse_listing(response.json(), retrieved_at)
        except (InvalidListing, ValueError):
            return ListingFetch(
                **base,
                status=FetchStatus.FAILED,
                error_code=SourceErrorCode.INVALID_RESPONSE,
                http_status=response.status_code,
            )
        return ListingFetch(
            **base,
            status=FetchStatus.OK,
            http_status=response.status_code,
            posts=parsed.posts,
            rejected=parsed.rejected,
            next_after=parsed.after,
        )


def replay_reddit_client(recordings: Path) -> httpx.Client:
    """Serve recorded-shaped answers. No request leaves the machine.

    Each file holds `{"request": {"path", "q", "after"?}, "status", "headers"?, "body"}`
    and answers the request with the same path, `q` and `after`. A file's name plays no
    part. An unrecorded request gets a transport error: never an invented empty listing.
    """
    answers: dict[tuple[str, str, str], tuple[int, dict[str, str], object]] = {}
    for path in sorted(recordings.glob("*.json")):
        recorded = json.loads(path.read_text(encoding="utf-8"))
        request = recorded["request"]
        key = (request["path"], request["q"], request.get("after", ""))
        if key in answers:
            raise ValueError(f"{path.name}: a second recording answers the same request")
        answers[key] = (recorded["status"], recorded.get("headers", {}), recorded["body"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != API_HOST:
            raise httpx.ConnectError("replay: unexpected host", request=request)
        params = request.url.params
        key = (request.url.path, params.get("q", ""), params.get("after", ""))
        if key not in answers:
            raise httpx.ConnectError("replay: no recording for this request", request=request)
        status, headers, body = answers[key]
        return httpx.Response(status, json=body, headers=headers)

    return httpx.Client(transport=httpx.MockTransport(handler))
