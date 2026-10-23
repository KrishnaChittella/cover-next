"""The public site's data file: absent sources are states, never zeros."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from metaspike.discovery.store import DiscoveryStore
from metaspike.discovery.wikidata import WikidataGame
from metaspike.publishing.site import build_site_data

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)


def test_a_game_with_no_readings_says_missing_and_holds_no_numbers(tmp_path: Path) -> None:
    store = DiscoveryStore(tmp_path)
    quiet = WikidataGame(qid="Q9", article="Quiet_Game")
    data = build_site_data([(quiet, None, True)], store, NOW.date(), None, NOW)
    game = data["games"][0]
    assert game["slug"] == "quiet-game" and game["pinned"] and game["rise"] is None
    assert game["wikipedia"] == {
        "state": "missing",
        "daily": [],
        "last_7_days": None,
        "previous_7_days": None,
    }
    assert game["youtube"] == {"state": "missing"}
    assert data["sources"]["wikipedia"]["state"] == "missing"


def test_a_week_with_an_unknown_day_has_no_total(tmp_path: Path) -> None:
    store = DiscoveryStore(tmp_path)
    game = WikidataGame(qid="Q1", article="Hollow_Knight", igdb=("hollow-knight",))
    day = NOW.date()
    for back in range(14):
        if back != 9:  # one day of the previous week was never read
            store.save_day(day - timedelta(days=back), {"Hollow_Knight": 100})
    riser = {"recent": 100.0, "baseline": 100.0, "ratio": 1.0, "new": False}
    shown = build_site_data([(game, riser, False)], store, day, None, NOW)["games"][0]
    assert shown["slug"] == "hollow-knight" and shown["name"] == "Hollow Knight"
    assert shown["wikipedia"]["last_7_days"] == 700
    assert shown["wikipedia"]["previous_7_days"] is None
    assert len(shown["wikipedia"]["daily"]) == 31


def test_youtube_is_shown_video_by_video_and_nothing_is_derived() -> None:
    from metaspike.creator_sources.youtube import (
        FetchStatus,
        SearchFetch,
        SearchQuery,
        SearchResult,
        VideoDetail,
    )
    from metaspike.publishing.site import _youtube

    game = "780b4850-6248-4a81-8fe0-7f0762376cfc"
    moment = NOW - timedelta(hours=1)
    ids = ["v1", "v2", "v3", "v4"]
    search = SearchFetch(
        fetch_id="f",
        run_id="r",
        query=SearchQuery(canonical_game_id=game, text="Rust"),
        page_index=0,
        retrieved_at=moment,
        status=FetchStatus.OK,
        results=tuple(
            SearchResult(video_id=i, channel_id="c", title=i, published_at=moment) for i in ids
        ),
    )
    views = {"v1": 10, "v2": 500, "v3": None, "v4": 70}
    details = {
        i: VideoDetail(video_id=i, channel_id="c", title=i, published_at=moment, view_count=v)
        for i, v in views.items()
    }
    shown = _youtube([search], details, game, NOW)
    assert set(shown) == {"state", "retrieved_at", "top_videos"}  # no median, total or count
    assert [v["id"] for v in shown["top_videos"]] == ["v2", "v4", "v1"]
    # Older than YouTube's 30 days: not shown at all.
    assert _youtube([search], details, game, NOW + timedelta(days=31)) == {"state": "missing"}
