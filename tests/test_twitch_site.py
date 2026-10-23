"""Twitch on the public site: mapped by id, two sums a day, unmapped is not zero."""

from datetime import UTC, date, datetime
from pathlib import Path

from metaspike.discovery.store import DiscoveryStore
from metaspike.discovery.twitch import TwitchDaily, collect
from metaspike.discovery.wikidata import WikidataGame
from metaspike.domain.signals import SourceErrorCode
from metaspike.publishing.site import build_site_data
from metaspike.sources.twitch import TwitchLive

DAY = date(2026, 10, 24)
NOW = datetime(2026, 10, 24, 13, 30, tzinfo=UTC)


class FakeIgdb:
    def ids_for_slugs(self, slugs: object) -> dict[str, int]:
        return {"hades": 1, "quiet": 2, "broken": 3}


class FakeTwitch:
    def categories(self, igdb_ids: object) -> dict[str, str]:
        return {"1": "t1", "3": "t3"}  # Twitch links no category to IGDB 2

    def live(self, label: str, category: str) -> TwitchLive:
        if category == "t3":
            return TwitchLive(
                canonical_game_id=label,
                twitch_game_id=category,
                retrieved_at=NOW,
                error_code=SourceErrorCode.HTTP_ERROR,
            )
        return TwitchLive(
            canonical_game_id=label,
            twitch_game_id=category,
            retrieved_at=NOW,
            viewers=900,
            streams=12,
            complete=False,
        )


def test_collect_maps_by_id_and_keeps_only_two_sums(tmp_path: Path) -> None:
    store = TwitchDaily(tmp_path)
    slugs = ["hades", "quiet", "broken", "no-igdb-row"]
    counts = collect(FakeIgdb(), FakeTwitch(), slugs, store, DAY)  # type: ignore[arg-type]
    assert counts == (1, 1, 2)
    assert store.viewers.get("hades") == {DAY: 900} and store.streams.get("hades") == {DAY: 12}
    assert store.partial.get("hades") == {DAY: 1}
    assert store.viewers.get("broken") == {}  # a failure stores nothing
    assert store.categories() == {
        "broken": "t3",
        "hades": "t1",
        "no-igdb-row": None,
        "quiet": None,
    }


def test_site_data_states(tmp_path: Path) -> None:
    store = TwitchDaily(tmp_path / "twitch")
    collect(FakeIgdb(), FakeTwitch(), ["hades", "quiet"], store, DAY)  # type: ignore[arg-type]
    games = [
        WikidataGame(qid="Q1", article="Hades", igdb=("hades",)),
        WikidataGame(qid="Q2", article="Quiet", igdb=("quiet",)),
        WikidataGame(qid="Q3", article="No_Igdb"),
    ]
    data = build_site_data(
        [(g, None, False) for g in games], DiscoveryStore(tmp_path), DAY, None, NOW, (), None, store
    )
    shown = [g["twitch"] for g in data["games"]]
    assert shown[0]["now"] == 900 and shown[0]["streams_now"] == 12 and shown[0]["lower_bound"]
    assert shown[1] == {"state": "unmapped"}
    assert shown[2]["state"] == "missing"
    assert data["sources"]["twitch"]["games"] == 1
    assert data["sources"]["twitch"]["basis"].startswith("https://discuss.dev.twitch.com/")
