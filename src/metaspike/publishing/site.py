"""The public site's one data file: what Cover Next shows per game, from sources cleared to show.

The games are the discovered risers plus the pinned set (`discovery.catalogue`). Per game:
its Wikipedia views for the last 31 days and how far its recent days rose above its own
normal level (Wikimedia, CC0) and YouTube's most-watched recent videos, one by one (shown
with YouTube attribution, refreshed daily so nothing shown is older than YouTube's 30-day
limit, and only while YouTube's policy clears public launch). Steam and Twitch
are not in this file: neither provider has said its figures may be shown publicly.

Missing is never zero: a game with no reading carries a `state` and null figures.
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from metaspike.creator_sources.store import CreatorSupplyStore
from metaspike.creator_sources.youtube import (
    RETENTION_DAYS,
    YOUTUBE_DATA_POLICY,
    FetchStatus,
    SearchFetch,
    VideoDetail,
)
from metaspike.discovery.catalogue import display_name, game_id, slug_of
from metaspike.discovery.risers import WINDOW_DAYS
from metaspike.discovery.store import DiscoveryStore
from metaspike.discovery.wikidata import WikidataGame
from metaspike.domain.source_policy import DeploymentStage

SITE_DATA_VERSION = "2"
WEEK = 7
TOP_VIDEOS = 3


def _week(values: Sequence[int | None]) -> int | None:
    # A week with any unknown day has no total: a partial sum would read as a drop.
    return None if len(values) < WEEK or None in values else sum(v or 0 for v in values)


def _wikipedia(store: DiscoveryStore, game: WikidataGame, day: date) -> dict[str, Any]:
    series = store.series(game, day, WINDOW_DAYS)
    if all(v is None for v in series):
        return {"state": "missing", "daily": [], "last_7_days": None, "previous_7_days": None}
    start = day - timedelta(days=WINDOW_DAYS - 1)
    return {
        "state": "ok",
        "article": game.article,
        "daily": [
            {"day": (start + timedelta(days=n)).isoformat(), "views": v}
            for n, v in enumerate(series)
        ],
        "last_7_days": _week(series[-WEEK:]),
        "previous_7_days": _week(series[-2 * WEEK : -WEEK]),
    }


def _youtube(
    searches: Sequence[SearchFetch], details: dict[str, VideoDetail], game_id: str, now: datetime
) -> dict[str, Any]:
    """This week's most-watched relevant videos, each with its own figures.

    Individual videos only: YouTube's Developer Policies forbid derived metrics (III.E.4.h)
    and combining data across content owners (III.E.2), so no median, total or count of
    videos is computed here.
    """
    fresh = [
        s
        for s in searches
        if s.query.canonical_game_id == game_id
        and s.status is FetchStatus.OK
        and s.retrieved_at >= now - timedelta(days=RETENTION_DAYS)
    ]
    if not fresh:
        return {"state": "missing"}
    latest = max(fresh, key=lambda s: s.retrieved_at)
    videos = [details[r.video_id] for r in latest.results if r.video_id in details]
    ranked = sorted(
        (v for v in videos if v.view_count is not None),
        key=lambda v: v.view_count or 0,
        reverse=True,
    )
    return {
        "state": "ok",
        "retrieved_at": latest.retrieved_at.isoformat(),
        "top_videos": [
            {
                "id": v.video_id,
                "title": v.title,
                "views": v.view_count,
                "published_at": v.published_at.isoformat(),
            }
            for v in ranked[:TOP_VIDEOS]
        ],
    }


def build_site_data(
    selection: Sequence[tuple[WikidataGame, Mapping[str, Any] | None, bool]],
    store: DiscoveryStore,
    day: date,
    youtube: CreatorSupplyStore | None,
    now: datetime,
) -> dict[str, Any]:
    if not YOUTUBE_DATA_POLICY.cleared_for(DeploymentStage.PUBLIC_LAUNCH):
        youtube = None
    searches = youtube.searches(now) if youtube is not None else ()
    details: dict[str, VideoDetail] = {}
    if youtube is not None:
        for fetch in youtube.videos(now):  # oldest first: the latest detail wins
            for detail in fetch.details:
                details[detail.video_id] = detail
    games: list[dict[str, Any]] = []
    for game, riser, pinned in selection:
        games.append(
            {
                "slug": slug_of(game),
                "name": display_name(game.article),
                "qid": game.qid,
                "igdb": game.igdb[0] if game.igdb else None,
                "steam_app": game.steam[0] if game.steam else None,
                "pinned": pinned,
                "rise": None
                if riser is None
                else {
                    "recent": round(float(riser["recent"])),
                    "baseline": None if riser["baseline"] is None else round(riser["baseline"]),
                    "ratio": None if riser["ratio"] is None else round(riser["ratio"], 2),
                    "new": bool(riser["new"]),
                },
                "wikipedia": _wikipedia(store, game, day),
                "youtube": _youtube(searches, details, game_id(game.qid), now),
            }
        )
    with_videos = sum(g["youtube"]["state"] == "ok" for g in games)
    return {
        "version": SITE_DATA_VERSION,
        "generated_at": now.isoformat(),
        "day": day.isoformat(),
        "sources": {
            "wikipedia": {
                "state": "ok" if store.day(day) is not None else "missing",
                "day": day.isoformat(),
                "games": len(store.day(day) or {}),
            },
            "youtube": {"state": "ok" if with_videos else "missing", "games": with_videos},
            "steam": {"state": "pending"},
            "twitch": {"state": "pending"},
        },
        "games": games,
    }
