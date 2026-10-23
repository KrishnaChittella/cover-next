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


def test_youtube_is_shown_video_by_video_and_only_when_it_is_about_the_game() -> None:
    from metaspike.creator_sources.association import GameNames
    from metaspike.creator_sources.youtube import (
        FetchStatus,
        SearchFetch,
        SearchQuery,
        SearchResult,
        VideoDetail,
    )
    from metaspike.publishing.site import _about_game, _youtube

    game = GameNames.of("780b4850-6248-4a81-8fe0-7f0762376cfc", "Anthem")
    other = GameNames.of("9abc9607-45ea-4441-9da2-3eb00af9d4a3", "Dragon Age")
    catalogue = [game, other]
    moment = NOW - timedelta(hours=1)
    game_topic = ("https://en.wikipedia.org/wiki/Action_game",)
    music = ("https://en.wikipedia.org/wiki/Music",)
    videos = {
        "v1": ("Anthem gameplay, the last mission", "20", 10, game_topic),
        "v2": ("Anthem in 2026: was it really that bad?", "20", 500, game_topic),
        "v3": ("Anthem boss fight", "20", None, game_topic),  # no view count: not ranked
        "v4": ("Anthem vs Dragon Age, which BioWare game aged better", "20", 9_000, game_topic),
        "v5": ("VCT Champions opening ceremony anthem", "10", 1_500_000, music),
        "v6": ("Opening ceremony highlights", "20", 2_000_000, game_topic),  # no name
        # The uploader chose Gaming for a music video; YouTube's own topic says music.
        "v7": ("GOLU ANTHEM | MUSIC VIDEO", "20", 800_000, music),
        # An esports team song: "video game culture" is not a game.
        "v8": (
            "KONGRES O2H, TOMODAKI & B2F ACE ANTHEM",
            "20",
            700_000,
            ("https://en.wikipedia.org/wiki/Video_game_culture",),
        ),
    }
    search = SearchFetch(
        fetch_id="f",
        run_id="r",
        query=SearchQuery(canonical_game_id=game.canonical_game_id, text="Anthem game"),
        page_index=0,
        retrieved_at=moment,
        status=FetchStatus.OK,
        results=tuple(
            SearchResult(video_id=i, channel_id="c", title=t, published_at=moment)
            for i, (t, _, _, _) in videos.items()
        ),
    )
    details = {
        i: VideoDetail(
            video_id=i,
            channel_id="c",
            title=t,
            published_at=moment,
            category_id=c,
            view_count=v,
            topic_categories=topics,
        )
        for i, (t, c, v, topics) in videos.items()
    }
    shown = _youtube([search], details, game, catalogue, NOW)
    assert set(shown) == {"state", "retrieved_at", "top_videos"}  # no median, total or count
    # Music, unrelated titles and videos naming another tracked game are left out.
    assert [v["id"] for v in shown["top_videos"]] == ["v2", "v1"]
    # A franchise name in the tags only is not enough: the title must name the game.
    gate = GameNames.of("780b4850-6248-4a81-8fe0-7f0762376cfc", "Baldur's Gate")
    short = VideoDetail(
        video_id="s1",
        channel_id="c",
        title="How to find the rarest spell in #baldursgate3",
        published_at=moment,
        category_id="20",
        tags=("baldur's gate",),
        view_count=50_000,
    )
    assert not _about_game(gate, [gate], short)
    # Older than YouTube's 30 days: not shown at all.
    later = NOW + timedelta(days=31)
    assert _youtube([search], details, game, catalogue, later) == {"state": "missing"}
