/* eslint-disable */
// GENERATED from <repo>/schemas by `npm run contract:generate`. Do not edit.
// The Python artifact contract (src/metaspike/publishing/artifacts.py) is the source.

/**
 * Dated events that have not started, for games with event coverage.
 *
 * "unavailable" means no game has event coverage, so nothing is known. It is not an
 * empty radar: an available radar with no items is.
 */
export interface RadarArtifact {
  as_of: string;
  availability: "available" | "unavailable";
  /**
   * INTERNAL / non-binding engineering contract; not the public contract.
   */
  contract_status: "internal";
  games_with_coverage: number;
  games_without_coverage: number;
  generated_at: string;
  items: RadarItemView[];
  rules_status: string;
  rules_version: string;
  run_id: string;
  schema_version: string;
  scope_note: string;
  score_version: string;
  summary: string;
  unavailable_reasons: ("no_event_coverage" | "event_coverage_stale")[];
  windows_hours: number[];
}
export interface RadarItemView {
  artifact_path: string;
  canonical_game_id: string;
  contribution: number;
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
  game_event_heat: number;
  hours_until_start: number;
  in_ramp: boolean;
  name: string;
  slug: string;
  sources: string[];
  starts_at: string;
  strength: number;
  text: string;
  time_precision: "day" | "exact";
  title: string;
  window_hours: number;
}
