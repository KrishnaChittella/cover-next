# Handoff: latest checkpoint (product branch)

## PRODUCT BUILD U6.1.1: YOUTUBE API CONTRACT AND TEMPORAL CLOSEOUT — CHATGPT REVIEW REQUIRED

Branch `product/local-ui` only. Offline. No live source call, no credential, no cloud
access, no deployment, no pull request, no merge. U6.1 was not reverted or redesigned.
No Reddit integration and no opportunity scoring were started.

1. **Starting SHA:** `0cae13fb739636a11c784dea72674f140fd0a5ad` (U6.1 handoff). Verified
   at the start: on `product/local-ui`, at that commit, tree clean.
2. **Implementation SHA:** `37586b4325d9d215b7f42e5d0b47d98efde12ffe` — `fix: close YouTube request and time-boundary gaps`.

## 3. Exact changed files

| File | Change |
|---|---|
| `src/metaspike/creator_sources/youtube.py` | `videos.list` sends `part` and `id` only; `VIDEOS_PARTS` constant; docstrings record the reset time and that `YouTubeQuota` is offline only. |
| `src/metaspike/creator_sources/reading.py` | Details dated after `as_of` are withheld; the reason `video_details_dated_after_the_moment_read_at`; the count `details_withheld_as_future_dated`. |
| `tests/test_creator_supply.py` | 3 new tests; 1 existing test made independent of an ordering it never controlled (below). |
| `docs/youtube-creator-supply.md` | The request contract, the reset time, the offline-only quota account, the time rule. |

Exactly the four expected files. Nothing under `infra/`, `.github/`, `apps/`, `schemas/`
or `publishing/`.

## 4. The unsupported parameter fixed

U6.1 sent `videos.list` with `part=snippet,statistics,topicDetails`, `id=<ids>` and
`maxResults=50`. `maxResults` is not supported with `id` and was removed. The request is
now exactly `part` and `id` (plus `key` only if a key is ever given). The 50-id
self-imposed bound, deduplication, request accounting (1 unit a call), parsing,
missing-id handling and replay are unchanged. `search.list` is unchanged.

## 5. Official API contract evidence

From developers.google.com/youtube/v3/docs/videos/list, read 2026-10-14:

- `maxResults`: "supported for use in conjunction with the `myRating` parameter, but it
  is not supported for use in conjunction with the `id` parameter. Acceptable values are
  1 to 50, inclusive."
- `pageToken`: the same note — not supported with `id`.
- Documented as usable with `id`: `hl`, `maxHeight`, `maxWidth`,
  `onBehalfOfContentOwner`, `regionCode`, `videoCategoryId`.
- `part` values include `snippet`, `statistics` and `topicDetails`, the three asked for.

The regression test captures the outgoing request with an offline mock transport (and a
socket that refuses to connect) and checks: method, host and path; parameters exactly
`{part, id}`; `part` and `id` values and id order; no `maxResults` or `pageToken`;
every parameter within the documented set; batches of 50 and 10 for 60 ids, each id
once; `search.list` still sending its documented parameters.

## 6. Temporal defect root cause

The parser rejects a publication time more than one hour after the response's
*retrieval* time; within that hour it keeps it. The reading filtered search results by
`as_of` but took the latest details retrieved by `as_of` without checking their own
publication time. A detail retrieved at T-30m claiming publication at T+20m was
therefore used at T: its classification made the video supported, and its channel, date
and view count entered the counts.

The fix is in the reading only; the parser's check is unchanged. A detail retrieved by
`as_of` whose publication time is after `as_of` is withheld entirely. The video falls
back to its search snippet (the snippet's channel and date), is `uncertain` with the
reason `video_details_dated_after_the_moment_read_at`, shows no counters, and is counted
in `details_withheld_as_future_dated`. No timestamp is corrected and no earlier detail
is substituted: two details that disagree are left unresolved. Read at or after the
claimed time, the same detail is used.

## 7. Before and after

Run against the pre-fix `youtube.py` and `reading.py` (stashed), with the new tests:

```
FAILED test_videos_list_is_asked_by_id_with_part_and_nothing_else
  assert {'part': ..., 'maxResults': '50'} == {'part': ..., 'id': ...}
FAILED test_nothing_dated_after_the_moment_contributes_to_a_reading
  assert <AssociationState.SUPPORTED> is <AssociationState.UNCERTAIN>
  (SYNtime0002, channel 'UCfuture...', taken from the future-dated detail)
2 failed
```

After the fix: both pass. `test_nothing_dated_after_the_moment_contributes_to_a_reading`
covers cases A–I on one synthetic store read at several moments: a search and details
from before the moment (used); a detail retrieved before but dated after it (withheld);
a search result dated after it, details retrieved after it, a search made after it
(absent); a later, larger view count (not shown earlier); the same store read later (all
used, the detail as retrieved); the boundary one second either side of the claimed time;
no future-dated detail in supported videos or channels; and no earlier detail put in a
contradicting later one's place. `test_the_synthetic_scenario_is_still_deterministic`
covers J: the scenario rebuilt is byte-identical, and its counts are unchanged.

## 8. Full test results (2026-10-14, on the committed tree)

- `uv run ruff format --check` — 240 files already formatted
- `uv run ruff check` — All checks passed
- `uv run mypy` — no issues in 225 source files
- `uv run pytest` — **1,565 passed, 8 deselected in 378.21s** (1,562 before, plus 3)
- `uv run metaspike export-schemas --check` — Schemas are up to date
- `uv run metaspike evaluate-resolver` — exit 0; golden set v3, 200 cases, 0 false auto merges
- `uv run pytest tests/test_creator_supply.py` — 25 passed
- `uv run pytest tests/test_evidence.py` — 32 passed

One existing test changed. `test_video_details_are_asked_for_in_bounded_batches` read
two `videos.list` fetches from the store in order. Both carry the same retrieval time,
so the store orders them by fetch id, a hash of the request; removing `maxResults`
changed the hash and swapped them. The test now sorts them by size. Its expectations
(batches of 50 and 10, one missing id) are unchanged.

Not run: `live` tests (not permitted); the four `slow` catalogue proofs (no catalogue
code changed). Frontend not touched.

## 9. Quota documentation correction

From developers.google.com/youtube/v3/determine_quota_cost (last updated 2026-10-08),
read 2026-10-14: "Daily quotas reset at midnight Pacific Time (PT)"; "a default quota
allocation of 100 `search.list` calls, 100 `videos.insert` calls, and 10,000 units per
day combined for all other endpoints"; search.list "The quota cost is 1 per call", in
its own bucket. The model already matched; the U6.1 handoff's statement that the reset
time was not established was wrong and is corrected in the documentation.
`YouTubeQuota` is now documented, in the code and the document, as an in-memory account
for one run: offline only, not persistent, not following the Pacific reset, and
unsuitable as a production quota manager. No ledger or scheduler was built.

## 10. Source-policy and frozen-rule parity

`YOUTUBE_DATA_POLICY` and every other source policy unchanged. The creator-supply report
stays private; verified, unverified and synthetic standings, publication refusals,
sample completeness, duplicate handling, the association rules, identity, demand
evidence and score v0 unchanged. No change to `resolver_v2`, `baseline_rules_v1`,
`signal_semantics_v2`, `metaspike_score_v0`, the event, validation or outcome rules, the
golden labels, or the artifact contract (1.5.0).

## 11. Remaining known YouTube limitations

1. Nothing real: no API project, key, request or recorded response.
2. No threshold for "substantial" versus "limited" creator competition.
3. A sample is a lower bound; search is not exhaustive.
4. The association rule has only seen invented titles; category id `20` = Gaming is
   still unverified from the documentation read.
5. No deletion or refresh job for the 30-day limit.
6. The quota account is offline only; no persistent ledger follows the Pacific reset.
7. Two details for one video that disagree are left unresolved, never reconciled.
8. Policy text read in summary, without legal review; the aggregation and
   derived-metrics clauses may forbid per-game counts.

## 12. C2-F isolation

C2-F isolation: Git-only verification. No GCP access, no production action.

## Commits

`37586b4` fix: close YouTube request and time-boundary gaps; then this handoff. Pushed
to `origin/product/local-ui` only.

STOP. Awaiting independent ChatGPT review and explicit user authorisation.
