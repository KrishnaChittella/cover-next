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

---

# 2026-10-21 23:31 UTC — Wikimedia activation preflight; YouTube secret adoption (PR #4)

## What
A read-only production preflight for Wikimedia steady state; nothing in GCP or GitHub was changed. Also opened PR #4, which adopts the hand-made `youtube-data-api-key` container into Terraform: import, no reader, no version.

## Changed
- PR #4 `infra/prod/secrets.tf`: the container in the map with `[]` readers, plus an `import` block.
- PR #4 `infra/prod/tests/*.tftest.hcl`, `tests/test_cloud_foundation.py`: a fourth container with no reader; `override_resource` for the import.

## Checks
- Preflight: all 7 jobs on the trial image `…49b836bdb979c754` (no collector fix). Scheduler ENABLED with the window `0 * 12-18 10 *`. Control record gen `1791684706340016` expired 18 Oct. The four `SOAK_*` variables are still set, `SCHEDULER_PAUSED=false`, budget $20.
- Local lock-free plan of PR #3 infra: 3 in-place updates (scheduler → `0 * * * *`, silence alert on, volume threshold 31 → 60). No destroy and no IAM. The real plan also updates the image on all 7 jobs.
- PR #4 plan: 1 import and 1 label-bookkeeping update, 0 destroy. `terraform test` 25 passed; cloud-foundation tests 58 passed.
- PR #4 CI: the image scan **fails**, because `main` still has certifi 2026.7.22. The fix is in PR #3.

## Owner action
- Merge PR #3, delete `SOAK_MONTH`, `SOAK_START_DAY`, `SOAK_END_DAY`, `SOAK_DAILY_EXECUTION_CEILING`, then say go. I run the deploy plan for your apply approval.
- Then approve, separately: the control record (draft validated, armed to 2026-11-22T23:30Z) and the 16 Oct repair.
- PR #4: after the activation apply, rebase onto `main`, then an infra-only plan and your apply approval.

## Not done / next
- Nothing applied, merged or enabled. YouTube collection is off. C2-F criteria 9, 10 and 11 are unchanged (fail, unresolved, unresolved).

---

# 2026-10-22 01:22 UTC — Wikimedia steady state deployed; collection not yet enabled

## What
Applied the reviewed steady-state plan to production, after PR #3 merged (`65df008`). The new image (collector fix, certifi 2026.10.19) and the hourly scheduler are live. Collection stays off, because the control record is still expired.

## Changed (production)
- Plan run `39697004948`, SHA-256 `ad51e8dadfc5a63a7ef93e9ebd5282749ccd531bdb55faebe614588f01e27ec9`. Applied by run `39701900753`: 0 added, 10 changed, 0 destroyed. The hash was checked and there was no drift.
- All 7 Cloud Run jobs moved from `sha256:a8680b2b…c754` to `sha256:d1f07a77…67a3`. No secret references.
- Scheduler `ms-dispatch-15m`: `0 * * * *`, ENABLED. First tick 2026-10-22 02:00 UTC.
- Alerts: "dispatcher has not succeeded for 3 hours" turned on; volume threshold 31 → 60.
- The four `SOAK_*` repository variables were deleted by the owner.

## Checks
- deploy verify (full suite, Terraform, image smoke and scan): pass, on both the plan run and the apply run.
- Live read-back: images, `METASPIKE_IMAGE`, scheduler and both alert policies match the plan.

## Owner action
- Approve the control record write: gen `1791684706340016`, armed until 2026-11-22T23:30Z, Wikimedia only, 22 requests a day.
- Approve the 16 Oct repair (doc option B; `METASPIKE_WIKIMEDIA_DAYS=5` if run on 22 Oct). It must run before 12:00 UTC on the day it runs.

## Not done / next
- Not checked yet: the first 12:00 collection, the 13:00 dbt checks, `period_due_day_status`, alerts and cost.
- PR #4 (YouTube secret adoption) is untouched. C2-F criteria 9, 10 and 11 are unchanged.

---

# 2026-10-22 01:26 UTC — Wikimedia control record armed

## What
Wrote the owner-approved control record, which turns on Wikimedia-only steady-state collection. The first collection is at 12:00 UTC today, and the warehouse run with its dbt checks at 13:00.

## Changed (production)
- `gs://metaspike-prod-kc-ops/dispatch/control.json`: conditional on gen `1791684706340016`; new gen **`1792632354008756`** (01:25:54 UTC).
- Content: enabled, armed until 2026-11-22T23:30Z, 1 start per tick, 3 executions a day, `wikimedia_pageviews` only (22 requests a day, 14 targets per execution), no pins.

## Checks
- The read-back matches the approved draft byte for byte. The dispatcher's own parser accepts it and reports it as armed now.

## Owner action
- Approve the 16 Oct repair (doc option B, `METASPIKE_WIKIMEDIA_DAYS=5` on 22 Oct, before 12:00 UTC). Until it runs, the 13:00 warehouse run fails on the 16 Oct `missing` slot, through 30 Oct.
- Merge PR #5 (handoff docs).

## Not done / next
- Not checked yet: the 12:00 collection, the 13:00 dbt checks, `period_due_day_status`, alerts and cost.
- PR #4 is untouched. C2-F criteria 9, 10 and 11 are unchanged.

---

# 2026-10-22 04:36 UTC — 16 Oct repaired; Steam supervised recorder (PR #6)

## What
The owner-approved 16 Oct repair ran in production. Separately, PR #6 adds `metaspike record-steam-players`, a supervised Steam recorder that plans by default and records only on confirmation. Built and tested offline only; it has never run live.

## Changed
- Production: one execution `ms-wikimedia-2r4zt` (01:35–01:38 UTC), `WORK_ORDERS=supervised`, `WIKIMEDIA_DAYS=5`, due slot 20 Oct. 14 requests, 14/14 collected, 70 rows covering 16–20 Oct. 14 new slot markers for 20 Oct; the 14 bad 16 Oct markers are untouched.
- PR #6 `src/metaspike/steam_players_recorder.py`: plan, record, check and inspect. At most 8 eligible apps, one 15-minute slot (in the plan id), 8 requests, no retry. Kept as replay envelope plus `body_text`, with an `acquisition_record_v1` record each. Answers that arrive after the slot ends are dropped.
- PR #6 `src/metaspike/cli.py`: the `record-steam-players` command. `tests/test_steam_players_recorder.py`: 23 tests. `tests/test_evidence.py`: allowlist updated.

## Checks
- ruff, mypy (250 files): pass. Recorder tests 23 passed; related suites 135 passed.
- PR #6 CI on `030a101`: 1713 passed; image and infrastructure pass.
- 02:00 dispatcher tick: one execution, succeeded, no jobs started.

## Owner action
- Review and merge PR #6.
- Then approve one supervised Steam session (8 apps, 1 slot, 8 requests), with the key in your shell and its registration naming MetaSpike / krishnachittella.dev.

## Not done / next
- After 13:30 UTC: check the 12:00 collection, the 13:00 dbt run, `period_due_day_status` and alerts. A row-count failure is expected for 21 Oct (0 rows); 16 Oct should become `late`, likely on the 23 Oct run.
- PR #4 is untouched. Steam `private_collection` is still uncleared.

---

# 2026-10-22 07:09 UTC — PR #6 and PR #4 merged; secret containers protected and YouTube secret adopted

## What
Merged PR #6 (Steam recorder) and, with the owner's approval, PR #4. Applied PR #4's infrastructure-only plan: all four Secret Manager containers are protected from deletion, and `youtube-data-api-key` is under Terraform. The owner granted auto-merge for routine code, test and docs PRs with green CI.

## Changed
- `main`: `f8586b2` (PR #6), then `26d9df2` (PR #4 at `09a6360`).
- Production: plan run `39731593423` (reused image `d1f07a77…`), SHA-256 `6989e183987d1b5dde00bb35f05f79a55702e600ba716b6285d0bc26041e7a43`. Applied by run `39735710234`: 1 imported, 0 added, 4 changed, 0 destroyed. The hash was checked.
- `secrets.tf`: `deletion_protection = true` (in state) and `prevent_destroy` on all four containers. A throwaway plan that removed one entry failed with `Instance cannot be destroyed`.

## Checks
- PR #4 CI: 1713 passed, dependency audits, image and Terraform tests (25 + 5) pass.
- After the apply: all four have `deletion_protection=true` in state. YouTube version 2 is enabled. IAM is identical to before (YouTube has no reader). All 7 jobs have the same generation and image. Dispatcher ticks at 05:00, 06:00 and 07:00 succeeded; control record unchanged.
- Unverifiable: data-access audit logging is off, so "no value read" rests on the plan's scope, not on logs.

## Owner action
- Approve one supervised Steam session when ready (see the 04:36 entry).

## Not done / next
- After 13:30 UTC: the Wikimedia verification (12:00 collection, 13:00 dbt, `period_due_day_status`, alerts, cost).
- YouTube and Steam collection are off; no source permission changed.

---

# 2026-10-22 20:52 UTC — Wikimedia verification: expected row-count failure on 22 Oct

## What
Read-only check of today's runs. Collection is healthy. The 13:00 warehouse run failed one dbt data check because nothing was collected on 21 Oct, as predicted. No production change.

## Changed
- Nothing in production or code. This entry only.

## Checks
- 12:00 `ms-wikimedia-bqhq8`: succeeded, slot 20 Oct already marked, 0 requests, 0 rows (correct skip after the repair).
- 13:00 `ms-warehouse-vd29d`: exit 1 on both attempts. Load succeeded (repair's 70 rows in staging). dbt `PASS=30 ERROR=1 SKIP=3`: `assert_daily_rows_are_not_far_below_the_weekly_median` (21 Oct 0 rows vs median 56). Skipped `period_due_day_status`, its accepted-values test and `assert_every_due_day_arrived_in_time`.
- Alert "warehouse load or build failed" opened 13:10:48 and auto-resolved 14:13:17. That was the owner's notification.
- Dispatcher 02:00–20:00 all succeeded. Scheduler ENABLED hourly. Control record generation `1792632354008756` unchanged. The only GitHub failure today was the 03:38 PR #6 CI, since fixed.
- Mart logic run read-only: 10–15 Oct `on_time`, 16 Oct `late` (first seen 22 Oct 01:38), 20 Oct `on_time`. 17–19 Oct were never planned, so not judged. No `missing`.
- Unverified: today's cost.

## Owner action
- None. Re-running today would fail the same way.

## Not done / next
- After 13:30 UTC on 23 Oct: confirm the row-count test passes (22 Oct 70 rows vs median 42) and the mart is built with 16 Oct `late`. This depends on the 12:00 run collecting slot 21 Oct.

---

# 2026-10-22 22:20 UTC — First live API reads, identity regression set, v2 event rules, warehouse demand reader

## What
Started live development (PR #10 merged). Ran the owner-approved bounded probes, fixed the one bug they exposed, and connected genuine inputs end to end: warehouse demand + Steam players + Steam News events → evidence → opportunities. Every game honestly reads INSUFFICIENT (inputs unpinned; frozen v1 rules ignore decaying patches).

## Source calls (all local, private_development, UNVERIFIED, outputs in ../MetaSpike-live, nothing committed raw)
- Steam players (key from .env): 22:02 one request (app 570) failed locally with a gzip double-decode (PR #11 fixes it); 22:04 plan `plan_838b3102…` 8/8 recorded, inspection intact. **9 requests total, one slot.** CS2 718,524; Dota 2 700,154; Rust 83,691; Helldivers 2 38,736; BG3 34,902; Elden Ring 18,738; Tarkov 13,210; NMS 11,091. `cache-control max-age` 194–900 s: counts are cached up to 15 min. Digest `6632b8bc…` (not pinned).
- Steam News (no key): 22:04–22:07, **8 requests**, 8 apps, 40 posts. v1 promoted 0 (not official 24, no type 10, patch size 4, not occurrence 2). Feeds include third-party press (RPS, PC Gamer, PlayGround.ru).
- IGDB (Twitch credentials from .env): 22:05, **8 HTTP requests** (1 token + 7 queries), 139 game records, 35 uid links.
- YouTube: project quota read (Service Usage API, read-only): 10,000 units/day; search.list 100/day. No API call (no key in .env). Twitch: not called (needs a policy PR first).
- BigQuery (read-only, own data): export of 1,960 Wikimedia rows, 14 articles, 8 Jul–20 Oct, digest `79ef9ba2…`.

## PRs
- #11 merged `f008881`: recording transport decodes a compressed answer once.
- #12 identity regression (139 real IGDB records): uid collides across sources (570, 730, …); 0 title-only merges; reworks/shared names apart; storefront duplicates quarantined; contested: Prey Evil attached to Prey (2006) via IGDB port link.
- #13 `event_classification_rules_v2` + `ingest-events --reclassify`: real posts 0 → 5 promoted (all official patches, minor at the lower bound). v1 byte-identical.
- #14 `export-wikimedia-warehouse` + `evidence --wikimedia-warehouse`, point in time.

## Owner action
- Decide whether to pin digests after review: Steam players `6632b8bc…`, warehouse export `79ef9ba2…` (makes them VERIFIED inputs).
- Add `METASPIKE_YOUTUBE_API_KEY` to .env yourself to unblock the approved YouTube probe.

## Not done / next
- `creator_opportunity_rules_v2`: a recent official patch as a reaction-content cause (v1 counts only upcoming/active; minor_patch duration 0).
- Bounded Steam News recorder (replay files + acquisition records, pinnable); Twitch policy + 3-request probe; brief schema + UI.
- 23 Oct after 13:30 UTC: Wikimedia/dbt verification.
