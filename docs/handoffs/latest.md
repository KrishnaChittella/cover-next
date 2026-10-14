# Handoff: latest checkpoint (product branch)

## PRODUCT BUILD U6.1: OFFLINE YOUTUBE CREATOR-SUPPLY INTEGRATION — CHATGPT REVIEW REQUIRED

Branch `product/local-ui` only. Offline. No live source call, no credential, no cloud
resource, no deployment, no pull request, no merge to `main`. This file is the
product-branch handoff; `main`'s handoff was not touched. **No later checkpoint has
been started**: no Reddit integration, no opportunity scoring.

- **Approved starting SHA:** `caf78ca27ebfea7d6241c1b270004f5e219eca69` (U6.0.1 handoff).
  Verified at the start: on `product/local-ui`, at that commit, tree clean.
- **Implementation SHA:** `6faa56c180f3b617e7f10b512ee77e3fd60d4885` —
  `feat: add offline YouTube creator-supply evidence`.

**In one line:** YouTube-shaped search and video responses can now be parsed, collected
within a quota, stored, associated with a game, and read as of a moment as a described
*sample* inside the private game evidence report — entirely offline, on synthetic data.
MetaSpike has never called YouTube.

## Things to review first

1. **"Substantial or limited" is deliberately not answered.** The brief asks whether
   sampled competition is substantial, limited or unknown, and also forbids arbitrary
   weights and invented scores. The two conflict without a calibrated threshold, and
   none exists. The reading gives a lower bound ("at least N videos from at least M
   channels") and says the total is unknown. I chose not to invent a cut-off.
2. **A domain file outside the evidence packages was changed:**
   `src/metaspike/domain/source_policy.py` gained `SourcePurpose.CREATOR_SUPPLY` and
   its branch in `registration_refusal` (23 lines, the same pattern U4 used for event
   intelligence). No existing purpose, policy or refusal changed.
3. **The private evidence report is now `game_evidence_v2`** (creator supply, a ninth
   state `incomplete`, and the U6.0.1 provenance fields). Not a public contract.
4. **Two API figures were not verified from the documentation read:** that category id
   `20` is Gaming (from general knowledge), and a batch of 50 ids per `videos.list`
   call (self-imposed). Both must be confirmed before any real call.
5. **Policy text was read in summary, not in full,** and with no legal review. The
   clauses cited (III.E.2.a aggregation, III.E.4.h derived metrics, III.E.4 30-day
   storage) need a full reading before collection. They may forbid per-game counts.
6. **The association rule has only ever seen invented titles.**

## Exact files changed

| File | Change |
|---|---|
| `src/metaspike/creator_sources/youtube.py` | New. Policy, documented shapes, parsing, quota account, adapter, replay client. |
| `src/metaspike/creator_sources/collect.py` | New. Bounded collector. |
| `src/metaspike/creator_sources/store.py` | New. Append-only store, read as of a moment. |
| `src/metaspike/creator_sources/association.py` | New. `creator_association_rules_v1`. |
| `src/metaspike/creator_sources/reading.py` | New. `read_creator_supply`. |
| `src/metaspike/creator_sources/synthetic.py` | New. The synthetic scenario. |
| `src/metaspike/creator_sources/__init__.py` | New package. |
| `src/metaspike/domain/source_policy.py` | `SourcePurpose.CREATOR_SUPPLY` and its refusal rules. |
| `src/metaspike/evidence/model.py` | `creator_supply` on a game; `EvidenceState.INCOMPLETE`; report v2. |
| `src/metaspike/evidence/assemble.py` | The creator-supply row and reading, gated and optional. |
| `src/metaspike/evidence/provenance.py` | Detection of the synthetic etag. |
| `src/metaspike/evidence/render.py` | The creator-supply block. |
| `src/metaspike/cli.py` | `--creator-supply-dir`; the scenario includes creator supply. |
| `tests/test_creator_supply.py` | New. 22 tests. |
| `tests/test_evidence.py` | One line: the report version string. |
| `docs/youtube-creator-supply.md` | New. |
| `docs/multi-source-evidence.md`, `README.md` | Pointers and the ninth state. |

No change under `infra/`, `.github/`, `apps/`, `schemas/`, `tests/fixtures/`, or to
any resolver, feature, scoring, event, validation or publishing module. No YouTube file
was added under `tests/fixtures/`: synthetic responses are written at run time.

## Source API model and quota

Verified against the official documentation on 2026-10-14:

- `search.list`: 1 call in its own "Search Queries" bucket, default **100 calls a day**.
  Each further page is another call. `maxResults` 0–50, default 5.
- `videos.list`: **1 unit** of the general bucket, default 10,000 units a day.
- Every request costs at least one, even an invalid one.
- `pageInfo.totalResults` is documented as an approximation capped at 1,000,000.

The older 100-units-per-search figure is not used. `YouTubeQuota` holds both buckets:
a request is paid for before it is sent; an unaffordable one is not sent and is
recorded as not sent; a failed one is still paid for; a `quotaExceeded` answer closes
only its own bucket; limits can be lowered, never raised; nothing is retried. The daily
reset time is not stated on the pages read and is not modelled.

Modelled and tested: query identity, page size, pagination, page limits, detail
batches, failed and malformed responses, partial responses, quota exhaustion.

## Topic association (`creator_association_rules_v1`)

- **Supported** needs all of: details retrieved; the catalogue name or an alias as
  whole words in the title, or as an exact tag; YouTube's own gaming classification
  (category or a video-game topic); no other catalogue game and no related product
  named.
- **Excluded** on contradicting evidence: the match is part of a longer catalogue name,
  or YouTube classifies the video as not gaming.
- **Uncertain** otherwise.

Deterministic; no probability or weight; names read from the catalogue state; the
frozen resolver is not involved and no game is merged by title. Uncertain and excluded
videos stay visible, labelled, and are never counted. Checked against the recorded
234-entity catalogue: a title naming a longer-named game never supports the shorter one.

## Creator-supply reading

States: `not_queried`, `expired`, `incomplete`, `no_results`, `ambiguous`,
`observed`, mapped to the game evidence as `missing`, `stale`, `incomplete`,
`available`, `unresolved`, `available`; with no input, `not_integrated` exactly as
before. Reported: queries and their completeness; distinct videos; supported, uncertain,
excluded; duplicates across queries; distinct channels and the most from one; recency
of supported videos; how many carry a view count.

`total_competition_known` is always false. `totalResults` appears nowhere in a
reading. A missing counter is `None`, never 0; counters are not summed. An empty or
partial sample is stated not to be evidence of low competition.

## Point-in-time correctness

Published, retrieved, observed and evaluated moments are separate fields. A reading
uses only what was retrieved by `as_of`. Tested:

- A video published thirty hours before `as_of` but first returned by a search made
  later is **not** in the earlier reading: a current search cannot reconstruct a
  historical one.
- A search made after `as_of`, holding a video published after `as_of`, is never read.
- Details retrieved after `as_of` are not used; a later, larger view count is never
  shown for an earlier moment.
- Anything retrieved more than 30 days before `as_of` is not read (`expired`).
- A response dated after its own retrieval is rejected.

## Synthetic and unverified provenance

U6.0.1 is preserved. No YouTube recording is pinned, so no creator-supply input can be
verified. The scenario is declared synthetic; its responses carry a `SYNTHETIC` etag and
are recognised as synthetic even when the store is handed over as a local store; any
other local store is unverified. Neither is ever `usable`. No option declares an input
real, and a claim of verified without a pinned digest is refused.

## Policy restrictions

`YOUTUBE_DATA_POLICY`: cleared for private development only (no API data is held);
`derived_metrics_allowed` unknown; 30-day retention; branding and link-back required.
The collector and the evidence reading both refuse every other stage; nothing under
`publishing/` or `schemas/` refers to the source; artifact contract unchanged at 1.5.0.
The existing Wikimedia, IGDB, Steam catalogue, Steam players and Steam News policies
are untouched. Public launch is not authorised. Requirements for real collection and
for public launch are listed in `docs/youtube-creator-supply.md`.

## Offline demonstration

```
uv run metaspike evidence --as-of 2026-10-20T12:00:00+00:00 --synthetic-scenario \
    --game rust --game dota-2 --game counter-strike-2 --game elden-ring --game minecraft
```

Run. Shows cases A–L: relevant videos; ambiguous ones; a search with no matches; partial
pagination; quota exhaustion (from the API, and not-sent locally); a duplicate across
queries; several videos from one channel; two retrieval times; a later search and video
that are never read; a missing view count; excluded associations; and "Total
competition: UNKNOWN" on every game. Every line is marked `[SYNTHETIC]`. Actual output
for Rust:

```
Creator supply: a SAMPLE, not the market (incomplete)  [SYNTHETIC]. Total competition: UNKNOWN.
  Incomplete sample. The sample held 9 video(s): 4 supported as being about this game
  (from 3 channel(s)), 3 uncertain, 2 excluded. ...
  query sample ytq_de646319: page_limited, 2 page(s), 8 result(s), retrieved 2026-10-20 10:00Z
  query sample ytq_ac9d576c: complete, 1 page(s), 2 result(s), retrieved 2026-10-20 11:00Z
  sampled videos 9 (returned twice: 1); supported 4 from 3 channel(s), most from one channel 2;
  published in the last 24 h 2, last 7 d 3; view count held for 3, absent for 1
```

## Test results (2026-10-14, on the committed tree)

- `uv run ruff format --check` — 240 files already formatted
- `uv run ruff check` — All checks passed
- `uv run mypy` — no issues in 225 source files
- `uv run pytest` — **1,562 passed, 8 deselected in 377.33s** (1,540 before, plus 22)
- `uv run metaspike export-schemas --check` — Schemas are up to date
- `uv run metaspike evaluate-resolver` — exit 0; golden set v3, 200 cases, 0 false auto merges
- `tests/test_creator_supply.py` — 22 passed; with `tests/test_evidence.py`, 54 passed

All 22 new tests passed on their first run; none was adjusted to pass. One existing
expectation changed: the report version string. Score v0 parity and the U6.0 and U6.0.1
regressions pass unchanged. A documentation-only note was added after the full run; the
three test files that read the docs were re-run afterwards: 146 passed.

Not run: `live` tests (not permitted); the four `slow` catalogue proofs (no catalogue or
resolver code changed; last run in U5.3). Frontend not touched, not re-run; no artifact
contract changed.

## Frozen rules

No change to `resolver_v2`, `baseline_rules_v1`, `signal_semantics_v2`,
`metaspike_score_v0`, `event_heat_rules_v1`, `event_classification_rules_v1`,
`validation_protocol_v1`, `outcome_rules_v1`, the golden labels, or the U5.2 and U5.3
catalogue code. Demand-signal semantics are untouched: creator supply is not a demand
family and moves no score.

## Remaining known limitations

1. Nothing real: no API project, key, request or recorded response. Everything is
   exercised on invented data, including the association rule.
2. No threshold for "substantial" versus "limited" competition (item 1 at the top).
3. A sample is a lower bound. Search is not exhaustive, so even a whole sample says
   nothing about the total.
4. The association rule: a video that does not name the game is uncertain; a gaming
   video naming it in passing is supported; a common-word name leans on YouTube's
   classification alone.
5. No deletion or refresh job for the 30-day limit; the store only reports what expired.
6. No persistent quota ledger across runs; the reset time is not established.
7. Query design is the game's name and nothing more.
8. No command fills a creator-supply store from a real source, by design.
9. Unchanged: no community-interest source; no opportunity score; no game has two
   verified demand families; genuine local data cannot be verified yet.

## C2-F isolation

C2-F isolation: Git-only verification. No GCP access, no production action.

From Git: changes only on `product/local-ui`; `origin/main` and local `main` are both
at `cb33198e2510965e41dff0f5fe7e75cacb7ee2ca`, as before; no file under `infra/` or
`.github/workflows/` changed; `BROAD_DISCOVERY_ENABLED` is still `False`; no pull
request was opened. No live production health was verified, because none was checked.

## Commits

`6faa56c` feat: add offline YouTube creator-supply evidence; then this handoff. Pushed
to `origin/product/local-ui` only.

## Exact next approval required

Independent ChatGPT review of U6.1, then explicit user authorisation. Nothing further
has been started.

STOP.
