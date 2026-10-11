"""The event sources MetaSpike knows, each with the policy it runs under.

A source that is not here has no policy, and a source with no policy is not used: its
observations are refused at ingestion and again where artifacts are built.
"""

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

import httpx

from metaspike.domain.source_policy import DeploymentStage, SourcePolicy, SourcePurpose
from metaspike.event_sources.steam_news import (
    DEFAULT_COUNT,
    NEWS_RETRY,
    PUBLIC_HOST,
    SOURCE_ID,
    STEAM_NEWS_POLICY,
    SteamNewsAdapter,
)
from metaspike.sources.http import AttemptBudget, LimitedHttpClient
from metaspike.sources.limiter import LimiterPool

EVENT_SOURCE_POLICIES: Mapping[str, SourcePolicy] = {SOURCE_ID: STEAM_NEWS_POLICY}


def event_source_refusal(
    source: str, stage: DeploymentStage, policies: Mapping[str, SourcePolicy] | None = None
) -> str | None:
    """Why this source's events may not be used at `stage`, or None if they may."""
    known = EVENT_SOURCE_POLICIES if policies is None else policies
    policy = known.get(source)
    if policy is None:
        return "no source policy is registered for this event source"
    return policy.registration_refusal(SourcePurpose.EVENT_INTELLIGENCE, stage)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def build_steam_news(
    client: httpx.Client,
    pool: LimiterPool,
    *,
    max_requests: int,
    now: Callable[[], datetime] = _utc_now,
    count: int = DEFAULT_COUNT,
) -> SteamNewsAdapter:
    """The adapter behind the Steam budget group's limiter and a hard request budget.

    `max_requests` is the most outbound requests the adapter can ever send, counted
    below the retry policy. No credential is read: the method takes none.
    """
    http = LimitedHttpClient(
        client,
        pool.for_policy(STEAM_NEWS_POLICY),
        NEWS_RETRY,
        budget=AttemptBudget(max_requests),
    )
    return SteamNewsAdapter(http, now, count)


def replay_news_client(recordings: Path) -> httpx.Client:
    """Serve recorded news responses from a directory. No request leaves the machine.

    A recording is `news_<appid>.json` holding `{"status": int, "body": ...}`, or
    `news_<appid>_before_<enddate>.json` for a page of a backfill. An app with no
    recording gets a transport error, which is a failed fetch: an unrecorded app is
    never answered with an invented empty feed.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != PUBLIC_HOST:
            raise httpx.ConnectError("replay: unexpected host", request=request)
        app_id = request.url.params.get("appid", "")
        end = request.url.params.get("enddate")
        name = f"news_{app_id}.json" if end is None else f"news_{app_id}_before_{end}.json"
        recording = recordings / name
        if not app_id.isdigit() or not recording.is_file():
            raise httpx.ConnectError("replay: no recording for this request", request=request)
        recorded = json.loads(recording.read_text(encoding="utf-8"))
        return httpx.Response(recorded["status"], json=recorded["body"])

    return httpx.Client(transport=httpx.MockTransport(handler))
