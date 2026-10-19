"""The dbt project, run offline against DuckDB, and held to the Python semantics.

The rows dbt reads here were produced the way production rows are: landed as
batches, loaded by the loader, then copied as they stand into a local DuckDB file
whose tables have the shapes Terraform declares. dbt runs in its own tool
environment (`warehouse/`), so these tests start it as a subprocess. Nothing reaches
BigQuery: the project's default target is the offline one.

If the dbt environment cannot be started the tests are skipped, unless
MS_REQUIRE_DBT=1 (set in CI), in which case that is a failure.
"""

import json
import os
import random
import re
import shutil
import subprocess
from collections import Counter
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
import yaml

from metaspike.domain.samples import SampleAttempt, SampleStatus, SlotState
from metaspike.domain.signals import SourceErrorCode
from metaspike.domain.timeseries import TimeWindow
from metaspike.warehouse.landing import TABLES, Table, encode_rows, sample_attempt_row, timestamp
from metaspike.warehouse.stores import WarehouseSampleStore
from metaspike.warehouse.tables import TableRef
from tests.test_sampling import KEY, SLOT, SOURCE, attempt, minutes
from tests.warehouse_support import World, table_schemas, world

REPO = Path(__file__).parent.parent
PROJECT = REPO / "warehouse"
ATTEMPTS = TableRef("ms_raw", "sample_attempts")
# The run's "now": dbt recomputes the three whole UTC days before it.
AS_OF = datetime(2026, 10, 9, 6, 0, tzinfo=UTC)
WINDOW_START = datetime(2026, 10, 6, tzinfo=UTC)
D0 = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)

_DUCKDB_TYPES = {
    "STRING": "VARCHAR",
    "INT64": "BIGINT",
    "FLOAT64": "DOUBLE",
    "TIMESTAMP": "TIMESTAMP",
    "JSON": "JSON",
    "BOOL": "BOOLEAN",
    "DATE": "DATE",
}


def _dbt(*arguments: str, database: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    uv = shutil.which("uv")
    assert uv is not None
    command = [
        uv, "run", "--project", str(PROJECT), "--locked", "dbt", *arguments,
        "--project-dir", str(PROJECT), "--profiles-dir", str(PROJECT),
        "--target-path", str(database.parent / "target"),
        "--log-path", str(database.parent / "logs"),
    ]  # fmt: skip
    environment = {**os.environ, "MS_DBT_DUCKDB_PATH": str(database), "DO_NOT_TRACK": "1"}
    environment.pop("VIRTUAL_ENV", None)
    done = subprocess.run(
        command, capture_output=True, text=True, env=environment, cwd=REPO, timeout=600
    )
    if check and done.returncode != 0:
        raise AssertionError(f"dbt {' '.join(arguments)} failed:\n{done.stdout}\n{done.stderr}")
    return done


@pytest.fixture(scope="session")
def dbt_ready(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Start the dbt environment once, or say plainly why these tests did not run."""
    required = os.environ.get("MS_REQUIRE_DBT") == "1"
    reason = None
    if shutil.which("uv") is None:
        reason = "uv is not installed"
    else:
        probe = _dbt(
            "parse", "--target", "offline",
            database=tmp_path_factory.mktemp("probe") / "probe.duckdb", check=False,
        )  # fmt: skip
        if probe.returncode != 0:
            reason = f"the dbt environment could not be started: {probe.stderr[-400:]}"
    if reason is not None:
        if required:
            pytest.fail(reason)
        pytest.skip(reason)


def naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)


def copy_to_duckdb(w: World, path: Path) -> None:
    """The warehouse's tables, as they stand, in a DuckDB file with the declared shapes."""
    connection = duckdb.connect(str(path))
    try:
        for name, fields in table_schemas().items():
            dataset, table = name.split(".")
            connection.execute(f"create schema if not exists {dataset}")
            columns = ", ".join(
                f'"{f["name"]}" {_DUCKDB_TYPES[f["type"]]}'
                + ("[]" if f.get("mode") == "REPEATED" else "")
                for f in fields
            )
            connection.execute(f"create or replace table {name} ({columns})")
            rows = w.warehouse.all_rows(TableRef(dataset, table))
            if rows:
                marks = ", ".join("?" for _ in fields)
                connection.executemany(
                    f"insert into {name} values ({marks})",
                    [[_cell(row[f["name"]], f) for f in fields] for row in rows],
                )
    finally:
        connection.close()


def _cell(value: Any, field: dict[str, Any]) -> Any:
    if isinstance(value, datetime):
        return naive(value)
    if field["type"] == "JSON" and value is not None:
        return json.dumps(value)
    return value


def query(path: Path, sql: str) -> list[tuple[Any, ...]]:
    connection = duckdb.connect(str(path), read_only=True)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def build(
    path: Path,
    *extra: str,
    as_of: datetime = AS_OF,
    selector: str = "hourly",
    rebuild_from: dict[str, str] | None = None,
    checks: bool = True,
) -> str:
    """`checks=False` leaves out the production data checks, for tests about the models
    that deliberately build from empty or partial data. They are on by default, as in
    production."""
    variables = json.dumps(
        {
            "run_as_of": f"{naive(as_of):%Y-%m-%d %H:%M:%S}",
            "production_data_checks": checks,
            **{f"rebuild_from_{group}": day for group, day in (rebuild_from or {}).items()},
        }
    )
    return _dbt(
        "build", "--target", "offline", "--selector", selector, "--vars", variables, *extra,
        database=path,
    ).stdout  # fmt: skip


def generated_attempts(
    seed: int = 20261007, apps: int = 12, slots: int = 40
) -> list[SampleAttempt]:
    """A deterministic mess: retries, duplicates, ties, zeros, refusals, gaps, late answers."""
    chooser = random.Random(seed)
    made: list[SampleAttempt] = []
    counter = 0
    for app_index in range(apps):
        app = str(1000 + app_index * 7)
        for slot_index in range(slots):
            slot = D0 + slot_index * SLOT.step
            roll = chooser.random()
            if roll < 0.18:
                continue  # nobody sampled this slot
            for _ in range(chooser.choice([1, 1, 1, 2, 2, 3, 4])):
                counter += 1
                status = chooser.choice(
                    [SampleStatus.OBSERVED] * 5
                    + [SampleStatus.FAILED] * 2
                    + [SampleStatus.REFUSED, SampleStatus.UNAVAILABLE]
                )
                # Whole seconds, drawn from few values, so equal times really happen
                # and the attempt id has to break the tie.
                at = slot + timedelta(seconds=chooser.choice([5, 5, 40, 40, 300, 610, 890]))
                made.append(
                    attempt(
                        at,
                        status,
                        value=chooser.choice([0, 0, 1, 250, 9000]),
                        app=app,
                        attempt_id=f"{chooser.getrandbits(48):012x}-{counter:05d}",
                        received_at=at + timedelta(seconds=chooser.choice([1, 30, 4000])),
                        scheduled_for=slot,
                        entity=None if app_index % 5 == 0 else attempt(D0).canonical_game_id,
                        error=(
                            chooser.choice([SourceErrorCode.RATE_LIMITED, SourceErrorCode.TIMEOUT])
                            if status in (SampleStatus.FAILED, SampleStatus.REFUSED)
                            else None
                        ),
                    )
                )
    chooser.shuffle(made)
    return made


def loaded_world(attempts: Sequence[SampleAttempt], batches: int = 7) -> World:
    w = world(lambda: AS_OF)
    store = WarehouseSampleStore(w.writer, w.warehouse, w.policy_for, now=w.now)
    size = max(1, len(attempts) // batches)
    for index in range(0, len(attempts), size):
        chunk = [
            a.model_copy(update={"collection_run_id": f"run-{index:05d}"})
            for a in attempts[index : index + size]
        ]
        store.append_many(chunk)
    w.load_everything()
    return w


# Who the dispatcher intended to sample, slot by slot (see `expectations`).
NEVER_ATTEMPTED = "5000"  # scheduled throughout, and nobody ever sampled it
DROPPED = "1007"  # scheduled for the first half only, then no longer eligible
UNSCHEDULED = "1014"  # sampled, but never in the dispatcher's plan
SLOTS = 40
Plan = dict[str, list[datetime]]
Built = tuple[World, Path, Plan]


def expectations(apps: Sequence[str]) -> Plan:
    """The slots each target was expected in. Eligibility changes are point-in-time."""
    plan = {app: [D0 + i * SLOT.step for i in range(SLOTS)] for app in apps}
    plan[NEVER_ATTEMPTED] = [D0 + i * SLOT.step for i in range(SLOTS)]
    plan[DROPPED] = plan[DROPPED][: SLOTS // 2]
    del plan[UNSCHEDULED]
    return plan


def ledger_row(source: str, metric: str, granularity: str, mode: str, app: str, slot: datetime,
               version: str = "g1") -> dict[str, Any]:  # fmt: skip
    return {
        "source": source,
        "metric": metric,
        "granularity": granularity,
        "mode": mode,
        "source_native_id": app,
        "slot_start": timestamp(slot),
        "schedule_version": version,
        "dispatch_run_id": f"tick-{slot:%Y%m%dT%H%MZ}",
        "recorded_at": timestamp(slot),
        "tier": 4,
        "due_at": timestamp(slot),
        "due_reason": "default_long_tail",
        "policy_status": "cleared",
        "disposition": "planned",
        "work_order_id": None,
    }


def land_expectations(w: World, plan: Plan) -> None:
    by_slot: dict[datetime, list[str]] = {}
    for app, slots in plan.items():
        for slot in slots:
            by_slot.setdefault(slot, []).append(app)
    for slot, apps in sorted(by_slot.items()):
        w.writer.land_internal(
            Table.EXPECTED_TARGETS,
            "dispatch",
            f"tick-{slot:%Y%m%dT%H%MZ}",
            slot,
            [
                ledger_row(SOURCE, "concurrent_players", SLOT.value, "sample_now", app, slot)
                for app in sorted(apps)
            ],
        )


@pytest.fixture(scope="module")
def built(dbt_ready: None, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Built]:
    attempts = generated_attempts()
    w = loaded_world(attempts)
    plan = expectations(sorted({a.source_native_id for a in attempts}))
    land_expectations(w, plan)
    # A period source is fetched, not sampled: its intent is recorded and never
    # becomes a sampling slot.
    day = datetime(2026, 10, 7, tzinfo=UTC)
    w.writer.land_internal(
        Table.EXPECTED_TARGETS,
        "dispatch",
        "tick-daily",
        D0,
        [
            ledger_row(
                "wikimedia_pageviews",
                "pageviews",
                "daily",
                "fetch_history",
                "en.wikipedia.org/Minecraft",
                day,
            )
        ],
    )
    w.load_everything("load-ledger")
    # One batch loaded a second time, as a failed guard would: staging must absorb it.
    again = [
        {**row, "loaded_at": "2026-10-09T05:00:00.000000Z", "landed_object": "loaded/twice"}
        for row in w.warehouse.all_rows(ATTEMPTS)[:25]
    ]
    for row in again:
        for column in ("scheduled_for", "slot_start", "observed_at", "received_at"):
            row[column] = row[column].strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    w.warehouse.load("an-accidental-second-load", ATTEMPTS, encode_rows(again))
    path = tmp_path_factory.mktemp("dbt") / "warehouse.duckdb"
    copy_to_duckdb(w, path)
    build(path)
    yield w, path, plan


# --- the project as written ----------------------------------------------------------------


def test_the_project_parses_for_the_offline_and_the_bigquery_target(
    dbt_ready: None, tmp_path: Path
) -> None:
    for target in ("offline", "prod"):
        _dbt("parse", "--target", target, database=tmp_path / f"{target}.duckdb")
        manifest = json.loads((tmp_path / "target" / "manifest.json").read_text())
        models = {n["name"]: n for n in manifest["nodes"].values() if n["resource_type"] == "model"}
        assert set(models) == {
            "stg_sample_attempts",
            "stg_period_observations",
            "stg_source_records",
            "stg_collection_runs",
            "stg_expected_targets",
            "resolved_sample_slots",
            "sample_slot_timeline",
            "sample_coverage_daily",
            "source_health_daily",
        }
        for name, node in models.items():
            assert node["schema"] == ("ms_staging" if name.startswith("stg_") else "ms_marts")
            assert set(node["config"]["tags"]) & {"hourly", "daily"}, name
        # The slots nobody sampled are reconstructed by a view, never written to a table.
        assert models["sample_slot_timeline"]["config"]["materialized"] == "view"
        if target == "prod":
            partitioned = {n: m["config"].get("partition_by") for n, m in models.items()}
            assert partitioned["resolved_sample_slots"] == {
                "field": "slot_start", "data_type": "timestamp", "granularity": "day"
            }  # fmt: skip
            assert models["stg_sample_attempts"]["config"]["incremental_strategy"] == (
                "insert_overwrite"
            )


def test_the_default_target_is_offline_and_every_bigquery_job_is_byte_limited() -> None:
    profile = yaml.safe_load((PROJECT / "profiles.yml").read_text())["metaspike_warehouse"]
    assert profile["target"] == "offline"
    assert profile["outputs"]["offline"]["type"] == "duckdb"
    prod = profile["outputs"]["prod"]
    assert prod["type"] == "bigquery" and prod["location"] == "us-central1"
    assert 0 < prod["maximum_bytes_billed"] <= 1024**3
    assert prod["method"] == "oauth" and "keyfile" not in prod  # the job's own identity
    text = (PROJECT / "profiles.yml").read_text()
    assert not re.search(r"private_key|client_secret|refresh_token|BEGIN ", text)


def test_selectors_and_lookback_are_declared() -> None:
    selectors = yaml.safe_load((PROJECT / "selectors.yml").read_text())["selectors"]
    assert [s["name"] for s in selectors] == ["hourly", "daily", "periods"]
    project = yaml.safe_load((PROJECT / "dbt_project.yml").read_text())
    assert project["vars"]["lookback_days"] == 3
    assert project["vars"]["period_lookback_days"] == 7
    from metaspike.jobs import warehouse_job

    # The job and the models must agree on where a window starts.
    assert project["vars"]["lookback_days"] == warehouse_job.LOOKBACK_DAYS
    assert project["vars"]["period_lookback_days"] == warehouse_job.PERIOD_LOOKBACK_DAYS
    assert set(warehouse_job.COVERS) == {s["name"] for s in selectors}
    assert project["flags"]["send_anonymous_usage_stats"] is False


def test_every_read_of_a_raw_table_is_bounded_on_its_partition_column() -> None:
    partition = {spec.table.value: spec.partition for spec in TABLES.values()}
    readers = 0
    for path in [*PROJECT.glob("models/**/*.sql"), *PROJECT.glob("tests/*.sql")]:
        text = path.read_text()
        for table in re.findall(r"source\('ms_(?:raw|ops)', '(\w+)'\)", text):
            readers += 1
            bound = rf"where {partition[table]} >= {{{{ ms_(?:check_)?window_start\("
            assert re.search(bound, text), f"{path.name} reads {table} without a window"
    assert readers >= 5


def test_no_feature_and_no_score_is_computed_in_sql() -> None:
    forbidden = re.compile(
        r"\b(score|robust_z|z_score|log_lift|lift|baseline|percentile|quantile|median|stddev|"
        r"variance|momentum|velocity|acceleration|change_point|opportunity|ms_features|"
        r"bucket_evaluations)\b",
        re.I,
    )
    for path in [*PROJECT.glob("models/**/*.sql"), *PROJECT.glob("macros/*.sql")]:
        code = "\n".join(
            line for line in path.read_text().splitlines() if not line.lstrip().startswith("--")
        )
        code = re.sub(r"\{#.*?#\}", "", code, flags=re.S)
        assert not forbidden.search(code), path.name


# --- parity with Python ------------------------------------------------------------------


def test_sql_slot_resolution_is_identical_to_python_resolve_slot(built: Built) -> None:
    w, path, _ = built
    store = WarehouseSampleStore(w.writer, w.warehouse, w.policy_for, now=w.now)
    span = TimeWindow(start=D0, end=D0 + 39 * SLOT.step)
    expected: dict[tuple[str, datetime], tuple[Any, ...]] = {}
    for app in store.native_ids(KEY):
        for slot in store.read(KEY, app, span, AS_OF):
            if slot.state is SlotState.NOT_SAMPLED:
                continue
            chosen = slot.selected
            expected[(app, naive(slot.key.slot_start))] = (
                slot.state.value,
                None if chosen is None else chosen.attempt_id,
                slot.value,
                None if chosen is None else naive(chosen.observed_at),
                len(slot.attempts),
                len(slot.duplicates),
            )
    found = {
        (row[0], row[1]): row[2:]
        for row in query(
            path,
            "select source_native_id, slot_start, state, selected_attempt_id, value, "
            "selected_observed_at, attempts, duplicate_successes "
            "from ms_marts.resolved_sample_slots",
        )
    }
    assert len(expected) > 350
    assert found == expected  # any difference fails
    states = Counter(value[0] for value in expected.values())
    assert set(states) == {"observed", "unavailable", "failed", "refused"}
    assert any(v[0] == "observed" and v[2] == 0 for v in expected.values())  # a real zero
    assert any(v[5] > 0 for v in expected.values())  # duplicate successes


def test_expected_slots_come_from_the_ledger_and_are_joined_to_what_happened(
    built: Built,
) -> None:
    w, path, plan = built
    store = WarehouseSampleStore(w.writer, w.warehouse, w.policy_for, now=w.now)
    # What Python says each EXPECTED slot is, from the attempts alone.
    expected = {}
    for app, slots in plan.items():
        for slot in slots:
            resolved = store.resolve(KEY, app, slot, AS_OF)
            expected[(app, naive(slot))] = (resolved.state.value, resolved.value)
    timeline = query(
        path,
        "select source_native_id, slot_start, was_expected, state, value "
        "from ms_marts.sample_slot_timeline",
    )
    found = {(row[0], row[1]): (row[3], row[4]) for row in timeline if row[2]}
    assert found == expected and len(expected) > 400

    states = {key: state for key, (state, _) in found.items()}
    # An eligible target that nobody attempted: every one of its slots is NOT_SAMPLED.
    never = [state for (app, _), state in states.items() if app == NEVER_ATTEMPTED]
    assert never == ["not_sampled"] * SLOTS
    assert NEVER_ATTEMPTED not in store.native_ids(KEY)  # it has no attempt at all
    # A successful attempt is OBSERVED; an attempt with no usable answer keeps its state.
    assert set(states.values()) == {"observed", "unavailable", "failed", "refused", "not_sampled"}
    # Missing is never zero: an unsampled slot has no value at all.
    assert all(value is None for state, value in found.values() if state != "observed")

    # A target that stopped being eligible has no expected slot afterwards, and the
    # slots it was expected in before the change are still there.
    dropped = sorted(slot for (app, slot) in states if app == DROPPED)
    assert len(dropped) == SLOTS // 2
    assert dropped[-1] == naive(D0 + (SLOTS // 2 - 1) * SLOT.step)
    # A target that was never scheduled has no expected slot. What was attempted for
    # it is shown, marked as not expected, and is not counted as coverage.
    unexpected = [row for row in timeline if not row[2]]
    assert {row[0] for row in unexpected} == {UNSCHEDULED, DROPPED}
    assert all(row[3] != "not_sampled" for row in unexpected)
    assert not [row for row in timeline if row[0].startswith("en.wikipedia")]  # not a sampler

    coverage = query(
        path,
        "select source_native_id, sum(expected_slots), sum(observed_slots), "
        "sum(not_sampled_slots), sum(unexpected_attempt_slots) "
        "from ms_marts.sample_coverage_daily group by 1",
    )
    assert {row[0] for row in coverage} == {*plan, UNSCHEDULED}
    for app, slots, observed, unsampled, extra in coverage:
        mine = Counter(state for (a, _), state in states.items() if a == app)
        assert (slots, observed, unsampled) == (
            sum(mine.values()), mine["observed"], mine["not_sampled"]
        )  # fmt: skip
        assert extra == len([row for row in unexpected if row[0] == app])


def test_the_ledger_is_point_in_time_and_nothing_fake_is_inserted(built: Built) -> None:
    w, path, plan = built
    staged = query(
        path,
        "select source_native_id, slot_start, schedule_version, recorded_at "
        "from ms_staging.stg_expected_targets where mode = 'sample_now'",
    )
    assert len(staged) == sum(len(slots) for slots in plan.values())
    assert {row[2] for row in staged} == {"g1"}
    # The intent for a slot was recorded at that slot, not afterwards.
    assert all(row[3] == row[1] for row in staged)
    assert "not_sampled" not in {row["status"] for row in w.warehouse.all_rows(ATTEMPTS)}


def test_not_sampled_is_never_a_stored_row(built: Built) -> None:
    _, path, _ = built
    tables = query(
        path,
        "select table_schema, table_name, table_type from information_schema.tables "
        "where table_schema like 'ms_%'",
    )
    kinds = {f"{schema}.{name}": kind for schema, name, kind in tables}
    assert kinds["ms_marts.sample_slot_timeline"] == "VIEW"
    for name, kind in kinds.items():
        if kind == "VIEW":
            continue
        text_columns = query(
            path,
            "select column_name from information_schema.columns where table_schema || '.' || "
            f"table_name = '{name}' and data_type = 'VARCHAR'",
        )
        for (column,) in text_columns:
            hits = query(path, f"select count(*) from {name} where {column} = 'not_sampled'")
            assert hits == [(0,)], f"{name}.{column}"


def test_staging_absorbs_a_batch_that_was_loaded_twice(built: Built) -> None:
    _, path, _ = built
    raw = query(path, "select count(*), count(distinct attempt_id) from ms_raw.sample_attempts")
    staged = query(
        path, "select count(*), count(distinct attempt_id) from ms_staging.stg_sample_attempts"
    )
    assert raw[0][0] == raw[0][1] + 25
    assert staged == [(raw[0][1], raw[0][1])]
    health = query(path, "select sum(attempts) from ms_marts.source_health_daily")
    assert health == [(raw[0][1],)]


# --- incremental behaviour -----------------------------------------------------------------


def snapshot(path: Path) -> dict[str, list[tuple[Any, ...]]]:
    return {
        table: sorted(query(path, f"select * from {table}"), key=repr)
        for table in (
            "ms_staging.stg_sample_attempts",
            "ms_marts.resolved_sample_slots",
            "ms_staging.stg_expected_targets",
            "ms_marts.sample_coverage_daily",
            "ms_marts.source_health_daily",
        )
    }


def test_incremental_runs_are_repeatable_and_pick_up_late_batches_within_the_lookback(
    dbt_ready: None, tmp_path: Path
) -> None:
    early = list(generated_attempts(apps=4, slots=12))
    w = loaded_world(early, batches=3)
    path = tmp_path / "warehouse.duckdb"
    copy_to_duckdb(w, path)
    first_log = build(path)
    assert "ERROR=0" in first_log and "WARN=0" in first_log
    first = snapshot(path)
    build(path)  # the same run again changes nothing
    assert snapshot(path) == first

    app = "1000"
    slot = D0 + 5 * SLOT.step
    before = query(
        path,
        "select state, value from ms_marts.resolved_sample_slots "
        f"where source_native_id = '{app}' and slot_start = '{naive(slot)}'",
    )
    # A batch arrives late: an earlier success for a slot already resolved (inside the
    # lookback), and an attempt for a day the lookback no longer reaches.
    late = attempt(slot + timedelta(seconds=1), value=4242, app=app, attempt_id="0000-late",
                   scheduled_for=slot)  # fmt: skip
    ancient_slot = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
    ancient = attempt(ancient_slot + minutes(1), value=7, app=app, attempt_id="0000-ancient",
                      scheduled_for=ancient_slot)  # fmt: skip
    store = WarehouseSampleStore(w.writer, w.warehouse, w.policy_for, now=w.now)
    store.append_many(
        [a.model_copy(update={"collection_run_id": "late-run"}) for a in (late, ancient)]
    )
    w.load_everything("load-2")
    copy_raw_only(w, path)
    build(path)
    after = query(
        path,
        "select state, value, selected_attempt_id from ms_marts.resolved_sample_slots "
        f"where source_native_id = '{app}' and slot_start = '{naive(slot)}'",
    )
    assert after == [("observed", 4242, "0000-late")] and before != [("observed", 4242)]
    untouched = {
        k: [r for r in rows if "0000-late" not in repr(r)] for k, rows in snapshot(path).items()
    }
    assert untouched["ms_marts.source_health_daily"] != first["ms_marts.source_health_daily"]
    # Outside the lookback a plain incremental run does not see the row. The warehouse
    # job never leaves it there: it passes the day the late data reaches back to, and
    # the same incremental run rebuilds from that day, with no full refresh.
    count = "select count(*) from ms_marts.resolved_sample_slots where slot_start < '2026-10-03'"
    assert query(path, count) == [(0,)]
    build(path, rebuild_from={"samples": "2026-10-02 00:00:00"})
    assert query(path, count) == [(1,)]
    targeted = snapshot(path)
    build(path, rebuild_from={"samples": "2026-10-02 00:00:00"})
    assert snapshot(path) == targeted
    # A full refresh, which reads all of history, arrives at exactly the same tables.
    build(path, "--full-refresh")
    assert snapshot(path) == targeted


def copy_raw_only(w: World, path: Path) -> None:
    """Refresh the raw tables in place, leaving what dbt built where it is."""
    connection = duckdb.connect(str(path))
    try:
        fields = table_schemas()["ms_raw.sample_attempts"]
        connection.execute("delete from ms_raw.sample_attempts")
        marks = ", ".join("?" for _ in fields)
        connection.executemany(
            f"insert into ms_raw.sample_attempts values ({marks})",
            [[_cell(row[f["name"]], f) for f in fields] for row in w.warehouse.all_rows(ATTEMPTS)],
        )
    finally:
        connection.close()


def test_a_contradictory_copy_of_an_attempt_fails_the_data_tests(
    dbt_ready: None, tmp_path: Path
) -> None:
    attempts = generated_attempts(apps=2, slots=6)
    w = loaded_world(attempts, batches=2)
    row = sample_attempt_row(attempts[0])
    contradiction = {
        **row,
        "status": "observed",
        "value": 123456,
        "error_code": None,
        "quality_flags": [f for f in row["quality_flags"] if f != "zero_reported"],
        "landed_object": "contradiction",
        "content_sha256": "0" * 64,
        "loaded_at": "2026-10-09T05:00:00.000000Z",
    }
    w.warehouse.load("contradiction", ATTEMPTS, encode_rows([contradiction]))
    path = tmp_path / "warehouse.duckdb"
    copy_to_duckdb(w, path)
    variables = json.dumps({"run_as_of": f"{naive(AS_OF):%Y-%m-%d %H:%M:%S}"})
    done = _dbt(
        "build", "--target", "offline", "--selector", "hourly", "--vars", variables,
        database=path, check=False,
    )  # fmt: skip
    assert done.returncode != 0
    assert "assert_attempt_copies_agree" in done.stdout and "FAIL" in done.stdout


def test_the_daily_selector_builds_every_model(dbt_ready: None, tmp_path: Path) -> None:
    w = loaded_world(generated_attempts(apps=2, slots=4), batches=1)
    path = tmp_path / "warehouse.duckdb"
    copy_to_duckdb(w, path)
    log = build(path, selector="daily", checks=False)
    assert "ERROR=0" in log
    built_tables = {
        f"{schema}.{name}"
        for schema, name in query(
            path,
            "select table_schema, table_name from information_schema.tables "
            "where table_schema in ('ms_staging', 'ms_marts')",
        )
    }
    assert {"ms_staging.stg_period_observations", "ms_staging.stg_source_records"} <= built_tables
    assert SOURCE and len(built_tables) == 9


# --- full refresh rebuilds ALL retained history ----------------------------------------------

OLD_SLOT = datetime(2026, 6, 15, 9, 0, tzinfo=UTC)  # 116 days before the run
PRE_OCTOBER_SLOT = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)
RECENT_SLOT = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
OLD_COUNT = "select count(*) from ms_marts.resolved_sample_slots where slot_start < '2026-10-01'"
ALL_SLOTS = "select cast(slot_start as varchar) from ms_marts.resolved_sample_slots order by 1"


def historical_world() -> World:
    """Recent samples, and samples from long before any date this project once assumed."""
    w = world(lambda: AS_OF)
    store = WarehouseSampleStore(w.writer, w.warehouse, w.policy_for, now=w.now)
    for name, slot in (("old", OLD_SLOT), ("sept", PRE_OCTOBER_SLOT), ("recent", RECENT_SLOT)):
        one = attempt(
            slot + minutes(1), value=10, app="42", attempt_id=f"h-{name}", scheduled_for=slot
        )
        store.append_many([one.model_copy(update={"collection_run_id": f"run-{name}"})])
        w.writer.land_internal(
            Table.EXPECTED_TARGETS,
            "dispatch",
            f"tick-{name}",
            slot,
            [ledger_row(SOURCE, "concurrent_players", SLOT.value, "sample_now", "42", slot)],
        )
    w.load_everything()
    return w


def test_a_full_refresh_rebuilds_history_older_than_any_fixed_date(
    dbt_ready: None, tmp_path: Path
) -> None:
    assert (AS_OF - OLD_SLOT).days > 90
    w = historical_world()
    path = tmp_path / "warehouse.duckdb"
    copy_to_duckdb(w, path)
    # A first build has no table to be incremental against: it reads all retained history.
    build(path)
    slots = [row[0][:10] for row in query(path, ALL_SLOTS)]
    assert slots == ["2026-06-15", "2026-09-20", "2026-10-08"]
    first = snapshot(path)

    # The normal fast path still recomputes only the recent days.
    build(path)
    assert snapshot(path) == first
    log = tmp_path / "target" / "compiled" / "metaspike_warehouse" / "models" / "staging"
    compiled = (log / "stg_sample_attempts.sql").read_text()
    assert "interval 3 day" in compiled and "2026-06-15" not in compiled

    # A full refresh arrives at the same tables: nothing is lost for being old.
    build(path, "--full-refresh")
    assert snapshot(path) == first
    assert query(path, OLD_COUNT) == [(2,)]
    refreshed = (log / "stg_sample_attempts.sql").read_text()
    assert "timestamp '2026-06-15 00:00:00'" in refreshed  # read from the data, not written down
    coverage = query(
        path, "select cast(day as varchar), expected_slots, observed_slots "
        "from ms_marts.sample_coverage_daily order by 1"
    )  # fmt: skip
    assert [(d[:10], e, o) for d, e, o in coverage] == [
        ("2026-06-15", 1, 1), ("2026-09-20", 1, 1), ("2026-10-08", 1, 1)
    ]  # fmt: skip


def test_full_refresh_equals_the_incremental_and_late_rebuild_route(
    dbt_ready: None, tmp_path: Path
) -> None:
    """Old data that arrives late, rebuilt incrementally, equals a full refresh of it all."""
    recent_only = world(lambda: AS_OF)
    store = WarehouseSampleStore(
        recent_only.writer, recent_only.warehouse, recent_only.policy_for, now=recent_only.now
    )
    one = attempt(RECENT_SLOT + minutes(1), value=10, app="42", attempt_id="h-recent",
                  scheduled_for=RECENT_SLOT)  # fmt: skip
    store.append_many([one.model_copy(update={"collection_run_id": "run-recent"})])
    recent_only.writer.land_internal(
        Table.EXPECTED_TARGETS, "dispatch", "tick-recent", RECENT_SLOT,
        [ledger_row(SOURCE, "concurrent_players", SLOT.value, "sample_now", "42", RECENT_SLOT)],
    )  # fmt: skip
    recent_only.load_everything()
    stepwise = tmp_path / "stepwise.duckdb"
    copy_to_duckdb(recent_only, stepwise)
    build(stepwise)
    assert query(stepwise, OLD_COUNT) == [(0,)]

    # The old batches arrive. A plain incremental run does not reach them; the late-data
    # rebuild, told the day they reach back to, does.
    everything = historical_world()
    copy_to_duckdb_raw(everything, stepwise)
    build(stepwise)
    assert query(stepwise, OLD_COUNT) == [(0,)]
    build(stepwise, rebuild_from={"samples": "2026-06-15 00:00:00"})
    assert query(stepwise, OLD_COUNT) == [(2,)]

    direct = tmp_path / "direct.duckdb"
    copy_to_duckdb(everything, direct)
    build(direct, "--full-refresh")

    def logical(path: Path) -> dict[str, list[tuple[Any, ...]]]:
        # Everything but the load bookkeeping, which differs between the two worlds.
        return {
            table: sorted(query(path, f"select * from {table}"), key=repr)
            for table in (
                "ms_marts.resolved_sample_slots",
                "ms_marts.sample_coverage_daily",
                "ms_marts.source_health_daily",
            )
        }

    assert logical(stepwise) == logical(direct)
    build(stepwise, "--full-refresh")
    assert logical(stepwise) == logical(direct)
    assert [row[0][:10] for row in query(direct, ALL_SLOTS)] == [
        "2026-06-15", "2026-09-20", "2026-10-08"
    ]  # fmt: skip


def copy_to_duckdb_raw(w: World, path: Path) -> None:
    """Refresh every raw table in place, leaving what dbt built where it is."""
    connection = duckdb.connect(str(path))
    try:
        for name, fields in table_schemas().items():
            dataset, table = name.split(".")
            connection.execute(f"delete from {name}")
            rows = w.warehouse.all_rows(TableRef(dataset, table))
            if rows:
                marks = ", ".join("?" for _ in fields)
                connection.executemany(
                    f"insert into {name} values ({marks})",
                    [[_cell(row[f["name"]], f) for f in fields] for row in rows],
                )
    finally:
        connection.close()


def test_an_empty_warehouse_full_refreshes_to_empty_tables(dbt_ready: None, tmp_path: Path) -> None:
    path = tmp_path / "warehouse.duckdb"
    copy_to_duckdb(world(lambda: AS_OF), path)
    log = build(path, "--full-refresh", selector="daily", checks=False)
    assert "ERROR=0" in log
    assert query(path, "select count(*) from ms_marts.resolved_sample_slots") == [(0,)]
    assert query(path, "select count(*) from ms_staging.stg_period_observations") == [(0,)]


def test_the_history_boundary_can_be_stated_explicitly_for_a_deliberate_replay(
    dbt_ready: None, tmp_path: Path
) -> None:
    path = tmp_path / "warehouse.duckdb"
    copy_to_duckdb(historical_world(), path)
    variables = json.dumps(
        {"run_as_of": f"{naive(AS_OF):%Y-%m-%d %H:%M:%S}", "history_start": "2026-09-01 00:00:00"}
    )
    _dbt("build", "--target", "offline", "--selector", "hourly", "--full-refresh",
         "--vars", variables, database=path)  # fmt: skip
    assert [row[0][:10] for row in query(path, ALL_SLOTS)] == ["2026-09-20", "2026-10-08"]


def test_no_fixed_history_date_remains_and_the_guards_are_still_in_place() -> None:
    project = yaml.safe_load((PROJECT / "dbt_project.yml").read_text())
    assert project["vars"]["history_start"] is None
    for path in [*PROJECT.glob("models/**/*.sql"), *PROJECT.glob("macros/*.sql"),
                 *PROJECT.glob("tests/*.sql"), PROJECT / "dbt_project.yml"]:  # fmt: skip
        assert not re.search(r"20\d\d-\d\d-\d\d", path.read_text()), path.name
    window = (PROJECT / "macros" / "window.sql").read_text()
    # The boundary is read from partition metadata, which needs no partition filter.
    assert "INFORMATION_SCHEMA.PARTITIONS" in window and "total_rows > 0" in window
    for group in ("samples", "periods", "records", "runs"):
        assert f"'{group}':" in window
    # The raw tables still refuse an unbounded read, and every dbt job is still limited.
    assert (
        "require_partition_filter = true" in (REPO / "infra" / "prod" / "bigquery.tf").read_text()
    )
    prod = yaml.safe_load((PROJECT / "profiles.yml").read_text())["metaspike_warehouse"]["outputs"]
    assert prod["prod"]["maximum_bytes_billed"] == 1024**3


# --- C2-A: the period models look back seven days ------------------------------------------

PAGEVIEWS = "wikimedia_pageviews"
ARTICLE = "en.wikipedia.org/Minecraft"
PERIOD_ROWS = "select cast(period_start as varchar), value from ms_staging.stg_period_observations"


def land_days(w: World, run: str, days: Sequence[datetime], value: float, seen: datetime) -> None:
    """One landed batch of daily pageviews for one article, as the collector writes it."""
    w.writer.land(
        Table.PERIOD_OBSERVATIONS,
        PAGEVIEWS,
        run,
        seen,
        [
            {
                "source": PAGEVIEWS,
                "metric": "pageviews",
                "source_identifier": ARTICLE,
                "canonical_game_id": "game-minecraft",
                "granularity": "daily",
                "period_start": timestamp(day),
                "value": value,
                "status": "observed",
                "observed_at": timestamp(seen),
                "collection_run_id": run,
                "source_timestamp": timestamp(day),
            }
            for day in days
        ],
    )


def day(month: int, number: int) -> datetime:
    return datetime(2026, month, number, tzinfo=UTC)


def staged(path: Path) -> list[tuple[str, float]]:
    return [(d[:10], v) for d, v in query(path, PERIOD_ROWS + " order by 1, 2")]


def test_a_routine_four_day_collection_is_ordinary_work_for_the_period_models(
    dbt_ready: None, tmp_path: Path
) -> None:
    from metaspike.jobs.warehouse_job import normal_window_start

    # AS_OF is 9 October 06:00. Seven days back is the 2nd; three days back is the 6th.
    assert normal_window_start(AS_OF, "periods") == day(10, 2)
    assert normal_window_start(AS_OF, "samples") == day(10, 6) == normal_window_start(AS_OF)
    w = world(lambda: AS_OF)
    history = [day(9, 25) + timedelta(days=n) for n in range(9)]  # 25 Sep to 3 Oct
    land_days(w, "run-fill", history, 100.0, AS_OF - timedelta(days=5))
    w.load_everything("load-1")
    path = tmp_path / "warehouse.duckdb"
    copy_to_duckdb(w, path)
    assert "ERROR=0" in build(path, selector="periods")
    assert len(staged(path)) == 9

    # The daily collection: four days ending at the latest day due, the 7th. Its
    # earliest day, the 4th, is older than three days and inside seven.
    routine = [day(10, 4) + timedelta(days=n) for n in range(4)]
    assert routine[0] < normal_window_start(AS_OF) and routine[0] >= day(10, 2)
    land_days(w, "run-daily", routine, 200.0, AS_OF)
    w.load_everything("load-2")
    copy_to_duckdb_raw(w, path)
    log = build(path, selector="periods")  # a plain incremental run, with no rebuild point
    assert "ERROR=0" in log and "WARN=0" in log
    rows = staged(path)
    assert len(rows) == 13 and ("2026-10-04", 200.0) in rows and ("2026-10-07", 200.0) in rows
    compiled = tmp_path / "target" / "compiled" / "metaspike_warehouse" / "models" / "staging"
    assert "interval 7 day" in (compiled / "stg_period_observations.sql").read_text()
    build(path, selector="periods")  # and again: the same thirteen rows
    assert staged(path) == rows

    # The period selection builds the period models and the run log, and nothing else.
    built_now = {name for (name,) in query(
        path, "select table_name from information_schema.tables where table_schema = 'ms_staging'"
    )}  # fmt: skip
    assert built_now == {"stg_period_observations", "stg_collection_runs"}
    assert query(path, "select count(*) from information_schema.tables "
                       "where table_schema = 'ms_marts'") == [(0,)]  # fmt: skip


def test_older_period_data_is_still_late_and_is_rebuilt_from_where_it_reaches(
    dbt_ready: None, tmp_path: Path
) -> None:
    w = world(lambda: AS_OF)
    land_days(w, "run-recent", [day(10, 5), day(10, 6), day(10, 7)], 100.0, AS_OF)
    w.load_everything("load-1")
    path = tmp_path / "warehouse.duckdb"
    copy_to_duckdb(w, path)
    build(path, selector="periods")
    before = staged(path)
    # A revision of a day eight days back, and a day far older: both beyond seven days.
    land_days(w, "run-late", [day(10, 1), day(9, 10)], 999.0, AS_OF + timedelta(minutes=1))
    w.load_everything("load-2")
    copy_to_duckdb_raw(w, path)
    build(path, selector="periods")
    assert staged(path) == before  # a plain incremental run does not reach them
    # The warehouse job passes the day the late rows reach back to; nothing is refreshed.
    build(path, selector="periods", rebuild_from={"periods": "2026-09-10 00:00:00"})
    late = staged(path)
    assert late == sorted([*before, ("2026-09-10", 999.0), ("2026-10-01", 999.0)])

    # A full refresh reads all retained history and arrives at exactly the same rows.
    build(path, "--full-refresh", selector="periods")
    assert staged(path) == late
    compiled = tmp_path / "target" / "compiled" / "metaspike_warehouse" / "models" / "staging"
    refreshed = (compiled / "stg_period_observations.sql").read_text()
    assert "timestamp '2026-09-10 00:00:00'" in refreshed  # read from the data, not written down
    assert "interval 7 day" not in refreshed


def test_the_sample_models_keep_their_three_day_lookback(dbt_ready: None, tmp_path: Path) -> None:
    w = loaded_world(generated_attempts(apps=2, slots=4), batches=1)
    path = tmp_path / "warehouse.duckdb"
    copy_to_duckdb(w, path)
    build(path, selector="daily", checks=False)
    build(path, selector="daily", checks=False)  # incremental this time
    compiled = tmp_path / "target" / "compiled" / "metaspike_warehouse" / "models"
    for model in ("staging/stg_sample_attempts", "staging/stg_expected_targets",
                  "staging/stg_collection_runs", "staging/stg_source_records",
                  "marts/resolved_sample_slots"):  # fmt: skip
        text = (compiled / f"{model}.sql").read_text()
        assert "interval 3 day" in text and "interval 7 day" not in text, model
    period = (compiled / "staging" / "stg_period_observations.sql").read_text()
    assert "interval 7 day" in period and "interval 3 day" not in period


def test_the_job_treats_four_days_as_routine_and_anything_older_as_late() -> None:
    from metaspike.jobs.warehouse_job import PENDING_OBJECT, warehouse
    from tests.warehouse_support import runtime_for

    w = world(lambda: AS_OF)
    land_days(w, "run-daily", [day(10, 4) + timedelta(days=n) for n in range(4)], 1.0, AS_OF)
    runtime, log = runtime_for(w, "ms-warehouse")
    calls: list[Sequence[str]] = []

    def dbt(arguments: Sequence[str], environment: Any) -> int:
        calls.append(arguments)
        return 0

    summary = warehouse(runtime, dbt)
    assert summary["selector"] == "periods" and summary["rebuilt_from"] == {}
    assert not [e for e in log if e["event"] == "late_data_loaded"]
    variables = json.loads(calls[0][calls[0].index("--vars") + 1])
    assert variables["period_lookback_days"] == 7 and variables["lookback_days"] == 3
    assert not [k for k in variables if k.startswith("rebuild_from_")]
    assert "--full-refresh" not in calls[0]  # a routine run never refreshes

    land_days(w, "run-late", [day(10, 1)], 2.0, AS_OF)  # eight days back
    summary = warehouse(runtime, dbt)
    assert summary["rebuilt_from"] == {"periods": day(10, 1).isoformat()}
    assert [e["table"] for e in log if e["event"] == "late_data_loaded"] == ["period_observations"]
    variables = json.loads(calls[1][calls[1].index("--vars") + 1])
    assert variables["rebuild_from_periods"] == "2026-10-01 00:00:00"
    assert json.loads(w.operations.get(PENDING_OBJECT).data) == {"since": {}}
