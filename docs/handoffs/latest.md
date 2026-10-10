# Handoff: latest checkpoint

This file is the short-lived handoff from Claude Code to ChatGPT. It is overwritten at
each checkpoint; Git history is the archive. It supports communication only and
replaces no code, test, audit or document.

Previous checkpoint: **PHASE 4 PART C2-F PREP: HARDENING READY — PLAN REVIEW REQUIRED**

Current checkpoint: **PHASE 4 PART C2-F PREP: BLOCKED — APPLY FAILED PART-WAY, 2 OF 3 RESOURCES CREATED**

The approved plan was applied once and the apply **failed after creating two of its
three resources**. Nothing was retried, nothing was deleted, no new plan was made. The
partial state is harmless and fully accounted for below. It needs a ruling before
anything else is done.

This is not "HARDENING APPLIED" and the success block was not printed.

## What happened

| | |
|---|---|
| Saved plan run | `38022130278` |
| Plan SHA-256 | `7d441d9e92d300032b4676daeda297409e50611760cc4b6a9329d25074da4914` |
| Apply run | `38023222777`, 2026-10-10, apply job ended 04:18:29 UTC, **failure** |
| Verification jobs | all three passed (the base image again came from the mirror) |
| Plan hash and destroy checks in the apply job | passed |
| Terraform result | 2 created, then an error; **not** "3 added" |

Order of events in the apply (UTC):

1. 04:18:24 `google_logging_metric.counter["ms_scheduler_attempt_failed"]` created.
2. 04:18:26 `google_monitoring_alert_policy.dispatcher_failed` created.
3. 04:18:27 `google_monitoring_alert_policy.scheduler_delivery_failed` **refused**:
   `Error 404: Cannot find metric(s) that match type =
   "logging.googleapis.com/user/ms_scheduler_attempt_failed". If a metric was created
   recently, it could take up to 10 minutes to become available.`

**Cause.** The alert policy reads the log-based counter created two seconds before it
in the same apply, and Cloud Monitoring had not yet published the counter's
descriptor. Terraform ordered the two correctly; the service needed time. This is the
risk named in the previous handoff ("a new log-based metric can take a short while to
become visible to a new alert policy"). The four older log-metric alerts were created
the same way in C1 and happened not to hit it.

It is a timing failure, not a permission, configuration or design failure. At 04:19
UTC, about forty seconds later, the descriptor was readable from the Monitoring API
(HTTP 200).

## Exact partial state (read-only audit, 04:19 UTC)

| | Before | Now |
|---|---|---|
| Prod Terraform state serial | 11 | **12** |
| Managed resources in state | 91 | **93** |
| Log-based metrics, live | 5 | **6** |
| Alert policies, live | 8 | **9** |

Created and recorded in state, neither tainted:

- log metric `ms_scheduler_attempt_failed`, filter
  `resource.type="cloud_scheduler_job" AND severity>=ERROR`;
- alert policy "MetaSpike: a dispatcher execution failed", enabled.

Not created, absent from state and from the live project:

- alert policy `scheduler_delivery_failed` ("MetaSpike: the scheduler could not start
  the dispatcher"). No orphan or half-made policy exists.

State outputs written: `dispatch_schedule = "0 * * * *"`,
`daily_execution_alert_threshold = 60`.

Terraform state and the live project agree with each other. Neither matches the
configuration yet: one resource is missing.

## Everything else, verified unchanged

- **Scheduler:** PAUSED, `0 * * * *`, `Etc/UTC`.
- **Control record:** disabled, generation `1791578645841911`, unchanged.
- **Monitoring:** `dispatcher_silent` still disabled. The seven other earlier policies
  unchanged and enabled. The audit comparison against the pre-apply snapshot shows one
  difference only: the alert list (the one new policy).
- **Cloud Run:** 34 executions; nothing ran. All seven jobs at generation 6 on
  `sha256:a8680b2be8b5e4bea48392c2b5324b44a680b4c2a4d56e2f49b836bdb979c754`. No job
  definition changed.
- **Security:** 0 secret versions; 0 user-managed keys across 10 service accounts; no
  IAM difference; WIF pool ACTIVE.
- **BigQuery quota:** 10,240 MiB a day.
- **Data:** row counts and object counts identical (1,372 / 28 / 20; 102 and 106
  objects). The 32 pending run-log rows are untouched.
- **Repository variables:** `SCHEDULER_PAUSED` = `true`; no soak variable set.
- **Sources:** Wikimedia 0, Steam 0, IGDB 0.

## Consequences

- **Risk now:** none. One extra, correct, enabled failure alert exists; one unused
  counter exists and costs nothing. With the scheduler paused neither can fire.
- **The saved plan is spent.** It was made against serial 11; state is at 12. It cannot
  be applied again and no attempt was made.
- **Gate 8 (monitoring for a finite soak) is not met.** The scheduler-delivery alert is
  one of the two that stand in for the silence alert during a soak.
- **A finding for later, not acted on:** any fresh environment would meet the same
  race. The repository deliberately has no wait step in Terraform (a test forbids
  one). Whether to add a bounded wait or accept a two-step first apply is a design
  decision that was not taken here.

## Pre-apply check, for the record (04:10 UTC, all met)

`main` level with origin and clean; only `docs/handoffs/latest.md` changed since
`0250e61`; plan run succeeded; artifact unexpired (2026-10-13T03:59:46Z) and hashing
exactly; rendered plan 3 add, 0 change, 0 destroy with exactly the three expected
resources; state serial 11; no apply since the plan; scheduler PAUSED `0 * * * *`
`Etc/UTC`; control disabled, generation unchanged; 34 executions; seven jobs on the
approved digest; 0 secret versions; 0 keys; `SCHEDULER_PAUSED` = `true`; no soak
variable.

## Still proven

- Docker mirror hardening: three runs on the runner have now fetched the pinned base
  from `mirror.gcr.io/library`, verified it and passed the scan.
- Finite scheduler hardening: offline and mocked-provider tests as reported; not yet
  exercised against production, by design.

## What I would do next, if approved

One new infrastructure-only plan from the unchanged code (`reuse_deployed_image=true`).
Expected: **1 to add** (`google_monitoring_alert_policy.scheduler_delivery_failed`),
0 to change, 0 to destroy, no output change. The counter's descriptor is now visible,
so the cause of the failure is gone. That plan would come back for review before any
apply. Nothing of this has been started.

## Exact next approval required

A ruling on the partial state, and approval (or not) to make that one plan.

C2-F has not started. No dates were chosen. STOP.
