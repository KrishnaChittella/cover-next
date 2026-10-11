# Historical validation and recommendation accountability

**Status: INTERNAL.** This describes how MetaSpike checks its own past rankings, and
what the one check run so far shows. That check is on a recording of six games. It
demonstrates that the method works and does not look ahead. It is not evidence that
`metaspike_score_v0`, or anything else here, predicts demand.

Code: `src/metaspike/validation/`. Published views:
`src/metaspike/publishing/validation_views.py`.

## The question

When MetaSpike would have ranked a game highly at a moment T, what happened after T?

To answer it honestly, the ranking at T has to be exactly the one that could have been
made at T, and what happened afterwards has to be measured against what was known at T,
not against anything learned later.

## Two sides that never meet

| | Prediction side | Outcome side |
|---|---|---|
| Sees | Observations and events known at or before T | The days after T |
| Produces | A frozen `RecommendationSnapshot` per game | An `OutcomeLabel` per snapshot |
| Code | `history.inputs_at`, `snapshot.predict` | `history.future`, `outcomes.label` |

The separation is structural, not a convention:

- `predict` takes a `PredictionInputs`, which is rebuilt for the day and refused if it
  holds any observation after it. It has no parameter through which an outcome, a
  label or a future value could be passed.
- The scoring, feature and event engines do not import the validation package at all.
  Inside it, the prediction modules do not import the outcome modules.
- A snapshot has no field for an outcome. A label points at a snapshot by id and copies
  its reference levels from it; it never recomputes them.
- Inputs are rebuilt from raw observations for each day. Features are never computed
  on the whole history and read off for a past day, which is how a future value leaks
  into a past baseline or percentile.

## A snapshot

`RecommendationSnapshot` records what MetaSpike knew and said about one game at one
moment: `as_of` and the last day of data it could see; the input window; the game; the
score version, the score, its components and status; the rank among games scored at
that moment, with the universe size and ranked count; confidence and its factors; the
features of the scored signal; the explanation and its reasons; source coverage; and
event intelligence as it stood then (availability, heat if available, rules version).

It holds computed values and versions, never a source's raw payload. Its
`snapshot_id` is a hash of its content. `created_at`, the moment the evaluation was
run, is outside the hash: the same prediction rebuilt later has the same id, which is
how reproduction is checked.

## `outcome_rules_v1`

For a daily demand signal. Let `t` be the snapshot's last day, `x[d]` the value on day
`d`, `b` the 28-day baseline median the snapshot recorded and `a` the 3-day average it
recorded.

| Outcome | Formula | Reading |
|---|---|---|
| **`realized_uplift_72h`** (primary) | `mean(x[t+1], x[t+2], x[t+3]) / b − 1` | Positive: the next three days ran above the game's known normal level. Exactly 0: they averaged exactly that level. Negative: below it. Not clipped |
| `movement_24h` | `x[t+1] / a − 1` | The next day against the recent level already known |
| `persistence_7d` | `count(x[t+k] > b, k = 1..7) / 7` | Share of the following week spent above the known normal level |
| `lead_time_days` | smallest `k` in 1..6 with `x[t+k] > x[t+k−1]`, `x[t+k] ≥ x[t+k+1]` and `x[t+k] > b` | Days to the first confirmed local peak above normal. None in the window is a finding |

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

For each day T in the range, in order:

1. rebuild the inputs as they stood at the end of T;
2. compute features, score, confidence, rank and event intelligence from those alone;
3. freeze the result as snapshots, one per game in the universe;
4. only then read the days after T and label each snapshot;
5. join snapshot and label.

**The universe at T** is every game in the catalogue. A game is ranked only if it was
scorable at T; the rest are kept as unranked, with the reason. What became of a game
later has no bearing on whether it was ranked at T.

**Point in time is by day.** A daily value is taken as known once its day has ended. A
recording made after the fact cannot show values a source later revised, so those are
not reconstructable from one; an observation store that keeps what was observed when
gives the same interface with revisions respected. Events are already point-in-time:
only observations made by T are used, so an event or a revision seen later cannot
change event intelligence at T.

### Checks that are run, not assumed

Every backtest runs these and records the result in its report:

| Check | What it does |
|---|---|
| `inputs_end_at_cutoff` | Each prediction's inputs were rebuilt for its day and refused if they held anything later |
| `reproduced_after_labelling` | After every outcome had been read, each prediction was computed again from scratch: identical snapshot ids |
| `unchanged_when_future_altered` | For the first, middle and last day, every later value was multiplied by 7 and the prediction recomputed: identical |
| `reference_frozen_at_snapshot` | Every label's reference is the one its snapshot recorded |

A failure raises; a report cannot be produced from a backtest that looked ahead. The
tests include a control showing that a computation which did look ahead gives a
different answer, so the equalities above are not vacuous.

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
2026-10-03 --from 2026-07-06 --to 2026-10-03`, offline, on the recorded replay:

- 90 snapshot days, 6 July to 3 October 2026. Universe of 15 games; **6 have recorded
  history** and were ever ranked. 1,350 snapshots: 420 ranked, 930 unranked. The first
  ranked day is 26 July: before that no game has enough history for a baseline.
- Primary outcome evaluable for 402. Unavailable: 18 `future_incomplete` (the last
  three days, six games each) and 930 `not_scored_at_snapshot`.
- **Evidence status: `framework_only`.** Six games where twenty are needed.
- All four look-ahead checks passed.

72-hour uplift against the baseline known at the snapshot:

| Strategy | Top 1 mean / hit rate | Top 3 | Top 5 | Rank association |
|---|---|---|---|---|
| `metaspike_score_v0` | +129.3% / 84% (n=67) | +72.0% / 68% (n=201) | +42.2% / 50% (n=335) | +0.68 over 67 days |
| `raw_popularity` | +78.9% / 46% | +42.7% / 47% | +41.6% / 46% | +0.08 |
| `simple_lift` | +127.6% / 84% | +67.3% / 68% | +42.6% / 49% | **+0.78** |
| `simple_velocity` | +90.4% / 63% | +57.1% / 51% | +37.3% / 41% | +0.18 |

Read with these in mind:

- **Plain lift does as well as score v0 here, and orders the games more consistently**
  (+0.78 against +0.68; positive on all 67 days against 64). On this sample score v0
  adds nothing measurable over the lift it is largely built from.
- The outcome is lift moved forward three days. A game whose attention is elevated
  today is usually still elevated three days later, so a ranking by lift is close to
  ranking by the outcome's own recent past. This measures persistence of attention,
  not foresight, and not value to a creator.
- The means are pulled up by a few extreme values (the largest single outcome is
  +1,046%). Across all 402 the mean is +35.2% and the **median is +5.5%**; 42% are
  hits and 29% are downsides.
- By score: exactly 0 → mean −8.4% (n=120); 0–10 → +1.5% (148); 10–25 → +52.9% (63);
  25–50 → +79.8% (37); 50–100 → +254.7% (34). By confidence: low +43.2% (144), medium
  +30.7% (258), high none.
- Next-day movement: mean +3.7%, median −1.9%. Weekly persistence: mean 0.61, median
  0.86. A local peak was found within the week for 259 ranked snapshots, median 3
  days ahead; none in the window for 119.
- Six games, one source, one period, in-sample. None of this generalises.

It ran in about 0.6 seconds, including the reproduction and perturbation passes.

## Events

The framework carries event intelligence in each snapshot, evaluated as known at T.
**Real event backtesting is unavailable**: no historical event observations exist, so
`event_heat_rules_v1` has not been backtested, and nothing is claimed for it. Synthetic
event histories are used only in tests, to prove that later observations and revisions
cannot reach an earlier snapshot. Event Heat is not part of score v0 and is not
evaluated or tuned here.

## The accountability store

`AccountabilityStore` is local and offline:

```
{root}/snapshots/{cutoff day}/{content hash}.ndjson
{root}/labels/{data through}/{content hash}.ndjson
{root}/reports/{report id}.json
```

Every record is identified by a hash of its content and written once. Writing it again
adds nothing; nothing is rewritten or deleted. A label made later, with more future
available, is a new record beside the earlier one, so an earlier "unavailable" stays as
evidence of what could be said then.

**Future warehouse mapping, not created:** three append-only tables, one row per
record: `ms_eval.recommendation_snapshots` (partitioned by `data_cutoff_day`, keyed by
`snapshot_id`), `ms_eval.outcome_labels` (partitioned by `data_through`, keyed by
`label_id`, joined on `snapshot_id`) and `ms_eval.validation_reports` (keyed by
`report_id`). Metrics stay derived. No cloud resource was created for any of this.

## Cost

Each day rebuilds each game's fixed 90-day window and scores it once, so the work is
linear in days × games; the reproduction pass doubles it and the perturbation check
adds three more days. Nothing is recomputed across the whole history per day.

Measured: 90 days × 15 games (1,350 snapshots) in about 0.6 s on a laptop.

Not measured, by the same arithmetic only: 1,000 games over 90 days is about 67 times
the work, 5,000 about 330 times, 10,000 about 670 times, so of the order of a minute,
a few minutes and several minutes. Two things would need attention before that:
snapshots are held in memory until the end of a run and would have to be written day
by day; and the store checks for existing records by reading them all. Neither is
needed at the present size, and neither was built.

## In the artifacts (contract 1.2.0, internal)

`validation.json`, named by `manifest.artifacts.validation`: the outcome definition
always, and the report when a backtest has been run. With none, `availability` is
`not_run` and there is no report and no number. An ordinary `metaspike run` never
performs a backtest; it publishes the latest report in the store if there is one.

## Limitations

- In-sample only: one period, one protocol, no held-out data.
- One demand source. The outcome is measured on the same signal the score is computed
  from: it shows whether attention continued, not whether a creator would have gained.
- Encyclopedia readership is a proxy for demand. It is not views, players, search
  interest or creator supply.
- Point in time is by day; source revisions are not reconstructable from a recording.
- Overlapping outcome windows make observations dependent; counts overstate evidence.
- No confidence intervals.
- A game whose data stopped is still scored by v0 on its last observed day, with a
  confidence of zero, and so stays in the ranking. The snapshot shows its staleness.
- Event intelligence took no part and has not been backtested.
- `metaspike_score_v0` is an internal attention measure, not the final
  creator-opportunity score. A result here is about v0 only.
