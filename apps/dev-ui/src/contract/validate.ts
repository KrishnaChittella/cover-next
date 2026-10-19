// Runtime validation against the checked-in JSON Schemas. The schema is the authority:
// anything it rejects is never rendered.
import Ajv2020, { type ErrorObject, type ValidateFunction } from "ajv/dist/2020";
import addFormats from "ajv-formats";

import type {
  ArtifactKind,
  EventIntelligenceView,
  Manifest,
  RadarArtifact,
  SourceHealthArtifact,
  ValidationArtifact,
} from "./index";
import currentSchema from "./schemas/current.schema.json";
import gameSchema from "./schemas/game.schema.json";
import manifestSchema from "./schemas/manifest.schema.json";
import methodologySchema from "./schemas/methodology.schema.json";
import radarSchema from "./schemas/radar.schema.json";
import validationSchema from "./schemas/validation.schema.json";
import sourceHealthSchema from "./schemas/source-health.schema.json";

const SCHEMAS: Record<ArtifactKind, object> = {
  manifest: manifestSchema,
  current: currentSchema,
  game: gameSchema,
  "source-health": sourceHealthSchema,
  methodology: methodologySchema,
  radar: radarSchema,
  validation: validationSchema,
};

// The contract version this build of the UI was generated from.
export const SUPPORTED_SCHEMA_VERSION = "1.5.0";

let ajv: Ajv2020 | null = null;
const compiled = new Map<ArtifactKind, ValidateFunction>();

function validator(kind: ArtifactKind): ValidateFunction {
  const existing = compiled.get(kind);
  if (existing) return existing;
  if (ajv === null) {
    ajv = new Ajv2020({ allErrors: true, strict: false });
    addFormats(ajv);
  }
  const built = ajv.compile(SCHEMAS[kind]);
  compiled.set(kind, built);
  return built;
}

export interface Violation {
  path: string;
  message: string;
}

function describe(error: ErrorObject): Violation {
  const extra =
    error.keyword === "additionalProperties"
      ? ` (${String((error.params as { additionalProperty?: string }).additionalProperty)})`
      : "";
  return { path: error.instancePath || "(root)", message: `${error.message ?? "invalid"}${extra}` };
}

/** Every way `value` breaks the schema for `kind`. Empty means it conforms. */
export function violations(kind: ArtifactKind, value: unknown): Violation[] {
  const validate = validator(kind);
  if (validate(value)) return [];
  return (validate.errors ?? []).map(describe);
}

/**
 * What the schema cannot say about event intelligence: unavailable is never a number and
 * a number is never unavailable. The publisher enforces this; the interface checks it
 * again, because showing a heat of 0 for a game nobody looked at would be a false claim.
 */
export function eventIntelligenceViolations(events: EventIntelligenceView, at = "/event_intelligence"): Violation[] {
  const found: Violation[] = [];
  const heat = events.event_heat ?? null;
  if (events.availability === "unavailable") {
    if (heat !== null) found.push({ path: `${at}/event_heat`, message: "must be null when event intelligence is unavailable" });
    if (events.contributions.length > 0) found.push({ path: `${at}/contributions`, message: "must be empty when unavailable" });
    if (events.unavailable_reasons.length === 0) found.push({ path: `${at}/unavailable_reasons`, message: "must give a reason when unavailable" });
  } else {
    if (heat === null) found.push({ path: `${at}/event_heat`, message: "must be a number when event intelligence is available" });
    if (events.unavailable_reasons.length > 0) found.push({ path: `${at}/unavailable_reasons`, message: "must be empty when available" });
  }
  return found;
}

/**
 * What the schema cannot say about an event source: a post is not an event. Promoted and
 * unpromoted candidates add up to the candidates, nothing is promoted that was not
 * classified, and a source that was never asked reports nothing.
 */
export function eventSourceViolations(health: SourceHealthArtifact): Violation[] {
  const found: Violation[] = [];
  health.event_sources.forEach((source, index) => {
    const at = `/event_sources/${index}`;
    if (source.candidates_promoted + source.candidates_not_promoted !== source.candidates) {
      found.push({ path: `${at}/candidates`, message: "promoted and unpromoted candidates do not add up" });
    }
    if (source.candidates_promoted > source.candidates_classified || source.candidates_classified > source.candidates) {
      found.push({ path: `${at}/candidates_promoted`, message: "more candidates promoted than classified" });
    }
    const typed = Object.values(source.promoted_event_types).reduce((sum, count) => sum + count, 0);
    if (typed !== source.candidates_promoted) {
      found.push({ path: `${at}/promoted_event_types`, message: "do not add up to the promoted count" });
    }
    const asked = source.fetches_ok + source.fetches_failed + source.fetches_not_sent > 0;
    if ((source.status === "collected") !== asked) {
      found.push({ path: `${at}/status`, message: "disagrees with the source's fetches" });
    }
    if (!asked && (source.posts_observed > 0 || source.candidates > 0 || source.games.length > 0)) {
      found.push({ path: `${at}/posts_observed`, message: "reports posts without the source having been asked" });
    }
    if (source.candidates_promoted > 0 && source.fetches_ok === 0) {
      found.push({ path: `${at}/candidates_promoted`, message: "promoted candidates without a successful fetch" });
    }
  });
  return found;
}

/** A bundle is private exactly when it names a source that is not cleared to be shown. */
export function manifestViolations(manifest: Manifest): Violation[] {
  const restricted = manifest.restricted_sources.length > 0;
  if ((manifest.distribution === "private_development_only") !== restricted) {
    return [{ path: "/distribution", message: "disagrees with the manifest's restricted sources" }];
  }
  return [];
}

/** An event source that is not cleared for public launch must make its bundle private. */
export function privacyViolations(manifest: Manifest, health: SourceHealthArtifact): Violation[] {
  const named = new Set(manifest.restricted_sources.map((source) => source.source));
  const found: Violation[] = [];
  health.event_sources.forEach((source, index) => {
    const asked = source.status === "collected" || source.fetches_after_as_of > 0;
    const cleared = source.stage_clearance.some((stage) => stage.stage === "public_launch" && stage.cleared);
    if (asked && !cleared && !named.has(source.source)) {
      found.push({
        path: `/event_sources/${index}/source`,
        message: "is not cleared for public launch, yet the manifest does not mark the bundle as private",
      });
    }
  });
  return found;
}

export function radarViolations(radar: RadarArtifact): Violation[] {
  const found: Violation[] = [];
  if (radar.availability === "unavailable") {
    if (radar.items.length > 0) found.push({ path: "/items", message: "must be empty when the radar is unavailable" });
    if (radar.unavailable_reasons.length === 0) found.push({ path: "/unavailable_reasons", message: "must give a reason when unavailable" });
  } else if (radar.unavailable_reasons.length > 0) {
    found.push({ path: "/unavailable_reasons", message: "must be empty when available" });
  }
  return found;
}

/**
 * What the schema cannot say about validation: "not run" carries no report, and a
 * report never claims a status its own checks do not support.
 */
export function validationViolations(validation: ValidationArtifact): Violation[] {
  const found: Violation[] = [];
  const report = validation.report ?? null;
  if (validation.availability === "not_run") {
    if (report !== null) found.push({ path: "/report", message: "must be null when no backtest has been run" });
    return found;
  }
  if (report === null) {
    found.push({ path: "/report", message: "must be present when validation is available" });
    return found;
  }
  const reached = report.evidence.checks.filter((check) => check.reached).map((check) => check.status);
  const supported = reached.length > 0 ? reached[reached.length - 1] : "framework_only";
  if (report.evidence.status !== supported) {
    found.push({ path: "/report/evidence/status", message: `claims ${report.evidence.status}; its checks support ${supported}` });
  }
  if (report.evidence.checks.some((check) => check.reached && check.requirements.some((item) => !item.met))) {
    found.push({ path: "/report/evidence/checks", message: "a status is marked reached with an unmet requirement" });
  }
  if (report.leakage_checks.length === 0 || report.leakage_checks.some((check) => !check.passed)) {
    found.push({ path: "/report/leakage_checks", message: "every leakage check must be present and passed" });
  }
  // Event-source provenance: a report cannot carry event intelligence and name no source.
  const events = report.events;
  const sources = events.event_sources_used;
  if (sources.some((source, index) => index > 0 && sources[index - 1]! >= source)) {
    found.push({ path: "/report/events/event_sources_used", message: "must be sorted and without repeats" });
  }
  if ((events.real_event_history || events.snapshots_with_event_intelligence > 0) && sources.length === 0) {
    found.push({ path: "/report/events/event_sources_used", message: "event intelligence was carried but no event source is named" });
  }
  if (events.snapshots_with_positive_event_heat > events.snapshots_with_event_intelligence) {
    found.push({ path: "/report/events/snapshots_with_positive_event_heat", message: "exceeds the snapshots with event intelligence" });
  }
  if (!report.leakage_checks.some((check) => check.name === "source_publication_lag_respected")) {
    found.push({ path: "/report/leakage_checks", message: "the source publication lag was not checked" });
  }
  // Point-in-time: a recommendation sees only days before its own, and is judged only on
  // days at or after it. ISO days compare correctly as strings.
  report.inspection.forEach((day, index) => {
    if (!(day.latest_due_day < day.day)) {
      found.push({ path: `/report/inspection/${index}/latest_due_day`, message: "is not before the recommendation day" });
    }
    if (!(day.primary_window_start >= day.day && day.primary_window_start <= day.primary_window_end)) {
      found.push({ path: `/report/inspection/${index}/primary_window_start`, message: "the outcome window does not follow the recommendation" });
    }
  });
  for (const field of ["snapshot_set_hash", "label_set_hash"] as const) {
    if (!/^[0-9a-f]{64}$/.test(report[field])) {
      found.push({ path: `/report/${field}`, message: "is not a SHA-256 digest" });
    }
  }
  return found;
}
