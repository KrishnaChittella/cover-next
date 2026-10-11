// Runtime validation against the checked-in JSON Schemas. The schema is the authority:
// anything it rejects is never rendered.
import Ajv2020, { type ErrorObject, type ValidateFunction } from "ajv/dist/2020";
import addFormats from "ajv-formats";

import type { ArtifactKind, EventIntelligenceView, RadarArtifact } from "./index";
import currentSchema from "./schemas/current.schema.json";
import gameSchema from "./schemas/game.schema.json";
import manifestSchema from "./schemas/manifest.schema.json";
import methodologySchema from "./schemas/methodology.schema.json";
import radarSchema from "./schemas/radar.schema.json";
import sourceHealthSchema from "./schemas/source-health.schema.json";

const SCHEMAS: Record<ArtifactKind, object> = {
  manifest: manifestSchema,
  current: currentSchema,
  game: gameSchema,
  "source-health": sourceHealthSchema,
  methodology: methodologySchema,
  radar: radarSchema,
};

// The contract version this build of the UI was generated from.
export const SUPPORTED_SCHEMA_VERSION = "1.1.0";

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
