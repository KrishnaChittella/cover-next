// @vitest-environment node
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import recorded from "@/test/fixtures/opportunities/recorded.json";
import synthetic from "@/test/fixtures/opportunities/synthetic.json";

import { enablement, loadOpportunityReport, parseView, reportDirectory } from "./loader";
import { checkReport } from "./validate";

const APP = path.resolve(import.meta.dirname, "..", "..", "..");
const ON = { NODE_ENV: "development", METASPIKE_PRIVATE_OPPORTUNITIES: "1" };
const reader = (files: Record<string, string>) => async (file: string) => {
  const text = files[path.basename(file)];
  if (text === undefined) throw new Error("ENOENT");
  return text;
};

describe("loading a private opportunity report", () => {
  it("is off unless a developer turns it on, and always off in production", async () => {
    expect(enablement({ NODE_ENV: "development" })).toMatchObject({ enabled: false });
    expect(enablement({ NODE_ENV: "development", METASPIKE_PRIVATE_OPPORTUNITIES: "true" })).toMatchObject({
      enabled: false,
    });
    expect(enablement({ NODE_ENV: "production", METASPIKE_PRIVATE_OPPORTUNITIES: "1" })).toMatchObject({
      enabled: false,
      reason: expect.stringContaining("production"),
    });
    expect(enablement(ON)).toEqual({ enabled: true });
    let read = 0;
    const counting = async () => {
      read += 1;
      return JSON.stringify(recorded);
    };
    const off = await loadOpportunityReport("verified", { NODE_ENV: "production", METASPIKE_PRIVATE_OPPORTUNITIES: "1" }, counting, APP);
    expect(off.kind).toBe("disabled");
    expect(read).toBe(0); // nothing is even read
  });

  it("reads one of two fixed files, never a path from a request", async () => {
    expect(parseView(undefined)).toBe("verified");
    expect(parseView("synthetic")).toBe("synthetic");
    for (const hostile of ["../../etc/passwd", "/etc/passwd", "verified.json", "SYNTHETIC", ["synthetic"], ""]) {
      expect(parseView(hostile as string)).toBe("verified");
    }
    const asked: string[] = [];
    const remember = async (file: string) => {
      asked.push(file);
      return JSON.stringify(recorded);
    };
    await loadOpportunityReport(parseView("../../../secrets"), ON, remember, APP);
    await loadOpportunityReport(parseView("synthetic"), ON, remember, APP);
    const directory = path.resolve(APP, "..", "..", ".metaspike", "private", "opportunities");
    expect(asked).toEqual([path.join(directory, "verified.json"), path.join(directory, "synthetic.json")]);
  });

  it("refuses a directory that is served or published, or not absolute", () => {
    expect(reportDirectory({}, APP)).toBe(path.resolve(APP, "..", "..", ".metaspike", "private", "opportunities"));
    expect(() => reportDirectory({ METASPIKE_OPPORTUNITY_DIR: "relative/dir" }, APP)).toThrow("absolute");
    expect(() => reportDirectory({ METASPIKE_OPPORTUNITY_DIR: path.join(APP, "public", "data") }, APP)).toThrow(
      "served or published",
    );
    expect(() =>
      reportDirectory({ METASPIKE_OPPORTUNITY_DIR: path.resolve(APP, "..", "..", "artifacts") }, APP),
    ).toThrow("served or published");
    expect(reportDirectory({ METASPIKE_OPPORTUNITY_DIR: "/tmp/private-reports" }, APP)).toBe("/tmp/private-reports");
  });

  it("fails closed when the report is absent, malformed, or another version", async () => {
    const absent = await loadOpportunityReport("synthetic", ON, reader({}), APP);
    expect(absent).toMatchObject({ kind: "absent", view: "synthetic" });
    const garbage = await loadOpportunityReport("verified", ON, reader({ "verified.json": "{not json" }), APP);
    expect(garbage.kind).toBe("invalid");
    const later = { ...recorded, rules_version: "creator_opportunity_rules_v3" };
    const unsupported = await loadOpportunityReport("verified", ON, reader({ "verified.json": JSON.stringify(later) }), APP);
    expect(unsupported).toMatchObject({ kind: "unsupported_version", version: "creator_opportunity_rules_v3" });
    const v2 = { ...recorded, rules_version: "creator_opportunity_rules_v2" };
    const second = await loadOpportunityReport("verified", ON, reader({ "verified.json": JSON.stringify(v2) }), APP);
    expect(second.kind).toBe("ok");
    const good = await loadOpportunityReport("verified", ON, reader({ "verified.json": JSON.stringify(recorded) }), APP);
    expect(good.kind).toBe("ok");
  });

  it("validates against the engine's schema and its own consistency", () => {
    expect(checkReport(recorded).kind).toBe("ok");
    expect(checkReport(synthetic).kind).toBe("ok");
    expect(checkReport([]).kind).toBe("invalid");
    const missing = structuredClone(recorded) as Record<string, unknown>;
    delete missing.assessments;
    expect(checkReport(missing).kind).toBe("invalid");
    const wrong = structuredClone(recorded);
    wrong.entries[0]!.readiness = "evidence_supported_candidate";
    const result = checkReport(wrong);
    expect(result.kind).toBe("invalid");
    const extra = structuredClone(recorded) as Record<string, unknown>;
    extra.score = 99; // nothing the engine does not write is accepted
    expect(checkReport(extra).kind).toBe("invalid");
    const shuffled = structuredClone(synthetic);
    shuffled.entries.reverse();
    expect(checkReport(shuffled).kind).toBe("invalid");
    const leaked = structuredClone(recorded);
    (leaked as { distribution: string }).distribution = "public";
    expect(checkReport(leaked).kind).toBe("invalid");
  });

  it("refuses a game assessed twice, listed twice, missing, or at another readiness", () => {
    type Report = typeof synthetic;
    const refusal = (mutate: (r: Report) => void) => {
      const copy = structuredClone(synthetic);
      mutate(copy);
      const result = checkReport(copy);
      expect(result.kind).toBe("invalid");
      return result.kind === "invalid" ? result.details.map((d) => `${d.path} ${d.message}`).join("; ") : "";
    };
    const first = synthetic.assessments[0]!.canonical_game_id;
    expect(synthetic.assessments.length).toBeGreaterThan(2);
    // The same game assessed twice.
    expect(refusal((r) => { r.assessments[1]!.canonical_game_id = first; })).toContain(
      "/assessments names the same game more than once",
    );
    // The same game twice in the verified list: one assessed game is then missing from it.
    const twiceVerified = refusal((r) => { r.entries[1]!.canonical_game_id = r.entries[0]!.canonical_game_id; });
    expect(twiceVerified).toContain("/entries names the same game more than once");
    expect(twiceVerified).toContain("/entries does not list exactly the assessed games");
    // The same in the simulated list.
    const twiceSimulated = refusal((r) => {
      r.simulated_entries[2]!.canonical_game_id = r.simulated_entries[0]!.canonical_game_id;
    });
    expect(twiceSimulated).toContain("/simulated_entries names the same game more than once");
    expect(twiceSimulated).toContain("/simulated_entries does not list exactly the assessed games");
    // An assessed game missing from either list.
    expect(refusal((r) => { r.entries.pop(); })).toContain("/entries does not list exactly the assessed games");
    expect(refusal((r) => { r.simulated_entries.pop(); })).toContain(
      "/simulated_entries does not list exactly the assessed games",
    );
    // A simulated entry whose readiness is not its game's simulated readiness.
    const other = (value: string) =>
      value === "insufficient_evidence" ? "provisional_watchlist" : "insufficient_evidence";
    expect(refusal((r) => { r.simulated_entries[0]!.readiness = other(r.simulated_entries[0]!.readiness); })).toContain(
      "/simulated_entries/0/readiness differs from the game's simulated readiness",
    );
    // Valid reports are still accepted.
    expect(checkReport(synthetic).kind).toBe("ok");
    expect(checkReport(recorded).kind).toBe("ok");
  });

  it("refuses to hand over a report it would have to correct", async () => {
    const copy = structuredClone(synthetic);
    copy.simulated_entries[0]!.readiness =
      copy.simulated_entries[0]!.readiness === "insufficient_evidence" ? "provisional_watchlist" : "insufficient_evidence";
    const loaded = await loadOpportunityReport("synthetic", ON, reader({ "synthetic.json": JSON.stringify(copy) }), APP);
    expect(loaded.kind).toBe("invalid");
    expect(loaded).not.toHaveProperty("report");
  });

  it("never puts a private report where it could be served, and never in a client bundle", () => {
    const publicDir = path.join(APP, "public");
    const files = existsSync(publicDir) ? readdirSync(publicDir, { recursive: true }).map(String) : [];
    expect(files.filter((f) => f.includes("opportunit"))).toEqual([]);
    // No client component imports the loader, the validator or the private types.
    const sources = readdirSync(path.join(APP, "src"), { recursive: true })
      .map(String)
      .filter((f) => /\.(ts|tsx)$/.test(f) && !f.endsWith(".test.ts") && !f.endsWith(".test.tsx"));
    for (const file of sources) {
      const text = readFileSync(path.join(APP, "src", file), "utf-8");
      if (text.startsWith('"use client"')) {
        expect(text, file).not.toMatch(/private\/opportunities|components\/opportunities/);
      }
    }
    const pages = ["page.tsx", path.join("[slug]", "page.tsx"), path.join("compare", "page.tsx")].map((p) =>
      readFileSync(path.join(APP, "src", "app", "opportunities", p), "utf-8"),
    );
    for (const page of pages) {
      expect(page).toContain('export const dynamic = "force-dynamic"');
      expect(page).not.toContain("use client");
    }
    expect(readFileSync(path.join(APP, ".gitignore"), "utf-8")).toContain("public/data/");
  });
});
