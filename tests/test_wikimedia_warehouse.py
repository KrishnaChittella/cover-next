"""Wikimedia pageviews read from a production warehouse export, point in time."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

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
