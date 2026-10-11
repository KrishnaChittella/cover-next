// Runtime validation against the checked-in JSON Schemas. The schema is the authority:
// anything it rejects is never rendered.
import Ajv2020, { type ErrorObject, type ValidateFunction } from "ajv/dist/2020";
import addFormats from "ajv-formats";

import type { ArtifactKind } from "./index";
import currentSchema from "./schemas/current.schema.json";
import gameSchema from "./schemas/game.schema.json";
import manifestSchema from "./schemas/manifest.schema.json";
import methodologySchema from "./schemas/methodology.schema.json";
import sourceHealthSchema from "./schemas/source-health.schema.json";

const SCHEMAS: Record<ArtifactKind, object> = {
  manifest: manifestSchema,
  current: currentSchema,
  game: gameSchema,
  "source-health": sourceHealthSchema,
  methodology: methodologySchema,
};

// The contract version this build of the UI was generated from.
export const SUPPORTED_SCHEMA_VERSION = "1.0.0";

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
