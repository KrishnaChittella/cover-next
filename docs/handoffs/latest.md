# Handoff: latest checkpoint (product branch)

## PRODUCT BUILD U6.6: REAL-SOURCE READINESS AND VERIFIED EVIDENCE HANDOFF — CHATGPT REVIEW REQUIRED

This checkpoint was worked on branch `product/local-ui` only, entirely offline:

- no live source call, credential read, cloud access or deployment;
- no source activation, pull request, merge or publication.

**No later checkpoint has been started.**

## Things to review first

1. **Verified demand is now tied to its data, not only to its digest.** Matching a
   claimed digest proves which directory is meant. It does not prove that the in-memory
   recording came from it. `evaluate_history` therefore replays the named directory and
   refuses pageviews that differ. This is a content comparison, not cryptographic
   authentication. The pinned digest stands for a reviewed recording, and the docs say
   so.
2. **A verified *context* claim is refused outright.** No recording of events, creator
   supply or community is pinned, and nothing could tie an observation to one. This
   fails closed rather than trusting a caller.
3. **Acquisition records can never make anything verified.** They check integrity (the
   bytes are unchanged) and policy at collection time. They do not check origin. The
   trusted live acquisition step is marked NOT EXECUTED in code and output.
4. **The genuine Steam recording stays unverified.**
   `tests/fixtures/steam_players/current_players_730.json` is a real recorded response,
   but it was never reviewed into `PINNED_RECORDINGS`. The readiness report says exactly
   that. I did not pin it: pinning grants verified standing, and that is the owner's
   decision.
5. **Reddit is "declared cleared" for private development, yet unusable there.** The
   policy's own `registration_refusal` refuses every stage while approval is pending.
   Both facts are shown side by side rather than one replacing the other.
6. **Three import allow-list tests** (`test_evidence`, `test_creator_supply`,
   `test_community_interest`) now also name `source_readiness.py`. Each allow-list gained
   one entry, and none was relaxed.

## 1. Starting SHA

`9a4abcdfde45c402c3a6f805a3349d2d409098d2`. Verified at the start: on
`product/local-ui`, at that commit, tree clean.

## 2. Implementation SHA

`7bec09113e30e310a7fe74f857ae2d6f009b17ca`: `feat: prepare verified real-source evidence
readiness`.

## 3. Exact files changed

| File | Change |
|---|---|
| `src/metaspike/opportunity/history.py` | `_check_origin`: honesty guard, no verified context, digest and replay correspondence for verified demand |
| `src/metaspike/source_readiness.py` | New: per-source readiness, read from policies, code and files |
| `src/metaspike/evidence/acquisition.py` | New: `acquisition_record_v1` and its integrity, policy and time checks |
| `src/metaspike/cli.py` | `source-readiness` command |
| `tests/test_source_readiness.py` | New: 17 tests (readiness, acquisition, rehearsal) |
| `tests/test_opportunity_history.py` | 5 provenance regressions (A to G) |
| `tests/test_evidence.py`, `test_creator_supply.py`, `test_community_interest.py` | Allow-lists name `source_readiness.py` |
| `docs/source-readiness-and-real-data-activation.md` | New |
| `docs/creator-opportunity-historical-evaluation.md` | Section on what verified origin checks |
| `docs/multi-source-evidence.md`, `docs/creator-opportunity-intelligence.md` | Links |
| `docs/handoffs/latest.md` | This handoff (separate commit) |

No frontend file changed.

## 4. Provenance gap correction

`evaluate_history` now runs `_check_origin` after the policy check:

1. `require_honest` on the demand origin and on every context origin. A verified origin
   without digests, or with an unpinned digest, is refused.
2. Any context observation with verified standing is refused.
3. Verified demand must name exactly one directory whose `recording_digest` still equals
   the claimed pinned digest. The supplied recording's demand values must then equal what
   replaying that directory gives.

Unchanged behaviour:

- Synthetic stays synthetic.
- Local stores and unverified files are read honestly and count nothing.
- There is no override option.

Regressions:

| | Case | Result |
|---|---|---|
| A | No digests | Refused |
| B | Unknown digest | Refused |
| B | Pinned catalogue digest on the Wikimedia directory | Refused |
| B | Pinned Wikimedia digest on another directory | Refused |
| B | Two digests | Refused |
| B | Pinned and correct, but altered in-memory pageviews | Refused |
| B | The same altered data labelled unverified | Read; counts nothing |
| C | Forged verified context, with a pinned digest | Refused |
| C | Forged verified context, digest-less | Refused |
| D and G | Recorded CLI | Fingerprint still `ophist_b1d2af287f618f8ab1dbc9634b5b1292`; 546 / 0 / 35 / 511 |
| E | Synthetic | Stays synthetic; nothing verified |
| F | Byte-altered copy of the pinned recording | `unverified_files`; every snapshot insufficient; nothing counted |

## 5. New readiness functionality

```
uv run metaspike source-readiness [--root DIR] [--json] [--out FILE]
```

For each of the seven source families it reports:

- each stage's permission, which is the policy's own `registration_refusal` for the
  source's purpose, with the declared clearance beside it where they differ;
- the furthest stage reached with every earlier stage permitted;
- publication status;
- every permission field, including approval, retention and commercial terms;
- whether the adapter, offline replay and store exist, checked by import;
- the credential variables a live run needs, by name only and never read;
- each committed fixture and default local store, with its standing (verified by pinned
  digest, synthetic, unverified or absent);
- blockers, owner actions, the answers needed from third parties, the next step and the
  integration path.

The output is deterministic and private; `--out` is refused in `artifacts/` or `public/`.
Tests show that output is identical whether or not credential variables are set, and that
their values never appear.

**`acquisition_record_v1`** records, per raw response:

- source, purpose and stage;
- the policy's review date and a digest of its full content;
- `requested_at` and `received_at`;
- a request free of credentials;
- the SHA-256 and length of the content;
- native ids and bindings.

It is refused in each of these cases:

- the policy refuses the purpose at the stage;
- the request carries a credential;
- the answer predates the request;
- a binding begins after acquisition;
- a binding names an id the response did not hold.

`check_content` detects altered bytes, and `policy_changed` detects a policy revised since.
`usable_at` is false before `received_at`. `acquisition_standing` returns UNVERIFIED at
best, and no field can declare a record verified.

## 6. Actual per-source status (command output)

| Family | Furthest permitted stage | Verified evidence | Publishable |
|---|---|---|---|
| wikimedia_pageviews (demand) | commercial_production | yes: pinned, 6 articles, 6 July to 3 October | yes |
| igdb_catalogue (metadata) | private_collection | yes: pinned | no (derived metrics unknown) |
| steam_app_catalogue (metadata) | private_development | yes: pinned | no |
| steam_concurrent_players (demand) | private_development | no: a real recording that is not pinned | no |
| steam_news (events) | private_development | no: fixtures synthetic; local probe store unverified (9 files) | no |
| youtube_creator_supply | private_development | no | no |
| reddit_community_interest | none (approval PENDING) | no | no |

The first source for genuine demand evidence is `wikimedia_pageviews`.

## 7. Offline rehearsal results

All in `tests/test_source_readiness.py`, passing:

- **The real recorded Steam response for app 730** was replayed by `metaspike sample` at
  6 October 01:30, after it was recorded at 01:25:26, so it is not backdated. It was then
  read with the pinned pageviews by `metaspike evidence --opportunities --json --out`.
- **Standings stayed honest.**
  - Pageviews were `verified`.
  - The counter-strike-2 player sample was `unverified` and absent from why-now.
  - YouTube and Reddit were `none`, never filled.
- **Readiness and output stayed private.**
  - Every entry's readiness equals its assessment's.
  - The report passes the UI's own `report.schema.json`.
  - Nothing was written under `artifacts/` or `public/`.
- **The synthetic scenario stayed simulated.**
  - Every real entry was `insufficient_evidence`; the simulated entries differ.
  - Every community angle was `synthetic` and unconfirmed.
- **Recorded responses stay unverified.** A YouTube-shaped response recorded privately is
  unverified.
- **Policy refusal holds.** A Reddit acquisition is refused because approval is pending.
  A Steam acquisition at private_collection is refused.
- **Every family has a defined `EvidenceInputs` way in.**

## 8. Post-soak first real-data plan

The full plan, with all 15 points, is in
`docs/source-readiness-and-real-data-activation.md`. In summary:

- **Source:** Wikimedia pageviews, keyless and CC0.
- **Stage and scope:** read at `private_development` on the owner's machine, never from
  production. At most the 15 bootstrap entities and one 90-day window. About 15 requests,
  under the policy's 200 a minute, at concurrency 1.
- **Reused components:** the existing adapter and limiter.
- **Recording:** response bodies are written in the replay format with one acquisition
  record each. The owner reviews them, and the digest is pinned in a reviewed commit.
  Until then it is unverified.
- **Entry into evidence:** through `--wikimedia-replay` with no new path.
- **Checks:** content and digest, policy unchanged, agreement on the overlap with the old
  recording.
- **Stop conditions:** a 429 or 403, a parse failure, a mismatch, or any scope breach.
- **Rollback:** delete the new directory.
- **Cost:** none in the cloud; anything else NOT VERIFIED.
- **Not yet built:** a small supervised `record-pageviews` command. That is later work for
  Claude, once authorised.

## 9. Owner-action blockers

**Wikimedia**

- Authorise the read after C2-F acceptance.
- Review and approve pinning the new recording.

**Steam players and Steam News**

- A privacy and data notice with a Steam Data section.
- US-only storage (us-central1).
- A key registration that identifies the application.
- For Steam News: a decision on keeping titles, and any backfill budget.
- Valve's position on derived metrics and derived events (UNKNOWN), which blocks any
  public use.

**IGDB**

- Public use needs attribution, and IGDB's answer on the partnership.

**Steam catalogue**

- A storage region, and a privacy policy naming it.

**YouTube**

- An API project, and acceptance of the API Services Terms.
- Deletion or refresh within 30 days.
- YouTube's answer on the aggregation and derived-metrics clauses.

**Reddit**

- Approval under the Responsible Builder Policy. It is PENDING, and the route is
  reported, NOT VERIFIED.
- A deletion process, and short retention.

## 10. Real-data verification boundaries

- **Verified** means only a reviewed recording whose content matches a pinned digest.
- **Unverified** covers local stores, unpinned genuine recordings and acquisition records.
  So does anything a caller merely claims: a claim is either refused or kept unverified.
- **Not authentication.** A digest identifies content. It is not proof a source sent it.
  No signature or attestation exists or is implied.
- **Not executed.** The trusted live acquisition step was not executed. No policy was
  revised and no permission was inferred. Statements the policies mark as "reported"
  remain NOT VERIFIED.

## 11. Full quality-gate results (actual)

**Backend**

| Gate | Result |
|---|---|
| `uv run ruff format --check` | exit 0 |
| `uv run ruff check` | exit 0 |
| `uv run mypy` | exit 0, no issues in 244 files |
| `uv run metaspike export-schemas --check` | exit 0, up to date |
| `uv run metaspike evaluate-resolver` | exit 0; golden set v3 `c2b3aef7b372`; false AUTO_MATCH merges 0; precision 100.0% (52/52); real-evidence recall 100.0%; NEW_ENTITY 100.0% |
| Focused suites (see below) | 262 passed |
| `uv run pytest` | **1,640 passed, 8 deselected**, exit 0 (previously 1,618) |

The focused suites were readiness, opportunity history, opportunity, historical
validation, evidence, creator supply, community, Steam players and v0 parity.

**Frontend**

No frontend file changed in this checkpoint, so these gates were not required. They were
run anyway to confirm the private report schema and the UI are unaffected:

| Gate | Result |
|---|---|
| `contract:check` | exit 0 |
| `opportunities:schema:check` | exit 0 |
| `typecheck` | exit 0 |
| `lint` | exit 0 |
| `test` | exit 0, 197 passed |
| `build` | exit 0 |

## 12. Frozen-rule parity

These are unchanged:

- resolver_v2;
- baseline_rules_v1;
- signal_semantics_v2;
- metaspike_score_v0;
- event_heat_rules_v1;
- event_classification_rules_v1;
- validation_protocol_v1;
- outcome_rules_v1;
- recommendation_snapshot_v2;
- creator_opportunity_rules_v1.

No file changed under `sources/`, `event_sources/`, `creator_sources/`,
`community_sources/`, `domain/`, `catalogue/`, `validation/`, `scoring/`,
`opportunity/rules.py` or `evidence/provenance.py`. Source policies, collectors, identity
mapping and quarantine, `PINNED_RECORDINGS` and the public artifact contract 1.5.0 are
therefore all unchanged.

The U6.5 recorded historical metrics and fingerprint are unchanged (tested). The score v0,
resolver golden and rubric regressions all pass.

## 13. Git-only C2-F isolation

C2-F isolation: Git-only verification. No GCP access, no production action.

Verified with Git:

- Only `product/local-ui` changed.
- `main` is untouched: `origin/main` and the main worktree are both at
  `cb33198e2510965e41dff0f5fe7e75cacb7ee2ca`.
- No changes under `infra/`, `.github/`, `artifacts/`, `schemas/` or `apps/`.
- `BROAD_DISCOVERY_ENABLED = False` is unchanged.
- No deployment, source API call or source activation.

## 14. No later checkpoint started

Nothing beyond U6.6 has been started:

- no U6.7;
- no post-soak acceptance;
- no source activation;
- no recorder built or run;
- no deployment or publication.

Awaiting independent ChatGPT review and explicit owner authorisation.
