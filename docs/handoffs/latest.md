# Handoff: latest checkpoint

This file is the short-lived handoff from Claude Code to ChatGPT. It is overwritten at
each checkpoint; Git history is the archive. It supports communication only and
replaces no code, test, audit or document.

Previous checkpoint: **PHASE 4 PART C2-F PREP: BLOCKED — APPLY FAILED PART-WAY, 2 OF 3 RESOURCES CREATED**

Current checkpoint: **PHASE 4 PART C2-F PREP: PARTIAL STATE RECONCILED IN PLAN — REVIEW REQUIRED**

One new infrastructure-only plan was made against the current state. It adds exactly
the one missing alert policy and nothing else. **It was not applied.** No code,
Terraform or workflow changed. The scheduler stayed paused, the control record
disabled, and no source was called. C2-F has not started.

## The plan to review

| | |
|---|---|
| Plan run | `38024751036` (commit `468e8f7`, `reuse_deployed_image=true`) |
| Plan SHA-256 | `f476e8250004da1902a4a517b657cc9d34acb119fc8cb8087c9ba1bb31eaa56d` |
| Artifact expires | 2026-10-13T04:46:18Z |
| Made against | prod state serial 12 |
| Result | **1 to add, 0 to change, 0 to destroy, 0 to replace** |

The one addition, in full:

```
+ google_monitoring_alert_policy.scheduler_delivery_failed
    display_name          = "MetaSpike: the scheduler could not start the dispatcher"
    enabled               = true
    combiner              = "OR"
    notification_channels = [ the existing reviewed channel ]
    conditions
      display_name = "any failed scheduler attempt in an hour"
      condition_threshold
        filter          = resource.type="cloud_scheduler_job" AND
                          metric.type="logging.googleapis.com/user/ms_scheduler_attempt_failed"
        comparison      = COMPARISON_GT
        threshold_value = 0
        duration        = "0s"
        aggregations    = alignment_period "3600s", per_series_aligner ALIGN_SUM
```

Confirmed from the rendered plan, which lists one resource and no other:

- no Scheduler change;
- no Cloud Run job change, and no image change (the image was reused: build, smoke,
  scan and push were skipped, and nothing was pushed to the registry, still 11 images);
- no IAM change;
- no secret change;
- no quota change (the quota is not in this Terraform root);
- no change to any existing alert policy;
- no logging-metric change;
- no "Changes to Outputs" section at all, so no drift shows there.

The workflow's own check on the JSON plan found nothing destroyed or replaced. The
JSON plan is not kept in the artifact (only the binary plan and its hash are), and the
binary cannot be decoded locally because local Terraform is one patch behind CI, so
the JSON was not read independently; the rendered text and the hash are the evidence.

## Partial-apply history

1. Plan `38022130278` (SHA `7d441d9e…a4914`): 3 to add. Approved.
2. Apply `38023222777`, 04:18 UTC on 2026-10-10: created the counter
   `ms_scheduler_attempt_failed` and the policy `dispatcher_failed`, then Cloud
   Monitoring refused `scheduler_delivery_failed` with a 404 because the counter's
   descriptor was not yet published. State went from serial 11 to 12. Nothing was
   retried or deleted.
3. Ruling: partial state accepted as safe; that plan is spent and must not be reused.
4. This checkpoint: plan `38024751036` for the one remaining resource.

## Precheck (04:37 UTC), all met

- Prod state serial **12**, 93 managed resources. State and live agree: the same six
  log metrics and the same nine alert policies in both.
- `ms_scheduler_attempt_failed` exists and is healthy: filter
  `resource.type="cloud_scheduler_job" AND severity>=ERROR`, DELTA INT64, not disabled.
- `dispatcher_failed` exists and is enabled.
- `scheduler_delivery_failed` is absent, from state and from the project.
- `dispatcher_silent` remains disabled; the other seven policies enabled as before.
- **The counter's descriptor is readable from the Monitoring API (HTTP 200)**, so the
  condition that caused the failure has cleared.
- Scheduler PAUSED, `0 * * * *`, `Etc/UTC`.
- Control disabled, generation `1791578645841911`, unchanged.
- Executions 34. All seven jobs at generation 6 on `sha256:a8680b2b…c754`.
- 0 secret versions; 0 user-managed keys across 10 service accounts; no IAM, schema,
  secret, alert or scheduler difference from the audit taken after the partial apply;
  WIF pool ACTIVE.
- BigQuery quota 10,240 MiB a day.
- Repository variables: `SCHEDULER_PAUSED` = `true`; no soak variable set.
- Source calls since the partial apply: 0. No Cloud Run log entry of any kind since
  04:10 UTC.

After planning the state was read again: serial 12, 93 resources, 9 policies, scheduler
paused, 34 executions. Planning changed nothing.

## The long-term finding

Recorded in section 28 of `docs/phase4-c2-plan.md`, not fixed. A fresh environment can
race when a new log-based metric and an alert policy that reads it are created in one
apply. Here the descriptor has propagated, so the reconciliation is a one-resource
plan. No wait step was added and the dependency design was not changed.

## State

| | |
|---|---|
| Prod state serial | 12 |
| Scheduler | PAUSED |
| Control record | DISABLED |
| Executions | 34 |
| Source calls | 0 |
| Alert policies live | 9 (10 after this plan) |
| Deployed image | `sha256:a8680b2b…c754`, all seven jobs |
| Secret versions / keys | 0 / 0 |
| 32 pending run-log rows | untouched |

Git: this handoff and one paragraph in `docs/phase4-c2-plan.md`. No code, Terraform or
workflow change since `0250e61`. The portfolio repository was not touched.

## Exact next approval required

Review of plan `38024751036` (SHA-256 above) and approval to apply exactly that plan.
Once it is applied and verified, the remaining C2-F launch items are: soak dates, the
final control record, the final daily execution ceiling, the supervised warehouse
catch-up for the 32 pending rows, and the reviewed finite unpause plan.

C2-F has not started. No dates were chosen. STOP.
