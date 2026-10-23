"""Game discovery, offline. Every response here is SYNTHETIC, in the documented shape."""

import bz2
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from metaspike.discovery.dump import count_views, stream_lines
from metaspike.discovery.risers import (
    BASELINE_DAYS,
    MIN_RECENT,
    RECENT_DAYS,
    WINDOW_DAYS,
    assess,
    rank,
)
from metaspike.discovery.store import DiscoveryStore
from metaspike.discovery.wikidata import WikidataGame, parse_games
from metaspike.domain.signals import SourceErrorCode
from metaspike.sources.base import SourceError
from tests.conftest import mock_client

DAY = date(2026, 10, 22)


def test_wikidata_rows_become_one_game_each_with_every_id() -> None:
    rows = [
        {
            "item": "http://www.wikidata.org/entity/Q1",
            "title": "Hollow Knight",
            "igdb": "hollow-knight",
            "steam": "367520",
        },
        {
            "item": "http://www.wikidata.org/entity/Q1",
            "title": "Hollow Knight",
            "igdb": "hollow-knight",
            "steam": "999",
        },
        {
            "item": "http://www.wikidata.org/entity/Q2",
            "title": "Valorant",
            "igdb": "valorant",
            "steam": "",
        },
        {
            "item": "http://www.wikidata.org/entity/Q3",
            "title": "Bad Steam",
            "igdb": "",
            "steam": "not-a-number",
        },
        {"item": "junk", "title": "No item"},
    ]
    games = parse_games(rows)
    assert games == [
        WikidataGame(qid="Q3", article="Bad_Steam"),
        WikidataGame(
            qid="Q1", article="Hollow_Knight", igdb=("hollow-knight",), steam=("367520", "999")
        ),
        WikidataGame(qid="Q2", article="Valorant", igdb=("valorant",)),
    ]


def test_a_dump_sums_platforms_for_wanted_english_articles_only() -> None:
    lines = [
        "en.wikipedia Hollow_Knight 53391134 mobile-web 552 A17B30",
        "en.wikipedia Hollow_Knight 53391134 desktop 667 A28B19",
        "de.wikipedia Hollow_Knight 1 desktop 9000 A1",
        "en.wikipedia Not_A_Game 2 desktop 5000 A1",
        "en.wikipedia Broken_Line",
        "en.wikipedia Valorant 3 desktop x A1",
    ]
    assert count_views(lines, {"Hollow_Knight", "Valorant"}) == {"Hollow_Knight": 1219}


def test_a_dump_in_several_compressed_streams_is_read_to_the_end() -> None:
    body = bz2.compress(b"en.wikipedia A 1 desktop 5 A5\nen.wikipedia B 2 desk") + bz2.compress(
        b"top 7 A7\nen.wikipedia C 3 desktop 9 A9\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/2026/2026-10/pageviews-20261022-user.bz2")
        return httpx.Response(200, content=body)

    lines = list(stream_lines(mock_client(handler), DAY))
    assert count_views(lines, {"A", "B", "C"}) == {"A": 5, "B": 7, "C": 9}

    missing = mock_client(lambda _: httpx.Response(404))
    with pytest.raises(SourceError) as error:
        list(stream_lines(missing, DAY))
    assert error.value.code is SourceErrorCode.NOT_FOUND


def test_a_day_without_a_dump_or_history_is_unknown_never_zero(tmp_path: Path) -> None:
    store = DiscoveryStore(tmp_path)
    game = WikidataGame(qid="Q1", article="Hollow_Knight")
    store.save_day(DAY, {"Other": 4})  # complete dump: Hollow Knight had no views
    store.save_backfill("Q1", {DAY - timedelta(days=1): 40})
    assert store.series(game, DAY, 3) == [None, 40, 0]


def series(
    baseline: int | None, recent: int | None, known_baseline: int = BASELINE_DAYS
) -> list[int | None]:
    unknown = BASELINE_DAYS - known_baseline
    return [None] * unknown + [baseline] * known_baseline + [recent] * RECENT_DAYS


def test_a_game_is_compared_with_its_own_normal_level() -> None:
    indie = assess("Q1", "Indie", series(300, 3000))
    blockbuster = assess("Q2", "Blockbuster", series(8000, 8400))
    assert indie and blockbuster and indie.ratio and blockbuster.ratio
    assert indie.ratio > 8 > 1.1 > blockbuster.ratio
    assert [r.article for r in rank([blockbuster, indie])] == ["Indie", "Blockbuster"]


def test_too_few_views_or_days_are_not_a_story_and_a_new_game_has_no_ratio() -> None:
    assert assess("Q1", "Quiet", series(10, MIN_RECENT - 1)) is None
    assert assess("Q1", "Unknown", [None] * WINDOW_DAYS) is None
    new = assess("Q3", "Wardogs", series(None, 50_000, known_baseline=5))
    assert new and new.new and new.ratio is None and new.baseline is None
    old = assess("Q4", "Old", series(1000, 1200))
    assert old
    assert [r.article for r in rank([new, old])] == ["Old", "Wardogs"]
    with pytest.raises(ValueError):
        assess("Q5", "Short", [1, 2, 3])


def test_the_selection_is_top_risers_then_new_games_then_pinned_once_each() -> None:
    from metaspike.discovery.catalogue import display_name, game_id, select

    games = [
        WikidataGame(qid="Q1", article="Anthem_(video_game)", igdb=("anthem",)),
        WikidataGame(qid="Q2", article="Aion_2"),
        WikidataGame(qid="Q3", article="Wardogs", igdb=("wardogs",)),
        WikidataGame(qid="Q4", article="Quiet"),
    ]
    report: list[dict[str, object]] = [
        {"qid": "Q1", "ratio": 44.9, "new": False},
        {"qid": "Q3", "ratio": 0.7, "new": False},
        {"qid": "Q2", "ratio": None, "new": True},
    ]
    chosen = select(report, games, top=1)
    assert [(g.article, pinned) for g, _, pinned in chosen] == [
        ("Anthem_(video_game)", False),
        ("Aion_2", False),
        ("Wardogs", True),  # pinned, below the top-1 cut, still shown once with its row
    ]
    assert chosen[2][1] == report[1]
    assert display_name("Anthem_(video_game)") == "Anthem"
    assert display_name("Rust_(video_game)") == "Rust"
    assert display_name("Exodus_(2027_video_game)") == "Exodus"
    assert display_name("Shrek_2_(video_game)") == "Shrek 2"
    assert display_name("Doom_(1993)") == "Doom (1993)"
    assert game_id("Q1") == game_id("Q1") != game_id("Q2")
