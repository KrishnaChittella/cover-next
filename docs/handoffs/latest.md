# Handoff: latest checkpoint

This file is the short-lived handoff from Claude Code to ChatGPT. It is overwritten at
each checkpoint; Git history is the archive. It supports communication only and
replaces no code, test, audit or document.

Previous checkpoint: **PHASE 4 PART C2-F PREP: HARDENING COMPLETE — LAUNCH DESIGN REQUIRED**

Current checkpoint: **PHASE 4 PART C2-F: LAUNCH DESIGN READY — EXECUTION APPROVAL REQUIRED**

Design and review only. Nothing was executed or changed: no warehouse run, no control
write, no repository variable, no Terraform apply, no source call, no unpause. The
design is complete, but **three of the instruction's assumptions did not survive a
reading of the code and need a ruling before execution.** They are first.

## Three points that need a ruling

### 1. The launch must finish before the window opens, so the catch-up is on 11 October

The window's first tick is 2026-10-12T00:00:00Z. A paused scheduler does not fire, and
Cloud Scheduler does not make up missed runs when resumed. A catch-up "on 12 October
UTC, before the unpause" therefore cannot give 168 ticks: every hour that passes on
the 12th before the apply is a tick lost, and if 03:00 is lost so is that day's
collection, which is the Monday reconciliation.

Consistent design: **everything up to and including the unpause apply happens before
2026-10-12T00:00Z.** The unpaused finite scheduler then simply waits for midnight. The
catch-up is therefore on **11 October UTC**, where
`METASPIKE_LOAD_DAYS = (11 − 9) + 1 = 3`, which is the job's default. **No load-window
override is needed at all.** (4 would be right on the 12th; that case is the one being
avoided. On 10 October it would be 2.)

If the launch cannot be finished by about 22:00Z on the 11th, the choice is a ruling,
not a default: move the window (for example 13 to 19 October, `0 * 13-19 10 *`, still
168 ticks and still containing a Monday), or accept fewer than 168 ticks.

### 2. A day's absolute request ceiling is 22 per task attempt, not 22 flat

The order's budget of 22 is enforced inside the collector process. `ms-wikimedia` has
`max_retries = 1`. If a collector task crashes before writing its receipt (an uncaught
error, a timeout, memory), Cloud Run starts it once more; the second attempt claims
the same uncompleted order and gets a fresh budget of 22. Finished targets are skipped
by their markers, so the realistic extra is small, but the strict bound is
**2 × 22 = 44 physical requests in a day**, not 22. A source failure does not cause
this: it ends in a receipt and a normal exit. It needs a crash, which happened once in
C1 (`ms-wikimedia-mr9kc`, 7 October).

Options, none taken: accept 44 as the documented absolute bound for the soak (expected
remains 14); or set that job's retries to 0, a job change that would add a line to the
unpause plan and cost the retry's resilience. I recommend accepting 44 for C2-F and
treating any day above 22 as a review event.

### 3. The daily alert threshold: 30 is right by count, 31 is the strict bound by time

By the code, a UTC day holds at most 24 dispatchers and 3 children (the counter is
keyed by the tick's UTC date). A rolling 24 hours can straddle two UTC days and so
hold the children of both: 3 late on one day and 3 early on the next. Hence the factor
of two: 24 + 2 × 3 = 30.

One refinement: the alert counts **completed** executions, and a dispatcher completes
77 to 265 s after its tick. A 24-hour window can therefore catch 25 dispatcher
completions, making the strict bound 25 + 6 = 31. The expected soak has 2 children a
day (collector at 03:00, warehouse at 04:00), so its rolling maximum is 25 + 4 = 29.

Recommendation: **keep 30.** It fires above 30, so never in the expected soak (29) and
only in a case that deserves a look anyway (the child cap fully used on two adjacent
days). An alert is a review event, not a stop.

## Window

| | |
|---|---|
| Dates | 2026-10-12 to 2026-10-18 UTC inclusive (Monday to Sunday) |
| Cron | `0 * 12-18 10 *`, `Etc/UTC` |
| First tick | 2026-10-12T00:00:00Z |
| Last tick | 2026-10-18T23:00:00Z |
| Not a tick | 2026-10-11T23:00:00Z, 2026-10-19T00:00:00Z |
| Ticks in 2026 | exactly 168 |

Verified by running the repository's schedule logic for this window, not by
arithmetic alone; its 23 tests pass. **Recurrence:** the cron has no year and is
eligible again from 2027-10-12T00:00Z. It is not a one-shot schedule. `armed_until`
and the post-soak reconciliation are what bound it to 2026.

## Control record (proposed, not written)

```json
{"version":1,"enabled":true,"armed_until":"2026-10-18T23:30:00Z","max_job_starts_per_tick":1,"max_executions_per_day":3,"sources":{"wikimedia_pageviews":{"enabled":true,"max_requests_per_day":22,"max_targets_per_execution":14}},"pins":[]}
```

The C2-D posture; the code requires nothing different. No Steam, no IGDB, no sampled
source, no pins; tiering stays shadow only.

**`armed_until` = 2026-10-18T23:30:00Z.** The dispatcher compares it with the real
time at which it runs, not with the tick. The latest any execution has reached its
first line is 259 s after creation, and Scheduler delivery has been at most 52 s late,
so the 23:00 tick is evaluated by about 23:05; 30 minutes is roughly six times that,
the same margin as C2-D. It carries the year, it ends inside the window's last hour so
no authorisation outlives the schedule, and it is independent of the cron: if the
schedule were wrong, every tick after 23:30 would refuse.

It is written with a generation precondition on the current object
(`1791578645841911`), so a concurrent change makes the write fail.

## Execution ceilings

- Dispatchers: 168, one per tick, fixed by the cron.
- Children: at most `max_executions_per_day` per UTC day, enforced by the tick against
  the day's counter, and at most one start per tick. 7 × 3 = 21.
- **Total ceiling for the soak: 189.** Expected: 168 + 14 = 182.
- Plus one supervised catch-up before the window. Project total: 34 now, 35 after the
  catch-up, **at most 224** at the end, expected 217.
- Rolling 24-hour alert threshold: **30** (point 3).

## Wikimedia over the seven days

The 14 articles form one cohort, due at the 03:00 UTC tick. Each day:

| UTC day | Tick | Window | Targets | Reserved | Expected requests | Expected rows |
|---|---|---|---|---|---|---|
| Mon 12 | 03:00 | 14 days (weekly reconciliation) | 14 | 22 | 14 | 196 |
| Tue 13 to Sun 18 | 03:00 | 4 days | 14 | 22 | 14 | 56 |

- One request per article whatever the window; the window only sets the date range.
- The order's budget is `min(1000, int(14 × 1.5) + 1, what is left of the day)` = 22,
  and the whole 22 is reserved against the day's limit of 22 when the order is
  written. A second order the same day therefore finds nothing left and is deferred:
  **no faster cadence is possible.**
- **No catch-up storm.** Nothing is made up for the days the scheduler was paused. The
  Monday window of 14 days happens to cover them in the one ordinary order.
- The warehouse follows at 04:00, once a day.

| | Per UTC day | Seven days |
|---|---|---|
| Reserved budget | 22 | 154 |
| Expected, no retries | 14 | 98 |
| Absolute, one task attempt | 22 | 154 |
| Absolute, with a Cloud Run task retry (point 2) | 44 | 308 |

The configured ceiling of 10,000 a day is never the binding one.

## Warehouse catch-up (designed, not run)

Exactly one supervised execution, on 11 October UTC, before anything else in the
launch:

```
gcloud run jobs execute ms-warehouse --region us-central1 --project metaspike-prod-kc \
    --wait --update-env-vars METASPIKE_DBT_SELECTOR=daily
```

- `--update-env-vars` is a supported flag of `gcloud run jobs execute` (checked in the
  installed gcloud) and applies to that execution only; the job definition is not
  changed. `METASPIKE_DBT_SELECTOR` and `METASPIKE_LOAD_DAYS` are both read by the job.
- **Load window:** the default of 3 reaches the 9 October folder on the 11th. If it
  were run on the 12th, add `METASPIKE_LOAD_DAYS=4`.
- **Selector, a choice to confirm:** left alone, the job would pick the `hourly`
  selection, because only run-log rows are new, and that selection has never run
  against BigQuery by itself. Naming `daily` uses the path proven three times
  (PASS=28). Without the override the catch-up is also a first.
- The ordinary idempotent loader loads the 32 pending rows (32 batches of one row) and
  anything else legitimately unloaded in the window; loaded batches are recognised and
  skipped. Nothing is rewritten or deleted.
- **Verify:** execution succeeded; `batches_loaded` 32, `incomplete_batches` 0; dbt
  PASS, 0 errors; `ms_ops.collection_runs` 20 → 52; a load receipt for every 9 October
  batch, so none pending; observations and ledger unchanged (1,372 and 28).

## Cost, one-minute billing model

| Item | Volume | List price |
|---|---|---|
| Dispatcher | 168 × 60 s = 10,080 vCPU-s | $0.18 |
| Collector | 7 × 60 s | $0.01 |
| Warehouse, 7 and the catch-up | 8 × 120 s | $0.02 |
| Memory | about 6,200 GiB-s | $0.01 |
| BigQuery | 8 builds × 220 MiB = 1.7 GiB | $0.01 |
| Scheduler | 1 job, 7 days | $0.02 |
| Registry | 7 days of at most 2.1 GB | $0.04 |
| Storage, logging | megabytes | under $0.01 |
| **Total** | | **about $0.30** |

At the ceiling of 21 children, all as long as a warehouse run, about $0.33. With
allowances, about $0.04. Comfortably under $1 and every boundary. BigQuery is about
0.25 GiB a day against a 10 GiB quota.

## Launch order (designed, not performed)

All of A to H before 2026-10-12T00:00Z.

- **A.** Read-only preflight: state serial 13, scheduler paused, control disabled, 34
  executions, approved image, 0 secrets and keys, 10 policies, no soak variable.
- **B.** The one warehouse catch-up.
- **C.** Verify it. A failure stops the launch here with everything at rest.
- **E.** Set the five repository variables.
- **F.** One infrastructure-only plan.
- **G.** STOP for review of the plan.
- **D.** After approval: write the control record.
- **H.** Apply the reviewed plan, once.
- **I.** Verify: scheduler ENABLED with `0 * 12-18 10 *`, next run 2026-10-12T00:00Z,
  volume alert at 30, silence alert disabled, no execution yet.

**D moves after G.** Writing the control record before the plan is reviewed would arm
it for the whole review with nothing to gain. Written just before the apply, it is
armed only when the launch is actually going ahead.

At every point collection is stopped unless both barriers are open:

| If it fails at | State |
|---|---|
| B or C | Paused, disabled. Nothing to undo |
| E or F | Paused, disabled. Restore the variables |
| D | Paused. Control unchanged or disabled |
| H, wholly or partly | Control armed, scheduler paused or unpaused with a finite cron. Disable the control record (one write), audit, report. No retry |
| After H | Ticks only inside the window; the control record still gates each one |

The one-action stop stays `gcloud scheduler jobs pause ms-dispatch-15m`, and disabling
the control record stops source work without it.

## Stop and incident rules

| Condition | Fails closed by itself | Then |
|---|---|---|
| Scheduler execution outside the window | Yes: control expired, tick refuses | Incident. Pause, audit |
| Control refusal inside the window | Yes, nothing runs | Review at once: the control record is wrong or was changed |
| Rolling 24 h executions above 30 | No; alert only. Children are capped at 3 a day in code | Review; stop if unexplained |
| A day's requests above 22 | The process cannot exceed 22; a task retry can reach 44 | Review; above 44 is an incident and a stop |
| Source 401 or 403 | Collector halts; breaker counts it | Stop the soak; policy question |
| Repeated 429 | Breaker opens on the first, doubling cool-down | Alert at three in an hour. Review; stop if it recurs after a cool-down |
| Malformed or invalid work order | Collector refuses, asks nothing | Review |
| Unexpected duplicate collection | Markers skip finished targets; no second order in a day | Incident if rows are duplicated |
| Warehouse failure | Alert; data stays landed, the loader is idempotent | Review; next run retries |
| dbt failure | Same alert | Review before the next build |
| BigQuery quota risk | The 10 GiB quota refuses further queries | Review above 2 GiB in a day |
| Any Steam, IGDB or other source call | Not cleared, no credential, never planned | Incident. Stop immediately |
| Any secret access | None exists to read | Incident. Stop immediately |
| IAM, image or job drift | No | Incident. Stop immediately |
| A failed dispatcher or scheduler attempt | Alert | Review; one is tolerable if explained, a second stops |

Automatic: the control record, the cron window, the day's execution and request
counters, the breaker, work-order validation, markers, the quota. Human review: the
alerts, a daily read-only audit, and the audit at the end. No new runtime mechanism.

## Success criteria (to be judged afterwards, not now)

1. 168 dispatcher executions from 168 ticks, or each missing one explained by a
   documented platform failure.
2. Zero scheduler attempts and zero executions after 2026-10-18T23:00Z's tick.
3. No dispatcher wake-up outside the window; no refusal inside it.
4. Wikimedia only: no other source contacted, planned or recorded.
5. Requests: 14 a day expected, never above 22 in a task attempt, 98 expected in all.
6. Seven collections, one a day, each 14 of 14; no 401, 403 or 429.
7. Monday 12 October used the 14-day window and the other six days the 4-day window.
8. Seven warehouse runs, each loading only new batches; dbt passing every time.
9. No duplicate logical rows; every day from the window's coverage present for all 14
   articles; counts reconcile with the landed batches.
10. Monitoring: no silence incident; no volume alert; any failure alert explained.
11. Executions at or below 189 for the soak and 30 in any rolling day.
12. Cost within about $0.30 at list price; BigQuery under 2 GiB a day.
13. Security unchanged: 0 secret versions, 0 keys, IAM, WIF, image and jobs as now.
14. Post-soak reconciliation returns the scheduler to paused `0 * * * *` with no
    window, and the control record is disabled.

## Unpause plan inputs (intended, not set)

```
SCHEDULER_PAUSED=false
SOAK_MONTH=10
SOAK_START_DAY=12
SOAK_END_DAY=18
SOAK_DAILY_EXECUTION_CEILING=30
```

Expected plan: **0 to add, 2 to change, 0 to destroy.**

```
~ google_cloud_scheduler_job.tick
    paused   = true -> false
    schedule = "0 * * * *" -> "0 * 12-18 10 *"

~ google_monitoring_alert_policy.execution_volume
    conditions.display_name = "more than 60 completed executions in 24 hours"
                           -> "more than 30 completed executions in 24 hours"
    conditions.condition_threshold.threshold_value = 60 -> 30

Changes to Outputs:
  ~ collection_running              = false -> true
  ~ daily_execution_alert_threshold = 60 -> 30
  ~ dispatch_schedule               = "0 * * * *" -> "0 * 12-18 10 *"
  + soak_scheduled_ticks            = 168
```

Not expected to change: `dispatcher_silent` (stays disabled in a finite soak), the
other eight policies, every job, the image, IAM, secrets, buckets, datasets. Anything
beyond the above is a blocker.

## State, read at 05:28 UTC on 2026-10-10

Scheduler PAUSED, `0 * * * *`, `Etc/UTC`. Control disabled. Executions 34. Prod state
serial 13. `SCHEDULER_PAUSED` = `true`, no soak variable. Source calls 0. The 32
pending rows untouched. The portfolio repository was not touched.

## Exact next approval required

Rulings on the three points at the top (launch on the 11th with the default load
window; the 44-request bound; threshold 30), on the catch-up's `daily` selector, and
then approval to execute steps A to G.

C2-F has not started. STOP.
