"""Measure the offline catalogue evaluation on SYNTHETIC records of a given size.

    uv run python -m tests.catalogue_scale 1000 5000 10000 20000

Each size is measured in this one process, so peak memory is cumulative: run a size
by itself for its own peak. The records are invented (see `catalogue_samples`); they
are shaped like IGDB and Steam catalogue records and carry those source names, so they
are evaluated under those sources' real policies at the private-development stage.
Nothing is fetched and nothing is stored.

What this measures is the cost of resolving. It says nothing about how well real data
resolves; the golden set does that.
"""

import json
import resource
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from time import perf_counter

from metaspike.catalogue.evaluation import BroadEvaluation, evaluate_broad_catalogue
from metaspike.domain.source_policy import DeploymentStage, SourcePolicy
from metaspike.domain.source_records import SourceRecord
from metaspike.sources.igdb import IGDB_POLICY
from metaspike.sources.steam_catalogue import STEAM_CATALOGUE_POLICY
from tests.catalogue_samples import stratified_sample

AS_OF = datetime(2026, 10, 5, tzinfo=UTC)
CATALOGUE_POLICIES: dict[str, SourcePolicy] = {
    IGDB_POLICY.source: IGDB_POLICY,
    STEAM_CATALOGUE_POLICY.source: STEAM_CATALOGUE_POLICY,
}


def counting_ids(start: int = 10_000) -> Callable[[], uuid.UUID]:
    issued = [0]

    def factory() -> uuid.UUID:
        issued[0] += 1
        return uuid.UUID(int=start + issued[0], version=4)

    return factory


def synthetic_records(size: int) -> list[SourceRecord]:
    return list(stratified_sample(size).records)


def evaluate_synthetic(size: int) -> BroadEvaluation:
    return evaluate_broad_catalogue(
        synthetic_records(size),
        policies=CATALOGUE_POLICIES,
        stage=DeploymentStage.PRIVATE_DEVELOPMENT,
        as_of=AS_OF,
        id_factory=counting_ids(),
    )


def _peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Bytes on macOS, kilobytes on Linux.
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def measure(size: int) -> dict[str, object]:
    records = synthetic_records(size)
    started = perf_counter()
    evaluation = evaluate_broad_catalogue(
        records,
        policies=CATALOGUE_POLICIES,
        stage=DeploymentStage.PRIVATE_DEVELOPMENT,
        as_of=AS_OF,
        id_factory=counting_ids(),
    )
    total = perf_counter() - started
    summary = evaluation.summary
    work = summary.broad_work
    resolving = summary.scoped_work.seconds + work.seconds
    return {
        "records": summary.source_records,
        "entities": summary.canonical_entities,
        "quarantined": summary.quarantined_records,
        "related_products": summary.related_products,
        "resolver_calls": work.resolver_calls,
        "fingerprint_checks": work.fingerprint_checks,
        "entities_examined": work.entities_offered,
        "entities_all_pairs": work.entities_all_pairs,
        "share_of_all_pairs": round(work.share_of_all_pairs or 0.0, 4),
        "full_scans": work.full_scans,
        "most_entities_offered": work.most_entities_offered,
        "passes": work.passes,
        "resolving_seconds": round(resolving, 2),
        "coverage_seconds": round(total - resolving, 2),
        "total_seconds": round(total, 2),
        "peak_rss_mb": round(_peak_rss_mb()),
    }


if __name__ == "__main__":
    for argument in sys.argv[1:]:
        print(json.dumps(measure(int(argument))), flush=True)
