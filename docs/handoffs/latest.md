# Handoff: latest checkpoint (product branch)

This file is the short-lived handoff from Claude Code to ChatGPT. **This copy lives only
on the branch `product/local-ui`.** The handoff on `main` is the C2-F soak's and was not
touched. It supports communication only and replaces no code, test or document.

Production baseline: **PHASE 4 PART C2-F: SEVEN-DAY SOAK RUNNING** (observed read-only
at 01:20 UTC on 2026-10-13; see the last section. Not altered. Not judged.)

Parallel checkpoint: **PRODUCT BUILD U5.2: OFFLINE CATALOGUE SCALING OPTIMIZATION — CHATGPT REVIEW REQUIRED**

Previous checkpoint U5.1.2 was approved. Baseline commit `c89ea4b`.

Offline and on the product branch only. No source was called. Both targets were
optimised with exact equivalence shown; this is not a BLOCKED handoff. Production
adoption remains after C2-F and is not proposed here.

Details: `docs/catalogue-expansion.md`, section "Scaling optimisation (U5.2)".

## Code changed, and why

| File | Change | Why |
|---|---|---|
| `resolution/blocking.py` | The packaging-variant key | Removes the full-scan fallback (Target A) |
| `catalogue/eligibility.py` | A per-call lookup | Removes the per-entity scans (Target B) |
| `tests/eligibility_reference.py` | New: the previous eligibility, verbatim | The reference for parity |
| `tests/test_steam_eligibility_index.py` | New | Parity tests |
| `tests/test_catalogue_blocking.py` | Adversarial tests added; two tests that described the old fallback updated | See "Tests" |
| `tests/test_catalogue_expansion.py` | Pinned work counts updated | Full scans 60 → 0 |
| `docs/catalogue-expansion.md` | New section | |

Not changed: `resolver.py`, `normalize.py`, `quarantine.py`, `models.py`,
`catalogue/incremental.py`, `generate.py`, `persistence.py`, `coverage.py`,
`evaluation.py`, `cli.py`, `domain/`, `sources/`.

## A. Candidate blocking

The resolver calls an entity a packaging variant when the incoming title core, minus
rework words, minus numbers equal to one of **the other record's** release years,
equals the other record's core minus rework words. Which numbers drop depends on the
record compared with. The old index tried every subset of the incoming title's numbers
(exponential) and above ten numbers returned "assess every entity".

Now:

1. Every record is filed under the **words** of its core: the core with all numbers
   removed, "a number" being tested exactly as the resolver tests it.
2. Within that bucket each entry keeps its core and its release years, and the lookup
   evaluates the resolver's own expression by calling the resolver's `_without` and
   `_release_years`.

### Why it is a superset (in fact exact, for this rule)

- Step 1 is a necessary condition. Dropping numbers never changes a title's words, so
  two cores that become equal after dropping numbers already share the same words. A
  record in any other bucket cannot match. Tested directly on 2,000 seeded token lists.
- Step 2 is the resolver's comparison, not an approximation of it: same functions,
  same inputs. Every entity it returns is one the resolver relates by packaging, and
  every such entity is returned.
- Every other key is unchanged: own key, ids both ways, URLs both ways, parent and
  version parent, exact and alternative titles. Overrides and existing bindings are
  still added by reference.

No threshold lowered, no top-K, no similarity, no probabilistic filter, no assumption
that a number is a year or a title unique. A test asserts none of those constructs is
in the module's code.

### Remaining full-scan conditions

**None.** No input asks for every entity. Measured full scans went to 0 at every size
and in the 10,394-record proof. The `None` return meaning "assess everything" stays in
`wanted_refs`' contract, unused, as a safe way out for a future case.

What remains for this rule is a bucket scan: titles with the same words in the same
order share a bucket, compared by tuple, with no resolver call.

## B. Indexed eligibility

`steam_eligibility` builds one lookup per call from the same state, records and
`as_of`: games by id; per entity, records bound at `as_of`, in binding order; per
entity, Steam apps currently quarantined that name it; records by key.
`sampling_targets` groups each Steam record's binding history once.

The lookup is a local value, discarded after the call. No persisted second source.
Signatures unchanged. **The rule is character for character what it was** from the
first deciding line to the end of the function; a test compares the two texts.

Preserved: bindings point-in-time; quarantine current at every `as_of`, not
reconstructed; unconfirmed assertion not eligible; quarantined app not a binding;
bound-but-unasserted not confirmed; several apps stay several ids.

## Exact parity results

**Blocking.** `resolve(blocked_view(view)) == resolve(view)` (whole decision) and equal
quarantine fingerprints, on: every golden case; the hand-built adversarial views; 120
seeded random questions against 300 held records.
`generate_catalogue_blocked == generate_catalogue` as states and as JSON, on: both
recorded fixtures; a 700-record stratified sample; three input orderings; the seeded
random catalogue of 420 records with cross-check on; and **10,394 records** (the
pre-existing slow proof, run here: passed, 0 full scans). Cross-check mode unchanged.

**Eligibility.** Against the verbatim reference, field for field and in order, plus the
sampling targets with binding references, on: the 394 recorded rows; both fixture sets
together and the bootstrap set alone; the reference engine's state; five moments around
binding; a binding closed and replaced, and one closed without replacement; records
withheld; the empty catalogue; a state with no bindings or quarantine; one synthetic
entity per branch of the rule; a 3,000-record synthetic catalogue.

No mismatch was found at any point.

## Adversarial tests (all synthetic, each asserting offered == the resolver's own set)

A title with 37 distinct numbers; twelve numbers, one being the other record's release
year; several year-like tokens with different subsets released; a sequel numeral that
is not a year, and the same numeral when it is; `2016` and `02016`; titles that are
only numbers; a repeated year; exact and alternative titles both ways; one normalised
title on three distinct games; series siblings in Arabic and Roman numerals; remaster,
HD and packaging editions; a title of only packaging words; id links with unrelated
titles both ways; a store URL alone, corroborated, and claimed by two games; parent and
version-parent links; conflicting evidence; an override target with no evidence; an
existing binding; no key at all; an empty index. Cases A to R of the brief are covered.

## Frozen recorded counts (unchanged, pinned)

15 bootstrap entities; 234 broad; 219 broad-only; 327 bound; 44 quarantined; 23
related. Steam: 90 eligible, 69 inapplicable, 75 unknown, 91 apps. Wikimedia mapped 14;
without any eligible source 135.

## Benchmark

**Method.** Synthetic records only (`tests/catalogue_samples.py`). Identical datasets
and the same deterministic id factory on both sides. Same machine, same day, one
process per size, nothing else running. Apple M3, 8 cores, 8 GB, macOS 26.6.2, Python
3.12.15. One run per size per side. Laptop timings, not production metrics.

- Before: commit `c89ea4b`, run from an untouched export of it.
- After: commit `4eface9`.

| Records | | Resolving | Coverage | Total | Entities examined | Full scans | Resolver calls | Peak memory |
|---|---|---|---|---|---|---|---|---|
| 1,000 | before | 0.46 s | 0.05 s | 0.51 s | 25,176 | 60 | 1,030 | 54 MB |
| | after | 0.14 s | 0.01 s | 0.15 s | 2,233 | 0 | 1,030 | 54 MB |
| 5,000 | before | 10.38 s | 0.96 s | 11.33 s | 578,428 | 312 | 5,167 | 105 MB |
| | after | 1.09 s | 0.08 s | 1.18 s | 11,858 | 0 | 5,167 | 109 MB |
| 10,000 | before | 44.73 s | 4.00 s | 48.74 s | 2,289,686 | 628 | 10,343 | 170 MB |
| | after | 3.22 s | 0.20 s | 3.41 s | 23,887 | 0 | 10,343 | 177 MB |
| 20,000 | before | 191.41 s | 21.87 s | 213.28 s | 9,108,927 | 1,260 | 20,690 | 305 MB |
| | after | 10.70 s | 0.25 s | 10.95 s | 47,982 | 0 | 20,690 | 308 MB |

Entities, quarantined records, related products, resolver calls and fingerprint checks
are identical on both sides at every size. The "before" column reproduces U5's own
measurements to within a second. U5's original figures are left as recorded in the doc.

## Remaining performance bottlenecks

**Resolving is still superlinear**: each doubling multiplies it by about 3 (was 4.3).
Profiled at three sizes, the remaining growth is in the quarantine fingerprint:
`evaluation_fingerprint` hashes every record of every candidate entity each time a
quarantined record is re-examined, and `SourceRecord.content_hash` is recomputed each
call (59,515 calls at 5,000; 204,525 at 10,000; 747,955 at 20,000). A few synthetic
entities accumulate records in proportion to the sample (largest: 8, 16, 32 records;
mean 1.26 throughout), and every candidate for one re-hashes all of it. Whether real
catalogues have such entities is unknown.

Not changed: it is outside the two targets and decides when a quarantine is
re-resolved. Also unaddressed: whole-state snapshots per generation.

Caveats on the gain: the synthetic sample deliberately puts a dozen numbers in about 6%
of titles. On the 394 recorded rows there were 0 full scans before, so on data like
that the blocker's gain is small. No 50,000 run was made. Nothing ran on Cloud Run.

## A latent fault found, not fixed

The resolver's `_without` tests `str.isdigit()` then calls `int()`. A few characters
pass the first and fail the second (Ethiopic digits), so such a title raises
`ValueError` inside the resolver. Pre-existing, identical before and after, left alone
because the resolver is frozen here.

## Tests and slow-test exclusions

Added: 6 adversarial blocking tests; 5 eligibility parity tests.

Changed deliberately: the test that asserted a crowded title falls back to assessing
everything was replaced by one asserting it is keyed; the stratified-sample test now
asserts 0 full scans where it asserted more than 0; the pinned 1,000-record work counts
moved from 60 full scans and 25,176 entities offered to 0 and 2,233, with resolver
calls (1,030) and fingerprint checks (804) unchanged. No golden label was touched.

**Slow tests actually run in this checkpoint: all four**, as tests: the 5,000, 10,000
and 20,000 scale cases, and the 10,394-record blocked-versus-reference proof. 4 passed
in 29 minutes. Separately, the benchmark above was run as a script for both sides.

## Gates

Backend: `ruff format --check` clean (221 files); `ruff check` clean; `mypy` clean (208
files); `pytest` **1,499 passed, 8 deselected** (default `slow`/`live` exclusions; the
slow ones were then run explicitly, above); `export-schemas --check` up to date;
`evaluate-resolver` exit 0: golden set v3, 200 cases, 0 false automatic merges,
precision 52/52, real-evidence recall 44/44, quarantine rate 55/200, as before.

Frontend (no frontend change): `contract:check`, `typecheck`, `lint`, `test` (179
passed), `build` all pass.

## Unchanged

- **Source policy**: `sources/` and `domain/source_policy.py` no diff.
- **Identity rules**: `resolver.py`, `normalize.py`, `quarantine.py` no diff; golden set
  byte-identical.
- **Score**: `scoring/` `3b857c551d5ab5b0`, `features/` `24cf7d170f57d8ab`; golden
  replay bit-for-bit.
- **Other intelligence**: `events/` `333373c61ae6c37f`, `event_sources/`, `validation/`
  no diff; fingerprint tests pass.
- **Artifact contract**: 1.5.0; `schemas/` and `apps/` no diff.
- **Catalogue safety**: policy fail-closed, retained-source, completeness, sidecar and
  `as_of` tests all pass. Broad production discovery still `False`.
  `CatalogueRepository`, `advance_catalogue` and `CURRENT` untouched.

## Production isolation (read-only, 00:52 and 01:20 UTC 2026-10-13)

Read from GCP and GitHub:

- `main` = `cb33198`, level with origin, clean; `product/local-ui` not merged; no pull
  request; a push to it starts no workflow; last workflow run still 38104941975.
- Terraform state generation `1791685217422653`: unchanged.
- Control record generation `1791684706340016`: unchanged.
- Scheduler configuration ENABLED, `0 * 12-18 10 *`: unchanged.
- Seven jobs, generation 6, image `sha256:a8680b2be8b5…`: unchanged. Every soak
  execution ran that image.
- IAM, dataset schemas, secrets, alert policies: identical to the previous snapshot.
  0 secret versions, 0 user keys. Repository variables unchanged.
- No Wikimedia, Steam or IGDB call by U5.2. Local Steam quota ledger still 3.
- No benchmark was run anywhere but this laptop.
- The portfolio repository was not touched.

## C2-F observation (read-only, 01:20 UTC 2026-10-13; separate; not altered; not judged)

| | |
|---|---|
| Executions since the window opened | 28, none failed |
| 12 October | 24 dispatcher, 1 Wikimedia collector, 1 warehouse |
| 13 October so far | 2 dispatcher (00:00, 01:00) |
| Scheduler | last attempt 01:00:05Z; next 02:00Z |
| Warehouse rows (00:52 reading) | `collection_runs` 68, `expected_targets` 42, `period_observations` 1,568 |

Read from Cloud Run, Cloud Scheduler and BigQuery. A little over a day into a seven-day
window. This is not a statement that C2-F has passed. Nothing was triggered, restarted
or changed.

## Post-C2-F integration still pending

Nothing here is adopted in production. The blocked engine is what production's
`advance_catalogue` already calls, so a production image built from this branch would
pick the change up; no such image has been built, and none may be until this branch is
reviewed and merged after C2-F. Still to verify then: behaviour on real records at
scale; a first broad build on the production job's vCPU and memory; the fingerprint
cost on real data. Broad discovery remains a separate reviewed decision.

## Commits

`4eface9` perf: optimize offline catalogue blocking and eligibility; then this handoff.
Pushed to `origin/product/local-ui` only. No PR, no merge to `main`, no deployment.

## Exact next approval required

ChatGPT review of U5.2.

STOP. The next checkpoint has not been started.
