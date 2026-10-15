# Creator-Opportunity Historical Evaluation

U6.5. Offline and private. Code: `src/metaspike/opportunity/history.py` and
`history_synthetic.py`. Command: `metaspike opportunity-history`. Tests:
`tests/test_opportunity_history.py`.

This evaluates `creator_opportunity_rules_v1` (U6.3) over past moments. At each moment it
asks what the rubric would have said with only what was visible then. Afterwards, and
separately, it attaches what attention did next. It changes no rule:

- not the rubric, and not its thresholds (robust z 3.5, a 7-day discussion window);
- not `validation_protocol_v1`;
- not `outcome_rules_v1`;
- not `recommendation_snapshot_v2`.

Nothing was fitted.

## Two questions, kept apart

| | Question | Measurable here? |
|---|---|---|
| A | Did audience attention to the game rise after the moment? | Only by proxy. `outcome_rules_v1` over Wikipedia readership, labelled **ATTENTION PROXY** on every outcome. |
| B | Would a creator's video made then have done well? | No. |

**Creator-video success cannot yet be validated from the recorded data.** No video was
made. No video performance was recorded. Readership is not viewership.

## What a past moment can honestly see

The prediction side is `RecordedHistory.inputs_at(moment)`: the pageview recording cut
back to what Wikimedia had published by the moment, under its one-day publication lag.
This is the same cut the walk-forward backtest makes, and it is checked by
`ensure_available`.

Nothing else was recorded before these moments: no Steam player samples, YouTube
searches, Reddit posts or event observations. None is invented, backdated, or granted a
standing it did not have when it was observed. Those families are **missing** at every
recorded moment, each with a reason. On recorded data the rubric can therefore reach at
most a provisional watchlist, never a candidate.

The evaluation does not call `assess_evidence` with a backdated state. Its guard
(`as_of >= state.generated_at`) and the identity constraints stay as they are. Instead the
historical track reads the same pageview series through the same
`wikimedia_family_v2(series, pageviews_read_at(series))`, and applies the rubric's own
`movement_direction`, `readiness`, `ordering_key` and `ahead_of_next`. A test checks that
at 5 October 2026, 12:00, the readings equal the live evidence path's for every game.

A moment the recording cannot reconstruct is **unavailable**. It makes no prediction, and
the report counts those game-moments as "no prediction".

### Four instants, never merged

| Instant | Where it is recorded |
|---|---|
| Prediction moment | `as_of`: 00:00 UTC on the declared day |
| Newest completed period | `evaluation_day`: the day before |
| Newest due period | `latest_due_day`: the newest day Wikimedia had published, one lag earlier |
| Outcome period | On the outcome record: from the first day wholly after the moment, for three days |

## Records

**`opportunity_snapshot_v1`** is new and additive. It is frozen before any outcome is read.
It carries:

- the canonical id, moment, rules version and mode;
- the readiness and the rubric's facts;
- the verified and counted families, and the movements;
- the why-now statements;
- angle availability and its references;
- creator-coverage completeness (`complete_sample`, `incomplete_sample` or
  `not_observed_by_moment`);
- the missing families with reasons;
- the origins and their standing;
- the reconstruction status;
- the position in the rubric's order, and why it is ahead of the next;
- the id of the `recommendation_snapshot_v2` made from the same inputs;
- a content fingerprint (`opsnap_…`).

The snapshot carries real readiness only; it carries no simulated readiness.

**Outcome records** (`opout_…`) point at a snapshot fingerprint. Each has one of these
statuses:

| Status | Meaning |
|---|---|
| `positive_attention` | Uplift ≥ +0.10 |
| `negative_attention` | Uplift ≤ −0.10 |
| `neither_positive_nor_negative` | Uplift between the two |
| `forward_period_incomplete` | The window runs past the data |
| `unavailable` | A missing day, no reference, not scored, or the identity not yet bound |

They reuse `label()` and the snapshot's frozen reference levels unchanged. Changing the
future changes an outcome and never a snapshot; a test does exactly that.

The report (`opportunity_history_report_v1`) carries no wall-clock time. The same inputs
give the same bytes and the same fingerprint, in any input order.

## Metrics

All metrics are descriptive: counts, and shares shown with their denominators.

- **Moments:** requested, reconstructed and unavailable, and the game-moments with no
  prediction.
- **Assessments:** candidate, watchlist and insufficient counts.
- **Abstention:** the share that is insufficient.
- **Family availability.**
- **Outcome availability.**
- **Missing evidence by family and reason.**

Per readiness group, the report gives the outcome statuses, the distinct games and the
number of episodes, where an episode is one game on consecutive moments.

- **No intervals.** The observations are too few for an interval to mean anything.
- **A share over nothing is NOT ESTIMABLE, never zero.** With no candidate at all, the
  candidate rate reads **NOT ESTIMABLE — NO POSITIVE CANDIDATES**.
- **Exploratory sensitivity.** Labelled as such, and not a result. It counts how many
  decisions would change if the movement threshold alone were 2.5, 3.0, 4.0 or 5.0.
  No outcome is attached to any alternative, so nothing here can select a threshold.
  The rubric is unchanged.

## Recorded-only result

Recomputed by the command, not restated from U6.3.

```
uv run metaspike opportunity-history --recorded --replay tests/fixtures/wikimedia \
  --recorded-through 2026-10-03 --from 2026-07-05 --to 2026-10-07
```

The run covered 95 requested moments and six games with recorded pageviews. Its demand
input was the pinned recording, so its standing is verified.

**Moments**

| | Count |
|---|---|
| Reconstructed | 91 |
| Unavailable | 4 (5–6 July and 6–7 October; 24 game-moments with no prediction) |

**Assessments**

| | Count |
|---|---|
| Total | 546 |
| Candidate | 0, so the candidate rate is NOT ESTIMABLE — NO POSITIVE CANDIDATES |
| Watchlist | 35 |
| Insufficient | 511 (abstention 93.6%) |

**Family availability**

| Family | Available |
|---|---|
| Readership | 540 of 546 |
| Steam players, events, community, creator supply | 0 of 546 each (not recorded at these moments) |

**Outcomes** (attention proxy)

| Group | Result |
|---|---|
| Watchlist | 34 positive, 1 forward-incomplete |
| Insufficient | 140 positive, 112 negative, 110 neither, 23 forward-incomplete, 126 unavailable |

At 5 October no game moves past 3.5. The largest |z| is GTA V at −1.86, flat by the rule.
This matches U6.3 and is recomputed here.

**Exploratory sensitivity.** These are decision counts only; no outcome is attached to
any alternative.

| Threshold | Changed decisions | Candidate | Watchlist | Insufficient |
|---|---|---|---|---|
| 3.5 (as written) | — | 0 | 35 | 511 |
| 2.5 | 26 | 0 | 61 | 485 |
| 3.0 | 11 | 0 | 46 | 500 |
| 4.0 | 8 | 0 | 27 | 519 |
| 5.0 | 13 | 0 | 22 | 524 |

No threshold produces a candidate, because none exists without an angle and a coverage
sample.

**How to read "34 of 34".** It is not evidence that the watchlist anticipates attention.

- **The observations are not independent.** The 35 watchlist assessments are 10 episodes
  over 6 games, and consecutive moments' forward windows overlap.
- **The outcome favours a rise already under way.** `outcome_rules_v1` compares the next
  three days with the same 28-day baseline the movement already exceeded. A rise that
  merely persists therefore counts as positive.

So it measures whether attention stayed up, not whether it was foreseen. The report states
this whenever the watchlist is non-empty.

## Synthetic mode

`metaspike opportunity-history --synthetic` uses six invented games, `synthetic-alpha` to
`synthetic-foxtrot`, with invented pageviews and invented discussions, samples and events.
Every input is labelled synthetic, and the report opens with a SYNTHETIC statement.

Synthetic and recorded inputs are refused together, in either direction. In this mode the
invented evidence is counted *as if verified* (`synthetic_counted_as_if_verified`), so that
the method can be seen reaching what recorded data cannot:

- a candidate followed by rising attention, and one followed by falling attention;
- watchlist entries with either outcome;
- abstentions;
- an incomplete forward period;
- an unavailable moment;
- evidence retrieved too late to count.

## Guarantees under test

- **No look-ahead.** Values not yet published at a moment are multiplied sevenfold, and
  the snapshots do not change (`perturb_unavailable`). Snapshots stay frozen when the
  future changes.
- **The four instants stay apart.** The outcome window starts after the moment, and the
  due period trails it by the lag.
- **Retrieval time governs.** Anything retrieved after a moment is missing at that moment
  (`observed_only_after_the_moment`): events, creator samples and discussions alike. A
  discussion goes stale after 7 days, and an incomplete sample never establishes coverage.
- **Rebinding stays in its own time.** A binding that begins later leaves earlier moments
  unbound, with no demand reading and no outcome. From the binding on, results are
  identical to an always-bound game.
- **Policy.** Only private development is allowed. Reddit data is refused while its
  approval is pending. The universe is bounded (50 games at most) and must be known and
  distinct. `--out` is refused inside `artifacts/` or any `public` directory.
- **The underlying score v0 snapshots** are the walk-forward backtest's own, with
  identical ids.
- **The command runs offline in both modes**, and its output is deterministic.

## Comparison to existing validation

The existing walk-forward backtest (`validation_protocol_v1`) asks whether score v0's
daily *ranking* is followed by attention. This evaluation asks a different question: what
the opportunity rubric would have *decided* at each moment, including when it abstained,
and what attention did next.

They share their machinery:

- `RecordedHistory` and `inputs_at`;
- `prediction_moment` and `days_between`;
- `predict()` and `recommendation_snapshot_v2`;
- `label()` and `outcome_rules_v1`;
- `perturb_unavailable` (in tests).

Every opportunity snapshot names the score v0 snapshot made from the same inputs, and a
test checks that those ids are identical to the backtest's own. Neither evaluation's
report, rules or snapshot format is changed.

## Commands

```
# recorded: the pinned Wikimedia recording, explicit range, cadence and universe
uv run metaspike opportunity-history --recorded --replay tests/fixtures/wikimedia \
  --recorded-through 2026-10-03 --from 2026-07-05 --to 2026-10-07 [--cadence-days N] \
  [--game SLUG ...] [--json] [--out LOCAL_FILE]

# synthetic: invented games and evidence, deterministic
uv run metaspike opportunity-history --synthetic [--json]
```

`--out` is refused inside `artifacts/` and any `public` directory. The output is identical
across runs.

## Validation status

The method has been checked; the rubric's predictive value has not.

- **Software correctness** is tested, with synthetic tests as the main tool. Those tests
  show the mechanics work. They establish nothing about prediction quality.
- **Attention prediction (question A)** is measured descriptively, on six games over one
  recording. No candidate could be formed, so candidate accuracy is NOT ESTIMABLE.
  Watchlist outcomes are dominated by persistence.
- **Creator-video success (question B)** is not measurable.

## Assumptions

- Moments are 00:00 UTC on each declared day, every `--cadence-days` days.
- Wikimedia publishes a day 24 hours after it ends; later days are withheld.
- A recording made after the fact cannot show a value the source later revised.
- Identity bindings are the seed catalogue's as given, unless a start is declared. This is
  the assumption the validation protocol's backtests already make.
- Outcomes use `outcome_rules_v1` and each moment's `recommendation_snapshot_v2` reference
  levels, unchanged.

## Remaining requirements for calibrated creator-opportunity ranking

Each item would have to be retained at the moment it was retrieved, under an approved
policy:

- point-in-time creator-supply samples;
- discussions;
- event observations;
- an outcome measure of actual video performance.

Beyond those inputs, a calibrated ranking would also need:

- **Many more moments and games**, so that candidates exist and episodes are numerous
  enough for intervals.
- **A forward outcome that does not reward persistence.** For example, a baseline taken
  from before the movement. That would be a new, separately approved outcome-rules
  version.
- **A held-out period.** Any revised threshold would be chosen on one period and reported
  on another, under a new rubric version.

None of these inputs exists today, and none is collected by this work.
