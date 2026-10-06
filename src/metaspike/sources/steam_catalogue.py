"""Steam catalogue adapter: the list of apps on Steam, and nothing else.

Docs: https://partner.steamgames.com/doc/webapi/IStoreService  (reviewed 2026-10-04)

Uses IStoreService/GetAppList/v1. The older ISteamApps/GetAppList/v2 has been removed
by Valve and must not be used. This adapter does not collect player counts, prices,
reviews or store pages.

WHAT IS AND IS NOT DOCUMENTED
The request parameters are documented and used exactly as documented. The response
body is NOT documented on the official page. An authenticated call on 2026-10-05
returned an envelope of `apps`, `have_more_results` and `last_appid`, and apps with
`appid`, `name`, `last_modified` and `price_change_number`. Because that shape is
observed rather than promised, only `appid` is required; every other field is read
if present, and `SteamCataloguePage.observed_app_fields` reports what was seen.

A normal user Web API key is accepted on the public host.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

import httpx

from metaspike.domain.identity import SourceRecordKey
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
from metaspike.domain.source_records import ProductType, SourceRecord
from metaspike.sources.base import SourceError
from metaspike.sources.credentials import Secret
from metaspike.sources.http import LimitedHttpClient

SOURCE_ID = "steam_catalogue"
# One Web API key has one daily allowance across every Steam endpoint, so every Steam
# source must share this budget group.
STEAM_BUDGET_GROUP = "steam_web_api"
# The public host. The partner host shown in the docs' example requires a publisher key.
APP_LIST_URL = "https://api.steampowered.com/IStoreService/GetAppList/v1/"
DEFAULT_PAGE_SIZE = 10_000  # documented default
MAX_PAGE_SIZE = 50_000  # documented maximum

STEAM_CATALOGUE_POLICY = SourcePolicy(
    source=SOURCE_ID,
    raw_storage_allowed=Permission.RESTRICTED,
    retention_days=None,
    historical_storage_allowed=Permission.RESTRICTED,
    derived_metrics_allowed=Permission.UNKNOWN,
    commercial_use_allowed=Permission.UNKNOWN,
    redistribution_allowed=Permission.RESTRICTED,
    attribution_required=Requirement.REQUIRED,
    attribution_text="Steam data provided by Valve via the Steam Web API.",
    approval_required=Requirement.NOT_REQUIRED,
    approval_status=ApprovalStatus.NOT_REQUIRED,
    commercial_approval_required=Requirement.UNKNOWN,
    commercial_approval_status=ApprovalStatus.PENDING,
    stage_clearance=(
        StageClearance(
            stage=DeploymentStage.PRIVATE_DEVELOPMENT,
            cleared=True,
            reason=(
                "A Web API key is available to any Steam account for an application the "
                "holder owns and operates; nothing in the terms prevents private testing."
            ),
        ),
        StageClearance(
            stage=DeploymentStage.PRIVATE_COLLECTION,
            cleared=False,
            reason=(
                "The terms require a privacy policy that names the country where Steam "
                "Data is stored. Choose the storage region and write that policy first."
            ),
        ),
        StageClearance(
            stage=DeploymentStage.PUBLIC_LAUNCH,
            cleared=False,
            reason=(
                "Pages showing Steam Data must carry the Valve name, logo and followable "
                "links, 'as is' disclaimers and a privacy policy; the branding guidance "
                "the terms refer to has not been located."
            ),
        ),
        StageClearance(
            stage=DeploymentStage.COMMERCIAL_PRODUCTION,
            cleared=False,
            reason=(
                "The Steam Web API Terms of Use (July 2010) do not address commercial "
                "use. They incorporate the Steam Subscriber Agreement, whose section 2 "
                "restricts commercial exploitation of Content and Services and whose "
                "section 4.C restricts automation broadly; see steam_current_players."
            ),
        ),
    ),
    operations=SourceOperations(
        budget_group=STEAM_BUDGET_GROUP,
        rate_limit=RateLimit(
            max_requests=1,
            per_seconds=1,
            max_concurrency=1,
            basis=LimitBasis.SELF_IMPOSED,
            note="Steam documents no short-window limit; one request per second is ours.",
        ),
        quota=QuotaBudget(
            limit=100_000,
            unit="calls",
            period=QuotaPeriod.DAY,
            basis=LimitBasis.DOCUMENTED,
            note="Per key, across all Steam Web API endpoints. Day boundary undocumented.",
        ),
        cache_max_age_seconds=None,
        expected_publication_lag_seconds=0,
        publication_lag_basis=LimitBasis.SELF_IMPOSED,
    ),
    display=DisplayRules(
        link_back_required=Requirement.REQUIRED,
        branding_required=Requirement.REQUIRED,
        note=(
            "Valve name, logo and links on any page incorporating Steam Data, without "
            "'nofollow'. Must not appear endorsed by or affiliated with Valve, nor "
            "available from a third party. Data is provided 'as is'."
        ),
    ),
    reviewed_at=date(2026, 10, 6),
    references=(
        "https://steamcommunity.com/dev/apiterms",
        "https://partner.steamgames.com/doc/webapi/IStoreService",
        "https://partner.steamgames.com/doc/webapi/ISteamApps",
        "https://partner.steamgames.com/doc/webapi_overview",
    ),
    notes=(
        "Licence: distribute Steam Data to end users for their personal use via an "
        "application you own and operate. Storage is contemplated only alongside a "
        "privacy policy. Derived metrics and commercial use are not addressed. The API "
        "key must be kept confidential and travels in the request URL."
    ),
)


class SteamAppKind(StrEnum):
    """The product filters GetAppList documents. One kind is requested at a time."""

    GAMES = "games"
    DLC = "dlc"
    SOFTWARE = "software"
    VIDEOS = "videos"
    HARDWARE = "hardware"


# What a kind lets us say about a record. A demo or playtest may be listed under
# "games" for all the documentation tells us, so that kind alone proves nothing.
_PRODUCT_TYPE_FOR_KIND = {
    SteamAppKind.GAMES: ProductType.UNKNOWN,
    SteamAppKind.DLC: ProductType.DLC,
    SteamAppKind.SOFTWARE: ProductType.SOFTWARE,
    SteamAppKind.VIDEOS: ProductType.VIDEO,
    SteamAppKind.HARDWARE: ProductType.HARDWARE,
}


@dataclass(frozen=True)
class SteamCataloguePage:
    kind: SteamAppKind
    records: tuple[SourceRecord, ...]
    # The response's own app rows (app id, name and timestamps; no authentication data).
    raw_apps: tuple[dict[str, Any], ...]
    # Field names actually present in the response, for checking against assumptions.
    observed_envelope_fields: frozenset[str]
    observed_app_fields: frozenset[str]
    apps_returned: int
    apps_without_a_name: int
    # None when the response does not say; callers then stop on a short page.
    has_more: bool | None
    next_after_appid: int | None


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SteamCatalogueAdapter:
    def __init__(
        self,
        http: LimitedHttpClient,
        api_key: Secret,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._http = http
        self._api_key = api_key
        self._now = now

    @property
    def source(self) -> str:
        return SOURCE_ID

    @property
    def description(self) -> str:
        return "The list of apps on Steam (app id and name) from the Steam Web API."

    def fetch_page(
        self,
        kind: SteamAppKind,
        *,
        modified_since: datetime | None = None,
        after_appid: int | None = None,
        max_results: int = DEFAULT_PAGE_SIZE,
    ) -> SteamCataloguePage:
        if not 1 <= max_results <= MAX_PAGE_SIZE:
            raise ValueError(f"max_results must be between 1 and {MAX_PAGE_SIZE}")
        params: dict[str, str | int] = {"key": self._api_key.reveal(), "max_results": max_results}
        params.update({f"include_{each.value}": _flag(each is kind) for each in SteamAppKind})
        if modified_since is not None:
            params["if_modified_since"] = int(modified_since.timestamp())
        if after_appid is not None:
            params["last_appid"] = after_appid

        label = f"steam app list ({kind.value})"
        response = self._http.request("GET", APP_LIST_URL, label=label, params=params)
        if response.status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
            raise SourceError(
                SourceErrorCode.UNAUTHORIZED,
                f"{label}: API key rejected (HTTP {response.status_code})",
            )
        if response.status_code != httpx.codes.OK:
            raise SourceError(SourceErrorCode.HTTP_ERROR, f"{label}: HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise SourceError(SourceErrorCode.INVALID_RESPONSE, f"{label}: not JSON") from None
        return self._page(kind, body, label)

    def iter_apps(
        self,
        kind: SteamAppKind,
        *,
        modified_since: datetime | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_pages: int = 100,
    ) -> Iterator[SourceRecord]:
        """Every app of one kind, optionally only those modified since a moment."""
        after: int | None = None
        for _ in range(max_pages):
            page = self.fetch_page(
                kind, modified_since=modified_since, after_appid=after, max_results=page_size
            )
            if after is not None and page.next_after_appid == after:
                return  # the cursor did not advance: this page repeats the previous one
            yield from page.records
            more = page.has_more if page.has_more is not None else page.apps_returned >= page_size
            if not more or page.next_after_appid is None:
                return
            after = page.next_after_appid
        raise SourceError(
            SourceErrorCode.INVALID_RESPONSE,
            f"steam app list ({kind.value}): pagination did not finish in {max_pages} pages",
        )

    def _page(self, kind: SteamAppKind, body: Any, label: str) -> SteamCataloguePage:
        if not isinstance(body, dict):
            raise SourceError(SourceErrorCode.INVALID_RESPONSE, f"{label}: expected an object")
        envelope = body.get("response", body)
        if not isinstance(envelope, dict):
            raise SourceError(SourceErrorCode.INVALID_RESPONSE, f"{label}: expected an object")
        apps = envelope.get("apps", [])
        if not isinstance(apps, list) or any(not isinstance(app, dict) for app in apps):
            raise SourceError(SourceErrorCode.INVALID_RESPONSE, f"{label}: 'apps' is not a list")

        fetched_at = self._now()
        records: list[SourceRecord] = []
        app_ids: list[int] = []
        for app in apps:
            try:
                app_ids.append(app_id_of(app))
            except ValueError:
                raise SourceError(
                    SourceErrorCode.INVALID_RESPONSE, f"{label}: an app has no usable appid"
                ) from None
            parsed = parse_app_row(app, kind, fetched_at)
            if parsed is not None:
                records.append(parsed)
        nameless = len(apps) - len(records)

        more = envelope.get("have_more_results")
        cursor = envelope.get("last_appid")
        if isinstance(cursor, bool) or not isinstance(cursor, int):
            cursor = max(app_ids, default=None)
        return SteamCataloguePage(
            kind=kind,
            records=tuple(records),
            raw_apps=tuple(apps),
            observed_envelope_fields=frozenset(envelope),
            observed_app_fields=frozenset(field for app in apps for field in app),
            apps_returned=len(apps),
            apps_without_a_name=nameless,
            has_more=more if isinstance(more, bool) else None,
            next_after_appid=cursor,
        )


def app_id_of(app: dict[str, Any]) -> int:
    app_id = app.get("appid")
    if isinstance(app_id, bool) or not isinstance(app_id, int) or app_id <= 0:
        raise ValueError("app has no usable appid")
    return app_id


def parse_app_row(
    app: dict[str, Any], kind: SteamAppKind, fetched_at: datetime
) -> SourceRecord | None:
    """One app of the list as a source-native record, or None if it has no name."""
    name = app.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    modified = app.get("last_modified")
    return SourceRecord(
        key=SourceRecordKey(source=SOURCE_ID, source_id=str(app_id_of(app))),
        title=name.strip(),
        product_type=_PRODUCT_TYPE_FOR_KIND[kind],
        source_category=kind.value,
        source_updated_at=(
            datetime.fromtimestamp(modified, UTC)
            if isinstance(modified, int) and not isinstance(modified, bool)
            else None
        ),
        fetched_at=fetched_at,
    )


def _flag(value: bool) -> str:
    return "true" if value else "false"
