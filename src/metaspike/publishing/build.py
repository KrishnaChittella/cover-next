"""Assemble the artifact bundle for one run. Pure: no filesystem access.

Everything published is built from contract views (`publishing/artifacts.py`);
internal domain models reach an artifact only through `publishing/mapping.py`.
"""

import hashlib
import json
from datetime import date, datetime
from typing import Any

from metaspike.domain.games import CanonicalGame, Catalogue
from metaspike.domain.observations import TimeSeries
from metaspike.domain.signals import CollectionStatus, Signal
from metaspike.domain.source_policy import SourcePolicy
from metaspike.domain.timeseries import Granularity
from metaspike.features.attention import (
    FEATURE_DEFINITIONS,
    HISTORY_DAYS,
    LONG_WINDOW_DAYS,
    MIN_LONG_SAMPLES,
    rolling_mean,
)
from metaspike.pipeline.assess import GameAssessment
from metaspike.pipeline.collection import CollectionReport, SourceRun
from metaspike.pipeline.normalize import latest_due_period
from metaspike.publishing.artifacts import (
    CONTRACT_STATUS,
    CURRENT_FILE,
    METHODOLOGY_FILE,
    SCHEMA_VERSION,
    SCOPE_NOTE,
    SOURCE_HEALTH_FILE,
    VERSIONS_DIR,
    ArtifactBundle,
    ConfidenceMethodology,
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
    SignalView,
    SourceCoverage,
    SourceFailure,
    SourceFreshness,
    SourceHealthArtifact,
    SourceHealthEntry,
    SourceMethodology,
    SourceStatusView,
    UnrankedEntry,
    game_file,
)
from metaspike.publishing.mapping import (
    confidence_view,
    explanation_view,
    feature_definition_view,
    features_view,
    metric_view,
    policy_view,
    score_view,
    term_view,
)
from metaspike.scoring.v0 import (
    CONFIDENCE_HIGH_ABOVE,
    CONFIDENCE_MEDIUM_FROM,
    CONFIDENCE_TERMS,
    SCORE_TERMS,
    SCORE_VERSION,
    V0_INDEPENDENT_SOURCES,
    source_ceiling,
)
from metaspike.sources.registry import SourceRegistry

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
    by_game = {assessment.canonical_game_id: assessment for assessment in assessments}
    ranks = _ranks(catalogue, by_game)

    drafts = {
        game.slug: _game_artifact(
            game, catalogue, by_game[game.canonical_game_id], report, registry, ranks, generated_at
        )
        for game in catalogue.games
    }
    methodology = _methodology(registry, generated_at)
    content_hash = _content_hash(drafts, methodology)
    run_id = f"{generated_at:%Y%m%dT%H%M%SZ}-{content_hash[:8]}"

    games = {slug: draft.model_copy(update={"run_id": run_id}) for slug, draft in drafts.items()}
    methodology = methodology.model_copy(update={"run_id": run_id})
    current = _current(games, report, generated_at, run_id)
    source_health = _source_health(catalogue, report, registry, generated_at, run_id)
    version_path = f"{VERSIONS_DIR}/{run_id}"
    manifest = Manifest(
        **_header(generated_at, run_id),
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


def _header(generated_at: datetime, run_id: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "contract_status": CONTRACT_STATUS,
        "generated_at": generated_at,
        "run_id": run_id,
        "score_version": SCORE_VERSION,
    }


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
    registry: SourceRegistry,
    ranks: dict[str, int],
    generated_at: datetime,
) -> GameArtifact:
    history: tuple[HistorySeries, ...] = ()
    series = assessment.scored_series
    if series is not None:
        policy = registry.policy(series.source)
        if policy.field_display_permitted(series.metric):
            history = (_history(series, policy),)
    relationships = []
    for relationship in game.relationships:
        parent = catalogue.get(relationship.parent_game_id)
        relationships.append(
            RelationshipView(
                relationship_type=relationship.relationship_type.value,
                parent_game_id=parent.canonical_game_id,
                parent_slug=parent.slug,
                parent_name=parent.name,
            )
        )
    features = assessment.scored_features
    confidence = assessment.confidence
    return GameArtifact(
        **_header(generated_at, _PENDING_RUN_ID),
        canonical_game_id=game.canonical_game_id,
        slug=game.slug,
        name=game.name,
        aliases=game.aliases,
        lifecycle_state=game.lifecycle_state.value,
        relationships=tuple(relationships),
        rank=ranks.get(game.canonical_game_id),
        score=score_view(assessment.score),
        confidence=None if confidence is None else confidence_view(confidence),
        features=None if features is None else features_view(features),
        explanation=explanation_view(assessment.explanation),
        history=history,
        source_coverage=_coverage(game, report, registry),
        methodology=MethodologyRef(
            score_version=SCORE_VERSION, path=METHODOLOGY_FILE, scope_note=SCOPE_NOTE
        ),
        updated_at=generated_at,
    )


def _history(series: TimeSeries, policy: SourcePolicy) -> HistorySeries:
    values = series.values()
    means = rolling_mean(values, LONG_WINDOW_DAYS, MIN_LONG_SAMPLES)
    return HistorySeries(
        source=series.source,
        metric=series.metric,
        granularity=series.granularity.value,
        attribution=policy.attribution_text,
        points=tuple(
            HistoryPoint(
                day=point.period_day,
                value=point.value,
                rolling_mean_7d=None if mean is None else round(mean, 2),
            )
            for point, mean in zip(series.points, means, strict=True)
        ),
    )


def _observed_days(signals: list[Signal]) -> list[date]:
    """Distinct UTC days on which any of the signals has an observation, in order."""
    return sorted(
        {
            point.period_day
            for signal in signals
            if signal.series is not None
            for point in signal.series.points
            if point.value is not None
        }
    )


def _coverage(
    game: CanonicalGame, report: CollectionReport, registry: SourceRegistry
) -> tuple[SourceCoverage, ...]:
    coverage = []
    for run in report.runs:
        outcomes = [o for o in run.outcomes if o.canonical_game_id == game.canonical_game_id]
        collected = [outcome for outcome in outcomes if outcome.available]
        if not collected:
            failed = any(outcome.status is CollectionStatus.FAILED for outcome in outcomes)
            coverage.append(
                SourceCoverage(source=run.source, status="failed" if failed else "not_mapped")
            )
            continue
        observed = _observed_days(collected)
        latest = observed[-1] if observed else None
        span = (observed[-1] - observed[0]).days + 1 if observed else 0
        due = _due_day(run.source, report, registry)
        coverage.append(
            SourceCoverage(
                source=run.source,
                status="covered",
                latest_observed_day=latest,
                staleness_days=max(0, (due - latest).days) if latest else None,
                observed_days=len(observed),
                missing_days=span - len(observed),
                attribution=registry.policy(run.source).attribution_text,
            )
        )
    return tuple(coverage)


def _due_day(source: str, report: CollectionReport, registry: SourceRegistry) -> date:
    """The latest day the source was due to have published when the run collected."""
    lag = registry.policy(source).operations.expected_publication_lag
    due = latest_due_period(Granularity.DAILY, report.observed_at, lag)
    return min(due, report.window.end).date()


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
        **_header(generated_at, run_id),
        scope_note=SCOPE_NOTE,
        expected_latest_day=report.window.end.date(),
        confidence_ceiling=round(100 * source_ceiling(V0_INDEPENDENT_SOURCES), 1),
        active_sources=tuple(run.source for run in report.runs),
        entries=tuple(sorted(entries, key=lambda entry: entry.rank)),
        unranked=tuple(unranked),
    )


def _source_health(
    catalogue: Catalogue,
    report: CollectionReport,
    registry: SourceRegistry,
    generated_at: datetime,
    run_id: str,
) -> SourceHealthArtifact:
    return SourceHealthArtifact(
        **_header(generated_at, run_id),
        sources=tuple(
            _source_health_entry(run, catalogue, report, registry) for run in report.runs
        ),
    )


def _source_health_entry(
    run: SourceRun, catalogue: Catalogue, report: CollectionReport, registry: SourceRegistry
) -> SourceHealthEntry:
    by_status: dict[CollectionStatus, list[str]] = {status: [] for status in CollectionStatus}
    for game_id, entity_status in run.entity_status().items():
        by_status[entity_status].append(game_id)
    succeeded = by_status[CollectionStatus.COLLECTED]
    failed = by_status[CollectionStatus.FAILED]

    status: SourceStatusView = "healthy"
    if not succeeded:
        status = "failed"
    elif failed:
        status = "degraded"

    observed = _observed_days(list(run.with_status(CollectionStatus.COLLECTED)))
    latest = observed[-1] if observed else None
    due = _due_day(run.source, report, registry)
    first_error = {
        outcome.canonical_game_id: outcome.error_code
        for outcome in reversed(run.with_status(CollectionStatus.FAILED))
        if outcome.error_code is not None
    }
    return SourceHealthEntry(
        source=run.source,
        status=status,
        last_success=run.finished_at if succeeded else None,
        freshness=SourceFreshness(
            expected_latest_day=report.window.end.date(),
            latest_observed_day=latest,
            staleness_days=max(0, (due - latest).days) if latest else None,
        ),
        entities_attempted=len(succeeded) + len(failed),
        entities_succeeded=len(succeeded),
        entities_failed=len(failed),
        entities_not_mapped=len(by_status[CollectionStatus.NOT_MAPPED]),
        failures=tuple(
            SourceFailure(
                canonical_game_id=game_id,
                slug=catalogue.get(game_id).slug,
                error_code=first_error[game_id].value,
            )
            for game_id in failed
            if game_id in first_error
        ),
    )


def _methodology(registry: SourceRegistry, generated_at: datetime) -> MethodologyArtifact:
    return MethodologyArtifact(
        **_header(generated_at, _PENDING_RUN_ID),
        scope_note=SCOPE_NOTE,
        score=ScoreMethodology(
            version=SCORE_VERSION,
            range=(0, 100),
            terms=tuple(term_view(term) for term in SCORE_TERMS),
        ),
        confidence=ConfidenceMethodology(
            range=(0, 100),
            current_ceiling=round(100 * source_ceiling(V0_INDEPENDENT_SOURCES), 1),
            bands={
                "low": f"< {CONFIDENCE_MEDIUM_FROM:g}",
                "medium": f"{CONFIDENCE_MEDIUM_FROM:g} to {CONFIDENCE_HIGH_ABOVE:g}",
                "high": f"> {CONFIDENCE_HIGH_ABOVE:g} (unreachable with one source)",
            },
            terms=tuple(term_view(term) for term in CONFIDENCE_TERMS),
        ),
        features=FeatureMethodology(
            granularity="daily",
            history_days=HISTORY_DAYS,
            principle=(
                "Each game is compared against its own recent normal, never against other "
                "games' raw popularity. All windows are anchored on t, the latest observed "
                "day; missing days are excluded from every window, never counted as zero."
            ),
            definitions=tuple(feature_definition_view(d) for d in FEATURE_DEFINITIONS),
        ),
        explanations=(
            "Deterministic templates rendered from computed features. A sentence appears "
            "only when its backing feature crosses a fixed threshold. No language model."
        ),
        metrics=tuple(metric_view(metric) for metric in registry.metrics.definitions()),
        sources=tuple(
            SourceMethodology(
                source=entry.policy.source,
                signals=tuple(
                    SignalView(metric=signal.metric, granularity=signal.granularity.value)
                    for signal in entry.adapter.signals
                ),
                description=entry.adapter.description,
                policy=policy_view(entry.policy),
            )
            for entry in registry.entries()
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
