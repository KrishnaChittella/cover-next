"""Offline warehouse fixtures: the real table schemas, in-memory buckets and tables.

The schemas are read from the Terraform that declares the real tables, so a row that
loads here is a row the real table accepts by shape.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from metaspike.domain.source_policy import DeploymentStage, SourcePolicy, StageClearance
from metaspike.sources.igdb import IGDB_POLICY
from metaspike.sources.steam_catalogue import STEAM_CATALOGUE_POLICY
from metaspike.sources.steam_players import STEAM_CURRENT_PLAYERS_POLICY
from metaspike.sources.wikimedia import WIKIMEDIA_PAGEVIEWS_POLICY
from metaspike.warehouse.landing import TABLES, LandingWriter
from metaspike.warehouse.loader import Loader
from metaspike.warehouse.objects import MemoryObjectStore
from metaspike.warehouse.tables import MemoryWarehouse

if TYPE_CHECKING:
    from metaspike.jobs.runtime import Runtime

SCHEMAS = Path(__file__).parent.parent / "infra" / "prod" / "schemas"

REAL_POLICIES = {
    policy.source: policy
    for policy in (
        IGDB_POLICY,
        STEAM_CATALOGUE_POLICY,
        STEAM_CURRENT_PLAYERS_POLICY,
        WIKIMEDIA_PAGEVIEWS_POLICY,
    )
}


def table_schemas() -> dict[str, list[dict[str, Any]]]:
    return {
        spec.qualified: json.loads((SCHEMAS / f"{table.value}.json").read_text(encoding="utf-8"))
        for table, spec in TABLES.items()
    }


def real_policy(source: str) -> SourcePolicy:
    return REAL_POLICIES[source]


def cleared(policy: SourcePolicy) -> SourcePolicy:
    """A TEST-ONLY copy of a policy with private collection cleared.

    It exists so the mechanics of a store can be exercised for a source whose real
    policy refuses the stage. The real policies are never changed, and other tests
    assert that the real ones still refuse.
    """
    return policy.model_copy(
        update={
            "stage_clearance": tuple(
                StageClearance(stage=c.stage, cleared=True, reason="test-only clearance")
                if c.stage is DeploymentStage.PRIVATE_COLLECTION
                else c
                for c in policy.stage_clearance
            )
        }
    )


def cleared_policy(source: str) -> SourcePolicy:
    return cleared(REAL_POLICIES[source])


@dataclass
class World:
    """Everything a warehouse test needs, wired the way production is."""

    landing: MemoryObjectStore
    operations: MemoryObjectStore
    warehouse: MemoryWarehouse
    now: Callable[[], datetime]
    policy_for: Callable[[str], SourcePolicy]

    @property
    def writer(self) -> LandingWriter:
        return LandingWriter(self.landing, self.policy_for)

    def loader(self, run_id: str = "load-1") -> Loader:
        return Loader(
            self.landing,
            # The loader's real grant on the operations bucket is prefix-conditional:
            # it can read and write its own prefix by name and cannot list.
            self.operations.view(
                writable_prefixes=("warehouse/",),
                readable_prefixes=("warehouse/",),
                can_list=False,
            ),
            self.warehouse,
            now=self.now,
            run_id=run_id,
        )

    def load_everything(self, run_id: str = "load-1") -> int:
        """Load every complete landed batch. Returns the number of rows appended."""
        loader, rows = self.loader(run_id), 0
        seen = set()
        for name in self.landing.list(""):
            source, table, day = name.split("/")[:3]
            if (source, table, day) not in seen:
                seen.add((source, table, day))
                report = loader.load_day(source, TABLES_BY_NAME[table], day.removeprefix("dt="))
                rows += report.rows_loaded
        return rows


TABLES_BY_NAME = {table.value: table for table in TABLES}


def world(
    now: Callable[[], datetime], policy_for: Callable[[str], SourcePolicy] = cleared_policy
) -> World:
    return World(
        landing=MemoryObjectStore(),
        operations=MemoryObjectStore(),
        warehouse=MemoryWarehouse(table_schemas()),
        now=now,
        policy_for=policy_for,
    )


def runtime_for(
    w: World,
    job: str,
    *,
    policy_for: Callable[[str], SourcePolicy] | None = None,
    environ: dict[str, str] | None = None,
    state: MemoryObjectStore | None = None,
    run_id: str = "run-0001",
) -> "tuple[Runtime, list[dict[str, Any]]]":
    """A job runtime over the in-memory world, and the structured log it writes."""
    from metaspike.domain.source_policy import DeploymentStage
    from metaspike.jobs.runtime import Runtime

    log: list[dict[str, Any]] = []
    runtime = Runtime(
        job=job,
        run_id=run_id,
        stage=DeploymentStage.PRIVATE_COLLECTION,
        image="registry.invalid/metaspike@sha256:" + "0" * 64,
        buckets={
            "landing": w.landing,
            "ops": w.operations,
            "state": state if state is not None else MemoryObjectStore(),
            "raw": MemoryObjectStore(),
            "tokens": MemoryObjectStore(),
        },
        warehouse=w.warehouse,
        now=w.now,
        policy_for=policy_for or w.policy_for,
        environ=environ or {},
        emit=lambda entry: log.append(dict(entry)),
        project="project",
        region="us-central1",
    )
    return runtime, log
