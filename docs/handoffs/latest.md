# Handoff: latest checkpoint

This file is the short-lived handoff from Claude Code to ChatGPT. It is overwritten at
each checkpoint; Git history is the archive. It supports communication only and
replaces no code, test, audit or document.

Previous accepted checkpoint: **PHASE 4 PART C2-D PREP.1: PLAN READY**

Current checkpoint: **PHASE 4 PART C2-D: SOAK ACTIVE**

The first unattended soak is running. The reviewed plan was applied at 02:43 UTC, the
scheduler fired by itself at 03:00:00 UTC, and the dispatcher it started ran the
Wikimedia collection within every ceiling. **No local process is required for the rest
of the soak**: the control record expires by itself at 08:30 UTC, and nothing on the
owner's machine is running or waiting.

The soak is **not complete**. The scheduler stays enabled until it is paused by hand
(morning cleanup, below).

All times are UTC on 2026-10-09.

## One thing to know first: an alert fired at launch

The dispatcher-silence policy opened an incident at 02:44:29, seconds after the apply
enabled it, and will have sent an email. This is not a fault in the soak: the policy
fires when no dispatcher has succeeded in three hours, and at the moment it was
switched on none had. A dispatcher succeeded at 03:02, so the condition has cleared
and the incident should close by itself; it was still listed as open at 03:05. It was
not closed or altered by hand. No other incident is open.

## Preflight (02:33)

All passed, read-only:

- `main` at `675a18e`, level with origin, clean; the only change since the plan's commit
  (`55393ea`) is this handoff file.
- Plan run `37742520427` succeeded; its artifact was not expired (2026-10-11T07:27:34Z)
  and, downloaded again, hashed to
  `163df18caa122253ce9086d95cdb604b4cb29ac7612ed3246dd8daf4a08a63fe` exactly. Its
  rendered contents: 0 add, 2 change, 0 destroy; `paused: true -> false` and
  `enabled: false -> true`, nothing else. (The plan binary still cannot be decoded
  locally, one Terraform patch version apart; the rendered output and the hash are what
  was checked.)
- Prod state serial 9. No apply since the plan.
- Scheduler `ms-dispatch-15m`: PAUSED, `0 * * * *`, `Etc/UTC`, no attempt ever.
- Repository variable `SCHEDULER_PAUSED` = `true` (left `true` throughout).
- Control record present, disabled, generation `1791432826876698`.
- 14 executions. All seven jobs on
  `sha256:a8680b2be8b5e4bea48392c2b5324b44a680b4c2a4d56e2f49b836bdb979c754`, with every
  `METASPIKE_IMAGE` equal to it (checked with the workflow's own resolving script).
- BigQuery project daily query limit 10,240 MiB.
- 0 secret versions; 0 user-managed keys.
- Retained data intact: 1,316 observations, 14 ledger rows, 12 run rows.

## Control record

Built with the repository's `ControlRecord` model and written at 02:33:30 on the exact
previous generation:

```
{"version":1,"enabled":true,"armed_until":"2026-10-09T08:30:00Z","max_job_starts_per_tick":1,"max_executions_per_day":3,"sources":{"wikimedia_pageviews":{"enabled":true,"max_requests_per_day":22,"max_targets_per_execution":14}},"pins":[]}
```

- Old generation `1791432826876698`; **new generation `1791513212387018`**.
- SHA-256 `bbacc1b8342d5794e274020ec510a8e77d98a3db31b8bb96a91bcffe8a2461d8`; read back
  byte for byte identical.
- Parsed with the repository's reader at that moment: active, no refusal.
- Meaning: Wikimedia only (no Steam, IGDB, catalogue or sampled source; no pins); at
  most 22 physical outbound HTTP attempts for the UTC day; at most 14 targets an
  execution; at most one child start a tick; at most three dispatcher-started
  executions for the UTC day; **expires 08:30 UTC**.

## Apply

| | |
|---|---|
| Reviewed plan run | `37742520427` |
| Plan SHA-256 | `163df18caa122253ce9086d95cdb604b4cb29ac7612ed3246dd8daf4a08a63fe` |
| Apply run | `37875151956`, started 02:33:38, finished 02:43:35, success |
| Result | `Apply complete! Resources: 0 added, 2 changed, 0 destroyed.` |
| Changed | `google_cloud_scheduler_job.tick`, `google_monitoring_alert_policy.dispatcher_silent` |
| Prod state serial | 9 → 10 |

No new plan was made. `allow_destroy` false.

## Post-apply verification (02:44, before the 02:57 cut-off)

- **Scheduler:** ENABLED; schedule `0 * * * *`; time zone `Etc/UTC`; next run
  03:00:00; same target (`…/jobs/ms-dispatch:run`), same identity (`ms-scheduler`),
  same retry settings (retry count 1) and deadline (180 s).
- **Monitoring:** 8 policies, all enabled; `dispatcher_silent` now enabled; the other
  seven unchanged.
- **Jobs:** all seven still at generation 6 on `sha256:a8680b2b…c754`. No job
  definition changed.
- **Control:** unchanged since arming, generation `1791513212387018`.
- **Security:** 0 secret versions; 0 user-managed keys.
- **Executions:** still 14. Nothing was started by hand, then or later.

## The 03:00 tick: proven

**Scheduler.** Attempt at 03:00:00.98, HTTP 200 from the Cloud Run API; one attempt, no
retry. Next run scheduled for 04:00.

**Dispatcher `ms-dispatch-cfw4f`**, run id `20261009T030219Z-ms-dispatch-cfw4f`.
**Created by `ms-scheduler`**, not by a person. One attempt, exit 0.

- Tick `2026-10-09T03:00:00Z`. The control record was accepted.
- Schedule `s9939a2accd9a17c152bc` reused (not regenerated). 1 partition read.
- Expected targets 14; dispositions `planned` 14. Only Wikimedia was due; no other
  source exists in the configuration or was planned.
- One work order, one child started: `ms-wikimedia`. Executions counted today: 1.

**Work order `88c3319704fe9da52b41d759f1c38e3d`**: Wikimedia pageviews, slot
2026-10-07, `window_days` 4, **14 targets, `max_requests` 22**, expiring 03:50, not a
probe. The day's counter shows 22 attempts reserved for Wikimedia, which is the whole
of the day's cap.

**Ledger.** One batch for the tick with 14 rows: tier 4, `due_at` 03:00, disposition
`planned`, all carrying that order id. Exactly one lease exists for the tick, so a
repeated delivery would have done nothing.

**Collector `ms-wikimedia-s9gg8`**, run id `20261009T030433Z-ms-wikimedia-s9gg8`.
Created by `ms-dispatch`. One attempt, exit 0.

- **Outbound attempts 14, of a budget of 22. Retries 0.** No source error.
- Targets intended 14, attempted 14, collected 14, failed 0, deferred by the budget 0,
  skipped 0.
- Window 4 days; history filled 0; no 90-day refetch. 56 rows landed (14 articles by 4
  days, ending 2026-10-07). 14 completion markers for the slot; bootstrap markers still
  14.
- Receipt: `succeeded`, 14 requests, budget 22, 0 retries, completed 03:04:38. This is
  the first receipt written with the attempt fields, and the first run of the
  request-budget image in the cloud.

No WARNING or ERROR log entry from any job. No Secret Manager access.

| | `ms-dispatch-cfw4f` | `ms-wikimedia-s9gg8` |
|---|---|---|
| Created | 03:00:01 | 03:02:21 |
| First application log | 03:02:19 | 03:04:33 |
| Completed | 03:02:29 | 03:04:42 |

## State at 03:05

- **Executions: 16** (14 before, plus one dispatcher and one collector). The ceiling for
  the checkpoint is 29.
- Scheduler ENABLED, last attempt 03:00:00, next 04:00.
- `warehouse.json`: a warehouse run is owed since 03:00, so the 04:00 tick should start
  `ms-warehouse`.
- Health still shows the order as pending; the 04:00 tick folds the receipt in.

## What should happen with nobody watching

| Time | Expected |
|---|---|
| 04:00 | Dispatcher folds the receipt and starts `ms-warehouse` (second of three permitted child executions) |
| 05:00 to 08:00 | Hourly dispatcher ticks with nothing due; no child started |
| 08:30 | The control record expires |
| 09:00 onward | Each hourly dispatcher refuses with `control_expired`; no source request and no child job is possible |

After 08:30 the source is safe whatever else happens. The dispatcher still runs once an
hour until the scheduler is paused, which is why cleanup should not be left late.

## Morning cleanup (not yet done; needs the owner)

Recommended by about 13:00 UTC (08:00 CDT) or earlier. By 13:00 the execution count
would be about 27, under the ceiling of 29; each further hour adds one.

1. Verify the 04:00 warehouse tick.
2. Verify the control record expired at 08:30.
3. Verify at least the 09:00 dispatcher refused with `control_expired`.
4. Pause at once:
   `gcloud scheduler jobs pause ms-dispatch-15m --location=us-central1 --project=metaspike-prod-kc`
5. Read back and require `PAUSED`:
   `gcloud scheduler jobs describe ms-dispatch-15m --location=us-central1 --project=metaspike-prod-kc --format='value(state)'`
6. Write the control record with `enabled: false`, on its generation.
7. Audit the whole soak.
8. Make a new infrastructure-only reconciliation plan with `scheduler_paused=true`
   (the repository variable is already `true`) and `reuse_deployed_image`.
9. Require that it only returns the scheduler to paused and disables
   `dispatcher_silent`.
10. Apply it only after review.

Note for step 4 onward: once the scheduler is paused, the silence alert stays enabled
until step 10 and will fire again about three hours after the last successful
dispatcher.

## On anything unexpected

Pause the scheduler with the command above, confirm `PAUSED`, disable the control
record, retry nothing by hand, keep the evidence, and report blocked.

## Warnings

1. **The silence alert fired at launch** (top of this file). Expect one alert email
   from about 02:44, and a recovery when it closes.
2. **The scheduler runs hourly until paused by hand.** Nothing pauses it automatically.
3. **The whole day's request budget is already reserved** (22 of 22). If the 03:00
   collection had needed to be redone today, the dispatcher would have had no attempts
   to give; that is the conservative reservation working as designed.
4. A background wait from this session timed out and ended by itself; it performed no
   cloud action. Nothing local is running.
5. Still open from earlier: the deferred freshness alert; the missed-tick review before
   any sampled source; the branch-protection decision before any secret-backed source;
   local Terraform one patch behind CI.

## Exact next approval required

The C2-D completion step, when the owner returns: the cleanup sequence above, the soak
audit, and review of the reconciliation plan before it is applied. C2-E has not begun.
