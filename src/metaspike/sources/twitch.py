"""Twitch Helix, as far as live demand needs it: `GET /helix/games`, `GET /helix/streams`.

Docs: https://dev.twitch.tv/docs/api/reference/  (reviewed 2026-10-23)

    games     up to 100 `igdb_id` values per call; each category carries the `igdb_id`
              Twitch holds for it, so a category is matched by id, never by name.
    streams   `game_id`, `first` up to 100, `after` cursor; "descending order by the
              number of viewers", and pages may repeat or skip streams while paging.

A reading is a sum over the streams that were read: live viewers and the number of live
streams. Only the top pages are read, so a reading that stopped with a cursor left is a
lower bound and says so (`complete` is false). Stream titles, user names, thumbnails and
everything else are never kept: only the two sums leave this module.

Storage. The Twitch Developer Services Agreement allows a copy only as a cache "for only
a twenty-four hour time period without further sharing it with third parties", unless
Twitch authorises more in writing. `TwitchLiveStore` keeps readings for 24 hours and
deletes anything older on every write. No history is built from this source.

Credentials are the IGDB ones (one Twitch application) through `IgdbAuth`.
"""

import json
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlencode

import httpx
from pydantic import AwareDatetime

from metaspike.domain.model import FrozenModel
from metaspike.domain.signals import SourceErrorCode
from metaspike.domain.source_policy import (
    ApprovalStatus,
    DeploymentStage,
    DisplayRules,
    LimitBasis,
    Permission,
    RateLimit,
    Requirement,
    SourceOperations,
    SourcePolicy,
    StageClearance,
)
from metaspike.sources.base import SourceError
from metaspike.sources.http import LimitedHttpClient
from metaspike.sources.igdb_auth import IgdbAuth

SOURCE_ID: Final = "twitch_helix"
API_URL: Final = "https://api.twitch.tv/helix"
MAX_IDS_PER_CALL: Final = 100  # documented for games
PAGE_SIZE: Final = 100  # documented maximum of `first`
# Self-imposed: the top 300 streams carry most of a category's viewers.
MAX_PAGES: Final = 3
CACHE_HOURS: Final = 24  # Developer Services Agreement: cache for 24 hours only

TWITCH_HELIX_POLICY = SourcePolicy(
    source=SOURCE_ID,
    # A 24-hour cache, unless Twitch authorises more in writing.
    raw_storage_allowed=Permission.RESTRICTED,
    retention_days=1,
    historical_storage_allowed=Permission.RESTRICTED,
    # Twitch reserves "insights into such data (including all derivative works)"; whether
    # a viewer sum per category may be used is not stated. Unanswered is not allowed.
    derived_metrics_allowed=Permission.UNKNOWN,
    commercial_use_allowed=Permission.UNKNOWN,
    # "Re-syndication and re-distribution of ... data as available from a Twitch API is
    # prohibited."
    redistribution_allowed=Permission.PROHIBITED,
    attribution_required=Requirement.REQUIRED,
    attribution_text="Live data from Twitch.",
    approval_required=Requirement.NOT_REQUIRED,
    approval_status=ApprovalStatus.NOT_REQUIRED,
    commercial_approval_required=Requirement.UNKNOWN,
    commercial_approval_status=ApprovalStatus.PENDING,
    stage_clearance=(
        StageClearance(
            stage=DeploymentStage.PRIVATE_DEVELOPMENT,
            cleared=True,
            reason=(
                "Bounded live reads from the owner's machine with the existing Twitch "
                "application; only two sums per category are kept, for 24 hours."
            ),
        ),
        StageClearance(
            stage=DeploymentStage.PRIVATE_COLLECTION,
            cleared=False,
            reason=(
                "Not cleared. Unattended collection is fine within the 24-hour cache, but "
                "nothing reads it yet in the cloud and the owner has not approved it."
            ),
        ),
        StageClearance(
            stage=DeploymentStage.PUBLIC_LAUNCH,
            cleared=False,
            reason=(
                "Not cleared. Re-distributing API data is prohibited and derived insights "
                "are reserved to Twitch: showing a Twitch figure, or a verdict resting on "
                "one, needs Twitch's written answer."
            ),
        ),
        StageClearance(
            stage=DeploymentStage.COMMERCIAL_PRODUCTION,
            cleared=False,
            reason="Not cleared. Nothing above is settled.",
        ),
    ),
    operations=SourceOperations(
        budget_group="twitch_helix",
        rate_limit=RateLimit(
            max_requests=2,
            per_seconds=1,
            max_concurrency=1,
            basis=LimitBasis.SELF_IMPOSED,
            note="Far below the documented app-token bucket; 429 when exceeded.",
        ),
        quota=None,
        cache_max_age_seconds=CACHE_HOURS * 3600,
        expected_publication_lag_seconds=0,
        publication_lag_basis=LimitBasis.SELF_IMPOSED,
    ),
    display=DisplayRules(
        link_back_required=Requirement.UNKNOWN,
        branding_required=Requirement.REQUIRED,
        note="Twitch marks only to attribute Twitch as the source (Trademark Guidelines).",
    ),
    reviewed_at=date(2026, 10, 23),
    references=(
        "https://dev.twitch.tv/docs/api/reference/",
        "https://legal.twitch.com/en/legal/developer-agreement/",
    ),
    notes=(
        "Read on 2026-10-23, no legal review. Storage beyond 24 hours, showing figures "
        "and derived verdicts publicly all need Twitch's written authorisation."
    ),
)


class TwitchLive(FrozenModel):
    """One category's live sums at one moment, or why there are none."""

    canonical_game_id: str
    twitch_game_id: str | None
    retrieved_at: AwareDatetime
    viewers: int | None = None
    streams: int | None = None
    # False when a cursor was left unread: the sums are then a lower bound.
    complete: bool = False
    error_code: SourceErrorCode | None = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


class TwitchHelixAdapter:
    def __init__(
        self, http: LimitedHttpClient, auth: IgdbAuth, now: Callable[[], datetime] = _utc_now
    ) -> None:
        self._http = http
        self._auth = auth
        self._now = now

    @property
    def source(self) -> str:
        return SOURCE_ID

    def categories(self, igdb_ids: Iterable[str]) -> dict[str, str]:
        """IGDB id -> Twitch category id, for the categories Twitch itself links to them."""
        wanted = sorted(set(igdb_ids))
        found: dict[str, str] = {}
        for start in range(0, len(wanted), MAX_IDS_PER_CALL):
            chunk = wanted[start : start + MAX_IDS_PER_CALL]
            for row in self._data("games", [("igdb_id", i) for i in chunk]):
                igdb_id, twitch_id = row.get("igdb_id"), row.get("id")
                if isinstance(igdb_id, str) and igdb_id in chunk and isinstance(twitch_id, str):
                    found[igdb_id] = twitch_id
        return found

    def live(self, canonical_game_id: str, twitch_game_id: str) -> TwitchLive:
        """Sum the top pages of live streams. Never raises for a source failure."""
        retrieved_at = self._now()
        seen: dict[str, int] = {}
        cursor: str | None = None
        try:
            for _ in range(MAX_PAGES):
                query = [("game_id", twitch_game_id), ("type", "live"), ("first", str(PAGE_SIZE))]
                if cursor:
                    query.append(("after", cursor))
                body = self._get("streams", query)
                for row in _rows(body):
                    count = row.get("viewer_count")
                    if isinstance(row.get("id"), str) and isinstance(count, int) and count >= 0:
                        seen[row["id"]] = count  # a stream repeated across pages counts once
                pagination = body.get("pagination")
                cursor = pagination.get("cursor") if isinstance(pagination, dict) else None
                if not cursor:
                    break
        except SourceError as error:
            return TwitchLive(
                canonical_game_id=canonical_game_id,
                twitch_game_id=twitch_game_id,
                retrieved_at=retrieved_at,
                error_code=error.code,
            )
        return TwitchLive(
            canonical_game_id=canonical_game_id,
            twitch_game_id=twitch_game_id,
            retrieved_at=retrieved_at,
            viewers=sum(seen.values()),
            streams=len(seen),
            complete=not cursor,
        )

    def _data(self, endpoint: str, query: Sequence[tuple[str, str]]) -> list[dict[str, Any]]:
        return _rows(self._get(endpoint, query))

    def _get(self, endpoint: str, query: Sequence[tuple[str, str]]) -> dict[str, Any]:
        label = f"twitch {endpoint}"
        # Repeated parameters (igdb_id=1&igdb_id=2) do not fit a mapping, so the URL carries them.
        url = f"{API_URL}/{endpoint}?{urlencode(query)}"
        headers, token = self._auth.headers()
        response = self._http.request("GET", url, label=label, headers=headers)
        if response.status_code == httpx.codes.UNAUTHORIZED:
            # One refresh and one retry: a second rejection is a real credential problem.
            self._auth.invalidate(token)
            headers, _ = self._auth.headers()
            response = self._http.request("GET", url, label=label, headers=headers)
        if response.status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
            raise SourceError(
                SourceErrorCode.UNAUTHORIZED,
                f"{label}: credentials rejected (HTTP {response.status_code})",
            )
        if response.status_code != httpx.codes.OK:
            raise SourceError(SourceErrorCode.HTTP_ERROR, f"{label}: HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise SourceError(SourceErrorCode.INVALID_RESPONSE, f"{label}: not JSON") from None
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise SourceError(SourceErrorCode.INVALID_RESPONSE, f"{label}: no data list")
        return body


def _rows(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in body.get("data", []) if isinstance(row, dict)]


class TwitchLiveStore:
    """`{root}/{retrieved}.json`, one file per run, none older than 24 hours."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def write(self, readings: Sequence[TwitchLive], now: datetime) -> Path:
        self.purge(now)
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / f"{now.astimezone(UTC):%Y%m%dT%H%M%SZ}.json"
        path.write_text(json.dumps([r.model_dump(mode="json") for r in readings], indent=1))
        return path

    def purge(self, now: datetime) -> int:
        """Delete every run older than the cache allows. Returns how many went."""
        expired = [p for p, moment in self._runs() if moment < now - timedelta(hours=CACHE_HOURS)]
        for path in expired:
            path.unlink()
        return len(expired)

    def latest(self, as_of: datetime) -> tuple[TwitchLive, ...]:
        """The newest run at or before `as_of`, if it is still inside the cache window."""
        held = [
            (moment, p)
            for p, moment in self._runs()
            if as_of - timedelta(hours=CACHE_HOURS) <= moment <= as_of
        ]
        if not held:
            return ()
        _, path = max(held)
        return tuple(TwitchLive.model_validate(row) for row in json.loads(path.read_text()))

    def _runs(self) -> list[tuple[Path, datetime]]:
        if not self._root.is_dir():
            return []
        return [
            (p, datetime.strptime(p.stem, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC))
            for p in sorted(self._root.glob("*.json"))
        ]
