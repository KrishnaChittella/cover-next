/* eslint-disable */
// GENERATED from <repo>/schemas by `npm run contract:generate`. Do not edit.
// The Python artifact contract (src/metaspike/publishing/artifacts.py) is the source.

export interface GameArtifact {
  aliases: string[];
  canonical_game_id: string;
  confidence?: ConfidenceView | null;
  /**
   * INTERNAL / non-binding engineering contract; not the public contract.
   */
  contract_status: "internal";
  event_intelligence: EventIntelligenceView;
  events?: EventView[];
  explanation: ExplanationView;
  features?: FeaturesView | null;
  generated_at: string;
  history: HistorySeries[];
  lifecycle_state:
    "PRE_RELEASE" | "LAUNCH_WINDOW" | "RELEASED" | "EVERGREEN" | "LIVE_SERVICE" | "DORMANT";
  methodology: MethodologyRef;
  name: string;
  rank?: number | null;
  relationships: RelationshipView[];
  run_id: string;
  schema_version: string;
  score: ScoreView;
  score_version: string;
  slug: string;
  source_coverage: SourceCoverage[];
  updated_at: string;
}
export interface ConfidenceView {
  band: "low" | "medium" | "high";
  ceiling: number;
  factors: ConfidenceFactorsView;
  independent_sources: number;
  value: number;
}
export interface ConfidenceFactorsView {
  completeness: number;
  freshness: number;
  history: number;
  samples: number;
  source_ceiling: number;
}
/**
 * A game's event intelligence: available with a heat, or explicitly unavailable.
 *
 * `event_heat` is null exactly when `availability` is "unavailable". A heat of 0 is a
 * finding (a source looked and nothing is relevant); null is the absence of one.
 */
export interface EventIntelligenceView {
  as_of: string;
  availability: "available" | "unavailable";
  contributions: EventContributionView[];
  coverage_checked_at?: string | null;
  coverage_sources: string[];
  event_heat?: number | null;
  reasons: ReasonView[];
  rules_status: string;
  rules_version: string;
  summary: string;
  top_event_id?: string | null;
  unavailable_reasons: ("no_event_coverage" | "event_coverage_stale")[];
}
/**
 * One event at the evaluation moment, and its part in the game's Event Heat.
 */
export interface EventContributionView {
  contribution: number;
  counted: boolean;
  end_assumed: boolean;
  ends_at: string;
  event_id: string;
  event_type:
    | "release"
    | "major_patch"
    | "minor_patch"
    | "season"
    | "wipe"
    | "expansion"
    | "dlc"
    | "expedition"
    | "beta"
    | "playtest"
    | "free_weekend"
    | "trailer"
    | "showcase"
    | "platform_release"
    | "major_in_game_event";
  importance: number;
  in_ramp: boolean;
  not_counted_because?: string | null;
  phase: "upcoming" | "active" | "decaying" | "expired" | "called_off";
  rank?: number | null;
  sources: string[];
  starts_at: string;
  status: "scheduled" | "cancelled" | "withdrawn";
  strength: number;
  text?: string | null;
  time_precision: "day" | "exact";
  title: string;
}
export interface ReasonView {
  code: string;
  text: string;
}
export interface EventView {
  canonical_game_id: string;
  ends_on?: string | null;
  event_type:
    | "release"
    | "major_patch"
    | "minor_patch"
    | "season"
    | "wipe"
    | "expansion"
    | "dlc"
    | "expedition"
    | "beta"
    | "playtest"
    | "free_weekend"
    | "trailer"
    | "showcase"
    | "platform_release"
    | "major_in_game_event";
  reference?: string | null;
  source: string;
  starts_on: string;
  title: string;
}
export interface ExplanationView {
  reasons: ReasonView[];
  summary: string;
  top_reasons: string[];
}
export interface FeaturesView {
  as_of_day?: string | null;
  avg_3d?: number | null;
  avg_7d?: number | null;
  baseline_mad_28d?: number | null;
  baseline_mean_28d?: number | null;
  baseline_median_28d?: number | null;
  baseline_samples?: number;
  baseline_std_28d?: number | null;
  consecutive_rising_days?: number;
  flags?: string[];
  history_percentile?: number | null;
  history_span_days?: number;
  latest_views?: number | null;
  lift_7d_pct?: number | null;
  lift_pct?: number | null;
  lift_ratio?: number | null;
  missing_days?: number;
  observed_days: number;
  percentile_samples?: number;
  prior_avg_3d?: number | null;
  recent_7d_samples?: number;
  robust_scale_method?: ("mad" | "mean_ad") | null;
  robust_z_score?: number | null;
  staleness_days?: number | null;
  velocity_pct?: number | null;
  velocity_ratio?: number | null;
  window_days: number;
  z_score?: number | null;
}
export interface HistorySeries {
  attribution?: string | null;
  granularity: string;
  metric: string;
  points: HistoryPoint[];
  source: string;
}
export interface HistoryPoint {
  day: string;
  rolling_mean_7d?: number | null;
  value?: number | null;
}
export interface MethodologyRef {
  path: string;
  scope_note: string;
  score_version: string;
}
export interface RelationshipView {
  parent_game_id: string;
  parent_name: string;
  parent_slug: string;
  relationship_type:
    | "MOD_ECOSYSTEM"
    | "ONLINE_COMPONENT"
    | "SEQUEL"
    | "REMAKE"
    | "REMASTER"
    | "STANDALONE_EXPANSION"
    | "PACKAGING_VARIANT"
    | "COLLECTION";
}
export interface ScoreView {
  components?: ScoreComponentsView | null;
  score_version: string;
  status: "scored" | "unscored";
  unscored_reason?:
    | (
        | "no_observations"
        | "insufficient_history"
        | "insufficient_recent_data"
        | "zero_baseline"
        | "zero_dispersion"
      )
    | null;
  value?: number | null;
}
export interface ScoreComponentsView {
  anomaly: number;
  magnitude: number;
  momentum: number;
  significance: number;
}
export interface SourceCoverage {
  attribution?: string | null;
  latest_observed_day?: string | null;
  missing_days?: number;
  observed_days?: number;
  source: string;
  staleness_days?: number | null;
  status: "covered" | "failed" | "not_mapped";
}
