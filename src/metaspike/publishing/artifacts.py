"""The public artifact contract consumed by the future MetaSpike web application.

Every artifact carries `schema_version`. Additive, backwards-compatible changes bump
the minor version; anything a consumer could break on bumps the major version.
Artifacts contain derived values and attributed daily counts only: never raw
third-party payloads, secrets or exception traces.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Annotated

from pydantic import AwareDatetime, Field, StringConstraints

from metaspike.domain.events import GameEvent
from metaspike.domain.games import GameId, LifecycleState, RelationshipType, Slug
from metaspike.domain.model import FrozenModel
from metaspike.domain.observations import Metric
from metaspike.domain.scores import (
    ConfidenceBand,
    ConfidenceResult,
    Explanation,
    ScoreResult,
    UnscoredReason,
)
from metaspike.domain.source_policy import SourcePolicy
from metaspike.features.attention import AttentionFeatures, FeatureDefinition
from metaspike.scoring.v0 import TermDefinition
from metaspike.sources.base import SourceErrorCode

SCHEMA_VERSION = "1.0.0"
SCOPE_NOTE = (
    "metaspike_score_v0 measures relative attention anomaly and recent momentum from a "
    "single source (Wikimedia pageviews, daily). It is not a complete measure of creator "
    "demand or supply."
)

SemVer = Annotated[str, StringConstraints(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
RunId = Annotated[str, StringConstraints(pattern=r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")]


class ArtifactHeader(FrozenModel):
    schema_version: SemVer
    generated_at: AwareDatetime
    run_id: RunId
    score_version: str


class CoverageStatus(StrEnum):
    COVERED = "covered"
    FAILED = "failed"
    NOT_MAPPED = "not_mapped"


class SourceCoverage(FrozenModel):
    source: str
    status: CoverageStatus
    latest_observed_day: date | None = None
    staleness_days: int | None = None
    observed_days: int = 0
    missing_days: int = 0
    attribution: str | None = None


class LeaderboardEntry(FrozenModel):
    rank: int = Field(ge=1)
    canonical_game_id: GameId
    slug: Slug
    name: str
    score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=100)
    confidence_band: ConfidenceBand
    lifecycle_state: LifecycleState
    summary: str
    top_reasons: tuple[str, ...]
    source_coverage: tuple[SourceCoverage, ...]
    as_of_day: date
    updated_at: AwareDatetime
    artifact_path: str


class UnrankedEntry(FrozenModel):
    canonical_game_id: GameId
    slug: Slug
    name: str
    lifecycle_state: LifecycleState
    reason: UnscoredReason
    summary: str
    source_coverage: tuple[SourceCoverage, ...]
    updated_at: AwareDatetime
    artifact_path: str


class CurrentArtifact(ArtifactHeader):
    scope_note: str
    expected_latest_day: date
    confidence_ceiling: float = Field(ge=0, le=100)
    active_sources: tuple[str, ...]
    entries: tuple[LeaderboardEntry, ...]
    unranked: tuple[UnrankedEntry, ...]


class RelationshipView(FrozenModel):
    relationship_type: RelationshipType
    parent_game_id: GameId
    parent_slug: Slug
    parent_name: str


class HistoryPoint(FrozenModel):
    day: date
    value: float | None = Field(default=None, ge=0)
    rolling_mean_7d: float | None = Field(default=None, ge=0)


class HistorySeries(FrozenModel):
    source: str
    metric: Metric
    granularity: str
    attribution: str | None = None
    points: tuple[HistoryPoint, ...]


class MethodologyRef(FrozenModel):
    score_version: str
    path: str
    scope_note: str


class GameArtifact(ArtifactHeader):
    canonical_game_id: GameId
    slug: Slug
    name: str
    aliases: tuple[str, ...]
    lifecycle_state: LifecycleState
    relationships: tuple[RelationshipView, ...]
    rank: int | None = Field(default=None, ge=1)
    score: ScoreResult
    confidence: ConfidenceResult | None = None
    features: AttentionFeatures | None = None
    explanation: Explanation
    history: tuple[HistorySeries, ...]
    source_coverage: tuple[SourceCoverage, ...]
    events: tuple[GameEvent, ...] = ()
    methodology: MethodologyRef
    updated_at: AwareDatetime


class SourceStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"


class SourceFreshness(FrozenModel):
    expected_latest_day: date
    latest_observed_day: date | None = None
    staleness_days: int | None = None


class SourceFailure(FrozenModel):
    canonical_game_id: GameId
    slug: Slug
    error_code: SourceErrorCode


class SourceHealthEntry(FrozenModel):
    source: str
    status: SourceStatus
    last_success: AwareDatetime | None = None
    freshness: SourceFreshness
    entities_attempted: int = Field(ge=0)
    entities_succeeded: int = Field(ge=0)
    entities_failed: int = Field(ge=0)
    entities_not_mapped: int = Field(ge=0)
    failures: tuple[SourceFailure, ...]


class SourceHealthArtifact(ArtifactHeader):
    sources: tuple[SourceHealthEntry, ...]


class ScoreMethodology(FrozenModel):
    version: str
    range: tuple[float, float]
    terms: tuple[TermDefinition, ...]


class ConfidenceMethodology(FrozenModel):
    range: tuple[float, float]
    current_ceiling: float
    bands: dict[str, str]
    terms: tuple[TermDefinition, ...]


class FeatureMethodology(FrozenModel):
    granularity: str
    history_days: int
    principle: str
    definitions: tuple[FeatureDefinition, ...]


class SourceMethodology(FrozenModel):
    source: str
    metric: Metric
    granularity: str
    description: str
    policy: SourcePolicy


class MethodologyArtifact(ArtifactHeader):
    scope_note: str
    score: ScoreMethodology
    confidence: ConfidenceMethodology
    features: FeatureMethodology
    explanations: str
    sources: tuple[SourceMethodology, ...]
    limitations: tuple[str, ...]


class ManifestPaths(FrozenModel):
    current: str
    source_health: str
    methodology: str
    games: dict[str, str]


class Manifest(ArtifactHeader):
    content_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    version_path: str
    artifacts: ManifestPaths
    game_count: int = Field(ge=0)
    ranked_count: int = Field(ge=0)


CURRENT_FILE = "current.json"
SOURCE_HEALTH_FILE = "source-health.json"
METHODOLOGY_FILE = "methodology.json"
MANIFEST_FILE = "manifest.json"
GAMES_DIR = "games"
VERSIONS_DIR = "versions"


def game_file(slug: str) -> str:
    return f"{GAMES_DIR}/{slug}.json"


@dataclass(frozen=True)
class ArtifactBundle:
    """Everything one run publishes, already validated as typed models."""

    manifest: Manifest
    current: CurrentArtifact
    games: dict[str, GameArtifact]
    source_health: SourceHealthArtifact
    methodology: MethodologyArtifact

    def version_files(self) -> dict[str, FrozenModel]:
        """Path relative to the version directory -> artifact."""
        files: dict[str, FrozenModel] = {
            CURRENT_FILE: self.current,
            SOURCE_HEALTH_FILE: self.source_health,
            METHODOLOGY_FILE: self.methodology,
        }
        for slug, game in sorted(self.games.items()):
            files[game_file(slug)] = game
        return files
