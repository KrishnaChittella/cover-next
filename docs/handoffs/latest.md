# Handoff: latest checkpoint (product branch)

This file is the short-lived handoff from Claude Code to ChatGPT. **This copy lives only
on the branch `product/local-ui`.** The handoff on `main` is the C2-F soak's and was not
touched. It supports communication only and replaces no code, test or document.

Production baseline: **PHASE 4 PART C2-F: ARMED AND SCHEDULED — SEVEN-DAY SOAK PENDING**
(true at 21:04 UTC on 2026-10-11: the first tick is 2026-10-12T00:00Z and no scheduler
attempt has been made)

Parallel checkpoint: **PRODUCT BUILD U4: EVENT SOURCE INGESTION COMPLETE — CHATGPT REVIEW REQUIRED**

Private development only. Nothing was deployed, published or connected to production.
One bounded, supervised live probe was run; its exact scope is below.

Full description: `docs/event-sources.md`.

## One decision that needs your review

The existing build refuses any *demand* source whose derived-metrics permission is
unanswered, at every stage. Steam's is unanswered. Applied to events as it stands, that
rule would keep every Steam-derived event out of the artifacts and so out of the local
interface.

I gated events **by deployment stage** instead: event data may enter a bundle only at a
stage the source's policy clears for event intelligence (today, private development
only). Any such bundle is marked on its manifest, `distribution:
"private_development_only"` with the source under `restricted_sources`; the validator
and the interface both refuse a bundle that carries such data unmarked, and the
interface shows a notice on every page.

This makes the artifact bundle a place private-only data can be, which it was not
before. The demand-source rule is unchanged. If you would rather events follow the
stricter demand rule, the change is one condition in
`publishing/build.py::_restricted_event_sources`, and the interface would then show
event sources only as "not collected".

## Source policy

`STEAM_NEWS_POLICY`, source id `steam_news`. Its own policy, not the catalogue's or the
player counts'. New purpose `SourcePurpose.EVENT_INTELLIGENCE`.

| | |
|---|---|
| Private development | cleared |
| Private collection | **not cleared** |
| Public launch | **not cleared** |
| Commercial production | **not cleared** |
| Raw / historical storage | restricted (conditional) |
| Derived events | **unknown** |
| Redistribution / display | restricted; a publisher's title: **unknown** |
| Commercial use | **unknown** |
| Attribution | required |
| Budget | 1 request/s self-imposed; 100,000 calls/day documented; one budget group with every Steam source |
| Publication | a post's own timestamp; lag 0, self-imposed, undocumented |
| Never read | post body (`contents`), `author` |

Recorded as unresolved on the policy: the Web API terms are written around a key and
this method takes none; derived data is not addressed; a post is the publisher's text,
not Valve's; the Subscriber Agreement's automation clause; the undocumented response;
commercial use. Unknown was left unknown.

Enforced at ingestion (before any request), at storage (per field) and at the build.

## Adapter

`EventSourceAdapter` protocol (`event_sources/base.py`): `fetch` and `backfill`,
returning immutable `SourceItem`s with source, source game id, source item id,
`published_at`, title, optional summary, reference, raw feed and tag fields,
`observed_at` and provenance. No classification, scoring or canonical game in it.

`SteamNewsAdapter`: `GET https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/`
with `appid`, `count` (5; never above 20), `maxlength=1`, and `enddate` for a backfill
page. No key. Only `gid`, `title`, `date` are required; other fields optional, unknown
ones ignored. A bad entry fails alone (position and reason, never content); a bad
envelope fails the request. A title is plain text, never parsed or rendered. No link is
followed. Uses the existing `LimitedHttpClient`, Steam limiter and `AttemptBudget`; one
transmission per fetch, no retry.

## Event candidate

`EventCandidate`: canonical game and the binding it resolved through; source item id and
item version; announced and observed times; proposed type, start and precision where
established; classifying rule and all matching rules; evidence; evidence category;
timing basis and whether explicit or inferred; promotion status; **every** refusal; an
explanation; the observation id when promoted.

## `event_classification_rules_v1` (exact)

Deterministic; titles and structured tags only. No LLM, embeddings or similarity. Pinned
by fingerprint.

Read at all only if `feedname == "steam_community_announcements"`.

| Rule | Type | Needs | Without a date |
|---|---|---|---|
| `patch_notes_hotfix` | minor patch | tag `patchnotes` + "hotfix"/"hotfixes"/"hot fix" | publication time |
| `patch_notes_major_update` | major patch | tag `patchnotes` + "major update" | publication time |
| `season_start` | season | "season" + a number | publication time only with a live phrase |
| `free_weekend` | free weekend | "free weekend" | same |
| `expansion_release` | expansion | "expansion" | same |
| `dlc_release` | DLC | "DLC" | same |

Type refusals: `not_an_official_feed`, `no_type_evidence`, `ambiguous_type` (more than
one type), `patch_size_not_established` (tag, no size word),
`title_not_about_an_occurrence` (trailer, roadmap, recap, sale, delayed, ends, last,
and the rest of a fixed list).

Timing, asked only once the type is settled:

- **Explicit date**, three forms with a year (`2026-10-20`, `October 20, 2026`,
  `20 October 2026`). Refused as `ambiguous_date` (no year, slashed digits, impossible
  date, more than one), `date_role_unclear` (no start word, or an end word such as
  "until"), `date_before_publication`, `date_too_far_ahead` (over 180 days). Otherwise
  day precision, explicit.
- **Publication time**, to the second, only where the rule allows. Refused as
  `title_says_not_yet` (coming, soon, tomorrow, will, preview, ...) or
  `no_timing_evidence`. The four title rules need "now live", "is live", "out now",
  "available now" or one of six similar phrases. The two patch-notes rules do not: the
  accepted basis is the publisher's own patch-notes filing. That is the one inference in
  the rule set and each such candidate records its timing as inferred.

No end time is proposed.

## Promotion gate

All of: game bound; source cleared at this stage; type established; timing established.
Otherwise a candidate with its reasons. A promoted candidate becomes one
`EventObservation` through the event engine's own `observe`.
**`EventObservation` was not extended**: it already carries the source item id and
reference; rule and timing basis stay on the candidate, linked by `observation_id`. A
post later edited into a non-event appends a `withdrawn` observation.

Resolution is the catalogue's existing binding (bound app, confirmed by a structured
external id), the standard the player-count sampler uses. No title is read. An unbound
app's post is a source-level candidate only: no observation, no coverage.

## Coverage

A successful fetch records a `CoverageCheck` for the game asked about whatever it
returned: nothing, posts that classify as nothing, refused candidates, or only
unreadable entries. That game then has Event Heat 0, available. A failed fetch, or one
not sent because the budget was spent, records none: unavailable, not zero.

## Storage

`EventSourceStore`, separate from `EventStore`, under
`{root}/event-source-v1/{fetches,items,candidates}/{observed day}/{hash}.ndjson`.
Append-only, content-addressed, idempotent; an edited post is a new version (including
one reverting to earlier wording); point-in-time reads; an item with a field the policy
forbids is refused whole. Only promoted observations and coverage checks go to
`EventStore`, which is all Event Heat reads. The event engine does not import this
package. No cloud table was created; the future mapping is documented.

## Historical support

`backfill(app, max_requests=N)` pages backwards with the documented `enddate`, stopping
on failure, an empty page, no progress, or N requests. Implemented and tested offline
only. **No live backfill was run**; the command refuses one.

Both `published_at` and `observed_at` are kept. A backfilled post is not treated as
known in the past: point-in-time reads use `observed_at`. Using publication time as
availability in a later backtest would be an explicit assumption, not yet made.

The backtest already takes an event store. A synthetic test proves the wiring
(unavailable before a post was seen, counted after; score v0 identical). Evidence status
stays `framework_only`. No event performance is claimed.

## Live probe: performed

After every offline gate had passed, and after a read-only C2-F check.

| | |
|---|---|
| When | 2026-10-11 21:02:56 to 21:02:57 UTC, from the owner's machine |
| Apps | 730, 570, 1086940 (Counter-Strike 2, Dota 2, Baldur's Gate 3), all bound |
| Requests | **3**, one per app, hard budget 3, no retry, no backfill |
| Credential | none |
| Result | HTTP 200 ×3 |

Shape observed: envelope `appid`, `count`, `newsitems` (`count` is the app's total, not
the number returned); twelve post fields, all already handled; own-feed posts had
`feed_type` 1 and syndicated ones 0; `is_external_url` was true on all 15 and is not
used; the only tag was `patchnotes`. `author` and `contents` were present and unread.

**No fixture was kept from it.** The policy does not clearly allow a publisher's titles
in a repository. Test fixtures are synthetic and say so. The 15 posts are in the local
untracked store only.

### Real results, candidates versus events

| | |
|---|---|
| Posts observed | 15 |
| Candidates created | 15 |
| Candidates classified | **0** |
| Candidates promoted | **0** |
| Not promoted | 15: not the game's own feed 9; patch notes with no size 4; no type evidence 2 |
| Event types observed | none |
| Event observations written | 0 |
| Coverage checks | 3 |

**Zero candidates could safely become events.** The classifier was not changed
afterwards, and no post was inspected to find a rule that would promote it.

Exactly: those three games have real coverage as of the probe and no real event, so
their event intelligence at that moment is available with a heat of 0. No game has a
non-zero Event Heat from real data.

## Artifacts and UI

Internal contract **1.3.0 → 1.4.0**, Python → JSON Schema → TypeScript.

- `source-health.json`: `event_sources`, counts only (status, stage clearances, checks,
  games checked, posts, candidates classified / promoted / not promoted / unresolved,
  refusal counts, promoted types, checks after the evaluation moment, a row per game).
- `manifest.json`: `distribution`, `restricted_sources`.
- Sources page: one event-source panel. Every page: the private-only notice when it
  applies. No redesign. No candidate view with titles was added. Radar and game
  artifacts hold promoted events only.

The replay bundle is evaluated on 4 October, before the probe, so it shows Steam News as
`not_collected` and counts the three later checks as taking no part.

## Tests and gates

94 new backend tests (`tests/test_event_sources.py`) and 12 new frontend tests, covering
every item in the list given.

Backend: `ruff format --check` clean; `ruff check` clean; `mypy` clean (200 files);
`pytest` **1,450 passed**, 5 deselected (slow marker), run before the probe and again
after it. Frontend: `contract:check`, `typecheck`, `lint`, `test` (**175 passed**),
`build` all pass.

Also changed: an autouse test fixture now points the command line's default local
stores at an empty directory, so no test reads a developer's real local state.

Not done: the new panel was not opened in a real browser; it is covered by component
tests and the production build.

## Unchanged

| | |
|---|---|
| `src/metaspike/scoring` | `3b857c551d5ab5b0`, as before |
| `src/metaspike/features` | `24cf7d170f57d8ab`, as before |
| `src/metaspike/events` (the whole engine) | `333373c61ae6c37f`, as before |
| `events/rules.py` / `event_heat_rules_v1` | `7358ea6563ddcad9`; model fingerprint test passes |
| `src/metaspike/validation` (U3.1) | no diff; the publication-lag regression passes |
| Score v0 golden replay | bit-for-bit |

Event Heat is not in score v0.

## Limitations worth knowing

Titles only, English only, one source. Most real posts will not be promoted, by design.
The feed name and tag are observed values, not documented ones. Posts already stored are
not re-read when rules change. A deleted post is not noticed.

## Production and C2-F isolation (read-only, 21:04 UTC 2026-10-11)

C2-F has **not begun**; there is no autonomous activity to report. Scheduler ENABLED,
`0 * 12-18 10 *`, next run 2026-10-12T00:00:00Z, no attempt. Control generation
`1791684706340016`, unchanged. Terraform state unchanged (latest object generation is
the launch apply's). Executions 35, last `ms-warehouse-lxqp9`. Seven jobs, generation 6,
image `sha256:a8680b2be8b5…`. Ten alert policies; IAM, schemas, secrets and alerts
identical to the previous snapshot. 0 secret versions, 0 user keys. Rows unchanged.
Repository variables unchanged. Last workflow run still 38104941975. `main` =
`cb33198`, level with origin. No pull request. The portfolio repository was not
touched.

The probe called Steam's public API from this machine only. No production job, bucket,
identity or secret was involved, and production made no source call because of U4.

## Commits

`30bc382` feat: add official event source ingestion; then this handoff. Pushed to
`origin/product/local-ui` only. No PR, no merge to `main`.

## Exact next approval required

ChatGPT review of U4, including the stage-gating decision above.

STOP. U5 has not been started.
