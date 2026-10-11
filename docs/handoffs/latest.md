# Handoff: latest checkpoint (product branch)

This file is the short-lived handoff from Claude Code to ChatGPT. **This copy lives only
on the branch `product/local-ui`.** The handoff on `main` is the C2-F soak's and was not
touched. It supports communication only and replaces no code, test or document.

Production baseline: **PHASE 4 PART C2-F: ARMED AND SCHEDULED — SEVEN-DAY SOAK PENDING**
(true when U2 finished, at 03:57 UTC on 2026-10-11: the first tick is 2026-10-12T00:00Z
and no scheduler attempt has been made yet)

Parallel checkpoint: **PRODUCT BUILD U2: EVENT INTELLIGENCE COMPLETE — CHATGPT REVIEW REQUIRED**

The source-independent event engine is built, tested, wired into the internal artifacts
and shown in the developer UI. Everything is offline and deterministic. No event source
is collected, so the real replay honestly shows "unavailable" everywhere. **Score v0 is
unchanged**, proven against a snapshot taken before this work.

## Where it is

| | |
|---|---|
| Branch | `product/local-ui` |
| Worktree | `../MetaSpike-product-ui` |
| Base | `cb33198` (the C2-F launch commit) |
| U1 | `d01331f`, `e0bd7e2` |
| U2 commit | `9cf5564869faa08846675a49b44128b621ee3b93`, "feat: add event intelligence foundation" |

## Modules added

Backend, `src/metaspike/events/` (about 1,900 lines with the view mapping):

| File | What it is |
|---|---|
| `model.py` | `EventReport`, `EventObservation`, `EventRecord`, `EventRevision`, `CoverageCheck`, `resolve_game`, `observe` |
| `reconcile.py` | Identity, deduplication and revisions: a pure fold over observations |
| `rules.py` | `event_heat_rules_v1`: every constant, each with a rationale |
| `heat.py` | One event's strength over time, the bounded aggregation, the result model |
| `intelligence.py` | The one place availability is decided |
| `explain.py` | Sentence templates over an evaluated event |
| `radar.py` | Upcoming events by window |
| `store.py` | Local append-only store, point-in-time reads |
| `publishing/event_views.py` | The boundary between the engine and the artifact contract |

Also: `docs/event-intelligence.md` (permanent), `schemas/radar.schema.json`,
`tests/test_event_intelligence.py`, `tests/test_event_artifacts.py`,
`tests/fixtures/score_v0_replay_golden.json`.

Frontend: `src/lib/events.ts`, `src/components/event-panel.tsx`,
`src/components/radar.tsx`, `src/app/radar/page.tsx`, `src/components/events.test.tsx`,
`src/test/synthetic-events.ts`, regenerated `src/contract/`.

## Event model

- **`EventObservation`**: what one source said about one event of one game at one
  moment. Immutable, append-only, identified by a hash of its content. Carries the
  canonical game, source, `observed_at`, type, title, `starts_at`, optional `ends_at`,
  time precision (`day` or `exact`), status (`scheduled`, `cancelled`, `withdrawn`),
  the source-native event id where there is one, and a reference.
- **`EventRecord`**: one real-world event as known at a moment, always derived, never
  stored. Stable `event_id`, current fields, first and last observed, sources, and a
  **revision** per observation saying which rule matched and which fields changed.
- **`CoverageCheck`**: a source looked at a game's events at a moment, whatever it
  found. This is what separates "nothing relevant" from "nobody looked".
- **Resolving the game**: an event attaches only through an exact catalogue binding
  (source, namespace, value) to exactly one canonical game. No title matching. An
  unresolved game is refused, and so is a record for a game outside the catalogue.

## Revisions and deduplication

An observation continues a known event of the same game by the first rule that applies:

1. `same_source_native_id`: same source, same native id. Date, title, end may change.
2. `same_source_type_and_day`: same source, same type, same UTC start day, and not two
   different native ids.
3. `cross_source_same_reference`: another source, same type, exactly the same reference.
4. `cross_source_same_type_day_and_title`: another source, same type, same UTC start
   day, same title after folding case, accents and punctuation. Equality only.

Otherwise it is a new event: **a safe non-merge is preferred to a wrong merge.** The id
comes from the first observation and never changes. Nothing is overwritten; "as of" an
earlier moment the event is as it was then. A duplicate that could not safely be merged
is bounded in the heat (one event per type and start day counts). A source without
native ids that moves a date is seen as a new event; that is a stated limitation.

## `event_heat_rules_v1`

INTERNAL / UNCALIBRATED. Per type: importance, ramp, pre-event peak, default duration,
half-life, active floor, decay window, and a rationale. Importance runs from release
1.00, expansion 0.85, season 0.70, wipe 0.65, major patch and platform release 0.60,
down to minor patch 0.15. Global: scale 100, at most 3 contributing events, coverage
trusted for 14 days, horizon 30 days, radar windows 24, 168 and 720 hours. The full
table is in `docs/event-intelligence.md` and is published in the methodology artifact.
No function carries a constant of its own; a test reads the engine's source to check.

## Event Heat

One event's strength, 0 to 1: zero before its ramp; rising linearly to its pre-event
peak inside the ramp; 1 at the start; `max(0.5^(days since start / half-life), floor)`
while running; `strength at end × 0.5^(days since end / half-life)` afterwards; zero
once the decay window has passed. It never falls before the start and never rises after
it. An unstated end uses the type's default duration and is flagged as assumed.
Cancelled and withdrawn events have no strength.

Contribution = importance × strength. **Aggregation**, bounded by construction:

1. same type and same UTC start day counts once, at the larger contribution;
2. only the 3 largest contributions count;
3. `heat = 100 × (1 − ∏(1 − c))`.

Heat is never below the largest contribution, reaches 100 only if one contribution is
1, and never falls when an event is added. Forty simultaneous hotfixes give at most
38.6, less than one major patch. Every relevant event is listed with strength,
contribution, whether it counted, its rank, or why not.

`as_of` is always an argument and must carry a time zone; nothing reads a clock. In the
pipeline it is the end of the run's last day, so a replay evaluates the same moment
whenever it is run.

## Availability

| | Published as |
|---|---|
| A source looked recently, nothing relevant | `available`, heat `0` |
| No source looked, or not within 14 days | `unavailable`, heat `null`, reason `no_event_coverage` or `event_coverage_stale` |
| Events known and current | `available`, heat above 0, contributions listed |

Unavailable never becomes a number. Four independent places refuse a heat on
unavailable intelligence: the engine's result model, the artifact view, the artifact
validator, and the developer UI's loader.

## Radar

Upcoming events for games with current coverage, soonest first, each in the shortest
window that holds it (next 24 hours, 7 days, 30 days; a window includes its end; a
started event is not upcoming). Each item has its timing, whether its ramp has begun,
strength, contribution and its game's whole heat as context. It ranks nothing and
recommends nothing. With no coverage anywhere the radar is `unavailable`, which is
distinct from an available radar with no items.

## Artifact contract: 1.0.0 → 1.1.0 (internal)

Additive; no existing field removed or renamed; `metaspike_score_v0` keeps its name.

- `games/{slug}.json`: new required `event_intelligence`. The older `events` list is now
  filled from the same evaluation when available.
- New `radar.json`, named by new `manifest.artifacts.radar`.
- `methodology.json`: new `event_heat` section with the rules in full.
- `current.json`: unchanged. The leaderboard knows nothing about events.

Schemas were regenerated by `metaspike export-schemas`, and the frontend types by
`npm run contract:generate`. Four existing tests pinned the old artifact set (file
counts, `build_bundle`'s parameter list, the version string) and were migrated
deliberately; nothing was weakened.

## UI

- **Game page**: an "Event intelligence" section. Available: Event Heat, the strongest
  event with type and phase, the explanation, and a table of relevant events with
  timing, phase, strength, contribution and whether each counted. Unavailable: the word
  "Unavailable", the reason, and no number.
- **`/radar`** (new, in the navigation): events grouped by window when available; a
  truthful unavailable state otherwise; a distinct empty state.
- **Methodology**: the Event Heat rules rendered from the artifact.
- The "what exists today" panel now says an engine exists but nothing is collected.

## Score v0 is unchanged

- Before any U2 change, every game's rank, score, components, confidence, features,
  explanation, history and coverage from the offline replay were saved to
  `tests/fixtures/score_v0_replay_golden.json` (taken from the bundle published at
  `d01331f`).
- `test_score_v0_is_identical_to_what_was_published_before_event_intelligence` compares
  the pipeline's output today with that file, field for field, for all 15 games.
- `test_events_change_no_score_rank_confidence_feature_or_reason` runs the pipeline with
  and without synthetic events: those fields are identical, and `current.json` is the
  same document apart from the run id.
- `test_the_score_engine_does_not_import_the_event_engine` checks the separation both
  ways in the source.
- The existing v0 golden and reproduction tests still pass untouched.

## Results

Backend: `ruff format --check` clean; `ruff check` clean; `mypy` strict clean (179
files); `pytest` **1,281 passed**, 5 deselected (was 1,199). 82 new tests.

Frontend: `contract:check` current (6 schemas); type check clean; lint clean;
**133 tests** in 9 files (was 111); production build succeeds with `/radar`.

Test coverage against the checkpoint's list: all 15 types configured; stable identity;
repeat dedupe; revisions (date, title, end, cancellation); six non-merge cases;
point-in-time reconstruction; ramp, start, end, decay and expiry boundaries to the
second; unknown end; overlapping events; bounded aggregation; contribution order;
unavailable ≠ zero; deterministic explanations; radar 24 h / 7 d / 30 d boundaries;
lifecycle independence; unresolved game refusal; artifact serialisation and schema;
frontend contract current; game UI available, zero and unavailable; radar populated,
empty and unavailable; score v0 regression. No test contacts a network.

## Real replay behaviour

Offline replay, run `20261011T034743Z-9cf1238c`, contract 1.1.0: all 15 games carry
`availability: unavailable`, `event_heat: null`, reason `no_event_coverage`; the radar
is `unavailable` with 0 games covered and 15 without. In the browser (production build,
headless Chrome) the game page shows "Event Heat: Unavailable" with the reason and no
number, and `/radar` shows "Radar unavailable: nothing is known about upcoming events
yet". Screenshots: `apps/dev-ui/docs/screenshots/radar-unavailable.png` and
`game-events-unavailable.png`. The populated states were not viewed in a browser: they
exist only in tests, by design.

## Synthetic data, and where it stops

Synthetic events exist only in `tests/test_event_intelligence.py`,
`tests/test_event_artifacts.py` and `apps/dev-ui/src/test/synthetic-events.ts`. Each is
named "Synthetic …". A test asserts that the bundle the developer UI serves contains no
such string, and the frontend fixtures are checked against the schema so they are valid
contract instances.

## Limits worth knowing before review

- Every rule value is a prior. Nothing is fitted or backtested.
- There is no way yet to put real events in: no source, and no CLI command for the
  store. The store is exercised by tests and read by the pipeline; that is all.
- Importance is per type, not per game.
- Stale coverage makes a game unavailable even when a future event is on record. That
  is deliberately conservative and may be too strict once a source exists.
- References are stored and not published, pending each source's policy.
- No cloud resource, table or schema was created; the warehouse mapping is documented
  only.

## Production isolation

Read at 03:57 UTC on 2026-10-11, after all U2 work:

- `main` and `origin/main` at `cb33198`; the original worktree clean and on `main`.
- Prod Terraform state serial 14, 94 resources.
- Scheduler ENABLED, `0 * 12-18 10 *`, `Etc/UTC`, next run 2026-10-12T00:00:00Z, no
  attempt yet. C2-F has not begun, so there is no soak execution to tell apart from
  product work: executions are still 35 and the last is still the catch-up.
- Control record byte-identical to the launch record, generation `1791684706340016`.
- All seven jobs on `sha256:a8680b2b…c754`. Registry 11 images.
- No IAM, schema, secret, alert or scheduler difference from the launch audit. 0 secret
  versions, 0 keys. Repository variables unchanged.
- No workflow run since the launch commit's own push.
- No Wikimedia, Steam or IGDB call. The only pipeline runs were offline replays; the
  only network use was a headless browser pointed at 127.0.0.1.
- Nothing under `.github/`, `infra/`, `warehouse/`, the Dockerfile or the lockfiles
  changed. krishnachittella.dev was not modified. Nothing was deployed or published.

## Branch push

The workflow files are unchanged on this branch, so the U1 finding stands: `deploy.yml`
runs on pushes to `main` only and its cloud jobs require the `main` ref; `ci.yml` has
no push trigger. **`product/local-ui` was pushed**; no workflow run followed. No pull
request was opened and nothing was merged.

## Exact next approval required

Review of U2. U3 has not been started.

STOP.
