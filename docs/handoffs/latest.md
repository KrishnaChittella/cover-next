# Handoff: latest checkpoint (product branch)

This file is the short-lived handoff from Claude Code to ChatGPT. **This copy lives only
on the branch `product/local-ui`.** The handoff on `main` is the C2-F soak's and was not
touched. It supports communication only and replaces no code, test or document.

Production baseline: **PHASE 4 PART C2-F: SEVEN-DAY SOAK RUNNING** (observed read-only
at 00:04 UTC on 2026-10-13; see the last section. Not altered. Not judged.)

Parallel checkpoint: **PRODUCT BUILD U5.1.1: HISTORICAL CATALOGUE SOURCE POLICY CLOSED — CHATGPT REVIEW REQUIRED**

Offline only. No source was called. No policy permission, identity rule or performance
characteristic was changed.

Details: `docs/catalogue-expansion.md`, section "Source policy follows the state".

## The gap

U5.1's policy gate checked the sources of the records supplied to a call. A state that
is continued still holds what earlier sources put into it. So:

1. generate a local state from IGDB and Steam catalogue records at private development;
2. reuse it at private collection, supplying only IGDB records;
3. the gate sees only IGDB, which clears that stage; Steam's bindings, decisions and
   entities stay in the state; the provenance file is rewritten for the later stage
   without declaring Steam.

The same was possible when continuing a `BroadEvaluation` with a batch that omitted a
source. No production effect; local and offline only.

## The corrections

Four things are kept apart: records supplied now; records processed before; sources
retained in a state; sources authorised at the stated stage.

### Retained-source policy enforcement

- `retained_sources(state)` reads, **from the state itself**, every external source
  that left something in it: bindings (closed ones included), the decision log,
  quarantine and related products. The bootstrap entities are not a source. It mirrors
  what production's repository already reads before a commit.
- `require_catalogue_sources(records, policies, stage, retained=...)` holds supplied
  **and** retained sources to the stated stage for `CATALOGUE_METADATA`. A refusal
  names the source and says it is retained. Absence from the batch excuses nothing.
- Used in `evaluate_broad_catalogue` (retained in both of the previous evaluation's
  catalogues), in `generate-catalogue`, and in the `sample` command's state loader.
- The existing `SourcePolicy` definitions are the only authority. No stage is
  defaulted, no source name is exempt.

### Complete records are required to continue (found while fixing this)

My first version allowed a partial batch at a stage every source clears. Its tests
showed that was wrong: with Steam's records left out, the engine **re-decided**
quarantined records. It reads its records as all the evidence there is, so a record
left out is evidence withdrawn.

So the input contract is the stricter of the two you offered: a continuation must be
given **every earlier record again**, plus what is new (`require_complete`). A partial
or empty batch is refused. The policy check runs first, so a batch that drops a source
the stage does not clear is refused for that reason, by name.

For an evaluation the earlier records are `previous.record_keys`. For a local state
they are read from its decision log (`known_record_keys`); on the recorded data that
log names all 394 records.

### Previous-state validation and sidecar integrity

`state.provenance.json` now carries `state_sha256` (the digest of the state file it
describes) and `retained_sources` (read from the state), with each retained source's
record count for that run and its cleared stages.

A local state is reused only if all of these hold, in order:

| Check | Refused when |
|---|---|
| Provenance exists | no provenance file |
| Readable, and a local state's | unreadable; not an object; wrong `kind`; `is_production_catalogue` not `false` |
| Describes this file | digest differs: the state was edited, or the file belongs to another state; or no digest |
| Agrees with the state | declared `retained_sources` are not what the state retains |
| Policy | a retained or supplied source does not clear the stated stage |
| Completeness | the records omit one the state was made from |
| Time | `as_of` earlier than the state's `generated_at`, or than a record's `fetched_at` |

**The provenance file is a description, never a grant.** Its declared stage and cleared
stages are not read when deciding anything; a test asserts the loader does not read
them. A file edited to claim `public_launch`, with a matching digest, changes nothing.
The digest is not a signature: it ties a description to its state and catches a stale
or mismatched pair. Nobody can widen a permission by editing either file, because none
is taken from them.

A refused state is left exactly as it is: not repaired, rewritten or deleted. Tested by
comparing the directory's bytes before and after every refusal.

### Old local states

A state with no provenance file, or with U5.1's provenance (no digest), is **refused**.
It does not inherit permission from today's records. There is no automatic migration:
nothing in such a state says under what policy it was made. The documented handling is
to leave it or move it aside and generate a new state into a new path. Neither
worktree on this machine currently has a local catalogue state file.

### Stage changes

Authorised by the policies and nothing else. A state made only from IGDB records moves
from private development to private collection (IGDB clears both), and its provenance
then says so. A state retaining Steam's catalogue does not.

### `as_of`

Unchanged from U5.1: the time of the evaluation or generation, not a historical cutoff.
A record fetched after it is refused. Added: `generate-catalogue` refuses an `as_of`
earlier than the `generated_at` of the state it continues (the evaluation already
refused this). Mapping and decision history remain append-only; no binding is dated
before its evidence; no historical record is invented.

## Regression tests

Four new tests in `tests/test_catalogue_expansion.py`, one earlier test extended:

| Case | Result |
|---|---|
| A. Private-development state from IGDB + Steam | built; provenance names both |
| B. Reused at private collection, IGDB only | refused: Steam retained, not cleared; nothing written |
| C. Same at public launch and commercial | refused |
| D. Reused at private development, complete records | permitted; ids, bindings, decisions, quarantine equal |
| – Reused at private development, IGDB only | refused: 128 records missing |
| E. No provenance | refused; files untouched |
| F. Unreadable, not an object, wrong kind, claims production, edited state, other digest, no digest, hides a source, no sources declared | each refused; files untouched |
| – Provenance edited to claim a later stage | still refused by policy |
| G. Evaluation with Steam-derived state, continued where Steam is not cleared, with IGDB-only, five-record and empty batches | refused, naming Steam as retained |
| – Same with Steam's policy left out of the mapping | refused |
| H. Evaluation continued with complete records and policies | succeeds |
| – IGDB-only evaluation promoted to private collection | succeeds |
| I. Identical repeat | narrow 15, broad 234, broad-only 219, zero incremental admissions |
| J. No source supplied or retained | bootstrap catalogue; stage still required; continuable at any stage |
| K. Backdating a continuation | refused; files untouched |
| L. No identity change or rewrite | asserted on every permitted reuse |
| M. Sampling replay tests | pass, offline, under the stated private stage |

No test was weakened.

## Frozen recorded-data results (unchanged, pinned)

394 records; 15 narrow entities; 234 broad; 219 broad-only; 327 bound; 44 quarantined;
23 related products. Wikimedia identifier-ready 14; Steam identifier-ready 90; without
any eligible source 135. First and repeated evaluations give the same numbers. No new
catalogue data was collected and no synthetic record is in these results.

## Source-policy permissions unchanged

`sources/` and `domain/source_policy.py` have no diff. IGDB: private development and
private collection. Steam catalogue: private development only. Steam players and Steam
News: private development only.

## Performance bottlenecks still open

Not touched: the full-scan fallback that makes resolving quadratic; the quadratic
`steam_eligibility` scan; whole-state snapshots per generation; an unmeasured first
broad build against the production job's limits. The U5 measurements stand. Nothing
was re-benchmarked. `require_complete` and `retained_sources` add a linear pass each.

## Gates

Backend: `ruff format --check` clean (219 files); `ruff check` clean; `mypy` clean (206
files); `pytest` **1,488 passed, 8 deselected**; `export-schemas --check` up to date;
`evaluate-resolver` exit 0 (golden set v3, 200 cases, 0 false automatic merges,
precision 52/52).

The 8 deselected are the default `slow` and `live` exclusions. No slow test and no
benchmark was run in this checkpoint.

Frontend (no frontend change): `contract:check`, `typecheck`, `lint`, `test` (179
passed), `build` all pass.

## Unchanged

`resolution/` (`883d0775ac898ab1`), `catalogue/generate.py`, `incremental.py`,
`persistence.py`, `eligibility.py`, `coverage.py`, `lifecycle/`, `domain/`, `sources/`:
no diff. `scoring/` `3b857c551d5ab5b0`, `features/` `24cf7d170f57d8ab`, `events/`
`333373c61ae6c37f`, `event_sources/`, `validation/`: no diff. Golden set file
byte-identical. Artifact contract 1.5.0; `schemas/` and `apps/` no diff. Broad
production discovery still `False`. Production's `CatalogueRepository` and `CURRENT`
untouched.

## Production isolation (read-only, 00:04 UTC 2026-10-13)

Read from GCP and GitHub:

- `main` = `cb33198`, level with origin, clean; `product/local-ui` not merged; no pull
  request. A push to `product/local-ui` starts no workflow; last workflow run still
  38104941975 (11 October).
- Terraform state generation `1791685217422653`: unchanged.
- Control record generation `1791684706340016`: unchanged.
- Scheduler configuration ENABLED, `0 * 12-18 10 *`: unchanged.
- Seven jobs, generation 6, image `sha256:a8680b2be8b5…`: unchanged.
- IAM, dataset schemas, secrets, alert policies: identical to the previous snapshot.
  0 secret versions, 0 user keys. Repository variables unchanged.
- No Wikimedia, Steam or IGDB call by U5.1.1. Local Steam quota ledger still 3.
- The portfolio repository was not touched.

## C2-F observation (read-only; separate; not altered; not judged)

| | |
|---|---|
| Executions since the window opened | 27, none failed |
| 12 October | 24 dispatcher, 1 Wikimedia collector, 1 warehouse: 26 |
| 13 October so far | 1 dispatcher (00:00) |
| Scheduler | last attempt 00:00:48Z on 13 October; next 01:00Z |
| Warehouse rows, written by the soak | `collection_runs` 54 → 68; `expected_targets` 28 → 42; `period_observations` 1,372 → 1,568 |

Those row changes are the soak's own. This is what was observable at one moment, one
day into a seven-day window. It is not a statement that C2-F has passed or will; that
is judged after 18 October against its criteria. Nothing was triggered, restarted or
changed.

## Commits

`835b96e` fix: preserve catalogue source policy across state reuse; then this handoff.
Pushed to `origin/product/local-ui` only. No PR, no merge to `main`.

## Exact next approval required

ChatGPT review of U5.1.1.

STOP. U5.2 has not been started.
