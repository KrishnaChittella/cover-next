# Source Readiness and Real-Data Activation

U6.6. Offline and private. Code:

- `src/metaspike/source_readiness.py`
- `src/metaspike/evidence/acquisition.py`
- the U6.5 provenance fix in `src/metaspike/opportunity/history.py`

Command: `metaspike source-readiness`. Tests: `tests/test_source_readiness.py` and
`tests/test_opportunity_history.py`.

This answers, for each source and from code, what is built, what evidence exists, what
may be used privately, what may be published, and what stops a verified observation.
Nothing here grants a permission. Unknown stays UNKNOWN, pending stays PENDING, and a
working parser is not a licence.

## The command

```
uv run metaspike source-readiness [--root DIR] [--json] [--out LOCAL_FILE]
```

For every source it reports the following, from these places:

| Reported | Where it comes from |
|---|---|
| Stage permissions | Each stage's result is the policy's own `registration_refusal` for the source's purpose. The declared clearance is shown beside it where the two differ: Reddit is *declared* cleared for offline parsing yet unusable, because approval is pending. |
| Permissions | Approval, raw and historical storage, retention, derived metrics, redistribution, attribution, commercial use and approval, link-back and branding, exactly as declared. |
| Code | Whether the adapter, offline replay and local store exist, checked by importing them. |
| Credentials | Environment variables a live run would need, by name only. No value is read, and whether one is set is not checked. |
| Evidence | Committed fixtures and default local stores, each with a standing (see below). |
| Next steps | Blockers, owner actions, answers needed from third parties, the next technical step and the integration path. |

An evidence item is verified only if its content matches a pinned digest. It is synthetic
if invented, unverified if it is a local store or an unpinned recording, and absent if
there is nothing there.

Output is deterministic, with no timestamp. `--out` is refused in `artifacts/` and in any
`public` directory. The owner actions and next steps are written in `source_readiness.py`,
taken from each policy's clearance reasons. A statement the policy itself marks as
"reported" is NOT VERIFIED.

## Source status (actual output, 2026-10-14)

| Family | Purpose | Furthest permitted stage | Verified evidence today | Publishable |
|---|---|---|---|---|
| wikimedia_pageviews | demand | commercial_production | **yes**: pinned recording, 6 articles, 6 July to 3 October | yes (CC0) |
| igdb_catalogue | catalogue metadata | private_collection | yes: pinned recordings | no: derived metrics unknown |
| steam_app_catalogue | catalogue metadata | private_development | yes: pinned recording | no |
| steam_concurrent_players | demand | private_development | **no**: a genuine recorded response (app 730), not pinned, so unverified | no |
| steam_news | events | private_development | **no**: fixtures synthetic; local probe store unverified | no |
| youtube_creator_supply | creator supply | private_development | **no**: none exists | no |
| reddit_community_interest | community interest | **none**: approval required and PENDING | **no**: none exists | no |

Catalogue metadata establishes identity. It is never demand, and the readiness report
never picks it as a demand source.

**First source for genuine demand evidence: Wikimedia pageviews.** It is the only demand
source permitted at private collection or beyond, and the only one with verified evidence.

## Provenance requirements

**The U6.5 gap is closed.** `evaluate_history` now fails closed on any verified claim it
cannot back:

1. It applies `require_honest` to the demand origin and to every context origin. A
   verified origin with no digests, or with a digest pinned for nothing, is refused.
2. A verified context observation is refused outright. No recording of any context
   family is pinned, and nothing could tie an in-memory observation to one.
3. Verified demand must name exactly one directory whose content still has the claimed
   pinned digest.

   **A digest is not enough.** It proves which directory, not that the in-memory data
   handed to the evaluator came from it. So the evaluator replays that directory and
   requires the supplied pageview values to equal it. If they differ, the input is
   refused. The same data, labelled honestly as unverified, is read but counts nothing.

The recorded CLI result is unchanged: `ophist_b1d2af287f618f8ab1dbc9634b5b1292`, with
546 assessments (0 candidate, 35 watchlist, 511 insufficient).

**Acquisition records (`acquisition_record_v1`)** are the contract a future real collection
writes beside each raw response. Each record holds:

- the source and the purpose;
- the stage;
- the policy's review date, and a digest of its full content, so a later change is
  visible;
- `requested_at` and `received_at`; a record is unusable before `received_at`;
- the request, which must carry no credential;
- the SHA-256 and length of the exact bytes;
- the native ids, and their bindings as they stood at acquisition.

The record refuses to exist in any of these cases:

- the policy refuses the purpose at the stage;
- the answer predates the request;
- a binding begins after the acquisition;
- a binding names an id the response did not hold.

`check_content` detects any change to the bytes. **Integrity is not origin.** Anyone can
hash any bytes, and no signature or attestation is added. `acquisition_standing` therefore
returns UNVERIFIED for intact content and never VERIFIED. Origin is still established only
by the existing route: a reviewed recording whose digest is pinned in `provenance.py`.

The trusted live acquisition step is **NOT EXECUTED**. That step is a supervised
collection whose output is reviewed and pinned.

## Current blockers and owner actions

These come from the policies. The command lists them in full.

**Wikimedia**

- Nothing in policy blocks it.
- Fresh data waits on the C2-F acceptance review and the owner's authorisation. This
  checkpoint reads no production data.

**Steam concurrent players**

Private collection is not cleared until all of the following hold:

- a privacy and data notice with a Steam Data section is published;
- storage is in the US (us-central1) only;
- the key registration identifies the application;
- calls stay far below 100,000 a day.

Public use needs Valve's position on derived metrics (UNKNOWN).

**Steam News**

- It needs the same Steam conditions.
- It needs a decision on keeping publisher-authored titles.
- No backfill budget is approved.
- Public use needs Valve's position on derived events.

**IGDB**

- Private collection is permitted for identity metadata.
- Public use needs static IGDB attribution, and IGDB's answer on the partnership.

**Steam app catalogue**

- It needs a storage region and a privacy policy naming it.

**YouTube**

It needs:

- an API project and acceptance of the API Services Terms (owner);
- deletion or refresh within 30 days (owner);
- YouTube's answer on the aggregation and derived-metrics clauses (third party).

**Reddit**

- Approval is PENDING under Reddit's Responsible Builder Policy. That route is reported,
  and NOT VERIFIED from Reddit's own pages.
- It also needs a deletion process and short retention.
- The adapter refuses a network client while approval is pending.

## Offline rehearsal

The whole path runs offline in `tests/test_source_readiness.py`:

input → provenance → source policy → game evidence → opportunity assessment → private
report → UI schema.

The real recorded Steam response for app 730 is replayed through `metaspike sample` at a
moment *after* it was recorded, so nothing is backdated. It is then read with the pinned
pageviews by `metaspike evidence --opportunities`. Shown:

- **Pinned pageviews stay verified.** Missing sources stay `none` and are never filled.
- **The genuine Steam sample stays unverified** in a local store, and is never a why-now.
- **The private report passes the UI's own schema**, and every entry's readiness is its
  assessment's.
- **Nothing is written** under `artifacts/` or any `public` directory.
- **The synthetic scenario does not become real.** Invented YouTube, Reddit, Steam and
  event evidence leaves every real entry at `insufficient_evidence`, while simulated
  entries differ. Invented discussions are synthetic and never confirmed.
- **Recording a response changes nothing.** A YouTube-shaped response can be recorded
  privately and stays unverified.
- **Every family has a defined way into `EvidenceInputs`.**

To run it by hand:

```
uv run metaspike source-readiness
uv run metaspike evidence --as-of 2026-10-05T12:00:00+00:00 \
  --wikimedia-replay tests/fixtures/wikimedia --recorded-through 2026-10-03 --opportunities
uv run metaspike opportunity-history --recorded --replay tests/fixtures/wikimedia \
  --recorded-through 2026-10-03 --from 2026-07-05 --to 2026-10-07
```

## First real-data plan, after C2-F acceptance

**DO NOT EXECUTE** until the C2-F acceptance review has passed and the owner authorises
this step.

1. **Prerequisites**
   - The C2-F acceptance review has passed.
   - The owner has authorised this step explicitly.
   - U6.6 is approved.
   - The owner has supplied a contact string for the Wikimedia User-Agent
     (`METASPIKE_CONTACT`, defaulting to krishnachittella.dev).
2. **Source**
   - `wikimedia_pageviews`, for daily per-article pageviews.
   - It is read directly from the public Analytics API, on the owner's machine.
   - It is not read from production storage, and the soak is not touched.
3. **Components reused**
   - `WikimediaPageviewsAdapter` and `build_client`, with its compliant User-Agent.
   - `collect`, `default_registry` and `window_ending`.
   - `replay_client`, which reads the recording format, and `recording_digest` with
     `PINNED_RECORDINGS`.
   - `record_acquisition`.
   - `assess_evidence`, `shortlist` and `evaluate_history`.
   - The private UI loader.
4. **Missing credentials or approvals**
   - No credential is needed. Wikimedia is keyless and its data is CC0.
   - The owner's authorisation is the only approval.
5. **Stage and purpose**
   - Stage: `private_development` for this read, which the policy permits.
   - Purpose: `demand_signals`.
6. **Scope**
   - The six articles already recorded and the bootstrap games: 15 entities at most.
   - One window of 90 days ending on the latest due day, in one supervised run.
7. **Rate and quota protections**
   - The policy's documented 200 requests a minute, at concurrency 1, enforced by the
     existing limiter.
   - About 15 requests in total.
   - Retries come from the existing adapter configuration.
8. **Origin verification**
   - The response bodies are written in the replay format, `<article>.json`, with one
     acquisition record per response.
   - The owner reviews the directory, and its `recording_digest` is pinned in
     `provenance.py` in a reviewed commit.
   - Until then it is UNVERIFIED.
9. **Point-in-time retention**
   - The recording is dated by its acquisition records' `received_at`.
   - The publication lag stays 1 day, so a moment before `received_at` cannot use it.
   - The earlier pinned recording is kept unchanged.
10. **Entry into evidence**
    - `metaspike evidence --wikimedia-replay <new dir> --recorded-through <day>`.
    - Then `--opportunities`, and `opportunity-history --recorded` over the new range.
    - No new path is needed.
11. **Correctness checks**
    - The digest matches the review.
    - Each record's `check_content` passes, and `policy_changed` is false.
    - Values on the overlap with the old recording agree. A disagreement is a revision,
      and is reported, not overwritten.
    - Readiness and history reports are regenerated.
    - The full gates pass.
12. **What stays private**
    - Everything in this step: the recording, the reports and the opportunity UI.
13. **What may be public**
    - By policy, Wikimedia-derived output may be published (CC0).
    - Any publication is separate, authorised work through the existing artifact
      pipeline. None is part of this step.
14. **Stop and rollback conditions**
    - Any HTTP 429 or 403.
    - Any response failing to parse.
    - Any digest or overlap mismatch.
    - Any request outside the stated scope.
    - Any production resource touched.
    - Rollback: delete the new local directory. Nothing else changed.
15. **Cost**
    - No cloud cost: the step runs locally with no GCP use.
    - The API is free. Any other cost is NOT VERIFIED.

**Owner actions**

- Authorise the run after C2-F acceptance.
- Run the recorder.
- Review the directory and approve pinning its digest.

**Later work for Claude, once authorised**

- A small supervised `record-pageviews` command. It writes response bodies and acquisition
  records through the existing adapter, and refuses any scope beyond the declared one. It
  does not exist yet, and nothing here contacts a source.
- The pinning commit, and regenerated reports.

**The next sources, in order of what the owner alone can unblock:**

1. **Steam concurrent players** (owner actions: privacy notice, storage region, key
   registration). Its public use still waits on Valve.
2. **Steam News** (the same, plus a titles decision).
3. **YouTube** (owner project plus YouTube's answer).
4. **Reddit** (Reddit's approval).

## What remains for public release

| Source | Needed before public use |
|---|---|
| Wikimedia | Only the existing publication pipeline. It is CC0. |
| Steam players and Steam News | Valve's answer on derived metrics and derived events. Then Steam attribution, branding and the privacy notice. |
| IGDB | Attribution, and IGDB's answer on the partnership. |
| YouTube | Brand features, the Terms of Service link, a privacy policy referencing Google's, and a basis for derived figures. |
| Reddit | Approval, and a basis for derived output. |

Calibration of the opportunity rubric still needs point-in-time multi-source history. See
[creator-opportunity-historical-evaluation.md](creator-opportunity-historical-evaluation.md).

The public artifact contract stays 1.5.0.

## Test results

See the U6.6 handoff (`docs/handoffs/latest.md`) for the exact gate results.
