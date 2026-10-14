// A private report is rendered only after it validates against the schema generated
// from the engine's own model, and only for the rules version this UI understands.
import Ajv2020, { type ErrorObject } from "ajv/dist/2020";
import addFormats from "ajv-formats";

import schema from "./report.schema.json";
import { OPPORTUNITY_RULES_VERSION, type OpportunityReport } from "./types";

export interface Violation {
  path: string;
  message: string;
}

export type Checked =
  | { kind: "ok"; report: OpportunityReport }
  | { kind: "unsupported_version"; version: string }
  | { kind: "invalid"; details: Violation[] };

let compiled: ReturnType<Ajv2020["compile"]> | null = null;

function validator() {
  if (compiled === null) {
    const ajv = new Ajv2020({ allErrors: true, strict: false });
    addFormats(ajv);
    compiled = ajv.compile(schema);
  }
  return compiled;
}

function describe(error: ErrorObject): Violation {
  return { path: error.instancePath || "(root)", message: error.message ?? "invalid" };
}

export function checkReport(value: unknown): Checked {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return { kind: "invalid", details: [{ path: "(root)", message: "is not an object" }] };
  }
  const version = (value as { rules_version?: unknown }).rules_version;
  if (version !== OPPORTUNITY_RULES_VERSION) {
    return { kind: "unsupported_version", version: typeof version === "string" ? version : String(version) };
  }
  const validate = validator();
  if (!validate(value)) {
    return { kind: "invalid", details: (validate.errors ?? []).map(describe) };
  }
  const report = value as OpportunityReport;
  const problems: Violation[] = [];
  if (report.distribution !== "private_development_only") {
    problems.push({ path: "/distribution", message: "must be private_development_only" });
  }
  const ids = new Set(report.assessments.map((a) => a.canonical_game_id));
  for (const [name, list] of [
    ["entries", report.entries],
    ["simulated_entries", report.simulated_entries],
  ] as const) {
    if (list.length !== report.assessments.length) {
      problems.push({ path: `/${name}`, message: "does not list every assessed game exactly once" });
    }
    list.forEach((entry, index) => {
      if (!ids.has(entry.canonical_game_id)) {
        problems.push({ path: `/${name}/${index}`, message: "names a game with no assessment" });
      }
      if (entry.position !== index + 1) {
        problems.push({ path: `/${name}/${index}/position`, message: "is not in order" });
      }
    });
  }
  for (const [index, entry] of report.entries.entries()) {
    const assessment = report.assessments.find((a) => a.canonical_game_id === entry.canonical_game_id);
    if (assessment && assessment.readiness !== entry.readiness) {
      problems.push({ path: `/entries/${index}/readiness`, message: "differs from the game's verified readiness" });
    }
  }
  return problems.length > 0 ? { kind: "invalid", details: problems } : { kind: "ok", report };
}
