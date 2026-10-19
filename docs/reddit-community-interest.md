# Reddit community interest (U6.2)

**Offline, private, on the product branch only.** MetaSpike has never called Reddit.
There is no account, app registration, OAuth client or token, and nothing here is in
production. This describes a parser, a bounded collector, an association rule, theme
rules, candidate angles and a reading, all exercised on synthetic Reddit-shaped data.

Interest in a game and competition for it are not the whole of a video opportunity: a
creator also needs to know what players are asking and arguing about. This checkpoint
adds the first evidence of that as a described **sample** of discussion. It adds no
score, no weight and no opportunity ranking.

## What could and could not be verified

**Reddit's own documentation and terms could not be read.** `reddit.com/dev/api`,
`support.reddithelp.com` and `redditinc.com` refused every fetch from this environment
(HTTP 403 and blocked hosts) on 2026-10-14. Everything below about Reddit's contract
comes from Reddit's long-standing public API shape and from **secondary** descriptions
of the current terms. It is marked NOT VERIFIED and must be confirmed from Reddit's own
pages before anything real is done.

| Point | What secondary sources report | Status |
|---|---|---|
| Search | `GET /r/{subreddit}/search` with `q`, `restrict_sr`, `sort`, `t`, `limit` (default 25, maximum 100), `after` | NOT VERIFIED |
| Listing | `{"kind": "Listing", "data": {"after", "before", "children"}}`, posts as `{"kind": "t3", "data": {...}}` | NOT VERIFIED |
| Rate limit | 100 queries a minute per OAuth client, averaged over 10 minutes; `X-Ratelimit-Used`, `-Remaining`, `-Reset` headers | NOT VERIFIED |
| Access | Every application needs Reddit's approval under its Responsible Builder Policy, non-commercial ones included; commercial use needs a separate agreement | NOT VERIFIED |
| Deletion | Content deleted on Reddit must be removed; Reddit strongly recommends deleting stored content within 48 hours | NOT VERIFIED |

Nothing here claims access has been denied: it has not been requested.

## The code path

`src/metaspike/community_sources/`:

| File | What it holds |
|---|---|
| `reddit.py` | The source policy, the query model, listing parsing, the local request budget, the adapter, the replay client. |
| `collect.py` | `collect_community_interest`: each query once, a few pages at most. |
| `store.py` | `CommunityStore`: append-only listing pages, read as of a moment. |
| `association.py` | `community_association_rules_v1`: supported, uncertain or excluded. |
| `themes.py` | `community_theme_rules_v1` and candidate angles. |
| `reading.py` | `read_community_interest`: what the sample says about one game at a moment. |
| `synthetic.py` | The synthetic scenario. |

Reused, unchanged in meaning: the source-policy model (one new purpose,
`COMMUNITY_INTEREST`, sharing creator supply's refusal rules); the U6.1 catalogue name
matching (`GameNames`) and its six sample states; the title tokeniser; the U6.0 evidence
reading and the U6.0.1 provenance rule.

Read from a post, and nothing more: id, subreddit, title, permalink, creation time,
score, comment count, flair text, and whether its link points at the reserved
`.invalid` domain (only to recognise synthetic data; the link is not kept). **Never
read:** usernames or author ids, post bodies, comments, awards, media, profiles,
private communities, messages.

## The collection guard (U6.3)

The adapter takes either an offline `RedditReplay`, which answers from recorded files
and holds no client or transport, or a network-capable client that it accepts only if
`REDDIT_DATA_POLICY` clears collection at a stated stage. Approval is pending, so a
network-capable client is refused before any request, at every stage. See
[creator-opportunity-intelligence.md](creator-opportunity-intelligence.md).

## Queries and samples

A query is explicit and bounded: the canonical game, the words, a **declared scope**
(the game's own community, a general gaming community, or all of Reddit), the subreddit,
communities declared unrelated, a page size of at most 100, `sort=new`, a time window,
and the reason it is asked. The reason and scope are kept with every result.

Each query is asked once per run, for at most three pages. A page is paid for from a
local budget (at most 20 requests a run) before it is sent; one that cannot be paid for
is recorded as not sent. A 429, or a response whose remaining-requests header reads
zero, halts the budget. The budget is local and self-imposed. It is **not** Reddit's
rate limit, and no live scheduler or persistent ledger exists.

Each query sample is recorded as complete, page-limited, rate-limited, budget-limited
or failed. The game's reading uses the same six states as creator supply:
`not_queried`, `expired`, `incomplete`, `no_results`, `ambiguous`, `observed`. With no
community input the game evidence still says `not_integrated`.

A sample is not Reddit. An empty search is not evidence of low interest. A quiet
community is not proof of no interest. A score is not a number of interested people. A
comment count is not independent demand. A post returned twice is one post. None of
these numbers is added to or compared with pageviews, players or video counts.

## Is this discussion about this game?

Reddit gives no structured classification of a post. What it gives is **where** the
post was made, so `community_association_rules_v1` relies on the communities the query
plan declares:

- **Supported**: in the subreddit declared as the game's own community, unless its
  title names only other catalogue games; or in a declared general gaming community,
  with the game's name in the title as whole words, and no other game and no related
  product named.
- **Excluded**: in a subreddit declared unrelated to this game (r/rust is the
  programming language); or the matching words are part of a longer catalogue name.
- **Uncertain**: everything else, including a site-wide search with no declared
  community, several games named in a general community, or a related product named.

A declared scope is a human assertion written into the plan, and the reading reports
it. Questions and comparisons are not penalised: "Rust or DayZ?" in Rust's own
community is a Rust discussion. No probability, no weight, no resolver involvement.

**A defect found and fixed here, shared with U6.1.** Some catalogue aliases are in
another script (a Korean title of *Dead Space 3*). The title tokeniser keeps Latin
letters and digits only, so such an alias became a bare "3" and matched every title
containing that number. A name is now used for matching only if every letter survives
the tokeniser, it is not an executable file name, and it has at least three letters and
digits. The effect before was conservative: affected videos were left uncertain, never
wrongly supported.

## Themes and candidate angles

`community_theme_rules_v1` reads the title alone, against fixed word lists: a question
(ends with "?" or starts with a question word), how-to, update, issue, comparison ("vs",
"better than", "worth it", "should I buy"). A title may carry several themes, or none
(`unclassified`). "X or Y?" is a question, not a comparison: "or" alone is too common.

A **candidate angle** needs at least two distinct supported posts that share a theme
and something specific: the same version number for an update, or at least one shared
content word otherwise. One discussion is one angle, listing all the themes its posts
share. Each angle carries its kind (an explainer, a how-to, an update breakdown, a
problem and fix, a comparison), what the posts share, and every supporting post's id,
community, creation time and retrieval time. A theme seen in one post only is listed
as observed once, not as an angle.

An angle is a **candidate**: it says what the sample showed being discussed more than
once. It is not a validated opportunity and says nothing about whether a video would do
well. No angle is invented: the shared words come from the titles themselves.

## Time

`created_at`, `retrieved_at` and the reading's `as_of` are separate. A reading uses only
pages retrieved by `as_of` and only posts created by `as_of`, including a post the
parser kept because it was within an hour of its own retrieval. A search made later is
never used to describe earlier, whatever its posts' creation times. Scores and comment
counts are those of the latest retrieval used, with its time; a later count is never
shown for an earlier moment. Pages older than 48 hours at `as_of` are past the retention
limit and are not read.

## Provenance and policy

`REDDIT_DATA_POLICY`, purpose `community_interest`. Approval is **required and
pending**, so the policy refuses Reddit data at every stage, private development
included.

- An input that holds no Reddit data (the scenario's, declared synthetic, or a store
  in which every post links to the reserved domain) may be read at private development
  only, and is marked synthetic.
- Any other local store may hold Reddit data and is **refused**, not merely unverified.
- No input can be verified: no Reddit recording is pinned. No option declares one real.
- Retention: 2 days (from the 48-hour recommendation); the store reports what expired;
  no deletion job exists.
- `derived_metrics_allowed` unknown; display and attribution rules unknown; public
  launch and collection not cleared.
- Nothing under `publishing/` or `schemas/` refers to the source; artifact contract 1.5.0.

Being noncommercial does not grant access or any data use.

## The demonstration

```
uv run metaspike evidence --as-of 2026-10-20T12:00:00+00:00 --synthetic-scenario \
    --game rust --game dota-2 --game counter-strike-2 --game elden-ring --game minecraft
```

Every community line is marked `[SYNTHETIC]`. It shows:

| | Where |
|---|---|
| A. Several relevant questions | Rust: two "how do I stop getting offlined" posts |
| B. An ambiguous title | Rust: the language (excluded) and a film (uncertain) |
| C. Several discussions of one patch | Rust: three posts about version 2.3 |
| D. Two distinct supported topics | Rust: the patch and the offline raids |
| E. A bounded query with no results | Dota 2 |
| F. A page limit | Rust, its own community |
| G. Rate-limited, and budget-limited | Counter-Strike 2 (429); Elden Ring (not sent) |
| H. A duplicate across queries | Rust: one post from two queries, counted once |
| I, J. Created and retrieved after the moment | A later search with a newer post: never read |
| K. A later, higher score | Not shown in the earlier reading (tested at two moments) |
| L. A question that becomes a candidate angle | Rust: the offline-raid explainer |
| M. An unrelated discussion | Rust: r/rust, excluded |
| N. Synthetic data that cannot count | Every Reddit source is synthetic and never usable |

`--community-dir DIR` reads a local store, which is refused unless it holds no Reddit
data. No command fills a store from Reddit.

## Tests

`tests/test_community_interest.py`, 13 tests, all offline.

## Before live collection

1. Read Reddit's Developer Terms, Data API Terms and Responsible Builder Policy from
   Reddit's own pages; confirm every NOT VERIFIED point above.
2. Request access, as a noncommercial application, and receive approval.
3. A deletion process for content deleted on Reddit, and short retention.
4. A rate limiter that follows Reddit's headers across runs.
5. Reviewed query plans: which communities are each game's, and which are unrelated.
6. A check of the association and theme rules against real titles.

## Before public launch

Everything above, and: Reddit's approval for showing derived output; the display and
attribution rules; a decision on whether any post-derived text may be shown at all;
and a reviewed public data contract.

## What remains for opportunity scoring

Nothing here weighs discussion against demand or competition. A scoring rule would need
calibrated thresholds for every sample, an explanation that keeps each source's meaning,
and an evaluation against outcomes. None exists yet.
