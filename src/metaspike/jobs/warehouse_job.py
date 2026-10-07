"""The warehouse job: load what was landed, then build the models.

Late data. The models normally recompute the three whole UTC days before now. A batch
whose rows are older than that would be loaded into the raw table and never reach the
marts. So the loader reports each batch's earliest partition value BEFORE it writes
the batch's receipt, and anything earlier than the normal window is recorded as a
pending rebuild, durably, by name. The dbt run then starts each affected group of
models from that point instead of from the normal window. The pending record is
cleared only after dbt has succeeded, so:

    a crash after the load keeps the pending record;
    a failed dbt run keeps it and the job exits non-zero;
    no successful run leaves known late data out of the marts.

If a group with pending late data is not part of the hourly selection, the run is
widened to the daily selection, which builds everything.
"""

import json
import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import AwareDatetime, Field, ValidationError

from metaspike.domain.model import FrozenModel
from metaspike.jobs.runtime import Runtime
from metaspike.warehouse.landing import INTERNAL_AREAS, BatchManifest, Table
from metaspike.warehouse.loader import BatchOutcome, Loader
from metaspike.warehouse.objects import ObjectNotFound, PreconditionFailed

LOOKBACK_DAYS = 3
PENDING_OBJECT = "warehouse/pending_rebuild.json"
DAILY_HOUR = 3
SELECTOR_ENV = "METASPIKE_DBT_SELECTOR"
LOAD_DAYS_ENV = "METASPIKE_LOAD_DAYS"
DBT_ENV = "METASPIKE_DBT"
DBT_PROJECT_ENV = "METASPIKE_DBT_PROJECT"
DEFAULT_DBT = "/opt/dbt/bin/dbt"
DEFAULT_DBT_PROJECT = "/app/warehouse"

# Which tables each landing area holds. Only these prefixes are listed.
AREA_TABLES: dict[str, tuple[Table, ...]] = {
    "steam_current_players": (Table.SAMPLE_ATTEMPTS, Table.COLLECTION_RUNS),
    "wikimedia_pageviews": (Table.PERIOD_OBSERVATIONS, Table.COLLECTION_RUNS),
    "igdb": (
        Table.SOURCE_RECORDS,
        Table.BINDING_EVENTS,
        Table.RESOLUTION_DECISIONS,
        Table.QUARANTINE_EVENTS,
    ),
    "steam_catalogue": (
        Table.SOURCE_RECORDS,
        Table.BINDING_EVENTS,
        Table.RESOLUTION_DECISIONS,
        Table.QUARANTINE_EVENTS,
    ),
    "dispatch": (Table.EXPECTED_TARGETS, Table.COLLECTION_RUNS),
    "catalogue": (Table.ENTITY_EVENTS, Table.LIFECYCLE_EVENTS, Table.COLLECTION_RUNS),
}
assert set(AREA_TABLES) >= INTERNAL_AREAS

# The models that must be rebuilt when a table receives late rows, by dbt variable.
GROUP_OF: dict[Table, str] = {
    Table.SAMPLE_ATTEMPTS: "samples",
    Table.EXPECTED_TARGETS: "samples",
    Table.PERIOD_OBSERVATIONS: "periods",
    Table.SOURCE_RECORDS: "records",
    Table.COLLECTION_RUNS: "runs",
}
HOURLY_GROUPS = frozenset({"samples", "runs"})

Dbt = Callable[[Sequence[str], Mapping[str, str]], int]


class PendingRebuild(FrozenModel):
    """Per group of models, the earliest moment that late-loaded data reaches back to."""

    since: dict[str, AwareDatetime] = Field(default_factory=dict)


def normal_window_start(now: datetime) -> datetime:
    """Where an incremental dbt run starts reading. Must match the dbt macro."""
    day = (now.astimezone(UTC) - timedelta(days=LOOKBACK_DAYS)).date()
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


class _Pending:
    def __init__(self, runtime: Runtime) -> None:
        self._store = runtime.operations

    def read(self) -> tuple[PendingRebuild, int | None]:
        try:
            stored = self._store.get(PENDING_OBJECT)
        except ObjectNotFound:
            return PendingRebuild(), None
        try:
            return PendingRebuild.model_validate_json(stored.data), stored.generation
        except ValidationError:
            # Unreadable is treated as "everything may be late", never as "nothing is".
            everything = datetime(2020, 1, 1, tzinfo=UTC)
            return (
                PendingRebuild(since=dict.fromkeys(set(GROUP_OF.values()), everything)),
                stored.generation,
            )

    def note(self, group: str, earliest: datetime) -> None:
        for _ in range(5):
            pending, generation = self.read()
            known = pending.since.get(group)
            if known is not None and known <= earliest:
                return
            body = (
                PendingRebuild(since={**pending.since, group: earliest})
                .model_dump_json()
                .encode("utf-8")
            )
            try:
                if generation is None:
                    self._store.create(PENDING_OBJECT, body)
                else:
                    self._store.replace(PENDING_OBJECT, body, generation)
                return
            except PreconditionFailed:
                continue
        raise RuntimeError("the pending-rebuild record could not be updated")

    def clear(self, groups: frozenset[str], taken: PendingRebuild) -> None:
        """Forget what this run rebuilt, keeping anything noted since it started."""
        for _ in range(5):
            pending, generation = self.read()
            if generation is None:
                return
            kept = {
                group: since
                for group, since in pending.since.items()
                if group not in groups or taken.since.get(group) != since
            }
            try:
                self._store.replace(
                    PENDING_OBJECT,
                    PendingRebuild(since=kept).model_dump_json().encode("utf-8"),
                    generation,
                )
                return
            except PreconditionFailed:
                continue
        raise RuntimeError("the pending-rebuild record could not be cleared")


def run_dbt(arguments: Sequence[str], environment: Mapping[str, str]) -> int:
    """Run the pinned dbt from its own environment. Output goes to the job's log."""
    return subprocess.run(list(arguments), env=dict(environment), check=False).returncode


def warehouse(runtime: Runtime, dbt: Dbt = run_dbt) -> Mapping[str, Any]:
    now = runtime.now()
    window_start = normal_window_start(now)
    pending = _Pending(runtime)

    def before_receipt(manifest: BatchManifest, earliest: datetime) -> None:
        group = GROUP_OF.get(manifest.table)
        if group is not None and earliest < window_start:
            pending.note(group, earliest)
            runtime.log(
                "late_data_loaded",
                severity="WARNING",
                table=manifest.table.value,
                data_object=manifest.data_object,
                earliest=earliest.isoformat(),
                normal_window_start=window_start.isoformat(),
            )

    loader = Loader(
        runtime.landing,
        runtime.operations,
        runtime.warehouse,
        now=runtime.now,
        run_id=runtime.run_id,
        before_receipt=before_receipt,
    )
    days = int(runtime.environ.get(LOAD_DAYS_ENV, "3"))
    loaded = already = rows = 0
    incomplete: list[str] = []
    for area, tables in sorted(AREA_TABLES.items()):
        for table in tables:
            for offset in range(days):
                day = (now.astimezone(UTC) - timedelta(days=offset)).date().isoformat()
                report = loader.load_day(area, table, day)
                rows += report.rows_loaded
                loaded += report.count(BatchOutcome.LOADED) + report.count(BatchOutcome.RECOVERED)
                already += report.count(BatchOutcome.ALREADY_LOADED)
                incomplete.extend(report.incomplete)
    if incomplete:
        runtime.log("incomplete_batches", severity="WARNING", objects=incomplete[:20])

    taken, _ = pending.read()
    selector = runtime.environ.get(SELECTOR_ENV) or (
        "daily" if now.astimezone(UTC).hour == DAILY_HOUR else "hourly"
    )
    if selector == "hourly" and set(taken.since) - HOURLY_GROUPS:
        selector = "daily"  # late data for models the hourly selection does not build
    variables: dict[str, Any] = {
        "run_as_of": f"{now.astimezone(UTC):%Y-%m-%d %H:%M:%S}",
        "lookback_days": LOOKBACK_DAYS,
    }
    for group, since in sorted(taken.since.items()):
        variables[f"rebuild_from_{group}"] = f"{since.astimezone(UTC):%Y-%m-%d} 00:00:00"
    project = runtime.environ.get(DBT_PROJECT_ENV, DEFAULT_DBT_PROJECT)
    scratch = runtime.environ.get("TMPDIR", "/tmp")
    code = dbt(
        [
            runtime.environ.get(DBT_ENV, DEFAULT_DBT),
            "build",
            "--target",
            "prod",
            "--selector",
            selector,
            "--vars",
            json.dumps(variables, sort_keys=True),
            "--project-dir",
            project,
            "--profiles-dir",
            project,
            "--target-path",
            f"{scratch}/dbt-target",
            "--log-path",
            f"{scratch}/dbt-logs",
        ],
        {**os.environ, **runtime.environ, "MS_GCP_PROJECT": runtime.project, "DO_NOT_TRACK": "1"},
    )
    if code != 0:
        runtime.log(
            "warehouse_failed",
            severity="ERROR",
            stage="dbt",
            exit_code=code,
            pending_rebuild={g: s.isoformat() for g, s in taken.since.items()},
        )
        raise RuntimeError(f"dbt exited with {code}; pending rebuilds are kept")
    covered = frozenset(GROUP_OF.values()) if selector == "daily" else HOURLY_GROUPS
    pending.clear(covered, taken)
    return {
        "batches_loaded": loaded,
        "batches_already_loaded": already,
        "rows_loaded": rows,
        "incomplete_batches": len(incomplete),
        "selector": selector,
        "rebuilt_from": {g: s.isoformat() for g, s in sorted(taken.since.items())},
    }
