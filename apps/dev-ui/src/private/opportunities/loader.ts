// SERVER ONLY. Reads a private creator-opportunity report from local disk, for the
// developer UI, when a developer has explicitly enabled it. Never imported by a client
// component, never served as a static file, never read in a production build.
//
//   enabled   only when METASPIKE_PRIVATE_OPPORTUNITIES=1 and NODE_ENV is not
//             "production" (next build and next start run as production)
//   where     <repo>/.metaspike/private/opportunities/, or an absolute directory in
//             METASPIKE_OPPORTUNITY_DIR (a server environment variable)
//   which     one of two fixed file names, chosen by a fixed view name; nothing a
//             browser sends becomes part of a path
import { readFile } from "node:fs/promises";
import path from "node:path";

import { checkReport, type Violation } from "./validate";
import type { OpportunityReport } from "./types";

export const VIEWS = ["verified", "synthetic"] as const;
export type View = (typeof VIEWS)[number];

const FILES: Record<View, string> = { verified: "verified.json", synthetic: "synthetic.json" };

export type Loaded =
  | { kind: "disabled"; reason: string }
  | { kind: "absent"; file: string; view: View }
  | { kind: "invalid"; file: string; details: Violation[] }
  | { kind: "unsupported_version"; file: string; version: string }
  | { kind: "ok"; file: string; view: View; report: OpportunityReport };

type Env = Record<string, string | undefined>;

/** Only the exact word "synthetic" selects the synthetic view. Anything else: verified. */
export function parseView(raw: string | string[] | undefined): View {
  return raw === "synthetic" ? "synthetic" : "verified";
}

export function enablement(env: Env = process.env): { enabled: true } | { enabled: false; reason: string } {
  if (env.NODE_ENV === "production") {
    return {
      enabled: false,
      reason: "Private opportunity reports are never read in a production build or server.",
    };
  }
  if (env.METASPIKE_PRIVATE_OPPORTUNITIES !== "1") {
    return {
      enabled: false,
      reason: "Private opportunity reports are disabled. Set METASPIKE_PRIVATE_OPPORTUNITIES=1 for the dev server.",
    };
  }
  return { enabled: true };
}

const APP = process.cwd();

export function reportDirectory(env: Env = process.env, app: string = APP): string {
  const configured = (env.METASPIKE_OPPORTUNITY_DIR ?? "").trim();
  const directory =
    configured === ""
      ? path.resolve(/*turbopackIgnore: true*/ app, "..", "..", ".metaspike", "private", "opportunities")
      : configured;
  if (!path.isAbsolute(directory)) {
    throw new Error("METASPIKE_OPPORTUNITY_DIR must be an absolute path");
  }
  const resolved = path.resolve(directory);
  const servedRoots = [path.resolve(app, "public"), path.resolve(app, "..", "..", "artifacts")];
  if (servedRoots.some((root) => resolved === root || resolved.startsWith(root + path.sep))) {
    throw new Error("a private report directory may not be inside a served or published directory");
  }
  return resolved;
}

export async function loadOpportunityReport(
  view: View,
  env: Env = process.env,
  // Untraced on purpose: the report is read at request time from outside the app and
  // must never be bundled or traced into build output.
  read: (file: string) => Promise<string> = (file) => readFile(/*turbopackIgnore: true*/ file, "utf-8"),
  app: string = APP,
): Promise<Loaded> {
  const allowed = enablement(env);
  if (!allowed.enabled) return { kind: "disabled", reason: allowed.reason };
  let directory: string;
  try {
    directory = reportDirectory(env, app);
  } catch (error) {
    return { kind: "disabled", reason: (error as Error).message };
  }
  const file = path.join(/*turbopackIgnore: true*/ directory, FILES[view]);
  let text: string;
  try {
    text = await read(file);
  } catch {
    return { kind: "absent", file, view };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { kind: "invalid", file, details: [{ path: "(root)", message: "is not JSON" }] };
  }
  const checked = checkReport(parsed);
  if (checked.kind === "ok") return { kind: "ok", file, view, report: checked.report };
  if (checked.kind === "unsupported_version") return { kind: "unsupported_version", file, version: checked.version };
  return { kind: "invalid", file, details: checked.details };
}
