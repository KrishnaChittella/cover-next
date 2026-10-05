"""Assemble the typed artifact bundle for one run. Pure: no filesystem access."""

import hashlib
import json
from datetime import datetime

from metaspike.domain.games import CanonicalGame, Catalogue
from metaspike.domain.observations import DailySeries
from metaspike.domain.source_policy import SourcePolicy
from metaspike.features.attention import (
    FEATURE_DEFINITIONS,
    HISTORY_DAYS,
    LONG_WINDOW_DAYS,
    MIN_LONG_SAMPLES,
    AttentionFeatures,
    rolling_mean,
)
from metaspike.pipeline.assess import GameAssessment
from metaspike.pipeline.collection import (
    CollectionReport,
    CollectionStatus,
    GameCollection,
    SourceRun,
)
from metaspike.publishing.artifacts import (
    CURRENT_FILE,
    METHODOLOGY_FILE,
    SCHEMA_VERSION,
    SCOPE_NOTE,
    SOURCE_HEALTH_FILE,
    VERSIONS_DIR,
    ArtifactBundle,
    ConfidenceMethodology,
    CoverageStatus,
    CurrentArtifact,
    FeatureMethodology,
    GameArtifact,
    HistoryPoint,
    HistorySeries,
    LeaderboardEntry,
    Manifest,
    ManifestPaths,
    MethodologyArtifact,
    MethodologyRef,
    RelationshipView,
    ScoreMethodology,
    SourceCoverage,
    SourceFailure,
    SourceFreshness,
    SourceHealthArtifact,
    SourceHealthEntry,
    SourceMethodology,
    SourceStatus,
    UnrankedEntry,
    game_file,
)
from metaspike.scoring.v0 import (
    CONFIDENCE_HIGH_ABOVE,
    CONFIDENCE_MEDIUM_FROM,
    CONFIDENCE_TERMS,
    SCORE_TERMS,
    SCORE_VERSION,
    source_ceiling,
)
from metaspike.sources.registry import SourceRegistry

FEATURE_DECIMALS = 4
_PENDING_RUN_ID = "00000000T000000Z-00000000"
# Fields that describe when a run happened rather than what it found. They are left
# out of the content hash so identical inputs always produce an identical hash.
_VOLATILE_FIELDS = {"generated_at", "run_id", "updated_at"}

LIMITATIONS: tuple[str, ...] = (
    "Single source: only Wikimedia pageviews are collected, so nothing is corroborated and "
    "confidence is capped at 60.",
    "Wikipedia readership is a proxy for general public attention. It is not player counts, "
    "viewership, search demand or creator supply.",
    "Daily granularity in UTC. The newest point is the last complete day, typically 1 day old.",
    "English Wikipedia only, so attention from non-English audiences is under-represented.",
    "Views of redirect titles are counted separately by the source and are not included.",
    "A game with no article of its own (for example FiveM) has no coverage and is unranked.",
    "A day the source omits is treated as missing, not as zero views.",
    "No event detection yet: the score reports that attention moved, not what caused it.",
    "Weekly seasonality is not modelled; the 28-day baseline spans four whole weeks to "
    "limit, but not remove, its effect.",
    "Score weights and saturation points are stated priors, not values fitted to outcomes.",
    "The score rewards upward attention only; declines score 0 rather than negative.",
)


def build_bundle(
    catalogue: Catalogue,
    report: CollectionReport,
    assessments: tuple[GameAssessment, ...],
    registry: SourceRegistry,
    generated_at: datetime,
) -> ArtifactBundle:
    policies = {entry.policy.source: entry.policy for entry in registry.entries()}
    by_game = {assessment.canonical_game_id: assessment for assessment in assessments}
    ranks = _ranks(catalogue, by_game)

    drafts = {
        game.slug: _game_artifact(
            game, catalogue, by_game[game.canonical_game_id], report, policies, ranks, generated_at
        )
        for game in catalogue.games
    }
    methodology = _methodology(registry, generated_at)
    content_hash = _content_hash(drafts, methodology)
    run_id = f"{generated_at:%Y%m%dT%H%M%SZ}-{content_hash[:8]}"

    games = {slug: draft.model_copy(update={"run_id": run_id}) for slug, draft in drafts.items()}
    methodology = methodology.model_copy(update={"run_id": run_id})
    current = _current(games, report, generated_at, run_id)
    source_health = _source_health(catalogue, report, generated_at, run_id)
    version_path = f"{VERSIONS_DIR}/{run_id}"
    manifest = Manifest(
        schema_version=SCHEMA_VERSION,
        generated_at=generated_at,
        run_id=run_id,
        score_version=SCORE_VERSION,
        content_hash=content_hash,
        version_path=version_path,
        artifacts=ManifestPaths(
            current=f"{version_path}/{CURRENT_FILE}",
            source_health=f"{version_path}/{SOURCE_HEALTH_FILE}",
            methodology=f"{version_path}/{METHODOLOGY_FILE}",
            games={slug: f"{version_path}/{game_file(slug)}" for slug in sorted(games)},
        ),
        game_count=len(games),
        ranked_count=len(current.entries),
    )
    return ArtifactBundle(
        manifest=manifest,
        current=current,
        games=games,
        source_health=source_health,
        methodology=methodology,
    )


def _ranks(catalogue: Catalogue, by_game: dict[str, GameAssessment]) -> dict[str, int]:
    """Rank scored games by score, then confidence, then slug so ties are deterministic."""
    scored: list[tuple[float, float, str, str]] = []
    for game in catalogue.games:
        assessment = by_game[game.canonical_game_id]
        if assessment.score.value is not None and assessment.confidence is not None:
            scored.append(
                (
                    -assessment.score.value,
                    -assessment.confidence.value,
                    game.slug,
                    game.canonical_game_id,
                )
            )
    return {game_id: rank for rank, (*_, game_id) in enumerate(sorted(scored), start=1)}


def _game_artifact(
    game: CanonicalGame,
    catalogue: Catalogue,
    assessment: GameAssessment,
    report: CollectionReport,
    policies: dict[str, SourcePolicy],
    ranks: dict[str, int],
    generated_at: datetime,
) -> GameArtifact:
    history: tuple[HistorySeries, ...] = ()
    series = assessment.series
    if series is not None and policies[series.source].public_history_permitted:
        history = (_history(series, policies[series.source]),)
    relationships = []
    for relationship in game.relationships:
        parent = catalogue.get(relationship.parent_game_id)
        relationships.append(
            RelationshipView(
                relationship_type=relationship.relationship_type,
                parent_game_id=parent.canonical_game_id,
                parent_slug=parent.slug,
                parent_name=parent.name,
            )
        )
    return GameArtifact(
        schema_version=SCHEMA_VERSION,
        generated_at=generated_at,
        run_id=_PENDING_RUN_ID,
        score_version=SCORE_VERSION,
        canonical_game_id=game.canonical_game_id,
        slug=game.slug,
        name=game.name,
        aliases=game.aliases,
        lifecycle_state=game.lifecycle_state,
        relationships=tuple(relationships),
        rank=ranks.get(game.canonical_game_id),
        score=assessment.score,
        confidence=assessment.confidence,
        features=None if assessment.features is None else _rounded(assessment.features),
        explanation=assessment.explanation,
        history=history,
        source_coverage=_coverage(game, report, policies),
        methodology=MethodologyRef(
            score_version=SCORE_VERSION, path=METHODOLOGY_FILE, scope_note=SCOPE_NOTE
        ),
        updated_at=generated_at,
    )


def _rounded(features: AttentionFeatures) -> AttentionFeatures:
    rounded = {
        name: round(value, FEATURE_DECIMALS) for name, value in features if isinstance(value, float)
    }
    return features.model_copy(update=rounded)


def _history(series: DailySeries, policy: SourcePolicy) -> HistorySeries:
    values = series.values()
    means = rolling_mean(values, LONG_WINDOW_DAYS, MIN_LONG_SAMPLES)
    return HistorySeries(
        source=series.source,
        metric=series.metric,
        granularity="daily",
        attribution=policy.attribution_text,
        points=tuple(
            HistoryPoint(
                day=point.period_start,
                value=point.value,
                rolling_mean_7d=None if mean is None else round(mean, 2),
            )
            for point, mean in zip(series.points, means, strict=True)
        ),
    )


def _coverage(
    game: CanonicalGame, report: CollectionReport, policies: dict[str, SourcePolicy]
) -> tuple[SourceCoverage, ...]:
    coverage = []
    for run in report.runs:
        outcome = _outcome(run, game.canonical_game_id)
        attribution = policies[run.source].attribution_text
        if outcome.series is None:
            status = (
                CoverageStatus.FAILED
                if outcome.status is CollectionStatus.FAILED
                else CoverageStatus.NOT_MAPPED
            )
            coverage.append(SourceCoverage(source=run.source, status=status))
            continue
        observed = [p.period_start for p in outcome.series.points if p.value is not None]
        latest = max(observed, default=None)
        span = (latest - min(observed)).days + 1 if latest else 0
        coverage.append(
            SourceCoverage(
                source=run.source,
                status=CoverageStatus.COVERED,
                latest_observed_day=latest,
                staleness_days=(report.window.end - latest).days if latest else None,
                observed_days=len(observed),
                missing_days=span - len(observed),
                attribution=attribution,
            )
        )
    return tuple(coverage)


def _outcome(run: SourceRun, canonical_game_id: str) -> GameCollection:
    for outcome in run.outcomes:
        if outcome.canonical_game_id == canonical_game_id:
            return outcome
    raise KeyError(f"{run.source} has no outcome for {canonical_game_id}")


def _current(
    games: dict[str, GameArtifact], report: CollectionReport, generated_at: datetime, run_id: str
) -> CurrentArtifact:
    entries: list[LeaderboardEntry] = []
    unranked: list[UnrankedEntry] = []
    for slug, game in sorted(games.items()):
        if (
            game.rank is not None
            and game.score.value is not None
            and game.confidence is not None
            and game.features is not None
            and game.features.as_of_day is not None
        ):
            entries.append(
                LeaderboardEntry(
                    rank=game.rank,
                    canonical_game_id=game.canonical_game_id,
                    slug=slug,
                    name=game.name,
                    score=game.score.value,
                    confidence=game.confidence.value,
                    confidence_band=game.confidence.band,
                    lifecycle_state=game.lifecycle_state,
                    summary=game.explanation.summary,
                    top_reasons=game.explanation.top_reasons,
                    source_coverage=game.source_coverage,
                    as_of_day=game.features.as_of_day,
                    updated_at=generated_at,
                    artifact_path=game_file(slug),
                )
            )
        else:
            assert game.score.unscored_reason is not None
            unranked.append(
                UnrankedEntry(
                    canonical_game_id=game.canonical_game_id,
                    slug=slug,
                    name=game.name,
                    lifecycle_state=game.lifecycle_state,
                    reason=game.score.unscored_reason,
                    summary=game.explanation.summary,
                    source_coverage=game.source_coverage,
                    updated_at=generated_at,
                    artifact_path=game_file(slug),
                )
            )
    return CurrentArtifact(
        schema_version=SCHEMA_VERSION,
        generated_at=generated_at,
        run_id=run_id,
        score_version=SCORE_VERSION,
        scope_note=SCOPE_NOTE,
        expected_latest_day=report.window.end,
        confidence_ceiling=round(100 * source_ceiling(len(report.runs)), 1),
        active_sources=tuple(run.source for run in report.runs),
        entries=tuple(sorted(entries, key=lambda entry: entry.rank)),
        unranked=tuple(unranked),
    )


def _source_health(
    catalogue: Catalogue, report: CollectionReport, generated_at: datetime, run_id: str
) -> SourceHealthArtifact:
    entries = []
    for run in report.runs:
        succeeded = run.with_status(CollectionStatus.COLLECTED)
        failed = run.with_status(CollectionStatus.FAILED)
        observed_days = [
            point.period_start
            for outcome in succeeded
            if outcome.series is not None
            for point in outcome.series.points
            if point.value is not None
        ]
        latest = max(observed_days, default=None)
        if not succeeded:
            status = SourceStatus.FAILED
        elif failed:
            status = SourceStatus.DEGRADED
        else:
            status = SourceStatus.HEALTHY
        entries.append(
            SourceHealthEntry(
                source=run.source,
                status=status,
                last_success=run.finished_at if succeeded else None,
                freshness=SourceFreshness(
                    expected_latest_day=report.window.end,
                    latest_observed_day=latest,
                    staleness_days=(report.window.end - latest).days if latest else None,
                ),
                entities_attempted=len(succeeded) + len(failed),
                entities_succeeded=len(succeeded),
                entities_failed=len(failed),
                entities_not_mapped=len(run.with_status(CollectionStatus.NOT_MAPPED)),
                failures=tuple(
                    SourceFailure(
                        canonical_game_id=outcome.canonical_game_id,
                        slug=catalogue.get(outcome.canonical_game_id).slug,
                        error_code=outcome.error_code,
                    )
                    for outcome in failed
                    if outcome.error_code is not None
                ),
            )
        )
    return SourceHealthArtifact(
        schema_version=SCHEMA_VERSION,
        generated_at=generated_at,
        run_id=run_id,
        score_version=SCORE_VERSION,
        sources=tuple(entries),
    )


def _methodology(registry: SourceRegistry, generated_at: datetime) -> MethodologyArtifact:
    sources = registry.entries()
    return MethodologyArtifact(
        schema_version=SCHEMA_VERSION,
        generated_at=generated_at,
        run_id=_PENDING_RUN_ID,
        score_version=SCORE_VERSION,
        scope_note=SCOPE_NOTE,
        score=ScoreMethodology(version=SCORE_VERSION, range=(0, 100), terms=SCORE_TERMS),
        confidence=ConfidenceMethodology(
            range=(0, 100),
            current_ceiling=round(100 * source_ceiling(len(sources)), 1),
            bands={
                "low": f"< {CONFIDENCE_MEDIUM_FROM:g}",
                "medium": f"{CONFIDENCE_MEDIUM_FROM:g} to {CONFIDENCE_HIGH_ABOVE:g}",
                "high": f"> {CONFIDENCE_HIGH_ABOVE:g} (unreachable with one source)",
            },
            terms=CONFIDENCE_TERMS,
        ),
        features=FeatureMethodology(
            granularity="daily",
            history_days=HISTORY_DAYS,
            principle=(
                "Each game is compared against its own recent normal, never against other "
                "games' raw popularity. All windows are anchored on t, the latest observed "
                "day; missing days are excluded from every window, never counted as zero."
            ),
            definitions=FEATURE_DEFINITIONS,
        ),
        explanations=(
            "Deterministic templates rendered from computed features. A sentence appears "
            "only when its backing feature crosses a fixed threshold. No language model."
        ),
        sources=tuple(
            SourceMethodology(
                source=entry.policy.source,
                metric=entry.adapter.metric,
                granularity="daily",
                description=entry.adapter.description,
                policy=entry.policy,
            )
            for entry in sources
        ),
        limitations=LIMITATIONS,
    )


def _content_hash(games: dict[str, GameArtifact], methodology: MethodologyArtifact) -> str:
    payload = {
        "games": [
            games[slug].model_dump(mode="json", exclude=_VOLATILE_FIELDS) for slug in sorted(games)
        ],
        "methodology": methodology.model_dump(mode="json", exclude=_VOLATILE_FIELDS),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
