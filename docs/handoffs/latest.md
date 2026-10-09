# Handoff: latest checkpoint

This file is the short-lived handoff from Claude Code to ChatGPT. It is overwritten at
each checkpoint; Git history is the archive. It supports communication only and
replaces no code, test, audit or document.

Previous accepted checkpoint: **PHASE 4 PART C2-D: SOAK ACTIVE**

Current checkpoint: **PHASE 4 PART C2-D: CLEANUP AUDITED — REVIEW REQUIRED**

The scheduler is paused and the control record is disabled. The soak did everything it
was meant to do and every source and safety limit held. **One operational criterion
failed: C2-D EXECUTION CEILING BREACHED.** The checkpoint allowed 29 executions in
total and there are 34, because cleanup came about eight hours after the recommended
time and the scheduler kept starting the dispatcher each hour. All five excess
executions are dispatchers that refused with `control_expired`. C2-D is **not** marked
complete; that ruling is for review.

A reconciliation plan is saved and **not applied**.

**Time-sensitive:** the dispatcher-silence alert is still enabled. The last successful
dispatcher ended at 20:02 UTC, so the policy will fire at about **23:02 UTC on
2026-10-09** unless the reconciliation plan is applied before then. It is harmless but
will send an email.

All times are UTC on 2026-10-09.

## Pause and control

- **Pause:** `gcloud scheduler jobs pause ms-dispatch-15m` issued at 20:43:46; the job
  records the change at **20:43:48**. Read back immediately: `PAUSED`. Schedule
  `0 * * * *`, time zone `Etc/UTC`. The last attempt was the 20:00 tick; a paused job
  shows no next run. It was the first action taken, before any audit.
- **Control record before:** generation `1791513212387018`, the armed record exactly as
  written at launch (`enabled: true`, `armed_until` 2026-10-09T08:30:00Z).
- **Expiry proof:** the repository's own reader returns `control_expired` for those
  bytes now, permits work at 08:29:59, and returns `control_expired` at 08:30:00.
- **Control record after:** the same record with only `enabled` set to false, written
  on that exact generation at 20:44. **New generation `1791578645841911`**, SHA-256
  `92460ed1e395d024f6b1c157c2bf55ce084cf8764401901557669aaa98b647e2`, read back byte for
  byte identical.

## Execution timeline

Every Cloud Run execution of the soak. All 20 succeeded on their first attempt, with no
task retry. Every dispatcher was created by `ms-scheduler`; the two children were
created by `ms-dispatch`. Nothing was started by hand. The scheduler made 18 attempts,
one per hour, each answered 200, none retried.

| Tick | Execution | Result | Child started |
|---|---|---|---|
| 03:00 | `ms-dispatch-cfw4f` | 14 planned, one work order | `ms-wikimedia-s9gg8` |
| | `ms-wikimedia-s9gg8` | 14 attempts, 0 retries, 14 collected, 56 rows | |
| 04:00 | `ms-dispatch-22c76` | receipt folded, nothing due, warehouse owed | `ms-warehouse-d726r` |
| | `ms-warehouse-d726r` | 8 batches, 76 rows, dbt PASS=28 | |
| 05:00 | `ms-dispatch-knjhv` | nothing due | none |
| 06:00 | `ms-dispatch-xxzm7` | nothing due | none |
| 07:00 | `ms-dispatch-xjrfl` | nothing due | none |
| 08:00 | `ms-dispatch-q2hjs` | nothing due | none |
| 09:00 | `ms-dispatch-lsgfb` | **refused: `control_expired`** | none |
| 10:00 | `ms-dispatch-cmqqv` | refused: `control_expired` | none |
| 11:00 | `ms-dispatch-8sns5` | refused: `control_expired` | none |
| 12:00 | `ms-dispatch-zwjgx` | refused: `control_expired` | none |
| 13:00 | `ms-dispatch-dwkz5` | refused: `control_expired` | none |
| 14:00 | `ms-dispatch-cjtjk` | refused: `control_expired` | none |
| 15:00 | `ms-dispatch-mfkhn` | refused: `control_expired` | none |
| 16:00 | `ms-dispatch-f7b7m` | refused: `control_expired` | none |
| 17:00 | `ms-dispatch-vrgkt` | refused: `control_expired` | none |
| 18:00 | `ms-dispatch-f7qm6` | refused: `control_expired` | none |
| 19:00 | `ms-dispatch-hklbs` | refused: `control_expired` | none |
| 20:00 | `ms-dispatch-5ql4q` | refused: `control_expired` | none |

Each dispatcher took between about one and four and a half minutes from creation to
completion, almost all of it before its first log line, as in earlier runs.

## Execution ceiling: breached

| | |
|---|---|
| Baseline before C2-D | 14 |
| New in C2-D | 20 (18 dispatchers, 1 collector, 1 warehouse) |
| **Absolute total** | **34** |
| Ceiling | 29 |
| **Exceeded by** | **5** |

The 15th new execution was the 15:00 dispatcher, which brought the total to exactly 29.
The five beyond it are the dispatchers of **16:00, 17:00, 18:00, 19:00 and 20:00**.

Classification, kept apart from the acceptance result:

- **A. Extra fail-closed dispatcher executions after expiry: yes, all five.** Each
  logged `tick_refused` with reason `control_expired` and exited.
- **B. Extra child or source executions: none.** There is exactly one collector and one
  warehouse execution in the whole soak, both before 05:00.

What the twelve refused dispatchers (09:00 to 20:00) did and did not do:

- no child job started;
- no source request: the dispatcher holds no source client, and no collector ran;
- no work order: the order area still holds the two orders from 8 and 9 October;
- no tick plan and no lease after 08:00: a refused tick writes neither;
- no expected-target ledger row: the only rows for the day are the 14 from 03:00;
- no movement in the day's counters: still 22 attempts reserved and 2 child executions.

One thing they **did** write, and it should not be called "no data change": every
dispatcher run, refused or not, lands its own two run-log batches (started, finished).
That is operational record, not source data. It is what makes the refusals auditable.

**Safety impact: none.** The control expiry was the barrier, and it held for twelve
consecutive ticks with nobody present. **Operational criterion: failed**, by five
executions, entirely through late cleanup. The scheduler has no stop of its own.

## The two working ticks

**03:00.** Tick `2026-10-09T03:00:00Z`; schedule `s9939a2accd9a17c152bc` reused; 1
partition read; 14 targets planned; work order `88c3319704fe9da52b41d759f1c38e3d` (14
targets, `max_requests` 22, slot 2026-10-07, 4-day window). The collector sent **14
physical outbound attempts of a budget of 22, with 0 retries**, collected 14 of 14,
failed 0, deferred 0 by the budget, filled no history, and landed 56 rows. Receipt
`succeeded`.

**04:00.** The dispatcher folded that receipt (health: last success 03:04:38, breaker
closed, nothing pending), planned nothing, issued no order, and started exactly
`ms-warehouse`. Child executions counted for the day: 2 of the 3 allowed.

**Warehouse `ms-warehouse-d726r`.** 8 batches loaded and 11 already loaded, 0
incomplete, 76 rows (56 observations, 14 ledger rows, 6 run-log rows). Selector `daily`.
No late data (`rebuilt_from` empty, no `late_data_loaded` event, pending-rebuild record
empty), no full refresh. dbt 1.11.15: **PASS=28, WARN=0, ERROR=0, SKIP=0**. Each new batch has a load receipt (19 receipts in all, 8 from this run), so
each was loaded exactly once.

**05:00 to 08:00.** Four dispatchers, each with nothing due, no order and no child.

## Source and request budget

| | Whole UTC day |
|---|---|
| Wikimedia physical outbound attempts | **14** (limit 22) |
| Retries | 0 |
| Targets attempted / collected | 14 / 14 |
| Source errors, 429s | 0, 0 |
| Request-budget exhaustion | none |
| Wikimedia calls after the 03:00 collection | 0 (no collector ran again) |
| Steam, IGDB, catalogue, sampled-source calls | 0 (those jobs did not run) |
| Secret Manager accesses | 0 |

The runtime audit log for the day shows BigQuery calls by `ms-warehouse` only. There is
no packet-level egress logging; the source figures rest on the collector's own count of
transport sends and on which jobs ran.

## Data

| | Before | After | Change |
|---|---|---|---|
| `ms_raw.period_observations` | 1,316 | 1,372 | +56 |
| `ms_staging.stg_period_observations` | 1,316 | 1,372 | +56 |
| `ms_ops.expected_targets` | 14 | 28 | +14 |
| `ms_ops.collection_runs` | 12 | 20 | +8 |

- The new batch is 14 articles by 4 days, 2026-10-04 to 2026-10-07: no 90-day refetch.
  The earlier two batches are intact.
- Period range 2026-07-08 to 2026-10-07; 1,190 rows before 1 October: no truncation.
- No exact duplicate keys; no duplicate within a run; no negative, null or future
  values. 56 periods now have more than one version (the overlap, by design) and none
  was revised.
- The ledger rows for the day all belong to the 03:00 tick, disposition `planned`,
  carrying the day's order id.
- **Not loaded:** the run logs of the sixteen dispatchers from 05:00 to 20:00 (32 rows)
  are landed and waiting. The next warehouse run loads them if it happens within three
  days; later than that needs the job's existing wider load window.
- Dispatcher state: backlog empty; the order pointer names the 03:00 order, whose
  receipt exists; no pending receipt; no warehouse run owed.

BigQuery for the day, all from the one warehouse run: 70 jobs (46 top level), 247,666
bytes processed, **230,686,720 bytes (about 220 MiB) billed**, 59,775 slot-ms, no
error. Against the 10,240 MiB daily limit that is about 2 percent.

Storage now: landing 102 objects (637,261 bytes); operations 106 objects (70,269 bytes).

## Alerts

- **Dispatcher silence:** opened 02:44:29, the moment the apply enabled it, and
  **closed by itself at 03:07:05** after the first dispatcher succeeded. Solely the
  start-up artifact.
- No other incident during the soak: no job-failure, warehouse-failure, rate-limit,
  execution-volume, storage or parity incident. (The only other incident on record is
  the S4 synthetic one from 7 October, long closed.)
- Completed executions for the day by the monitoring metric: 22 at the time of reading,
  against that policy's threshold of 60.
- The only WARNING entries from any job are the twelve `tick_refused` lines. No ERROR.
- All 8 policies are enabled, `dispatcher_silent` included. It was not altered by hand
  and will fire at about 23:02 UTC unless reconciled first (top of this file).

## Security and infrastructure

- All seven jobs at generation 6 on
  `sha256:a8680b2be8b5e4bea48392c2b5324b44a680b4c2a4d56e2f49b836bdb979c754`, every
  `METASPIKE_IMAGE` agreeing (checked with the resolving script).
- 0 secret versions; 0 user-managed keys; WIF provider ACTIVE and unchanged.
- No difference in IAM, dataset access, bucket IAM or secrets against the pre-soak
  snapshot; the differences are the alert policy's enabled flag and the row metadata of
  the three tables that received data.
- BigQuery project daily query limit 10,240 MiB; per-user limit untouched.
- Scheduler cron `0 * * * *`, time zone `Etc/UTC`.
- Prod state serial 10. Repository variable `SCHEDULER_PAUSED` = `true`, as it has been
  since the plan was made.

## Reconciliation plan (NOT APPLIED)

| | |
|---|---|
| Run | `37989297604`, `operation = plan`, `reuse_deployed_image = true` |
| Head commit | `a4e9e99c8392d1d30af4e81d0e8265f9b009ade2` |
| Image planned with | `sha256:a8680b2b…c754`, resolved from the jobs; nothing built or pushed |
| Plan SHA-256 | `74b80a4212f4fa04c15a310b4d4c5ef421b762386f3738dda30e80673ffe1de6` |
| Artifact | `tfplan`, expires 2026-10-12T20:56:59Z |
| Counts | **0 add, 1 change, 0 destroy, 0 replace** |

```
# google_monitoring_alert_policy.dispatcher_silent will be updated in-place
  ~ enabled = true -> false
    (8 unchanged attributes and 1 unchanged block)

Plan: 0 to add, 1 to change, 0 to destroy.
```

Terraform refreshed all 91 resources, saw the scheduler already paused, and proposes no
scheduler change. No image, job, IAM, WIF, quota, secret, dataset, table, bucket, cron,
time-zone, identity, retry or other alert change. After the plan: scheduler PAUSED,
state serial still 10, 34 executions, control disabled.

## Recommendation (the ruling is ChatGPT's)

Accept the soak's **safety and function**: collection, warehouse, quiet ticks, expiry
and twelve unattended refusals all behaved as designed, inside every source and child
limit. Record the **execution ceiling as failed**, by five fail-closed dispatchers, with
cause "cleanup performed about eight hours late". Before any longer soak, close the gap
it exposes: nothing stops the scheduler when a soak's control record expires. Options
are an operator alarm tied to `armed_until`, or having the dispatcher pause its own
trigger on expiry (which would need a narrow new permission and its own review).

## Warnings

1. **The silence alert will fire at about 23:02 UTC** unless the reconciliation plan is
   applied first.
2. **32 run-log rows are landed and unloaded**, with a three-day routine load window.
3. The reconciliation plan is valid only while prod state stays at serial 10.
4. Each refused dispatcher still costs an execution and two small landed objects.
5. Start-up delay per execution remains one to four minutes, unexplained in billing
   terms; the cost model is for C2-E.
6. Still open from earlier: the deferred freshness alert; the missed-tick review before
   any sampled source; the branch-protection decision before any secret-backed source;
   local Terraform one patch behind CI.

## Exact next approval required

1. A ruling on C2-D: accepted, accepted with the ceiling recorded as failed, or failed.
2. Approval to apply the reconciliation plan by run id and hash: run `37989297604`,
   SHA-256 `74b80a4212f4fa04c15a310b4d4c5ef421b762386f3738dda30e80673ffe1de6`, expected
   0 add, 1 change, 0 destroy.

STOP. The reconciliation plan is not applied. C2-E has not begun.
