"""The artifact contract: view models that define exactly what is published.

STATUS: INTERNAL / NON-BINDING. Schema 1.x is an engineering contract used to build
and test the pipeline. It is not the public MetaSpike contract, nothing outside this
repository may depend on it, and it may change without a major version bump. The
public contract is defined once, for the single production release.

This module deliberately imports nothing from the domain. Every published shape is
declared here, so a change to an internal model can never silently change what is
published; `publishing/mapping.py` is the only place the two meet.

Artifacts contain derived values and attributed counts only: never raw third-party
payloads, secrets or exception traces.
"""

from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

SCHEMA_VERSION = "1.0.0"
CONTRACT_STATUS = "internal"
SCOPE_NOTE = (
    "metaspike_score_v0 measures relative attention anomaly and recent momentum from a "
    "single source (Wikimedia pageviews, daily). It is not a complete measure of creator "
    "demand or supply."
)

SemVer = Annotated[str, StringConstraints(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
RunId = Annotated[str, StringConstraints(pattern=r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")]
GameIdView = Annotated[str, StringConstraints(pattern=r"^msg_[0-9]{4}$")]
SlugView = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]

LifecycleStateView = Literal[
    "PRE_RELEASE", "LAUNCH_WINDOW", "RELEASED", "EVERGREEN", "LIVE_SERVICE", "DORMANT"
]
RelationshipTypeView = Literal["MOD_ECOSYSTEM", "ONLINE_COMPONENT", "SEQUEL"]
ScoreStatusView = Literal["scored", "unscored"]
UnscoredReasonView = Literal[
    "no_observations",
    "insufficient_history",
    "insufficient_recent_data",
    "zero_baseline",
    "zero_dispersion",
]
ConfidenceBandView = Literal["low", "medium", "high"]
CoverageStatusView = Literal["covered", "failed", "not_mapped"]
SourceStatusView = Literal["healthy", "degraded", "failed"]
EventTypeView = Literal[
    "release",
    "major_patch",
    "minor_patch",
    "season",
    "wipe",
    "expansion",
    "dlc",
    "expedition",
    "beta",
    "playtest",
    "free_weekend",
    "trailer",
    "showcase",
    "platform_release",
    "major_in_game_event",
]


class ContractModel(BaseModel):
    """Immutable, closed model that rejects NaN and Infinity on every float field."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class ArtifactHeader(ContractModel):
    schema_version: SemVer
    contract_status: Literal["internal"] = Field(
        description="INTERNAL / non-binding engineering contract; not the public contract."
    )
    generated_at: AwareDatetime
    run_id: RunId
    score_version: str


class SourceCoverage(ContractModel):
    source: str
    status: CoverageStatusView
    latest_observed_day: date | None = None
    staleness_days: int | None = None
    observed_days: int = 0
    missing_days: int = 0
    attribution: str | None = None


class LeaderboardEntry(ContractModel):
    rank: int = Field(ge=1)
    canonical_game_id: GameIdView
    slug: SlugView
    name: str
    score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=100)
    confidence_band: ConfidenceBandView
    lifecycle_state: LifecycleStateView
    summary: str
    top_reasons: tuple[str, ...]
    source_coverage: tuple[SourceCoverage, ...]
    as_of_day: date
    updated_at: AwareDatetime
    artifact_path: str


class UnrankedEntry(ContractModel):
    canonical_game_id: GameIdView
    slug: SlugView
    name: str
    lifecycle_state: LifecycleStateView
    reason: UnscoredReasonView
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


class RelationshipView(ContractModel):
    relationship_type: RelationshipTypeView
    parent_game_id: GameIdView
    parent_slug: SlugView
    parent_name: str


class ScoreComponentsView(ContractModel):
    significance: float = Field(ge=0, le=1)
    magnitude: float = Field(ge=0, le=1)
    anomaly: float = Field(ge=0, le=1)
    momentum: float = Field(ge=0, le=1)


class ScoreView(ContractModel):
    score_version: str
    status: ScoreStatusView
    value: float | None = Field(default=None, ge=0, le=100)
    components: ScoreComponentsView | None = None
    unscored_reason: UnscoredReasonView | None = None


class ConfidenceFactorsView(ContractModel):
    source_ceiling: float = Field(ge=0, le=1)
    freshness: float = Field(ge=0, le=1)
    history: float = Field(ge=0, le=1)
    completeness: float = Field(ge=0, le=1)
    samples: float = Field(ge=0, le=1)


class ConfidenceView(ContractModel):
    value: float = Field(ge=0, le=100)
    band: ConfidenceBandView
    ceiling: float = Field(ge=0, le=100)
    independent_sources: int = Field(ge=0)
    factors: ConfidenceFactorsView


class FeaturesView(ContractModel):
    as_of_day: date | None = None
    window_days: int
    observed_days: int
    history_span_days: int = 0
    missing_days: int = 0
    staleness_days: int | None = None
    latest_views: float | None = None
    avg_3d: float | None = None
    avg_7d: float | None = None
    prior_avg_3d: float | None = None
    recent_7d_samples: int = 0
    baseline_samples: int = 0
    baseline_mean_28d: float | None = None
    baseline_median_28d: float | None = None
    baseline_std_28d: float | None = None
    baseline_mad_28d: float | None = None
    lift_ratio: float | None = None
    lift_pct: float | None = None
    lift_7d_pct: float | None = None
    velocity_ratio: float | None = None
    velocity_pct: float | None = None
    consecutive_rising_days: int = 0
    z_score: float | None = None
    robust_z_score: float | None = None
    robust_scale_method: Literal["mad", "mean_ad"] | None = None
    history_percentile: float | None = None
    percentile_samples: int = 0
    flags: tuple[str, ...] = ()


class ReasonView(ContractModel):
    code: str
    text: str


class ExplanationView(ContractModel):
    summary: str
    reasons: tuple[ReasonView, ...]
    top_reasons: tuple[str, ...]


class HistoryPoint(ContractModel):
    day: date
    value: float | None = Field(default=None, ge=0)
    rolling_mean_7d: float | None = Field(default=None, ge=0)


class HistorySeries(ContractModel):
    source: str
    metric: str
    granularity: str
    attribution: str | None = None
    points: tuple[HistoryPoint, ...]


class EventView(ContractModel):
    canonical_game_id: GameIdView
    event_type: EventTypeView
    title: str
    starts_on: date
    ends_on: date | None = None
    source: str
    reference: str | None = None


class MethodologyRef(ContractModel):
    score_version: str
    path: str
    scope_note: str


class GameArtifact(ArtifactHeader):
    canonical_game_id: GameIdView
    slug: SlugView
    name: str
    aliases: tuple[str, ...]
    lifecycle_state: LifecycleStateView
    relationships: tuple[RelationshipView, ...]
    rank: int | None = Field(default=None, ge=1)
    score: ScoreView
    confidence: ConfidenceView | None = None
    features: FeaturesView | None = None
    explanation: ExplanationView
    history: tuple[HistorySeries, ...]
    source_coverage: tuple[SourceCoverage, ...]
    events: tuple[EventView, ...] = ()
    methodology: MethodologyRef
    updated_at: AwareDatetime


class SourceFreshness(ContractModel):
    # The last complete day in the requested window.
    expected_latest_day: date
    latest_observed_day: date | None = None
    # Days by which the latest observation trails the day the source was due to have
    # published, given its expected publication lag (see methodology). Never negative.
    staleness_days: int | None = None


class SourceFailure(ContractModel):
    canonical_game_id: GameIdView
    slug: SlugView
    error_code: str


class SourceHealthEntry(ContractModel):
    source: str
    status: SourceStatusView
    last_success: AwareDatetime | None = None
    freshness: SourceFreshness
    entities_attempted: int = Field(ge=0)
    entities_succeeded: int = Field(ge=0)
    entities_failed: int = Field(ge=0)
    entities_not_mapped: int = Field(ge=0)
    failures: tuple[SourceFailure, ...]


class SourceHealthArtifact(ArtifactHeader):
    sources: tuple[SourceHealthEntry, ...]


class TermView(ContractModel):
    name: str
    formula: str
    rationale: str


class FeatureDefinitionView(ContractModel):
    name: str
    unit: str
    definition: str
    valid_when: str


class MetricView(ContractModel):
    metric_id: str
    display_name: str
    unit: str
    description: str
    role: str
    aggregation: str
    granularities: tuple[str, ...]


class RateLimitView(ContractModel):
    max_requests: int
    per_seconds: int
    max_concurrency: int
    basis: str
    note: str


class QuotaView(ContractModel):
    limit: int
    unit: str
    period: str
    basis: str
    note: str


class SourceOperationsView(ContractModel):
    rate_limit: RateLimitView
    quota: QuotaView | None = None
    cache_max_age_seconds: int | None = None
    expected_publication_lag_seconds: int
    publication_lag_basis: str


class DisplayRulesView(ContractModel):
    link_back_required: str
    branding_required: str
    note: str


class FieldRuleView(ContractModel):
    field: str
    storage_allowed: str
    display_allowed: str
    note: str


class SourcePolicyView(ContractModel):
    source: str
    raw_storage_allowed: str
    retention_days: int | None = None
    historical_storage_allowed: str
    derived_metrics_allowed: str
    commercial_use_allowed: str
    redistribution_allowed: str
    attribution_required: str
    attribution_text: str | None = None
    approval_required: str
    approval_status: str
    operations: SourceOperationsView
    display: DisplayRulesView
    field_rules: tuple[FieldRuleView, ...]
    reviewed_at: date
    references: tuple[str, ...]
    notes: str


class ScoreMethodology(ContractModel):
    version: str
    range: tuple[float, float]
    terms: tuple[TermView, ...]


class ConfidenceMethodology(ContractModel):
    range: tuple[float, float]
    current_ceiling: float
    bands: dict[str, str]
    terms: tuple[TermView, ...]


class FeatureMethodology(ContractModel):
    granularity: str
    history_days: int
    principle: str
    definitions: tuple[FeatureDefinitionView, ...]


class SignalView(ContractModel):
    metric: str
    granularity: str


class SourceMethodology(ContractModel):
    source: str
    signals: tuple[SignalView, ...]
    description: str
    policy: SourcePolicyView


class MethodologyArtifact(ArtifactHeader):
    scope_note: str
    score: ScoreMethodology
    confidence: ConfidenceMethodology
    features: FeatureMethodology
    explanations: str
    metrics: tuple[MetricView, ...]
    sources: tuple[SourceMethodology, ...]
    limitations: tuple[str, ...]


class ManifestPaths(ContractModel):
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
    """Everything one run publishes, already validated as contract models."""

    manifest: Manifest
    current: CurrentArtifact
    games: dict[str, GameArtifact]
    source_health: SourceHealthArtifact
    methodology: MethodologyArtifact

    def version_files(self) -> dict[str, ContractModel]:
        """Path relative to the version directory -> artifact."""
        files: dict[str, ContractModel] = {
            CURRENT_FILE: self.current,
            SOURCE_HEALTH_FILE: self.source_health,
            METHODOLOGY_FILE: self.methodology,
        }
        for slug, game in sorted(self.games.items()):
            files[game_file(slug)] = game
        return files
