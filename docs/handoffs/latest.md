# Handoff: latest checkpoint (product branch)

This file is the short-lived handoff from Claude Code to ChatGPT. **This copy lives only
on the branch `product/local-ui`.** The handoff on `main` is the C2-F soak's and was not
touched. It supports communication only and replaces no code, test or document.

Production baseline: **PHASE 4 PART C2-F: ARMED AND SCHEDULED — SEVEN-DAY SOAK PENDING**
(true at 19:55 UTC on 2026-10-11: the first tick is 2026-10-12T00:00Z and no scheduler
attempt has been made)

Parallel checkpoint: **PRODUCT BUILD U3.1: POINT-IN-TIME VALIDATION CORRECTED — CHATGPT REVIEW REQUIRED**

Everything here is offline and deterministic. No source was called, nothing was
deployed, and production was only read.

## The bug

`RecordedHistory.inputs_at(cutoff_day)` kept every value through `cutoff_day` and
stamped the prediction at the end of that day. It restricted by the calendar and not by
whether the source had published the period. Wikimedia publishes a day 24 hours after
it ends, so each simulated recommendation used one day nobody could have seen at that
moment, and its outcome window began the day after. That is point-in-time leakage. The
existing checks could not see it: they perturbed only days after the cutoff.

## Corrected semantics

Three times, now three fields on every snapshot:

| Field | Meaning | Fixture example |
|---|---|---|
| `as_of` (P) | The instant the recommendation is simulated | 2026-09-15 00:00 UTC |
| `evaluation_day` | Last UTC day fully ended at P | 2026-09-14 |
| `latest_due_day` | Newest day the source had published by P | 2026-09-13 |

Also `latest_observed_day` (newest day a value was actually held) and
`publication_lag_hours`. `data_cutoff_day` is gone. Snapshot format is
`recommendation_snapshot_v2`; report format `validation_report_v2`.

- **Availability** is `latest_due_period(granularity, P, lag)`, the pipeline's own
  function, with the lag from each source's policy
  (`operations.expected_publication_lag`). It is applied where a recording becomes
  inputs (`inputs_at(as_of: datetime)`) and checked again where inputs are used
  (`ensure_available`, called by `predict`). Feature and scoring code are untouched and
  know nothing of any source. A lag must be stated for every source; there is no
  default.
- **Outcome window** starts at `first_forward_day(as_of)`: the day beginning at P when
  P is exactly on a UTC boundary, otherwise the following day. 72h is the next three
  whole forward days; 24h, 7d and local-peak use the same start. A label records
  `prediction_at` and is refused if its window starts before it.
- The day that had ended but was not due is in neither side. Its only use is on the
  outcome side, as the "day before" when deciding whether the first forward day rose.
- The backtest's `--from`/`--to` are prediction days, each at 00:00 UTC. A day the
  recording cannot stand in for is refused with a clear error.

## New invariant and checks

At P, no observation whose period was not due by P under its source's lag may enter a
prediction. A violation raises `LeakageError`; no report can be produced from it.

Five checks run in every backtest (was four):

| Check | Checked on the fixture |
|---|---|
| `source_publication_lag_respected` (new; replaces `inputs_end_at_cutoff`) | 546 series |
| `outcome_window_after_prediction` (new) | 1,365 |
| `reproduced_after_labelling` | 1,365 |
| `unchanged_when_unseen_values_altered` (renamed from `unchanged_when_future_altered`; now also alters ended-but-unpublished days) | 45 |
| `reference_frozen_at_snapshot` | 1,365 |

The artifact validator and the UI's validator additionally refuse a report without the
lag check, with a recommendation that sees its own day, with an outcome window before
its recommendation, with no lag stated, or with a malformed provenance hash.

## Regression test

`test_a_period_that_had_ended_but_was_not_yet_published_is_withheld`: P = 00:00 on day
61, lag one day. Day 59 (X) is due; day 60 (X+1) is in the recording with 9,000,000
and is not due. Asserts X+1 is absent (null, not zero), the newest feature day is X,
and the snapshot (score, rank, confidence, id) is identical whether X+1 is 90 or
9,000,000. It then applies the old calendar-only rule to the same data and shows the
value does get in and the score changes, and that such inputs are refused when the true
lag is stated.

Stated plainly: the old code was not checked out and run against this test, because
its interface no longer exists; the old behaviour is reproduced inside the test.

Companion tests: zero lag; the exact due boundary; one second before and after; a
mid-day P taking the next whole day; two sources with different lags; a not-yet-due day
is missing and never zero; the first outcome day follows the recommendation, not the
data. The U2 regression that a later event revision cannot rewrite an earlier
recommendation is kept and passes; events are evaluated at the exact P. Event Heat was
not calibrated.

## Provenance

`snapshot_set_hash` and `label_set_hash`: SHA-256 over the sorted record ids joined by
newlines. Same set, same hash; one changed record, different hash (tested both ways,
including same snapshots with a different future changing only the label hash). Both
feed the `report_id`. `AccountabilityStore.audit(report)` recomputes them from stored
records; the `backtest` command prints the result. Published in `validation.json` and
shown in a compact section on `/validation`. Ids and hashes only, no observations.

**Internal schema 1.2.0 → 1.3.0**, Python contract → JSON Schema → generated
TypeScript. Renamed `first/last_snapshot_day` to `first/last_prediction_day`; added
`first/last_latest_due_day`, `publication_lag_hours`, the two hashes; inspection days
gain `latest_due_day`, `primary_window_start`, `primary_window_end`. Published outcome
formulas are restated relative to the recommendation (`x[f]`, `[P]`).

## Corrected real fixture result

`uv run metaspike backtest --replay tests/fixtures/wikimedia --recorded-through
2026-10-03 --from 2026-07-07 --to 2026-10-05`

- 91 prediction days at 00:00 UTC; newest visible day 2026-07-05 to 2026-10-03; lag
  wikimedia_pageviews 24 h.
- Universe 15; 6 ever ranked; 1,365 snapshots (420 ranked, 945 unranked).
- Evaluable 396; unavailable: `future_incomplete` 24, `not_scored_at_snapshot` 945.
- **Evidence status `framework_only`**, set mechanically by `validation_protocol_v1`.
- 5 of 5 checks passed.
- `report_ab3be39389de0313637e9518d2569722`
- `snapshot_set_hash` `8a0f192d11aac11fd92d37736598cb3cd7ea130c253c84cac5efeb86770ee748`
- `label_set_hash` `6b8aa3ce15dbe68977bf1bd68778d4ed65f0e2cfd704a94af7adf70d182cabdf`

Mean / median / hit rate (top 1 n=66, top 3 n=198, top 5 n=330); association over 66 days:

| Strategy | Top 1 | Top 3 | Top 5 | Rank association |
|---|---|---|---|---|
| `metaspike_score_v0` | +114.4% / +50.1% / 82% | +66.6% / +30.0% / 68% | +41.4% / +10.7% / 51% | +0.62 (60 pos, 6 neg) |
| `raw_popularity` | +77.9% / +5.4% / 47% | +41.0% / +9.2% / 48% | +42.6% / +8.9% / 47% | +0.06 (29, 37) |
| `simple_lift` | +112.6% / +51.3% / 80% | +62.8% / +25.9% / 68% | +41.2% / +9.5% / 50% | +0.71 (64, 2) |
| `simple_velocity` | +74.3% / +35.4% / 67% | +56.9% / +10.5% / 51% | +39.0% / +5.1% / 43% | +0.15 (41, 25) |

All ranked (n=396): mean +36.6%, median +6.1%, min −49.0%, max +1,045.5%, hits 43.9%,
downsides 28.3%. By score: 0 → −0.1% (117); 0–10 → +6.2% (146); 10–25 → +52.3% (62);
25–50 → +76.5% (37); 50–100 → +221.2% (34). Confidence: low +47.0% (144), medium +30.6%
(252), high none. Movement 24h: mean +8.5%, median −1.9% (408). Persistence: mean 0.62,
median 0.86 (372). Peak: found 259, none 113, unavailable 48; median 3 days.

**Plain lift still orders the games more consistently than score v0** (+0.71 against
+0.62). Score v0's top pick has a marginally higher mean and a marginally lower median.

## Against U3's provisional figures (superseded)

| | U3 (leaky) | U3.1 |
|---|---|---|
| Days / snapshots | 90 / 1,350 | 91 / 1,365 |
| Evaluable / future incomplete | 402 / 18 | 396 / 24 |
| v0 top 1 mean / hit | +129.3% / 84% | +114.4% / 82% |
| v0 top 3 | +72.0% / 68% | +66.6% / 68% |
| v0 top 5 | +42.2% / 50% | +41.4% / 51% |
| v0 association | +0.68 | +0.62 |
| lift top 1 | +127.6% / 84% | +112.6% / 80% |
| lift association | +0.78 | +0.71 |
| popularity association | +0.08 | +0.06 |
| velocity top 1 / association | +90.4% / +0.18 | +74.3% / +0.15 |
| All ranked mean / median | +35.2% / +5.5% | +36.6% / +6.1% |

Leading-group means and both main rank associations fell once the unpublished day was
removed. The qualitative reading did not change.

## Runtime

Measured: the fixture run (91 days × 15 games) took 0.58 to 0.59 s on a laptop,
including reproduction and perturbation. Nothing else has been measured. Complexity is
O(prediction days × games). The unmeasured timing claims for 1,000 to 10,000 games are
removed. Bottlenecks noted, not addressed: results held in memory until the end; the
store reads every record on each add and audit; each day re-filters the recording.

## Frozen parts, unchanged

| | |
|---|---|
| `src/metaspike/scoring` (concatenated sources, SHA-256 prefix) | `3b857c551d5ab5b0`, as at U3.1 start |
| `src/metaspike/features` | `24cf7d170f57d8ab`, same |
| `src/metaspike/events` | `333373c61ae6c37f`, same |
| `events/rules.py` | `7358ea6563ddcad9`, same |
| Pinned model fingerprints (event heat rules, outcome rules, validation protocol) | tests unchanged and passing |
| Score v0 golden replay | bit-for-bit; also reproduced by the backtest with zero lag |

`git diff a50b678 -- src/metaspike/scoring src/metaspike/features src/metaspike/events`
is empty.

## Found along the way

- A local store holding U3-format records made `metaspike run` fail. The store now
  keeps each simulation generation apart (`{root}/point-in-time-v2/…`); older records
  stay on disk and are never read as current. Tested.
- An unreachable backtest range was an unexpected failure; it is now a clean error.

## Gates

Backend: `ruff format --check` clean (200 files); `ruff check` clean; `mypy` clean (190
files); `pytest` 1,356 passed, 5 deselected (the slow marker, as before).
Frontend (`apps/dev-ui`): `contract:check`, `typecheck`, `lint`, `test` (163 passed),
`build` all pass.
Not done: the page was not opened in a real browser this checkpoint; it is covered by
the component tests against the real replay bundle and by the production build.

## Production isolation (read-only, 19:55 UTC 2026-10-11)

C2-F has **not begun**: no autonomous activity to report. Scheduler ENABLED,
`0 * 12-18 10 *`, `Etc/UTC`, next run 2026-10-12T00:00:00Z, no attempt. Control record
generation `1791684706340016`, unchanged. Executions 35, last `ms-warehouse-lxqp9` at
2026-10-11T01:06Z. Seven jobs, generation 6, image `sha256:a8680b2be8b5…`. Ten alert
policies; IAM, schemas, secrets, alerts identical to the last snapshot. 0 secret
versions, 0 user keys. Rows unchanged (collection_runs 54, observations 1,372, ledger
28). Repository variables unchanged. `main` = `cb33198`, level with origin. Last
workflow run is still 38104941975. No pull request exists. The portfolio repository
was not touched.

## Commits

`4333284` fix: make historical validation publication-lag safe; then this handoff.
Pushed to `origin/product/local-ui` only. No PR, no merge to `main`.

## Exact next approval required

ChatGPT review of U3.1.

STOP. U4 has not been started.
