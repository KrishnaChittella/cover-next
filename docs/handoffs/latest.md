# Handoff: latest checkpoint (product branch)

## PRODUCT BUILD U6.5: OFFLINE HISTORICAL OPPORTUNITY EVALUATION — CHATGPT REVIEW REQUIRED

This checkpoint was worked on branch `product/local-ui` only, entirely offline:

- no live source call, credential, cloud access or deployment;
- no pull request, merge or publication.

**No later checkpoint has been started.**

## Things to review first

1. **On recorded data the rubric reaches no candidate, so candidate accuracy is NOT
   ESTIMABLE — NO POSITIVE CANDIDATES.** This was recomputed, not restated. Nothing but
   readership was recorded before October, so no moment can have an angle or a coverage
   sample. Creator-video success cannot yet be validated from the recorded data.
2. **"Watchlist 34 of 34 positive" is not evidence of foresight.** The 35 watchlist
   assessments are 10 overlapping episodes over 6 games. `outcome_rules_v1` compares the
   next three days with the same 28-day baseline the movement already exceeded, so a
   rise under way that merely persists counts as positive. The report states this
   whenever the watchlist is non-empty. The rule is unchanged; a fix would need a new,
   separately approved outcome-rules version.
3. **A behaviour-preserving extraction in `opportunity/rules.py`.** `movement_direction`
   and `ahead_of_next` were lifted out of `_movements` and `_order` unchanged, so the
   historical track uses the rubric's own code instead of a copy. The logic and the
   wording are byte-identical, and every U6.3 test passes unchanged.
4. **Three dependency allow-list tests were extended, not weakened.**
   - `test_evidence`, `test_creator_supply` and `test_community_interest` list the modules
     allowed to import those packages.
   - They now also name `opportunity/history*.py`, which import the pageview cut, the
     provenance labels and the YouTube and Reddit policies. The Reddit policy is imported
     in order to refuse.
   - `test_evidence` now compares a sorted list.
5. **The exploratory sensitivity counts decisions only.** No outcome is attached to any
   alternative threshold.

## 1. Approved starting SHA

`5c7224521b1d661f166f2ea3fa24f642fd87cb14`. Verified at the start: on
`product/local-ui`, at that commit, tree clean.

## 2. Implementation SHA

`5c2a724713b95a9b590212a5c2d8294ff88062fb`: `feat: evaluate creator opportunities
historically offline`.

## 3. Exact files changed

| File | Change |
|---|---|
| `apps/dev-ui/src/private/opportunities/validate.ts` | Uniqueness, exact-set and simulated-readiness checks |
| `apps/dev-ui/src/private/opportunities/loader.test.ts` | 2 tests: refusals, valid reports still accepted, load refused |
| `apps/dev-ui/src/components/opportunities.test.tsx` | 1 test: an invalid report renders the refusal and no entries |
| `src/metaspike/opportunity/history.py` | New: snapshot, outcome and report models; the walk; metrics; render |
| `src/metaspike/opportunity/history_synthetic.py` | New: invented games and evidence, labelled SYNTHETIC |
| `src/metaspike/opportunity/rules.py` | `movement_direction`, `ahead_of_next` extracted; behaviour unchanged |
| `src/metaspike/cli.py` | `opportunity-history` command |
| `tests/test_opportunity_history.py` | New: 25 tests |
| `tests/test_evidence.py`, `test_creator_supply.py`, `test_community_interest.py` | Allow-lists name the new modules |
| `docs/creator-opportunity-historical-evaluation.md` | New |
| `docs/creator-opportunity-intelligence.md` | Pointer to it under "Calibration still needed" |
| `docs/handoffs/latest.md` | This handoff (separate commit) |

## 4. U6.4 validator correction

`checkReport` still runs the version check, the schema and the distribution check first,
and still checks each entry's id, position and verified readiness. It now also refuses:

- a duplicate canonical id among the assessments;
- a duplicate id in `entries` or `simulated_entries`;
- either list not being exactly the assessed set;
- a simulated entry whose readiness differs from its assessment's `simulated_readiness`.

It never corrects a report and never computes an order.

Each case has a frontend test:

- duplicate assessment ids;
- duplicate verified ids;
- duplicate simulated ids;
- a game missing from either list;
- inconsistent simulated readiness;
- valid recorded and synthetic reports still accepted;
- the loader returning `invalid`, with no report;
- the refusal panel rendering with no shortlist rows.

## 5. Historical evaluation design

`evaluate_history` takes:

- the catalogue and the recording;
- the publication lags;
- explicit first and last days, and a cadence;
- a bounded universe of slugs (at most 50);
- the demand provenance and the mode;
- optional dated context observations;
- optional binding start times.

The walk:

1. For each declared day, the moment is 00:00 UTC. A moment `RecordedHistory` cannot
   reconstruct is **unavailable** and makes no prediction.
2. Otherwise it builds `inputs_at(moment)`, runs `predict()` to get the
   `recommendation_snapshot_v2` for every catalogue game, and reads each universe game's
   readership through `wikimedia_family_v2(series, pageviews_read_at(series))`. That is
   the evidence layer's own computation.
3. It applies the rubric's `movement_direction`, `readiness`, `ordering_key` and
   `ahead_of_next`.
4. Each game's result is frozen as an **`opportunity_snapshot_v1`** with a content
   fingerprint, `opsnap_…`. The snapshot carries:
   - readiness, facts and movements;
   - verified and counted families;
   - why-now statements;
   - angle availability and references;
   - creator-coverage completeness;
   - missing families with reasons;
   - origins and their standing;
   - reconstruction status;
   - position and reason;
   - the id of the base `recommendation_snapshot_v2`.
5. Only after every moment is frozen is the future read. The outcome records
   (`opout_…`) are attached by fingerprint.

The report is `opportunity_history_report_v1`, fingerprinted as `ophist_…`. It carries
no wall-clock time.

There are two modes, and their inputs are never mixed:

- **recorded:** verified-only counting;
- **synthetic:** invented inputs counted as if verified, labelled throughout.

## 6. Existing validation components reused

| Area | Reused |
|---|---|
| Walk and inputs | `RecordedHistory`, `inputs_at`, `ensure_available` (inside `inputs_at`), `PredictionInputs`, `days_between`, `prediction_moment`, `latest_due_at`, `first_forward_day` |
| Snapshots and outcomes | `predict`, `RecommendationSnapshot`, `label`, `OutcomeLabel`, `OUTCOME_RULES_V1` (thresholds +0.10 and −0.10), `content_id` |
| Leakage check | `perturb_unavailable` (in tests) |
| Rubric | `readiness`, `Facts`, `MovementNote`, `Statement`, `ordering_key`, `NOTEWORTHY_ROBUST_Z`, `TIMELY_DISCUSSION` |
| Evidence | `pageviews_read_at`, `wikimedia_family_v2`, `InputProvenance`, `recording_provenance` |
| Policy | `collection_refusal`, `registration_refusal` |

`validation_protocol_v1`, `outcome_rules_v1` and `recommendation_snapshot_v2` are
unchanged.

## 7. Prediction/outcome separation

- **Snapshots are frozen before the future is read**, and their fingerprints exclude all
  outcomes. Rewriting attention after a moment changes the outcome and leaves the snapshot
  byte-identical (tested).
- **No look-ahead.** Multiplying every value not yet published at a moment by seven
  changes no snapshot at or before it (tested).
- **The four instants stay apart:**
  - `as_of` is 00:00 UTC on the declared day;
  - `evaluation_day` is the day before;
  - `latest_due_day` is the day before that (a one-day lag), and is never earlier than
    `latest_observed_day`;
  - the outcome window starts at `first_forward_day(as_of)`, after the evaluation day
    (tested for every snapshot).
- **Retrieval time governs.** Context counts only if `retrieved_at <= moment`; otherwise
  it is missing with `observed_only_after_the_moment`. This applies to events, creator
  samples and discussions alike (tested).
- **Rebinding.** Before a binding begins, a game is `identity_not_bound_at_the_moment`,
  with no demand reading and an unavailable outcome. From the binding on, the result is
  identical to an always-bound game (tested).

## 8. Recorded fixture coverage

The pinned Wikimedia recording `tests/fixtures/wikimedia` (verified, digest pinned in
U6.0.1) covers 6 July to 3 October 2026 with a one-day lag.

The seed catalogue's games with a recorded series are:

- escape-from-tarkov;
- gta-v;
- gta-vi;
- minecraft;
- no-mans-sky;
- star-citizen.

The other 8 seed games have no pageview data in the recording.

The requested range was 5 July to 7 October: 95 moments.

| Moments | Count |
|---|---|
| Reconstructed | 91 (7 July to 5 October) |
| Unavailable | 4 (5–6 July, before the recording; 6–7 October, due days not held) |

Before 5 October nothing was recorded for:

- Steam players;
- events;
- YouTube;
- Reddit.

Those families are missing at every moment with a reason, and none was invented or
backdated. The local Steam News probe store (11 October, unverified) postdates every
moment and is not read.

## 9. Actual readiness distribution (recorded)

546 assessments:

| Readiness | Count |
|---|---|
| Candidate | 0 |
| Watchlist | 35 (10 episodes over 6 games) |
| Insufficient | 511 (abstention 93.6%) |

Readership was available for 540 of 546. All 6 without it were at the first
reconstructed moment, 7 July, which had a single day of history. Each other family was
available for 0 of 546.

At 5 October every game is flat. The largest |z| is GTA V at −1.86, matching U6.3. A test
checks that the history's readings at 5 October 12:00 equal the live evidence path's for
every game.

## 10. Actual attention-proxy results (recorded)

These are labelled ATTENTION PROXY throughout.

| Group | Positive | Negative | Neither | Forward-incomplete | Unavailable |
|---|---|---|---|---|---|
| Watchlist | 34 | 0 | 0 | 1 | 0 |
| Insufficient | 140 | 112 | 110 | 23 | 126 |

All 126 unavailable outcomes are `not_scored_at_snapshot`, at moments from 7 to 27 July,
when score v0 did not yet have enough history.

Positive shares, with no interval reported:

- watchlist: 34/34;
- insufficient: 140/362.

For the caveat on the watchlist result, see "Things to review first", item 2.

**Exploratory sensitivity**, as decision counts only, with no outcome attached:

| Robust z | Changed decisions | Candidate | Watchlist | Insufficient |
|---|---|---|---|---|
| 2.5 | 26 | 0 | 61 | 485 |
| 3.0 | 11 | 0 | 46 | 500 |
| 4.0 | 8 | 0 | 27 | 519 |
| 5.0 | 13 | 0 | 22 | 524 |

No threshold yields a candidate.

Report fingerprint: `ophist_b1d2af287f618f8ab1dbc9634b5b1292`. It is identical across
runs.

## 11. Metrics not estimable

- **Candidate accuracy:** NOT ESTIMABLE — NO POSITIVE CANDIDATES.
- **Creator-video success:** cannot yet be validated from the recorded data. There is no
  video performance outcome at all.
- **Confidence intervals:** none reported anywhere. The observations are too few, and are
  not independent.
- **A share over an empty denominator** reads NOT ESTIMABLE, never 0% or 100% (tested,
  including a run with no reconstructable moment).

## 12. Synthetic test results

The synthetic run uses six invented games, `synthetic-*`, with ids far from any real one.
Everything is labelled SYNTHETIC, and the report opens with that statement.

`metaspike opportunity-history --synthetic` gives 15 moments, of which 14 were
reconstructed and 1 unavailable.

| Readiness | Count | Outcomes |
|---|---|---|
| Candidate | 7 | 5 positive, 2 negative |
| Watchlist | 35 | |
| Insufficient | 42 | |

Overall there are 24 forward-incomplete outcomes. Report fingerprint:
`ophist_a3f196593341a1b25623346132ce06a6`.

Tests cover each required case:

- a candidate followed by positive attention, and one followed by negative attention;
- a watchlist with positive and with negative outcomes;
- an insufficient assessment;
- an incomplete forward period;
- a missing source observation;
- a later rebinding;
- an event observed only afterwards;
- video statistics retrieved afterwards, and an incomplete sample;
- Reddit retrieved afterwards, and a stale discussion;
- a changed outcome leaving a frozen snapshot unchanged.

These tests validate the software only. They establish nothing about prediction quality.

## 13. Source, time and provenance constraints

- **Stage:** private development only; any other stage is refused.
- **Mixing:** synthetic and recorded inputs are refused together, in both directions.
- **Reddit:** non-synthetic community data is refused under `collection_refusal`, because
  approval is pending.
- **YouTube and Steam News:** non-synthetic data must clear `registration_refusal`.
- **Universe:** bounded, known and distinct.
- **`--out`:** refused inside `artifacts/` or any `public` directory.
- **No network:** none in either mode (tested with sockets blocked).
- **No backdating:** a verified standing is never granted retroactively. The
  `generated_at` guard and the identity constraints are not touched, and this track never
  calls `assess_evidence`.
- **Stated assumption:** seed-catalogue bindings are taken as given, as the existing
  backtest does.

## 14. Full backend quality gates (actual results)

| Gate | Result |
|---|---|
| `uv run ruff format --check` | exit 0 |
| `uv run ruff check` | exit 0 |
| `uv run mypy` | exit 0, no issues in 241 files |
| `uv run metaspike export-schemas --check` | exit 0, up to date |
| `uv run metaspike evaluate-resolver` | exit 0; golden set v3 `c2b3aef7b372`; AUTO_MATCH precision 100.0% (52/52); real-evidence recall 100.0%; NEW_ENTITY 100.0% |
| Targeted suites (see below) | 199 passed |
| `uv run pytest` | **1,618 passed, 8 deselected**, exit 0 (previously 1,593) |

The targeted suites were `test_opportunity_history`, `test_opportunity`,
`test_historical_validation`, `test_evidence`, `test_creator_supply`,
`test_community_interest` and `test_v0_parity`.

An earlier full run, before the allow-list update, failed exactly the three allow-list
tests in item 4 of "Things to review first". The run above is on the committed tree.

## 15. Full frontend quality gates (`apps/dev-ui`, actual results)

| Gate | Result |
|---|---|
| `npm run contract:check` | exit 0 |
| `npm run opportunities:schema:check` | exit 0 |
| `npm run typecheck` | exit 0 |
| `npm run lint` | exit 0 |
| `npm test` | exit 0, **197 passed** (13 files; previously 194) |
| `npm run build` | exit 0 |

## 16. Frozen-rule parity

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
- creator_opportunity_rules_v1, with thresholds 3.5 and 7 days (asserted);
- source policies;
- public artifact contract 1.5.0.

Golden labels were not touched. Score v0 parity, the U6.3 rubric tests and the
historical-validation tests all pass unchanged. The base score v0 snapshots made here have
ids identical to `run_backtest`'s own (tested for 28 September to 5 October).

## 17. Known blockers for calibration

- **No point-in-time history** for creator supply, community, events or player samples.
  Their collection needs policy clearance, and Reddit approval is pending.
- **No creator-video performance outcome** of any kind.
- **Six games, ten watchlist episodes and no candidates.** That is far too few for
  intervals or for choosing a threshold.
- **`outcome_rules_v1` rewards persistence** for movement-triggered assessments. A
  pre-movement baseline would need a new, approved outcome-rules version.
- **No held-out period.** Any revised rule would need one, under a new rubric version.

## 18. Git-only C2-F isolation

C2-F isolation: Git-only verification. No GCP access, no production action.

Verified with Git before committing:

- Only `product/local-ui` changed.
- `main` is untouched: `origin/main` and the main worktree are both at
  `cb33198e2510965e41dff0f5fe7e75cacb7ee2ca`.
- No changes under `infra/`, `.github/workflows/`, `artifacts/`, `schemas/` or any
  `public/` directory.
- `BROAD_DISCOVERY_ENABLED = False` is unchanged.
- No deployment and no live source call.

## 19. No later checkpoint started

Nothing beyond U6.5 has been started:

- no source activation;
- no publishing;
- no infrastructure change;
- no new product checkpoint.

Awaiting independent ChatGPT review and explicit user authorisation.
