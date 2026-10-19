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
  const assessedIds = report.assessments.map((a) => a.canonical_game_id);
  const ids = new Set(assessedIds);
  if (ids.size !== assessedIds.length) {
    problems.push({ path: "/assessments", message: "names the same game more than once" });
  }
  const byId = new Map(report.assessments.map((a) => [a.canonical_game_id, a]));
  for (const [name, list, field, label] of [
    ["entries", report.entries, "readiness", "verified readiness"],
    ["simulated_entries", report.simulated_entries, "simulated_readiness", "simulated readiness"],
  ] as const) {
    if (list.length !== report.assessments.length) {
      problems.push({ path: `/${name}`, message: "does not list every assessed game exactly once" });
    }
    const listed = new Set(list.map((entry) => entry.canonical_game_id));
    if (listed.size !== list.length) {
      problems.push({ path: `/${name}`, message: "names the same game more than once" });
    }
    // Exactly the assessed set: nothing missing, nothing extra. Never corrected, only refused.
    if (listed.size !== ids.size || [...ids].some((id) => !listed.has(id))) {
      problems.push({ path: `/${name}`, message: "does not list exactly the assessed games" });
    }
    list.forEach((entry, index) => {
      if (!ids.has(entry.canonical_game_id)) {
        problems.push({ path: `/${name}/${index}`, message: "names a game with no assessment" });
      }
      if (entry.position !== index + 1) {
        problems.push({ path: `/${name}/${index}/position`, message: "is not in order" });
      }
      const assessment = byId.get(entry.canonical_game_id);
      if (assessment && assessment[field] !== entry.readiness) {
        problems.push({ path: `/${name}/${index}/readiness`, message: `differs from the game's ${label}` });
      }
    });
  }
  return problems.length > 0 ? { kind: "invalid", details: problems } : { kind: "ok", report };
}
