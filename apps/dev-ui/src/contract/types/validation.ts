/* eslint-disable */
// GENERATED from <repo>/schemas by `npm run contract:generate`. Do not edit.
// The Python artifact contract (src/metaspike/publishing/artifacts.py) is the source.

/**
 * Historical validation, or the plain statement that none has been run.
 *
 * "not_run" carries no report and no number. It is not a result of zero.
 */
export interface ValidationArtifact {
  availability: "available" | "not_run";
  /**
   * INTERNAL / non-binding engineering contract; not the public contract.
   */
  contract_status: "internal";
  generated_at: string;
  outcome_definition: OutcomeDefinitionView;
  protocol_version: string;
  report?: ValidationReportView | null;
  run_id: string;
  schema_version: string;
  scope_note: string;
  score_version: string;
}
/**
 * `outcome_rules_v1` as published: what each outcome is and every constant in it.
 */
export interface OutcomeDefinitionView {
  parameters: ParameterView[];
  terms: TermView[];
  unavailable: TermView[];
  version: string;
}
export interface ParameterView {
  name: string;
  rationale: string;
  value: string;
}
export interface TermView {
  formula: string;
  name: string;
  rationale: string;
}
export interface ValidationReportView {
  confidence_bands: StratumView[];
  created_at: string;
  data_through: string;
  event_rules_version: string;
  events: EventBacktestView;
  evidence: EvidenceView;
  first_snapshot_day: string;
  format: string;
  games_ranked: number;
  games_with_outcome: number;
  independent_sources: number;
  inspection: InspectionDayView[];
  inspection_truncated: boolean;
  last_snapshot_day: string;
  lead_time: LeadTimeView;
  leakage_checks: LeakageCheckView[];
  limitations: string[];
  movement_24h: SpreadView;
  outcome_rules_version: string;
  overall: OutcomeSummaryView;
  persistence_7d: SpreadView;
  primary_evaluable: number;
  primary_unavailable: {
    [k: string]: number;
  };
  protocol_version: string;
  ranked: number;
  recommendations: number;
  report_id: string;
  score_bins: StratumView[];
  score_version: string;
  snapshot_dates: number;
  strategies: StrategyResultView[];
  universe_size: number;
  unranked: number;
}
export interface StratumView {
  label: string;
  outcomes: OutcomeSummaryView;
}
/**
 * A set of primary outcomes. With a count of 0 every statistic is null, never 0.
 */
export interface OutcomeSummaryView {
  count: number;
  downside_rate?: number | null;
  hit_rate?: number | null;
  maximum?: number | null;
  mean?: number | null;
  median?: number | null;
  minimum?: number | null;
}
export interface EventBacktestView {
  note: string;
  real_event_history: boolean;
  snapshots_with_event_intelligence: number;
}
export interface EvidenceView {
  checks: StatusCheckView[];
  conclusion: string;
  holdout_rationale: string;
  holdout_supported: boolean;
  status: "framework_only" | "insufficient_sample" | "exploratory" | "validated";
}
export interface StatusCheckView {
  rationale: string;
  reached: boolean;
  requirements: RequirementView[];
  status: "framework_only" | "insufficient_sample" | "exploratory" | "validated";
}
export interface RequirementView {
  actual: number;
  met: boolean;
  name: string;
  required: number;
}
export interface InspectionDayView {
  as_of: string;
  day: string;
  ranked: InspectionEntryView[];
  unranked: InspectionUnrankedView[];
}
export interface InspectionEntryView {
  confidence: number;
  lead_time_days?: number | null;
  movement_24h?: number | null;
  name: string;
  outcome_availability: "available" | "unavailable";
  persistence_7d?: number | null;
  rank: number;
  realized_uplift_72h?: number | null;
  score: number;
  slug: string;
  unavailable_reason?: string | null;
}
export interface InspectionUnrankedView {
  name: string;
  reason: string;
  slug: string;
}
export interface LeadTimeView {
  found: number;
  mean_days?: number | null;
  median_days?: number | null;
  none_in_window: number;
  unavailable: number;
}
export interface LeakageCheckView {
  checked: number;
  description: string;
  name: string;
  passed: boolean;
}
export interface SpreadView {
  count: number;
  mean?: number | null;
  median?: number | null;
}
export interface StrategyResultView {
  association: AssociationView;
  description: string;
  label: string;
  strategy: string;
  top_k: TopKView[];
}
export interface AssociationView {
  days_skipped: number;
  days_used: number;
  mean_rho?: number | null;
  median_rho?: number | null;
  negative_days: number;
  positive_days: number;
  statistic: string;
}
export interface TopKView {
  days_with_full_group: number;
  k: number;
  outcomes: OutcomeSummaryView;
}
