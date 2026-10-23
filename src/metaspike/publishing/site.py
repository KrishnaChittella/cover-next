"""The public site's one data file: what Cover Next shows per game, from sources cleared to show.

Wikimedia pageviews (CC0) and YouTube's most-watched recent videos, one by one (shown
with YouTube attribution, refreshed daily so nothing shown is older than YouTube's 30-day
limit, and only while YouTube's policy clears public launch). Steam and Twitch
are not in this file: neither provider has said its figures may be shown publicly.

Missing is never zero: a game with no reading carries a `state` and null figures.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from metaspike.creator_sources.store import CreatorSupplyStore
from metaspike.creator_sources.youtube import (
    RETENTION_DAYS,
    YOUTUBE_DATA_POLICY,
    FetchStatus,
    SearchFetch,
    VideoDetail,
)
from metaspike.domain.games import CanonicalGame
from metaspike.domain.source_policy import DeploymentStage
from metaspike.pipeline.collection import CollectionReport
from metaspike.sources.wikimedia import WIKIMEDIA_PAGEVIEWS_POLICY

SITE_DATA_VERSION = "1"
WEEK = 7
TOP_VIDEOS = 3


def _wikimedia(report: CollectionReport, game_id: str) -> dict[str, Any]:
    signal = next(
        (
            s
            for s in report.signals_for(game_id).signals
            if s.key.source == WIKIMEDIA_PAGEVIEWS_POLICY.source and s.series is not None
        ),
        None,
    )
    if signal is None or signal.series is None:
        return {"state": "missing", "daily": [], "last_7_days": None, "previous_7_days": None}
    daily = [
        {
            "day": p.period_start.date().isoformat(),
            "views": None if p.value is None else int(p.value),
        }
        for p in signal.series.points
    ]

    def total(days: list[dict[str, Any]]) -> int | None:
        # A week with any missing day has no total: a partial sum would read as a drop.
        values = [d["views"] for d in days]
        return None if len(values) < WEEK or None in values else sum(values)

    return {
        "state": "ok",
        "daily": daily,
        "last_7_days": total(daily[-WEEK:]),
        "previous_7_days": total(daily[-2 * WEEK : -WEEK]),
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
    games: Sequence[CanonicalGame],
    wikimedia: CollectionReport,
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
    return {
        "version": SITE_DATA_VERSION,
        "generated_at": now.isoformat(),
        "games": [
            {
                "slug": game.slug,
                "name": game.name,
                "wikipedia": _wikimedia(wikimedia, game.canonical_game_id),
                "youtube": _youtube(searches, details, game.canonical_game_id, now),
            }
            for game in games
        ],
    }
