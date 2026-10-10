# Handoff: latest checkpoint

This file is the short-lived handoff from Claude Code to ChatGPT. It is overwritten at
each checkpoint; Git history is the archive. It supports communication only and
replaces no code, test, audit or document.

Previous checkpoint: **PHASE 4 PART C2-F PREP: PARTIAL STATE RECONCILED IN PLAN — REVIEW REQUIRED**

Current checkpoint: **PHASE 4 PART C2-F PREP: HARDENING COMPLETE — LAUNCH DESIGN REQUIRED**

The one-resource reconciliation plan was applied once and succeeded. The monitoring
hardening is now complete in production: all three resources of the original plan
exist. The scheduler stayed paused, the control record disabled, and no source was
called. C2-F has not started and no dates were chosen.

## The apply

| | |
|---|---|
| Saved plan run | `38024751036` |
| Plan SHA-256 | `f476e8250004da1902a4a517b657cc9d34acb119fc8cb8087c9ba1bb31eaa56d` |
| Apply run | `38026182605`, 2026-10-10, apply job finished 05:11:47 UTC, success |
| Result | `Apply complete! Resources: 1 added, 0 changed, 0 destroyed.` (0 replaced) |
| Created | `google_monitoring_alert_policy.scheduler_delivery_failed` |
| Prod state serial | 12 → **13** |
| Managed resources | 93 → **94**, exactly one more |

**Pre-apply check (05:02 UTC), all met:** `main` level with origin and clean; only two
documents changed since the plan's commit `468e8f7`; plan run succeeded; artifact
unexpired and hashing exactly; state serial 12 with no apply since the plan; the
counter and `dispatcher_failed` present, `scheduler_delivery_failed` absent, the
descriptor readable; scheduler PAUSED `0 * * * *` `Etc/UTC`; control disabled; 34
executions; seven jobs on the approved digest; 0 secret versions; 0 keys; WIF ACTIVE;
quota 10,240 MiB; no soak variable; no source call.

## Partial-apply history, kept

1. Plan `38022130278` (SHA `7d441d9e…a4914`), 3 to add, approved.
2. Apply `38023222777`, 04:18 UTC: created the counter `ms_scheduler_attempt_failed`
   and the policy `dispatcher_failed`; Cloud Monitoring then refused
   `scheduler_delivery_failed` with a 404 because the counter's descriptor was not yet
   published. Serial 11 → 12. Nothing retried or deleted.
3. Ruling: partial state accepted as safe; that plan spent.
4. Plan `38024751036` (SHA `f476e825…a56d`), 1 to add, approved.
5. Apply `38026182605`, 05:11 UTC: created the remaining policy. Serial 12 → 13.

The race between a new log-based metric and an alert that reads it remains recorded as
a later infrastructure-hardening finding in section 28 of `docs/phase4-c2-plan.md`. It
is not fixed.

## Monitoring state, complete

Ten alert policies, all on the one reviewed notification channel:

| Policy | Enabled |
|---|---|
| MetaSpike: the scheduler could not start the dispatcher (new) | yes |
| MetaSpike: a dispatcher execution failed (new, from the first apply) | yes |
| MetaSpike: repeated job failures | yes |
| MetaSpike: warehouse load or build failed | yes |
| MetaSpike: a source keeps rate limiting | yes |
| MetaSpike: Steam refused the key | yes |
| MetaSpike: baseline state parity mismatch | yes |
| MetaSpike: unusually many job executions in a day (threshold 60) | yes |
| MetaSpike: landing bucket above 5 GB | yes |
| MetaSpike: dispatcher has not succeeded for 3 hours | **no**, as intended |

The new policy, read back from the live project:

- display name "MetaSpike: the scheduler could not start the dispatcher"; enabled;
- condition "any failed scheduler attempt in an hour";
- filter `resource.type="cloud_scheduler_job" AND
  metric.type="logging.googleapis.com/user/ms_scheduler_attempt_failed"`;
- greater than 0, duration 0 s, summed over a 3,600 s alignment period;
- one notification channel, identical to the channel on every other policy.

The nine policies that existed before this apply are identical to their pre-apply
copies in every field, `dispatcher_failed` included. Six log-based metrics;
`ms_scheduler_attempt_failed` is unchanged (filter
`resource.type="cloud_scheduler_job" AND severity>=ERROR`, DELTA INT64). The counter
has no data, which is correct: no scheduler attempt has failed. No failure was
provoked to test it.

## Everything else, verified unchanged

- **Scheduler:** PAUSED, `0 * * * *`, `Etc/UTC`.
- **Control record:** disabled, generation `1791578645841911`.
- **Cloud Run:** 34 executions; no job ran (no Cloud Run log entry of any kind since
  before the first apply). All seven jobs at generation 6 on
  `sha256:a8680b2be8b5e4bea48392c2b5324b44a680b4c2a4d56e2f49b836bdb979c754`.
- **Security:** 0 secret versions; 0 user-managed keys across 10 service accounts; the
  audit comparison against the pre-apply snapshot differs only in the alert list; WIF
  pool ACTIVE.
- **BigQuery quota:** 10,240 MiB a day.
- **Registry:** 11 images, none new.
- **Sources:** Wikimedia 0, Steam 0, IGDB 0.
- **Repository variables:** `SCHEDULER_PAUSED` = `true`; `SOAK_MONTH`,
  `SOAK_START_DAY`, `SOAK_END_DAY` and `SOAK_DAILY_EXECUTION_CEILING` unset.
- **The 32 pending run-log rows:** untouched (36 landed for 2026-10-09, 4 loaded).
- The portfolio repository was not touched.

## Hardening status

Complete:

- finite scheduler window (built and tested; first used by the unpause plan);
- monitoring for a finite soak (applied: the two failure alerts; the silence alert and
  the volume threshold switch with the soak's variables);
- base image from Google's public mirror with Docker Hub as fallback (five runs on the
  runner have now fetched, verified and scanned through the mirror);
- approved billing model and projections recorded.

## Remaining launch items, exactly

1. Choose the seven days, inside one UTC calendar month.
2. Calculate the scheduled tick count (24 for each day; 168 for seven).
3. Choose the final daily execution ceiling (`24 + 2 x max_executions_per_day` is the
   value that never fires in a correct soak).
4. Prepare the control record, with `armed_until` in the soak's year.
5. Calculate `METASPIKE_LOAD_DAYS` for the 32 pending rows:
   (UTC date of the run − 2026-10-09) + 1.
6. Perform the supervised warehouse catch-up.
7. Produce and review the finite unpause plan.

None of these was begun.

## Exact next approval required

The C2-F launch design: the dates, the control record and the daily ceiling.

C2-F has not started. STOP.
