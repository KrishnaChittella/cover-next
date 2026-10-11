// Runtime validation against the checked-in JSON Schemas. The schema is the authority:
// anything it rejects is never rendered.
import Ajv2020, { type ErrorObject, type ValidateFunction } from "ajv/dist/2020";
import addFormats from "ajv-formats";

import type { ArtifactKind, EventIntelligenceView, RadarArtifact, ValidationArtifact } from "./index";
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
export const SUPPORTED_SCHEMA_VERSION = "1.3.0";

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
