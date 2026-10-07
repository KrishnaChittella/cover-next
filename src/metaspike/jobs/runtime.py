"""What a job is given: its configuration, its storage, its policies, its log.

Everything comes from the environment Cloud Run sets on the job. Nothing is read
from a file, no key exists, and access tokens come from the metadata server for the
job's own service account. Tests build a `Runtime` directly from in-memory stores.

Logs are one JSON object per line on standard output, which Cloud Logging reads as
structured entries. The alert counters match on the `event` field.
"""

import json
import os
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from metaspike.domain.source_policy import DeploymentStage, SourcePolicy
from metaspike.sources.igdb import IGDB_POLICY
from metaspike.sources.steam_catalogue import STEAM_CATALOGUE_POLICY
from metaspike.sources.steam_players import STEAM_CURRENT_PLAYERS_POLICY
from metaspike.sources.wikimedia import WIKIMEDIA_PAGEVIEWS_POLICY
from metaspike.warehouse.auth import MetadataToken
from metaspike.warehouse.landing import LandingWriter
from metaspike.warehouse.objects import GcsObjectStore, ObjectStore
from metaspike.warehouse.tables import BigQueryWarehouse, Warehouse

STAGE_ENV = "METASPIKE_STAGE"
PROJECT_ENV = "METASPIKE_PROJECT"
REGION_ENV = "METASPIKE_REGION"
IMAGE_ENV = "METASPIKE_IMAGE"
BUCKETS = ("landing", "raw", "state", "ops", "tokens")

# The reviewed policies, and only those. A job has no way to substitute another.
POLICIES: dict[str, SourcePolicy] = {
    policy.source: policy
    for policy in (
        IGDB_POLICY,
        STEAM_CATALOGUE_POLICY,
        STEAM_CURRENT_PLAYERS_POLICY,
        WIKIMEDIA_PAGEVIEWS_POLICY,
    )
}


class ConfigurationError(Exception):
    """The job was started without something it needs."""


class Refused(Exception):
    """The job must not run: a policy or stage condition is not met."""


def production_policy(source: str) -> SourcePolicy:
    return POLICIES[source]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _emit(entry: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
    sys.stdout.flush()


@dataclass
class Runtime:
    job: str
    run_id: str
    stage: DeploymentStage
    image: str
    buckets: Mapping[str, ObjectStore]
    warehouse: Warehouse
    now: Callable[[], datetime] = _utc_now
    policy_for: Callable[[str], SourcePolicy] = production_policy
    environ: Mapping[str, str] = field(default_factory=dict)
    emit: Callable[[Mapping[str, Any]], None] = _emit
    project: str = ""
    region: str = ""

    @property
    def landing(self) -> ObjectStore:
        return self.buckets["landing"]

    @property
    def operations(self) -> ObjectStore:
        return self.buckets["ops"]

    @property
    def state(self) -> ObjectStore:
        return self.buckets["state"]

    @property
    def writer(self) -> LandingWriter:
        return LandingWriter(self.landing, self.policy_for, self.stage)

    def log(self, event: str, severity: str = "INFO", **fields: Any) -> None:
        self.emit(
            {"severity": severity, "event": event, "job": self.job, "run_id": self.run_id, **fields}
        )

    def refuse_unless_cleared(self, source: str, purpose: Any) -> None:
        refusal = self.policy_for(source).registration_refusal(purpose, self.stage)
        if refusal is not None:
            raise Refused(f"{source}: {refusal}")


def runtime_from_environment(
    job: str, environ: Mapping[str, str] | None = None, client: httpx.Client | None = None
) -> Runtime:
    env = os.environ if environ is None else environ
    missing = [
        name for name in (STAGE_ENV, PROJECT_ENV, REGION_ENV, IMAGE_ENV) if not env.get(name)
    ]
    if missing:
        raise ConfigurationError(f"not set: {', '.join(missing)}")
    try:
        stage = DeploymentStage(env[STAGE_ENV])
    except ValueError:
        raise ConfigurationError(f"{STAGE_ENV} is not a deployment stage") from None
    project, region = env[PROJECT_ENV], env[REGION_ENV]
    http = client or httpx.Client(timeout=60.0)
    token = MetadataToken(http)
    now = _utc_now()
    execution = env.get("CLOUD_RUN_EXECUTION") or uuid.uuid4().hex[:12]
    return Runtime(
        job=job,
        run_id=f"{now:%Y%m%dT%H%M%SZ}-{execution}"[:120],
        stage=stage,
        image=env[IMAGE_ENV],
        buckets={name: GcsObjectStore(http, f"{project}-{name}", token) for name in BUCKETS},
        warehouse=BigQueryWarehouse(http, project, region, token, sleep=_sleep),
        environ=env,
        project=project,
        region=region,
    )


def _sleep(seconds: float) -> None:
    time.sleep(seconds)
