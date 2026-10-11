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
export interface ReasonView {
  code: string;
  text: string;
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
