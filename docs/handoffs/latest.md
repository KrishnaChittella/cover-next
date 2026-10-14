# Handoff: latest checkpoint (product branch)

## PRODUCT BUILD U6.2: OFFLINE REDDIT COMMUNITY-INTEREST INTEGRATION — CHATGPT REVIEW REQUIRED

Branch `product/local-ui` only. Offline. No live source call, no account, no
credential, no cloud access, no deployment, no pull request, no merge. **No later
checkpoint has been started**: no opportunity scoring, no public publishing, no
production integration.

1. **Approved starting SHA:** `b73091cd1d64230d94a5ce5476be7adf798b28bb` (U6.1.1
   handoff). Verified at the start: on `product/local-ui`, at that commit, tree clean.
2. **Implementation SHA:** `bb997253d4994d7af4a628b5fd62fd6880e45a86` — `feat: integrate offline Reddit community interest`.

## Things to review first

1. **Reddit's own pages could not be read.** `reddit.com/dev/api`,
   `support.reddithelp.com` and `redditinc.com` refused every fetch from this environment
   (HTTP 403 and blocked hosts). The API shape and the terms come from secondary sources
   and are marked **NOT VERIFIED** throughout. Nothing real can proceed until they are
   read from Reddit itself.
2. **Reddit data is refused at every stage.** Secondary sources report that every
   application, noncommercial included, needs approval under Reddit's Responsible
   Builder Policy. The policy therefore has approval `required`/`pending`, and the
   existing policy model refuses it everywhere. To make an offline demonstration
   possible at all, the evidence gate reads a community input at private development
   **only if it holds no Reddit data**: declared synthetic, or every post in it linking
   to the reserved `.invalid` domain. Any other local store is refused, not merely
   unverified. This is a judgement call; it is stricter than the YouTube path.
3. **A defect found in U6.1's name matching, fixed here.** Catalogue aliases in another
   script (Korean, Chinese, Cyrillic) lost their letters to the title tokeniser and became
   bare numbers ("데드 스페이스 3" → "3"). Every title containing such a number was read
   as naming those games. In U6.1 that could only leave a video *uncertain*, never wrongly
   supported. `GameNames.of` now skips names whose letters do not survive the tokeniser,
   executable file names, and names under three letters and digits. This touches
   `src/metaspike/creator_sources/association.py`; all 25 YouTube tests pass unchanged.
4. **Association rests on declared communities.** Reddit gives no classification of a
   post, so "supported" means the post is in the community the query plan declares as
   the game's own, or names the game in a declared gaming community. A declared scope is
   a human assertion and is reported as one.
5. **The private evidence report is now `game_evidence_v3`** (community interest).

## 3. Exact files changed

| File | Change |
|---|---|
| `src/metaspike/community_sources/` (7 files) | New: policy, query model, listing parser, budget, adapter, replay; collector; store; association; themes and angles; reading; synthetic scenario. |
| `src/metaspike/domain/source_policy.py` | `SourcePurpose.COMMUNITY_INTEREST`, sharing creator supply's refusal rules; the refusal wording is now per purpose (YouTube's text is unchanged). |
| `src/metaspike/creator_sources/association.py` | The name-matching fix (item 3). |
| `src/metaspike/evidence/assemble.py`, `model.py`, `provenance.py`, `render.py` | The community row and reading, its gate, detection, rendering; report v3. |
| `src/metaspike/cli.py` | `--community-dir`; the scenario includes community interest. |
| `tests/test_community_interest.py` | New, 13 tests. |
| `tests/test_creator_supply.py` | Expected lists now include the new source, purpose and the two community modules that reuse U6.1 code; report version. |
| `tests/test_evidence.py` | Report version string. |
| `docs/reddit-community-interest.md` | New. |
| `docs/multi-source-evidence.md`, `docs/youtube-creator-supply.md`, `README.md` | Pointers. |

Nothing under `infra/`, `.github/`, `apps/`, `schemas/`, `tests/fixtures/` or
`publishing/`. No Reddit file was added under `tests/fixtures/`.

## 4. Reddit API contract investigated

| Point | Reported by secondary sources | Status |
|---|---|---|
| `GET /r/{sub}/search` | `q`, `restrict_sr`, `sort`, `t`, `limit` (default 25, max 100), `after` | NOT VERIFIED |
| Listing | `kind: Listing`, `data.after`, `data.children[]` of `kind: t3` | NOT VERIFIED |
| Rate limit | 100 queries/minute per OAuth client, 10-minute average; `X-Ratelimit-*` headers | NOT VERIFIED |
| Deletion | Remove content deleted on Reddit; delete stored content within 48 hours recommended | NOT VERIFIED |

Fields read from a post: id, subreddit, title, permalink, creation time, score, comment
count, flair, and whether its link is on `.invalid` (the link itself is not kept).
Never read: usernames or author ids, bodies, comments, media, profiles, private
communities, messages.

## 5. Source-access conditions

Reported: approval required for every application under the Responsible Builder Policy;
free within the rate limit for noncommercial use once approved; commercial use needs a
separate agreement. Not requested, not denied. `REDDIT_DATA_POLICY`: approval
required/pending; derived output unknown; retention 2 days; display and attribution
unknown; cleared for private development only on the basis that no Reddit data is held;
collection, public launch and commercial use not cleared. Being noncommercial grants
nothing by itself.

## 6. Query and sample semantics

A query names the game, the words, a declared scope (own community, general gaming,
site-wide), the subreddit, communities declared unrelated, a page size, and why it is
asked. One run, at most three pages a query, from a local budget of at most 20
requests; a 429 or a zero remaining-requests header halts it. Each query is recorded as
complete, page-limited, rate-limited, budget-limited or failed. The game's reading uses
the six creator-supply states; without input it is `not_integrated` as before.

## 7. Game and topic relevance (`community_association_rules_v1`)

Supported: in the declared own community (unless only other games are named), or named
alone in a declared gaming community. Excluded: in a community declared unrelated, or
the match is part of a longer catalogue name. Uncertain: everything else. Deterministic;
no probability or weight; no resolver involvement; no game created or merged.

## 8. Community-interest evidence model

Per game: queries and completeness; candidate, supported, uncertain and excluded posts;
duplicates across queries; distinct supported communities; creation recency; how many
supported posts carry a score and a comment count; themes; candidate angles.
`total_discussion_known` is always false. A score is not a count of people, a comment
count is not independent demand, and nothing is combined with other sources' numbers.
Score v0, demand and events are unchanged with or without the input (tested).

## 9. Evidence-backed candidate angles (`community_theme_rules_v1`)

Themes from fixed word lists on titles: question, how-to, update, issue, comparison; or
none. An angle needs at least two distinct supported posts sharing a theme and something
specific (a version number, or a content word from their titles). It names its kinds,
what is shared, and each supporting post's id, community, creation and retrieval time.
Status `candidate_not_validated`. A theme in one post only is listed, not an angle. In
the scenario: "update or patch breakdown — about version 2.3, 3 posts" and "explainer /
how-to — about getting, offlined, stop, 2 posts".

## 10. Point-in-time correctness

`created_at`, `retrieved_at` and `as_of` kept apart. Only pages retrieved and posts
created by `as_of` are used, including a post the parser kept within its one-hour
tolerance. A later search never describes earlier. Scores and comments are those of the
latest retrieval used; a later score (340) is not shown at an earlier moment (12). Pages
older than 48 hours are not read. Tested at four moments.

## 11. Source-policy and provenance tests

The scenario's store passed as a local store is read and recognised as synthetic; a
local store with nothing synthetic about it is refused ("required approval has not been
granted"); synthetic input is refused outside private development; a verified claim
without a pinned digest is refused; a missing policy is refused; nothing in
`publishing/`, `schemas/`, jobs, scheduling, warehouse, pipeline or sources refers to
Reddit; artifact contract 1.5.0.

## 12. Offline demonstration

```
uv run metaspike evidence --as-of 2026-10-20T12:00:00+00:00 --synthetic-scenario \
    --game rust --game dota-2 --game counter-strike-2 --game elden-ring --game minecraft
```

Run. Shows cases A–N (table in `docs/reddit-community-interest.md`). Every line is
marked `[SYNTHETIC]`; no title is printed.

## 13. Quality gates (2026-10-14, on the committed tree)

- `uv run ruff format --check` — 250 files already formatted
- `uv run ruff check` — All checks passed
- `uv run mypy` — no issues in 234 source files
- `uv run pytest` — **1,578 passed, 8 deselected in 393.60s** (1,565 before, plus 13)
- `uv run metaspike export-schemas --check` — Schemas are up to date
- `uv run metaspike evaluate-resolver` — exit 0; golden set v3, 200 cases, 0 false auto merges
- `tests/test_community_interest.py` 13 passed; `tests/test_creator_supply.py` 25 passed;
  `tests/test_evidence.py` 32 passed

Four test expectations of mine were wrong on first run and corrected: an assumed order
of same-moment fetches, a word ("broke") not in the issue list, and two U6.1 lists that
now legitimately include Reddit. A one-line doc edit followed the full run; the three
doc-reading test files were re-run: 146 passed. Not run: `live` tests; the four `slow`
catalogue proofs (no catalogue code changed). Frontend not touched.

Frozen rules unchanged: `resolver_v2`, `baseline_rules_v1`, `signal_semantics_v2`,
`metaspike_score_v0`, the event, validation and outcome rules, golden labels, U5.2/U5.3,
artifact contract 1.5.0. Every existing source policy unchanged.

## 14. Known limitations and blockers

1. **Blocker:** Reddit's documentation and terms must be read from Reddit's own pages.
2. **Blocker:** access must be requested and approved before any Reddit data is used.
3. No deletion job; no rate limiter across runs; no real query plans.
4. Association depends on declared communities; site-wide posts are always uncertain.
5. Themes are word lists on titles: no bodies, no comments, no semantics. Many real
   questions will be unclassified; "X or Y?" is not a comparison.
6. Angles are candidates only; nothing validates them as opportunities.
7. Everything is exercised on invented data.
8. No opportunity scoring: no rule weighs discussion against demand or competition.

## 15. Git-only production isolation

C2-F isolation: Git-only verification. No GCP access, no production action.

## 16. No later checkpoint

Nothing beyond U6.2 has been started.

STOP.
