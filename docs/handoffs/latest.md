# Handoff: latest checkpoint (product branch)

## PRODUCT BUILD U6.7.1: WIKIMEDIA RECORDING TEMPORAL AND INSPECTION CLOSEOUT — CHATGPT REVIEW REQUIRED

This checkpoint was worked on branch `product/local-ui` only, entirely offline:

- no live source request;
- no live recorder run;
- no pin;
- no cloud access or deployment;
- no pull request or merge.

**No later checkpoint has been started.**

## Things to review first

1. **The time guard runs inside `assess_evidence`, not only in the CLI.** A library
   caller is held to it. It keys on the directory that the pageview provenance names. A
   caller who builds a misleading *unverified* provenance by hand can still read a
   recorder directory early; such data is unverified and counts for nothing. A caller
   claiming *verified* cannot slip past: every non-legacy pinned digest must name a
   recorder directory that is intact, complete and received by `as_of`.
2. **Two evaluation modes are now explicit and tested.**
   - Point-in-time readings (`assess_evidence`, `evidence`, the private UI) refuse an
     `as_of` before receipt.
   - `opportunity-history` stays a retrospective reconstruction of source periods
     (validation_protocol_v1), so its moments may precede retrieval. It now also requires
     an intact, complete recorder recording.
   
   Its report text is unchanged. The distinction is stated in code and in the runbook,
   not added to the report, so the U6.5 fingerprint is unchanged.
3. **A recording in which every target said "no data"** is classified `failed` by the
   recorder's existing rule: everything answered, but nothing was recorded. This is
   requirement K's "complete recording with no usable bodies". It is never review-ready
   and never verified evidence.
4. **One file outside the brief's likely list changed: `src/metaspike/evidence/
   acquisition.py`.** It gains one public helper, `record_id_matches()`, which checks that
   a record's id is still the digest of its own content. This lets inspection detect an
   edited record. It is five lines, uses the existing private `_record_id`, and changes no
   behaviour. `src/metaspike/opportunity/history.py` was also changed, for the
   retrospective-mode requirement in §3.

## 1. Starting SHA

`9510c95c895376e8d96804b2b3ac435124314fad`. Verified at the start: on
`product/local-ui`, at that commit, tree clean.

## 2. Implementation SHA

`aafaa05ece1483d6d0bdee600f669144083d3fbc`: `fix: enforce recording time and inspection
safety`.

## 3. Exact files changed

| File | Change |
|---|---|
| `src/metaspike/pageview_recorder.py` | Adds `check_recording` and `RecordingCheck` (integrity, completeness and review-readiness), `require_usable_recording`, `is_recorder_output`, `LEGACY_PAGEVIEW_RECORDINGS` and `RecordingRefused`. Inspection reports the three separately. |
| `src/metaspike/evidence/assemble.py` | `assess_evidence` calls `require_usable_recording` when pageviews are supplied. |
| `src/metaspike/cli.py` | `evidence` refuses early with a clear message (exit 5). `--inspect` exits 0, 3 or 4. |
| `src/metaspike/opportunity/history.py` | Verified demand from a recorder directory must be intact and complete. A non-legacy pinned digest without acquisition files is refused. |
| `src/metaspike/evidence/acquisition.py` | `record_id_matches()` (see item 4 above). |
| `tests/test_pageview_recorder.py` | 6 new tests; the partial-run test now asserts the honest diagnostic verdict. |
| `docs/wikimedia-recording-runbook.md` | Inspection verdicts, exit codes, the time boundary, the two modes. |
| `docs/handoffs/latest.md` | This handoff (separate commit). |

No frontend file or contract changed.

## 4. Acquisition-time enforcement

`require_usable_recording(provenance, as_of)` applies to every directory the pageview
input names that carries `acquisition/`.

- `check_recording` must find no problem. If it does, the recording is refused as "do not
  hold together".
- `as_of` must be at or after the latest `received_at` among the records. Otherwise it is
  refused: "a reading at … could not have had them". Nothing is assessed: the guard runs
  before the assessment, at the CLI with exit 5 and inside `assess_evidence` as a
  `ValueError` subclass.
- Read as verified, the recording must be review-ready: intact, complete, and at least one
  body.
- Read as verified, every digest must be a legacy fixture or a recorder directory.
  Deleting `acquisition/` from a new recording never makes it legacy.

No acquisition time is ever invented or backdated. The check compares recorded times only.

## 5. Legacy historical-fixture compatibility

`LEGACY_PAGEVIEW_RECORDINGS = {260d787e…}` is the U6.0.1 pageview fixture. It predates
acquisition records, so its retrieval times are unknown. It is read, as before, under the
publication-lag reconstruction, and never as a point-in-time acquisition.

Tested:

- the fixture is verified;
- it is not recorder output;
- the guard passes;
- `assess_evidence` at `REAL_AS_OF` reads it as verified;
- the U6.5 history fingerprint is unchanged: `ophist_b1d2af287f618f8ab1dbc9634b5b1292`,
  with 546 assessments (0 candidate, 35 watchlist, 511 insufficient).

## 6. Complete versus partial inspection

`inspect_recording` now reports three things separately:

- **`integrity`:** `intact` or `problems found`;
- **`run_status`:** `complete`, `partial` or `failed`;
- **`review_ready`:** intact AND complete AND at least one body.

It also gives a verdict.

| Verdict | Exit code |
|---|---|
| `INTACT AND COMPLETE: ready for owner review (still UNVERIFIED)` | 0 |
| `INTACT BUT PARTIAL: diagnostic only, NOT ready for pinning` | 3 |
| `INTACT BUT FAILED: nothing usable was recorded, NOT ready for pinning` | 3 |
| `PROBLEMS FOUND: not a usable recording` | 4 |

Partial and failed runs are still inspected in full, including the replay. Every verdict
repeats that integrity is not provenance.

## 7. Manifest consistency validation

`check_recording` now refuses:

**File set**

- unexpected files or directories, at the top level or in `acquisition/`;
- a body without a record;
- a record without a body;
- more than one record per article;
- bodies or records that differ from what the manifest lists;
- a bodies digest that differs from the one written at acquisition.

**Records**

- a record whose id no longer matches its content (an edited record);
- a binding to another game or article;
- a record whose request is not the planned `GET <path>`;
- a record whose source or policy digest is not the plan's;
- record times that differ from the exchange's;
- an exchange that is out of order or earlier than the plan.

**Manifest and outcomes**

- a plan whose id no longer matches its content (an edited plan);
- outcomes that are not the planned targets in planned order;
- a recorded outcome that does not name its exact file and record id;
- a non-recorded outcome that keeps a body;
- a "not attempted" outcome that has an answer, or that appears before an attempted one;
- a `requests_sent` count that differs from the attempted targets;
- targets not attempted although the run says it did not stop;
- a run status that disagrees with the outcomes.

## 8. Before and after regression evidence

**Before (U6.7):** inspecting a partial or failed run said "no problems found", with exit
0. `metaspike evidence --wikimedia-replay <recorder dir>` read a recording at any `as_of`,
including before its responses were received.

**After (U6.7.1),** with mock transports and temporary files only:

| | Test | Result |
|---|---|---|
| A | Reading at the last receipt | Accepted, CLI and library |
| B | Reading one second before | Refused, exit 5, nothing assessed; unpinned and under a TEST-ONLY pin; library `assess_evidence` raises |
| C | Legacy fixture | Behaviour unchanged |
| D | Edited manifest outcome; edited plan; edited record time | Refused |
| E | Dropped record; duplicated record | Refused |
| F | Self-consistent record bound to another game | Refused ("binds another game") |
| G | Self-consistent record with another request; with shifted times | Refused |
| H | Complete run | `intact` / `complete` / ready; exit 0 |
| I | Partial run (503 mid-run) | `intact` / `partial` / not ready; exit 3; refused if pinned; readable unpinned as unverified |
| J | Failed run (transport failure) | `intact` / `failed` / not ready; exit 3; refused if pinned |
| K | All "no data" | `failed`, 0 bodies, not ready; refused if pinned |
| — | New recording with `acquisition/` deleted | Refused when pinned; unverified when not |
| — | History mode | A complete recording is accepted at moments before receipt (retrospective, stated); a partial one is refused |
| L, M, N and P | Existing replay, unverified-standing, no-network (sockets blocked, live transport replaced), planning, confirmation, policy and budget tests | All pass unchanged |
| O | U6.5 fingerprint | Unchanged |

## 9. Full quality-gate results (actual)

| Gate | Result |
|---|---|
| `uv run ruff format --check` | exit 0 (267 files) |
| `uv run ruff check` | exit 0 |
| `uv run mypy` | exit 0, no issues in 246 files |
| `uv run metaspike export-schemas --check` | exit 0, up to date |
| `uv run metaspike evaluate-resolver` | exit 0; golden v3 `c2b3aef7b372`; false AUTO_MATCH merges 0; precision 100.0% (52/52); real-evidence recall 100.0%; NEW_ENTITY 100.0% |
| Targeted suites (see below) | 232 passed |
| `uv run pytest` | **1,668 passed, 8 deselected**, exit 0 (previously 1,662) |

The targeted suites were recorder, history, readiness, evidence, historical validation,
opportunity, wikimedia and v0 parity.

**Run time.** The full run took 17m40s, against 7m37s for U6.7. The machine was at 5%
battery and not charging. On the same suite, the code before this change took 53s and the
code after it took 47s, so the time is the machine's, not the change's.

**Frontend gates:** not run, because no frontend code or contract changed. No live API
test exists or was run.

## 10. Frozen contract parity

These are unchanged:

- resolver_v2;
- baseline_rules_v1;
- signal_semantics_v2;
- metaspike_score_v0;
- event_heat_rules_v1;
- event_classification_rules_v1;
- validation_protocol_v1;
- outcome_rules_v1;
- recommendation_snapshot_v2;
- creator_opportunity_rules_v1.

Also unchanged: source policies, collectors, `PINNED_RECORDINGS`, the public artifact
contract 1.5.0 and the U6.5 historical result.

## 11. Git-only C2-F isolation

C2-F isolation: Git-only verification. No GCP access, no production action.

Verified with Git:

- Only `product/local-ui` changed.
- `main` is untouched: `origin/main` and the main worktree are both at
  `cb33198e2510965e41dff0f5fe7e75cacb7ee2ca`.
- No changes under `infra/`, `.github/`, `artifacts/`, `schemas/`, `apps/` or
  `tests/fixtures/`.
- `BROAD_DISCOVERY_ENABLED = False` is unchanged.
- No deployment and no source call.

A temporary worktree of `9510c95`, used only for the timing comparison, was removed.

## 12. No live run occurred

No request was sent to Wikimedia or any source. `cli._live_transport` was replaced in
every test, and sockets were blocked. `.metaspike/recordings` does not exist. Nothing was
pinned.

## 13. No later checkpoint started

Nothing beyond U6.7.1 has been started:

- no U6.8;
- no live recording;
- no post-soak acceptance;
- no pin;
- no deployment, merge or publication.

Awaiting independent ChatGPT review and explicit owner authorisation.
