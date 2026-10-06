"""Wikimedia Pageviews adapter (Analytics API, per-article daily endpoint).

Docs: https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/
"""

import logging
import os
import time
from collections.abc import Callable
from datetime import date, datetime
from typing import Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from metaspike import __version__
from metaspike.domain.games import CanonicalGame, SourceIdentifier
from metaspike.domain.metrics import PAGEVIEWS
from metaspike.domain.observations import Freshness, Observation, ObservationStatus
from metaspike.domain.signals import SignalSpec, SourceErrorCode
from metaspike.domain.source_policy import (
    ApprovalStatus,
    DeploymentStage,
    DisplayRules,
    FieldRule,
    LimitBasis,
    Permission,
    RateLimit,
    Requirement,
    SourceOperations,
    SourcePolicy,
    StageClearance,
)
from metaspike.domain.timeseries import Granularity, TimeWindow, start_of_day
from metaspike.sources.base import SourceError, SourceMode
from metaspike.sources.http import LimitedHttpClient, build_http_client
from metaspike.sources.http import RetryConfig as RetryConfig
from metaspike.sources.limiter import Clock, RateLimiter

logger = logging.getLogger(__name__)

SOURCE_ID = "wikimedia_pageviews"
ENGLISH_WIKIPEDIA = "en.wikipedia.org"
BASE_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
CONTACT_ENV_VAR = "METASPIKE_CONTACT"
DEFAULT_CONTACT = "https://krishnachittella.dev"
DAILY_PAGEVIEWS = SignalSpec(metric=PAGEVIEWS.metric_id, granularity=Granularity.DAILY)

# Assumptions recorded from the official documentation on the review date. The data
# licence (CC0 1.0) is stated by the Analytics API access policy; CC0 waives the
# rights that would otherwise restrict storage, derivation, commercial use and
# redistribution, and carries no attribution condition. We attribute anyway.
WIKIMEDIA_PAGEVIEWS_POLICY = SourcePolicy(
    source=SOURCE_ID,
    raw_storage_allowed=Permission.ALLOWED,
    retention_days=None,
    historical_storage_allowed=Permission.ALLOWED,
    derived_metrics_allowed=Permission.ALLOWED,
    commercial_use_allowed=Permission.ALLOWED,
    redistribution_allowed=Permission.ALLOWED,
    attribution_required=Requirement.NOT_REQUIRED,
    attribution_text="Pageview data from the Wikimedia Analytics API (CC0 1.0).",
    approval_required=Requirement.NOT_REQUIRED,
    approval_status=ApprovalStatus.NOT_REQUIRED,
    commercial_approval_required=Requirement.NOT_REQUIRED,
    commercial_approval_status=ApprovalStatus.NOT_REQUIRED,
    stage_clearance=tuple(
        StageClearance(stage=stage, cleared=True, reason="CC0 1.0 data with no usage condition.")
        for stage in DeploymentStage
    ),
    operations=SourceOperations(
        budget_group="wikimedia",
        rate_limit=RateLimit(
            max_requests=200,
            per_seconds=60,
            max_concurrency=1,
            basis=LimitBasis.DOCUMENTED,
            note=(
                "200 requests/minute for unauthenticated clients with a compliant "
                "User-Agent. The Analytics API asks clients to wait for each request to "
                "finish before sending another, hence a concurrency of 1."
            ),
        ),
        quota=None,
        cache_max_age_seconds=None,
        expected_publication_lag_seconds=24 * 60 * 60,
        publication_lag_basis=LimitBasis.OBSERVED,
    ),
    display=DisplayRules(
        link_back_required=Requirement.NOT_REQUIRED,
        branding_required=Requirement.NOT_REQUIRED,
        note=(
            "CC0 data carries no display condition. Wikimedia marks must not be used in a "
            "way that implies endorsement."
        ),
    ),
    field_rules=(
        FieldRule(
            field=PAGEVIEWS.metric_id,
            storage_allowed=Permission.ALLOWED,
            display_allowed=Permission.ALLOWED,
            note="Aggregate daily counts; no personal data.",
        ),
    ),
    reviewed_at=date(2026, 10, 4),
    references=(
        "https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/documentation/access-policy.html",
        "https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy",
        "https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use",
        "https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits",
    ),
    notes=(
        "API data is published under CC0 1.0. Use is subject to the Wikimedia Terms of Use: "
        "a descriptive User-Agent with contact information is mandatory, requests must be "
        "made one at a time, and clients that endanger service stability may be blocked. "
        "No publication schedule is documented for daily data: one hour after a day ended "
        "it was not yet available, so a 24-hour lag is allowed for until measured."
    ),
)


class _PageviewItem(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    timestamp: str = Field(pattern=r"^[0-9]{10}$")
    granularity: Literal["daily"]
    views: int = Field(ge=0)


class _PageviewResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    items: list[_PageviewItem] = Field(min_length=1)


def user_agent() -> str:
    """User-Agent in the format the Wikimedia User-Agent policy asks for."""
    contact = os.environ.get(CONTACT_ENV_VAR, DEFAULT_CONTACT)
    return f"MetaSpike/{__version__} ({contact}) httpx/{httpx.__version__}"


def build_client() -> httpx.Client:
    return build_http_client(user_agent())


class WikimediaPageviewsAdapter:
    def __init__(
        self,
        client: httpx.Client | LimitedHttpClient,
        retry: RetryConfig | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Take a limited client, or a bare transport that is wrapped in this source's limits."""
        if isinstance(client, LimitedHttpClient):
            self._http = client
        else:
            limiter = RateLimiter(WIKIMEDIA_PAGEVIEWS_POLICY.operations, clock=Clock(sleep))
            self._http = LimitedHttpClient(client, limiter, retry)

    @property
    def source(self) -> str:
        return SOURCE_ID

    @property
    def mode(self) -> SourceMode:
        return SourceMode.FETCH_HISTORY

    @property
    def signals(self) -> tuple[SignalSpec, ...]:
        return (DAILY_PAGEVIEWS,)

    @property
    def description(self) -> str:
        return (
            "Daily human (non-bot) pageviews of the game's English Wikipedia article, all "
            "access methods, from the Wikimedia Analytics API."
        )

    def fetch(
        self,
        game: CanonicalGame,
        identifier: SourceIdentifier,
        signal: SignalSpec,
        window: TimeWindow,
        observed_at: datetime,
    ) -> list[Observation]:
        if signal != DAILY_PAGEVIEWS:
            raise ValueError(f"{SOURCE_ID} does not provide {signal.metric}/{signal.granularity}")
        url = (
            f"{BASE_URL}/{identifier.namespace}/all-access/user/"
            f"{quote(identifier.value, safe='')}/daily/"
            f"{window.start:%Y%m%d}/{window.end:%Y%m%d}"
        )
        article = identifier.value
        response = self._http.request("GET", url, label=article)
        if response.status_code == httpx.codes.NOT_FOUND:
            raise SourceError(
                SourceErrorCode.NOT_FOUND,
                f"{article}: no pageview data for this article and date range",
            )
        if response.status_code != httpx.codes.OK:
            raise SourceError(SourceErrorCode.HTTP_ERROR, f"{article}: HTTP {response.status_code}")
        views_by_day = _parse(response.content, window, article)
        return [
            _observation(game, identifier, day, views, observed_at)
            for day, views in sorted(views_by_day.items())
        ]


def _parse(content: bytes, window: TimeWindow, article: str) -> dict[date, int]:
    try:
        payload = _PageviewResponse.model_validate_json(content)
    except ValidationError as exc:
        raise SourceError(
            SourceErrorCode.INVALID_RESPONSE,
            f"{article}: response did not match the documented shape "
            f"({exc.error_count()} problem(s))",
        ) from None
    views_by_day: dict[date, int] = {}
    for item in payload.items:
        try:
            day = datetime.strptime(item.timestamp, "%Y%m%d%H").date()
        except ValueError:
            raise SourceError(
                SourceErrorCode.INVALID_RESPONSE, f"{article}: unparseable timestamp"
            ) from None
        if not window.contains(start_of_day(day)):
            raise SourceError(
                SourceErrorCode.INVALID_RESPONSE, f"{article}: day outside the requested range"
            )
        if day in views_by_day:
            raise SourceError(SourceErrorCode.INVALID_RESPONSE, f"{article}: duplicate day")
        views_by_day[day] = item.views
    return views_by_day


def _observation(
    game: CanonicalGame,
    identifier: SourceIdentifier,
    day: date,
    views: int,
    observed_at: datetime,
) -> Observation:
    period_start = start_of_day(day)
    period_end = period_start + Granularity.DAILY.step
    return Observation(
        canonical_game_id=game.canonical_game_id,
        source=SOURCE_ID,
        metric=DAILY_PAGEVIEWS.metric,
        granularity=DAILY_PAGEVIEWS.granularity,
        period_start=period_start,
        value=float(views),
        status=ObservationStatus.OBSERVED,
        observed_at=observed_at,
        source_timestamp=period_start,
        freshness=Freshness(
            period_end=period_end,
            lag_hours=round((observed_at - period_end).total_seconds() / 3600, 2),
        ),
        reference=f"{identifier.namespace}/{identifier.value}",
    )
