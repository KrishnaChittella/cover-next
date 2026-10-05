"""Live smoke test against the real Wikimedia API.

Excluded from the default test run. Run it explicitly:

    uv run pytest -m live
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from metaspike.catalogue.seed import load_seed_catalogue
from metaspike.pipeline.collection import CollectionStatus, latest_complete_day, window_ending
from metaspike.pipeline.run import run_pipeline
from metaspike.publishing.validation import validate_published
from metaspike.sources.registry import default_registry
from metaspike.sources.wikimedia import build_client


@pytest.mark.live
def test_live_wikimedia_pipeline(tmp_path: Path) -> None:
    def now() -> datetime:
        return datetime.now(UTC)

    with build_client() as client:
        result = run_pipeline(
            load_seed_catalogue(),
            default_registry(client),
            window_ending(latest_complete_day(now())),
            tmp_path / "artifacts",
            clock=now,
        )
    (run,) = result.report.runs
    assert len(run.with_status(CollectionStatus.COLLECTED)) >= 10
    manifest = validate_published(tmp_path / "artifacts")
    assert manifest.ranked_count >= 10
