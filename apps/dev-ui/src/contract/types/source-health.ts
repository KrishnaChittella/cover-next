/* eslint-disable */
// GENERATED from <repo>/schemas by `npm run contract:generate`. Do not edit.
// The Python artifact contract (src/metaspike/publishing/artifacts.py) is the source.

export interface SourceHealthArtifact {
  /**
   * INTERNAL / non-binding engineering contract; not the public contract.
   */
  contract_status: "internal";
  event_sources: EventSourceView[];
  generated_at: string;
  run_id: string;
  schema_version: string;
  score_version: string;
  sources: SourceHealthEntry[];
}
/**
 * An event source: what it was asked by `as_of` and what came of it, in counts.
 *
 * A post is not an event. `posts_observed` and `candidates` count announcements;
 * only `candidates_promoted` became event observations, and only those can reach
 * Event Heat or the radar. No title or text of a post is published here.
 */
export interface EventSourceView {
  as_of: string;
  candidates: number;
  candidates_classified: number;
  candidates_not_promoted: number;
  candidates_promoted: number;
  candidates_unresolved: number;
  classification_rules_version: string;
  fetches_after_as_of: number;
  fetches_failed: number;
  fetches_not_sent: number;
  fetches_ok: number;
  games: EventSourceGameView[];
  games_checked: number;
  last_failure_at?: string | null;
  last_success_at?: string | null;
  posts_observed: number;
  promoted_event_types: {
    [k: string]: number;
  };
  purpose: "event_intelligence";
  refusals: {
    [k: string]: number;
  };
  source: string;
  stage_clearance: EventSourceStageView[];
  status: "not_collected" | "collected";
}
export interface EventSourceGameView {
  candidates_classified: number;
  candidates_not_promoted: number;
  candidates_promoted: number;
  canonical_game_id: string;
  fetches_failed: number;
  fetches_ok: number;
  last_checked_at?: string | null;
  name: string;
  posts: number;
  slug: string;
  source_game_ids: string[];
}
export interface EventSourceStageView {
  cleared: boolean;
  reason: string;
  stage: string;
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
