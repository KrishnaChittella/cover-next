"""Studio stories: games rising together under one studio fold into one story."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from metaspike.discovery.store import DiscoveryStore
from metaspike.discovery.studios import Studio, group, parse_studios
from metaspike.discovery.wikidata import WikidataGame
from metaspike.publishing.site import build_site_data

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)
BIOWARE = Studio(qid="Q207993", name="BioWare", role="developer", article="BioWare")
EA = Studio(qid="Q173941", name="Electronic Arts", role="publisher", article="Electronic_Arts")


def _rising(ratio: float = 10.0) -> dict[str, Any]:
    return {"recent": 1000.0, "baseline": 100.0, "ratio": ratio, "new": False}


def test_parse_keeps_developer_over_publisher_and_skips_unlabelled() -> None:
    def row(game: str, prop: str, studio: str, label: str) -> dict[str, Any]:
        return {
            "item": {"value": f"http://www.wikidata.org/entity/{game}"},
            "prop": {"value": f"http://www.wikidata.org/prop/direct/{prop}"},
            "studio": {"value": f"http://www.wikidata.org/entity/{studio}"},
            "studioLabel": {"value": label},
            "article": {"value": "https://en.wikipedia.org/wiki/Bio%27Ware"},
        }

    found = parse_studios(
        [
            row("Q1", "P123", "Q207993", "BioWare"),
            row("Q1", "P178", "Q207993", "BioWare"),
            row("Q1", "P123", "Q5", "Q5"),  # no English label: the label service echoes the id
        ]
    )
    assert found == {
        "Q1": [Studio(qid="Q207993", name="BioWare", role="developer", article="Bio'Ware")]
    }


def test_three_rising_games_make_a_story_and_each_game_joins_one() -> None:
    games = [WikidataGame(qid=f"Q{n}", article=f"Game_{n}") for n in range(5)]
    selection = [(g, _rising(), False) for g in games]
    selection[4] = (games[4], _rising(1.0), False)  # steady: never in a story
    studios = {g.qid: [BIOWARE, EA] for g in games}
    stories = group(selection, studios)
    assert [s["name"] for s in stories] == ["BioWare"]  # EA has no games left
    assert stories[0]["members"] == ["game-0", "game-1", "game-2", "game-3"]


def test_two_games_are_not_a_story() -> None:
    games = [WikidataGame(qid=f"Q{n}", article=f"Game_{n}") for n in range(2)]
    assert group([(g, _rising(), False) for g in games], {g.qid: [BIOWARE] for g in games}) == []


def test_site_data_marks_the_lead_and_folds_the_rest(tmp_path: Path) -> None:
    games = [WikidataGame(qid=f"Q{n}", article=f"Game_{n}") for n in range(3)]
    selection = [(g, _rising(), False) for g in games]
    stories = group(selection, {g.qid: [BIOWARE] for g in games})
    data = build_site_data(selection, DiscoveryStore(tmp_path), NOW.date(), None, NOW, stories)
    refs = [g["story"] for g in data["games"]]
    assert [r["lead"] for r in refs] == [True, False, False]
    assert data["stories"][0]["members"] == ["game-0", "game-1", "game-2"]
