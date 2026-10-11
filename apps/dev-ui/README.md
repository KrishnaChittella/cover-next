# MetaSpike developer UI

A private, local interface over MetaSpike's published artifacts. It answers, visually,
"what game has the strongest content opportunity right now, and why?" using only what
the pipeline has already computed.

**Internal development.** It is not a public product, it is not deployed anywhere, and
the dev server listens on 127.0.0.1 only.

**Score v0 is not the final opportunity score.** `metaspike_score_v0` measures relative
attention anomaly and recent momentum from one source (Wikimedia daily pageviews). The
interface says so on every page that shows a score, and shows nothing for the things
that do not exist yet: event heat, multi-source agreement, creator-supply gap, the
final opportunity model.

## Run it

Needs Node 20.9 or newer, and `uv` for the data step.

```
cd apps/dev-ui
npm install
npm run data:replay     # build artifacts OFFLINE from recorded responses, then sync them
npm run dev             # http://localhost:3000
```

If `<repo>/artifacts` already holds a bundle you want to look at, use
`npm run data:sync` in place of `data:replay`. Neither makes a network call to any
source: `data:replay` runs `uv run metaspike run --replay tests/fixtures/wikimedia
--as-of 2026-10-03`, which answers from files.

| Command | What it does |
|---|---|
| `npm run dev` | Development server on 127.0.0.1:3000 |
| `npm run build` / `npm run start` | Production build, and serving it locally |
| `npm run data:sync` | Validate `<repo>/artifacts`, copy the selected version into `public/data` |
| `npm run data:sync -- --from <dir>` | The same, from another artifacts root |
| `npm run data:replay` | Rebuild `<repo>/artifacts` offline, then sync |
| `npm run contract:generate` | Regenerate the schema copy and the TypeScript types |
| `npm run contract:check` | Fail if that copy is out of date |
| `npm run typecheck`, `npm run lint`, `npm test` | The checks, one at a time |
| `npm run check` | All of them, then a production build |

## Pages

| Route | Artifact | Shows |
|---|---|---|
| `/` | `current.json`, `source-health.json` | Scope notice, run snapshot, leaderboard with search, sort and filters, unranked games with reasons, what exists today |
| `/games` | `current.json` | Every game in the run, searchable |
| `/games/[slug]` | `games/{slug}.json` | Rank, score, components, confidence and its factors, why-now reasons, history chart, features, coverage |
| `/sources` | `source-health.json` | What each source returned in this run |
| `/methodology` | `methodology.json` | Score and confidence terms, feature definitions, source policy, limitations |

## Where the data comes from

One base URL, `NEXT_PUBLIC_METASPIKE_DATA_URL`, default `/data` (see `.env.example`).
Under it the interface reads `manifest.json`, then exactly the files the manifest
names, at the manifest's own version paths:

```
public/data/
  manifest.json                      the pointer
  versions/{run_id}/current.json     what the manifest selects
  versions/{run_id}/source-health.json
  versions/{run_id}/methodology.json
  versions/{run_id}/games/{slug}.json
```

That is the published layout, unchanged. Pointing the variable at a CDN or an object
store that serves the same layout needs no code change. `public/data` is a local copy
and is not committed.

The interface computes no score, no rank and no feature. It sorts and filters what the
leaderboard already contains.

## How the contract is enforced

The JSON Schemas in `<repo>/schemas` are exported from the Python contract
(`src/metaspike/publishing/artifacts.py`) and are the only authority.

- `npm run contract:generate` copies them byte for byte into `src/contract/schemas`
  and generates `src/contract/types` from them. No artifact type is written by hand.
- At run time every file is validated against its schema before anything is rendered
  (`src/contract/validate.ts`). A file that fails is not rendered at all: the page
  shows which file, and the first violations with their paths.
- A file from a different run than the manifest selects, a contract version this
  build was not generated for, or a manifest path that leaves the data root, is
  refused the same way.
- `npm run contract:check` fails when the committed copy no longer matches
  `<repo>/schemas`, so a contract change cannot be missed.

If the Python contract changes: `uv run metaspike export-schemas`, then
`npm run contract:generate`, then fix what the type checker reports.

## Layout

```
scripts/        generate-contract.mjs, sync-data.mjs
src/contract/   schema copy, generated types, runtime validation
src/lib/data/   base URL, manifest-following client, loading hook
src/lib/        formatting, leaderboard search/sort, history gaps, coverage
src/components/ the pages' parts
src/app/        routes
src/test/       one real replay bundle and the stand-in fetch that serves it
```

## Tests

`npm test` runs 111 tests. They cover what would mislead silently: manifest and path
resolution, the base URL, rejection of schema-invalid and mismatched data, rank order,
search and filters, null features (absent, never zero), unranked reasons, score and
confidence formatting, and history gaps staying gaps. The fixture under
`src/test/fixtures/bundle` is an unmodified replay output; nothing in it is hand
written.

## Not here, on purpose

Final opportunity scoring, event detection, Steam or IGDB data, creator-supply gap,
R2 or any CDN, public pages, SEO, share cards, accounts, and anything on
krishnachittella.dev.
