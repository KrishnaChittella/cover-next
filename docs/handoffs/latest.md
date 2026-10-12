# Handoff: latest checkpoint (product branch)

This file is the short-lived handoff from Claude Code to ChatGPT. **This copy lives only
on the branch `product/local-ui`.** The handoff on `main` is the C2-F soak's and was not
touched. It supports communication only and replaces no code, test or document.

Production baseline: **PHASE 4 PART C2-F: SEVEN-DAY SOAK RUNNING** (observed read-only
at 02:34 UTC on 2026-10-12; see the last section. Not altered.)

Parallel checkpoint: **PRODUCT BUILD U5: OFFLINE CATALOGUE EXPANSION FOUNDATION — CHATGPT REVIEW REQUIRED**

Baseline commit `1936e07` (U4.2), branch `product/local-ui`. Offline only. No source
was called, nothing was stored, nothing was deployed.

Full description: `docs/catalogue-expansion.md`.

## IMPLEMENTED

A thin layer over what already existed. No second resolver, no second store.

- `catalogue/evaluation.py`: `evaluate_broad_catalogue(records, policies=, stage=,
  as_of=)`. Runs the existing `generate_catalogue_blocked` on the records production's
  scope would see, then on all of them, in memory, and summarises in counts.
- `catalogue/coverage.py`: per entity, whether each monitoring source could be asked,
  reusing `SourceEligibility`. Steam is exactly `steam_eligibility`; Wikimedia is the
  catalogue's exact article mapping.
- `metaspike evaluate-catalogue --stage ...`: the same, as a command. Counts only.
- `CATALOGUE_SOURCE_POLICIES`: the explicit mapping the command passes.
- `tests/catalogue_scale.py`: a measurement helper for synthetic sizes.
- 18 tests (15 in ordinary CI, 3 marked slow).

### Controlled broad-evaluation behaviour

Requirements A–K of the brief, each met and tested:

| | |
|---|---|
| A. Recorded records or fixtures only | takes records; no adapter or client is imported |
| B. States its scope | every summary says `offline_broad_evaluation` and the stage |
| C, D. Policy and stage | see below |
| E. Existing resolver and blocked path | calls `generate_catalogue_blocked` twice; holds no identity rule |
| F. Bootstrap entities and ids preserved | tested |
| G. Admitted separately from quarantined | bound / quarantined / related, and new / existing |
| H. Related products apart from entities | tested |
| I. Production pointer untouched | no repository, object store or pointer is imported |
| J. No live call | tested with the network blocked |
| K. Uncleared or unsupported source refused | tested |

`BROAD_DISCOVERY_ENABLED` is still `False`. `in_scope(..., BROAD)` still raises.
`advance_catalogue` still defaults to the narrow scope. The evaluation does not go
through that gate at all.

### Source-policy enforcement

Every source among the records needs a `SourcePolicy`, handed in under its own name,
that clears the **stated** stage for `CATALOGUE_METADATA`. `policies` and `stage` have
no defaults. Refused: no mapping; an empty one; one missing that source; a policy under
another source's name; no stage; a stage not cleared; an unknown source; a record posing
as bootstrap evidence; an unknown or prohibited storage permission.

No policy was changed. The recorded fixtures evaluate at private development and are
refused at private collection (Steam's catalogue is not cleared there), public launch
and commercial production.

### Evidence, identity and quarantine

Unchanged, and pinned by tests: bootstrap ids, slugs, identifiers, typed relationships
and lifecycle preserved; proven identity joins without duplicating; an eligible new
game becomes `NEW_ENTITY`; a shared title is never identity; related products, non-games
and packaging variants never become entities; repeating inputs makes zero resolver
calls and changes nothing; new evidence releases a quarantine by appending, with the
earlier decision and bindings intact and a point-in-time read still showing the record
unbound; no identifier is inferred from a title; cross-checked against all-pairs with
no difference.

## TESTED OFFLINE

### Recorded inputs actually used

The existing fixture `tests/fixtures/catalogue`, recorded 2026-10-05: **266 IGDB games
and 128 Steam apps, 394 records.** Nothing new was fetched.

| | |
|---|---|
| Records in production's scope / seen only by broad | 17 / 377 |
| Bound | 327 (219 admitted as new entities, 108 resolved to existing) |
| Quarantined | 44 (title only 21, conflicting evidence 12, id-link title mismatch 8, undetermined type 2, multiple candidates 1) |
| Related products | 23 |
| Entities: production's scope / broad | 15 / 234 |

### Source coverage

| Source | Eligible | Inapplicable | Unknown |
|---|---|---|---|
| Wikimedia pageviews | 14 | 0 | 220 |
| Steam concurrent players | 90 | 69 | 75 |

Ready for both 5. **Without any eligible source 135.** These are identifier states, not
measurements: no field can hold a count of players or views, and unknown or inapplicable
is never zero.

### Synthetic-only scale tests

`tests/catalogue_samples.py` (pre-existing): invented games in fixed strata, shaped like
IGDB and Steam records, deliberately heavy in hard cases. Measured on one laptop, one
process per size:

| Records | Entities | Quarantined | Resolver calls | Entities examined | All-pairs | Share | Full scans | Total time | Peak memory |
|---|---|---|---|---|---|---|---|---|---|
| 1,000 | 432 | 402 | 1,030 | 25,176 | 675,566 | 3.7% | 60 | 0.51 s | 54 MB |
| 5,000 | 2,070 | 2,021 | 5,167 | 578,428 | 16,138,916 | 3.6% | 312 | 11.4 s | 108 MB |
| 10,000 | 4,117 | 4,043 | 10,343 | 2,289,686 | 64,147,993 | 3.6% | 628 | 49.2 s | 169 MB |
| 20,000 | 8,210 | 8,094 | 20,690 | 9,108,927 | 255,861,875 | 3.6% | 1,260 | 214.2 s | 306 MB |

Nothing larger was run; nothing is claimed for 50,000. The existing 50,000-entity
*scheduling* simulation is unrelated to resolving and was not re-run.

### Performance findings

1. Blocking holds at about 3.6% of all-pairs at every size.
2. **Resolving is quadratic anyway** (about ×4.3 per doubling). A record whose title
   cannot be keyed falls back to a full scan of every entity; about 6% of synthetic
   records do, and those scans are at least 98% of the entities examined at 20,000. On
   the 394 recorded records there were 0 full scans, so the real share is unknown.
3. The coverage summary is quadratic too (22 s at 20,000): `steam_eligibility` scans
   every binding per entity.
4. Memory is linear, about 15 MB per thousand records.
5. A stored snapshot is the whole state each generation: about 2.1 KB per record
   (measured at 1,000 and 5,000).

**None was fixed.** The first is in the resolver's blocking and needs its own reviewed
change and equivalence proof.

## Current catalogue limitations identified

- Production resolves 17 of the 394 recorded records and has 15 entities.
- Only 394 real recorded records exist. Behaviour on thousands of real games is
  unmeasured.
- Entities admitted by a broad pass have **no source identifier**. 220 of 234 are
  unknown for Wikimedia, the only source cleared for unattended collection. No approved
  way to obtain an article mapping exists, and a title may not be used.
- The quadratic costs above. The production catalogue job has a one-hour timeout and
  2 GiB; whether a first broad build fits has not been measured.
- `metaspike generate-catalogue` and `audit-bootstrap-bindings` already resolve every
  recorded record locally with no scope or policy check. They predate this checkpoint
  and were left alone; `evaluate-catalogue` is the gated path. Flagged for a ruling.

## NOT PRODUCTION-PROVEN

Everything above. It ran on a laptop over fixtures. No broad build has run on the
production job, against real records at scale, or through the repository.

## BLOCKED BY SOURCE POLICY

- Steam catalogue: private development only. Not cleared for unattended collection.
- IGDB: cleared through private collection; nothing public or commercial.
- Steam player counts: private development only. Ninety entities are identifier-ready
  and none may be sampled in production.
- Steam News: private development only.

## DEFERRED UNTIL AFTER C2-F

Turning broad discovery on; any live catalogue fetch; a fix to the full-scan fallback;
an indexed eligibility; snapshot retention; a measured broad build on the real job; any
new source in production; any change to the soak.

## What was deliberately not activated

Broad production discovery; any live source call; any stored catalogue or pointer move;
sampling, tiers or schedules; any score, component or weight; any change to the
developer interface or the artifact contract (still 1.5.0).

## What remains for multi-source demand signals

Reviewed, not rebuilt: source families with one voice each, four applicability states,
`signal_semantics_v2`, signals normalised against their own history, the shadow-only
access gate, Steam eligibility, event evidence and its gates, point-in-time validation.
Nothing was added to them. No metric was aggregated, no source counted as independent
agreement, no weight introduced.

Still needed, in order: (1) a Wikimedia identifier for entities beyond the bootstrap
14, which is the largest gap; (2) permission; (3) a combination rule for entities with
several Steam apps; (4) an agreement rule across families, defined before it is fitted.

## Cost

Nothing here costs anything. For a broad rollout, measured and estimated kept apart:

| Item | Measured | Assumed | Estimate |
|---|---|---|---|
| Discovery, requests | documented page limits (IGDB 500; Steam app list 50,000) | | 20,000 IGDB games is 40 requests; API price $0 |
| Discovery, compute | 192 s to resolve 20,000 synthetic records, laptop | 2–4× slower on one Cloud Run vCPU; about $0.0000185 a vCPU-second (the approved table's rate) | one to two cents, once |
| Metadata refresh | | one small daily execution at the one-minute minimum | about $0.03 a month |
| Daily Wikimedia coverage | | approved planning figures, C2 plan §28, simulated, with a stand-in sampled source that is not approved | $4.28 at 1,000 catalogued; $4.69 at 5,000; $5.76 at 10,000; $11.55 at 50,000 a month |
| Selective sampled monitoring | | C2 plan §4 estimate, 24,200 requests a day at the caps | about $9 a month at one request a second, or inside the allowance at four concurrent. Blocked by policy |
| Storage | about 2.1 KB of snapshot per record | about $0.02 a GB-month; one generation a day | about 42 MB a generation at 20,000; about $0.03 a month, accumulating until pruned |

Discovery, refresh and storage together are cents. Collection cadence is the cost that
matters, and the approved figures are unchanged. Boundaries unchanged: preferred $5–10,
scrutiny $10–15, hard $20. No expense was activated.

## Proofs of no change

| | |
|---|---|
| Score v0 | `scoring/` `3b857c551d5ab5b0`, `features/` `24cf7d170f57d8ab`: no diff; golden replay bit-for-bit |
| Identity golden set | `tests/fixtures/resolution_golden.json` byte-identical (SHA-256 pinned in a test); version 3, 200 cases, 178 correct, **0 false automatic merges**; `evaluate-resolver` exit 0 with 100% auto-match precision (52/52) |
| Resolver, blocking, generation, persistence, eligibility | `resolution/`, `catalogue/generate.py`, `incremental.py`, `persistence.py`, `eligibility.py`, `domain/`: no diff |
| U3.1 | `validation/`: no diff; the publication-lag regression and the pinned no-event set hashes pass |
| U4.2 | `event_sources/`, `events/`: no diff; the provenance and fail-closed tests pass |
| Baselines, Event Heat, classifier, outcome rules, protocol | fingerprint tests pass |
| Artifact contract | 1.5.0; `schemas/` and `apps/` have no diff |

## Gates

Backend: `ruff format --check` clean (218 files); `ruff check` clean; `mypy` clean (206
files); `pytest` **1,478 passed, 8 deselected**; `export-schemas --check` up to date;
`evaluate-resolver` exit 0.

The 8 deselected are the `slow` and `live` markers, excluded by the project's default:
the 5 that existed, and 3 new slow scale cases (5,000 / 10,000 / 20,000). Of the new
ones the 5,000 case was run and passed; the other two were not run as tests, but the
same measurement was run for all four sizes through `python -m tests.catalogue_scale`.
The pre-existing 10,000-record blocked-versus-reference proof was not re-run.

Frontend (no frontend change): `contract:check`, `typecheck`, `lint`, `test` (179
passed), `build` all pass.

## Production isolation (read-only, 02:34 UTC 2026-10-12)

Verified by reading GCP and GitHub, not inferred:

- `main` = `cb33198`, level with origin, clean. `product/local-ui` is not merged; no
  pull request exists.
- Workflow triggers: `deploy.yml` runs on push to `main` and manual dispatch only;
  `ci.yml` on pull requests, calls and manual dispatch. A push to `product/local-ui`
  starts nothing. Last workflow run is still 38104941975 (11 October).
- Terraform state: latest generation `1791685217422653`, the launch apply. Unchanged.
- Control record: generation `1791684706340016`. Unchanged.
- Scheduler configuration: ENABLED, `0 * 12-18 10 *`. Unchanged.
- Seven jobs, generation 6, image `sha256:a8680b2be8b5…`. Unchanged.
- IAM, dataset schemas, secrets and alert policies identical to the previous snapshot.
  0 secret versions, 0 user keys.
- Repository variables unchanged.
- No Wikimedia, Steam or IGDB call was made by U5. The local Steam quota ledger still
  reads 3, from the U4 probe.
- The portfolio repository was not touched.

## C2-F observation (read-only; separate from U5; not altered)

The soak window opened at 2026-10-12T00:00Z and is running by itself.

| Tick | Execution | Result |
|---|---|---|
| 00:00 UTC | `ms-dispatch-5lc87` | succeeded |
| 01:00 UTC | `ms-dispatch-xv92w` | succeeded |
| 02:00 UTC | `ms-dispatch-zbb72` | succeeded |

Three dispatcher executions, no collector or warehouse run yet, which is what the launch
plan expects before 03:00. Scheduler last attempt 02:00:04Z, next 03:00Z. Executions 35
→ 38. Objects in the operations bucket 138 → 145 and in landing 102 → 114.

This is an observation at one moment, not a judgement of the soak. The 03:00 collection
and 04:00 warehouse run had not happened yet. Nothing was triggered, restarted or
changed.

## Commits

`c8b59c4` feat: add offline catalogue expansion foundation; then this handoff. Pushed
to `origin/product/local-ui` only. No PR, no merge to `main`.

## Exact next approval required

ChatGPT review of U5.

STOP. The next U5 checkpoint has not been started.
