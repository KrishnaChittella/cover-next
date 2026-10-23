"""Steam on the public site: one reading a day, history kept, missing is never zero."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from metaspike.discovery.daily import DailyHistory, view
from metaspike.discovery.steam import collect
from metaspike.discovery.store import DiscoveryStore
from metaspike.discovery.wikidata import WikidataGame
from metaspike.domain.samples import SampleReading, SampleStatus
from metaspike.domain.signals import SourceErrorCode
from metaspike.publishing.site import build_site_data

DAY = date(2026, 10, 24)
NOW = datetime(2026, 10, 24, 13, 30, tzinfo=UTC)


class FakeAdapter:
    def __init__(self, answers: dict[str, SampleReading]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    def sample(self, app: str) -> SampleReading:
        self.asked.append(app)
        return self.answers[app]


def _ok(value: int) -> SampleReading:
    return SampleReading(status=SampleStatus.OBSERVED, value=value, observed_at=NOW)


def _failed(halt: bool = False) -> SampleReading:
    return SampleReading(
        status=SampleStatus.FAILED,
        observed_at=NOW,
        error_code=SourceErrorCode.RATE_LIMITED,
        halt_run=halt,
    )


def test_a_failed_reading_stores_nothing_and_a_halt_stops_the_run(tmp_path: Path) -> None:
    history = DailyHistory(tmp_path)
    adapter = FakeAdapter({"1": _ok(5), "2": _failed(halt=True), "3": _ok(7)})
    assert collect(adapter, ["1", "2", "3"], history, DAY) == (1, 1)  # type: ignore[arg-type]
    assert adapter.asked == ["1", "2"]
    assert history.get("1") == {DAY: 5} and history.get("2") == {}


def test_no_normal_before_two_weeks_then_recent_against_the_median() -> None:
    short = {DAY - timedelta(days=n): 100 for n in range(5)}
    assert view(short, DAY)["normal"] is None
    days = {DAY - timedelta(days=n): 100 for n in range(3, 31)}
    days |= {DAY: 300, DAY - timedelta(days=1): 300, DAY - timedelta(days=2): 300}
    shown = view(days, DAY)
    assert shown["now"] == 300 and shown["normal"] == 100 and shown["ratio"] == 3.0
    assert len(shown["daily"]) == 31


def test_no_reading_today_is_missing_not_zero() -> None:
    assert view({DAY - timedelta(days=1): 50}, DAY) == {"state": "missing", "daily": []}


def test_site_data_separates_not_on_steam_from_not_read(tmp_path: Path) -> None:
    history = DailyHistory(tmp_path / "steam")
    history.put("10", DAY, 42)
    games = [
        WikidataGame(qid="Q1", article="On_Steam", steam=("10",)),
        WikidataGame(qid="Q2", article="Unread", steam=("20",)),
        WikidataGame(qid="Q3", article="Console_Only"),
    ]
    selection = [(g, None, False) for g in games]
    data = build_site_data(selection, DiscoveryStore(tmp_path), DAY, None, NOW, (), history)
    states = [g["steam"]["state"] for g in data["games"]]
    assert states == ["ok", "missing", "inapplicable"]
    assert data["games"][0]["steam"]["now"] == 42
    assert data["sources"]["steam"]["games"] == 1
    assert "not endorsed by or affiliated with Valve" in data["sources"]["steam"]["attribution"]
