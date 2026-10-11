# Handoff: latest checkpoint (product branch)

This file is the short-lived handoff from Claude Code to ChatGPT. **This copy lives only
on the branch `product/local-ui`.** The handoff on `main` is the C2-F soak's and was not
touched. It supports communication only and replaces no code, test or document.

Production baseline: **PHASE 4 PART C2-F: ARMED AND SCHEDULED — SEVEN-DAY SOAK PENDING**

Parallel checkpoint: **PRODUCT BUILD U1: LOCAL UI COMPLETE — CHATGPT REVIEW REQUIRED**

The first private, local MetaSpike interface is built, tested and running against real
artifacts from the offline replay pipeline. Production, `main` and the C2-F soak were
not touched in any way.

## Where it is

| | |
|---|---|
| Branch | `product/local-ui` |
| Worktree | `../MetaSpike-product-ui` (sibling of the original, which stays on `main`) |
| Base commit | `cb33198e2510965e41dff0f5fe7e75cacb7ee2ca`, the C2-F launch commit |
| Feature commit | `d01331fd89522ce5fc7e481d019cbc6b6de94396`, "feat: add local MetaSpike product UI" |
| App | `apps/dev-ui/` |

## Stack

| | |
|---|---|
| Framework | Next.js 16.4.0, App Router, React 19.3.0 |
| Language | TypeScript 6.0.3, `strict` plus `noUncheckedIndexedAccess` |
| Styling | Tailwind CSS 4.3.3, one dark palette as theme tokens, system fonts only |
| Charts | Apache ECharts 6.1.0, modular import, canvas renderer |
| Validation | Ajv 8.20.0 (JSON Schema 2020-12) with ajv-formats |
| Tests | Vitest 5.0.3, Testing Library, jsdom |
| Lint | ESLint 9.39.5 with eslint-config-next 16.4.0 |
| Package manager | npm 11, `package-lock.json` committed |

TypeScript 6 and ESLint 9 are what the Next 16.4 lint stack resolves to; the newest
majors of each are not yet supported by it. No second backend, no API server, no
external font, no analytics.

## Routes

| Route | Artifact read | Content |
|---|---|---|
| `/` | `current.json`, `source-health.json` | Header and question, INTERNAL DEVELOPMENT badge, scope notice, snapshot strip, leaderboard (search, sort, lifecycle and confidence filters), unranked games with reasons, "what exists today" panel |
| `/games` | `current.json` | Every game in the run, searchable, ranked or not |
| `/games/[slug]` | `games/{slug}.json` only | Name, aliases, relationships with a link to the parent, rank, score and version, confidence with ceiling and factors, why-now summary and coded reasons, the four score components, history chart, feature panel, source coverage, method reference |
| `/sources` | `source-health.json` | Status, last success, expected and latest observed day, staleness, attempted, succeeded, failed, not mapped, failures grouped by error code |
| `/methodology` | `methodology.json` | Score terms, confidence terms, ceiling and bands, feature definitions, metrics, source signals and usage policy, limitations |

Navigation: Overview, Games, Sources, Methodology. No account, settings or portfolio
links.

## Data adapter

- One base URL: `NEXT_PUBLIC_METASPIKE_DATA_URL`, default `/data`.
- The client (`src/lib/data/client.ts`) reads `manifest.json` first and then exactly the
  files the manifest names, at the manifest's own `versions/{run_id}/…` paths. The
  manifest and version-path model is kept as published; nothing is flattened into a
  frontend dataset.
- A manifest path that is absolute, or contains `..`, a scheme, a backslash or a query,
  is refused, so a manifest cannot point the interface elsewhere.
- `npm run data:sync` runs the repository's own `metaspike validate-artifacts`, then
  copies only `manifest.json` and the one version it selects into `public/data`
  (git-ignored). The source artifacts are only read.
- `npm run data:replay` first rebuilds the artifacts with
  `uv run metaspike run --replay tests/fixtures/wikimedia --as-of 2026-10-03`, which
  answers from recorded files. No live source call exists anywhere in the app or its
  scripts.
- A CDN or object store serving the same layout needs only the variable changed.
- **The browser computes no score, rank or feature.** It sorts and filters the
  leaderboard the artifact already holds; the artifact's rank is the default order and
  the tie-break for every other.

## Schema and type safety

- `<repo>/schemas/*.schema.json`, exported from the Python contract, is the authority.
- `npm run contract:generate` copies those files byte for byte into
  `src/contract/schemas` and generates `src/contract/types` from them. No artifact type
  is written by hand.
- Every file is validated against its schema at run time before anything renders. A
  failure renders nothing from that data: the page names the file and lists the first
  violations with their JSON paths.
- Also refused, visibly: an artifact from a different run than the manifest selects; a
  contract version this build was not generated for; a game file whose slug does not
  match.
- `npm run contract:check` fails when the committed copy no longer matches
  `<repo>/schemas`.

## How to run

```
cd apps/dev-ui
npm install
npm run data:replay     # offline; or `npm run data:sync` if artifacts/ already exists
npm run dev             # http://localhost:3000, bound to 127.0.0.1
```

## Data used for the smoke check

Real output of the offline replay, not hand-made: run `20261011T025133Z-a8861c54`,
contract 1.0.0, as of 2026-10-03, **6 ranked of 15 games**. Minecraft 12.2, Grand Theft
Auto VI 2.3, four games at 0.0 (attention at or below baseline), nine unranked (eight
whose recorded responses are not in the replay fixtures, and FiveM, which has no
mapping). Confidence ceiling 60. Source health "degraded", which is what a replay with
six recorded games honestly reports.

It is a thin, unexciting leaderboard. That is the truth of the fixture data and it was
not dressed up.

## Browser smoke check

Production build served locally, loaded in headless Chrome:

| Page | Result |
|---|---|
| `/` | Renders: scope notice, snapshot, 6-row leaderboard, 9 unranked, capability panel |
| `/games/minecraft` | Renders: headline, reasons, component and factor meters, chart drawn on a canvas, features, coverage |
| `/games/fivem` | Renders the unscored state: "Unranked", "Not scored", "Not available", no meters, no chart |
| `/sources` | Renders: Wikimedia pageviews, degraded, 14 attempted, 6 succeeded, 8 failed, 1 not mapped |
| `/methodology` | Renders: v0 distinction, score and confidence terms, feature definitions, policy, 11 limitations |
| `/` at a true 390 px width | Renders as a list of cards with no horizontal overflow |

Screenshots: `apps/dev-ui/docs/screenshots/` (overview, game, unscored game, sources).

Not verified visually: the game page at 390 px (the headless browser would not finish
loading it inside a narrow frame; its layout uses the same responsive grid and
scrolling tables as the pages that were checked). No interactive click-through was
done in a real browser; search, sort and filters are covered by the component tests.

## Tests and checks

`npm run check` passes: contract copy current, type check clean, lint clean,
**111 tests in 8 files**, production build succeeds.

| Area | Covered |
|---|---|
| Manifest and paths | Manifest first, then exactly the versioned files; remote base URL; unsafe paths refused |
| Base URL | Default, trailing slashes, environment variable |
| Schema-invalid data | Out-of-range score, missing field, extra field, wrong type, unknown enum, bad date, null where a number is required, non-JSON body; and the page renders nothing from it |
| Consistency | Different run, unsupported contract version, wrong game file |
| Ranked ordering | Default equals artifact rank; ties keep rank order in both directions; input not mutated |
| Search and filters | Words in any order, diacritics, lifecycle, band, combined, no matches, reset |
| Null features | Each nullable feature shows "not available", never a digit; a real zero stays zero |
| Unranked | Every one listed with its reason; failed collection distinguished from not mapped; no score shown |
| Formatting | Score, confidence, counts, signed percentages, UTC dates |
| History | Null and omitted days become gaps; never zero; never joined; rolling mean not invented |
| Pages | Overview, ranked game, unscored game, sources, methodology against the real bundle |

The Python suite is unchanged and passes in the worktree: 1,199 passed.

## Design

Dark and restrained: near-black canvas, one amber signal colour, hairline borders, a
system sans-serif with tabular figures for every number. Tables where the data is
tabular, not a card per number. Every state has a word and a shape as well as a colour
(band glyphs, status marks, arrows with "up"/"down" for assistive technology). Visible
focus ring, skip link, table captions and scopes, `aria-sort` on sortable headers,
live regions for result counts, a text summary and a data table behind every chart,
fixed chart height and skeletons so loading does not collapse the page, reduced motion
respected.

## Files added

- `apps/dev-ui/` (90 files): configuration, `scripts/`, `src/app`, `src/components`,
  `src/lib`, `src/contract` (schema copy and generated types), `src/test` (one replay
  bundle), `docs/screenshots`, `README.md`, `package-lock.json`.
- Root `README.md`: one short section pointing to the developer UI. No historical
  section was rewritten.
- This handoff.

Nothing under `src/metaspike`, `infra/`, `.github/`, `tests/`, `schemas/`,
`warehouse/`, the Dockerfile or the Python lockfile was changed.

## Intentionally not implemented

Final opportunity scoring; event detection or event heat; multi-source agreement;
creator-supply gap; Steam or IGDB data of any kind; R2, Cloudflare or any CDN; public
pages, SEO, Open Graph cards, sharing; accounts, login, settings, billing; portfolio
integration; deployment of any kind. Where these concepts are named in the interface
they carry an explanation and no number.

## Production was not touched

Read back at 03:18 UTC on 2026-10-11, after the build:

- `main` and `origin/main` both at `cb33198`; no product commit on it; original
  worktree clean and on `main`.
- Prod Terraform state serial 14, 94 resources.
- Scheduler ENABLED, `0 * 12-18 10 *`, `Etc/UTC`, next run 2026-10-12T00:00:00Z, no
  attempt yet.
- Control record byte-identical to the launch record, generation `1791684706340016`.
- 35 executions, the last still the catch-up. All seven jobs on
  `sha256:a8680b2b…c754`.
- No IAM, schema, secret, alert or scheduler difference from the launch audit. 0 secret
  versions, 0 keys. Registry 11 images. Repository variables unchanged.
- No workflow run since the launch commit's own push.
- No Wikimedia, Steam or IGDB call: the only pipeline run was the offline replay, and
  the only network use was `npm install` from the public registry, version lookups
  there, and a headless browser pointed at localhost.
- krishnachittella.dev was not modified. Nothing was published.

## Branch push safety

Proven from the workflow files, which are unchanged on this branch:

- `deploy.yml` runs on `push` to `main` only, and on manual dispatch. Its
  identity-check, plan and apply jobs all require `github.ref == 'refs/heads/main'`,
  and the Workload Identity condition separately pins the `main` ref, so a dispatch
  from this branch cannot obtain a deployment credential.
- `ci.yml` runs on pull requests, when called, and on manual dispatch. It has no `push`
  trigger and holds no cloud credential.

A push of `product/local-ui` therefore starts no workflow at all. **The branch was
pushed** to `origin/product/local-ui`, and no workflow run followed. No pull request
was opened.

## Exact next approval required

Review of U1. U2 has not been started.

STOP.
