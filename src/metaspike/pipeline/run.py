"""The end-to-end vertical slice: collect -> normalise -> assess -> publish."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from metaspike.domain.games import Catalogue
from metaspike.domain.signals import CollectionStatus
from metaspike.domain.timeseries import TimeWindow
from metaspike.pipeline.assess import assess
from metaspike.pipeline.collection import CollectionReport, collect
from metaspike.publishing.artifacts import ArtifactBundle
from metaspike.publishing.build import EventInputs, build_bundle
from metaspike.publishing.publisher import publish
from metaspike.sources.registry import SourceRegistry
from metaspike.storage.base import ObservationStore


class CollectionFailedError(Exception):
    """No source returned data for any game, so there is nothing truthful to publish."""


@dataclass(frozen=True)
class RunResult:
    report: CollectionReport
    bundle: ArtifactBundle
    version_dir: Path


def build_and_publish(
    catalogue: Catalogue,
    report: CollectionReport,
    registry: SourceRegistry,
    artifacts_dir: Path,
    generated_at: datetime,
    events: EventInputs | None = None,
) -> RunResult:
    if not report.any_success:
        raise CollectionFailedError(
            "no source returned data for any game; the previously published artifacts "
            "were left unchanged"
        )
    assessments = assess(catalogue, report)
    bundle = build_bundle(catalogue, report, assessments, registry, generated_at, events)
    version_dir = publish(bundle, artifacts_dir)
    return RunResult(report=report, bundle=bundle, version_dir=version_dir)


def run_pipeline(
    catalogue: Catalogue,
    registry: SourceRegistry,
    window: TimeWindow,
    artifacts_dir: Path,
    clock: Callable[[], datetime],
    store: ObservationStore | None = None,
) -> RunResult:
    report = collect(catalogue, registry, window, observed_at=clock(), clock=clock)
    if store is not None:
        store_report(report, store)
    return build_and_publish(catalogue, report, registry, artifacts_dir, generated_at=clock())


def store_report(report: CollectionReport, store: ObservationStore) -> int:
    """Persist every collected series. Returns how many new observation versions were kept."""
    return sum(
        store.write(outcome.series)
        for run in report.runs
        for outcome in run.with_status(CollectionStatus.COLLECTED)
        if outcome.series is not None
    )
