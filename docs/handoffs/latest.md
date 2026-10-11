# Handoff: latest checkpoint

This file is the short-lived handoff from Claude Code to ChatGPT. It is overwritten at
each checkpoint; Git history is the archive. It supports communication only and
replaces no code, test, audit or document.

Previous checkpoint: **PHASE 4 PART C2-F: FINAL UNPAUSE PLAN READY — REVIEW REQUIRED**

Current checkpoint: **PHASE 4 PART C2-F: ARMED AND SCHEDULED — SEVEN-DAY SOAK PENDING**

The control record is armed and the approved unpause plan is applied. The scheduler is
enabled with the finite cron and its next run is the first tick of the window.
**C2-F is NOT YET PASSED and has not yet run:** the observation window opens at
2026-10-12T00:00:00Z. Nothing was triggered by hand and no source was called.

No manual action is required before the first tick. Nothing depends on a laptop.

## Launch

| | |
|---|---|
| Launch completed | 2026-10-11 02:20:17 UTC, 21 h 40 m before the first tick |
| Control record written | 02:11:44 UTC |
| Control generation | `1791578645841911` (disabled) → **`1791684706340016`** (armed) |
| Write mechanism | `--if-generation-match` on the disabled generation; it held |
| Read-back | byte-identical to the approved record (238 bytes); validated by the dispatcher's own model before writing |
| Approved plan | run `38101148562`, SHA-256 `61c335bbd54e7bbed2cba09490315331d0c858f595f079aea28fd75e95659bd1` |
| Apply run | `38104368815`, apply job finished 02:20:20 UTC, success |
| Terraform result | `Apply complete! Resources: 0 added, 2 changed, 0 destroyed.` (0 replaced) |
| Prod state serial | 13 → **14**; still 94 managed resources |

Order kept: preflight, then control, then apply. Between the control write and the
apply the scheduler was confirmed still PAUSED with 35 executions.

The control record now in place:

```json
{"version":1,"enabled":true,"armed_until":"2026-10-18T23:30:00Z","max_job_starts_per_tick":1,"max_executions_per_day":3,"sources":{"wikimedia_pageviews":{"enabled":true,"max_requests_per_day":22,"max_targets_per_execution":14}},"pins":[]}
```

## Preflight (02:10 UTC on 11 October), all met

Before midnight UTC, against an external clock. `main` level with origin and clean;
only two documents changed since the plan's commit `fea9429`. Plan run succeeded;
artifact unexpired (2026-10-14T01:24:20Z) and hashing exactly; rendered diff 0 add,
2 change, 0 destroy. State serial 13, 94 resources, no apply since the plan. Scheduler
PAUSED `0 * * * *` `Etc/UTC`. Control disabled at generation `1791578645841911`. 35
executions, the last being the catch-up `ms-warehouse-lxqp9`. Seven jobs on the
approved digest. 51 landed batches and 51 load receipts, so none pending;
`collection_runs` 54, observations 1,372, ledger 28. Ten policies, silence disabled,
the two failure alerts enabled, volume threshold 60. The five repository variables as
approved. 0 secret versions, 0 keys, WIF ACTIVE, quota 10,240 MiB. No Cloud Run log
entry since the catch-up.

## Verified after the apply (02:20 UTC)

**Scheduler**, read from the live job:

| | |
|---|---|
| State | **ENABLED** |
| Schedule | **`0 * 12-18 10 *`** |
| Time zone | `Etc/UTC` |
| Next scheduled run | **2026-10-12T00:00:00Z** |
| Last attempt | none |

Only four fields differ from before the apply: state, schedule, the next-run time and
the update timestamp. Retry settings, deadline, target and identity are unchanged. No
scheduler attempt has been logged since the scheduler was paused on 9 October.

**Monitoring.** Ten policies, none added or removed. One changed: "unusually many job
executions in a day" now reads "more than 31 completed executions in 24 hours" with a
threshold of **31**, and nothing else in that policy changed. `dispatcher_silent`
remains disabled. `scheduler_delivery_failed` and `dispatcher_failed` remain enabled.
The other seven are identical to their pre-apply copies.

**Terraform outputs:** `collection_running = true`,
`daily_execution_alert_threshold = 31`, `dispatch_schedule = "0 * 12-18 10 *"`,
`soak_scheduled_ticks = 168`.

**Control:** enabled, byte-identical to the approved record, `armed_until`
2026-10-18T23:30:00Z, generation `1791684706340016`.

**Cloud Run:** executions still **35**; the last is the catch-up of 01:06 UTC. No
dispatcher, collector or warehouse job has run since. No Cloud Run log entry at all
since the catch-up. All seven jobs at generation 6 on
`sha256:a8680b2be8b5e4bea48392c2b5324b44a680b4c2a4d56e2f49b836bdb979c754`.

**Security:** 0 secret versions; 0 user-managed keys across 10 service accounts; the
audit comparison differs only in the alert list and the scheduler; WIF pool ACTIVE.

**BigQuery quota:** 10,240 MiB a day.

**Sources:** Wikimedia 0, Steam 0, IGDB 0.

**Repository variables:** `SCHEDULER_PAUSED=false`, `SOAK_MONTH=10`,
`SOAK_START_DAY=12`, `SOAK_END_DAY=18`, `SOAK_DAILY_EXECUTION_CEILING=31`.

The portfolio repository was not touched.

## The soak

| | |
|---|---|
| Opens | 2026-10-12T00:00:00Z |
| Last tick | 2026-10-18T23:00:00Z |
| Control expires | 2026-10-18T23:30:00Z |
| Ticks | 168 |

Expected: 168 dispatchers; a collection opportunity at 03:00 UTC on each of seven
days; Monday 12 October with the 14-day reconciliation window, Tuesday to Sunday with
4-day windows; 14 requests a day (above 22 a review event, above 44 an incident); two
children a day, at most three; at most 189 executions for the soak, so at most 224 in
the project; no rolling 24 hours above 31 completed executions; about $0.30.

First milestones, to be read afterwards and not waited for:

| UTC | Expected |
|---|---|
| 2026-10-12 00:00 | First tick. Dispatcher only; nothing is due |
| 2026-10-12 03:00 | Monday reconciliation order: 14 targets, 14-day window, 22 reserved, 14 requests expected. Collector starts |
| 2026-10-12 04:00 | Warehouse run, after the collector's receipt |

Work begins two to four minutes after each hour: that is Cloud Run's start-up time,
measured in C2-E, and is not a fault.

**The schedule is not one-shot.** The cron has no year and the same dates are eligible
again from 2027-10-12. The control record's expiry and the post-soak reconciliation
are what bound it to this year.

## Success criteria

The fourteen criteria are in section 31 of `docs/phase4-c2-plan.md`, with the alert
ceiling at 31. They are judged after the window. **C2-F is not yet passed.**

## Stops, if one is needed

- Stop source work at once: disable the control record (one write, on generation
  `1791684706340016`).
- Stop everything: `gcloud scheduler jobs pause ms-dispatch-15m --location=us-central1
  --project=metaspike-prod-kc`.

Neither is needed for the soak to end. The ticks stop by themselves after 23:00 UTC on
18 October.

## After the window

A read-only audit against the criteria; then the reconciliation: `SCHEDULER_PAUSED`
back to `true`, the four soak variables deleted, one plan, review, apply; and the
control record disabled.

## Exact next approval required

None before the first tick. The next checkpoint is whatever observation or audit
ChatGPT instructs, during or after the window.

STOP. Nothing is to be triggered by hand.
