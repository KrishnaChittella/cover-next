"""Measure the offline catalogue evaluation on SYNTHETIC records of a given size.

    uv run python -m tests.catalogue_scale 1000 5000 10000 20000

Each size is measured in this one process, so peak memory is cumulative: run a size
by itself for its own peak. The records are invented (see `catalogue_samples`); they
are shaped like IGDB and Steam catalogue records and carry those source names, so they
are evaluated under those sources' real policies at the private-development stage.
Nothing is fetched and nothing is stored.

What this measures is the cost of resolving. It says nothing about how well real data
resolves; the golden set does that.

Each size is evaluated twice. The first run is timed and left alone. The second counts
how many times a record's content hash is actually COMPUTED, by wrapping the property
on the class; it is slower for that, so nothing from it is reported as a time. Counting
at the property means the figure is comparable across implementations, whether or not
one of them keeps hashes it has already computed.
"""

import json
import resource
import sys
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from time import perf_counter

from metaspike.catalogue.evaluation import BroadEvaluation, evaluate_broad_catalogue
from metaspike.domain.source_policy import DeploymentStage, SourcePolicy
from metaspike.domain.source_records import SourceRecord
from metaspike.sources.igdb import IGDB_POLICY
from metaspike.sources.steam_catalogue import STEAM_CATALOGUE_POLICY
from tests.catalogue_samples import stratified_sample

# After every synthetic record's fetch time: an evaluation is never dated before its evidence.
AS_OF = datetime(2026, 10, 6, tzinfo=UTC)
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


@contextmanager
def counting_content_hashes() -> Iterator[list[int]]:
    """Count every real computation of `SourceRecord.content_hash` while active."""
    original = SourceRecord.__dict__["content_hash"]
    count = [0]

    def counted(record: SourceRecord) -> str:
        count[0] += 1
        return str(original.fget(record))

    SourceRecord.content_hash = property(counted)  # type: ignore[method-assign,assignment]
    try:
        yield count
    finally:
        SourceRecord.content_hash = original  # type: ignore[method-assign]


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
    peak = round(_peak_rss_mb())
    summary = evaluation.summary
    with counting_content_hashes() as computed:
        again = evaluate_broad_catalogue(
            records,
            policies=CATALOGUE_POLICIES,
            stage=DeploymentStage.PRIVATE_DEVELOPMENT,
            as_of=AS_OF,
            id_factory=counting_ids(),
        )
    # The counting run is the same evaluation, and must come to the same catalogue.
    assert again.state == evaluation.state and again.scoped == evaluation.scoped
    work = summary.broad_work
    resolving = summary.scoped_work.seconds + work.seconds
    return {
        "records": summary.source_records,
        "entities": summary.canonical_entities,
        "quarantined": summary.quarantined_records,
        "related_products": summary.related_products,
        "resolver_calls": work.resolver_calls,
        "fingerprint_checks": work.fingerprint_checks,
        # Counted at the property, in a second run: every real computation, by anyone.
        "content_hashes_computed": computed[0],
        # What the engine itself asked for, where it reports that (None before it did).
        "content_hashes_requested": getattr(work, "content_hashes_requested", None),
        "entities_examined": work.entities_offered,
        "entities_all_pairs": work.entities_all_pairs,
        "share_of_all_pairs": round(work.share_of_all_pairs or 0.0, 4),
        "full_scans": work.full_scans,
        "most_entities_offered": work.most_entities_offered,
        "passes": work.passes,
        "resolving_seconds": round(resolving, 2),
        "coverage_seconds": round(total - resolving, 2),
        "total_seconds": round(total, 2),
        "peak_rss_mb": peak,
    }


if __name__ == "__main__":
    for argument in sys.argv[1:]:
        print(json.dumps(measure(int(argument))), flush=True)
