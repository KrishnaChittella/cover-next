/* eslint-disable */
// GENERATED from <repo>/schemas by `npm run contract:generate`. Do not edit.
// The Python artifact contract (src/metaspike/publishing/artifacts.py) is the source.

export interface CurrentArtifact {
  active_sources: string[];
  confidence_ceiling: number;
  /**
   * INTERNAL / non-binding engineering contract; not the public contract.
   */
  contract_status: "internal";
  entries: LeaderboardEntry[];
  expected_latest_day: string;
  generated_at: string;
  run_id: string;
  schema_version: string;
  scope_note: string;
  score_version: string;
  unranked: UnrankedEntry[];
}
export interface LeaderboardEntry {
  artifact_path: string;
  as_of_day: string;
  canonical_game_id: string;
  confidence: number;
  confidence_band: "low" | "medium" | "high";
  lifecycle_state:
    "PRE_RELEASE" | "LAUNCH_WINDOW" | "RELEASED" | "EVERGREEN" | "LIVE_SERVICE" | "DORMANT";
  name: string;
  rank: number;
  score: number;
  slug: string;
  source_coverage: SourceCoverage[];
  summary: string;
  top_reasons: string[];
  updated_at: string;
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
export interface UnrankedEntry {
  artifact_path: string;
  canonical_game_id: string;
  lifecycle_state:
    "PRE_RELEASE" | "LAUNCH_WINDOW" | "RELEASED" | "EVERGREEN" | "LIVE_SERVICE" | "DORMANT";
  name: string;
  reason:
    | "no_observations"
    | "insufficient_history"
    | "insufficient_recent_data"
    | "zero_baseline"
    | "zero_dispersion";
  slug: string;
  source_coverage: SourceCoverage[];
  summary: string;
  updated_at: string;
}
