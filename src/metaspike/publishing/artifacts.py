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
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

SCHEMA_VERSION = "1.3.0"
CONTRACT_STATUS = "internal"
SCOPE_NOTE = (
    "metaspike_score_v0 measures relative attention anomaly and recent momentum from a "
    "single source (Wikimedia pageviews, daily). It is not a complete measure of creator "
    "demand or supply."
)

EVENT_SCOPE_NOTE = (
    "Event Heat is internal and uncalibrated (event_heat_rules_v1). It is reported beside "
    "metaspike_score_v0 and is not part of it. Unavailable means no source has looked at a "
    "game's events recently; it never means zero."
)
RADAR_SCOPE_NOTE = (
    "The radar lists dated events that have not started, with their timing. It ranks "
    "nothing and recommends nothing: an event being scheduled is not, by itself, a content "
    "opportunity."
)

VALIDATION_SCOPE_NOTE = (
    "Historical validation of metaspike_score_v0, an internal attention measure, against "
    "what the same demand signal did afterwards. It is not evidence about the final "
    "creator-opportunity model, which does not exist yet. Read the evidence status before "
    "any number."
)

SemVer = Annotated[str, StringConstraints(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
RunId = Annotated[str, StringConstraints(pattern=r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")]
GameIdView = Annotated[
    str,
    StringConstraints(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    ),
]
SlugView = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]

LifecycleStateView = Literal[
    "PRE_RELEASE", "LAUNCH_WINDOW", "RELEASED", "EVERGREEN", "LIVE_SERVICE", "DORMANT"
]
RelationshipTypeView = Literal[
    "MOD_ECOSYSTEM",
    "ONLINE_COMPONENT",
    "SEQUEL",
    "REMAKE",
    "REMASTER",
    "STANDALONE_EXPANSION",
    "PACKAGING_VARIANT",
    "COLLECTION",
]
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


EventPhaseView = Literal["upcoming", "active", "decaying", "expired", "called_off"]
EventAvailabilityView = Literal["available", "unavailable"]
EventUnavailableReasonView = Literal["no_event_coverage", "event_coverage_stale"]
EventStatusView = Literal["scheduled", "cancelled", "withdrawn"]
TimePrecisionView = Literal["day", "exact"]


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


class EventContributionView(ContractModel):
    """One event at the evaluation moment, and its part in the game's Event Heat."""

    event_id: str
    event_type: EventTypeView
    title: str
    starts_at: AwareDatetime
    # The end used for evaluation. When `end_assumed`, the source gave none and this is
    # the start plus the type's default duration.
    ends_at: AwareDatetime
    end_assumed: bool
    time_precision: TimePrecisionView
    status: EventStatusView
    phase: EventPhaseView
    in_ramp: bool
    strength: float = Field(ge=0, le=1)
    importance: float = Field(gt=0, le=1)
    contribution: float = Field(ge=0, le=1)
    counted: bool
    rank: int | None = Field(default=None, ge=1)
    not_counted_because: str | None = None
    sources: tuple[str, ...]
    text: str | None = None


class EventIntelligenceView(ContractModel):
    """A game's event intelligence: available with a heat, or explicitly unavailable.

    `event_heat` is null exactly when `availability` is "unavailable". A heat of 0 is a
    finding (a source looked and nothing is relevant); null is the absence of one.
    """

    availability: EventAvailabilityView
    rules_version: str
    rules_status: str
    as_of: AwareDatetime
    event_heat: float | None = Field(default=None, ge=0, le=100)
    top_event_id: str | None = None
    summary: str
    reasons: tuple[ReasonView, ...]
    unavailable_reasons: tuple[EventUnavailableReasonView, ...]
    coverage_checked_at: AwareDatetime | None = None
    coverage_sources: tuple[str, ...]
    contributions: tuple[EventContributionView, ...]

    @model_validator(mode="after")
    def _availability_is_consistent(self) -> Self:
        available = self.availability == "available"
        if available != (self.event_heat is not None):
            raise ValueError("event_heat is a number exactly when availability is available")
        if available == bool(self.unavailable_reasons):
            raise ValueError("unavailable_reasons are given exactly when unavailable")
        if not available and (self.contributions or self.top_event_id is not None):
            raise ValueError("unavailable event intelligence carries no events")
        return self


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
    event_intelligence: EventIntelligenceView
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
    budget_group: str
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


class StageClearanceView(ContractModel):
    stage: str
    cleared: bool
    reason: str


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
    commercial_approval_required: str
    commercial_approval_status: str
    stage_clearance: tuple[StageClearanceView, ...]
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


class EventTypeRuleView(ContractModel):
    event_type: EventTypeView
    label: str
    importance: float
    ramp_days: float
    pre_event_peak: float
    default_duration_days: float
    half_life_days: float
    active_floor: float
    decay_window_days: float
    rationale: str


class ParameterView(ContractModel):
    name: str
    value: str
    rationale: str


class EventHeatMethodology(ContractModel):
    version: str
    status: str
    scope_note: str
    range: tuple[float, float]
    terms: tuple[TermView, ...]
    aggregation: str
    availability: tuple[TermView, ...]
    parameters: tuple[ParameterView, ...]
    types: tuple[EventTypeRuleView, ...]
    radar_windows_hours: tuple[int, ...]
    limitations: tuple[str, ...]


class MethodologyArtifact(ArtifactHeader):
    scope_note: str
    score: ScoreMethodology
    confidence: ConfidenceMethodology
    event_heat: EventHeatMethodology
    features: FeatureMethodology
    explanations: str
    metrics: tuple[MetricView, ...]
    sources: tuple[SourceMethodology, ...]
    limitations: tuple[str, ...]


class RadarItemView(ContractModel):
    canonical_game_id: GameIdView
    slug: SlugView
    name: str
    event_id: str
    event_type: EventTypeView
    title: str
    starts_at: AwareDatetime
    time_precision: TimePrecisionView
    hours_until_start: float = Field(ge=0)
    window_hours: int
    in_ramp: bool
    strength: float = Field(ge=0, le=1)
    contribution: float = Field(ge=0, le=1)
    game_event_heat: float = Field(ge=0, le=100)
    sources: tuple[str, ...]
    text: str
    artifact_path: str


class RadarArtifact(ArtifactHeader):
    """Dated events that have not started, for games with event coverage.

    "unavailable" means no game has event coverage, so nothing is known. It is not an
    empty radar: an available radar with no items is.
    """

    scope_note: str
    availability: EventAvailabilityView
    rules_version: str
    rules_status: str
    as_of: AwareDatetime
    windows_hours: tuple[int, ...]
    games_with_coverage: int = Field(ge=0)
    games_without_coverage: int = Field(ge=0)
    unavailable_reasons: tuple[EventUnavailableReasonView, ...]
    summary: str
    items: tuple[RadarItemView, ...]

    @model_validator(mode="after")
    def _availability_is_consistent(self) -> Self:
        available = self.availability == "available"
        if available == bool(self.unavailable_reasons):
            raise ValueError("unavailable_reasons are given exactly when unavailable")
        if not available and self.items:
            raise ValueError("an unavailable radar carries no items")
        return self


EvidenceStatusView = Literal["framework_only", "insufficient_sample", "exploratory", "validated"]
ValidationAvailabilityView = Literal["available", "not_run"]
OutcomeAvailabilityView = Literal["available", "unavailable"]


class OutcomeSummaryView(ContractModel):
    """A set of primary outcomes. With a count of 0 every statistic is null, never 0."""

    count: int = Field(ge=0)
    mean: float | None = None
    median: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    hit_rate: float | None = Field(default=None, ge=0, le=1)
    downside_rate: float | None = Field(default=None, ge=0, le=1)


class TopKView(ContractModel):
    k: int
    days_with_full_group: int
    outcomes: OutcomeSummaryView


class AssociationView(ContractModel):
    statistic: str
    days_used: int
    days_skipped: int
    mean_rho: float | None = None
    median_rho: float | None = None
    positive_days: int
    negative_days: int


class StrategyResultView(ContractModel):
    strategy: str
    label: str
    description: str
    top_k: tuple[TopKView, ...]
    association: AssociationView


class StratumView(ContractModel):
    label: str
    outcomes: OutcomeSummaryView


class LeadTimeView(ContractModel):
    found: int
    none_in_window: int
    unavailable: int
    median_days: float | None = None
    mean_days: float | None = None


class SpreadView(ContractModel):
    count: int = Field(ge=0)
    mean: float | None = None
    median: float | None = None


class RequirementView(ContractModel):
    name: str
    required: int
    actual: int
    met: bool


class StatusCheckView(ContractModel):
    status: EvidenceStatusView
    reached: bool
    requirements: tuple[RequirementView, ...]
    rationale: str


class EvidenceView(ContractModel):
    status: EvidenceStatusView
    checks: tuple[StatusCheckView, ...]
    holdout_supported: bool
    holdout_rationale: str
    conclusion: str


class LeakageCheckView(ContractModel):
    name: str
    description: str
    checked: int = Field(ge=0)
    passed: bool


class EventBacktestView(ContractModel):
    real_event_history: bool
    snapshots_with_event_intelligence: int = Field(ge=0)
    note: str


class InspectionEntryView(ContractModel):
    rank: int
    slug: SlugView
    name: str
    score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=100)
    outcome_availability: OutcomeAvailabilityView
    unavailable_reason: str | None = None
    realized_uplift_72h: float | None = None
    movement_24h: float | None = None
    persistence_7d: float | None = Field(default=None, ge=0, le=1)
    lead_time_days: int | None = None


class InspectionUnrankedView(ContractModel):
    slug: SlugView
    name: str
    reason: str


class InspectionDayView(ContractModel):
    # The UTC day the recommendation was simulated on, and the exact moment.
    day: date
    as_of: AwareDatetime
    # The newest demand day that could be seen at that moment.
    latest_due_day: date
    primary_window_start: date
    primary_window_end: date
    ranked: tuple[InspectionEntryView, ...]
    unranked: tuple[InspectionUnrankedView, ...]


class ValidationReportView(ContractModel):
    report_id: str
    format: str
    protocol_version: str
    outcome_rules_version: str
    score_version: str
    event_rules_version: str
    first_prediction_day: date
    last_prediction_day: date
    first_latest_due_day: date
    last_latest_due_day: date
    publication_lag_hours: dict[str, float]
    data_through: date
    # Exactly which records the report summarises: SHA-256 over their sorted ids.
    snapshot_set_hash: str
    label_set_hash: str
    snapshot_dates: int
    universe_size: int
    games_ranked: int
    games_with_outcome: int
    independent_sources: int
    recommendations: int
    ranked: int
    unranked: int
    primary_evaluable: int
    primary_unavailable: dict[str, int]
    overall: OutcomeSummaryView
    strategies: tuple[StrategyResultView, ...]
    score_bins: tuple[StratumView, ...]
    confidence_bands: tuple[StratumView, ...]
    movement_24h: SpreadView
    persistence_7d: SpreadView
    lead_time: LeadTimeView
    evidence: EvidenceView
    leakage_checks: tuple[LeakageCheckView, ...]
    events: EventBacktestView
    limitations: tuple[str, ...]
    inspection: tuple[InspectionDayView, ...]
    inspection_truncated: bool
    created_at: AwareDatetime


class OutcomeDefinitionView(ContractModel):
    """`outcome_rules_v1` as published: what each outcome is and every constant in it."""

    version: str
    terms: tuple[TermView, ...]
    parameters: tuple[ParameterView, ...]
    unavailable: tuple[TermView, ...]


class ValidationArtifact(ArtifactHeader):
    """Historical validation, or the plain statement that none has been run.

    "not_run" carries no report and no number. It is not a result of zero.
    """

    scope_note: str
    availability: ValidationAvailabilityView
    protocol_version: str
    outcome_definition: OutcomeDefinitionView
    report: ValidationReportView | None = None

    @model_validator(mode="after")
    def _availability_is_consistent(self) -> Self:
        if (self.availability == "available") != (self.report is not None):
            raise ValueError("a report is present exactly when validation is available")
        return self


class ManifestPaths(ContractModel):
    current: str
    source_health: str
    methodology: str
    radar: str
    validation: str
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
RADAR_FILE = "radar.json"
VALIDATION_FILE = "validation.json"
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
    radar: RadarArtifact
    validation: ValidationArtifact

    def version_files(self) -> dict[str, ContractModel]:
        """Path relative to the version directory -> artifact."""
        files: dict[str, ContractModel] = {
            CURRENT_FILE: self.current,
            SOURCE_HEALTH_FILE: self.source_health,
            METHODOLOGY_FILE: self.methodology,
            RADAR_FILE: self.radar,
            VALIDATION_FILE: self.validation,
        }
        for slug, game in sorted(self.games.items()):
            files[game_file(slug)] = game
        return files
