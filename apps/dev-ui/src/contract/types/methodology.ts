/* eslint-disable */
// GENERATED from <repo>/schemas by `npm run contract:generate`. Do not edit.
// The Python artifact contract (src/metaspike/publishing/artifacts.py) is the source.

export interface MethodologyArtifact {
  confidence: ConfidenceMethodology;
  /**
   * INTERNAL / non-binding engineering contract; not the public contract.
   */
  contract_status: "internal";
  event_heat: EventHeatMethodology;
  explanations: string;
  features: FeatureMethodology;
  generated_at: string;
  limitations: string[];
  metrics: MetricView[];
  run_id: string;
  schema_version: string;
  scope_note: string;
  score: ScoreMethodology;
  score_version: string;
  sources: SourceMethodology[];
}
export interface ConfidenceMethodology {
  bands: {
    [k: string]: string;
  };
  current_ceiling: number;
  /**
   * @minItems 2
   * @maxItems 2
   */
  range: [number, number];
  terms: TermView[];
}
export interface TermView {
  formula: string;
  name: string;
  rationale: string;
}
export interface EventHeatMethodology {
  aggregation: string;
  availability: TermView[];
  limitations: string[];
  parameters: ParameterView[];
  radar_windows_hours: number[];
  /**
   * @minItems 2
   * @maxItems 2
   */
  range: [number, number];
  scope_note: string;
  status: string;
  terms: TermView[];
  types: EventTypeRuleView[];
  version: string;
}
export interface ParameterView {
  name: string;
  rationale: string;
  value: string;
}
export interface EventTypeRuleView {
  active_floor: number;
  decay_window_days: number;
  default_duration_days: number;
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
  half_life_days: number;
  importance: number;
  label: string;
  pre_event_peak: number;
  ramp_days: number;
  rationale: string;
}
export interface FeatureMethodology {
  definitions: FeatureDefinitionView[];
  granularity: string;
  history_days: number;
  principle: string;
}
export interface FeatureDefinitionView {
  definition: string;
  name: string;
  unit: string;
  valid_when: string;
}
export interface MetricView {
  aggregation: string;
  description: string;
  display_name: string;
  granularities: string[];
  metric_id: string;
  role: string;
  unit: string;
}
export interface ScoreMethodology {
  /**
   * @minItems 2
   * @maxItems 2
   */
  range: [number, number];
  terms: TermView[];
  version: string;
}
export interface SourceMethodology {
  description: string;
  policy: SourcePolicyView;
  signals: SignalView[];
  source: string;
}
export interface SourcePolicyView {
  approval_required: string;
  approval_status: string;
  attribution_required: string;
  attribution_text?: string | null;
  commercial_approval_required: string;
  commercial_approval_status: string;
  commercial_use_allowed: string;
  derived_metrics_allowed: string;
  display: DisplayRulesView;
  field_rules: FieldRuleView[];
  historical_storage_allowed: string;
  notes: string;
  operations: SourceOperationsView;
  raw_storage_allowed: string;
  redistribution_allowed: string;
  references: string[];
  retention_days?: number | null;
  reviewed_at: string;
  source: string;
  stage_clearance: StageClearanceView[];
}
export interface DisplayRulesView {
  branding_required: string;
  link_back_required: string;
  note: string;
}
export interface FieldRuleView {
  display_allowed: string;
  field: string;
  note: string;
  storage_allowed: string;
}
export interface SourceOperationsView {
  budget_group: string;
  cache_max_age_seconds?: number | null;
  expected_publication_lag_seconds: number;
  publication_lag_basis: string;
  quota?: QuotaView | null;
  rate_limit: RateLimitView;
}
export interface QuotaView {
  basis: string;
  limit: number;
  note: string;
  period: string;
  unit: string;
}
export interface RateLimitView {
  basis: string;
  max_concurrency: number;
  max_requests: number;
  note: string;
  per_seconds: number;
}
export interface StageClearanceView {
  cleared: boolean;
  reason: string;
  stage: string;
}
export interface SignalView {
  granularity: string;
  metric: string;
}
