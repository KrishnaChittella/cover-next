"""The dispatcher: one scheduler tick, bounded.

It holds no source secret and reads no source. What it does on a tick, and in what
order, is in `metaspike.scheduling.tick`: read the control record and stop unless it
permits work; decide what is due from the schedule, reading only what can be due;
bound it by the source's limits; record every due target and what was done about it
in the expected-target ledger; write a work order per collector; then start the jobs.

A source whose policy does not clear the stage is never planned: no ledger row, no
work order, no job. A job is started through the Cloud Run Admin API with the
dispatcher's own identity, which may start the six other jobs and nothing else.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

import httpx

from metaspike.domain.source_policy import SourcePurpose
from metaspike.jobs.runtime import Runtime
from metaspike.jobs.targets import load_targets
from metaspike.scheduling.config import PRODUCTION, DispatchConfig
from metaspike.scheduling.tick import JobStarter, TickEnvironment, run_tick
from metaspike.warehouse.landing import BatchAlreadyLanded, Table, policy_version

__all__ = ["CloudRunJobs", "JobStarter", "dispatch"]


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


def dispatch(
    runtime: Runtime, starter: JobStarter, config: DispatchConfig = PRODUCTION
) -> Mapping[str, Any]:
    def land(name: str, at: datetime, rows: Sequence[Mapping[str, Any]]) -> None:
        # If it is already landed, an earlier run of this tick recorded the same intent.
        try:
            runtime.writer.land_internal(Table.EXPECTED_TARGETS, "dispatch", name, at, rows)
        except BatchAlreadyLanded:
            return

    def cleared(source: str) -> bool:
        policy = runtime.policy_for(source)
        return policy.registration_refusal(SourcePurpose.DEMAND_SIGNALS, runtime.stage) is None

    return run_tick(
        TickEnvironment(
            store=runtime.operations,
            land=land,
            starter=starter,
            now=runtime.now,
            config=config,
            cleared=cleared,
            policy_version=lambda source: policy_version(runtime.policy_for(source)),
            publication_lag=lambda source: (
                runtime.policy_for(source).operations.expected_publication_lag
            ),
            load_targets=lambda: load_targets(runtime.operations, runtime.now()),
            log=runtime.log,
        )
    )
