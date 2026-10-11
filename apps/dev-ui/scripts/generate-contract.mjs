// Copies the checked-in JSON Schemas into the app and generates TypeScript types from
// them. The schemas in <repo>/schemas are exported from the Python contract and are the
// only authority; nothing here is written by hand.
//
//   node scripts/generate-contract.mjs           write src/contract/{schemas,types}
//   node scripts/generate-contract.mjs --check   fail if what is committed is out of date
import { readFile, writeFile, mkdir, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { compile } from "json-schema-to-typescript";

const app = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = path.resolve(app, "..", "..", "schemas");
const schemasOut = path.join(app, "src", "contract", "schemas");
const typesOut = path.join(app, "src", "contract", "types");
const check = process.argv.includes("--check");

const banner = [
  "/* eslint-disable */",
  "// GENERATED from <repo>/schemas by `npm run contract:generate`. Do not edit.",
  "// The Python artifact contract (src/metaspike/publishing/artifacts.py) is the source.",
].join("\n");

// Two rewrites, for type generation only; the copy used for runtime validation is
// byte-identical to the checked-in schema.
//  - The schemas are draft 2020-12, where a fixed-length array is `prefixItems`. The
//    type generator reads the older spelling.
//  - Every property carries a `title`, which the generator would turn into a named type
//    (and into a clash such as a `SourceCoverage` list beside the `SourceCoverage`
//    model). Titles are kept only where they name a model: the root and `$defs`.
function forTypes(node, keepTitle = true) {
  if (Array.isArray(node)) return node.map((item) => forTypes(item, false));
  if (node === null || typeof node !== "object") return node;
  const out = {};
  for (const [key, value] of Object.entries(node)) {
    if (key === "title" && !keepTitle) continue;
    if (key === "$defs") {
      out[key] = Object.fromEntries(Object.entries(value).map(([name, model]) => [name, forTypes(model, true)]));
    } else if (key === "properties") {
      // A property NAMED "title" is a property, not a schema title.
      out[key] = Object.fromEntries(Object.entries(value).map(([name, model]) => [name, forTypes(model, false)]));
    } else {
      out[key === "prefixItems" ? "items" : key] = forTypes(value, false);
    }
  }
  return out;
}

const names = (await readdir(source)).filter((name) => name.endsWith(".schema.json")).sort();
if (names.length === 0) throw new Error(`no schemas found in ${source}`);

const outputs = new Map();
for (const name of names) {
  const text = await readFile(path.join(source, name), "utf8");
  const schema = JSON.parse(text);
  outputs.set(path.join(schemasOut, name), text);
  const types = await compile(forTypes(schema), schema.title, {
    bannerComment: banner,
    additionalProperties: false,
    style: { printWidth: 100 },
  });
  outputs.set(path.join(typesOut, name.replace(".schema.json", ".ts")), types);
}

let stale = 0;
for (const [file, text] of outputs) {
  if (check) {
    const current = await readFile(file, "utf8").catch(() => null);
    if (current !== text) {
      stale += 1;
      console.error(`out of date: ${path.relative(app, file)}`);
    }
  } else {
    await mkdir(path.dirname(file), { recursive: true });
    await writeFile(file, text);
    console.log(`wrote ${path.relative(app, file)}`);
  }
}
if (check && stale > 0) {
  console.error("The contract copy does not match <repo>/schemas. Run: npm run contract:generate");
  process.exit(1);
}
if (check) console.log(`contract copy is current (${names.length} schemas)`);
