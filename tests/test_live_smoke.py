"""Live smoke test against the real Wikimedia API.

Excluded from the default test run. Run it explicitly:

    uv run pytest -m live
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from metaspike.catalogue.seed import load_seed_catalogue
from metaspike.domain.signals import CollectionStatus
from metaspike.domain.source_records import ProductType
from metaspike.pipeline.collection import latest_complete_day, window_ending
from metaspike.pipeline.run import run_pipeline
from metaspike.publishing.validation import validate_published
from metaspike.sources.catalogue_sources import build_igdb, build_steam, register_catalogue_sources
from metaspike.sources.limiter import FileQuotaLedger, LimiterPool
from metaspike.sources.registry import SourceRegistry, default_registry
from metaspike.sources.steam_catalogue import SteamAppKind
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


@pytest.mark.live
def test_live_igdb_identity_lookup(tmp_path: Path) -> None:
    """One authenticated IGDB call. Needs the IGDB credentials in the environment."""
    with build_client() as client:
        pool = LimiterPool(ledger=FileQuotaLedger(tmp_path / "quota"))
        igdb = build_igdb(client, pool, token_cache=Path(".metaspike/tokens/igdb.json"))
        registry = SourceRegistry()
        register_catalogue_sources(registry, igdb=igdb)
        records = igdb.games_by_ids([1020])
    (gta_v,) = records
    assert gta_v.title == "Grand Theft Auto V" and gta_v.product_type is ProductType.MAIN_GAME
    assert gta_v.release_dates and gta_v.developers


@pytest.mark.live
def test_live_steam_catalogue_page(tmp_path: Path) -> None:
    """One authenticated Steam app-list call. Catalogue only: no player counts."""
    with build_client() as client:
        pool = LimiterPool(ledger=FileQuotaLedger(tmp_path / "quota"))
        steam = build_steam(client, pool)
        registry = SourceRegistry()
        register_catalogue_sources(registry, steam=steam)
        page = steam.fetch_page(SteamAppKind.GAMES, max_results=5)
    assert page.apps_returned == 5 and len(page.records) == 5
    assert page.observed_app_fields >= {"appid", "name"}
    assert page.has_more is True and page.next_after_appid is not None
