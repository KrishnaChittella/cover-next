// Copies the published artifact bundle the UI needs into public/data, after the
// repository's own validator has accepted it. Source artifacts are only read.
//
//   node scripts/sync-data.mjs                 sync from <repo>/artifacts
//   node scripts/sync-data.mjs --from <dir>    sync from another artifacts root
//   node scripts/sync-data.mjs --replay        first rebuild <repo>/artifacts OFFLINE from
//                                              the recorded responses in tests/fixtures
//
// What is copied is exactly the manifest and the one version it selects, at the same
// relative paths. Nothing is flattened, renamed or rewritten, and older versions and the
// top-level convenience copy are left behind. There is no live source call anywhere here:
// --replay passes --replay to the pipeline, which then never opens a network connection.
import { spawnSync } from "node:child_process";
import { cp, mkdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const app = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repo = path.resolve(app, "..", "..");
const args = process.argv.slice(2);
const fromIndex = args.indexOf("--from");
const source = path.resolve(fromIndex >= 0 ? args[fromIndex + 1] ?? "" : path.join(repo, "artifacts"));
const target = path.join(app, "public", "data");
const REPLAY_FIXTURES = "tests/fixtures/wikimedia";
const REPLAY_AS_OF = "2026-10-03";
const REPLAY_FROM = "2026-07-06";

function fail(message) {
  console.error(`data:sync: ${message}`);
  process.exit(1);
}

function metaspike(commandArgs) {
  const result = spawnSync("uv", ["run", "metaspike", ...commandArgs], { cwd: repo, stdio: "inherit" });
  if (result.error) fail(`could not run uv (${result.error.message}). Install uv, or run the command yourself.`);
  return result.status === 0;
}

if (args.includes("--replay")) {
  if (fromIndex >= 0) fail("--replay rebuilds <repo>/artifacts; do not combine it with --from.");
  // The backtest first, also offline, so the bundle carries a validation report for the
  // same recording. It walks the whole recorded range and never contacts a source.
  console.log(`data:sync: backtesting the recording offline (${REPLAY_FROM} to ${REPLAY_AS_OF})`);
  const backtested = metaspike([
    "backtest",
    "--replay",
    REPLAY_FIXTURES,
    "--recorded-through",
    REPLAY_AS_OF,
    "--from",
    REPLAY_FROM,
    "--to",
    REPLAY_AS_OF,
  ]);
  if (!backtested) fail("the offline backtest failed; nothing was synced.");
  console.log(`data:sync: rebuilding artifacts offline from ${REPLAY_FIXTURES} (as of ${REPLAY_AS_OF})`);
  if (!metaspike(["run", "--replay", REPLAY_FIXTURES, "--as-of", REPLAY_AS_OF, "--artifacts-dir", source])) {
    fail("the offline replay failed; nothing was synced.");
  }
}

const exists = await stat(path.join(source, "manifest.json")).then(
  () => true,
  () => false,
);
if (!exists) {
  fail(
    `no manifest.json in ${source}.\n` +
      "  Build artifacts offline first:  npm run data:replay\n" +
      "  or point at an existing bundle: npm run data:sync -- --from <artifacts dir>",
  );
}

// The contract's own validator decides whether the bundle may be used.
if (!metaspike(["validate-artifacts", "--artifacts-dir", source])) {
  fail("the artifacts did not validate; nothing was synced.");
}

const manifestText = await readFile(path.join(source, "manifest.json"), "utf8");
const manifest = JSON.parse(manifestText);
const version = manifest.version_path;
if (typeof version !== "string" || !/^versions\/[A-Za-z0-9_-]+$/.test(version)) {
  fail(`unexpected version_path in the manifest: ${JSON.stringify(version)}`);
}

await rm(target, { recursive: true, force: true });
await mkdir(path.join(target, "versions"), { recursive: true });
await cp(path.join(source, version), path.join(target, version), { recursive: true });
// The manifest is written last, as a publisher would: it is what selects the version.
await writeFile(path.join(target, "manifest.json"), manifestText);

console.log(
  `data:sync: ${manifest.ranked_count} ranked of ${manifest.game_count} games, run ${manifest.run_id}\n` +
    `           ${path.relative(repo, source)} -> ${path.relative(repo, target)}`,
);
