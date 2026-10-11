/* eslint-disable */
// GENERATED from <repo>/schemas by `npm run contract:generate`. Do not edit.
// The Python artifact contract (src/metaspike/publishing/artifacts.py) is the source.

export interface SourceHealthArtifact {
  /**
   * INTERNAL / non-binding engineering contract; not the public contract.
   */
  contract_status: "internal";
  generated_at: string;
  run_id: string;
  schema_version: string;
  score_version: string;
  sources: SourceHealthEntry[];
}
export interface SourceHealthEntry {
  entities_attempted: number;
  entities_failed: number;
  entities_not_mapped: number;
  entities_succeeded: number;
  failures: SourceFailure[];
  freshness: SourceFreshness;
  last_success?: string | null;
  source: string;
  status: "healthy" | "degraded" | "failed";
}
export interface SourceFailure {
  canonical_game_id: string;
  error_code: string;
  slug: string;
}
export interface SourceFreshness {
  expected_latest_day: string;
  latest_observed_day?: string | null;
  staleness_days?: number | null;
}
