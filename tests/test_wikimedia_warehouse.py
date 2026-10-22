"""Wikimedia pageviews read from a production warehouse export, point in time."""

import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from metaspike import cli
from metaspike.domain.signals import SourceErrorCode
from metaspike.domain.timeseries import TimeWindow
from metaspike.evidence.model import Origin
from metaspike.evidence.provenance import recording_provenance
from metaspike.sources.base import SourceError
from metaspike.sources.wikimedia import DAILY_PAGEVIEWS
from metaspike.sources.wikimedia_warehouse import (
    EXPORT_FILE,
    MAX_BYTES_BILLED,
    WarehouseExport,
    WarehousePageviewsAdapter,
    export_from_bq_json,
    export_query,
    load_export,
)
from tests.conftest import GAME, WIKI_IDENTIFIER

ARTICLE = f"{WIKI_IDENTIFIER.namespace}/{WIKI_IDENTIFIER.value}"
WINDOW = TimeWindow.of_days(date(2026, 10, 14), date(2026, 10, 20))


def bq_row(
    day: str, views: str | None, observed: str, status: str = "observed"
) -> dict[str, str | None]:
    """One row as `bq query --format=json` prints it: naive UTC timestamps, string numbers."""
    return {
        "source_identifier": ARTICLE,
        "canonical_game_id": GAME.canonical_game_id,
        "period_start": f"{day} 00:00:00",
        "value": views,
        "status": status,
        "observed_at": observed,
        "collection_run_id": "20261022T013800Z-ms-wikimedia-2r4zt",
        "landed_object": "wikimedia_pageviews/period_observations/dt=2026-10-22/x/part-0000.ndjson",
        "content_sha256": "0" * 64,
    }


ROWS = [
    bq_row("2026-10-15", "1000.0", "2026-10-17 03:03:42"),
    # The same day observed again later with a revised count: the later one wins, once known.
    bq_row("2026-10-15", "1010.0", "2026-10-19 12:00:00"),
    # Recovered late: observed only on 22 Oct.
    bq_row("2026-10-16", "1200.0", "2026-10-22 01:38:01"),
    # Not an observation: never a value.
    bq_row("2026-10-17", None, "2026-10-19 12:00:00", status="missing"),
]


def export_of(rows: list[dict[str, str | None]]) -> WarehouseExport:
    return export_from_bq_json(
        json.dumps(rows),
        project="metaspike-prod-kc",
        first_day="2026-10-01",
        last_day="2026-10-21",
        exported_at=datetime(2026, 10, 22, 23, 0, tzinfo=UTC),
    )


def read(as_of: datetime, rows: list[dict[str, str | None]] = ROWS) -> dict[date, float | None]:
    adapter = WarehousePageviewsAdapter(export_of(rows))
    observations = adapter.fetch(GAME, WIKI_IDENTIFIER, DAILY_PAGEVIEWS, WINDOW, as_of)
    return {o.period_start.date(): o.value for o in observations}


def test_only_what_the_warehouse_had_observed_is_seen() -> None:
    assert read(datetime(2026, 10, 18, tzinfo=UTC)) == {date(2026, 10, 15): 1000.0}
    assert read(datetime(2026, 10, 20, tzinfo=UTC)) == {date(2026, 10, 15): 1010.0}
    assert read(datetime(2026, 10, 22, 2, 0, tzinfo=UTC)) == {
        date(2026, 10, 15): 1010.0,
        date(2026, 10, 16): 1200.0,
    }


def test_a_row_that_is_not_an_observation_is_absent_never_zero() -> None:
    seen = read(datetime(2026, 10, 22, 2, 0, tzinfo=UTC))
    assert date(2026, 10, 17) not in seen
    assert 0.0 not in seen.values()


def test_nothing_known_yet_is_a_source_error_not_an_empty_series() -> None:
    adapter = WarehousePageviewsAdapter(export_of(ROWS))
    with pytest.raises(SourceError) as caught:
        adapter.fetch(
            GAME, WIKI_IDENTIFIER, DAILY_PAGEVIEWS, WINDOW, datetime(2026, 10, 16, tzinfo=UTC)
        )
    assert caught.value.code is SourceErrorCode.NOT_FOUND


def test_an_observation_keeps_when_it_was_observed_and_its_lag() -> None:
    adapter = WarehousePageviewsAdapter(export_of(ROWS))
    (late,) = [
        o
        for o in adapter.fetch(
            GAME,
            WIKI_IDENTIFIER,
            DAILY_PAGEVIEWS,
            WINDOW,
            datetime(2026, 10, 22, 2, 0, tzinfo=UTC),
        )
        if o.period_start.date() == date(2026, 10, 16)
    ]
    assert late.observed_at == datetime(2026, 10, 22, 1, 38, 1, tzinfo=UTC)
    assert late.freshness is not None and late.freshness.lag_hours == pytest.approx(121.63)


def test_the_export_query_is_bounded_and_read_only() -> None:
    query = export_query("2026-07-01", "2026-10-21")
    assert query.startswith("SELECT ") and "ms_staging.stg_period_observations" in query
    assert "period_start >= TIMESTAMP('2026-07-01')" in query
    assert "source = 'wikimedia_pageviews'" in query
    for verb in ("INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "DROP"):
        assert verb not in query.upper()
    assert MAX_BYTES_BILLED <= 50_000_000


def test_an_export_round_trips_and_is_unverified_until_pinned(tmp_path: Path) -> None:
    export = export_of(ROWS)
    export.write(tmp_path / "export")
    assert (tmp_path / "export" / EXPORT_FILE).is_file()
    assert load_export(tmp_path / "export") == export
    assert recording_provenance([tmp_path / "export"]).origin is Origin.UNVERIFIED_FILES
    with pytest.raises(FileExistsError):
        export.write(tmp_path / "export")


def test_evidence_refuses_two_pageview_inputs_at_once(tmp_path: Path) -> None:
    export_of(ROWS).write(tmp_path / "export")
    code = cli.main(
        [
            "evidence",
            "--as-of",
            "2026-10-22T22:10:00+00:00",
            "--wikimedia-warehouse",
            str(tmp_path / "export"),
            "--wikimedia-replay",
            str(tmp_path),
            "--recorded-through",
            "2026-10-03",
        ]
    )
    assert code != 0


# --- the CLI, end to end ---------------------------------------------------------------

HELLDIVERS = "en.wikipedia.org/Helldivers_2"


def helldivers_row(day: str, views: str, observed: str) -> dict[str, str | None]:
    return {**bq_row(day, views, observed), "source_identifier": HELLDIVERS}


HELLDIVERS_ROWS = [
    helldivers_row("2026-10-18", "800.0", "2026-10-20 12:00:00"),
    helldivers_row("2026-10-19", "810.0", "2026-10-21 12:00:00"),
    # Due at 00:00 on 22 Oct, but only observed at 01:38.
    helldivers_row("2026-10-20", "795.0", "2026-10-22 01:38:01"),
]


def evidence_json(
    export_dir: Path, as_of: str, capsys: pytest.CaptureFixture[str]
) -> dict[str, Any]:
    capsys.readouterr()
    code = cli.main(
        [
            "evidence",
            "--as-of",
            as_of,
            "--game",
            "helldivers-2",
            "--wikimedia-warehouse",
            str(export_dir),
            "--json",
        ]
    )
    assert code == 0
    (game,) = json.loads(capsys.readouterr().out)["games"]
    (source,) = [s for s in game["sources"] if s["source"] == "wikimedia_pageviews"]
    found: dict[str, Any] = source
    return found


def test_evidence_reads_the_warehouse_point_in_time_and_unverified(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    export_of(HELLDIVERS_ROWS).write(tmp_path / "export")
    # At 01:00 the due day, 20 Oct, had not been observed: the latest day known is 19 Oct,
    # and the reading says stale rather than pretending the due day is there.
    before = evidence_json(tmp_path / "export", "2026-10-22T01:00:00+00:00", capsys)
    assert before["state"] == "stale"
    assert before["latest_observed_at"] == "2026-10-20T00:00:00Z"
    # At 02:00 it had.
    after = evidence_json(tmp_path / "export", "2026-10-22T02:00:00+00:00", capsys)
    assert after["state"] == "available"
    assert after["latest_observed_at"] == "2026-10-21T00:00:00Z"
    for reading in (before, after):
        provenance = reading["provenance"]
        assert provenance["origin"] == "unverified_files"
        assert provenance["standing"] == "unverified"
        assert "production warehouse export" in provenance["note"]


def test_synthetic_data_never_replaces_a_supplied_warehouse_export(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    export_of(HELLDIVERS_ROWS).write(tmp_path / "export")
    code = cli.main(
        [
            "evidence",
            "--as-of",
            "2026-10-22T02:00:00+00:00",
            "--wikimedia-warehouse",
            str(tmp_path / "export"),
            "--synthetic-scenario",
        ]
    )
    assert code != 0
    assert "--wikimedia-warehouse" in capsys.readouterr().err


def test_an_answer_at_the_row_limit_plus_one_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from metaspike.sources import wikimedia_warehouse

    assert f"LIMIT {wikimedia_warehouse.EXPORT_MAX_ROWS + 1}" in export_query(
        "2026-10-01", "2026-10-21"
    )
    monkeypatch.setattr(wikimedia_warehouse, "EXPORT_MAX_ROWS", 2)
    assert len(export_of(ROWS[:2]).rows) == 2
    with pytest.raises(ValueError, match="may be incomplete"):
        export_of(ROWS[:3])


class FakeBq:
    """Stands in for `bq`: records the command, answers as told. Nothing is run."""

    def __init__(self, returncode: int, stdout: str, stderr: str = "") -> None:
        self.answer = (returncode, stdout, stderr)
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **_: object) -> object:
        self.commands.append(command)
        code, out, err = self.answer
        return subprocess.CompletedProcess(command, code, out, err)


def export_cli(out: Path) -> int:
    return cli.main(
        [
            "export-wikimedia-warehouse",
            "--first-day",
            "2026-10-01",
            "--last-day",
            "2026-10-21",
            "--out",
            str(out),
        ]
    )


def test_the_export_command_writes_a_bounded_read_only_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from metaspike.sources import wikimedia_warehouse

    bq = FakeBq(0, json.dumps(HELLDIVERS_ROWS))
    monkeypatch.setattr(subprocess, "run", bq)
    assert export_cli(tmp_path / "export") == 0
    (command,) = bq.commands
    assert command[:2] == ["bq", "query"]
    assert f"--maximum_bytes_billed={MAX_BYTES_BILLED}" in command
    assert f"--max_rows={wikimedia_warehouse.EXPORT_MAX_ROWS + 1}" in command
    assert command[-1] == export_query("2026-10-01", "2026-10-21")
    assert len(load_export(tmp_path / "export").rows) == 3
    assert "UNVERIFIED until pinned" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("returncode", "stdout", "reason"),
    [
        (1, "", "bq query failed"),
        (0, "{}", "did not print a list"),
        (0, "not json", "export refused"),
    ],
)
def test_a_failed_or_malformed_export_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    returncode: int,
    stdout: str,
    reason: str,
) -> None:
    monkeypatch.setattr(subprocess, "run", FakeBq(returncode, stdout, "access denied"))
    assert export_cli(tmp_path / "export") != 0
    assert reason in capsys.readouterr().err
    assert not (tmp_path / "export").exists()


def test_a_truncated_export_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from metaspike.sources import wikimedia_warehouse

    monkeypatch.setattr(wikimedia_warehouse, "EXPORT_MAX_ROWS", 2)
    monkeypatch.setattr(subprocess, "run", FakeBq(0, json.dumps(HELLDIVERS_ROWS)))
    assert export_cli(tmp_path / "export") != 0
    assert "may be incomplete" in capsys.readouterr().err
    assert not (tmp_path / "export").exists()


def test_the_export_never_overwrites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bq = FakeBq(0, "[]")
    monkeypatch.setattr(subprocess, "run", bq)
    (tmp_path / "export").mkdir()
    assert export_cli(tmp_path / "export") != 0
    assert bq.commands == []
