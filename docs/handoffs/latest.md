# Handoff: latest checkpoint (product branch)

## MULTISOURCE IMPLEMENTATION — RECORDED SOURCES TO THE PRODUCT UI

Previous checkpoint: PRODUCT BUILD U6.9 (`ec679aa`, on `product/local-ui`).

## Branch

- `product/multisource`, cut from `product/local-ui` at
  `ec679aa7f566d25f0a2a08873abceff2718aedc9`.
- Implementation SHA: `ab9d989a76d551d2351237af3781a9608ca1eade`.
- Docs SHA: `ec5958d6dc343deccdcaeae79c138901162086de`.
- These branches were not modified: `main` (`cb33198`), `product/local-ui` (`ec679aa`)
  and `ops/c2f-acceptance-review` (`67f9e31`).
- Nothing was merged or deployed.
- No GCP access, no live source call, no credential read.

## What was done

1. **Collector completeness fix.** Commit `111d40f`. `collect_wikimedia` no longer
   marks a slot done when the answer lacks the due period.
   - The answer is still landed.
   - The target counts in `targets_due_period_missing`, and a `due_period_missing`
     WARNING is logged.
   - The run, and the work-order outcome, are partial.
   - The regression test reproduces the C2-F 16 October answer. The next run then asks
     again and completes.
2. **Steam replay dating fix.** Commits `a8abc4a` and `c90cf3b`.
   - **Before:** `metaspike sample --replay` dated a 6 October response by the wall
     clock, and invented failed attempts for apps with no recording.
   - **Now:**
     - replays are dated from the recording's `recorded_at`;
     - only recorded apps are asked;
     - a replay may not be filed under a later slot.
   - The tests were updated to the stricter behaviour.
3. **Integrated path.** Commit `8a03d1a`.
   - `npm run opportunities:integrated` reads all the genuine recorded inputs through
     the existing `sample` and `evidence --opportunities` commands. No new pipeline was
     added.
   - New UI view `/opportunities?view=integrated`, selected by the exact word only. It
     carries a notice and lists the unverified sources.
   - The board gains a column of demand and event sources, each with state and origin.
   - The game page source table gains kind and freshness.
4. **End-to-end proof.** Commit `ab9d989`. `tests/test_integrated_recorded.py` runs the
   generator's commands with sockets refused and the clock moved to 2027. It requires
   the UI fixture's exact bytes and asserts every source's standing.

## Inputs, by standing

| | |
|---|---|
| **Verified (pinned)** | IGDB and Steam app-list catalogue records, identity only. Wikimedia pageviews for six articles through 3 Oct |
| **Genuinely recorded, unverified** | One Steam players response: app 730, 2026-10-06 01:25:26 UTC. It reaches Counter-Strike 2 as `available`, `fresh` and UNVERIFIED, and is never counted |
| **Synthetic** | The Steam News fixtures and the existing demonstration view. Neither is used in the integrated report |
| **Absent** | Eight Wikimedia articles; seven Steam apps; real Steam News on this machine; YouTube; Reddit (approval PENDING) |

**Result:** every game abstains. No frozen rule, score, schema or policy was changed.
The public contract is still 1.5.0.

## Steam: steps remaining

Details are in `docs/multisource-integration.md`.

**Before the first supervised real sample:**

1. Owner authorisation for one session: 8 apps, 1 slot, 8 requests.
2. The key, set in the owner's shell.
3. **A supervised Steam recorder (not built).** Live `sample` writes neither an
   acquisition record nor a replayable recording, so nothing could ever be pinned.
4. Review the recording, then pin its digest.
5. History accrues only under `private_collection`, which needs:
   - a privacy notice;
   - US-only storage;
   - the key registration.

**Before anything public:**

- Valve's answer on derived metrics and commercial use; both are UNKNOWN.
- A privacy notice.
- Valve attribution and branding.
- A reviewed public contract change.

## Gates

| Gate | Result |
|---|---|
| ruff check, ruff format --check, mypy (247 files) | exit 0 |
| pytest | **1677 passed**, 8 deselected, 9 m 05 s |
| dev-ui typecheck, lint | exit 0 |
| dev-ui vitest | **242 passed** (16 files) |
| contract:check, opportunities:schema:check | current |
| next build | exit 0; no private report traced into `.next` |
| Live check | dev server on 127.0.0.1: integrated board, game page, compare, verified and synthetic views all HTTP 200, with the source table rendered |

## Run it

```
cd apps/dev-ui
npm run opportunities:integrated && npm run opportunities:recorded
METASPIKE_PRIVATE_OPPORTUNITIES=1 npm run dev
# http://127.0.0.1:3000/opportunities?view=integrated
# http://127.0.0.1:3000/opportunities/counter-strike-2?view=integrated
uv run pytest -q tests/test_integrated_recorded.py tests/test_jobs.py tests/test_steam_players.py
```

## Next step proposed (not started)

A supervised Steam players recorder, built and tested offline only:

- plan by default;
- `--live --confirm`;
- recordings written in replay format;
- acquisition records;
- `--inspect`.

Any live run needs separate owner approval.

STOP. Awaiting review.

---

# 2026-10-21 22:35 UTC — PR #3 finished (`ci/steady-state-hardening`, CI green on `33775f9`)

## What
Finished PR #3: the image scan's CRITICAL certifi finding is fixed. The 36 h due-day check now fails only on periods still missing; late recoveries are kept as SLA breaches. The 12:00 collection / 13:00 warehouse day is tested end to end.

## Changed
- `uv.lock`, `warehouse/uv.lock`: certifi 2026.7.22 → 2026.10.19 (GHSA-6q48-6c7w-9gw2).
- `.github/workflows/ci.yml`: the `application` job's limit goes from 20 to 30 min (the suite takes ~20 min; a passing run was cancelled).
- `warehouse/models/marts/period_due_day_status.sql` (new): `on_time`, `late`, `missing` or `pending` per planned period.
- `warehouse/tests/assert_every_due_day_arrived_in_time.sql`: fails on `missing` only.
- `tests/test_warehouse_data_checks.py`: late, missing-then-repaired and kept-breach cases, plus an 8-day dispatcher → collector → warehouse → real dbt run.
- `docs/wikimedia-2026-10-16-gap.md`: the 14 bad 16 Oct markers (confirmed by a read-only listing) and the repair options.

## Checks
- ruff, mypy (248 files): pass.
- CI full suite: 1690 passed. Image scan: pass. Terraform tests: 25 + 5 passed.

## Owner action
- Delete the `SOAK_*` repository variables.
- Decide on the three `*_VERSION` secret variables.
- Merge, then deploy (plan → apply).
- Write a new control record.
- Repair 16 Oct (doc option B). Until then, every 13:00 warehouse run fails, through 30 Oct.

## Not done / next
- No merge, deploy or GCP write. Criteria 10 and 11 of the C2-F alert audit are still unresolved.
