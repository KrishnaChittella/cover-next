"""Every genuine recorded source, through the engine, to the developer UI's report.

The same two commands `npm run opportunities:integrated` runs: the one recorded Steam
response replayed into a fresh sample store, then the evidence reading with the pinned
catalogue and pageview recordings. Offline: no socket may be opened.
"""

import json
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from metaspike import cli
from metaspike.sources.limiter import Clock, LimiterPool

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
UI = REPO / "apps" / "dev-ui" / "src"
UI_REPORT = UI / "test" / "fixtures" / "opportunities" / "integrated.json"
UI_SCHEMA = UI / "private" / "opportunities" / "report.schema.json"
CATALOGUE = [str(FIXTURES / "catalogue"), str(FIXTURES / "bootstrap_catalogue")]
AS_OF = "2026-10-06T01:30:00+00:00"
RECORDED_AT = datetime(2026, 10, 6, 1, 25, 26, tzinfo=UTC)


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    def no_socket(*_: object, **__: object) -> None:
        raise AssertionError("the integration tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", no_socket)
    monkeypatch.setattr(cli, "LimiterPool", lambda: LimiterPool(clock=Clock(lambda s: None)))
    monkeypatch.delenv("METASPIKE_STEAM_WEB_API_KEY", raising=False)
    # The wall clock is far from the recording. A replay must not be dated by it.
    monkeypatch.setattr(cli, "_now", lambda: datetime(2027, 1, 1, tzinfo=UTC))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def integrated_report(work: Path, capsys: pytest.CaptureFixture[str]) -> bytes:
    sample = [
        "sample", "--recorded", *CATALOGUE, "--state", str(work / "state.json"),
        "--store-dir", str(work / "samples"), "--replay", str(FIXTURES / "steam_players"),
    ]  # fmt: skip
    assert cli.main(sample) == cli.EXIT_OK
    out = work / "integrated.json"
    evidence = [
        "evidence", "--as-of", AS_OF, "--recorded", *CATALOGUE,
        "--wikimedia-replay", str(FIXTURES / "wikimedia"), "--recorded-through", "2026-10-03",
        "--sample-store", str(work / "samples"), "--opportunities", "--json", "--out", str(out),
    ]  # fmt: skip
    assert cli.main(evidence) == cli.EXIT_OK
    capsys.readouterr()
    return out.read_bytes()


def test_the_recorded_sources_reach_the_ui_report_byte_for_byte(
    offline: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    produced = integrated_report(offline, capsys)
    # The developer UI's tests read an unmodified copy of exactly this report.
    assert produced == UI_REPORT.read_bytes()
    report = json.loads(produced)
    assert not list(Draft202012Validator(json.loads(UI_SCHEMA.read_text())).iter_errors(report))


def test_each_source_reaches_its_bound_game_with_an_honest_standing(
    offline: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = json.loads(integrated_report(offline, capsys))
    sources = {(a["slug"], s["source"]): s for a in report["assessments"] for s in a["sources"]}

    # Steam players: app 730 is bound to Counter-Strike 2. The one recorded answer is
    # dated when Steam gave it, is current at the reading, and is of unverified origin.
    players = sources[("counter-strike-2", "steam_current_players")]
    assert players["state"] == "available" and players["standing"] == "unverified"
    observed = datetime.fromisoformat(players["latest_observed_at"])
    assert RECORDED_AT <= observed < RECORDED_AT.replace(second=27)
    # No other app was recorded, so no other game has a Steam observation.
    others = [s for (slug, name), s in sources.items() if name == "steam_current_players"]
    assert [s for s in others if s["latest_observed_at"] is not None] == [players]

    # Pinned pageviews, read a day after the next day fell due: stale, and verified.
    assert sources[("minecraft", "wikimedia_pageviews")]["state"] == "stale"
    assert sources[("minecraft", "wikimedia_pageviews")]["standing"] == "verified"
    # No recording of this article is held: missing, never filled in.
    assert sources[("counter-strike-2", "wikimedia_pageviews")]["state"] == "missing"

    # Catalogue records are identity, never a demand family.
    for name in ("igdb", "steam_catalogue"):
        note = sources[("counter-strike-2", name)]
        assert note["kind"] == "catalogue_metadata" and note["standing"] == "verified"

    # Nothing unverified counts: no game is more than insufficient, and nothing says now.
    assert report["contains_unverified"] is True and report["contains_synthetic"] is False
    assert {a["readiness"] for a in report["assessments"]} == {"insufficient_evidence"}
    cs2 = next(a for a in report["assessments"] if a["slug"] == "counter-strike-2")
    assert cs2["why_now"] == [] and cs2["facts"]["moving_up"] == []
    # Sources with no input stay without a standing.
    assert sources[("counter-strike-2", "creator_supply")]["standing"] == "none"
    assert sources[("counter-strike-2", "community_interest")]["standing"] == "none"
