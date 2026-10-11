# Handoff: latest checkpoint (product branch)

This file is the short-lived handoff from Claude Code to ChatGPT. **This copy lives only
on the branch `product/local-ui`.** The handoff on `main` is the C2-F soak's and was not
touched. It supports communication only and replaces no code, test or document.

Production baseline: **PHASE 4 PART C2-F: ARMED AND SCHEDULED — SEVEN-DAY SOAK PENDING**
(true at 05:00 UTC on 2026-10-11: the first tick is 2026-10-12T00:00Z and no scheduler
attempt has been made)

Parallel checkpoint: **PRODUCT BUILD U3: HISTORICAL VALIDATION COMPLETE — CHATGPT REVIEW REQUIRED**

The validation and accountability system is built, tested, published through the
internal artifact contract and shown at `/validation`. It was run offline on the
recorded replay and the result is reported as it came out: **evidence status
`framework_only`**, and on that sample plain lift orders games at least as well as
score v0. Score v0 and `event_heat_rules_v1` are unchanged.

| | |
|---|---|
| Branch | `product/local-ui` |
| Earlier | U1 `d01331f`; U2 `9cf5564`; U2.1 `ea3a857` |
| U3 commit | `7d40516635d6149da85b7c6518995465115e4d10`, "feat: add historical validation and accountability" |

## Modules added

Backend, `src/metaspike/validation/` (about 1,900 lines with the view mapping):

| File | What it is |
|---|---|
| `rules.py` | `outcome_rules_v1` and `validation_protocol_v1`, every constant with a rationale |
| `history.py` | One recording, two views: `inputs_at` (prediction side) and `future` (outcome side); `ensure_no_future` |
| `snapshot.py` | `RecommendationSnapshot` and `predict`, the only function that makes one |
| `outcomes.py` | `OutcomeLabel` and `label` |
| `metrics.py` | Strategies, top-k, Spearman association, score bins, confidence bands, lead time |
| `report.py` | `ValidationReport`, evidence status, inspection days |
| `backtest.py` | The walk-forward engine and its run-time leakage checks |
| `store.py` | `AccountabilityStore`, append-only |
| `publishing/validation_views.py` | The boundary to the artifact contract |

Also: `docs/historical-validation.md` (permanent), `schemas/validation.schema.json`,
`tests/test_historical_validation.py`, a `backtest` CLI command.

Frontend: `src/components/validation.tsx`, `src/app/validation/page.tsx`,
`src/components/validation.test.tsx`, regenerated `src/contract/`.

## `outcome_rules_v1`, exactly

For a daily signal. `t` is the snapshot's last day, `x[d]` the value on day `d`, `b` the
28-day baseline median the snapshot recorded, `a` the 3-day average it recorded.

| Outcome | Formula |
|---|---|
| **`realized_uplift_72h`** (primary) | `mean(x[t+1], x[t+2], x[t+3]) / b − 1` |
| `movement_24h` | `x[t+1] / a − 1` |
| `persistence_7d` | `count(x[t+k] > b for k in 1..7) / 7` |
| `lead_time_days` | smallest `k` in 1..6 with `x[t+k] > x[t+k−1]` and `x[t+k] ≥ x[t+k+1]` and `x[t+k] > b` |

Positive primary: the next three days ran above the game's known normal level. Exactly
0: they averaged exactly that level. Negative: below it. Not clipped. The reference is
frozen at the snapshot; nothing is computed from the future. Hit: uplift ≥ +0.10.
Downside: uplift ≤ −0.10.

Each measure needs every day of its own window. A missing day, or one the data does not
reach, makes it **unavailable**, never zero and never an average of what is there.
Reasons: `future_incomplete`, `no_reference`, `not_scored_at_snapshot`. A test pins the
rules by fingerprint.

## Leakage protections

Structural:

- `predict` takes a `PredictionInputs` rebuilt for its day; `ensure_no_future` refuses
  any input whose window, observation time or any point lies after the cutoff. It has
  no parameter an outcome could be passed through.
- Inputs are rebuilt from raw observations per day. Features are never computed on the
  whole history and sliced.
- Scoring, features, events, domain, sources and storage do not import the validation
  package. Inside it the prediction modules do not import the outcome modules.
  Publishing may carry a finished report and cannot reach the engine or a label.
- A snapshot has no field for an outcome. A label copies its reference from its
  snapshot.
- Events use only observations made by T, so later observations and revisions cannot
  reach an earlier snapshot.

Run during every backtest, and recorded in the report; a failure raises:

| Check | On the fixture |
|---|---|
| `inputs_end_at_cutoff` | 90 days |
| `reproduced_after_labelling`: recomputed after all outcomes were read, identical ids | 1,350 snapshots |
| `unchanged_when_future_altered`: later values ×7 for the first, middle and last day, identical | 45 snapshots |
| `reference_frozen_at_snapshot` | 1,350 labels |

Tests include a control proving that a computation which did look ahead gives a
different answer, so the equality tests are not vacuous.

## Snapshot and label

**`RecommendationSnapshot`**: `snapshot_id` (content hash), `as_of`, `data_cutoff_day`,
input window, game id, slug, name, lifecycle state, score version, score with
components and status, rank, universe size, ranked count, confidence with factors, the
scored signal and its features, the explanation and reasons, source coverage, event
intelligence at that moment (availability, heat if available, rules version), and
`created_at`, which is outside the id so a rebuild later has the same id. No raw source
payload.

**`OutcomeLabel`**: `label_id`, `snapshot_id`, game id, rules version, `data_through`,
the two reference levels copied from the snapshot, the forward days read with each
observed or not, the primary window, availability and reason, the four measures, and
the peak status.

## Store

`AccountabilityStore`, local and offline: `snapshots/{cutoff day}/`, `labels/{data
through}/`, `reports/`. Records are content-addressed and written once; writing again
adds nothing; nothing is rewritten or deleted. A label made later with more future is a
new record beside the earlier one. The warehouse mapping is documented only; no cloud
resource was created.

## Backtest protocol

For each day T: rebuild inputs as of the end of T; predict from those alone; freeze the
snapshots, one per catalogue game; only then read the days after T and label; join.
The universe at T is the whole catalogue; a game is ranked only if scorable at T, and
unranked games are kept with their reason. What happened to a game later has no bearing
on its rank at T.

## Comparators

Four orderings of the same games on the same day, each judged by the same primary
outcome: `metaspike_score_v0` (the published rank), `raw_popularity` (latest value),
`simple_lift` (3-day average over the 28-day median), `simple_velocity` (3-day average
over the preceding 3 days). Each key is a value the snapshot already held. A missing
key sorts last; ties break by slug. No random baseline.

## Report metrics

Counts; for each strategy the outcomes of its top 1, 3 and 5 (count, mean, median,
minimum, maximum, hit rate, downside rate) and a rank association (mean and median
daily Spearman correlation between position and outcome, with positive and negative
day counts); outcome by fixed score bin and by confidence band; next-day movement,
weekly persistence and lead time; evidence; leakage checks; an events note;
limitations; and the last 60 days' rankings with what followed, for inspection. An
empty summary has no mean. Nothing is resampled or random.

## Evidence-status rule

| Status | Needs at least |
|---|---|
| `framework_only` | anything less than the next row |
| `insufficient_sample` | 30 days, 20 games, 500 outcomes |
| `exploratory` | 90 days, 100 games, 5,000 outcomes |
| `validated` | 180 days, 500 games, 50,000 outcomes, 2 independent sources, and a held-out period |

The status is the highest one whose requirements, and every lower one's, are met.
`validation_protocol_v1` has no held-out period, so it cannot report `validated` at any
sample size; a test checks that with a billion outcomes. The artifact validator and the
UI loader each refuse a report whose status its checks do not support.

## The fixture backtest, exactly as it came out

```
uv run metaspike backtest --replay tests/fixtures/wikimedia \
    --recorded-through 2026-10-03 --from 2026-07-06 --to 2026-10-03
```

- 90 snapshot days. Universe 15 games; **6 have recorded history** and were ever
  ranked. 1,350 snapshots: 420 ranked, 930 unranked. First ranked day 26 July.
- Primary outcome evaluable for 402; unavailable 18 `future_incomplete` and 930
  `not_scored_at_snapshot`.
- **Evidence status `framework_only`**: 6 games where 20 are needed, 402 outcomes where
  500 are needed.

| Strategy | Top 1 mean / hit | Top 3 | Top 5 | Rank association |
|---|---|---|---|---|
| `metaspike_score_v0` | +129.3% / 84% (n=67) | +72.0% / 68% (n=201) | +42.2% / 50% (n=335) | +0.68 (64 positive days, 3 negative) |
| `raw_popularity` | +78.9% / 46% | +42.7% / 47% | +41.6% / 46% | +0.08 |
| `simple_lift` | +127.6% / 84% | +67.3% / 68% | +42.6% / 49% | **+0.78** (67 positive, 0 negative) |
| `simple_velocity` | +90.4% / 63% | +57.1% / 51% | +37.3% / 41% | +0.18 |

What that does and does not say:

- **Score v0 does not beat plain lift here.** Lift matches it at the top and orders the
  six games more consistently. On this sample v0 adds nothing measurable over the lift
  it is largely built from.
- The outcome is lift moved forward three days, so ranking by lift is close to ranking
  by the outcome's own recent past. This measures persistence of attention, not
  foresight and not value to a creator.
- The means are pulled up by a few extremes (largest single outcome +1,046%). Over all
  402: mean +35.2%, **median +5.5%**, 42% hits, 29% downsides, lowest −48.8%.
- By score: exactly 0 → −8.4% (n=120); 0–10 → +1.5% (148); 10–25 → +52.9% (63); 25–50
  → +79.8% (37); 50–100 → +254.7% (34). By confidence: low +43.2% (144), medium +30.7%
  (258), high none.
- Six games, one source, one period, in-sample. None of it generalises, and the report
  says so in its own text.

**Runtime:** about 0.6 s for 90 days × 15 games, including the reproduction and
perturbation passes. Cost is linear in days × games. Larger catalogues were not
measured: by the same arithmetic 1,000 games is about a minute, 5,000 a few minutes,
10,000 several minutes; before that, snapshots would need writing day by day (they are
held in memory now) and the store's duplicate check would need an index.

## Artifact and schema

Internal contract **1.1.0 → 1.2.0**, additive: new `validation.json` and
`manifest.artifacts.validation`. No existing field removed or renamed. With no backtest
the artifact says `availability: "not_run"` with no report and no number. Values are
published as computed; nothing is rounded at the boundary. Schemas came from
`metaspike export-schemas` and the frontend types from `npm run contract:generate`.
Five assertions in four existing test files pinned the old artifact set (file counts,
the bundle builder's parameter list, the version string) and were updated to the new
one; none was loosened.

An ordinary `metaspike run` never performs a backtest. It publishes the latest report
in the store if there is one.

## `/validation`

Private, in the navigation. In order: the evidence status with its conclusion and the
scope note; what was tested; score v0 beside the three alternatives; outcome by score
and by confidence; secondary outcomes; why the status is what it is, requirement by
requirement; the look-ahead checks; the event-backtesting notice; the outcome
definition; a day picker showing that day's ranking and what followed; limitations.
Strategies appear in the artifact's order with no winner marked. A negative or mixed
result renders as it is. "Not run" is its own state with no number.

## Tests added

Backend, 51 in `tests/test_historical_validation.py`, covering: pinned rule
fingerprints; inputs holding nothing after the cutoff; refusal of leaky inputs; the
look-ahead control; future changes and appended days not altering a past prediction;
snapshot id determinism and rebuild; import and signature separation; later events and
revisions not reaching an earlier snapshot; events adding nothing to score v0; exact
primary, 24-hour, 7-day and lead-time boundaries; missing future unavailable, not
zero; negative outcomes kept; the universe at T and unscored preservation;
deterministic ties; comparator ordering; top-k and association against an independent
count; Spearman on known cases; evidence thresholds and the unreachable `validated`;
reproduction of the published score v0; the fixture result; determinism and linearity;
the append-only store and later labels; the artifact, its `not_run` state and its
schema; tampered reports refused; the CLI offline with the network blocked, idempotent,
and with no live mode.

Frontend, 22 new in `validation.test.tsx` (157 in all): formatting; the real replay's
status, counts, strategies, empty strata, requirements, checks, events notice,
limitations and day inspection; that lift's lead over v0 is shown and nothing is
reordered or praised; a synthetic bad result rendered as bad; `not_run`; and refusal of
reports that claim more than they show.

## Results

Backend: `ruff format --check` clean (200 files); `ruff check` clean; `mypy` strict
clean (190 files); `pytest` **1,344 passed**, 5 deselected (was 1,293).

Frontend: `contract:check` current (7 schemas); type check clean; lint clean;
**157 tests** in 10 files (was 135); production build succeeds with `/validation`.

Browser: the production build served on 127.0.0.1 and loaded in headless Chrome;
`/validation` renders the real report. Screenshot:
`apps/dev-ui/docs/screenshots/validation.png` (part of the page). The day picker was
exercised in component tests, not by hand in a browser.

No test contacts a network; the CLI test blocks sockets and passes.

## Unchanged, with proof

- **Score v0**: the pre-U2 golden comparison passes; the backtest's snapshot for the
  last recorded day equals the published score, rank, confidence, features and
  explanation for all 15 games; nothing under `scoring/` or `features/` changed.
- **`event_heat_rules_v1`**: `rules.py` has the same SHA-256 as at U2 (`7358ea65…`), a
  test pins the rules' fingerprint, and nothing under `events/` changed. Event Heat is
  not in score v0; a backtest with synthetic events gives identical scores, ranks and
  outcomes.

## Limits worth knowing before review

- The whole result rests on six games. It validates the framework, not the score.
- The outcome and the score come from the same signal.
- No held-out period, no confidence intervals, overlapping outcome windows.
- Point in time is by day; a recording cannot show later source revisions.
- A game whose data stopped is still scored by v0 on its last observed day with a
  confidence of zero, and so stays ranked. The backtest shows this; it was not changed.
- Real event backtesting is unavailable.

## Production isolation

Read at 05:00 UTC on 2026-10-11:

- `main` and `origin/main` at `cb33198e2510965e41dff0f5fe7e75cacb7ee2ca`. No product
  work merged. Original worktree clean and on `main`.
- Prod Terraform state serial 14, 94 resources.
- Scheduler ENABLED, `0 * 12-18 10 *`, `Etc/UTC`, next run 2026-10-12T00:00:00Z, no
  attempt yet. C2-F has not started: executions are still 35 and the newest is still
  the catch-up of 01:06 UTC. There is no soak execution to separate from U3 activity.
- Control record byte-identical to the launch record, generation `1791684706340016`.
- All seven jobs on `sha256:a8680b2b…c754`. Registry 11 images.
- No IAM, schema, secret, alert or scheduler difference. 0 secret versions, 0 keys.
  Repository variables unchanged. No workflow run since the launch commit's push.
- No Wikimedia, Steam or IGDB call: every pipeline and backtest run replayed recorded
  files. Nothing under `.github/`, `infra/`, `warehouse/` or the lockfiles changed.
  krishnachittella.dev was not modified. Nothing was deployed.

## Branch push

Workflow files are unchanged on this branch, so a push of `product/local-ui` starts no
workflow. **The branch was pushed**; no run followed. No pull request, no merge.

## Exact next approval required

Review of U3. U4 has not been started.

STOP.
