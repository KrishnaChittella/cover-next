# Handoff: latest checkpoint (product branch)

## PRODUCT BUILD U6.7.2: VERIFIED PAGEVIEW CONTENT BINDING — CHATGPT REVIEW REQUIRED

This checkpoint was worked on branch `product/local-ui` only, entirely offline:

- no live source request or live recorder run;
- no new recording and no new pin;
- no cloud access or deployment;
- no pull request or merge.

**No later checkpoint has been started.**

## Starting and implementation SHAs

- **Starting:** `349551dd16fcfb50e71311c9356b364d13a3964f`. Verified at the start: on
  `product/local-ui`, at that commit, tree clean.
- **Implementation:** `f86d3200628dc86bc18628988a966bb586181b39`: `fix: bind verified
  pageviews to pinned replay content`.

## Exact changed files

| File | Change |
|---|---|
| `src/metaspike/pageview_recorder.py` | New `bind_verified_pageviews`. `material_content` moved here unchanged (it now raises `RecordingRefused` on a duplicate). |
| `src/metaspike/evidence/assemble.py` | `assess_evidence` calls `bind_verified_pageviews(state.catalogue, wikimedia, provenance, as_of)` after the U6.7.1 guard. |
| `src/metaspike/opportunity/history.py` | The verified-demand block is replaced by `bind_verified_pageviews(..., as_of=None)`, wrapped as `HistoryRefused` with the same messages. `material_content` was removed from here. |
| `tests/test_pageview_recorder.py` | 5 boundary regressions. |
| `tests/test_evidence.py` | One test corrected (see "Things to review"). |
| `docs/wikimedia-recording-runbook.md` | A short note on content binding. |
| `docs/handoffs/latest.md` | This handoff (separate commit). |

No frontend file, contract or schema changed.

## Things to review

1. **One existing test fed forged data, and was corrected.**
   `test_pageviews_are_stale_exactly_when_a_due_day_is_missing` removed a day from a copy
   of the pinned recording but kept the pinned recording's *verified* provenance. That is
   exactly the gap this checkpoint closes, so the new boundary refused it.

   The test now labels the copy honestly, as `recording_provenance([copy])`, which is
   unverified files. It keeps every staleness and freshness assertion, and adds an
   assertion that claiming verified for the copy is refused. The test was not weakened:
   it now checks one more thing.
2. **Cost.** A verified pageview reading now replays its pinned directory once per
   `assess_evidence` call, to compare content. With the six-article fixture this is cheap.
   The full suite took 7m46s, against 7m37s in U6.7.
3. **A gate rerun, disclosed.** My first full run used `-p no:logging` to cut log noise.
   That flag disables pytest's `caplog` fixture, so 1 test failed and 5 errored in suites
   that use `caplog`. Those suites are unrelated to this change, and they pass under the
   normal plugin set. The reported result is a rerun of plain `uv run pytest`.

## Verified-origin proof

`bind_verified_pageviews(catalogue, recording, provenance, as_of)` does nothing for an
unverified or synthetic input; such inputs are read as they are and counted for nothing.
For a **verified** pageview input it refuses unless every one of these holds:

1. **`require_honest`:** every claimed digest is pinned.
2. **Exactly one named directory**, whose `recording_digest` on disk still equals the
   claimed digest. A pinned digest attached to another directory is refused.
3. **Recorder or legacy.**
   - **A recorder directory** (it has `acquisition/`) must pass `check_recording` with no
     problem and be review-ready: intact, complete, non-empty. When `as_of` is given
     (a point-in-time reading), every response must have been received by `as_of`.
   - **Without acquisition records,** the digest must be the legacy U6.0.1 fixture.
     Otherwise the input is refused.
4. **`material_content(supplied) == material_content(replay of the directory)`.** The
   replay uses the supplied report's window and `observed_at`.
   - **Compared:** sources run; per signal, source, game, signal key, status, error code
     and message; per series, identity, granularity, window and `latest_due`; per point,
     game, source, metric, granularity, period, status, value, reference and source
     timestamp.
   - **Duplicates** are refused, never collapsed.
   - **Ignored:** only replay-time stamps (run start and finish, point `observed_at`, and
     the freshness lag derived from it).

Where it is called:

- **`assess_evidence`** (library boundary): `as_of` is enforced.
- **`opportunity-history`:** `as_of=None`. This is the retrospective reconstruction of
  source periods, unchanged in meaning.

Both share one implementation, and there is no cyclic import: neither the recorder nor
the history evaluator imports `assemble`.

**Limitation, stated plainly.** This binds data to reviewed, pinned bytes. It is not
cryptographic authentication of Wikimedia. Unverified and synthetic inputs are not
content-checked, because they claim nothing. A caller who hand-builds an *unverified*
provenance naming no directory still reads as unverified and counts for nothing.

## Regression results

These tests use offline fixtures, mocks and temporary directories, with sockets blocked
and the live transport replaced.

| | Case | Result |
|---|---|---|
| A | Honest legacy pinned recording with its genuine replay | Accepted, verified, readership counted |
| B | The pinned Wikimedia digest claimed for the catalogue directory | Refused: "no longer has the claimed pinned content" |
| C | Valid pinned directory, one value changed in memory | Refused: "not what the pinned recording holds" |
| D | Game identity changed to an unclaimed id; signal key source changed | Refused |
| E | Failure error code changed; a failed signal dropped; a signal duplicated | Refused (the duplicate as "more than one entry") |
| F | Recorder recording, TEST-ONLY pin, read at its last receipt | Accepted, verified |
| G | The same recording one second before receipt | Refused |
| — | The same pinned recording with a duplicated signal in memory, after receipt | Refused |
| H | A pinned partial recorder recording | Refused ("never verified demand evidence") |
| H | A pinned recorder recording whose body was corrupted, even with the corrupted bytes TEST-ONLY pinned | Refused ("do not hold together") |
| I | A byte-altered copy (`unverified_files`), and a forged unverified label | Read, never counted as verified readership |
| J | Synthetic provenance | Read as synthetic, never counted as verified |
| K and L | U6.5 history, recorded CLI | `ophist_b1d2af287f618f8ab1dbc9634b5b1292`; 546 assessments: 0 / 35 / 511 |
| M | Opportunity, score v0, resolver golden | Unchanged |
| N | Network | No traffic, no fallback |

The U6.5, U6.6 and U6.7.1 history-origin regressions also pass with the same messages:
forged digests, the replay-content mismatch, metadata-only alterations, and partial or
legacy handling.

## Full quality gates (actual)

| Gate | Result |
|---|---|
| `uv run ruff format --check` | exit 0 |
| `uv run ruff check` | exit 0 |
| `uv run mypy` | exit 0, no issues in 246 files |
| `uv run metaspike export-schemas --check` | exit 0, up to date |
| `uv run metaspike evaluate-resolver` | exit 0; golden v3 `c2b3aef7b372`; false AUTO_MATCH merges 0; precision 100.0% (52/52); real-evidence recall 100.0%; NEW_ENTITY 100.0% |
| Targeted suites (see below) | 215 passed |
| `uv run pytest` | **1,673 passed, 8 deselected**, exit 0 (previously 1,668) |

The targeted suites were recorder, history, evidence, opportunity, historical validation,
readiness and v0 parity.

**Frontend gates:** not run, because no frontend code or contract changed. No live test
was run.

## Historical fingerprint parity

`ophist_b1d2af287f618f8ab1dbc9634b5b1292`: 546 assessments (0 candidate, 35 watchlist,
511 insufficient), unchanged (tested through the recorded CLI).

## Frozen contract parity

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

Also unchanged: source policies, `PINNED_RECORDINGS` (`evidence/provenance.py` was not
modified), the recorder CLI, the public artifact contract 1.5.0 and `main`.

## Known limitations

- **Binding is not authentication.** Verified means "equal to bytes this repository
  reviewed and pinned". The review is what vouches for them.
- **The check costs one replay** of the pinned directory per verified reading.
- **Unverified inputs are not content-checked.** They are never counted, so nothing
  depends on them.
- **Pageviews only.** No other source family has a pinned recording, so this binding
  covers pageviews only. Verified context in the history evaluator stays refused, as in
  U6.6.

## Git-only isolation

C2-F isolation: Git-only verification. No GCP access, no production action.

Verified with Git:

- Only `product/local-ui` changed.
- `main` is at `cb33198e2510965e41dff0f5fe7e75cacb7ee2ca`, on both `origin/main` and the
  main worktree.
- No changes under `infra/`, `.github/`, `artifacts/`, `schemas/`, `apps/` or
  `tests/fixtures/`.
- `BROAD_DISCOVERY_ENABLED = False` is unchanged.

## No live calls

No request was sent to Wikimedia or any source. The recorder was not run live.
`.metaspike/recordings` does not exist. Nothing new was pinned.

## No later checkpoint started

Nothing beyond U6.7.2 has been started:

- no live recording;
- no post-soak acceptance;
- no pin;
- no deployment, merge or publication.

Awaiting independent ChatGPT review.
