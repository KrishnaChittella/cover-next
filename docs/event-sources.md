# Event sources

**Status: INTERNAL, private development only.** This describes how MetaSpike reads
announcements from a source and decides which of them are events. One source exists:
Steam News. Nothing derived from it is cleared for public launch or commercial use, and
being reachable without a key does not change that.

Code: `src/metaspike/event_sources/`. The consumer, unchanged, is the event
intelligence engine in `src/metaspike/events/` (see
[event-intelligence.md](event-intelligence.md)).

## The shape of it

```
source --fetch--> SourceItem --classify--> EventCandidate --promote--> EventObservation
                      |                         |                           |
               EventSourceStore          EventSourceStore               EventStore
               (every post)              (every candidate)       (promoted only: all that
                                                                  Event Heat ever reads)
```

**An announcement is not an event.** A post is evidence. It becomes a candidate, which
says what the post might be and why. A candidate becomes an event observation only when
both its type and its timing are established. Everything else stays a candidate, with
the reason, and never reaches Event Heat or the radar.

## The source: Steam News

| | |
|---|---|
| Source id | `steam_news` |
| Method | `GET https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/` |
| Documentation | https://partner.steamgames.com/doc/webapi/ISteamNews (reviewed 2026-10-11) |
| Credential | None. The method lists no key parameter; none is read, held or sent |
| Parameters used | `appid`; `count` (5 by default, never above 20); `maxlength=1`; `enddate` for a backfill page |
| Never used | `GetNewsForAppAuthed` (publisher key), the partner host, the `feeds` parameter, any store page, any link inside a post, browser automation |

Documented: the interface, method, version and five parameters. **Not documented:** the
response body, any field of it, error behaviour, caching, and whether `enddate` is
exclusive.

`maxlength` is documented as "if this is 0 the full content is returned", so it is sent
as 1: the body is never even transferred in full, and what does arrive is never read.

## Source policy

`STEAM_NEWS_POLICY` is its own policy. It is not the Steam catalogue or player-count
policy and grants nothing to either. The policy model gained a purpose,
`SourcePurpose.EVENT_INTELLIGENCE`, because an event source derives an event (a type and
a time), not a demand metric.

| Question | State |
|---|---|
| Private development | **Cleared.** Adapter, a bounded supervised probe, a local store on the owner's machine |
| Private (unattended) collection | **Not cleared.** Needs the conditions every Steam source needs, plus a decision on keeping publisher-authored titles. No backfill is budgeted or approved |
| Public launch | **Not cleared** |
| Commercial production | **Not cleared** |
| Raw storage | Restricted (conditional) |
| Historical storage | Restricted (conditional) |
| Derived events | **Unknown.** The terms do not address derived data |
| Redistribution / public display | Restricted; display of a publisher's title is **unknown** |
| Commercial use | **Unknown** |
| Attribution | Required: "Steam news provided by Valve via the Steam Web API." plus Valve name, logo and followable links wherever Steam Data is shown |
| Rate limit | 1 request a second, self-imposed; shared with every other Steam source |
| Quota | 100,000 calls a day, documented, shared with every other Steam source (one budget group) |
| Publication | A post carries its own publication time and is taken as visible from then; lag 0, self-imposed, not documented |

Per field: the post **body** (`contents`) and **author** are prohibited, self-imposed,
and never read. A **title** may be kept privately as the evidence a classification
rests on; whether it may be shown publicly is unknown. A **URL** is kept as a
reference and never requested.

Unresolved, recorded on the policy and not assumed away:

1. The Web API Terms of Use are written around an API key; this method takes none.
   Whether the terms apply to a keyless call is not stated. They are applied in full.
2. The terms license distributing Steam Data "as is" and say nothing of data derived
   from it. A classified event is derived.
3. A post is written by the game's publisher, not by Valve. The terms do not say what
   a third party's text may be used for.
4. The Subscriber Agreement's clause on automated systems applies as it does to the
   player-count source.
5. The response body, caching and `enddate` are undocumented.
6. Commercial use is not addressed anywhere reviewed.

Unknown stays unknown. Nothing here was read as a yes.

### Where the policy is enforced

- **Ingestion** refuses, before any request, a stage the policy does not clear.
- **Storage** refuses an item carrying a field the policy does not allow to be kept.
- **Building artifacts** fails closed. An event source is *used* when it is behind an
  event record or a coverage check, or when its summary shows it was ever asked
  (before or after the evaluation moment). Every used source must have a `SourcePolicy`
  in the inputs' `source_policies`, filed under its own name, that clears the bundle's
  stage for event intelligence. A missing mapping, an empty one, or one that lacks
  only that source stops the build with `SourcePolicyError` before any event
  intelligence is computed. There is no way to vouch for event data without a policy
  and no source name is exempt. `source_policies` may be absent only when no source is
  used at all, and the bundle is then `internal`.
- At the private-development stage a cleared source is allowed in, and the manifest is
  marked `distribution: "private_development_only"` with the source named under
  `restricted_sources`. The developer interface shows a notice on every page of such a
  bundle. At private collection, public launch and commercial production the policy
  must clear that exact stage, or the build refuses.
- **Validating a published bundle** checks the same from the outside: the distribution
  is private exactly when a source is restricted; a collected event source not cleared
  for public launch must be named as restricted; and every source an event or a
  coverage check in the bundle is attributed to must be either named as restricted or
  described on source-health as cleared for public launch.

Tests invent events, and invented events go through the same gate: `tests/
synthetic_policy.py` holds a test-only policy (named `synthetic_...`, cleared for
private development alone, in no runtime registry, with no endpoint). There is no
production code path that treats a synthetic source differently.

A note on that last point, because it is a decision and not a deduction. The existing
build refuses any *demand* source whose derived-metrics permission is unanswered,
whatever the stage. Events are gated by stage instead, so that the local developer
interface can show what a private probe found. That makes the artifact bundle a place
private-only data can be, which it was not before; the manifest marking exists to keep
such a bundle from ever being taken for something publishable. No publisher exists yet.
When one does, it must refuse a bundle that is not `internal`.

## The boundary: `EventSourceAdapter`

A source sits behind `EventSourceAdapter` (`base.py`): `fetch(source_game_id, before=)`
and `backfill(source_game_id, max_requests=, before=)`. It returns `SourceItem`s:

| Field | |
|---|---|
| `source`, `game` | The source, and the game as the source names it (`steam_news` / `app` / `730`) |
| `source_item_id` | The source's own id for the post (`gid`) |
| `published_at`, `observed_at` | When the source says it was published; when MetaSpike saw it |
| `title` | Plain text. Control characters removed, nothing parsed, rendered or executed |
| `summary` | Always `None` for Steam News: the body is never read |
| `reference` | The post's URL if it is `http(s)`. Never requested |
| `feed_name`, `feed_label`, `feed_type`, `tags`, `is_external_reference` | Structured data exactly as given |
| `provenance` | Fetch id, endpoint, the game that was asked about, position in the page |
| `content_id`, `item_version_id` | What was said; one sighting of it |

No adapter classifies, scores, or names a canonical game. A fetch always comes back as
an `EventSourceFetch`, succeeded or not: a failure is a value with an error code and no
items, never an empty success.

### Reading a response

Only `gid`, `title` and `date` are required of an entry. Everything else is optional and
unknown fields are ignored. A malformed **entry** is rejected alone, with its position
and a reason and never its content. A malformed **envelope** (no `appnews` object, no
`newsitems` list, or an answer about a different app) fails the whole request. Each
fetch records the field names it saw, never their values.

Requests go through the same `LimitedHttpClient` as every source: the Steam budget
group's rate limiter and quota ledger, and an `AttemptBudget` that counts outbound
attempts below the retry policy. There is one transmission per fetch and no retry.

## Canonical game resolution

A Steam app id resolves through the catalogue's own binding and nothing else
(`resolve.py`): the same standard the player-count sampler uses. An app counts only when
it is bound to an entity **and** a record bound to that entity lists it as a structured
external id. There is no title in the resolver at all.

An item whose app is not bound is kept as a source-level candidate, refused with
`unresolved_game`. It gets no observation and no coverage and cannot affect Event Heat.

## The candidate

`EventCandidate` (`candidates.py`) carries: the canonical game if bound, and the binding
it resolved through; the source's item id and the item version it was read from; when
the post was announced and when it was observed; the proposed event type, start and
precision, where established; the rule that classified it and every rule that matched;
the evidence, as short strings; the evidence category; the timing basis and whether it
is explicit or inferred; the promotion status; **every** reason it was refused; a plain
explanation; and the id of the observation it became, when promoted.

## `event_classification_rules_v1`

Deterministic. It reads a title's words and a post's structured tags. No body, no
language model, no embeddings, no similarity. A title is folded to lowercase words;
a phrase matches only as whole consecutive words. **False negatives are accepted; a
false event is not.** Wherever a title could be read two ways, the answer is no.

Status: `internal_precision_first_unvalidated`. The rule set is pinned by a fingerprint
in the tests; changing a rule is a new version.

### A. Type

A post is read at all only if it comes from the game's own announcement feed
(`feedname` equal to `steam_community_announcements`). Posts syndicated from elsewhere
are never evidence of an event, whatever their titles say.

| Rule | Type | Evidence required | Timing without a date |
|---|---|---|---|
| `patch_notes_hotfix` | minor patch | tag `patchnotes` **and** "hotfix" / "hotfixes" / "hot fix" in the title | publication time |
| `patch_notes_major_update` | major patch | tag `patchnotes` **and** "major update" in the title | publication time |
| `season_start` | season | "season" followed by a number | publication time, only if the title says it is live |
| `free_weekend` | free weekend | "free weekend" | same |
| `expansion_release` | expansion | "expansion" | same |
| `dlc_release` | DLC | "DLC" | same |

The type is established only when exactly one type matches and nothing in the title
speaks against it:

- **More than one type** → `ambiguous_type`.
- **Filed as patch notes with no size stated** → `patch_size_not_established`. Whether a
  patch is major or minor is taken from the publisher's words, never guessed.
- **A word that makes the title something other than an occurrence** → refused as
  `title_not_about_an_occurrence`. The list: award(s), blog, bundle, cancel(l)ed,
  concluded, contest, delay(ed), devlog, diary, discount, drops, ended, ending, ends,
  feedback, final, giveaway, interview, last, livestream, merch, nominee, over,
  postponed, recap, retrospective, review(s), roadmap, sale, soundtrack, stream, survey,
  teaser, trailer, wishlist.
- **Nothing matches** → `no_type_evidence`.

### B. Timing

Asked only once the type is settled. A publication timestamp is never an event time by
itself.

**An explicit date in the title** is read in three forms only, each with a year:
`2026-10-20`, `October 20, 2026` (also `Oct 20th, 2026`), `20 October 2026`. Then:

- anything else date-like (a month and day with no year, digits around a slash, an
  impossible date) or more than one date → `ambiguous_date`;
- the title must contain a start word (arrives, available, begins, launches, live,
  releases, starts) and no end word (by, deadline, through, thru, till, until), or →
  `date_role_unclear`;
- before the UTC day of publication → `date_before_publication`;
- more than 180 days ahead → `date_too_far_ahead`;
- otherwise the event starts that day, day precision. Timing is **explicit**.

**With no date**, the post's publication time may stand as the start, to the second,
only where the rule allows it:

- any of announced, announcement, announcing, arriving, coming, countdown, incoming,
  later, next, planned, preview, reveal(ed), schedule(d), soon, tomorrow, tonight,
  upcoming, when, will → `title_says_not_yet`;
- the four title rules also need the title to say the thing is live: "now live", "live
  now", "is live", "out now", "available now", "now available", "has begun", "has
  started", "starts now", "begins now". Without it → `no_timing_evidence`;
- the two patch-notes rules do not need that phrase. Their accepted basis is that the
  publisher filed the post as patch notes, and patch notes describe a build that has
  shipped. This is the one inference in the rule set, and it is recorded on every such
  candidate as timing that is **inferred**, not explicit.

No end time is ever proposed in v1.

## The promotion gate

A candidate is promoted only when **all** of these hold: the game is bound; the source
is cleared at this stage; the type is established; the timing is established. Otherwise
it stays a candidate and lists every reason.

A promoted candidate becomes one `EventObservation`, made through the event engine's own
`observe`, which refuses anything not bound to exactly one game. `EventObservation` was
**not extended**: it already carries the source's item id (`source_event_id`) and
reference. The classification rule and timing basis stay on the candidate, linked by
`observation_id`. The observation's title is the post's title; the body is never
anywhere.

If a post is later edited, the new version is read again. Still an event: a new
observation with the same source id, which the reconciler applies as a revision of the
same event. No longer an event: a `withdrawn` observation is appended, so the event
stops counting from then on and never before.

## Coverage

A fetch that **succeeds** records a `CoverageCheck` for the game that was asked about,
whatever came back: no posts, posts that classify as nothing, candidates that were
refused, or only unreadable entries. Coverage means *the source was checked*, not *an
event was found*. A checked game with nothing promotable has Event Heat **0**, which is
a real answer.

A fetch that **fails**, or was never sent because the request budget was spent, records
no coverage. That game's event intelligence stays unavailable, which is not zero.

An app the catalogue does not bind gets no coverage.

## Storage

`EventSourceStore` is local, offline and append-only, and separate from `EventStore`:

```
{root}/event-source-v1/fetches/{observed day}/{content hash}.ndjson
{root}/event-source-v1/items/{observed day}/{content hash}.ndjson
{root}/event-source-v1/candidates/{observed day}/{content hash}.ndjson
```

- A file is one batch, named by the hash of its content and written once. Nothing is
  rewritten or deleted.
- A post is kept once per distinct content. Fetching it again unchanged adds nothing. A
  post whose content changed is a new version beside the old one, including a post that
  goes back to an earlier wording.
- A candidate is a reading of one version under one version of the rules.
- A fetch record holds the request's parameters, outcome and the field names seen. It
  never holds a response.
- Every read takes an `as_of` and uses only what had been observed by then.
- An item carrying a field its policy does not permit is refused whole, not trimmed.

The default location is `.metaspike/event-sources/`, which is not in version control.

**Future warehouse mapping, not created:** three append-only tables, one row per record:
`ms_events.source_fetches` (partitioned by day of `observed_at`, keyed by `fetch_id`),
`ms_events.source_items` (keyed by `item_version_id`, clustered by `source`,
`source_item_id`) and `ms_events.event_candidates` (keyed by `candidate_id`, joined to
items on `item_version_id` and to event observations on `observation_id`). Promoted
observations and coverage checks keep the mapping in event-intelligence.md. No cloud
resource was created.

## Historical backfill

`backfill(app, max_requests=N)` walks backwards with the documented `enddate`: each page
asks for posts earlier than the oldest post of the page before. It stops when a fetch
fails, a page is empty, a page makes no progress, or N requests have been made. It is
implemented and tested offline. **No live backfill was run**, and the command refuses
one: any backfill must be separately budgeted and approved.

Two times are kept for every post, and they are not the same. `published_at` is when
the source says the post went out. `observed_at` is when MetaSpike saw it. A post
fetched today in a backfill was not *known to MetaSpike* in the past, and the event
store's point-in-time reads use `observed_at`, so a backfilled event does not appear in
an earlier snapshot. A later backtest that wants to treat a post as known from its
publication time will have to say so as an explicit assumption, as the demand backtest
does for publication lag, and accept that edits made since cannot be reconstructed.

The backtest already takes an event store. A test proves the wiring with synthetic
posts: an event is unavailable before it was seen and counted after, and score v0 is
identical with and without it. That proves the plumbing and nothing about prediction.
Evidence status remains `framework_only`.

## In the artifacts (contract 1.4.0, internal)

- `source-health.json` gains `event_sources`: per source, its status (`not_collected`
  or `collected`), every stage clearance, checks made, games checked, posts observed,
  candidates, classified, promoted, not promoted, unresolved, refusal counts, promoted
  event types, checks made after the evaluation moment, and a row per game. **Counts
  only**: no title, reference or text of a post.
- `manifest.json` gains `distribution` and `restricted_sources`.
- Radar and game artifacts are unchanged in shape. They hold promoted events only.

The developer interface shows the event source on the Sources page and the
private-development notice wherever it applies. Unpromoted candidates appear nowhere but
as counts.

## Command

```
uv run metaspike ingest-events --replay tests/fixtures/steam_news --apps 730 570
uv run metaspike ingest-events --live --apps 730 570 1086940
```

`--replay` answers from files and never opens a connection. `--live` is a supervised
probe: at most three apps, each bound to a catalogue game, one request each, no
backfill. Either prints counts and the response's field names, never a post.

The fixtures under `tests/fixtures/steam_news` are **synthetic**, written by hand. No
real response is kept in the repository.

## The supervised live probe (2026-10-11)

One probe was run, after every offline gate had passed and after a read-only check that
production was untouched and the soak had not started. Its exact scope:

| | |
|---|---|
| When | 2026-10-11, 21:02:56 to 21:02:57 UTC |
| Where | The owner's machine. No production infrastructure, job, bucket or identity |
| Credential | None read, held or sent |
| Apps | 3, each bound to a catalogue game: 730 (Counter-Strike 2), 570 (Dota 2), 1086940 (Baldur's Gate 3) |
| Requests | **3**, one per app, of a hard budget of 3. No retry, no backfill |
| Parameters | `appid`, `count=5`, `maxlength=1` |
| Result | HTTP 200 for all three |
| Steam quota used | 3 of the day's 100,000, recorded in the local ledger |

What it showed about the response's shape:

- Envelope fields: `appid`, `count`, `newsitems`. `count` is the app's total number of
  posts (1,771, 3,253 and 1,896), not the number returned.
- Post fields: `appid`, `author`, `contents`, `date`, `feed_type`, `feedlabel`,
  `feedname`, `gid`, `is_external_url`, `tags`, `title`, `url`. All twelve were already
  handled; none was new. `author` and `contents` were present and were not read.
- Feed names seen: `steam_community_announcements` on 6 posts; four syndicated feeds on
  the other 9. Posts from the game's own feed had `feed_type` 1 and the syndicated ones
  0, in this sample.
- `is_external_url` was true on all 15, the game's own posts included, so it does not
  tell the two apart and is not used.
- The only tag seen was `patchnotes`, on 4 posts.
- All 15 posts were readable; none was rejected.

These are observations of three responses, not guarantees.

**No response was kept as a fixture.** The policy does not clearly allow a publisher's
titles to be committed to a repository, so nothing from the probe is in version
control. The 15 posts are in the local, untracked source store only (titles, URLs, tags
and feed names; no body and no author).

### What the real data came to

| | |
|---|---|
| Posts observed | 15 |
| Candidates created | 15 |
| Candidates classified (a type established) | **0** |
| Candidates promoted to events | **0** |
| Not promoted | 15 |
| Refused: not the game's own feed | 9 |
| Refused: filed as patch notes, size not stated | 4 |
| Refused: no type evidence | 2 |
| Event types observed | none |
| Event observations written | 0 |
| Coverage checks recorded | 3 (one per game: the source was checked) |

**No candidate could safely become an event.** The classifier was not changed in
response, and no post was inspected to find a rule that would have promoted it.

So, stated exactly: for these three games Event Heat has real **coverage** as of the
probe and **no real event**. Their event intelligence at that moment is *available with
a heat of 0*, from real checks. No game has a non-zero Event Heat from real data. The
replay bundle the developer interface normally shows is evaluated on 4 October, a week
before the probe, so in it the source is still `not_collected`; it reports the three
later checks as taking no part.

The four patch-notes posts are the clearest example of what the gate is for: the
publisher's tag says "patch", nothing says how large, and the rules do not guess.

## Limitations

- The classifier reads titles only. Most real announcements will not be promoted. That
  is intended.
- `steam_community_announcements` and the `patchnotes` tag are observed values, not
  documented ones. If Steam changes either, posts stop classifying; they do not start
  misclassifying.
- Publication-time anchoring for patch notes is an inference. A publisher that posts
  notes ahead of a patch without saying so in the title would give a start that is
  early.
- A post is read when a new version of it is stored. Posts already stored are not read
  again when the rules change; that needs a re-read step that does not exist yet.
- A post that disappears from a feed is not noticed: a bounded fetch cannot tell
  "deleted" from "older than the page".
- English only. A title in another language matches nothing.
- One source. Nothing here is cross-checked against a second.
- Event Heat itself is uncalibrated (`event_heat_rules_v1`), unchanged here, and not
  part of score v0.
- Nothing Steam-derived is cleared for public or commercial use.
