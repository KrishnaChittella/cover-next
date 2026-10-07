"""The run log: one operational record when a job starts and one when it ends.

Rows go to `ms_ops.collection_runs`. A job that can write to the landing bucket lands
them under its own prefix and the loader appends them; a job that cannot (warehouse,
features, parity) loads its own rows directly. Either way a row is our own record of
what a job did. It holds counts and outcomes and never a source's data.
"""

import contextlib
import hashlib
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from metaspike.jobs.runtime import Refused, Runtime
from metaspike.warehouse.landing import TABLES, Table, encode_rows, policy_version, timestamp
from metaspike.warehouse.tables import JobAlreadyExists, TableRef

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 5

Sink = Callable[[dict[str, Any], int], None]


def landed(runtime: Runtime, area: str) -> Sink:
    """Run rows landed under the job's own prefix, for the loader to pick up."""

    def sink(row: dict[str, Any], part: int) -> None:
        runtime.writer.land_internal(
            Table.COLLECTION_RUNS, area, runtime.run_id, runtime.now(), [row], part=part
        )

    return sink


def direct(runtime: Runtime) -> Sink:
    """Run rows loaded straight into the table, for a job with no landing prefix."""
    spec = TABLES[Table.COLLECTION_RUNS]

    def sink(row: dict[str, Any], part: int) -> None:
        data = encode_rows([row])
        digest = hashlib.sha256(data).hexdigest()
        stamped = {
            **row,
            "landed_object": f"direct/{runtime.job}/{runtime.run_id}/{part}",
            "content_sha256": digest,
            "loaded_at": timestamp(runtime.now()),
        }
        # If the id is taken, this exact row is already recorded.
        with contextlib.suppress(JobAlreadyExists):
            runtime.warehouse.load(
                f"ms_run_{digest[:40]}",
                TableRef(spec.dataset, spec.table.value),
                encode_rows([stamped]),
            )

    return sink


def kept(runtime: Runtime, prefix: str) -> Sink:
    """Run rows kept as objects under the job's own operations prefix.

    For a job that can neither land nor write to the run-log dataset. The record is
    durable and readable by name; it is not in `ms_ops.collection_runs`.
    """

    def sink(row: dict[str, Any], part: int) -> None:
        name = f"{prefix}runs/{runtime.run_id}.{row['phase']}.json"
        runtime.operations.create(name, encode_rows([row]))

    return sink


def run_job(
    runtime: Runtime,
    sink: Sink,
    body: Callable[[], Mapping[str, Any]],
    *,
    source: str | None = None,
) -> int:
    """Run a job's body between a 'started' and a 'finished' record. Returns its exit code.

    The body returns a summary. `requests_sent` and `halted_by` in it are recorded in
    their own columns; the rest becomes the record's detail. A refusal is recorded
    and exits with its own code; any other failure is recorded and exits non-zero.
    """
    started_at = runtime.now()
    version = None if source is None else policy_version(runtime.policy_for(source))

    def record(phase: str, part: int, outcome: str | None, summary: Mapping[str, Any]) -> None:
        detail = {k: v for k, v in summary.items() if k not in ("requests_sent", "halted_by")}
        finished: datetime | None = None if phase == "started" else runtime.now()
        sink(
            {
                "run_id": runtime.run_id,
                "job": runtime.job,
                "source": source,
                "phase": phase,
                "started_at": timestamp(started_at),
                "finished_at": None if finished is None else timestamp(finished),
                "outcome": outcome,
                "requests_sent": summary.get("requests_sent"),
                "halted_by": summary.get("halted_by"),
                "policy_version": version,
                "image_digest": runtime.image,
                "detail": detail or None,
            },
            part,
        )

    runtime.log("job_started")
    record("started", 0, None, {})
    try:
        summary = dict(body())
    except Refused as refusal:
        runtime.log("job_refused", severity="WARNING", reason=str(refusal))
        record("finished", 1, "refused", {"reason": str(refusal)})
        return EXIT_REFUSED
    except Exception as error:
        # The class and the message we wrote ourselves; never a traceback with locals.
        runtime.log("job_failed", severity="ERROR", error=type(error).__name__, reason=str(error))
        record("finished", 1, "failed", {"error": type(error).__name__, "reason": str(error)})
        return EXIT_FAILED
    runtime.log("job_finished", **summary)
    record("finished", 1, str(summary.pop("outcome", "succeeded")), summary)
    return EXIT_OK
