"""v0 must not move.

`fixtures/v0_golden.json` was produced by commit 2c054b1, before the foundation was
generalised. The current code must reproduce it exactly, to the documented precision
(score 1 dp, components and features 4 dp), with or without extra signals present.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from metaspike.catalogue.seed import load_seed_catalogue
from metaspike.domain.games import CanonicalGame, Catalogue, SourceIdentifier
from metaspike.domain.metrics import (
    Aggregation,
    MetricDefinition,
    MetricRegistry,
    MetricRole,
    default_metrics,
)
from metaspike.domain.observations import Freshness, Observation, ObservationStatus
from metaspike.domain.signals import SignalSpec, SourceErrorCode
from metaspike.domain.timeseries import Granularity, TimeWindow
from metaspike.pipeline.collection import window_ending
from metaspike.pipeline.run import RunResult, run_pipeline
from metaspike.publishing.validation import validate_published
from metaspike.sources.base import SourceError
from metaspike.sources.registry import SourceRegistry
from metaspike.sources.replay import replay_client
from metaspike.sources.wikimedia import WIKIMEDIA_PAGEVIEWS_POLICY, WikimediaPageviewsAdapter
from tests.conftest import RECORDED_END, RECORDINGS, RUN_TIME, run_fixture_pipeline

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "v0_golden.json").read_text())["games"]
COMPARED = ("rank", "score", "confidence", "features", "explanation")

PLAYERS_SOURCE = "test_players"
CONCURRENT_PLAYERS = MetricDefinition(
    metric_id="concurrent_players",
    display_name="Concurrent players",
    unit="players",
    description="Test-only intraday metric.",
    role=MetricRole.DEMAND,
    aggregation=Aggregation.MEAN,
    granularities=(Granularity.FIFTEEN_MINUTES, Granularity.HOURLY),
)
HOURLY_PLAYERS = SignalSpec(metric="concurrent_players", granularity=Granularity.HOURLY)
PLAYERS_POLICY = WIKIMEDIA_PAGEVIEWS_POLICY.model_copy(update={"source": PLAYERS_SOURCE})


class FakePlayersAdapter:
    """A second, intraday source. It exists only to prove v0 ignores other signals."""

    source = PLAYERS_SOURCE
    signals = (HOURLY_PLAYERS,)
    description = "Test-only hourly player counts."

    def fetch(
        self,
        game: CanonicalGame,
        identifier: SourceIdentifier,
        signal: SignalSpec,
        window: TimeWindow,
        observed_at: datetime,
    ) -> list[Observation]:
        if identifier.value == "broken":
            raise SourceError(SourceErrorCode.TIMEOUT, "test_players: timed out")
        return [
            Observation(
                canonical_game_id=game.canonical_game_id,
                source=PLAYERS_SOURCE,
                metric=signal.metric,
                granularity=signal.granularity,
                period_start=period,
                value=1_000_000.0,
                status=ObservationStatus.OBSERVED,
                observed_at=observed_at,
                freshness=Freshness(period_end=period + signal.granularity.step, lag_hours=0),
            )
            for period in window.period_starts(signal.granularity)
        ]


def published_games(artifacts_dir: Path) -> dict[str, Any]:
    return {
        path.stem: json.loads(path.read_text()) for path in (artifacts_dir / "games").glob("*.json")
    }


def catalogue_with_players() -> Catalogue:
    mapped = {"gta-v": "271590", "minecraft": "mc", "star-citizen": "broken"}
    games = tuple(
        game.model_copy(
            update={
                "source_identifiers": (
                    *game.source_identifiers,
                    SourceIdentifier(
                        source=PLAYERS_SOURCE, namespace="app", value=mapped[game.slug]
                    ),
                )
            }
        )
        if game.slug in mapped
        else game
        for game in load_seed_catalogue().games
    )
    return Catalogue(games=games)


def run_with_second_source(artifacts_dir: Path) -> RunResult:
    metrics = MetricRegistry((*default_metrics().definitions(), CONCURRENT_PLAYERS))
    with replay_client(RECORDINGS) as client:
        registry = SourceRegistry(metrics)
        registry.register(WikimediaPageviewsAdapter(client), WIKIMEDIA_PAGEVIEWS_POLICY)
        registry.register(FakePlayersAdapter(), PLAYERS_POLICY)
        return run_pipeline(
            catalogue_with_players(),
            registry,
            window_ending(RECORDED_END),
            artifacts_dir,
            clock=lambda: RUN_TIME,
        )


def test_golden_file_covers_every_game_and_is_not_trivial() -> None:
    assert set(GOLDEN) == {game.slug for game in load_seed_catalogue().games}
    scored = {slug: g["score"]["value"] for slug, g in GOLDEN.items() if g["rank"] is not None}
    assert scored == {
        "minecraft": 12.2,
        "gta-vi": 2.3,
        "escape-from-tarkov": 0.0,
        "gta-v": 0.0,
        "no-mans-sky": 0.0,
        "star-citizen": 0.0,
    }


@pytest.mark.parametrize("slug", sorted(GOLDEN))
def test_v0_output_is_identical_to_the_pre_refactor_commit(slug: str, tmp_path: Path) -> None:
    run_fixture_pipeline(tmp_path / "artifacts")
    game = published_games(tmp_path / "artifacts")[slug]
    for field in COMPARED:
        assert game[field] == GOLDEN[slug][field], f"{slug}.{field} changed"


def test_history_values_are_unchanged(artifacts_dir: Path) -> None:
    run_fixture_pipeline(artifacts_dir)
    recorded = json.loads((RECORDINGS / "Minecraft.json").read_text())["items"]
    (history,) = published_games(artifacts_dir)["minecraft"]["history"]
    assert [point["value"] for point in history["points"]] == [item["views"] for item in recorded]
    assert (history["metric"], history["granularity"]) == ("pageviews", "daily")


def test_a_second_intraday_source_changes_no_v0_score_or_confidence(artifacts_dir: Path) -> None:
    result = run_with_second_source(artifacts_dir)
    games = published_games(artifacts_dir)

    for slug, expected in GOLDEN.items():
        for field in COMPARED:
            assert games[slug][field] == expected[field], f"{slug}.{field} changed"
    assert result.bundle.current.confidence_ceiling == 60
    assert result.bundle.current.active_sources == (PLAYERS_SOURCE, "wikimedia_pageviews")
    validate_published(artifacts_dir)


def test_a_second_source_is_reported_without_entering_the_score(artifacts_dir: Path) -> None:
    result = run_with_second_source(artifacts_dir)
    games = published_games(artifacts_dir)

    coverage = {c["source"]: c["status"] for c in games["gta-v"]["source_coverage"]}
    assert coverage == {PLAYERS_SOURCE: "covered", "wikimedia_pageviews": "covered"}
    coverage = {c["source"]: c["status"] for c in games["star-citizen"]["source_coverage"]}
    assert coverage == {PLAYERS_SOURCE: "failed", "wikimedia_pageviews": "covered"}
    coverage = {c["source"]: c["status"] for c in games["fivem"]["source_coverage"]}
    assert coverage == {PLAYERS_SOURCE: "not_mapped", "wikimedia_pageviews": "not_mapped"}
    assert [h["metric"] for h in games["gta-v"]["history"]] == ["pageviews"]

    players = next(s for s in result.bundle.source_health.sources if s.source == PLAYERS_SOURCE)
    assert (players.entities_attempted, players.entities_succeeded) == (3, 2)
    assert (players.entities_failed, players.entities_not_mapped) == (1, 12)
    assert [(f.slug, f.error_code) for f in players.failures] == [("star-citizen", "timeout")]
    assert [m.metric_id for m in result.bundle.methodology.metrics] == [
        "concurrent_players",
        "pageviews",
    ]
