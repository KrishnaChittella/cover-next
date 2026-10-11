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
  game_count: number;
  generated_at: string;
  ranked_count: number;
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
