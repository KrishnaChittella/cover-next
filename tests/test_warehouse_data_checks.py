"""The production data checks: dbt tests that fail the warehouse job when data is wrong.

Each check is a SQL test tagged `production_data` and run by the `daily` selector. They
are held here to one healthy warehouse that passes all of them and to one broken copy
per check, which must fail that check and no other. dbt runs offline against DuckDB, as
in test_warehouse_dbt.py, and is skipped the same way unless MS_REQUIRE_DBT=1.
"""

import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from metaspike.jobs import runlog
from metaspike.jobs.collect import collect_wikimedia_job, seed_bootstrap_markers
from metaspike.jobs.dispatch import dispatch
from metaspike.jobs.targets import bootstrap_targets
from metaspike.jobs.warehouse_job import warehouse
from metaspike.scheduling.config import PRODUCTION
from metaspike.sources.wikimedia import SOURCE_ID as WIKIMEDIA
from metaspike.warehouse.landing import Table, encode_rows, timestamp
from metaspike.warehouse.tables import TableRef
from tests.test_c2_scheduling import Clock, Pageviews, arm, dispatcher, ordered_runtime
from tests.test_warehouse_dbt import (
    AS_OF,
    PAGEVIEWS,
    PROJECT,
    _dbt,
    build,
    copy_to_duckdb,
    copy_to_duckdb_raw,
    dbt_ready,  # noqa: F401  (a fixture, used through pytestmark)
    ledger_row,
    naive,
    query,
)
from tests.warehouse_support import World, real_policy, runtime_for, world

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


# --- late recovery: an SLA breach on record, not a failure every day -------------------------

STATUS = (
    "select cast(slot_start as date), status from ms_marts.period_due_day_status "
    "where source_native_id = '{article}' order by 1"
)


def statuses(path: Path, article: str) -> dict[str, str]:
    return {
        f"{day:%Y-%m-%d}": status for day, status in query(path, STATUS.format(article=article))
    }


def test_a_due_day_recovered_after_its_deadline_is_kept_as_late_and_passes(
    tmp_path: Path,
) -> None:
    # Article1 is asked for and silent on 3, 4 and 5 October. The 6 October run's
    # four-day window brings back its due days of 1, 2 and 3 October: the first two
    # after their deadline, the third within it.
    code, log = daily_build(healthy(silent=(ARTICLES[1], tuple(TICK_DAYS[2:5]))), tmp_path)
    assert code == 0, log[-3000:]
    assert "assert_every_due_day_arrived_in_time" not in failed_checks(log)
    seen = statuses(tmp_path / "warehouse.duckdb", ARTICLES[1])
    assert {day: seen[day] for day in ("2026-10-01", "2026-10-02", "2026-10-03")} == {
        "2026-10-01": "late",
        "2026-10-02": "late",
        "2026-10-03": "on_time",
    }
    assert set(statuses(tmp_path / "warehouse.duckdb", ARTICLES[0]).values()) <= {
        "on_time",
        "pending",
    }


def test_missing_fails_until_it_is_recovered_then_it_is_late_and_passes(tmp_path: Path) -> None:
    w = healthy(silent=(ARTICLES[2], tuple(TICK_DAYS[3:])))
    path = tmp_path / "warehouse.duckdb"
    code, log = daily_build(w, tmp_path)
    assert code != 0 and failed_checks(log) == {"assert_every_due_day_arrived_in_time"}
    assert statuses(path, ARTICLES[2])["2026-10-02"] == "missing"
    assert statuses(path, ARTICLES[2])["2026-10-06"] == "pending"  # its deadline is ahead

    # A repair run, an hour before the build, brings every missing day back.
    repaired_at = AS_OF - timedelta(hours=1)
    rows = [
        period_row(ARTICLES[2], datetime(2026, 10, day, tzinfo=UTC), repaired_at, 1.0, "repair")
        for day in range(2, 7)
    ]
    w.writer.land(Table.PERIOD_OBSERVATIONS, PAGEVIEWS, "repair", repaired_at, rows)
    w.load_everything("load-repair")
    copy_to_duckdb_raw(w, path)  # an incremental build over what dbt already built
    variables = json.dumps({"run_as_of": f"{naive(AS_OF):%Y-%m-%d %H:%M:%S}"})
    for _ in range(2):  # and the day after, nothing about it fails again
        done = _dbt(
            "build", "--target", "offline", "--selector", "daily", "--vars", variables,
            database=path, check=False,
        )  # fmt: skip
        assert done.returncode == 0, done.stdout[-3000:]
    seen = statuses(path, ARTICLES[2])
    assert [seen[f"2026-10-0{day}"] for day in range(2, 7)] == ["late"] * 4 + ["on_time"]


def test_a_late_breach_stays_on_record_after_it_leaves_the_check_window(tmp_path: Path) -> None:
    code, log = daily_build(healthy(silent=(ARTICLES[1], tuple(TICK_DAYS[2:5]))), tmp_path)
    assert code == 0, log[-2000:]
    path = tmp_path / "warehouse.duckdb"
    # Three weeks later an ordinary incremental run, which reads only its recent window.
    build(path, as_of=AS_OF + timedelta(days=21), selector="daily", checks=False)
    assert statuses(path, ARTICLES[1])["2026-10-01"] == "late"
    assert statuses(path, ARTICLES[1])["2026-10-02"] == "late"


# --- the steady-state day, end to end: collect at 12:00, load and check at 13:00 -------------


class OfflineDbt:
    """What the warehouse job asks dbt for, run against DuckDB instead of BigQuery."""

    def __init__(self, w: World, path: Path, *, real: bool) -> None:
        self.w, self.path, self.real = w, path, real
        self.selectors: list[str] = []
        self.log = ""

    def __call__(self, arguments: Sequence[str], environment: Mapping[str, str]) -> int:
        selector = arguments[arguments.index("--selector") + 1]
        self.selectors.append(selector)
        if not self.real:
            return 0
        if self.path.exists():
            copy_to_duckdb_raw(self.w, self.path)
        else:
            copy_to_duckdb(self.w, self.path)
        variables = arguments[arguments.index("--vars") + 1]
        done = _dbt(
            "build", "--target", "offline", "--selector", selector, "--vars", variables,
            database=self.path, check=False,
        )  # fmt: skip
        self.log = done.stdout
        return done.returncode


def test_steady_state_collects_at_twelve_then_loads_and_checks_at_thirteen(
    tmp_path: Path,
) -> None:
    start = datetime(2026, 10, 6, tzinfo=UTC)  # a Tuesday
    clock = Clock(start)
    w = world(clock, policy_for=real_policy)
    ids = [t.source_native_id for t in bootstrap_targets(start).targets]
    seed_bootstrap_markers(
        w.operations, WIKIMEDIA, ids, days=90, data_object="x", content_sha256="a" * 64,
        collection_run_id="first", completed_at=start,
    )  # fmt: skip
    arm(w.operations, start + timedelta(days=10))
    path = tmp_path / "warehouse.duckdb"
    days = 8
    started: dict[tuple[int, int], list[str]] = {}
    builds: list[tuple[int, int, str, int]] = []
    for day in range(days):
        for hour in range(24):
            clock.moment = (start + timedelta(days=day)).replace(hour=hour, second=9)
            # Each job runs as its command does: between its 'started' and 'finished'
            # run records, which the collector receipt check reads.
            runtime, _, starter = dispatcher(w, clock, run_id=f"tick-{clock.moment:%m%d%H}")
            ran = runlog.run_job(
                runtime, runlog.landed(runtime, "dispatch"), partial(dispatch, runtime, starter)
            )
            assert ran == runlog.EXIT_OK
            if starter.started:
                started[(day, hour)] = list(starter.started)
            for job in starter.started:
                clock.moment += timedelta(minutes=2)
                run_id = f"{job}-{clock.moment:%m%d%H}"
                if job == "ms-wikimedia":
                    collector, _ = ordered_runtime(w, clock, run_id)
                    ran = runlog.run_job(
                        collector,
                        runlog.landed(collector, WIKIMEDIA),
                        partial(collect_wikimedia_job, collector, Pageviews),  # type: ignore[arg-type]
                        source=WIKIMEDIA,
                    )
                    assert ran == runlog.EXIT_OK
                else:
                    assert job == "ms-warehouse"
                    w.now = clock
                    job_runtime, _ = runtime_for(w, job, run_id=run_id)
                    dbt = OfflineDbt(w, path, real=day >= days - 2)
                    ran = runlog.run_job(
                        job_runtime,
                        runlog.direct(job_runtime),
                        partial(warehouse, job_runtime, dbt),
                    )
                    builds.append((day, hour, dbt.selectors[-1], ran))
                    assert ran == runlog.EXIT_OK, dbt.log[-3000:]
                    if dbt.real:
                        assert set(re.findall(r"PASS (\w+)", dbt.log)) >= CHECKS
    # One collection at 12:00 and one warehouse run at 13:00, every day, and nothing else.
    assert PRODUCTION.period_hour == 12 and PRODUCTION.warehouse_daily_hour == 13
    assert started == {
        **{(day, 12): ["ms-wikimedia"] for day in range(days)},
        **{(day, 13): ["ms-warehouse"] for day in range(days)},
    }
    # The 13:00 run is the daily selection, which is the one that runs the checks; the
    # last two were real dbt builds (a first build, then an incremental one).
    assert builds == [(day, 13, "daily", 0) for day in range(days)]
    final = query(
        path, "select status, count(*) from ms_marts.period_due_day_status group by 1 order by 1"
    )
    # Every planned day arrived with the collection that asked for it.
    assert dict(final) == {"on_time": 14 * days}
