# Event intelligence

**Status: INTERNAL / UNCALIBRATED.** This describes an engine, not a product feature.
One event source exists, Steam News, for private development only (see
[event-sources.md](event-sources.md)). It is not collected on a schedule; a single
supervised probe checked three games and found no promotable event, so no real game has
a non-zero Event Heat and most real results are "unavailable".
Every number in `event_heat_rules_v1` is an engineering prior: not fitted, not
backtested, and not a statement about what anyone should make content about.

**Event Heat is not part of `metaspike_score_v0`.** It is computed beside the score and
changes no score, rank, confidence or reason. Tests prove that against a snapshot of
what was published before this existed (`tests/fixtures/score_v0_replay_golden.json`).
How, or whether, it enters a final opportunity score is undecided until there is data
to backtest.

Code: `src/metaspike/events/`. Published views: `src/metaspike/publishing/event_views.py`.

## What it answers

For a game, at an explicit moment `as_of`: what is known to be happening to it, how
much that matters right now, and why. And across games: which dated events have not
started yet.

## The core rule: unavailable is not zero

| Case | Meaning | Published as |
|---|---|---|
| A | A source looked recently and nothing is relevant | `available`, `event_heat: 0` |
| B | No source has looked, or not recently enough | `unavailable`, `event_heat: null`, with a reason |
| C | Events are known and current | `available`, `event_heat` above 0, with each contribution |

The three are distinct in every artifact: available with no contribution is exactly
`0`; available with any contribution is above `0`, however small; unavailable is `null`.

**Artifacts carry the computed values unrounded.** A heat one second into a ramp is
real and tiny (about 0.00006). Rounding it for publication would write `0`, which means
"a source looked and nothing contributes", so nothing at the contract boundary rounds
Event Heat, strength, contribution or hours. The validator refuses a zero heat beside a
counted event. How a number is shown is the interface's business: the developer UI
shows a positive heat below its display precision as `<0.1`, and a positive strength or
contribution as `<0.01`, never as zero.

B never becomes a number. The engine's result model, the artifact view, the artifact
validator and the developer UI each refuse a heat on unavailable intelligence
independently. Reasons: `no_event_coverage`, `event_coverage_stale`.

"Looked" means a **coverage check** (a source checked a game's events at a moment,
whatever it found) or an observation, which implies one. Coverage older than
`coverage_max_age_days` (14) is stale: dates move, so an old picture is not trusted
even when it holds a known future event.

## Data model

Two layers.

**`EventObservation`** is what one source said about one event of one game at one
moment. Immutable, append-only, identified by a hash of its content.

| Field | |
|---|---|
| `observation_id` | `obs_` + content hash. The same statement at the same moment is one observation |
| `canonical_game_id` | Resolved before the observation exists; see below |
| `source`, `observed_at` | Who said it and when it was seen |
| `event_type` | One of the 15 types in `domain/events.py` |
| `title` | As the source gave it |
| `starts_at`, `ends_at` | UTC. `ends_at` is optional |
| `time_precision` | `day` (starts 00:00 UTC that day; an end day is inclusive) or `exact` |
| `status` | `scheduled`, `cancelled`, `withdrawn` |
| `source_event_id` | The source's own id for the event, when it has one |
| `reference` | A URL or citation, where the source's policy allows keeping one |

**`EventRecord`** is one real-world event as known at a moment: its current fields, when
it was first and last observed, which sources report it, and a **revision** for every
observation that touched it, saying which rule matched and which fields changed. A
record is always derived from observations and never stored, so nothing is overwritten.

**Resolving the game.** A source reports an event against its own identifier for a game.
`resolve_game` accepts it only if the catalogue binds that exact identifier (source,
namespace and value) to exactly one canonical game. There is no title matching and no
fallback: an event for a game that cannot be named exactly is refused, and so is an
event record for a game that is not in the catalogue being published.

## Identity, deduplication and revisions

`reconcile` folds observations in observed order. Each either continues a known event
of the same game, by the first rule that applies, or starts a new one.

| Rule | An observation is the same event when |
|---|---|
| `same_source_native_id` | the same source reports the same native event id. Date, title and end may all have changed |
| `same_source_type_day_and_title` | the fallback when there is no shared native id: the same source reports the same type, the same UTC start day and the same title after folding case, accents and punctuation, and the two do not carry different native ids |
| `cross_source_same_reference` | another source cites exactly the same reference for the same type |
| `cross_source_same_type_day_and_title` | another source reports the same type, the same UTC start day and the same title after folding case, accents and punctuation. Equality, not similarity |

Anything else is a new event. **A safe non-merge is preferred to a wrong merge.**

- An event's id comes from its first observation and never changes.
- With a stable source-native id, a revised date, a changed title (however different),
  an end date arriving later and a cancellation are all revisions of the same event,
  each recorded with what changed.
- Without one, the same-source fallback needs an equal normalised title as well as the
  same type and start day. "Free Weekend" and "Free weekend!" are one event; "Winter
  Event" and "Holiday Event" on the same day are two. A substantive title change with
  no native id is therefore, safely, a separate event.
- A source that starts supplying a native id joins its earlier report of the same
  event only where that fallback evidence agrees; from then on the id carries
  revisions.
- A source that gives no end does not erase an end another source still states.
- Different types, different games, or a similar-but-not-equal title: never merged.
- A source with no native ids that moves a date, or retitles an event substantively,
  is seen as reporting a new event; the old one stays until that source cancels or
  withdraws it.
- Two reports of one event that share no id, reference or title stay separate. The
  heat aggregation counts one event per type and start day, so such a duplicate cannot
  count twice.
- No machine learning, no embeddings, no fuzzy matching.

The result is deterministic: the same observations give the same records in any order.

## Event Heat

One event's **strength** over time, 0 to 1, under its type's rule:

| When | Strength |
|---|---|
| Before the ramp | 0 (scheduled, not yet counting) |
| Inside the ramp | `pre_event_peak × (1 − days_to_start / ramp_days)`, rising |
| At the start | 1 |
| While running | `max(0.5 ^ (days_since_start / half_life_days), active_floor)` |
| After the end | `strength_at_end × 0.5 ^ (days_since_end / half_life_days)`, falling |
| `decay_window_days` after the end, or later | 0 (expired) |

It never falls before the start and never rises after it; a test checks this for every
type. An event with no stated end lasts its type's `default_duration_days`, and the
result says the end was assumed. A cancelled or withdrawn event has no strength.

An event's **contribution** is `importance × strength`, so a type's importance is also
its ceiling.

### `event_heat_rules_v1`

| Type | Importance | Ramp | Pre-event peak | Default length | Half-life | Floor | Decay window |
|---|---|---|---|---|---|---|---|
| release | 1.00 | 30 d | 0.60 | none | 7 d | 0 | 30 d |
| expansion | 0.85 | 21 d | 0.55 | none | 6 d | 0 | 21 d |
| season | 0.70 | 7 d | 0.50 | 60 d | 5 d | 0.15 | 3 d |
| wipe | 0.65 | 5 d | 0.60 | 14 d | 4 d | 0.10 | 2 d |
| major_patch | 0.60 | 5 d | 0.40 | none | 4 d | 0 | 10 d |
| platform_release | 0.60 | 14 d | 0.50 | none | 5 d | 0 | 14 d |
| major_in_game_event | 0.55 | 5 d | 0.50 | 3 d | 3 d | 0.25 | 3 d |
| dlc | 0.50 | 10 d | 0.45 | none | 4 d | 0 | 10 d |
| beta | 0.50 | 7 d | 0.45 | 7 d | 3 d | 0.20 | 4 d |
| expedition | 0.45 | 5 d | 0.40 | 42 d | 5 d | 0.10 | 2 d |
| showcase | 0.45 | 7 d | 0.40 | 1 d | 2 d | 0.50 | 4 d |
| free_weekend | 0.40 | 3 d | 0.40 | 3 d | 3 d | 0.40 | 2 d |
| trailer | 0.40 | 2 d | 0.30 | none | 2 d | 0 | 5 d |
| playtest | 0.35 | 5 d | 0.40 | 4 d | 3 d | 0.20 | 3 d |
| minor_patch | 0.15 | 1 d | 0.20 | none | 2 d | 0 | 3 d |

Global: scale 100; at most 3 contributing events; coverage trusted for 14 days; horizon
30 days; radar windows 24, 168 and 720 hours. Each value has a written rationale in
`src/metaspike/events/rules.py`, and the rules are published in full in the methodology
artifact. No function in the engine carries a constant of its own; a test reads the
source to check it. Changing any value means a new rules version.

### Aggregation

A game can have several events at once. Its heat is bounded by construction:

1. Events of the same type starting on the same UTC day count once, at the larger
   contribution.
2. Of what remains, only the 3 largest contributions count.
3. Those contributions `c` combine as `heat = 100 × (1 − ∏(1 − c))`.

So heat is never below the largest single contribution, reaches 100 only if one
contribution is 1, never falls when an event is added, and cannot be inflated by
stacking: forty simultaneous hotfixes give at most 38.6, less than one major patch.
A release at its start is 100; an expansion at its start with a season five days in is
90.3. Every relevant event is listed with its strength, contribution, whether it was
counted, its rank if so, and the reason if not.

## Time

`as_of` is always an argument and must carry a time zone. Nothing in the engine reads a
clock. Two things depend on it, separately:

- **What was known:** observations and coverage checks made after `as_of` are ignored,
  so a past evaluation is reproduced exactly, revisions included.
- **Where events stand:** each event's phase and strength at `as_of`.

In the pipeline `as_of` is the end of the run's last day, or the moment of collection
if that is earlier. A replay of a past day evaluates events as of that day, whenever
the replay is run.

## The result

`EventIntelligence`: availability, rules version and status, `as_of`, `event_heat` (a
number only when available), every relevant event with its evaluation and contribution,
the top event, a summary, reason sentences, the reasons for unavailability, and when
and by whom coverage was last checked.

Sentences come from fixed templates over an evaluated event: "Major update launches in
2 days: …", "New season started today: …", "Expansion launched 3 days ago; event
influence is still elevated: …". Days are whole UTC calendar days. A sentence cannot be
produced without an event behind it. No language model.

## Upcoming radar

`upcoming_radar` lists events that have not started, for games with current coverage:
soonest first, each in the shortest of the windows (next 24 hours, 7 days, 30 days)
that holds it. A window includes its end; an event that has started is not upcoming.
Each item carries its timing, whether its ramp has begun, its strength and
contribution, and its game's whole event heat as context.

It reports timing and context. **It ranks nothing and recommends nothing**: an event
being scheduled is not, by itself, a content opportunity. The final opportunity model
decides that, later.

If no game has coverage the radar is `unavailable`, which is different from an
available radar with no items.

## Storage

`EventStore` is local and offline:

```
{root}/observations/{observed day}/{content hash}.ndjson
{root}/coverage/{checked day}/{content hash}.ndjson
```

A file is one batch, created once and never rewritten or deleted. Appending what is
already stored adds nothing. `events(as_of)` reconstructs what was known at any moment.
The pipeline reads it from `.metaspike/events` (`--events-dir`); when it is absent,
which is the normal case today, every game is unavailable.

**Future warehouse mapping, not created:** two append-only tables with one row per
line, `ms_raw.event_observations` (the observation fields, partitioned by
`observed_at`) and `ms_ops.event_coverage_checks` (game, source, `checked_at`), both
keyed by content hash for idempotent loads. Records, heat and the radar stay derived.
No cloud resource was created for any of this.

## In the artifacts (contract 1.1.0, internal)

- `games/{slug}.json`: `event_intelligence` (always present), and the older `events`
  list, now filled from the same evaluation when available.
- `radar.json`, named by `manifest.artifacts.radar`.
- `methodology.json`: `event_heat`, the rules in full.
- `current.json` is unchanged: the leaderboard knows nothing about events.

## Limitations

- Internal and uncalibrated. Nothing has been fitted or backtested.
- One event source, private development only, not collected on a schedule. No real
  event has been promoted from it.
- Not part of score v0 and affects no rank.
- Importance is per type, not per game: a small game's release and a large game's are
  treated alike.
- Exact matching only, so some duplicates stay unmerged (bounded in the heat, as above).
- A date moved, or a title substantively changed, by a source without native ids looks
  like a new event.
- All days are UTC; an event known only to the day starts at 00:00 UTC.
- References are stored but not yet published, pending each source's policy.
- The radar's "opportunity" in its name is aspirational: it lists events, nothing more.
