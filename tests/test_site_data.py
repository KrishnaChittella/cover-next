"""The public site's data file: absent sources are states, never zeros."""

from datetime import UTC, date, datetime

from metaspike.catalogue.seed import bootstrap_catalogue
from metaspike.domain.timeseries import TimeWindow
from metaspike.pipeline.collection import CollectionReport
from metaspike.publishing.site import build_site_data

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)


def test_a_game_with_no_readings_says_missing_and_holds_no_numbers() -> None:
    games = bootstrap_catalogue().games[:2]
    empty = CollectionReport(
        window=TimeWindow.of_days(date(2026, 10, 1), date(2026, 10, 22)), observed_at=NOW, runs=()
    )
    data = build_site_data(games, empty, None, NOW)
    assert [g["slug"] for g in data["games"]] == [g.slug for g in games]
    for game in data["games"]:
        assert game["wikipedia"] == {
            "state": "missing",
            "daily": [],
            "last_7_days": None,
            "previous_7_days": None,
        }
        assert game["youtube"] == {"state": "missing"}


def test_youtube_is_shown_video_by_video_and_nothing_is_derived() -> None:
    from datetime import timedelta

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
