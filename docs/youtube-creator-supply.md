# YouTube creator supply (U6.1)

**Offline, private, on the product branch only.** MetaSpike has never called YouTube.
There is no API project, no key and no collected data, and nothing here is in
production. This document describes a parser, a bounded collector, an association rule
and a reading, all exercised against synthetic, YouTube-shaped responses.

Rising interest in a game is not by itself a good video opportunity: it matters how
much has already been made. This checkpoint adds the first evidence of that, as a
described **sample**. It adds no opportunity score and no competition score.

## The path

```
recorded-shaped files ─► replay transport ─► YouTubeAdapter ─► collect_creator_supply
   (synthetic today)        (no network)     (quota account)     (bounded pages)
                                                                        │
                                             CreatorSupplyStore ◄───────┘  append-only
                                                    │
        catalogue names at as_of ─► associate ─► read_creator_supply(as_of)
                                                    │
                              assess_evidence ─► GameEvidence.creator_supply
```

`src/metaspike/creator_sources/`:

| File | What it holds |
|---|---|
| `youtube.py` | The source policy, the documented request and response shapes, the quota account, the adapter, the replay client. |
| `collect.py` | `collect_creator_supply`: each query once, at most a few pages, then video details. |
| `store.py` | `CreatorSupplyStore`: append-only search pages and detail calls, read as of a moment. |
| `association.py` | `creator_association_rules_v1`: supported, uncertain or excluded. |
| `reading.py` | `read_creator_supply`: what the sample says about one game at a moment. |
| `synthetic.py` | The synthetic scenario. |

Reused, unchanged: the source-policy model and its stage gate; the catalogue state for
names and identity; `words` (the title tokeniser the event classifier uses); the U6.0
evidence reading and the U6.0.1 provenance rule; `Secret`; `SourceErrorCode`.

## The API, as documented

Read from the official documentation on 2026-10-14.

| | |
|---|---|
| `search.list` | `part=snippet`, `type=video`, `q`, `maxResults` (0–50, default 5), `order`, `publishedAfter`, `videoCategoryId`, `pageToken`. Returns `items[]` with `id.videoId` and `snippet.publishedAt`, `channelId`, `title`; `nextPageToken`; `pageInfo.totalResults`. |
| `videos.list` | `part=snippet,statistics,topicDetails`, `id`. Returns `snippet.categoryId`, `tags`; `statistics.viewCount`, `likeCount`, `commentCount`; `topicDetails.topicCategories`. |
| Errors | The documented envelope, by `reason`: `quotaExceeded`, `rateLimitExceeded`, and so on. |

`pageInfo.totalResults` is, in the documentation's words, "an approximation and may not
represent an exact value", with a maximum of 1,000,000. It is kept on the fetch record
as what the source said and is **never** read as a count of competing videos.

Not read at all: descriptions, thumbnails, channel titles, comments, captions,
transcripts, video content.

## Quota

| Method | Cost | Default daily allowance |
|---|---|---|
| `search.list` | 1 call, in its own "Search Queries" bucket | 100 calls |
| `videos.list` | 1 unit | 10,000 units, shared by all other endpoints |

Each page of results is another search call. Every request costs at least one, even an
invalid one. (An older figure of 100 units per search no longer matches the
documentation and is not used.)

`YouTubeQuota` holds one day's allowance in both buckets. A request is paid for before
it is sent; one that cannot be paid for is **not sent** and is recorded as such, so a
reading can tell a quota-limited sample from a complete one. A failed request is still
paid for. If the API answers `quotaExceeded`, nothing further is sent from that bucket
on that account; the other bucket is unaffected. Limits can be set lower than the
documented defaults, never higher. Nothing is retried: a retry would be a second charge.

Two figures here are **not** from the pages read and must be confirmed before any real
call: that category id `20` is Gaming (taken from general knowledge of the API, not
verified this session), and the batch size of 50 ids per `videos.list` call
(self-imposed; the method page states no maximum).

Not modelled: when the daily allowance resets. The pages read do not state it, so the
account is per run, and whoever runs a real collection must carry the day's usage.

## Is this video about this game?

A search returns videos that matched words. A title match is not relevance: "Rust" is a
language and a chemical reaction. `creator_association_rules_v1` puts each video in one
of three states for a game, and only the first is counted.

**Supported** — all of:
1. the video's details were retrieved (`videos.list`), not only its search snippet;
2. the game's catalogue name or a catalogue alias appears as whole consecutive words in
   the title, or is exactly one of the video's tags;
3. YouTube's own structured classification says it is a gaming video: category Gaming
   (`20`), or a topic category that is a video game topic;
4. no other catalogue game is named in the title, and no related product of this game
   (a DLC, an edition) is named.

**Excluded** — contradicting evidence: the matching words are part of a *longer*
catalogue name, so the title names a different game; or details were retrieved and
YouTube classifies the video as not gaming.

**Uncertain** — everything else: the name is not in the title or tags; only a snippet is
held; no classification is present; several games are named; a related product is named.

| Case | Outcome |
|---|---|
| "Rust wipe day", category Gaming | supported |
| "Rust programming tutorial", category Science & Technology | excluded |
| "How to remove rust from old tools", category Howto | excluded |
| A film sharing a game's title, category Film | excluded |
| "Rust Console Edition …" read for "Rust" | excluded (a longer catalogue name) |
| "Rust and Dota 2 funny moments" | uncertain (several games) |
| "Trust issues", "Rusty Lake" | uncertain (not the whole word) |
| "Dota …" read for "Dota 2" | uncertain (the numeral is part of the name) |
| A DLC of the game named in the title | uncertain |
| Only a search snippet held | uncertain |

It is deterministic. There is no probability, weight or score in it. Names are read from
the catalogue state as it stands; the frozen resolver takes no part, and no canonical
game is merged, created or renamed.

Limits by construction: a video about a game whose title does not name it is uncertain;
a gaming video naming the game in passing is supported; and a game whose name is a
common word leans entirely on YouTube's own classification.

## What a reading says, and what it never says

A reading describes a **sample**. It reports: the queries asked and how complete each
was; distinct videos returned; how many are supported, uncertain and excluded; results
returned by more than one query; among supported videos, distinct channels, the most
from one channel, how many were published in the last 24 hours and 7 days, and how many
carry a view count.

| State | Meaning | In the game evidence |
|---|---|---|
| `not_queried` | No search for the game by this moment. | `missing` |
| `expired` | Asked more than 30 days ago; past the retention limit. | `stale` |
| `incomplete` | A page limit, the quota, or a failure cut the sample short. | `incomplete` |
| `no_results` | A whole sample returned no video. | `available` |
| `ambiguous` | Videos came back and none is supported. | `unresolved` |
| `observed` | At least one supported video, from a whole sample. | `available` |

With no creator-supply input the game evidence says `not_integrated`, exactly as before.

It never says how much competition there is. `total_competition_known` is always false.
The supported count is a lower bound: "at least N videos from at least M channels".
Nothing is said above it. An empty or partial sample is not evidence of low competition,
and the text says so each time.

**"Substantial" or "limited" is not answered.** Calling a sample one or the other needs
a threshold calibrated against outcomes, and none exists. Inventing one would be an
arbitrary competition score under another name. This is a blocker for opportunity
scoring, listed below.

A missing counter stays missing: a response without `viewCount` gives `None`, never 0.
Counters are not summed or averaged.

## Time

Four moments are kept apart: when a video was **published**, when a search was
**retrieved**, when a video's details were **observed**, and the moment a reading is
**evaluated** at.

A reading at `as_of` uses only searches and details *retrieved* by `as_of`:

- **A current search cannot reconstruct a historical one.** A search answers for the
  moment it was asked. One made later is not a picture of earlier, whatever dates its
  videos carry: a video published before `as_of` but first returned by a later search
  is not part of what was known at `as_of`. Tested explicitly.
- Details retrieved after `as_of` are not used; the video is then uncertain.
- A counter is the latest retrieved by `as_of`, shown with that retrieval time. A later,
  larger counter is never shown for an earlier moment. There is no counter history.
- A response claiming a publication time after its own retrieval is rejected.
- Catalogue names come from the state the reading is given, which cannot postdate `as_of`.
- For each query the latest run by `as_of` is the sample; an older run is not mixed in.

No historical creator supply can be backfilled from the API, and none is fabricated.

## Provenance

The U6.0.1 rule applies unchanged. No YouTube recording is pinned, so **no
creator-supply input can be verified today**:

- The scenario's data is declared synthetic. Every response it writes carries an etag
  beginning `SYNTHETIC`, and the reading recognises that wherever the data is read from,
  including when the store is handed over as an ordinary local store.
- Any other local store is unverified.
- Neither is ever `usable`, and no option declares an input real.

There are no YouTube files under `tests/fixtures/`. The scenario and the tests write
their synthetic responses at run time, each with a note saying what it is.

## Source policy

`YOUTUBE_DATA_POLICY`, purpose `creator_supply`. Cleared for **private development
only**, on the basis that no API data is held at all.

| Requirement, from YouTube's Developer Policies | Status here |
|---|---|
| Stored non-authorised API data: at most 30 calendar days, then delete or refresh | `retention_days = 30`; the reading ignores anything older; **no deletion job exists** |
| Delete on request | Not built |
| No aggregating API data across channels (III.E.2.a); no new or derived metrics (III.E.4.h) | `derived_metrics_allowed = unknown` |
| A Derived Metrics Policy permits some derived metrics to clients that accept an amendment | Not accepted; whether per-game sample counts fall inside it is unanswered |
| YouTube shown as the source with its brand features; other content not shown as YouTube's | `branding_required`, `link_back_required` |
| A link to YouTube's Terms of Service; a privacy policy referencing Google's | Not built |
| One API project per client; no scraping; more quota only after a compliance audit | No project exists; nothing is scraped |

Being noncommercial does not waive any of these, and none was read as granted. The
policy text was read in summary on 2026-10-14; the clauses cited must be re-read in full
before any call is made. No legal review has been done.

Enforced in three places: the collector refuses before sending anything at any other
stage; the evidence reading refuses a creator-supply input at any other stage; and
nothing under `publishing/` or `schemas/` refers to this source. The artifact contract
is unchanged at 1.5.0.

## The demonstration

```
uv run metaspike evidence --as-of 2026-10-20T12:00:00+00:00 --synthetic-scenario \
    --game rust --game dota-2 --game counter-strike-2 --game elden-ring --game minecraft
```

Offline, no credential, byte-identical on a second run. Every creator-supply line is
marked `[SYNTHETIC]`. It shows:

| | Where |
|---|---|
| A. Clearly relevant videos | Rust: four supported |
| B. Ambiguous videos for a title query | Rust: three uncertain; Elden Ring: `ambiguous` |
| C. A bounded search with no matches | Dota 2: `no_results` |
| D. Partial pagination | Rust, first query: `page_limited` after two pages |
| E. Search quota exhaustion | Counter-Strike 2: `quota_limited (quotaExceeded)`; Elden Ring's first attempt not sent |
| F. Duplicates across queries | Rust: one video returned twice, counted once |
| G. Several videos from one channel | Rust: three channels, two videos from one |
| H. Different retrieval times | Rust: 10:00 and 11:00 |
| I. A video published after `as_of` | Rust: a later search holds one; it is never read |
| J. Missing statistics | Rust: view count held for three, absent for one |
| K. Unsupported or conflicting association | The language and the tools: excluded |
| L. Sample versus market | "Total competition: UNKNOWN" on every game |

`--creator-supply-dir DIR` reads a local store instead; it is unverified. There is no
command that fills a store from a real source.

## Tests

`tests/test_creator_supply.py`, 22 tests, all offline: response validation; search and
detail metadata; pagination and page limits; quota accounting, exhaustion and paid-for
failures; bounded detail batches; the key never stored; the replay never answering what
was not recorded; no socket opened; collector bounds and stage refusals; the store's
append-only and retention behaviour; the association rule, including against the real
recorded catalogue; the scenario read at several moments; counters never backdated;
integration into the game evidence; unchanged evidence without the input; synthetic and
unverified provenance; the policy; publication isolation; the command.

## Before real collection

1. An owner decision to create an API project and accept the YouTube API Services Terms.
2. YouTube's answer, or a careful reading, on whether counting sampled videos per game
   is aggregation or a derived metric under the policies.
3. A deletion and refresh job for the 30-day limit, and deletion on request.
4. A quota ledger that persists across runs, with the reset time established.
5. A query design: which words per game, which window, which order. Today's queries are
   the game's name.
6. A way to verify collected data's origin, as for every local store (U6.0.1).
7. A check of the association rule against real titles. It has only seen invented ones.

## Before public launch

Everything above, and: brand features and attribution on every page showing YouTube
data; the Terms of Service link and a privacy policy; an explicit basis for showing any
derived figure; and a reviewed public data contract. None is started.

## Still to come

Community interest (Reddit) has no adapter, policy or fixtures. Final opportunity
ranking has no rule: nothing here says how creator supply should be weighed against
demand, and the lower bound a sample gives is not yet a usable measure of competition.
