"""The end-to-end vertical slice: collect -> normalise -> assess -> publish."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from metaspike.domain.games import Catalogue
from metaspike.pipeline.assess import assess
from metaspike.pipeline.collection import CollectionReport, collect
from metaspike.publishing.artifacts import ArtifactBundle
from metaspike.publishing.build import build_bundle
from metaspike.publishing.publisher import publish
from metaspike.sources.base import FetchWindow
from metaspike.sources.registry import SourceRegistry


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
) -> RunResult:
    if not report.any_success:
        raise CollectionFailedError(
            "no source returned data for any game; the previously published artifacts "
            "were left unchanged"
        )
    assessments = assess(catalogue, report)
    bundle = build_bundle(catalogue, report, assessments, registry, generated_at)
    version_dir = publish(bundle, artifacts_dir)
    return RunResult(report=report, bundle=bundle, version_dir=version_dir)


def run_pipeline(
    catalogue: Catalogue,
    registry: SourceRegistry,
    window: FetchWindow,
    artifacts_dir: Path,
    clock: Callable[[], datetime],
) -> RunResult:
    report = collect(catalogue, registry, window, observed_at=clock(), clock=clock)
    return build_and_publish(catalogue, report, registry, artifacts_dir, generated_at=clock())
