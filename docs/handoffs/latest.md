# Handoff: latest checkpoint

This file is the short-lived handoff from Claude Code to ChatGPT. It is overwritten at
each checkpoint; Git history is the archive. It supports communication only and
replaces no code, test, audit or document.

Previous checkpoint: **PHASE 4 PART C2-F: LAUNCH DESIGN READY — EXECUTION APPROVAL REQUIRED**

Current checkpoint: **PHASE 4 PART C2-F: FINAL UNPAUSE PLAN READY — REVIEW REQUIRED**

The catch-up ran and passed, the five repository variables are set, and the finite
unpause plan is made and is exactly the expected diff. **The plan was not applied and
the control record was not written.** In live GCP the scheduler is still PAUSED and
the control record still DISABLED: both barriers are closed while the plan is
reviewed. C2-F has not started.

**Time matters.** The window opens at 2026-10-12T00:00:00Z. This handoff was written
at about 01:30Z on 11 October. The control write and the apply must both be finished
before midnight UTC, or ticks are lost.

## The plan to review

| | |
|---|---|
| Plan run | `38101148562` (commit `fea9429`, `reuse_deployed_image=true`) |
| Plan SHA-256 | `61c335bbd54e7bbed2cba09490315331d0c858f595f079aea28fd75e95659bd1` |
| Artifact expires | 2026-10-14T01:24:20Z |
| Made against | prod state serial 13, 94 managed resources |
| Result | **0 to add, 2 to change, 0 to destroy, 0 to replace** |
| Image | reused: the image all seven jobs already run. Build, smoke, scan and push skipped; nothing pushed |

Exact resource diff, both updated in place:

```
~ google_cloud_scheduler_job.tick                      (name ms-dispatch-15m)
    ~ paused   = true -> false
    ~ schedule = "0 * * * *" -> "0 * 12-18 10 *"
      (7 unchanged attributes hidden, time zone Etc/UTC among them;
       2 unchanged blocks hidden: the retry settings and the target)

~ google_monitoring_alert_policy.execution_volume
    ~ conditions.display_name
        "more than 60 completed executions in 24 hours"
     -> "more than 31 completed executions in 24 hours"
    ~ conditions.condition_threshold.threshold_value = 60 -> 31
      (everything else in the policy unchanged)
```

Exact output diff:

```
~ collection_running              = false -> true
~ daily_execution_alert_threshold = 60 -> 31
~ dispatch_schedule               = "0 * * * *" -> "0 * 12-18 10 *"
+ soak_scheduled_ticks            = 168
```

The rendered plan names these two resources and no other. Not in it:
`dispatcher_silent` (stays disabled), `scheduler_delivery_failed`, `dispatcher_failed`,
the other six policies, any Cloud Run job, any image, IAM, WIF, secrets, buckets,
datasets, service accounts. The BigQuery quota is not in this root. The workflow's own
check on the JSON plan found nothing destroyed or replaced. As before, the JSON plan is
not in the artifact and the binary cannot be decoded locally, so the rendered text and
the hash are the evidence.

## Rulings recorded

In section 30 of `docs/phase4-c2-plan.md`: catch-up on 11 October with the default
load window of 3; `daily` selector for it; request bound 22 per task attempt and 44
with a crash retry, above 22 a review event, above 44 an incident; **volume threshold
31** (25 dispatcher completions plus the children of two adjacent days), expected
maximum about 29; soak ceiling still 189; `armed_until` 2026-10-18T23:30:00Z.

For the success criteria this means: no rolling 24 hours above 31 completed
executions; total executions for the soak at or below 189.

## Preflight (2026-10-11 01:04 UTC), all met

The UTC date was 11 October, confirmed against an external clock. Prod state serial
13, 94 resources. Scheduler PAUSED, `0 * * * *`, `Etc/UTC`. Control disabled,
generation `1791578645841911`. 34 executions. Seven jobs on the approved digest. Ten
policies: `scheduler_delivery_failed` and `dispatcher_failed` enabled,
`dispatcher_silent` disabled, volume threshold 60. 0 secret versions, 0 keys, no IAM
or WIF difference. `SCHEDULER_PAUSED` = `true`, no soak variable. 51 landed batches
against 19 load receipts: exactly the 32 from 9 October pending and nothing else. No
Cloud Run log entry of any kind since the previous checkpoint.

The installed gcloud documents `--update-env-vars` as "environment variables overrides
for an execution of a job"; the persisted job is not changed.

## The catch-up

```
gcloud run jobs execute ms-warehouse --region us-central1 --project metaspike-prod-kc \
    --wait --update-env-vars METASPIKE_DBT_SELECTOR=daily
```

| | |
|---|---|
| Execution | `ms-warehouse-lxqp9` |
| Created / completed | 2026-10-11 01:06:11 / 01:10:47 UTC, 4 m 24 s |
| Result | succeeded on the first attempt, no retry |
| Image | the approved digest |
| Overrides | `METASPIKE_DBT_SELECTOR=daily` only; load window left at its default of 3 |
| Batches loaded | **32**, 32 rows, group `runs` |
| Batches already loaded | 8, recognised and skipped |
| Incomplete batches | 0 |
| dbt | selector `daily`; `PASS=28 WARN=0 ERROR=0 SKIP=0`; 8 incremental models, 1 view, 19 tests |
| BigQuery | 70 jobs (34 loads, 36 queries), 0 errors, 110 MiB billed |
| Rebuild pending | none |

| | Before | After |
|---|---|---|
| Pending 9 October run-log batches | 32 | **0** |
| Load receipts, `ms_ops.collection_runs` | 14 | 46 |
| `ms_ops.collection_runs` rows | 20 | **54** |
| 9 October dispatcher rows in the table | 4 | 36, all 36 that were landed |
| `ms_raw.period_observations` | 1,372 | 1,372 |
| `ms_ops.expected_targets` | 28 | 28 |

**The one difference from the prediction, explained.** The table holds 54 rows, not
52. The 32 pending rows account for 20 → 52. The other two are the rows this warehouse
execution wrote for itself, started and finished, as every warehouse run does (34 load
jobs: 32 batches and those 2). The design's figure left them out; no other batch was
found or loaded. All 54 rows are distinct; all 1,372 observations are distinct and
span the same dates as before.

## Post-catch-up safety state

- Scheduler PAUSED, `0 * * * *`, `Etc/UTC`. Control disabled, generation unchanged.
- Executions **35**: the one catch-up and nothing else. Only `ms-warehouse` ran.
- Job definitions unchanged: all seven at generation 6 on
  `sha256:a8680b2be8b5e4bea48392c2b5324b44a680b4c2a4d56e2f49b836bdb979c754`;
  `ms-warehouse` carries no selector in its stored environment.
- Prod state serial 13. No IAM, schema, secret, alert or scheduler difference. 0 secret
  versions. 0 user-managed keys. Registry 11 images.
- Wikimedia 0, Steam 0, IGDB 0.

## Repository variables, as set and read back

```
SCHEDULER_PAUSED=false
SOAK_MONTH=10
SOAK_START_DAY=12
SOAK_END_DAY=18
SOAK_DAILY_EXECUTION_CEILING=31
```

These change nothing live. They do mean that **any plan made from now on is an unpause
plan**, until they are restored.

## State at the end of this checkpoint (01:25 UTC)

| | |
|---|---|
| Live scheduler | **PAUSED**, `0 * * * *` |
| Control record | **DISABLED** |
| Live volume threshold | 60 |
| Prod state serial | 13 |
| Cloud Run executions | 35 |
| Source calls | 0 |
| Secret versions / keys | 0 / 0 |
| C2-F | NOT STARTED |

Git: this handoff and section 30 of the plan document. No code, Terraform or workflow
change. The portfolio repository was not touched.

## What remains, in order, after approval

1. Write the approved control record, with a precondition on generation
   `1791578645841911`.
2. Apply exactly plan `38101148562`, once.
3. Verify: scheduler ENABLED with `0 * 12-18 10 *` and next run 2026-10-12T00:00:00Z;
   volume threshold 31; `dispatcher_silent` disabled; state serial 14; no execution
   yet; everything else unchanged.

If the apply fails: disable the control record, audit read-only, report; no retry.
If approval cannot come in time to finish before 2026-10-12T00:00Z: the launch should
not be started late; the variables are restored and a new window is ruled on.

## Exact next approval required

Review of plan `38101148562` (SHA-256 above) and approval to write the control record
and apply exactly that plan.

C2-F has not started. STOP.
