#!/usr/bin/env node
// Private, local creator-opportunity reports for the developer UI. Never published.
//
//   node scripts/opportunities.mjs recorded    the recorded-only report (verified view)
//   node scripts/opportunities.mjs integrated  every genuine recorded source read together
//   node scripts/opportunities.mjs synthetic   the SYNTHETIC demonstration
//   node scripts/opportunities.mjs schema      regenerate the private report schema
//   node scripts/opportunities.mjs schema --check
//
// Reports go to <repo>/.metaspike/private/opportunities/, which is git-ignored and is
// not under any served directory. Everything runs offline through the existing
// `metaspike evidence --opportunities` command: no source is called.
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";

const here = path.dirname(new URL(import.meta.url).pathname);
const app = path.resolve(here, "..");
const repo = path.resolve(app, "..", "..");
const reports = path.join(repo, ".metaspike", "private", "opportunities");
const schemaFile = path.join(app, "src", "private", "opportunities", "report.schema.json");

const RECORDED = [
  "--as-of", "2026-10-05T12:00:00+00:00",
  "--wikimedia-replay", "tests/fixtures/wikimedia",
  "--recorded-through", "2026-10-03",
];
// Every genuine recorded input, read at one moment just after the recorded Steam
// response (2026-10-06 01:25:26 UTC): the catalogue fixtures (pinned), the pageviews
// (pinned, through 2026-10-03, so one due day short by then) and that one Steam
// response, replayed into a fresh private sample store dated when it was recorded. The
// Steam sample is genuine and of UNVERIFIED origin: shown, never counted.
const RECORDED_CATALOGUE = ["tests/fixtures/catalogue", "tests/fixtures/bootstrap_catalogue"];
const INTEGRATED_AS_OF = "2026-10-06T01:30:00+00:00";
const integratedWork = path.join(repo, ".metaspike", "private", "integrated");

function integrated(out) {
  rmSync(integratedWork, { recursive: true, force: true }); // the same inputs every time
  mkdirSync(integratedWork, { recursive: true });
  execFileSync(
    "uv",
    [
      "run", "metaspike", "sample",
      "--recorded", ...RECORDED_CATALOGUE,
      "--state", path.join(integratedWork, "state.json"),
      "--store-dir", path.join(integratedWork, "samples"),
      "--replay", "tests/fixtures/steam_players",
    ],
    { cwd: repo, stdio: ["ignore", "ignore", "inherit"] },
  );
  evidence(
    [
      "--as-of", INTEGRATED_AS_OF,
      "--recorded", ...RECORDED_CATALOGUE,
      "--wikimedia-replay", "tests/fixtures/wikimedia",
      "--recorded-through", "2026-10-03",
      "--sample-store", path.join(integratedWork, "samples"),
    ],
    out,
  );
}

const SYNTHETIC = [
  "--as-of", "2026-10-20T12:00:00+00:00",
  "--synthetic-scenario",
  ...["rust", "dota-2", "counter-strike-2", "elden-ring", "no-mans-sky", "minecraft"].flatMap(
    (slug) => ["--game", slug],
  ),
];

function evidence(args, out) {
  mkdirSync(path.dirname(out), { recursive: true });
  execFileSync("uv", ["run", "metaspike", "evidence", ...args, "--opportunities", "--json", "--out", out], {
    cwd: repo,
    stdio: ["ignore", "ignore", "inherit"],
  });
  console.log(`wrote ${path.relative(repo, out)}`);
}

function schema(check) {
  const generated = execFileSync(
    "uv",
    [
      "run",
      "python",
      "-c",
      "import json; from metaspike.opportunity.rules import OpportunityShortlist as S; " +
        "print(json.dumps(S.model_json_schema(mode='serialization'), indent=2, sort_keys=True))",
    ],
    { cwd: repo, encoding: "utf-8" },
  );
  if (check) {
    const current = readFileSync(schemaFile, "utf-8");
    if (current !== generated) {
      console.error("the private opportunity report schema is out of date: run `node scripts/opportunities.mjs schema`");
      process.exit(1);
    }
    console.log("private opportunity report schema is up to date");
    return;
  }
  writeFileSync(schemaFile, generated);
  console.log(`wrote ${path.relative(repo, schemaFile)}`);
}

const [command, ...rest] = process.argv.slice(2);
if (command === "recorded") evidence(RECORDED, path.join(reports, "verified.json"));
else if (command === "integrated") integrated(path.join(reports, "integrated.json"));
else if (command === "synthetic") evidence(SYNTHETIC, path.join(reports, "synthetic.json"));
else if (command === "schema") schema(rest.includes("--check"));
else {
  console.error("usage: opportunities.mjs recorded | integrated | synthetic | schema [--check]");
  process.exit(2);
}
