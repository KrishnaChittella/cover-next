# Multi-source game evidence (U6.0)

**Offline, private, on the product branch only.** This document describes a local
reading of what MetaSpike holds about a game at a moment, source by source. It is not
the opportunity ranking, it is not an artifact, and it changes nothing in production.

MetaSpike's aim is to tell a creator which game deserves a video now, why, on what
evidence, and how far that evidence can be trusted. That needs more than one kind of
evidence about the same game at the same moment. Each kind already existed here in its
own module. This checkpoint puts them side by side for one canonical game and says
plainly where a source says nothing and why. It adds no score and no weight.

## The path

```
recorded catalogue rows ─► resolver_v2 ─► catalogue state ─┐   identity at as_of
recorded pageviews ─► cut to what was published by as_of ──┤   demand: readership
local sample store ─► point-in-time entity history ────────┤   demand: players
local event store, source store ───────────────────────────┤   events, candidates
                                                           ▼
                              assess_evidence(inputs, as_of)  ─►  EvidenceReport
                                                                  (local, private)
```

`src/metaspike/evidence/`:

| File | What it holds |
|---|---|
| `model.py` | The typed reading: `EvidenceReport`, `GameEvidence`, `SourceEvidence`, and the eight states. |
| `assemble.py` | `assess_evidence`: the policy gate, then one pass that calls the existing components. |
| `render.py` | The text a developer reads. Counts, states, dates; no source text. |
| `synthetic.py` | An invented scenario for exercising the path, labelled as invented throughout. |

## What is reused, unchanged

Nothing was rebuilt. `assess_evidence` calls each of these as it is:

| Need | Existing component |
|---|---|
| Identity at a moment | The catalogue state's `MappingHistory`, read with `active_at(as_of)` |
| Which Steam apps stand for a game | `steam_eligibility`, `sampling_targets` (the binding audit's conditions) |
| Pageviews as published by a moment | `build_time_series`, `latest_due_period` (the backtest's cut) |
| Players for an entity, point in time | `steam_family_v2` over `read_entity_history` and the `SampleStore` |
| Hour and day completeness | `hourly_aggregates`, `daily_aggregates` (`baseline_rules_v1`) |
| Features, and which may be acted on | The one feature engine, read through `usable_features` |
| Availability states, evidence breadth | `signal_semantics_v2` (`entity_evidence_v2`) |
| Score v0 and its confidence | `assess_entity` (`metaspike_score_v0`), on the visible pageviews |
| Event intelligence and the radar | `assess_events`, `upcoming_radar` (`event_heat_rules_v1`) |
| Announcements and their fate | The source store's candidates (`event_classification_rules_v1`), counted |
| Policy | `require_catalogue_sources`, `require_event_source`, `registration_refusal` |

No frozen rule, score, schema or source policy was changed, and none needed to be.

## Kinds of evidence, never blended

| Kind | Sources | What it is evidence of |
|---|---|---|
| `catalogue_metadata` | IGDB, Steam's app list | Who the game is. Never that demand is rising. |
| `demand_signal` | Wikimedia pageviews, Steam concurrent players | Attention, each family against its own history. |
| `event_evidence` | Steam News | Promoted events and their timing. |
| `creator_supply` | none | Not integrated. |
| `community_interest` | none | Not integrated. |

Pageviews are not player counts and neither is a view count. No raw number of one
source is added to, averaged with, or compared against a raw number of another: each
demand family is read by the feature engine against its own past, and the reading
reports each family's movement separately. A family counts once, however many metrics
or aggregates come from it. An announcement is not a demand signal, and metadata is not
one either.

## Exact source-to-game mapping

| Source | How its id reaches a canonical game |
|---|---|
| IGDB | A source record bound by `resolver_v2`: `igdb:<id>` in the mapping history, with a mapping version and the moment the binding began. |
| Steam app list | The same, as `steam_catalogue:<appid>`. |
| Steam players | Sampled under the app id. An app counts only when it is bound to the entity **and** a record bound to that entity lists it as a structured external id (`steam_eligibility`). A sample belongs to the entity its app was bound to **when it was observed**. |
| Steam News | The same app binding, through `steam_app_bindings`. An unbound app's posts are source-level candidates and reach no game. |
| Wikimedia | The article identifier the bootstrap entity carries (`en.wikipedia.org`). Only the fifteen original games have one. |

A title is never an identity. A record that matches by title alone stays in quarantine,
and the reading lists it as `quarantined, NOT bound` and takes nothing from it.

## States

Eight, kept apart because each calls for something different:

| State | Meaning |
|---|---|
| `available` | The source observes the game and its latest observation is current. A reported zero is this, with a level of zero. |
| `stale` | It has observed the game, but not recently enough. |
| `missing` | It could observe the game and, as known at the moment, never has. |
| `inapplicable` | It cannot observe this game. Nothing is missing. |
| `unknown` | Whether it can observe this game is not established. |
| `unresolved` | Observations or records exist, and no approved rule makes one value of them (several Steam apps; a quarantined record). |
| `not_collected` | Nothing of this source was given to the reading. |
| `not_integrated` | MetaSpike has no source of this kind. Never a zero. |

## Time

A reading at `as_of` uses only what was known then:

- **Bindings**: those valid at `as_of`. A later rebinding is invisible earlier, and a
  sample is attributed through the binding valid at its own observation time. Stored
  samples are never rewritten.
- **Samples and events**: only attempts stored, observations made and checks recorded
  by `as_of`.
- **Pageviews**: a day is visible only once the source's publication lag has passed
  (one day, from the policy). The cut is the backtest's and is tested equal to it.
- **Metadata**: a source record fetched after `as_of` is not used. Only a record's
  latest version is held, so an earlier version cannot be shown in its place: what
  leaned on it reads as `unknown`. *Known limit: metadata history is not kept.*
- **Quarantine**: entries first seen after `as_of` are not listed.
- A moment before the catalogue state was generated is refused.

**Pageview freshness.** The frozen engine counts whole days between a daily series'
last bucket and the moment it is read, and knows nothing of publication lag; read at
the wall clock, a source that publishes a day late would be stale every day. The
reading hands the engine the end of the latest period the source was *due* to have
published, so pageviews are stale exactly when a due day is missing, which is the rule
as written and the one score v0 applies. No engine rule changed. **This choice is
flagged for review.**

## The command

```
uv run metaspike evidence --as-of 2026-10-05T12:00:00+00:00 \
    --wikimedia-replay tests/fixtures/wikimedia --recorded-through 2026-10-03
```

Offline and read-only: recorded files and local stores only, no network fallback, no
credential read, nothing stored. The same inputs give the same output byte for byte.

| Option | Meaning |
|---|---|
| `--as-of ISO-TIME` | Required, with a UTC offset. |
| `--game SLUG` (repeatable), `--all` | Default: the fifteen original games. |
| `--recorded DIR...` | Recorded catalogue rows (default: the two fixture directories). |
| `--state FILE` | A local catalogue state to read, never written. Default: resolve in memory, dated at the records' latest fetch. |
| `--wikimedia-replay DIR --recorded-through DATE` | Recorded pageviews. |
| `--sample-store DIR`, `--events-dir DIR`, `--event-sources-dir DIR` | Local stores. Not given means `not_collected`. |
| `--synthetic-scenario` | Invented samples and announcements in place of the stores. |
| `--stage` | Default `private_development`. Every input's source must clear it. |
| `--json`, `--out FILE` | The report as JSON; `--out` refuses the artifacts directory. |

Resolved in memory, entities beyond the original fifteen get ids that are the same on
every run and are marked `evaluation_only`: they are stored nowhere.

## What is real today, and what is not

**Real, recorded, in the repository:** 402 catalogue rows (IGDB and Steam's app list)
and daily pageviews for six games through 3 October 2026.

**Real, local to this machine, not in the repository:** the fifteen announcements of
the one supervised Steam News probe of 11 October 2026 (three apps).

**Not held at all:** any history of Steam player samples. One recorded response exists
as an adapter fixture; no sample store has ever been filled.

Read on the recorded inputs at 2026-10-05 12:00 UTC, for the fifteen original games:

| | Games |
|---|---|
| Pageviews available (current, real) | 6 |
| Pageviews missing (article mapped, no recording held) | 8 |
| Pageviews unknown (no article mapped) | 1 |
| Steam players: eligible, not collected | 8 |
| Steam players: inapplicable | 5 |
| Steam players: unknown | 2 |
| **Games with two demand families observing them** | **0** |
| Event coverage | none (nothing had been asked by then) |

Read at 2026-10-12 00:00 UTC with this machine's local event stores: the six
pageview series are stale (the recording ended eight due days earlier), and three
games have current Steam News coverage with fifteen announcements read, none promoted,
and an event heat of zero.

**So there is no real multi-source finding to report.** No game in the repository has
two real demand signals at the same moment. That is the honest state of the data, and
the command says it for each game.

## The synthetic scenario

Because the real inputs do not overlap, the integration is demonstrated on invented
data:

```
uv run metaspike evidence --as-of 2026-10-20T12:00:00+00:00 \
    --wikimedia-replay tests/fixtures/wikimedia --recorded-through 2026-10-03 \
    --synthetic-scenario
```

It writes, to a temporary directory discarded afterwards: player samples for four
eligible apps (one current, one stopped three hours earlier, one reporting zero, one
with an incomplete hour and day), leaves the other four never sampled, and writes
announcements for three apps which then pass through the real adapter, classifier and
promotion gate (an upcoming dated season, a hotfix, a post that stays a candidate, a
"coming soon" whose timing is not established). Identity is not invented.

Every synthetic input is labelled: a banner on the report, `[SYNTHETIC]` on each source
and movement it reaches, `origin: synthetic` in the JSON. A synthetic source is never
`usable` and never counts as a demand family observing a game, however current it
looks. The command refuses to mix the scenario with stored evidence. **Nothing in a
scenario report is a finding about any game.**

Synthetic-only coverage in the tests: everything about player samples (association,
never sampled, reported zero, stale, incomplete aggregates, several apps, rebinding),
promoted and upcoming events, and candidates. Recorded coverage: identity, metadata,
quarantine, pageviews, the publication-lag cut, score v0 parity.

## Policy

The reading is `private_development_only`, always. It has no schema in the artifact
contract (still 1.5.0, untouched), and nothing in the publishing path imports it.

Before anything is read, every input's source must have a policy clearing the stated
stage for its purpose; a missing policy, or one under another source's name, refuses
the whole reading. At `public_launch` the recorded catalogue alone is refused.

What the repository's policies say stands between each source and a noncommercial
public launch:

| Source | Cleared today | What remains for public launch |
|---|---|---|
| Wikimedia pageviews | every stage | Nothing. Attribution as already shown. |
| IGDB | private development, private collection | Static, user-visible IGDB attribution in the product, and IGDB's answer on whether an unmonetized but commercial-capable launch requires the partnership. Derived metrics: unknown. |
| Steam app list | private development | Valve name, logo and followable links, "as is" disclaimers and a privacy policy on pages showing Steam data; the branding guidance the terms refer to has not been located. |
| Steam concurrent players | private development | Derived metrics are not explicitly allowed (unknown), so nothing computed from it may be published. Needs an explicit answer. |
| Steam News | private development | Derived events are not explicitly allowed (unknown), and showing announcements is restricted. Needs an explicit answer. |
| Creator supply, community interest | no source | A source, and a reviewed policy for it, before anything else. |

The project is a noncommercial personal project; the stage in question is
`public_launch`, not `commercial_production`. None of the above was changed here, and
no unknown permission was read as granted.

The text reading prints no title of a post or a source record, and prints no raw level
of a restricted source (as `sample` never prints one). The JSON is a local, private
file; `--out` refuses to write inside the artifacts directory.

## What the reading says is missing

For every game it lists what a creator-opportunity recommendation would still need:
no creator-supply source; no community-interest source; no opportunity score (score v0
reads pageviews alone and is reported unchanged); and, where they apply, fewer than two
demand families, no event coverage, quarantined records, restricted sources, synthetic
evidence.

## Measured cost

On the development laptop (Apple M3, Python 3.12.15), one run each; local timings, not
production figures:

| Run | Wall time | Peak memory |
|---|---|---|
| Fifteen games, recorded catalogue and pageviews | 1.4 s | 72 MB |
| All 234 entities, as JSON | 1.5 s | 87 MB |
| Fifteen games with the synthetic scenario (including writing it) | 7.3 s | 95 MB |
| All 234 entities with a synthetic sample store, in process | about 35 s | not measured |

Most of a run is resolving the 402 recorded rows. With a sample store, each eligible
entity costs about 0.4 s, nearly all of it the engine folding the full hourly history;
ninety entities are eligible in the broad catalogue. Reading a game's sample history
is handed only that game's own app bindings instead of the whole mapping, which gives
the same answer (tested) and removed a per-slot search through every binding. No cloud
resource was used and nothing was benchmarked anywhere else.

## Not done here

- **Opportunity scoring.** No rule exists for combining families, for agreement between
  them, or for weighing demand against supply. The reading is its input, not a draft of it.
- **Creator supply (YouTube)** and **community interest (Reddit)**: no adapter, no
  policy, no fixtures. The model has a kind and a state for each, so adding a source
  fills a row and changes no existing contract.
- **A combination rule** for games with several Steam apps.
- **Real player history.** None is recorded; sampling is a private-development command.
- **Wikimedia identifiers** beyond the fifteen original games (14 of 234 entities).
- **Metadata history**: only the latest version of a source record is held.
- **Any public data contract** for multi-source evidence. It needs its own review.

## After C2-F

Nothing here is deployed or merged, and none of it may be before the C2-F soak window
closes and its evidence is reviewed. Then, separately and each under its own review:
whether Steam sampling runs in private collection (its policy clears private
development only today); the policy answers listed above; and only after those, any
public contract carrying more than pageviews.
