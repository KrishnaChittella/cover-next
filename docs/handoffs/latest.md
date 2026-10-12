# Handoff: latest checkpoint (product branch)

This file is the short-lived handoff from Claude Code to ChatGPT. **This copy lives only
on the branch `product/local-ui`.** The handoff on `main` is the C2-F soak's and was not
touched. It supports communication only and replaces no code, test or document.

Production baseline: **PHASE 4 PART C2-F: SEVEN-DAY SOAK RUNNING** (observed read-only
at 03:20 UTC on 2026-10-12; see the last section. Not altered. Not judged.)

Parallel checkpoint: **PRODUCT BUILD U5.1: CATALOGUE CORRECTNESS CLOSED — CHATGPT REVIEW REQUIRED**

Offline only. No source was called. Correctness and policy-boundary fixes; no
performance work, no identity or product intelligence changed.

Details: `docs/catalogue-expansion.md`.

## The original issues

**A. Scope comparison.** `evaluate_broad_catalogue` took one `previous: CatalogueState`,
in practice the earlier broad one, and gave it to the narrow pass as its history too. On
a repeat, `entities_in_production_scope` counted broad-only entities and
`entities_admitted_by_broad` went to 0. One field had silently changed from "how the two
scopes differ now" to "what changed since last time".

**B. Local generator.** `generate-catalogue` wrote a local catalogue state from recorded
external records with no policy or stage check. While closing it I found a second path:
the `sample` command generates and writes the same state file when none exists, through
`_load_or_generate_state`. Neither was a production mutation.

## The corrections

### A. Two catalogues, two histories

- `previous` is now `BroadEvaluation | None`: the earlier *evaluation*, carrying both
  catalogues. A bare `CatalogueState` raises `TypeError`.
- The narrow catalogue continues from the previous **narrow** one; the broad from the
  previous **broad** one. The first evaluation builds narrow, then broad on top of it,
  exactly as before, so first-evaluation results and work counts are unchanged.
- `BroadEvaluation` gained `record_keys`, the records it has been given.
- **Scope difference** (a property of the current state):
  `entities_in_production_scope` (narrow), `canonical_entities` (broad),
  `entities_only_in_broad_scope` (renamed from `entities_admitted_by_broad`). The last
  is counted **by records, not ids**: a broad entity is within the narrow scope if it is
  a bootstrap entity or holds a record the narrow scope reaches.
- **Since previous** (new, separately named): `continues_previous`,
  `narrow_entities_admitted_since_previous`, `broad_entities_admitted_since_previous`,
  `records_new_since_previous`, `quarantines_released_since_previous`. On a first
  evaluation these are `null`, not zero.
- One shared engine, `generate_catalogue_blocked`. No second resolver.

### First versus repeated evaluation, 394 recorded records

| | First | Identical repeat, U5 (wrong) | Identical repeat, U5.1 |
|---|---|---|---|
| Narrow entities | 15 | **234** | 15 |
| Broad entities | 234 | 234 | 234 |
| Only in broad scope | 219 | **0** | 219 |
| Broad admitted since previous | – | – | 0 |
| Narrow admitted since previous | – | – | 0 |
| Resolver calls | 9 + 380 | | 0 + 0 |

The U5 column was reproduced by running U5's module on the same repeat.

A later update, synthetic records added to the recorded evaluation: one linked to a
bootstrap entity, one linked to nothing. Narrow 16, broad 236, only-in-broad 220;
since previous: narrow 1, broad 2, records 2.

**One thing to know.** An entity that both catalogues create after the first evaluation
gets a different canonical id in each, because each history issues its own ids. Existing
identities are stable within each catalogue, bootstrap ids are identical in both, and
nothing consumes an id across the two. Aligning them would need the broad pass to adopt
the narrow pass's new entities, which I judged too fragile to do by replaying ids. It
is documented; say if you want it closed differently.

### Identity and quarantine invariants (tested)

On an identical repeat, for **both** catalogues: canonical ids, bindings, decisions,
quarantine and related products are equal to the first evaluation's, three times
running. On an update: earlier ids, bindings and decisions are a prefix of the later
ones. A released quarantine is one appended decision and one appended binding; the
earlier binding is not rewritten; a point-in-time read before the evidence still shows
the record unbound; the summary's counts equal the final state's. Cross-checked against
all-pairs while continuing: no difference.

### B. Local generator policy

- `generate-catalogue` now **requires `--stage`**, with no default. Before anything is
  resolved or written it calls `require_catalogue_sources`, the same gate the
  evaluation uses, with the existing `SourcePolicy` definitions for
  `CATALOGUE_METADATA`. With the recorded fixtures: private development proceeds;
  private collection, public launch and commercial production are refused (Steam's
  catalogue clears none of them), and nothing is written.
- `sample` passes `PRIVATE_DEVELOPMENT` explicitly in its handler, the only stage its
  own policy clears, and its local state goes through the same gate whether read or
  generated. `_load_or_generate_state` takes `stage` with no default.
- A written state now has a companion `<name>.provenance.json`:
  `kind: local_offline_catalogue_state`, `is_production_catalogue: false`, the stage,
  and each source with its record count and cleared stages. The command prints the
  same.
- No source-name exception, no hidden lookup: the command hands the mapping in.
- Production's path is untouched: `advance_catalogue`, `CatalogueRepository`, the scope
  gate and `CURRENT` have no diff.

Stated as it is: `audit-bootstrap-bindings` is a read-only identity audit. It resolves
in memory, prints to the terminal and persists nothing; it is **not** stage-gated.
`ingest-events` and `probe-sources steam-players` use catalogue records in memory to
resolve app ids and write no catalogue state; that use is not stage-gated either. A
test pins that the audit writes nothing. Whether read-only uses should name a stage is
open.

### `as_of` semantics

`as_of` is the **time of the evaluation**. It stamps `decided_at` and `valid_from`. It
is **not** a historical visibility cutoff: it hides no record, the counts do not depend
on it, and no point-in-time reconstruction is claimed.

What was wrong: nothing stopped an `as_of` earlier than the records' own `fetched_at`,
which dates a binding before its evidence existed, and the mapping history is read
point-in-time. Now:

- a record fetched after `as_of` is refused (evaluation and `generate-catalogue`);
- an `as_of` earlier than the evaluation being continued is refused;
- later evidence appends, and an earlier point-in-time read is unchanged.

No collection time was invented. The recorded fixtures carry one fetch time for the
whole file (2026-10-05 02:00 UTC).

**Effect on recorded tests, reported explicitly.** U5's run and tests were stamped
2026-10-05 00:00 UTC, two hours before that fetch time. That stamp is now refused, so
the tests and the documented command moved to 2026-10-06. Every count is identical;
only the timestamps on decisions and bindings differ. The core generators
(`generate_catalogue`, `generate_catalogue_blocked`) were not changed and still accept
any `as_of`; the check is in the evaluation and the local generator command.

## Tests added or changed

Added (6): the repeat regression (A, B); a later update per scope (C); a released
quarantine with summary-equals-state and cross-check (D, E); `as_of` semantics; the
local generator's gate and provenance; the audit persisting nothing.

Changed, deliberately:

- `tests/test_catalogue_expansion.py`, `tests/catalogue_scale.py`: `as_of` moved from 5
  to 6 October; `previous=` now an evaluation; the renamed field. Every pinned count is
  unchanged, including the 1,000-record work counts.
- `tests/test_generated_catalogue.py`: `--stage private_development` added; dates moved
  one day. Its pinned "234 entities (219 new this run)" is unchanged.
- `tests/test_steam_players.py`: the expected directory listing now includes the
  provenance file.

No test was weakened. No golden label was touched.

## Frozen U5 recorded-data results (unchanged, pinned in tests)

394 records; narrow 15 entities; broad 234; 219 only in broad; 327 bound; 44
quarantined; 23 related products. Wikimedia identifier-ready 14; Steam identifier-ready
90; without any eligible source 135.

The U5 scale measurements stand as recorded and were not re-measured or replaced. One
slow case (5,000 records) was **run as a test** in this checkpoint and passed with the
same counts as the U5 measurement; that is a test execution, not a new benchmark.

## Remaining scale bottlenecks (not addressed)

Unchanged from U5 and not touched here: the full-scan fallback in blocking, which makes
resolving quadratic; the quadratic `steam_eligibility` scan behind the coverage
summary; a whole-state snapshot per generation; and an unmeasured first broad build
against the production job's one-hour, 2 GiB limits. Quadratic performance has **not**
been fixed.

## Gates

Backend: `ruff format --check` clean (219 files); `ruff check` clean; `mypy` clean (206
files); `pytest` **1,484 passed, 8 deselected** (the default `slow` and `live`
exclusions); `export-schemas --check` up to date; `evaluate-resolver` exit 0: golden
set v3, 200 cases, 0 false automatic merges, precision 52/52.

Frontend (no frontend change): `contract:check`, `typecheck`, `lint`, `test` (179
passed), `build` all pass.

## Unchanged

`resolution/` (`883d0775ac898ab1`), `catalogue/generate.py`, `incremental.py`,
`persistence.py`, `eligibility.py`, `coverage.py`, `lifecycle/`, `domain/`: no diff.
`scoring/` `3b857c551d5ab5b0`, `features/` `24cf7d170f57d8ab`, `events/`
`333373c61ae6c37f`, `event_sources/`, `validation/`: no diff. Golden set file
byte-identical. Artifact contract 1.5.0; `schemas/` and `apps/` no diff. Score v0
golden replay bit-for-bit. Broad production discovery still `False`.

## Production isolation (read-only, 03:20 UTC 2026-10-12)

Read from GCP and GitHub:

- `main` = `cb33198`, level with origin, clean; `product/local-ui` not merged; no pull
  request.
- `deploy.yml` runs on push to `main` and manual dispatch; `ci.yml` on pull requests,
  calls and manual dispatch. A push to `product/local-ui` starts nothing. Last workflow
  run still 38104941975 (11 October).
- Terraform state generation `1791685217422653`: unchanged.
- Control record generation `1791684706340016`: unchanged.
- Scheduler configuration ENABLED, `0 * 12-18 10 *`: unchanged.
- Seven jobs, generation 6, image `sha256:a8680b2be8b5…`: unchanged.
- IAM, dataset schemas, secrets, alert policies: identical to the previous snapshot.
  0 secret versions, 0 user keys. Repository variables unchanged.
- No Wikimedia, Steam or IGDB call by U5.1. Local Steam quota ledger still 3.
- The portfolio repository was not touched.

## C2-F status (read-only; separate from U5.1; not altered; not judged)

| Time (UTC, 12 Oct) | Execution | Result |
|---|---|---|
| 00:00 | `ms-dispatch-5lc87` | succeeded |
| 01:00 | `ms-dispatch-xv92w` | succeeded |
| 02:00 | `ms-dispatch-zbb72` | succeeded |
| 03:00 | `ms-dispatch-9vhnx` | succeeded |
| 03:02 | `ms-wikimedia-gqf4b` | succeeded |

Five executions since the window opened; total 40. The Wikimedia collector ran after
the 03:00 tick, as the launch plan expects for the Monday reconciliation. The warehouse
run expected at 04:00 had not happened yet, and warehouse row counts are as they were.
Scheduler last attempt 03:00:04Z, next 04:00Z.

This is what was observable at one moment. It is **not** a statement that C2-F has
passed; that is judged after the window, against its criteria. Nothing was triggered,
restarted or changed.

## Commits

`ea1ff17` fix: correct catalogue comparison and policy boundaries; then this handoff.
Pushed to `origin/product/local-ui` only. No PR, no merge to `main`.

## Exact next approval required

ChatGPT review of U5.1.

STOP. U5.2 has not been started.
