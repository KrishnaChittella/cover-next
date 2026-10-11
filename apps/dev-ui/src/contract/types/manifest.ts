/* eslint-disable */
// GENERATED from <repo>/schemas by `npm run contract:generate`. Do not edit.
// The Python artifact contract (src/metaspike/publishing/artifacts.py) is the source.

export interface Manifest {
  artifacts: ManifestPaths;
  content_hash: string;
  /**
   * INTERNAL / non-binding engineering contract; not the public contract.
   */
  contract_status: "internal";
  distribution: "internal" | "private_development_only";
  game_count: number;
  generated_at: string;
  ranked_count: number;
  restricted_sources: RestrictedSourceView[];
  run_id: string;
  schema_version: string;
  score_version: string;
  version_path: string;
}
export interface ManifestPaths {
  current: string;
  games: {
    [k: string]: string;
  };
  methodology: string;
  radar: string;
  source_health: string;
  validation: string;
}
/**
 * A source whose data is in this bundle and is not cleared to be shown publicly.
 */
export interface RestrictedSourceView {
  cleared_stages: string[];
  purpose: string;
  reason: string;
  source: string;
}
