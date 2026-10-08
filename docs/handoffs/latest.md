# Handoff: latest checkpoint

This file is the short-lived handoff from Claude Code to ChatGPT. It is overwritten at
each checkpoint; Git history is the archive. It supports communication only and
replaces no code, test, audit or document.

Previous accepted checkpoint: **PHASE 4 PART C2-C.1: COMPLETE**

Current checkpoint: **PHASE 4 PART C2-D PREP: BLOCKED**

The quota is set and verified, the control template is validated, and the re-pause
design is corrected. The block is the unpause plan: the plan workflow produced
**9 changes, not 1**, so no approvable scheduler-only plan exists. Nothing was applied.
The scheduler is paused, the repository variable is back to `true`, the control record
is disabled, and no job ran.

## Why it is blocked

The plan (run `37738729342`) proposes 0 add, **9 change**, 0 destroy:

1. **Seven Cloud Run jobs**, image `sha256:a8680b2b…c754` →
   `sha256:e2d3575550ea31f701bacd91b433574dc1b9c673543139da0081869dbe26a216`. The plan
   workflow always builds with `--pull --no-cache` and plans with the digest it just
   built. The build is not byte-reproducible, so a new digest appears even though the
   application source is unchanged (HEAD differs from the deployed source only in
   documents). This is the image-digest gate; it failed as the instruction foresaw.
2. **`google_cloud_scheduler_job.tick`**: `paused: true -> false`. The intended change.
3. **`google_monitoring_alert_policy.dispatcher_silent`**: `enabled: false -> true`.
   Not a defect: the policy is defined as enabled exactly while the scheduler is not
   paused (accepted in C2-B, asserted by a Terraform test). But it means a correct
   unpause plan has **two** changes, not one, and the instruction's "exactly 1 change,
   no monitoring change" cannot be met as written.

The plan must not be applied and is left to expire (2026-10-11T06:55:00Z). Its hash,
for the record only: `2a93e8cc0043b424c30341b69c0c49be77c3131b26394900cfd6d26981c68491`.

## 1. Baseline (before any change)

All matched: seven jobs on `sha256:a8680b2b…c754`; scheduler PAUSED, `0 * * * *`, no
attempt; control present and disabled; 14 executions; query quota at the default
209,715,200 MiB with no override; 0 secret versions; 0 user-managed keys; 1,316
observations, 14 ledger rows, 12 run rows; no IAM, schema, alert or scheduler drift;
prod state serial 9; repository variable `SCHEDULER_PAUSED` = `true`.

## 2. BigQuery daily query quota: set and verified

- **Before:** `bigquery.googleapis.com/quota/query/usage`, limit `1/d/{project}`,
  effective and default 209,715,200 MiB; no override on any of the 206 BigQuery quota
  buckets.
- **Operation:** the `gcloud alpha` component is not installed here, so the documented
  equivalent was used with the owner's token:
  `POST …/v1beta1/projects/818314526181/services/bigquery.googleapis.com/consumerQuotaMetrics/bigquery.googleapis.com%2Fquota%2Fquery%2Fusage/limits/%2Fd%2Fproject/consumerOverrides?force=true`
  with body `{"overrideValue":"10240"}`. The operation completed.
- **Override identity:**
  `projects/818314526181/services/bigquery.googleapis.com/consumerQuotaMetrics/bigquery.googleapis.com%2Fquota%2Fquery%2Fusage/limits/%2Fd%2Fproject/consumerOverrides/Cg1RdW90YU92ZXJyaWRl`
- **Read back independently:** effective limit on `1/d/{project}` is **10,240 MiB**,
  override value 10240. `1/d/{project}/{user}` is unchanged and unlimited. A diff of
  all 206 buckets before and after shows that one bucket as the only change.
- Re-read at the end of the checkpoint: still 10,240.

## 3. Rollback for the quota

`DELETE` on the override's name above, with `?force=true`, by the owner; or
`gcloud alpha services quota delete --service=bigquery.googleapis.com
--consumer=projects/metaspike-prod-kc --metric=bigquery.googleapis.com/quota/query/usage
--unit='1/d/{project}' --force`. The limit then returns to the default.

Note: the cap now also applies to the owner's own queries in this project. Measured
need is about 220 MiB for a daily build.

## 4. Control template (validated, not written)

Built with the repository's `ControlRecord` model. Only `armed_until` is filled in at
launch.

```
{"version":1,"enabled":true,"armed_until":"<set at launch>","max_job_starts_per_tick":1,"max_executions_per_day":3,"sources":{"wikimedia_pageviews":{"enabled":true,"max_requests_per_day":22,"max_targets_per_execution":14}},"pins":[]}
```

Checked offline with an example expiry of 08:30 UTC: it permits work at 02:45, 03:00
and 08:00, and is refused as `control_expired` at 08:30 and 09:00. Under it the
dispatcher would issue one order for 14 targets with `max_requests` 22 and reserve 22.
The live `dispatch/control.json` was not touched.

## 5. Request-cap rationale

22 outbound attempts for the day: 14 first attempts for 14 articles and up to 8
retries, as a hard ceiling on physical HTTP requests. One job start per tick. Three
dispatcher-started executions a day: one collector, one warehouse, and one spare for a
possible second warehouse start. Dispatcher executions started by the scheduler are
not counted in that three.

## 6. Next valid soak window

The 03:00 UTC tick of 8 October has passed. The next is **2026-10-09 03:00 UTC, a
Friday** (not the Monday reconciliation day, so the window is the routine 4 days).

Suggested shape, to be confirmed at launch: arm the control and apply the unpause
between about 02:15 and 02:45 UTC (the apply workflow itself takes about 15 minutes);
`armed_until` 08:30 UTC; scheduled ticks at 03:00 to 08:00 work, the 09:00 tick
refuses; pause straight after. Every tick is on the same UTC day, so one day's
counters apply. That is about 7 dispatcher executions plus 2 or 3 children, under the
ceiling of 15.

## 7. Stale re-pause plan: confirmed

Tried locally with Terraform 1.16.4 and a resource that stands in for the scheduler:

- a "re-pause" plan made while the value is still paused contains **no changes**;
- after the unpause plan is applied, applying that earlier plan is refused:
  `Error: Saved plan is stale … the state was changed by another operation after the
  plan was created.`

So a re-pause plan saved in advance is not a rollback artifact.

## 8. Corrected rollback procedure

The stop is the command, which acts on the scheduler directly:

```
gcloud scheduler jobs pause ms-dispatch-15m --location=us-central1 --project=metaspike-prod-kc
gcloud scheduler jobs describe ms-dispatch-15m --location=us-central1 --project=metaspike-prod-kc --format='value(state)'
```

The read-back must say `PAUSED`. It is run at the end of the soak and at once on any
anomaly; the control record is then disabled as a second barrier. Terraform is brought
back in line afterwards by a normal reviewed plan made with `scheduler_paused = true`.
For understanding only, and not to be run outside an approved step:
`gcloud scheduler jobs resume ms-dispatch-15m --location=us-central1 --project=metaspike-prod-kc`.

Neither command was run. The scheduler is already paused.

## 9. Durable documentation changes

Commit `a82778daade478f4b06754d10aad159bf2bccd69`, documents only:

- `docs/phase4-c2-plan.md`: the C2-D row and the unpause gate no longer rely on a
  prepared re-pause plan; a new "Pausing again" passage explains why and gives the
  command; it also states that unpausing turns the silence alert on.
- `docs/phase4-runbook.md`: the quota section now records the quota as set.

No application code, Terraform or workflow changed. Push CI for that commit passed.

## 10. Repository variable

`SCHEDULER_PAUSED` was `true`; set to `false` at 06:38:30 UTC for the plan run only;
restored to `true` at 06:55:12 UTC, immediately after the run finished; read back
`true` then and again at the end. The plan step ran with `scheduler_paused=false`. No
apply was requested while it was false.

## 11–16. The plan that was produced (not approvable)

| | |
|---|---|
| Run | `37738729342`, plan only |
| Head commit | `a82778daade478f4b06754d10aad159bf2bccd69` |
| Candidate image | `sha256:e2d3575550ea31f701bacd91b433574dc1b9c673543139da0081869dbe26a216` |
| Deployed image | `sha256:a8680b2be8b5e4bea48392c2b5324b44a680b4c2a4d56e2f49b836bdb979c754` |
| **Digest equal?** | **No** |
| Counts | 0 add, 9 change, 0 destroy, 0 replace |
| Prod state serial | 9 |
| Artifact | `tfplan`, expires 2026-10-11T06:55:00Z |

Complete diff: the seven jobs' `image` and `METASPIKE_IMAGE`; the scheduler's
`paused: true -> false` and nothing else on it (no cron, name, target, identity or
retry change); the silence policy's `enabled: false -> true` and nothing else on it.
No IAM, WIF, secret, dataset, bucket, table or other monitoring change.

## 17–22. Final state

- **Scheduler:** PAUSED, `0 * * * *`, no attempt time.
- **Control record:** present, `enabled: false`, generation `1791432826876698`,
  unchanged.
- **Cloud Run executions:** 14. No Cloud Run log entry of any kind since the start of
  this checkpoint.
- **Jobs:** all seven on `sha256:a8680b2b…c754`. Prod state serial 9, bootstrap 9:
  nothing applied.
- **Secrets and keys:** 0 versions; 0 user-managed keys. No IAM, schema, alert or
  scheduler drift. The silence policy is still disabled.
- **Sources called:** none.
- **BigQuery quota:** 10,240 MiB a day.
- **Git:** `main` level with origin, clean. The portfolio repository was not touched.

## 23. Launch sequence for the soak (not executed)

1. Read-only baseline audit.
2. Require the query quota to read 10,240 MiB.
3. Require the scheduler PAUSED with no attempt.
4. Require the execution count unchanged (14 today).
5. Require all jobs on `sha256:a8680b2b…c754`.
6. Require 0 secret versions and 0 keys.
7. Replace the control record on its exact generation with the template above:
   enabled, a finite six-hour `armed_until`, Wikimedia only, 22 attempts a day, 14
   targets an execution, one start a tick, three a day.
8. Read the control record back and verify it exactly.
9. Apply the reviewed unpause plan by its run id and hash.
10. Confirm the scheduler reads ENABLED.
11. Start no dispatcher by hand.
12. Watch the scheduler start `ms-dispatch` each hour.
13. The window includes the 03:00 UTC collection tick.
14. And the 04:00 UTC warehouse tick.
15. Continue until `armed_until`.
16. Leave the scheduler running for one more tick, to see the dispatcher refuse with
    `control_expired`.
17. Pause the scheduler at once with the command in section 8.
18. Confirm PAUSED.
19. Disable the control record on its generation.
20. Audit executions, source requests, BigQuery, storage, alerts and security.
21. Stop at the C2-D report.

No collector, warehouse or dispatcher is started by hand. Ceilings: at most 22 outbound
attempts to Wikimedia; at most one child start a tick and three a day; at most 15 new
executions in all, and if that would be passed the scheduler is paused and the soak
stops. On anything unexpected: pause with `gcloud` at once, disable the control record,
retry nothing by hand, keep the evidence, report blocked.

## 24. Warnings and open questions

1. **How an infrastructure-only plan pins the deployed image** is unsolved, and it will
   recur for every plan that is not an application release. Recommended: give the
   deploy workflow's plan operation an optional input naming an existing digest; when
   it is given, the workflow verifies that digest is in the registry and plans with it
   instead of the one it just built. That is a workflow change with tests, and needs
   its own approval. A build that is byte-reproducible would also work but is a larger
   and less certain change.
2. **The unpause plan will have two changes**, the scheduler and the silence alert,
   unless the alert is decoupled from the variable. Decoupling would leave the system
   unattended without its silence alert, so the recommendation is to accept two.
   During a six-hour soak the alert needs three hours without a successful dispatcher
   to fire; when the scheduler is paused by command at the end, the policy stays
   enabled until Terraform is reconciled and **would fire about three hours after the
   pause** unless the reconciling plan is applied sooner. That should be planned for.
3. **The quota is now live** and limits the owner's ad hoc queries too.
4. Each plan run leaves an image in the registry. Three undeployed images are there
   now, one of which must never be deployed (`sha256:1b373a41…`).
5. Local Terraform is one patch behind CI, so plans are checked from the plan run's
   rendered output and their hash.
6. Still open from earlier: the deferred freshness alert; the missed-tick review before
   any sampled source; the branch-protection decision before any secret-backed source.

## 25. Exact next approval required

Decide how the unpause plan is to be made, then it can be prepared again:

- **A (recommended):** approve a small workflow change that lets a plan reuse an
  existing image digest, and accept that the unpause plan changes two resources (the
  scheduler and the silence alert). Expected plan: 0 add, 2 change, 0 destroy.
- **B:** keep the workflow as it is and accept a plan that also moves the jobs to a
  freshly built image of the same source. Not recommended: it mixes an image rollout
  into the unpause.
- **C:** unpause by the `gcloud` resume command, outside a reviewed plan. Not
  recommended: it gives up the saved-plan review for the one change that starts
  unattended operation.

Also needed for either A or B: a decision on warning 2 (when the reconciling pause plan
is applied after the soak).

STOP. No plan was applied; the control record is disabled; the scheduler is paused;
the soak has not started.
