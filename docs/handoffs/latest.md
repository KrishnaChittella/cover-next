# Handoff: latest checkpoint (product branch)

This file is the short-lived handoff from Claude Code to ChatGPT. **This copy lives only
on the branch `product/local-ui`.** The handoff on `main` is the C2-F soak's and was not
touched. It supports communication only and replaces no code, test or document.

Production baseline: **PHASE 4 PART C2-F: SEVEN-DAY SOAK RUNNING** (observed read-only
at 00:30 UTC on 2026-10-13; see the last section. Not altered. Not judged.)

Parallel checkpoint: **PRODUCT BUILD U5.1.2: SAMPLER CATALOGUE REUSE CONSISTENCY CLOSED — CHATGPT REVIEW REQUIRED**

Offline only. No source was called. One small correctness fix.

## The inconsistency

`generate-catalogue` required every earlier record when continuing a local catalogue
state. The other path that reuses a local state, `_load_or_generate_state`, used by the
`sample` command, did not. It validated provenance and policy, returned the state, and
`_sample` then called `steam_eligibility(state, records)`: bindings made from the
complete records, read beside whatever smaller set was supplied.

Reproduced against U5.1.1: the sampler given the state plus IGDB-only records exited 0
and went on to sample.

Local and offline only. Nothing suggests C2-F or any artifact was affected.

## The fix

One call in the loader, and one handler for its error:

- `_load_or_generate_state`, when it loads an existing state, now calls
  `require_complete(records, known_record_keys(state))` after the provenance check and
  the policy check. These are the same two functions `generate-catalogue` uses; there
  is one definition of completeness.
- `_sample` catches `IncompleteRecords` and returns `EXIT_INVALID_CATALOGUE` with
  `catalogue state cannot be reused: N record(s) the state being continued was made
  from are not among the records supplied (… from igdb, … from steam_catalogue)`.

### Affected code paths

`src/metaspike/cli.py` only: `_load_or_generate_state` and `_sample`. Net 13 lines
added, 1 removed. `_load_local_state`, `_generate_catalogue`, `_write_local_state` and
everything under `catalogue/` are unchanged.

### Refusal semantics

Order in the loader: policy for the supplied records; provenance (exists, readable,
digest, declared sources agree); policy for retained sources; completeness. Policy
therefore still wins over completeness: the same state at private collection is refused
as "not cleared" with complete records or with none.

An incomplete set is an expected refusal: exit code 2 (`EXIT_INVALID_CATALOGUE`), a
plain message on stderr, nothing on stdout, no traceback and no "unexpected failure".

### Proof that nothing happens on incomplete evidence

The test replaces the replay client factory with a counter. On each of four refusals
(IGDB only; Steam only; nothing; one fixture directory of two):

- the client factory is **not called**, so no request can be sent;
- stdout is empty: no target was chosen or reported;
- every file under the working directory is byte-identical before and after: no
  sample, and state and provenance unchanged.

The test also asserts, from the source, that the loader runs before
`steam_eligibility` and before the client is opened.

### Existing sampling replay behaviour

Unchanged. With the complete recorded inputs the sampler reuses the state across four
slots in the test: 8 targets each time, state and provenance never rewritten. The
pre-existing sampler tests pass unmodified apart from the U5.1 directory listing.

### Provenance and stage-policy regression

Still refused through the sampler, with nothing touched: no provenance file; a
provenance declaring fewer sources than the state retains; a wrong digest. Still
refused at private collection whatever records are supplied. All of U5.1.1's tests
pass.

## Tests

One new test, `test_the_sampler_reuses_a_state_only_with_the_evidence_it_was_made_from`
in `tests/test_steam_players.py`, covering A to I. It **fails against U5.1.1** (the
IGDB-only reuse returns 0) and passes now. It uses the recorded fixtures; the partial
sets are those same recorded rows with one source's rows left out. No synthetic record.

J and K are covered by tests that already existed and still pass.

## Frozen recorded catalogue counts (unchanged, pinned)

394 source records; 15 narrow entities; 234 broad; 219 broad-only; 327 bound; 44
quarantined; 23 related products.

## Gates

Backend: `ruff format --check` clean (219 files); `ruff check` clean; `mypy` clean (206
files); `pytest` **1,489 passed, 8 deselected**; `export-schemas --check` up to date;
`evaluate-resolver` exit 0 (golden set v3, 200 cases, 0 false automatic merges,
precision 52/52).

The 8 deselected are the default `slow` and `live` exclusions. **No slow test and no
benchmark was run.**

Frontend (no frontend change): `contract:check`, `typecheck`, `lint`, `test` (179
passed), `build` all pass.

## Existing intelligence unchanged

No diff under `catalogue/`, `resolution/` (`883d0775ac898ab1`), `domain/`, `sources/`,
`pipeline/`, `lifecycle/`, `scoring/` (`3b857c551d5ab5b0`), `features/`
(`24cf7d170f57d8ab`), `events/` (`333373c61ae6c37f`), `event_sources/`, `validation/`.
Steam eligibility rules, source bindings and identities: untouched. Source permissions:
untouched. Golden set byte-identical. Score v0 golden replay, U3.1 publication-lag and
U4.2 provenance tests pass. Artifact contract 1.5.0; `schemas/` and `apps/` no diff.
Broad production discovery still `False`; production's catalogue path and `CURRENT`
untouched.

## Remaining catalogue scale bottlenecks

Not touched: the full-scan fallback that makes resolving quadratic; the quadratic
`steam_eligibility` scan; whole-state snapshots per generation; an unmeasured first
broad build against the production job's limits. Nothing was optimised.

## Production branch isolation (read-only, 00:30 UTC 2026-10-13)

Read from GCP and GitHub:

- `main` = `cb33198`, level with origin, clean; `product/local-ui` not merged; no pull
  request; a push to it starts no workflow; last workflow run still 38104941975.
- Terraform state generation `1791685217422653`: unchanged.
- Control record generation `1791684706340016`: unchanged.
- Scheduler configuration ENABLED, `0 * 12-18 10 *`: unchanged.
- Seven jobs, generation 6, image `sha256:a8680b2be8b5…`: unchanged.
- IAM, dataset schemas, secrets, alert policies: identical to the previous snapshot.
  0 secret versions, 0 user keys. Repository variables unchanged.
- No Wikimedia, Steam or IGDB call by U5.1.2. Local Steam quota ledger still 3.
- The portfolio repository was not touched.

## C2-F observation (read-only, 00:30 UTC 2026-10-13; separate; not altered; not judged)

| | |
|---|---|
| Executions since the window opened | 27, none failed |
| 12 October | 24 dispatcher, 1 Wikimedia collector, 1 warehouse |
| 13 October so far | 1 dispatcher (00:00) |
| Scheduler | last attempt 00:00:48Z; next 01:00Z |
| Warehouse rows | `collection_runs` 68, `expected_targets` 42, `period_observations` 1,568 (unchanged since the 00:04 reading) |

Read from Cloud Run, Cloud Scheduler and BigQuery, not from GitHub. One day into a
seven-day window. This is not a statement that C2-F has passed. Nothing was triggered,
restarted or changed.

## Commits

The fix commit "fix: enforce complete catalogue evidence on sampler reuse", then this
handoff. Pushed to `origin/product/local-ui` only. No PR, no merge to `main`, no
deployment.

## Exact next approval required

ChatGPT review of U5.1.2.

STOP. U5.2 has not been started.
