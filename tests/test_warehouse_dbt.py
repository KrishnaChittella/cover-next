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
from metaspike.warehouse.landing import TABLES, encode_rows, sample_attempt_row
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


def build(path: Path, *extra: str, as_of: datetime = AS_OF, selector: str = "hourly") -> str:
    variables = json.dumps({"run_as_of": f"{naive(as_of):%Y-%m-%d %H:%M:%S}"})
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


@pytest.fixture(scope="module")
def built(
    dbt_ready: None, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[World, Path]]:
    attempts = generated_attempts()
    w = loaded_world(attempts)
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
    yield w, path


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
            "resolved_sample_slots",
            "sample_targets",
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
    assert [s["name"] for s in selectors] == ["hourly", "daily"]
    project = yaml.safe_load((PROJECT / "dbt_project.yml").read_text())
    assert project["vars"]["lookback_days"] == 3
    assert project["flags"]["send_anonymous_usage_stats"] is False


def test_every_read_of_a_raw_table_is_bounded_on_its_partition_column() -> None:
    partition = {spec.table.value: spec.partition for spec in TABLES.values()}
    readers = 0
    for path in [*PROJECT.glob("models/**/*.sql"), *PROJECT.glob("tests/*.sql")]:
        text = path.read_text()
        for table in re.findall(r"source\('ms_(?:raw|ops)', '(\w+)'\)", text):
            readers += 1
            bound = rf"where {partition[table]} >= {{{{ ms_window_start\("
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


def test_sql_slot_resolution_is_identical_to_python_resolve_slot(built: tuple[World, Path]) -> None:
    w, path = built
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


def test_the_timeline_view_reconstructs_the_slots_nobody_sampled(
    built: tuple[World, Path],
) -> None:
    w, path = built
    store = WarehouseSampleStore(w.writer, w.warehouse, w.policy_for, now=w.now)
    latest = max(row["slot_start"] for row in w.warehouse.all_rows(ATTEMPTS))
    expected = {}
    for app in store.native_ids(KEY):
        first = min(
            r["slot_start"] for r in w.warehouse.all_rows(ATTEMPTS) if r["source_native_id"] == app
        )
        for slot in store.read(KEY, app, TimeWindow(start=first, end=latest), AS_OF):
            expected[(app, naive(slot.key.slot_start))] = (slot.state.value, slot.value)
    found = {
        (row[0], row[1]): (row[2], row[3])
        for row in query(
            path,
            "select source_native_id, slot_start, state, value from ms_marts.sample_slot_timeline",
        )
    }
    assert found == expected
    assert sum(1 for state, _ in expected.values() if state == "not_sampled") > 30
    # Missing is never zero: an unsampled slot has no value at all.
    assert all(value is None for state, value in found.values() if state != "observed")

    coverage = query(
        path,
        "select source_native_id, sum(expected_slots), sum(observed_slots), "
        "sum(not_sampled_slots) from ms_marts.sample_coverage_daily group by 1",
    )
    for app, slots, observed, unsampled in coverage:
        states = Counter(state for (a, _), (state, _) in expected.items() if a == app)
        assert (slots, observed, unsampled) == (
            sum(states.values()), states["observed"], states["not_sampled"]
        )  # fmt: skip


def test_not_sampled_is_never_a_stored_row(built: tuple[World, Path]) -> None:
    _, path = built
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


def test_staging_absorbs_a_batch_that_was_loaded_twice(built: tuple[World, Path]) -> None:
    _w, path = built
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
            "ms_marts.sample_targets",
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
    # Outside the lookback an incremental run does not see the row...
    count = "select count(*) from ms_marts.resolved_sample_slots where slot_start < '2026-10-03'"
    assert query(path, count) == [(0,)]
    # ...and a full refresh, which reads all of history, does. The raw table had it all along.
    build(path, "--full-refresh")
    assert query(path, count) == [(1,)]
    rebuilt = snapshot(path)
    build(path, "--full-refresh")
    assert snapshot(path) == rebuilt


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
    log = build(path, selector="daily")
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
