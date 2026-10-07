"""The dispatcher: decide what is due this tick, record the intent, start the jobs.

It holds no source secret and reads no source. It does three things, in this order:

    1. record what it intends to be collected (the expected-target ledger), so that a
       slot nobody ends up attempting is still known to have been expected
    2. take the tick's lease, create-only, so one tick is dispatched at most once
    3. start the jobs that are due

A source whose policy does not clear the stage is never planned: no ledger row, no
job. A job is started through the Cloud Run Admin API with the dispatcher's own
identity, which may start the six other jobs and nothing else.
"""

import contextlib
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Protocol

import httpx

from metaspike.domain.source_policy import SourcePurpose
from metaspike.domain.timeseries import Granularity
from metaspike.jobs.runtime import Runtime
from metaspike.jobs.targets import Target, cleared_targets, expected_target_rows, load_targets
from metaspike.pipeline.normalize import latest_due_period
from metaspike.sources.base import SourceMode
from metaspike.warehouse.landing import BatchAlreadyLanded, Table
from metaspike.warehouse.objects import PreconditionFailed

TICK = Granularity.FIFTEEN_MINUTES
# UTC hours of the once-a-day jobs. Each runs on the tick at minute zero of its hour.
CATALOGUE_HOUR = 1
PERIOD_COLLECTION_HOUR = 2
PARITY_HOUR = 4
# The job that collects each source.
COLLECTOR = {
    "steam_current_players": "ms-steam-players",
    "wikimedia_pageviews": "ms-wikimedia",
}
CATALOGUE_SOURCES = ("igdb", "steam_catalogue")


class JobStarter(Protocol):
    def start(self, job: str) -> str:
        """Start one execution of a job. Returns a reference to it."""
        ...


class CloudRunJobs:
    """Starts jobs through the Cloud Run Admin API with the caller's own identity."""

    def __init__(
        self, client: httpx.Client, project: str, region: str, token: Callable[[], str]
    ) -> None:
        self._client = client
        self._base = f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs"
        self._token = token

    def start(self, job: str) -> str:
        response = self._client.post(
            f"{self._base}/{job}:run",
            json={},
            headers={"Authorization": f"Bearer {self._token()}"},
        )
        if response.status_code >= 400:
            raise RuntimeError(f"starting {job}: HTTP {response.status_code}")
        return str(response.json().get("name", ""))


def due_jobs(tick: datetime, sources: set[str], catalogue_cleared: bool) -> list[str]:
    """The jobs to start on this tick, given which sources have cleared targets."""
    on_the_hour = tick.minute == 0
    jobs = []
    for source, job in COLLECTOR.items():
        if source not in sources:
            continue
        sampled = source == "steam_current_players"
        if sampled or (on_the_hour and tick.hour == PERIOD_COLLECTION_HOUR):
            jobs.append(job)
    if on_the_hour and tick.hour == CATALOGUE_HOUR and catalogue_cleared:
        jobs.append("ms-catalogue")
    if on_the_hour:
        jobs.append("ms-warehouse")
    if tick.minute == 15:
        jobs.append("ms-features")
    if on_the_hour and tick.hour == PARITY_HOUR:
        jobs.append("ms-parity")
    return jobs


def dispatch(runtime: Runtime, starter: JobStarter) -> Mapping[str, Any]:
    now = runtime.now()
    tick = TICK.floor(now)
    document = load_targets(runtime.operations, now)
    targets = cleared_targets(document, runtime.policy_for, runtime.stage)
    catalogue_cleared = any(
        runtime.policy_for(source).registration_refusal(
            SourcePurpose.CATALOGUE_METADATA, runtime.stage
        )
        is None
        for source in CATALOGUE_SOURCES
    )
    jobs = due_jobs(tick, {target.source for target in targets}, catalogue_cleared)

    # Only the targets of a collector that is due on this tick are expected in it.
    due = tuple(target for target in targets if COLLECTOR.get(target.source) in jobs)

    def slot_of(target: Target) -> datetime:
        if target.mode is SourceMode.SAMPLE_NOW:
            return target.granularity.floor(now)
        lag = runtime.policy_for(target.source).operations.expected_publication_lag
        return latest_due_period(target.granularity, now, lag)

    name = f"tick-{tick:%Y%m%dT%H%MZ}"
    rows = expected_target_rows(due, slot_of, document.version, name, now)
    if rows:
        # If it is already landed, an earlier run of this tick recorded the same intent.
        with contextlib.suppress(BatchAlreadyLanded):
            runtime.writer.land_internal(Table.EXPECTED_TARGETS, "dispatch", name, now, rows)

    try:
        runtime.operations.create(f"dispatch/ticks/{name}.json", b"{}")
    except PreconditionFailed:
        runtime.log("tick_already_dispatched", tick=tick.isoformat())
        return {"outcome": "already_dispatched", "tick": tick.isoformat()}

    started: dict[str, str] = {}
    failed: dict[str, str] = {}
    for job in jobs:
        try:
            started[job] = starter.start(job)
        except Exception as error:
            failed[job] = str(error)
            runtime.log("job_start_failed", severity="ERROR", target_job=job, reason=str(error))
    if failed:
        raise RuntimeError(f"could not start: {', '.join(sorted(failed))}")
    return {
        "tick": tick.isoformat(),
        "schedule_version": document.version,
        "expected_targets": len(rows),
        "jobs_started": sorted(started),
    }
