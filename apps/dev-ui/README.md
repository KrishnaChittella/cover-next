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
| `npm run data:replay` | Backtest and rebuild `<repo>/artifacts` offline, then sync |
| `npm run contract:generate` | Regenerate the schema copy and the TypeScript types |
| `npm run contract:check` | Fail if that copy is out of date |
| `npm run typecheck`, `npm run lint`, `npm test` | The checks, one at a time |
| `npm run check` | All of them, then a production build |

## Creator opportunities (private, local only)

`/opportunities` shows the U6.3 creator-opportunity rubric (`creator_opportunity_rules_v1`)
over a **private** report. It is off by default and never on in a production build.

```
cd apps/dev-ui
npm run opportunities:recorded     # recorded-only report: verified evidence, as of 2026-10-05
npm run opportunities:synthetic    # SYNTHETIC demonstration, as of 2026-10-20
METASPIKE_PRIVATE_OPPORTUNITIES=1 npm run dev
# http://127.0.0.1:3000/opportunities            verified evidence only
# http://127.0.0.1:3000/opportunities?view=synthetic
```

Both generators run `uv run metaspike evidence ... --opportunities --json --out ...` offline
and write to `<repo>/.metaspike/private/opportunities/{verified,synthetic}.json`, which is
git-ignored and not under any served directory.

### Creator decision brief

Each game's page (`/opportunities/<slug>`) opens with a **creator decision brief**. It
restates the engine's assessment in plain words:

- whether to act: candidate, watchlist, or abstain;
- the rubric's conditions as met or not met;
- what changed and why it is timely;
- the angle, creator supply and community;
- counterevidence, freshness, what is not known, and what to confirm first.

The brief comes with a **Copy brief as text** button and a plain-text view.
It is built server-side from the validated report (`src/private/opportunities/brief.ts`);
the copy button receives only the finished text.

- **Nothing is computed in the interface.** Readiness, facts and order are the engine's.
  Missing evidence stays missing, and an empty or partial creator sample is never read as
  low competition.
- **The synthetic view's brief is simulated.** It opens and closes, on screen and in
  copied text, with "SYNTHETIC DEMONSTRATION — NOT A REAL RECOMMENDATION", and labels
  every invented or unverified item.

```
npm run opportunities:recorded && npm run opportunities:synthetic
METASPIKE_PRIVATE_OPPORTUNITIES=1 npm run dev
# http://127.0.0.1:3000/opportunities/minecraft              verified: abstains
# http://127.0.0.1:3000/opportunities/dota-2?view=synthetic  SIMULATED candidate
```

**How it is loaded.** The pages are server components rendered on request
(`force-dynamic`), never at build time. `src/private/opportunities/loader.ts` reads one of
two fixed files chosen by a fixed view name; only the exact word `synthetic` selects the
synthetic file, and nothing a browser sends becomes part of a path. An absolute directory
may be configured in the server environment as `METASPIKE_OPPORTUNITY_DIR`; one inside
`public/` or `artifacts/` is refused. The read is excluded from build tracing, so no report
can be traced or bundled into build output. No client component imports the reader.

**Enabling.** `METASPIKE_PRIVATE_OPPORTUNITIES=1` with the dev server. In `next build` and
`next start` (`NODE_ENV=production`) the page never reads a report and says so.

**Validation.** A report is rendered only if its `rules_version` is
`creator_opportunity_rules_v1` (otherwise: unsupported version), it validates against
`src/private/opportunities/report.schema.json` (generated from the engine's Python model;
`npm run opportunities:schema` regenerates it, `opportunities:schema:check` and a backend
test check it), its distribution is private, and its two orders list every assessed game in
order. Anything else is refused and nothing from it is shown. An absent report says how to
generate one.

**Verified and synthetic.** The default view shows **verified readiness only**. When no
verified evidence establishes anything, it says so: "No real creator opportunity is
established by the currently verified evidence." The synthetic view carries the banner
"SYNTHETIC DEMONSTRATION — NOT A REAL RECOMMENDATION", keeps the verified table, and adds a
separate simulated table whose every readiness is prefixed `SIMULATED:`. Simulated readiness
is never shown as a game's verified status.

**Order and explanations** are the engine's own: the UI renders the positions and the
"above the next because" sentence it was given, computes no score and re-sorts nothing. A
tie broken only by canonical id is shown as `=` and as "Indistinguishable on the evidence —
ordered by id only, which means nothing".

**What it does not show**, because it does not exist: a validated opportunity, a
probability, a percentage, a market total, a verified creator or community source. The
rubric's thresholds are unvalidated and the page says so.

## Pages

| Route | Artifact | Shows |
|---|---|---|
| `/` | `current.json`, `source-health.json` | Scope notice, run snapshot, leaderboard with search, sort and filters, unranked games with reasons, what exists today |
| `/games` | `current.json` | Every game in the run, searchable |
| `/games/[slug]` | `games/{slug}.json` | Rank, score, components, confidence and its factors, why-now reasons, event intelligence, history chart, features, coverage |
| `/opportunities` | private report (see above) | The creator-opportunity shortlist, verified view by default; `?view=synthetic` for the labelled demonstration |
| `/opportunities/[slug]` | private report | One game: why, why now, what changed, events, angles, creator coverage, discussion, unknowns, freshness and provenance, why it might be wrong |
| `/radar` | `radar.json` | Dated events that have not started, by window. Timing and context only |
| `/validation` | `validation.json` | A walk-forward backtest of score v0: evidence status first, then what was tested, score v0 beside simple alternatives, the look-ahead checks, limitations, and any past day's ranking with what followed |
| `/sources` | `source-health.json` | What each source returned in this run, and each event source: stage clearance, checks, posts observed, candidates classified, promoted and not promoted. Counts only, never a post |
| `/methodology` | `methodology.json` | Score and confidence terms, the Event Heat rules, feature definitions, source policy, limitations |

## Events

A game page shows its event intelligence: Event Heat, the strongest event and its phase,
every relevant event with its timing, strength and contribution, and the sentence that
explains it. `/radar` lists upcoming events by window.

Both have an **unavailable** state, and with today's data that is what you will see: no
event source is collected, so the offline replay has no event coverage. Unavailable
shows the word and the reason and no number. It is never shown as 0, which would mean
a source looked and found nothing. The interface refuses a game artifact that carries a
heat on unavailable intelligence.

The artifact keeps Event Heat exactly as computed. A heat that is positive but below
one decimal place is shown as `<0.1`, and a positive strength or contribution below two
places as `<0.01`: a positive value is never displayed as zero.

Event Heat is internal and uncalibrated, and is not part of score v0. No event is ever
invented for the developer UI: the synthetic events in `src/test/synthetic-events.ts`
exist only for tests.

## Validation

`/validation` shows a historical backtest: for each past day, what score v0 would have
ranked at 00:00 UTC using only the days its source had published by then, and what
each game's attention did over the three whole days that followed, against the
baseline known at that moment. `npm run data:replay` runs it offline over the recorded
history before syncing.

The page states the timing outright: when each recommendation was simulated, the
newest demand day it could see (two days back for a source with a one-day publication
lag), and the days it is judged on. A compact provenance section gives the hashes of
the exact snapshots and labels the report summarises.

Read the evidence status first. With the recorded fixture (six games with history) it
is `framework_only`: the machinery works and does not look ahead; the sample says
nothing about whether the score predicts demand. The page shows results as they came
out, including that plain lift orders those six games at least as well as score v0. If
no backtest is in the bundle the page says "not run" and shows no number. The
interface refuses a report whose status its own checks do not support.

## Private-only bundles

If a bundle holds event data derived from a source that is cleared for private
development and not for public launch (today: Steam News), its manifest says
`distribution: "private_development_only"` and every page shows a notice naming the
source. Such a bundle is for this local interface and must never be published. The
interface refuses a bundle that carries such data without the marking.

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
  versions/{run_id}/radar.json
  versions/{run_id}/validation.json
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

`npm test` runs 179 tests. They cover what would mislead silently: manifest and path
resolution, the base URL, rejection of schema-invalid and mismatched data, rank order,
search and filters, null features (absent, never zero), unranked reasons, score and
confidence formatting, history gaps staying gaps, event intelligence in its
available, zero and unavailable states, and validation in its real, bad-result and
not-run states. The fixture under
`src/test/fixtures/bundle` is an unmodified replay output; nothing in it is hand
written.

## Not here, on purpose

Final opportunity scoring, event detection, Steam or IGDB data, creator-supply gap,
R2 or any CDN, public pages, SEO, share cards, accounts, and anything on
krishnachittella.dev.
