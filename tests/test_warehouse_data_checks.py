"""The production data checks: dbt tests that fail the warehouse job when data is wrong.

Each check is a SQL test tagged `production_data` and run by the `daily` selector. They
are held here to one healthy warehouse that passes all of them and to one broken copy
per check, which must fail that check and no other. dbt runs offline against DuckDB, as
in test_warehouse_dbt.py, and is skipped the same way unless MS_REQUIRE_DBT=1.
"""

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from metaspike.warehouse.landing import Table, encode_rows, timestamp
from metaspike.warehouse.tables import TableRef
from tests.test_warehouse_dbt import (
    AS_OF,
    PAGEVIEWS,
    PROJECT,
    _dbt,
    copy_to_duckdb,
    dbt_ready,  # noqa: F401  (a fixture, used through pytestmark)
    ledger_row,
    naive,
)
from tests.warehouse_support import World, world

pytestmark = pytest.mark.usefixtures("dbt_ready")

ARTICLES = [f"en.wikipedia.org/Article{n}" for n in range(5)]
TICK_DAYS = [datetime(2026, 10, day, tzinfo=UTC) for day in range(1, 9)]  # 1 to 8 October
COLLECTED_AT = timedelta(hours=12, minutes=5)
CHECKS = {
    "assert_every_due_day_arrived_in_time",
    "assert_no_duplicate_keys_in_period_and_run_data",
    "assert_daily_rows_are_not_far_below_the_weekly_median",
    "assert_a_collector_receipt_is_recent",
}
# Where a healthy day lands: 5 articles, each asked for 4 days ending at the due day.
WINDOW = 4


def period_row(
    article: str, period: datetime, seen: datetime, value: float, run: str
) -> dict[str, Any]:
    return {
        "source": PAGEVIEWS,
        "metric": "pageviews",
        "source_identifier": article,
        "canonical_game_id": "game-x",
        "granularity": "daily",
        "period_start": timestamp(period),
        "value": value,
        "status": "observed",
        "observed_at": timestamp(seen),
        "collection_run_id": run,
        "source_timestamp": timestamp(period),
    }


def run_row(run: str, finished: datetime, outcome: str = "succeeded") -> dict[str, Any]:
    return {
        "run_id": run,
        "job": "ms-wikimedia",
        "source": PAGEVIEWS,
        "phase": "finished",
        "started_at": timestamp(finished - timedelta(minutes=4)),
        "finished_at": timestamp(finished),
        "outcome": outcome,
        "requests_sent": 5,
        "halted_by": None,
        "policy_version": "p1",
        "image_digest": "sha256:" + "0" * 64,
        "detail": {},
    }


def healthy(
    *,
    silent: tuple[str, tuple[datetime, ...]] | None = None,
    thin_day: datetime | None = None,
    last_receipt_age: timedelta | None = timedelta(hours=18),
) -> World:
    """Eight days of ordinary daily collection, ending the day before AS_OF (9 Oct 06:00)."""
    w = world(lambda: AS_OF)
    for tick in TICK_DAYS:
        due = tick - timedelta(days=2)
        seen = tick + COLLECTED_AT
        run = f"run-{tick:%d}"
        rows: list[dict[str, Any]] = []
        for article in ARTICLES:
            if silent is not None and article == silent[0] and tick in silent[1]:
                continue  # asked for, and nothing came back
            observed = seen
            days = 1 if thin_day == tick else WINDOW
            rows += [
                period_row(article, due - timedelta(days=back), observed, 100.0 + back, run)
                for back in range(days)
            ]
        by_time: dict[datetime, list[dict[str, Any]]] = {}
        for row in rows:
            by_time.setdefault(
                datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00")), []
            ).append(row)
        for observed, batch in sorted(by_time.items()):
            w.writer.land(
                Table.PERIOD_OBSERVATIONS, PAGEVIEWS, f"{run}-{observed:%H%M}", observed, batch
            )
        w.writer.land_internal(
            Table.EXPECTED_TARGETS,
            "dispatch",
            f"tick-{tick:%d}",
            tick + timedelta(hours=12),
            [
                ledger_row(PAGEVIEWS, "pageviews", "daily", "fetch_history", article, due)
                | {
                    "due_at": timestamp(tick + timedelta(hours=12)),
                    "recorded_at": timestamp(tick + timedelta(hours=12)),
                }
                for article in ARTICLES
            ],
        )
        finished = tick + COLLECTED_AT
        if last_receipt_age is not None and tick == TICK_DAYS[-1]:
            finished = AS_OF - last_receipt_age
        if last_receipt_age is not None or tick != TICK_DAYS[-1]:
            w.writer.land_internal(
                Table.COLLECTION_RUNS, "collector", run, finished, [run_row(run, finished)]
            )
    w.load_everything()
    return w


def daily_build(w: World, tmp_path: Path, *extra: str) -> tuple[int, str]:
    path = tmp_path / "warehouse.duckdb"
    copy_to_duckdb(w, path)
    variables = json.dumps({"run_as_of": f"{naive(AS_OF):%Y-%m-%d %H:%M:%S}"})
    done = _dbt(
        "build", "--target", "offline", "--selector", "daily", "--vars", variables, *extra,
        database=path, check=False,
    )  # fmt: skip
    return done.returncode, done.stdout


def failed_checks(log: str) -> set[str]:
    return set(re.findall(r"FAIL \d+ (\w+)", log)) & CHECKS


def test_a_healthy_warehouse_passes_every_production_check(tmp_path: Path) -> None:
    code, log = daily_build(healthy(), tmp_path)
    assert code == 0 and "ERROR=0" in log, log[-2000:]
    passed = set(re.findall(r"PASS (\w+)", log))
    assert passed >= CHECKS


Break = Callable[[], World]
BREAKS: dict[str, tuple[str, Break]] = {
    "a due day that never arrived": (
        "assert_every_due_day_arrived_in_time",
        lambda: healthy(silent=(ARTICLES[2], tuple(TICK_DAYS[3:]))),
    ),
    "a due day that only arrived with a later, overlapping window": (
        "assert_every_due_day_arrived_in_time",
        lambda: healthy(silent=(ARTICLES[1], tuple(TICK_DAYS[2:5]))),
    ),
    "yesterday collecting a quarter of the usual rows": (
        "assert_daily_rows_are_not_far_below_the_weekly_median",
        lambda: healthy(thin_day=TICK_DAYS[-1]),
    ),
    "no collector receipt for thirty hours": (
        "assert_a_collector_receipt_is_recent",
        lambda: healthy(last_receipt_age=timedelta(hours=30)),
    ),
    "no collector receipt at all": (
        "assert_a_collector_receipt_is_recent",
        lambda: healthy(last_receipt_age=None),
    ),
}


@pytest.mark.parametrize("name", sorted(BREAKS))
def test_each_broken_warehouse_fails_its_own_check_and_the_job_would_fail(
    tmp_path: Path,
    name: str,
) -> None:
    check, make = BREAKS[name]
    code, log = daily_build(make(), tmp_path)
    assert code != 0, "dbt must exit non-zero: that is what fails the warehouse job"
    assert failed_checks(log) == {check}, log[-3000:]


def test_two_copies_of_one_observation_that_disagree_fail_the_duplicate_check(
    tmp_path: Path,
) -> None:
    w = healthy()
    tick = TICK_DAYS[4]
    seen = tick + COLLECTED_AT
    due = tick - timedelta(days=2)
    clash = period_row(ARTICLES[0], due, seen, 999_999.0, "run-clash")
    w.writer.land(Table.PERIOD_OBSERVATIONS, PAGEVIEWS, "run-clash", seen, [clash])
    w.load_everything("load-2")
    code, log = daily_build(w, tmp_path)
    assert code != 0
    assert "assert_no_duplicate_keys_in_period_and_run_data" in failed_checks(log), log[-3000:]


def test_a_batch_loaded_twice_is_not_a_failure(tmp_path: Path) -> None:
    w = healthy()
    ref = TableRef("ms_raw", "period_observations")
    again = [
        {**row, "loaded_at": "2026-10-09T05:00:00.000000Z", "landed_object": "loaded/twice"}
        for row in w.warehouse.all_rows(ref)[:10]
    ]
    for row in again:
        for column in ("period_start", "observed_at", "source_timestamp"):
            row[column] = row[column].strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    w.warehouse.load("an-accidental-second-load", ref, encode_rows(again))
    code, log = daily_build(w, tmp_path)
    assert code == 0, log[-2000:]


def test_the_checks_are_tagged_so_only_the_daily_selector_runs_them() -> None:
    for name in CHECKS:
        text = (PROJECT / "tests" / f"{name}.sql").read_text()
        tags = re.search(r"tags=\[([^\]]*)\]", text)
        assert (
            tags is not None and "'daily'" in tags.group(1) and "'production_data'" in tags.group(1)
        )
        assert "'hourly'" not in tags.group(1) and "'periods'" not in tags.group(1), name
