# Historical validation and recommendation accountability

**Status: INTERNAL.** This describes how MetaSpike checks its own past rankings, and
what the one check run so far shows. That check is on a recording of six games. It
demonstrates that the method works and does not look ahead. It is not evidence that
`metaspike_score_v0`, or anything else here, predicts demand.

Code: `src/metaspike/validation/`. Published views:
`src/metaspike/publishing/validation_views.py`.

## The question

When MetaSpike would have ranked a game highly at a moment P, what happened after P?

To answer it honestly, the ranking at P has to be exactly the one that could have been
made at P, from what its sources had actually published by P; and what happened
afterwards has to be measured only on time after P, against what was known at P.

## Three times that are not the same

A simulated recommendation has three different times attached to it. The first version
of this backtest treated them as one, and that was a point-in-time error (see
"What was corrected" below).

| | Meaning | Example, Wikimedia (24 h lag) |
|---|---|---|
| **Prediction moment** `as_of` (P) | The instant the recommendation is simulated to have been made | 2026-09-15 00:00 UTC |
| **Evaluation day** | The last UTC day that had fully ended at P | 2026-09-14 |
| **Latest due day** | The newest day the source would have **published** by P | 2026-09-13 |

A period has **ended** when its last instant has passed. It is **due** when its source
would have published it: the end of the period plus that source's publication lag. A
day that has ended but is not yet due did happen, but nobody outside the source could
have seen its value at P, so a recommendation at P may not use it.

    due(day) = end of day + source publication lag
    latest due day at P = the newest day with due(day) <= P

The lag is each source's own policy figure
(`SourcePolicy.operations.expected_publication_lag`), read through the same
`latest_due_period` the collection pipeline uses for freshness. Nothing in the feature
or scoring code knows about any particular source: the rule is applied where a
recording is turned into inputs (`RecordedHistory.inputs_at`) and checked again where
inputs are used (`ensure_available`, called by `predict`). Sources with different lags
are each held to their own; a source with no stated lag is refused, not assumed to be
instant.

A snapshot therefore answers two separate questions directly: *when was this
recommendation simulated?* (`as_of`) and *what was the newest demand day MetaSpike
could actually see at that moment?* (`latest_due_day`, and `latest_observed_day` for
the newest day it really held a value for).

## Two sides that never meet

| | Prediction side | Outcome side |
|---|---|---|
| Sees | Observations whose period was due by P, and events known by P | Whole days beginning at or after P |
| Produces | A frozen `RecommendationSnapshot` per game | An `OutcomeLabel` per snapshot |
| Code | `history.inputs_at`, `snapshot.predict` | `history.future`, `outcomes.label` |

The separation is structural, not a convention:

- `predict` takes a `PredictionInputs`, which is rebuilt for the moment and refused if
  it holds any period not yet due, any observation made after P, or a window reaching
  past the evaluation day. It has no parameter through which an outcome, a label or a
  future value could be passed.
- The scoring, feature and event engines do not import the validation package at all.
  Inside it, the prediction modules do not import the outcome modules.
- A snapshot has no field for an outcome. A label points at a snapshot by id and copies
  its reference levels from it; it never recomputes them.
- Inputs are rebuilt from raw observations for each moment. Features are never computed
  on the whole history and read off for a past day, which is how a future value leaks
  into a past baseline or percentile.
- A day that is not yet due is **absent** from the inputs. It is not a zero and not a
  gap to be filled.

## A snapshot

`RecommendationSnapshot` (`recommendation_snapshot_v2`) records what MetaSpike knew and
said about one game at one moment: `as_of`; `evaluation_day`; `latest_due_day`;
`latest_observed_day`; the publication lag applied, in hours; the input window; the
game; the score version, the score, its components and status; the rank among games
scored at that moment, with the universe size and ranked count; confidence and its
factors; the features of the scored signal; the explanation and its reasons; source
coverage; and event intelligence as it stood then (availability, heat if available,
rules version).

It holds computed values and versions, never a source's raw payload. Its
`snapshot_id` is a hash of its content. `created_at`, the moment the evaluation was
run, is outside the hash: the same prediction rebuilt later has the same id, which is
how reproduction is checked.

## `outcome_rules_v1`

For a daily demand signal. Let P be the prediction moment and `f` the **first whole UTC
day beginning at or after P**: the day starting at P when P falls exactly on a UTC day
boundary, otherwise the following day. `x[d]` is the value on day `d`; `b` is the
28-day baseline median the snapshot recorded and `a` the 3-day average it recorded,
both from days that were due at P.

| Outcome | Formula | Reading |
|---|---|---|
| **`realized_uplift_72h`** (primary) | `mean(x[f], x[f+1], x[f+2]) / b − 1` | Positive: the three days after the recommendation ran above the game's known normal level. Exactly 0: they averaged exactly that level. Negative: below it. Not clipped |
| `movement_24h` | `x[f] / a − 1` | The first day after the recommendation against the recent level already known |
| `persistence_7d` | `count(x[f+k] > b, k = 0..6) / 7` | Share of the following week spent above the known normal level |
| `lead_time_days` | `1 +` smallest `k` in 0..5 with `x[f+k] > x[f+k−1]`, `x[f+k] ≥ x[f+k+1]` and `x[f+k] > b` | Days to the first confirmed local peak above normal; 1 is the first forward day. None in the window is a finding |

The windows are anchored on the **prediction moment**, not on the newest day of data.
With a one-day lag there is a day between the two: the day that had ended but was not
yet published. It is in neither the inputs nor any outcome window. Its only use is on
the outcome side, as the "day before" when deciding whether the first forward day was
a rise.

The constants of `outcome_rules_v1` are unchanged and its pinned fingerprint is the
same; what changed is the day the windows start from.

Thresholds: a **hit** is an uplift at or above +0.10; a **downside** is one at or below
−0.10. Both are stated priors, symmetric on purpose.

The primary outcome is the snapshot's own lift measure moved forward three days,
against a baseline frozen at the snapshot. No baseline is ever computed from the
future.

**Unavailable is not zero.** Each measure needs every day of its own window. A day that
is missing, or that the data does not yet reach, makes that measure unavailable. It is
never filled with zero and never averaged over what happens to be there. Reasons:
`future_incomplete`, `no_reference`, `not_scored_at_snapshot`.

The rules were fixed before any backtest was run. They are not to be tuned to make a
score look good: changing a value is a new version, and a test pins the current one.

## The walk-forward protocol

For each prediction day D in the range, in order, with P = 00:00 UTC on D:

1. rebuild the inputs as they stood at P: only periods due by P under each source's lag;
2. compute features, score, confidence, rank and event intelligence from those alone;
3. freeze the result as snapshots, one per game in the universe;
4. only then read the whole days from P onwards and label each snapshot;
5. join snapshot and label.

**The universe at P** is every game in the catalogue. A game is ranked only if it was
scorable at P; the rest are kept as unranked, with the reason. What became of a game
later has no bearing on whether it was ranked at P.

**What a recording can stand in for.** A recording made after the fact holds values but
not when each was published, so availability is reconstructed from the source's policy
lag. That lag is a stated figure, not a measured publication time, and values a source
later revised are not reconstructable from a recording. A backtest refuses a prediction
day the recording cannot stand in for: one before its window, or one at which a day it
does not hold would already have been due. An observation store that keeps what was
observed when gives the same interface with real publication times and revisions.

**Events** are already point-in-time and are evaluated at the same exact P: only
observations made by P are used, so an event or a revision seen later cannot change
event intelligence at P.

### Checks that are run, not assumed

Every backtest runs these and records the result in its report:

| Check | What it does |
|---|---|
| `source_publication_lag_respected` | Every series given to a prediction held no period its source had not yet published at P, under that source's lag; and no prediction rests on a day later than its source's latest due day |
| `outcome_window_after_prediction` | Every outcome window starts on the first whole day at or after P, and after the newest day the prediction used |
| `reproduced_after_labelling` | After every outcome had been read, each prediction was computed again from scratch: identical snapshot ids |
| `unchanged_when_unseen_values_altered` | For the first, middle and last day, every value not yet published at P was multiplied by 7 and the prediction recomputed: identical. This includes days that had ended but were not yet due |
| `reference_frozen_at_snapshot` | Every label's reference is the one its snapshot recorded |

`source_publication_lag_respected` replaces the first version's `inputs_end_at_cutoff`,
whose name described a calendar cutoff and not availability.
`unchanged_when_unseen_values_altered` replaces `unchanged_when_future_altered`, which
perturbed only days after the cutoff and so could not notice an ended-but-unpublished
day being used.

A failure raises; a report cannot be produced from a backtest in which any prediction
held a period that was not due. The artifact validator and the developer UI each
refuse a report that lacks the lag check, shows a recommendation seeing its own day,
or shows an outcome window starting before its recommendation. The tests include
controls showing that a computation which did look ahead gives a different answer, so
the equalities above are not vacuous.

## What was corrected

The first version rebuilt inputs "through day T" and stamped the prediction at the end
of T. For a source that publishes a day 24 hours after it ends, day T's value did not
exist for anyone at that moment. Every prediction therefore used one day it could not
have seen, and the outcome window started the day after it. That is point-in-time
leakage, and results from that version were provisional.

`tests/test_historical_validation.py::test_a_period_that_had_ended_but_was_not_yet_published_is_withheld`
is the regression: day X is due, day X+1 is in the recording with an enormous value
but is not due at P. It asserts X+1 is absent from the inputs, that features anchor on
X, and that score, rank and confidence are identical with and without the X+1 value;
and it shows that the old calendar-only rule does let the value in and changes the
score, so the test fails under the first version. Companion tests cover zero lag, the
exact due boundary, one second either side of it, a mid-day prediction moment, two
sources with different lags, and that a not-yet-due day is missing and never zero.

## Comparators

The score has to be compared with something. Four ways of ordering the same games on
the same day, each judged by the same primary outcome:

| Strategy | Order |
|---|---|
| `metaspike_score_v0` | The published rank |
| `raw_popularity` | Latest observed daily value, highest first |
| `simple_lift` | 3-day average over the 28-day median, highest first |
| `simple_velocity` | 3-day average over the preceding 3 days, highest first |

Each key is a value the snapshot already held. All four order the games score v0
ranked that day, so they share one universe. A missing key sorts last; ties are broken
by slug. No random baseline is used.

## Metrics

- Counts: snapshot days, snapshots, ranked and unranked, evaluable outcomes, and
  unavailable outcomes by reason.
- For each strategy, the outcomes of its top 1, top 3 and top 5 each day: count, mean,
  median, minimum, maximum, hit rate, downside rate.
- **Rank association**: for each day with at least three evaluable games, Spearman's
  correlation between position and outcome; then the mean and median over days, and
  how many days were positive and negative.
- Outcome by score, in fixed bins (0; 0–10; 10–25; 25–50; 50–100), because score v0 is
  exactly 0 for every game at or below its baseline and quantiles would split equal
  scores. Outcome by confidence band.
- Secondary outcomes over ranked games: next-day movement, weekly persistence, and
  lead time to the next peak.

A summary of no values has no mean: it is reported as empty, never as zero. Negative
outcomes are kept. Nothing is resampled and nothing is random.

## Evidence status

A report states how much it is worth, by thresholds in `validation_protocol_v1`:

| Status | Needs at least | Meaning |
|---|---|---|
| `framework_only` | (anything less than the next row) | The machinery works. Nothing is shown about the score |
| `insufficient_sample` | 30 days, 20 games, 500 outcomes | Every metric computes; none supports a conclusion |
| `exploratory` | 90 days, 100 games, 5,000 outcomes | Enough to look for patterns worth testing |
| `validated` | 180 days, 500 games, 50,000 outcomes, 2 independent sources, **and a held-out period** | Confirmed on data the rules were not chosen with |

The status is the highest one whose requirements, and every lower one's, are all met.
Protocol v1 has no held-out period, so **it cannot report `validated`** whatever the
sample. The artifact validator and the developer UI each refuse a report whose status
its own checks do not support.

## What this is and is not evidence of

A small recording can show that the framework works, that it does not look ahead, and
that its numbers are reproducible. It cannot show that a score is predictive, that the
event priors are right, or that MetaSpike does better than anything else in general.

No confidence intervals are reported. Outcomes on neighbouring days overlap (each uses
the next three days), so the observations are not independent, and with six games an
interval would imply a precision that does not exist.

## The fixture result, as it came out

`uv run metaspike backtest --replay tests/fixtures/wikimedia --recorded-through
2026-10-03 --from 2026-07-07 --to 2026-10-05`, offline, on the recorded replay. The
range is every prediction day the recording can stand in for under Wikimedia's 24-hour
lag.

- 91 prediction days, 7 July to 5 October 2026, each at 00:00 UTC. Newest demand day
  visible: 5 July at the first, 3 October at the last. Universe of 15 games; **6 have
  recorded history** and were ever ranked. 1,365 snapshots: 420 ranked, 945 unranked.
  The first ranked prediction is 28 July, on data through 26 July: before that no game
  has enough history for a baseline.
- Primary outcome evaluable for 396. Unavailable: 24 `future_incomplete` (the last four
  prediction days, six games each, whose three forward days run past the recording)
  and 945 `not_scored_at_snapshot`.
- **Evidence status: `framework_only`.** Six games where twenty are needed.
- All five checks passed (546 series, 1,365 windows, 1,365 reproductions, 45
  perturbed predictions, 1,365 references).

72-hour uplift against the baseline known at the recommendation (mean / median / hit
rate; top 1 n=66, top 3 n=198, top 5 n=330):

| Strategy | Top 1 | Top 3 | Top 5 | Rank association (66 days) |
|---|---|---|---|---|
| `metaspike_score_v0` | +114.4% / +50.1% / 82% | +66.6% / +30.0% / 68% | +41.4% / +10.7% / 51% | +0.62 (60 positive, 6 negative) |
| `raw_popularity` | +77.9% / +5.4% / 47% | +41.0% / +9.2% / 48% | +42.6% / +8.9% / 47% | +0.06 (29, 37) |
| `simple_lift` | +112.6% / +51.3% / 80% | +62.8% / +25.9% / 68% | +41.2% / +9.5% / 50% | **+0.71** (64, 2) |
| `simple_velocity` | +74.3% / +35.4% / 67% | +56.9% / +10.5% / 51% | +39.0% / +5.1% / 43% | +0.15 (41, 25) |

Read with these in mind:

- **Plain lift orders the games more consistently than score v0** (+0.71 against
  +0.62; positive on 64 of 66 days against 60). Score v0's top pick has a slightly
  higher mean (+114.4% against +112.6%) and a slightly lower median. On this sample
  score v0 adds nothing measurable over the lift it is largely built from.
- The outcome is lift moved forward, now across a one-day gap. A game whose attention
  is elevated is usually still elevated a few days later, so a ranking by lift is close
  to ranking by the outcome's own recent past. This measures persistence of attention,
  not foresight, and not value to a creator.
- The means are pulled up by a few extreme values (the largest single outcome is
  +1,045.5%; the lowest is −49.0%). Across all 396 the mean is +36.6% and the **median
  is +6.1%**; 43.9% are hits and 28.3% are downsides.
- By score: exactly 0 → mean −0.1%, median −16.1% (n=117); 0–10 → +6.2% (146); 10–25 →
  +52.3% (62); 25–50 → +76.5% (37); 50–100 → +221.2% (34). By confidence: low +47.0%
  (144), medium +30.6% (252), high none.
- Next-day movement: mean +8.5%, median −1.9% (n=408). Weekly persistence: mean 0.62,
  median 0.86 (n=372). A local peak was found within the week for 259 ranked
  snapshots, median 3 days ahead; none in the window for 113; unavailable for 48.
- Six games, one source, one period, in-sample. None of this generalises.

### Against the first version's provisional figures

The first version's figures are superseded. They are listed only so the effect of the
correction can be seen; they are not results.

| | First version (leaky, provisional) | Corrected |
|---|---|---|
| Prediction days / snapshots | 90 / 1,350 | 91 / 1,365 |
| Evaluable / `future_incomplete` | 402 / 18 | 396 / 24 |
| Score v0 top 1, mean / hit rate | +129.3% / 84% | +114.4% / 82% |
| Score v0 top 3 | +72.0% / 68% | +66.6% / 68% |
| Score v0 top 5 | +42.2% / 50% | +41.4% / 51% |
| Score v0 rank association | +0.68 | +0.62 |
| Simple lift top 1 | +127.6% / 84% | +112.6% / 80% |
| Simple lift rank association | +0.78 | +0.71 |
| Raw popularity rank association | +0.08 | +0.06 |
| Simple velocity top 1 / association | +90.4% / +0.18 | +74.3% / +0.15 |
| All ranked: mean / median | +35.2% / +5.5% | +36.6% / +6.1% |
| Checks | 4 | 5 |

The leading-group means and both rank associations fell once the unpublished day was
removed, as expected when a ranking loses a day of information it should not have had.
The evidence status is unchanged because it depends on sample size, not on results.

### Provenance

| | |
|---|---|
| `report_id` | `report_9e3a97a9725a587c5570ba2e42e7cd1a` |
| `snapshot_set_hash` | `8a0f192d11aac11fd92d37736598cb3cd7ea130c253c84cac5efeb86770ee748` |
| `label_set_hash` | `6b8aa3ce15dbe68977bf1bd68778d4ed65f0e2cfd704a94af7adf70d182cabdf` |

Each set hash is the SHA-256 of the record ids, sorted and joined by newlines. The ids
are themselves content hashes, so the same records always give the same hash, and one
added, removed or altered record gives a different one. Both hashes are part of what
the `report_id` is computed from. `AccountabilityStore.audit(report)` recomputes them
from the stored records for the report's period and says what does not match; the
`backtest` command prints the result. The report carries ids and hashes only, never
observations.

## Events

The framework carries event intelligence in each snapshot, evaluated as known at P.
**Real event backtesting is unavailable**: no historical event observation has been
visible to any prediction, so `event_heat_rules_v1` has not been backtested, and nothing
is claimed for it. Event Heat is not part of score v0 and is not evaluated or tuned
here.

### Event data is used only under policy, and the report says whose it was

A backtest may be given an event store. Event intelligence derived from a source is a
derived output of that source, so:

- If an event record or a coverage check from a source is **visible to any prediction**
  (observed at or before that prediction's moment), the source must have a
  `SourcePolicy`, filed under its own name, that clears the stated deployment stage for
  event intelligence. `run_backtest` takes both as explicit arguments,
  `event_source_policies` and `stage`, and neither has a default. Without them the
  backtest stops with `SourcePolicyError` **before** that day is predicted, so the event
  intelligence never enters a snapshot.
- If nothing is ever visible, neither is needed. A store whose contents were all
  observed after the last prediction day changes nothing and is not provenance.
- The `backtest` command passes the registered event-source policies and
  `private_development` explicitly, because it is a private, local command.

The report records what was actually used, under `events`:

| Field | Meaning |
|---|---|
| `event_sources_used` | Sorted. Every source whose record or coverage check was visible to at least one prediction. Not every registered source, and not every source in the store. A source used for coverage alone is included |
| `real_event_history` | At least one event **observation** was visible to at least one prediction. Coverage alone does not make it true; nor does an observation made after the range |
| `snapshots_with_event_intelligence` | Snapshots with event coverage, a heat of 0 included |
| `snapshots_with_positive_event_heat` | Of those, snapshots where an event actually counted |

A report cannot say its snapshots carried event intelligence and name no source: the
model refuses it. The sources are part of what the `report_id` is computed from.

When the report is published, each source it names goes through the **same** gate as the
run's own event data, with the same policy mapping (see
[event-sources.md](event-sources.md)). A report that used a source not cleared for
public launch makes the bundle `private_development_only` even when the run itself has
no event data at all; with no policy for that source, or at a stage it does not clear,
the build refuses. One function, `event_sources.gate.require_event_source`, is the gate
for both the backtest and the build.

The fixture backtest used no event source: `event_sources_used` is empty, and its
snapshot and label set hashes are the same as before this was added. Synthetic event
histories are used only in tests, under a test-only policy, to prove that later
observations and revisions cannot reach an earlier snapshot.

## The accountability store

`AccountabilityStore` is local and offline:

```
{root}/point-in-time-v2/snapshots/{prediction day}/{content hash}.ndjson
{root}/point-in-time-v2/labels/{data through}/{content hash}.ndjson
{root}/point-in-time-v2/reports/{report id}.json
```

Every record is identified by a hash of its content and written once. Writing it again
adds nothing; nothing is rewritten or deleted. A label made later, with more future
available, is a new record beside the earlier one, so an earlier "unavailable" stays as
evidence of what could be said then.

Records are kept under the generation of the simulation that made them. The first
version wrote directly under `{root}`; any such records are left where they are and are
never read as current results.

**Future warehouse mapping, not created:** three append-only tables, one row per
record: `ms_eval.recommendation_snapshots` (partitioned by the day of `as_of`, keyed by
`snapshot_id`), `ms_eval.outcome_labels` (partitioned by `data_through`, keyed by
`label_id`, joined on `snapshot_id`) and `ms_eval.validation_reports` (keyed by
`report_id`). Metrics stay derived. No cloud resource was created for any of this.

## Cost

**Measured:** 91 prediction days × 15 games (1,365 snapshots and 1,365 labels) ran in
0.58 to 0.59 s on a laptop, including the reproduction and perturbation passes. That
is the only size that has been run.

**Complexity, not a measurement:** O(prediction days × games). Each day rebuilds each
game's fixed 90-day window and scores it once; the reproduction pass doubles the work
and the perturbation check adds three more days. Nothing is recomputed across the
whole history per day. No runtime is stated for any larger universe, because none has
been run.

Known bottlenecks, none addressed because none bites at the present size:

- every snapshot and label of a run is held in memory until the end, where a large run
  would need to write day by day;
- the store finds existing records by reading all of them, on every add and audit;
- each day re-filters the whole recording per game, where an index by day would do.

## In the artifacts (contract 1.5.0, internal)

`validation.json`, named by `manifest.artifacts.validation`: the outcome definition
always, and the report when a backtest has been run. With none, `availability` is
`not_run` and there is no report and no number. An ordinary `metaspike run` never
performs a backtest; it publishes the latest report in the store if there is one.

1.3.0 replaces `first_snapshot_day` / `last_snapshot_day` with `first_prediction_day` /
`last_prediction_day`, and adds `first_latest_due_day`, `last_latest_due_day`,
`publication_lag_hours`, `snapshot_set_hash` and `label_set_hash`; each inspection day
gains `latest_due_day`, `primary_window_start` and `primary_window_end`. The change
went Python contract → JSON Schema → generated TypeScript.

1.5.0 adds `events.event_sources_used` and `events.snapshots_with_positive_event_heat`
to the report (format `validation_report_v3`). The report id changed with the format;
the snapshot and label set hashes did not. A stored report in an earlier format stays
on disk and is never read as current: it cannot say which event sources it used.

## Limitations

- In-sample only: one period, one protocol, no held-out data.
- One demand source. The outcome is measured on the same signal the score is computed
  from: it shows whether attention continued, not whether a creator would have gained.
- Encyclopedia readership is a proxy for demand. It is not views, players, search
  interest or creator supply.
- Availability is reconstructed from each source's stated publication lag, a policy
  figure and not a measured publication time. Source revisions are not reconstructable
  from a recording.
- Recommendations are simulated once a day at 00:00 UTC. With a one-day lag each rests
  on data two days old.
- Overlapping outcome windows make observations dependent; counts overstate evidence.
- No confidence intervals.
- A game whose data stopped is still scored by v0 on its last observed day, with a
  confidence of zero, and so stays in the ranking. The snapshot shows its staleness.
- Event intelligence took no part and has not been backtested.
- `metaspike_score_v0` is an internal attention measure, not the final
  creator-opportunity score. A result here is about v0 only.
