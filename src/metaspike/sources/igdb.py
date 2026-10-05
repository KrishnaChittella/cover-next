"""IGDB catalogue adapter: identity-oriented metadata only.

Docs: https://api-docs.igdb.com/  (reviewed 2026-10-04)

Only the fields needed to identify a game and place it in time are requested: title,
alternative titles, product type, status, release dates, platforms, companies,
franchises, external ids, websites and parent links. Ratings, media, summaries and
everything else are deliberately not collected.

Field names follow the current API (`game_type`, `game_status`, `external_game_source`,
`date_format`, `release_region`); the deprecated `category` and `status` fields are
not used.
"""

import logging
import re
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, date, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.signals import SourceErrorCode
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
from metaspike.domain.source_records import (
    DatePrecision,
    ExternalId,
    ProductType,
    ReleaseDate,
    SourceRecord,
)
from metaspike.sources.base import SourceError
from metaspike.sources.http import LimitedHttpClient
from metaspike.sources.igdb_auth import IgdbAuth

logger = logging.getLogger(__name__)

SOURCE_ID = "igdb"
API_URL = "https://api.igdb.com/v4"
MAX_PAGE_SIZE = 500  # documented: "The maximum value you can set for limit is 500."
STEAM_EXTERNAL_SOURCE = "steam"

GAME_FIELDS = (
    "name",
    "updated_at",
    "game_type.type",
    "game_status.status",
    "alternative_names.name",
    "release_dates.date",
    "release_dates.date_format.format",
    "release_dates.platform.name",
    "release_dates.release_region.region",
    "release_dates.status.name",
    "platforms.name",
    "involved_companies.developer",
    "involved_companies.publisher",
    "involved_companies.company.name",
    "franchises.name",
    "external_games.uid",
    "external_games.external_game_source.name",
    "websites.url",
    "parent_game",
    "version_parent",
)

# Small lookup tables whose values name things in game records: endpoint -> field.
LOOKUP_TABLES = {
    "game_types": "type",
    "game_statuses": "status",
    "date_formats": "format",
    "external_game_sources": "name",
    "release_date_statuses": "name",
}

IGDB_POLICY = SourcePolicy(
    source=SOURCE_ID,
    raw_storage_allowed=Permission.ALLOWED,
    retention_days=None,
    historical_storage_allowed=Permission.ALLOWED,
    derived_metrics_allowed=Permission.UNKNOWN,
    commercial_use_allowed=Permission.RESTRICTED,
    redistribution_allowed=Permission.RESTRICTED,
    attribution_required=Requirement.REQUIRED,
    attribution_text="Game metadata from IGDB.com",
    approval_required=Requirement.NOT_REQUIRED,
    approval_status=ApprovalStatus.NOT_REQUIRED,
    commercial_approval_required=Requirement.REQUIRED,
    commercial_approval_status=ApprovalStatus.PENDING,
    stage_clearance=(
        StageClearance(
            stage=DeploymentStage.PRIVATE_DEVELOPMENT,
            cleared=True,
            reason=(
                "The API is free for non-commercial usage, and IGDB states that storing "
                "and caching the data locally is allowed."
            ),
        ),
        StageClearance(
            stage=DeploymentStage.PRIVATE_COLLECTION,
            cleared=False,
            reason=(
                "Confirm with IGDB whether its own 'store and serve the data' statement or "
                "the Twitch Developer Services Agreement's 24-hour storage limit governs "
                "unattended long-term storage."
            ),
        ),
        StageClearance(
            stage=DeploymentStage.PUBLIC_LAUNCH,
            cleared=False,
            reason=(
                "Needs static, user-visible IGDB attribution in the product, and IGDB's "
                "answer on whether an unmonetized but commercial-capable launch requires "
                "the partnership."
            ),
        ),
        StageClearance(
            stage=DeploymentStage.COMMERCIAL_PRODUCTION,
            cleared=False,
            reason="Monetized products require a commercial partnership (partner@igdb.com).",
        ),
    ),
    operations=SourceOperations(
        budget_group="igdb",
        rate_limit=RateLimit(
            max_requests=4,
            per_seconds=1,
            max_concurrency=8,
            basis=LimitBasis.DOCUMENTED,
            note="4 requests per second; up to 8 open requests; 429 when exceeded.",
        ),
        quota=None,
        cache_max_age_seconds=None,
        expected_publication_lag_seconds=0,
        publication_lag_basis=LimitBasis.SELF_IMPOSED,
    ),
    display=DisplayRules(
        link_back_required=Requirement.REQUIRED,
        branding_required=Requirement.UNKNOWN,
        note=(
            "Under the partnership IGDB asks for user-facing attribution to IGDB.com that "
            "is visible and in a static location, not in a change log."
        ),
    ),
    field_rules=(
        FieldRule(
            field="images",
            storage_allowed=Permission.UNKNOWN,
            display_allowed=Permission.UNKNOWN,
            note="Not collected. Removed images persist for 30 days; nothing else is stated.",
        ),
    ),
    reviewed_at=date(2026, 10, 4),
    references=(
        "https://api-docs.igdb.com/",
        "https://legal.twitch.com/legal/developer-agreement/",
    ),
    notes=(
        "Derived metrics are not addressed by IGDB; MetaSpike derives identity and "
        "lifecycle from this metadata, not metrics. Commercial use is offered only through "
        "a partnership. Serving data to one's own users is endorsed; redistributing it as a "
        "dataset is not addressed. The Twitch agreement restricts monetizing and storing "
        "'Twitch Data'; whether IGDB data is covered is unresolved."
    ),
)

# Vocabularies as the live API returned them on 2026-10-05, matched after
# normalisation. The documentation's older enum names are accepted too. Anything
# unrecognised is kept out rather than guessed.
_PRODUCT_TYPES = {
    "main_game": ProductType.MAIN_GAME,
    "dlc_addon": ProductType.DLC,
    "dlc": ProductType.DLC,
    "expansion": ProductType.EXPANSION,
    "bundle": ProductType.BUNDLE,
    "standalone_expansion": ProductType.STANDALONE_EXPANSION,
    "mod": ProductType.MOD,
    "episode": ProductType.EPISODE,
    "season": ProductType.SEASON,
    "remake": ProductType.REMAKE,
    "remaster": ProductType.REMASTER,
    "expanded_game": ProductType.EXPANDED_GAME,
    "port": ProductType.PORT,
    "fork": ProductType.FORK,
    "pack": ProductType.PACK,
    "pack_addon": ProductType.PACK,
    "update": ProductType.UPDATE,
}
_DATE_PRECISION = {
    "yyyymmdd": DatePrecision.DAY,
    "yyyymm": DatePrecision.MONTH,
    "yyyymmmmdd": DatePrecision.DAY,
    "yyyymmmm": DatePrecision.MONTH,
    "yyyy": DatePrecision.YEAR,
    "yyyyq1": DatePrecision.QUARTER,
    "yyyyq2": DatePrecision.QUARTER,
    "yyyyq3": DatePrecision.QUARTER,
    "yyyyq4": DatePrecision.QUARTER,
    "tbd": DatePrecision.TBD,
}


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Named(_Lenient):
    name: str | None = None


class _GameType(_Lenient):
    type: str | None = None


class _GameStatus(_Lenient):
    status: str | None = None


class _DateFormat(_Lenient):
    format: str | None = None


class _Region(_Lenient):
    region: str | None = None


class _ReleaseDate(_Lenient):
    date: int | None = None
    date_format: _DateFormat | None = None
    platform: _Named | None = None
    release_region: _Region | None = None
    status: _Named | None = None


class _InvolvedCompany(_Lenient):
    developer: bool = False
    publisher: bool = False
    company: _Named | None = None


class _ExternalGame(_Lenient):
    uid: str | None = None
    external_game_source: _Named | None = None
    game: int | None = None


class _Website(_Lenient):
    url: str | None = None


class _Game(_Lenient):
    id: int
    name: str
    updated_at: int | None = None
    game_type: _GameType | None = None
    game_status: _GameStatus | None = None
    alternative_names: list[_Named] = []
    release_dates: list[_ReleaseDate] = []
    platforms: list[_Named] = []
    involved_companies: list[_InvolvedCompany] = []
    franchises: list[_Named] = []
    external_games: list[_ExternalGame] = []
    websites: list[_Website] = []
    parent_game: int | None = None
    version_parent: int | None = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _token(value: str) -> str:
    """Lowercase, with every run of non-alphanumerics collapsed to one underscore."""
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


class IgdbCatalogueAdapter:
    def __init__(
        self,
        http: LimitedHttpClient,
        auth: IgdbAuth,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._http = http
        self._auth = auth
        self._now = now

    @property
    def source(self) -> str:
        return SOURCE_ID

    @property
    def description(self) -> str:
        return (
            "Game identity metadata from IGDB: titles, product type, release dates, "
            "platforms, companies, external ids and parent links."
        )

    def games_by_ids(self, ids: Iterable[int]) -> list[SourceRecord]:
        wanted = sorted(set(ids))
        records: list[SourceRecord] = []
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            chunk = wanted[start : start + MAX_PAGE_SIZE]
            where = f"where id = ({','.join(str(i) for i in chunk)})"
            records.extend(self._games(f"{where}; limit {MAX_PAGE_SIZE};"))
        return records

    def search_games(self, title: str, limit: int = 50) -> list[SourceRecord]:
        """IGDB's own text search. Candidates only: a hit is not evidence of identity."""
        text = re.sub(r'["\\;]', " ", title).strip()
        if not text:
            return []
        return self._games(f'search "{text}"; limit {min(limit, MAX_PAGE_SIZE)};')

    def games_for_steam_apps(self, app_ids: Iterable[int]) -> list[SourceRecord]:
        """Games that IGDB itself links to the given Steam app ids."""
        wanted = sorted(set(app_ids))
        game_ids: set[int] = set()
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            chunk = wanted[start : start + MAX_PAGE_SIZE]
            uids = ",".join(f'"{app_id}"' for app_id in chunk)
            rows = self._query(
                "external_games",
                f"fields uid,game,external_game_source.name; where uid = ({uids}); "
                f"limit {MAX_PAGE_SIZE};",
            )
            for row in rows:
                link = _validated(_ExternalGame, row)
                source = link.external_game_source
                if (
                    link.game is not None
                    and source is not None
                    and source.name is not None
                    and _token(source.name) == STEAM_EXTERNAL_SOURCE
                ):
                    game_ids.add(link.game)
        return self.games_by_ids(game_ids)

    def games_updated_since(self, moment: datetime, max_pages: int = 20) -> Iterator[SourceRecord]:
        """Games IGDB changed after `moment`, oldest change first, one page at a time."""
        since = int(moment.timestamp())
        for page in range(max_pages):
            batch = self._games(
                f"where updated_at > {since}; sort updated_at asc; "
                f"limit {MAX_PAGE_SIZE}; offset {page * MAX_PAGE_SIZE};"
            )
            yield from batch
            if len(batch) < MAX_PAGE_SIZE:
                return

    def vocabulary(self, endpoint: str) -> tuple[str, ...]:
        """The values of one documented lookup table, e.g. every game type IGDB uses."""
        field = LOOKUP_TABLES[endpoint]
        rows = self._query(endpoint, f"fields {field}; limit {MAX_PAGE_SIZE};")
        return tuple(
            sorted({str(row[field]) for row in rows if isinstance(row, dict) and row.get(field)})
        )

    def game_rows(self, clauses: str) -> list[Any]:
        """The API's own rows for a games query, restricted to the identity fields."""
        return self._query("games", f"fields {','.join(GAME_FIELDS)}; {clauses}")

    def _games(self, clauses: str) -> list[SourceRecord]:
        fetched_at = self._now()
        return [parse_game_row(row, fetched_at) for row in self.game_rows(clauses)]

    def _query(self, endpoint: str, body: str) -> list[Any]:
        label = f"igdb {endpoint}"
        headers, token = self._auth.headers()
        response = self._post(endpoint, body, headers, label)
        if response.status_code == httpx.codes.UNAUTHORIZED:
            # One refresh and one retry: a second rejection is a real credential problem.
            self._auth.invalidate(token)
            headers, _ = self._auth.headers()
            response = self._post(endpoint, body, headers, label)
        if response.status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
            raise SourceError(
                SourceErrorCode.UNAUTHORIZED,
                f"{label}: credentials rejected (HTTP {response.status_code})",
            )
        if response.status_code != httpx.codes.OK:
            raise SourceError(SourceErrorCode.HTTP_ERROR, f"{label}: HTTP {response.status_code}")
        try:
            rows = response.json()
        except ValueError:
            raise SourceError(SourceErrorCode.INVALID_RESPONSE, f"{label}: not JSON") from None
        if not isinstance(rows, list):
            raise SourceError(SourceErrorCode.INVALID_RESPONSE, f"{label}: expected a list")
        return rows

    def _post(
        self, endpoint: str, body: str, headers: dict[str, str], label: str
    ) -> httpx.Response:
        return self._http.request(
            "POST", f"{API_URL}/{endpoint}", label=label, headers=headers, content=body
        )


def _validated[ModelT: BaseModel](model: type[ModelT], row: Any) -> ModelT:
    try:
        return model.model_validate(row)
    except ValidationError as error:
        raise SourceError(
            SourceErrorCode.INVALID_RESPONSE,
            f"igdb: record did not match the documented shape ({error.error_count()} problem(s))",
        ) from None


def parse_game_row(row: Any, fetched_at: datetime) -> SourceRecord:
    """One row of the games endpoint as a source-native record."""
    return _record(_validated(_Game, row), fetched_at)


def _record(game: _Game, fetched_at: datetime) -> SourceRecord:
    type_name = game.game_type.type if game.game_type and game.game_type.type else ""
    product_type = _PRODUCT_TYPES.get(_token(type_name), ProductType.UNKNOWN)
    if product_type is ProductType.UNKNOWN:
        logger.info("igdb %d: unrecognised game type %r", game.id, type_name)
    status = game.game_status.status if game.game_status else None
    companies = [
        (company.company.name, company.developer, company.publisher)
        for company in game.involved_companies
        if company.company is not None and company.company.name
    ]
    return SourceRecord(
        key=SourceRecordKey(source=SOURCE_ID, source_id=str(game.id)),
        title=game.name,
        alternative_titles=_names(game.alternative_names),
        product_type=product_type,
        source_category=type_name or None,
        status=_token(status) if status else None,
        release_dates=tuple(
            release
            for release in (_release(raw) for raw in game.release_dates)
            if release is not None
        ),
        platforms=_names(game.platforms),
        developers=_unique(name for name, developer, _ in companies if developer),
        publishers=_unique(name for name, _, publisher in companies if publisher),
        franchises=_names(game.franchises),
        external_ids=tuple(
            ExternalId(source=_token(link.external_game_source.name), value=link.uid)
            for link in game.external_games
            if link.uid and link.external_game_source and link.external_game_source.name
        ),
        websites=_unique(site.url for site in game.websites if site.url),
        parent=_reference(game.parent_game),
        version_parent=_reference(game.version_parent),
        source_updated_at=(
            None if game.updated_at is None else datetime.fromtimestamp(game.updated_at, UTC)
        ),
        fetched_at=fetched_at,
    )


def _names(items: list[_Named]) -> tuple[str, ...]:
    return _unique(item.name for item in items if item.name)


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


def _reference(game_id: int | None) -> SourceRecordKey | None:
    return None if game_id is None else SourceRecordKey(source=SOURCE_ID, source_id=str(game_id))


def _release(raw: _ReleaseDate) -> ReleaseDate | None:
    """A release date, or None when IGDB's precision is not one we recognise."""
    format_name = raw.date_format.format if raw.date_format and raw.date_format.format else ""
    precision = _DATE_PRECISION.get(_token(format_name).replace("_", ""))
    if precision is None:
        return None
    if precision is not DatePrecision.TBD and raw.date is None:
        return None
    return ReleaseDate(
        starts_on=(
            None
            if precision is DatePrecision.TBD or raw.date is None
            else datetime.fromtimestamp(raw.date, UTC).date()
        ),
        precision=precision,
        platform=raw.platform.name if raw.platform else None,
        region=raw.release_region.region if raw.release_region else None,
        status=raw.status.name if raw.status else None,
    )
