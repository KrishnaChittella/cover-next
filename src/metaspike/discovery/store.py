"""Local discovery store: the game list, one complete view count per day, and backfills.

    {root}/games.json              the Wikidata list
    {root}/days/YYYY-MM-DD.json    every game's views that day, from a complete dump
    {root}/backfill/<qid>.json     API history for one game, for days no dump covers

A day file is complete, so a game missing from it had no views that day. A day with no
file, and no backfilled value, is unknown: never zero.
"""

import json
from datetime import date, timedelta
from pathlib import Path

from metaspike.discovery.wikidata import WikidataGame


class DiscoveryStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save_games(self, games: list[WikidataGame]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = [g.model_dump(mode="json") for g in games]
        (self.root / "games.json").write_text(json.dumps(payload), encoding="utf-8")

    def games(self) -> list[WikidataGame] | None:
        path = self.root / "games.json"
        if not path.is_file():
            return None
        return [WikidataGame.model_validate(g) for g in json.loads(path.read_text("utf-8"))]

    def games_age_days(self, today: date) -> int | None:
        path = self.root / "games.json"
        if not path.is_file():
            return None
        return (today - date.fromtimestamp(path.stat().st_mtime)).days

    def save_day(self, day: date, views: dict[str, int]) -> None:
        folder = self.root / "days"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{day.isoformat()}.json").write_text(json.dumps(views), encoding="utf-8")

    def day(self, day: date) -> dict[str, int] | None:
        path = self.root / "days" / f"{day.isoformat()}.json"
        return json.loads(path.read_text("utf-8")) if path.is_file() else None

    def save_backfill(self, qid: str, views: dict[date, int]) -> None:
        folder = self.root / "backfill"
        folder.mkdir(parents=True, exist_ok=True)
        existing = self.backfill(qid)
        existing.update(views)
        payload = {d.isoformat(): v for d, v in sorted(existing.items())}
        (folder / f"{qid}.json").write_text(json.dumps(payload), encoding="utf-8")

    def backfill(self, qid: str) -> dict[date, int]:
        path = self.root / "backfill" / f"{qid}.json"
        if not path.is_file():
            return {}
        return {date.fromisoformat(d): v for d, v in json.loads(path.read_text("utf-8")).items()}

    def series(self, game: WikidataGame, end: date, days: int) -> list[int | None]:
        """Views for the `days` days ending on `end`, oldest first. None is unknown."""
        filled = self.backfill(game.qid)
        out: list[int | None] = []
        for offset in range(days - 1, -1, -1):
            day = end - timedelta(days=offset)
            complete = self.day(day)
            if complete is not None:
                out.append(complete.get(game.article, 0))
            else:
                out.append(filled.get(day))
        return out
