# Handoff: latest checkpoint (product branch)

## PRODUCT BUILD U6.3: OFFLINE CREATOR-OPPORTUNITY INTELLIGENCE — CHATGPT REVIEW REQUIRED

Branch `product/local-ui` only. Offline. No live source call, no credential, no cloud
access, no deployment, no pull request, no merge. **No later checkpoint has been
started**: no public publishing, production integration or launch work.

1. **Approved starting SHA:** `0e3e7c3cbadb14f79bde97dc58532e4db5e15762` (U6.2 handoff).
   Verified at the start: on `product/local-ui`, at that commit, tree clean.
2. **Implementation SHA:** `4c9866bad917fcd59a4996f084ea8dd0dd0ced05` — `feat: add offline creator opportunity intelligence`.

**In one line:** a transparent rubric now reads every source's evidence for a game and
says whether anything verified says "now", with an explained order. On today's real
data it justifies nothing, and says so.

## Things to review first

1. **No real recommendation is possible today.** Six recorded pageview series are the
   only verified demand evidence. None moves past the threshold (largest |robust z|
   1.86). Every real readiness is `insufficient_evidence`.
2. **One threshold was reused, and it is unvalidated.** "Moving" means robust z ≥ 3.5
   with a lift in the same direction: score v0's own outlier threshold, borrowed. The
   7-day window for timely discussion is a choice. Both are labelled UNVALIDATED.
3. **A "simulated readiness" sits beside every real one.** It runs the same rubric
   counting synthetic and unverified evidence as if real, so the rubric can be seen
   working. It is labelled throughout and never a recommendation. Reviewers should
   judge whether showing it at all is wise.
4. **A candidate needs a complete coverage sample, which can be a sample with no
   videos.** It is stated each time not to be low competition. Requiring at least one
   observed video instead is a defensible alternative.
5. **The Reddit adapter's API changed** (below): it no longer accepts an arbitrary client.

## 3. Exact files changed

| File | Change |
|---|---|
| `src/metaspike/opportunity/rules.py` | New. The rubric, the assessment contract, the shortlist and its ordering. |
| `src/metaspike/opportunity/render.py`, `__init__.py` | New. Text output. |
| `src/metaspike/community_sources/reddit.py` | `RedditReplay` replaces `replay_reddit_client`; `collection_refusal`; the adapter's policy guard. |
| `src/metaspike/community_sources/collect.py`, `synthetic.py` | Docstring; the scenario uses `RedditReplay`. |
| `src/metaspike/evidence/synthetic.py` | `write_synthetic_pageviews` and `PAGEVIEW_PATTERNS` for the demonstration. |
| `src/metaspike/cli.py` | `evidence --opportunities`; synthetic pageviews when the scenario has no recording. |
| `tests/test_opportunity.py` | New, 13 tests. |
| `tests/test_community_interest.py` | The replay helper uses `RedditReplay`; one new guard test. |
| `tests/test_creator_supply.py`, `tests/test_evidence.py` | Import lists now include the opportunity package that reads them. |
| `docs/creator-opportunity-intelligence.md` | New. |
| `docs/reddit-community-interest.md`, `README.md` | The guard; pointers. |

Nothing under `infra/`, `.github/`, `apps/`, `schemas/`, `tests/fixtures/` or `publishing/`.

## 4. Offline Reddit collector guard

**Root cause:** in U6.2 the evidence reader checked Reddit's pending approval; the
collector and adapter did not. Shown against the U6.2 adapter: built with an ordinary
`httpx.Client`, it sent the request (`requests sent: 1 status: ok`).

**Fix:** `RedditAdapter` takes either `RedditReplay` (answers from recorded files; holds
no HTTP client or transport, so it cannot reach a network) or a network-capable client
that it accepts only if `REDDIT_DATA_POLICY` clears collection at a stated stage. The
check runs when the adapter is built and again before each request. Approval is
pending, so a network-capable client is refused at every stage before any request; no
policy, or another source's policy, is refused too. Labels and file names play no part.
The policy was not changed. Synthetic replay still works and is deterministic.
**Regression:** `test_a_network_capable_client_cannot_send_reddit_requests_while_access_is_pending`
— zero requests reach a counting transport; a policy copy with approval granted (test
only) would build, which shows the check is the policy; the real policy is still pending.

## 5. Opportunity decision contract (`creator_opportunity_rules_v1`)

`OpportunityAssessment`: game, `as_of`, rules version; `readiness` and the `facts` behind
it (verified only); `simulated_readiness` and `simulated_facts`; per-source notes
(state, standing, latest observation, freshness); movements (direction, lift, z, standing);
events (type, date, precision, phase, standing; no titles); angles; `angle_status`;
creator-coverage and community statements; `why_now` (verified only); `unconfirmed`
(labelled); caveats; missing; uncertainty. Evidence and judgement are separate fields.
No score field exists; score v0 is not used.

## 6. Readiness and ranking

Readiness: candidate = up AND confirmed timely angle AND complete coverage sample AND no
disagreement; watchlist = up OR timely event; insufficient otherwise. Order:
readiness → families up → no disagreement → timely event → timely angle → coverage
known → sources answering → canonical id. Every entry says why it is above the next; ties
broken by id say "indistinguishable on the evidence; ordered by canonical id only, which
means nothing". No raw units mixed; no margins invented.

## 7. Why now and angles

Why now: verified movements up (with lift and z), upcoming or active verified events
(type, date, precision), recent verified discussion. Angles: community candidate angles
(U6.2) and timely events, each with references, timeliness, confirmation and its
unverified assumptions (that it would be watched; that existing videos do not cover it).
No title is written and nothing is copied from posts or announcements; with none, "angle
not yet established".

## 8. Source and provenance handling

Only verified evidence counts. Synthetic and unverified evidence appears under "not
counted", tagged. Unverified events never explain now (tested with the scenario's stores
read as local stores). Missing or empty creator supply is never called low competition;
a missing community input is stated unknown.

## 9. Recorded versus synthetic

Recorded-only run (`--as-of 2026-10-05T12:00:00+00:00`, the pageview recording): all 15
games `insufficient_evidence`; six verified `flat` movements; "No real recommendation
can be justified". Synthetic run: real order all insufficient; simulated order Dota 2
candidate, Rust watchlist, the rest insufficient.

## 10. Historical correctness

Assessments read only the evidence reading at `as_of`. Tested: two days earlier the same
stores give no event, no player climb, no searches, and a lower simulated readiness;
posts and videos retrieved after the moment never appear; a later rebinding leaves an
earlier shortlist unchanged; repeated runs are identical; input order does not matter.

## 11. Demonstration output (abridged, actual)

```
  No real recommendation can be justified: no game has verified evidence that says 'now'.
  SIMULATED order (synthetic counted as if real; NOT a recommendation):
     1. dota-2      EVIDENCE-SUPPORTED CANDIDATE   ahead of next: readier on the rubric
     2. rust        PROVISIONAL / WATCHLIST        ahead of next: readier on the rubric
  2. Rust
    - [SYNTHETIC] wikimedia_readership is up against its own history (log lift 1.39, robust z 27.7)
    - [SYNTHETIC] 3 posts discuss version 2.3 (update or patch breakdown)
  Caveats: steam_players is DOWN ...; demand families disagree ...
```

Commands are in `docs/creator-opportunity-intelligence.md`.

## 12. Test and quality-gate results (2026-10-14, on the committed tree)

- `uv run ruff format --check` — 255 files already formatted
- `uv run ruff check` — All checks passed
- `uv run mypy` — no issues in 238 source files
- `uv run pytest` — **1,592 passed, 8 deselected in 420.00s** (1,578 before, plus 14)
- `uv run metaspike export-schemas --check` — Schemas are up to date
- `uv run metaspike evaluate-resolver` — exit 0; golden set v3, 200 cases, 0 false auto merges
- `tests/test_opportunity.py` 13 passed; `tests/test_community_interest.py` 14;
  `tests/test_creator_supply.py` 25; `tests/test_evidence.py` 32;
  `tests/test_historical_validation.py` 70

First full run: three earlier tests that list which modules import the evidence,
YouTube and Reddit packages failed because the new package imports them; the lists were
updated and everything re-run. Two assertions of mine were too broad and were narrowed.
Not run: `live` tests; the four `slow` catalogue proofs (no catalogue code changed).
Frontend not touched.

## 13. Frozen-rule parity

No change to `resolver_v2`, `baseline_rules_v1`, `signal_semantics_v2`,
`metaspike_score_v0`, `event_heat_rules_v1`, `event_classification_rules_v1`,
`validation_protocol_v1`, `outcome_rules_v1`, golden labels, U5.2/U5.3, the U6.0–U6.2
evidence contracts, any source policy, or the artifact contract (1.5.0).

## 14. Limitations and remaining blockers

1. No verified source beyond pageviews; no real game can pass `insufficient` except by a
   verified pageview movement, and none moves today.
2. No threshold is validated; calibration against outcomes is the next real requirement.
3. The rubric is binary per question and does not grade strength.
4. YouTube and Reddit terms and approvals block any real creator or community evidence.
5. Case A (verified single-source attention) is shown only in a test, on a copy of real
   evidence with one movement replaced in memory, because no real series moves enough.

## 15. Git-only C2-F isolation

C2-F isolation: Git-only verification. No GCP access, no production action.

## 16. No later checkpoint

Nothing beyond U6.3 has been started.

STOP.
