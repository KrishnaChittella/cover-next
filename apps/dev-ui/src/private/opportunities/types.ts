// The private creator-opportunity report, as `metaspike evidence --opportunities --json`
// writes it (`creator_opportunity_rules_v1` or `_v2`). Not part of the public artifact contract.
// The schema in report.schema.json is generated from the Python model and is the
// authority; these types describe what the interface reads from a validated report.

export const OPPORTUNITY_RULES_VERSION = "creator_opportunity_rules_v1";
// Version 2 adds a reaction window and the event-first candidate; both are read the same way.
export const OPPORTUNITY_RULES_VERSIONS: readonly string[] = [OPPORTUNITY_RULES_VERSION, "creator_opportunity_rules_v2"];

export type Readiness = "evidence_supported_candidate" | "provisional_watchlist" | "insufficient_evidence";
export type Standing = "verified" | "unverified" | "synthetic" | "none";
export type Direction = "up" | "down" | "flat" | "unknown";

export interface Facts {
  moving_up: string[];
  moving_down: string[];
  disagreement: boolean;
  timely_event: boolean;
  angle: boolean;
  coverage_known: boolean;
  sources_answering: number;
}

export interface SourceNote {
  source: string;
  kind: string;
  state: string;
  standing: Standing;
  latest_observed_at: string | null;
  freshness: string | null;
}

export interface MovementNote {
  family: string;
  source: string;
  standing: Standing;
  direction: Direction;
  log_lift: number | null;
  robust_z: number | null;
  observed_through: string | null;
}

export interface EventNote {
  event_id: string;
  event_type: string;
  starts_at: string;
  time_precision: string;
  phase: string;
  standing: Standing;
}

export interface VideoAngle {
  origin: "community" | "event";
  addresses: string;
  about: string[];
  references: string[];
  timely: boolean;
  timely_reason: string;
  standing: Standing;
  confirmed: boolean;
  assumptions: string[];
}

export interface Statement {
  text: string;
  standing: Standing;
  references: string[];
}

export interface OpportunityAssessment {
  rules_version: string;
  canonical_game_id: string;
  slug: string;
  name: string;
  as_of: string;
  readiness: Readiness;
  facts: Facts;
  simulated_readiness: Readiness;
  simulated_facts: Facts;
  sources: SourceNote[];
  movements: MovementNote[];
  events: EventNote[];
  angles: VideoAngle[];
  angle_status: "established" | "not_yet_established";
  creator_coverage: string;
  community: string;
  why_now: Statement[];
  unconfirmed: Statement[];
  caveats: string[];
  missing: string[];
  uncertainty: string;
}

export interface ShortlistEntry {
  position: number;
  canonical_game_id: string;
  slug: string;
  readiness: Readiness;
  ahead_of_next: string;
}

export interface OpportunityReport {
  rules_version: string;
  as_of: string;
  distribution: "private_development_only";
  contains_synthetic: boolean;
  contains_unverified: boolean;
  entries: ShortlistEntry[];
  simulated_entries: ShortlistEntry[];
  assessments: OpportunityAssessment[];
  summary: string;
}
