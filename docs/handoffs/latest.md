# Handoff: latest checkpoint (product branch)

## PRODUCT BUILD U5.3: QUARANTINE FINGERPRINT SCALING — CHATGPT REVIEW REQUIRED

Branch `product/local-ui` only. Offline. No production action, no live source call, no
deployment, no pull request, no merge to `main`. This file is the product-branch
handoff; `main`'s handoff was not touched. No further checkpoint has been started.

- **Approved baseline (rollback point):** `55531e03183bff90ec85140528c3a3d7d694803c`
  (U5.2 handoff; U5.2 implementation `4eface9`).
- **Implementation commit:** `2494916` — `perf: reduce redundant quarantine
  fingerprint hashing`.
- **Production `main`:** `cb33198e2510965e41dff0f5fe7e75cacb7ee2ca`, unchanged.
- At the start the checkout was on `product/local-ui` at `55531e0`, level with
  `origin/product/local-ui`, tree clean; the previous handoff was U5.2.

**Result in one line:** each record's content hash is now computed once per run instead
of once per fingerprint that reads it; every fingerprint, decision and catalogue state
is identical to the original; resolving time roughly halved at 10,000 records and
above.

## Files changed

| File | Change |
|---|---|
| `src/metaspike/domain/source_records.py` | New `ContentHashMemo` (per-run, keyed by record object). `SourceRecord.content_hash` itself is untouched. |
| `src/metaspike/resolution/quarantine.py` | `evaluation_fingerprint(view, overrides, hashes=None)`: reads content hashes through the memo when one is given. Payload, ordering, canonical JSON and SHA-256 untouched. |
| `src/metaspike/catalogue/incremental.py` | `generate_catalogue_blocked` creates one memo per call and passes it to both fingerprint calls and the decision log. `GenerationStats` gained `content_hashes_requested` / `content_hashes_computed`. The cross-check side stays unmemoised. |
| `src/metaspike/catalogue/evaluation.py` | `WorkCount` reports the two new counters (offline evaluation summary only). |
| `tests/catalogue_scale.py` | Harness counts real `content_hash` computations by wrapping the property, in a separate second run. |
| `tests/fingerprint_reference.py` | New. Verbatim copy of the U5.2 `evaluation_fingerprint` (checked by diff), used as the reference. |
| `tests/test_fingerprint_memo.py` | New. 9 tests. |
| `docs/catalogue-expansion.md` | New section "Quarantine fingerprint hashing (U5.3)". U5 and U5.2 figures left as recorded. |

Not changed (no diff): `resolver.py`, `normalize.py`, `blocking.py`,
`catalogue/generate.py` (the reference engine), `persistence.py`, `eligibility.py`,
`sources/`, `scoring/`, `features/`, `events/`, `event_sources/`, `validation/`,
`schemas/`, `apps/`, `tests/fixtures`, `.github/`, the golden set. No persisted format
changed; no schema changed; no migration concern. The memo is never written anywhere.

## Root cause

`SourceRecord.content_hash` is a property that serialises the whole record and takes a
SHA-256 every time it is read. `evaluation_fingerprint` reads it for the incoming
record, for every record of every candidate entity and for every unresolved linking
record, on every pass. At 20,000 synthetic records that was 747,955 computations over
20,000 records. Confirmed by counting, not inferred (see Benchmark).

## The optimisation, exactly

`ContentHashMemo.of(record)` returns `record.content_hash`, computed the first time
that object is asked about and kept for the rest of the run. That is all. Larger
designs (per-entity evidence summaries) were considered and not used: they add state
that must be kept in step with entities, and were not needed for a clear gain.

## Cache lifetime and invalidation

- **Key: the record object** (`id(record)`), and the entry holds the record itself, so
  it checks `held is record` and the address cannot be reused by another record while
  the entry exists. **Not** the source-native key, and nothing a record says.
- A `SourceRecord` is a frozen model: one object has one content hash for its life.
- Any change is a new object (`model_copy(update=...)`, re-parse, re-fetch), which is
  looked up afresh. Changed content under the same source id can never be answered by
  the older record's entry.
- **Lifetime: one call of `generate_catalogue_blocked`.** Created inside it, dropped
  when it returns. Not module-level, not shared between runs, not stored. A
  continuation is a new run with a new memo. Bounded by the records of that run.
- Candidate membership, `bound_to`, linking context and overrides are still read
  directly on every fingerprint; only the per-record hash is reused.

Cases from the brief, and where each is shown (`tests/test_fingerprint_memo.py`):

| Case | Shown by |
|---|---|
| A same record repeatedly; B re-fetched unchanged | hash test: same value; a re-fetched object is computed afresh and equal |
| C changed title; D changed external ids; E changed release dates; F `model_copy` | hash test and fingerprint test: new value each time, never the older answer |
| G candidate gains or loses evidence; O a record in several candidate views; P several records on one entity | fingerprint test (gain, loss, one field of a candidate's record); real-build test (205,277 requests over 10,397 computations at 10,394 records) |
| H rebinding / existing binding | fingerprint test (`bound_to`); quarantine test (a bound candidate record changes and reopens the quarantine) |
| I corroborating evidence arrives | quarantine test: released by path A, one appended decision and binding |
| J linking context changes | linking test: an unresolved linking record arriving later reopens the record |
| K override changes | fingerprint test (applied, different, newer version, removed); linking test |
| L continuation from previous state | builds test and quarantine test, both engines |
| M two evaluations in one process | separate-runs test; no module-level memo exists |
| N same title, different source ids | hash test: different content, different hash |

## Fingerprint and build parity

- **Content hash:** `memo.of(r) == r.content_hash` for unchanged, re-fetched and
  changed records, and across 3,000 short-lived records with garbage collection.
- **Fingerprint:** current-with-memo == current-without == original U5.2 function, on
  hand-built views; the value moves for 16 distinct evidence changes and stays for
  changes that are not evidence.
- **Every fingerprint in real builds:** more than 2,500 fingerprints computed while
  building the recorded fixtures and a 1,500-record synthetic sample (first builds and
  continuations) were each re-derived by the original function and matched.
- **First generation:** blocked engine == reference engine (`==` and identical JSON) on
  the recorded fixtures and synthetic samples, cross-check on.
- **Incremental generation:** the same, for unchanged re-runs, new records, changed
  records, newly corroborated quarantines, a changed bound record reopening a
  quarantine, and a changed override.
- **10,394 records (slow proof, run in this checkpoint):** blocked == reference, equal
  JSON. 4,336 entities, 5,494 bindings, 4,087 quarantined, 0 full scans; 205,277 hashes
  requested, 10,397 computed. (The three beyond the record count are records the run
  holds as separate objects; a few more on small fixtures for the same reason. It is
  one per object, as designed.)

The reference engine was not edited and was not given a memo, so every engine
comparison is also a memo-versus-no-memo comparison. No mismatch was found at any
point.

## Quarantine re-evaluation behaviour (unchanged)

Each step run through both engines, states equal:

- unchanged evidence: skipped; the entry (evaluations, `first_seen`, `last_evaluated`,
  fingerprint, history) is exactly what it was, including after a re-fetch;
- a bound candidate record changes: the quarantine leaning on it is examined again
  (`evaluations` 2, `first_seen` kept, `last_evaluated` moved) and, still ambiguous,
  stays; an unrelated quarantine is not touched;
- conflicting evidence examined again is still a conflict;
- corroborating id evidence releases a title-only quarantine through the frozen
  resolver; earlier history intact; a point-in-time read before the evidence still
  shows the record unbound;
- a later unresolved linking record reopens the record it links to;
- an override resolves the record it names;
- no record is dropped: every supplied record is bound or in quarantine.

No threshold moved. A title alone is still never an identity. No auto-merge was added.

## Frozen recorded counts (unchanged, pinned by tests)

394 records · 15 narrow entities · 234 broad · 219 broad-only · 327 bound ·
44 quarantined · 23 related. Steam: 90 eligible / 69 inapplicable / 75 unknown,
91 apps. Wikimedia mapped 14. 135 without an eligible source. Golden set v3: 200 cases,
178 correct, 0 false auto merges; file and content hashes unchanged. On the recorded
394 rows the engine requests 749 content hashes and computes 397.

## Benchmark

**Method.** Same machine, same synthetic datasets (`tests/catalogue_samples.py`), same
deterministic id factory, one fresh process per size (so peak memory is not
cumulative). Apple M3, 8 cores, 8 GB, macOS 26.6.2, Python 3.12.15. Before = commit
`55531e0` run from an untouched `git archive` export with only the measuring harness
replaced; after = the working tree that became `2494916`. Times come from an
uninstrumented run; hash computations are counted in a second run of the same
evaluation with the `content_hash` property wrapped on the class, so the figure is real
computations, not accesses. "Requested" is the engine's own count of accesses.

**One run per size per side.** Variability was not measured; treat differences of a few
percent, and all memory differences here, as noise. Laptop timings on synthetic data,
not production measurements, and not a prediction for real records.

| Records | | Resolving | Coverage | Total | Hashes computed | Requested | Fingerprint checks | Resolver calls | Entities examined | Peak memory |
|---|---|---|---|---|---|---|---|---|---|---|
| 1,000 | before | 0.14 s | 0.01 s | 0.15 s | 5,184 | – | 804 | 1,030 | 2,233 | 56 MB |
| | after | 0.10 s | 0.01 s | 0.11 s | 1,000 | 5,184 | 804 | 1,030 | 2,233 | 55 MB |
| 5,000 | before | 1.08 s | 0.08 s | 1.16 s | 59,515 | – | 4,042 | 5,167 | 11,858 | 108 MB |
| | after | 0.71 s | 0.03 s | 0.75 s | 5,000 | 59,515 | 4,042 | 5,167 | 11,858 | 111 MB |
| 10,000 | before | 3.33 s | 0.24 s | 3.57 s | 204,525 | – | 8,086 | 10,343 | 23,887 | 171 MB |
| | after | 1.75 s | 0.19 s | 1.94 s | 10,000 | 204,525 | 8,086 | 10,343 | 23,887 | 179 MB |
| 20,000 | before | 10.49 s | 0.29 s | 10.78 s | 747,955 | – | 16,188 | 20,690 | 47,982 | 307 MB |
| | after | 5.41 s | 0.29 s | 5.69 s | 20,000 | 747,955 | 16,188 | 20,690 | 47,982 | 311 MB |
| 50,000 | before | 78.03 s | 1.00 s | 79.03 s | 4,401,151 | – | 41,532 | 51,729 | 121,421 | 707 MB |
| | after | 36.48 s | 0.98 s | 37.47 s | 50,000 | 4,401,151 | 41,532 | 51,729 | 121,421 | 719 MB |

Entities (432 / 2,070 / 4,117 / 8,210 / 20,230) and quarantined counts are identical on
both sides at every size. The baseline's hash counts equal the figures U5.2 profiled.
Memory: up to 12 MB more at 50,000 (one 64-character string per record), not
distinguishable from noise with one run. The U5 and U5.2 figures in
`docs/catalogue-expansion.md` are left as historical.

## Remaining performance issues

Resolving is still superlinear: about ×2.5 from 5,000 to 10,000, ×3.1 from 10,000 to
20,000 and ×6.7 from 20,000 to 50,000. Profiling puts the remainder inside the frozen
resolver's own assessment (`_assess`, `_has_id_link`, `_names`, `_title_relation`) when
a candidate entity holds many records; the largest synthetic entity holds 8 / 16 / 32
records at 5,000 / 10,000 / 20,000. That is resolver work, not hashing, and reducing it
would mean touching or wrapping frozen identity code, so it was left alone. Whether
real catalogues produce such entities is not known.

## Known Unicode issue (separate, not fixed)

The resolver's `_without` uses `str.isdigit()` and then `int()`. Some Unicode digits
(for example Ethiopic) pass the first and fail the second with `ValueError`. It
predates U5.2, is in frozen resolver code, and is not touched here. Nothing in U5.3
testing showed a wider consequence. It needs its own reviewed decision.

## Quality gates

Backend (in the product worktree):

- `uv run ruff format --check` — 223 files already formatted
- `uv run ruff check` — All checks passed
- `uv run mypy` — no issues in 210 source files
- `uv run pytest` — **1,508 passed, 8 deselected in 306.44s** (default `slow`/`live`
  exclusions; includes the 9 new fingerprint tests and all catalogue and quarantine
  regressions)
- `uv run metaspike export-schemas --check` — Schemas are up to date
- `uv run metaspike evaluate-resolver` — exit 0; 200 cases, 0 false auto merges

All re-run on 2026-10-14 on the exact tree that was committed.

Slow tests, run explicitly (`pytest -v -m slow tests/test_catalogue_blocking.py
tests/test_catalogue_expansion.py`): **4 passed, 43 deselected in 1286.77s** — the
10,394-record blocked-versus-reference proof (1,267.79 s) and the 5,000 / 10,000 /
20,000 scale cases. No slow test was excluded. `live` tests were not run (no live
source calls are permitted).

Frontend (`apps/dev-ui`, no frontend change; run 2026-10-13 before the implementation
commit, tree unchanged since): `contract:check`, `typecheck`, `lint`, `test`
(179 passed), `build` — all passed.

A note on elapsed time: the slow proof was first attempted on 2026-10-13 and did not
finish because the laptop, on a nearly empty battery, kept sleeping. It was re-run on
2026-10-14 on a charged machine; the result above is that run. The benchmark table was
measured earlier on 2026-10-13, before the battery ran down.

## Production isolation (read-only, 2026-10-14 00:44–00:46 UTC)

- `main` = `cb33198`, level with `origin/main`, clean. `product/local-ui` not merged.
  No pull request. Last workflow run still 38104941975 (11 Oct, `main`); a push to the
  product branch starts no workflow. `.github/` not modified.
- Terraform state generation `1791685217422653`: unchanged. No Terraform was run.
- Control record generation `1791684706340016`: unchanged.
- Scheduler ENABLED, configuration unchanged; only `lastAttemptTime` differs from the
  previous snapshot.
- Seven jobs: image and configuration identical to the previous snapshot.
- IAM, dataset schemas, secrets, alert policies: identical. 0 secret versions, 0
  user-managed keys across 10 service accounts.
- Repository variables unchanged (`SCHEDULER_PAUSED=false`, `SOAK_MONTH=10`,
  `SOAK_START_DAY=12`, `SOAK_END_DAY=18`, `SOAK_DAILY_EXECUTION_CEILING=31`).
- No Wikimedia, Steam or IGDB call. No job triggered. Benchmarks ran only on this
  laptop. The portfolio repository was not touched.
- `BROAD_DISCOVERY_ENABLED` is still `False`; `CatalogueRepository`,
  `advance_catalogue` and `CURRENT` untouched.

## C2-F observation (read-only, 2026-10-14 00:44 UTC; separate; not altered; not judged)

| | |
|---|---|
| Executions since the window opened | 53; all 53 succeeded; none running |
| 12 October | 24 dispatcher, 1 Wikimedia collector, 1 warehouse |
| 13 October | 24 dispatcher, 1 Wikimedia collector, 1 warehouse |
| 14 October so far | 1 dispatcher (00:00) |
| Scheduler | last attempt 2026-10-14T00:00:10Z |
| Warehouse rows | `collection_runs` 120, `expected_targets` 56, `period_observations` 1,624 |

The one failed task in the project's execution list (`ms-wikimedia-mr9kc`, 7 October)
predates the soak window. Two days into a seven-day window. This is not a statement
that C2-F has passed. Nothing was triggered, restarted, cancelled or changed.

## Production integration still deferred

Nothing here is adopted in production. Production's `advance_catalogue` calls the
blocked engine, so an image built from this branch would pick the change up; none has
been built and none may be until review and merge after C2-F.

## Product intelligence gaps (for the next decision, not started)

Performance is no longer the obvious next step. The substantive gaps are: Wikimedia
identifiers beyond the bootstrap set (14 of 234 entities mapped), source-policy
clearance beyond private development, source coverage and agreement (135 entities
without any eligible monitoring source), and creator opportunity scoring.

## Commits

`2494916` perf: reduce redundant quarantine fingerprint hashing; then this handoff.
Pushed to `origin/product/local-ui` only. No PR, no merge to `main`, no deployment.

## Exact next approval required

ChatGPT review of U5.3, then explicit user authorisation for whatever comes next.

STOP. No further checkpoint has been started.
