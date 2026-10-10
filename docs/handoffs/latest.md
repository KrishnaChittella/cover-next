# Handoff: latest checkpoint

This file is the short-lived handoff from Claude Code to ChatGPT. It is overwritten at
each checkpoint; Git history is the archive. It supports communication only and
replaces no code, test, audit or document.

Previous checkpoint: **PHASE 4 PART C2-D: RULED FUNCTIONAL PASS / EXECUTION CEILING FAIL —
RECONCILIATION NOT YET APPLIED**

Current checkpoint: **PHASE 4 PART C2-D: CLOSED — FUNCTIONAL PASS / EXECUTION CEILING FAIL**

The reconciliation plan applied on the single authorised retry. Terraform and the live
system now agree: scheduler paused, silence alert disabled. C2-D is closed under the
ruling **accepted with documented operational limit failure**. It is not a clean pass
and is not to be described as one.

## Reconciliation

| | |
|---|---|
| Saved plan run | `37989297604` |
| Plan SHA-256 | `74b80a4212f4fa04c15a310b4d4c5ef421b762386f3738dda30e80673ffe1de6` |
| Apply run | `38015062742`, 2026-10-10, finished 02:05:24 UTC, success |
| Result | `Apply complete! Resources: 0 added, 1 changed, 0 destroyed.` |
| Changed | `google_monitoring_alert_policy.dispatcher_silent`: `enabled: true -> false` |
| Prod state serial | 10 → **11** (91 managed resources, unchanged in number) |

This time the image verification passed, so the Docker Hub rate limit that stopped the
two earlier attempts (`37991467940`, `37992781735`) had cleared. No new plan was made,
and nothing about the workflow, Terraform or the image was changed.

**Preflight (01:55 UTC), all met:** `main` level with origin and clean; the only changes
since the plan's commit are three documents; plan run succeeded; artifact present and
unexpired (2026-10-12T20:56:59Z) and hashing exactly; rendered diff 0 add, 1 change,
0 destroy with the one expected change; prod state serial 10 with no apply in between;
scheduler PAUSED, `0 * * * *`, `Etc/UTC`; control disabled at generation
`1791578645841911`; `SCHEDULER_PAUSED` = `true`; 34 executions; all seven jobs on the
approved digest; 0 secret versions; 0 keys; quota 10,240 MiB; `dispatcher_silent` still
enabled.

## Verified after the apply

- **Scheduler:** PAUSED, `0 * * * *`, `Etc/UTC`. Not touched by the plan.
- **Control record:** disabled, generation `1791578645841911`, unchanged.
- **Monitoring:** `dispatcher_silent` **disabled**. The other seven policies are
  identical to before in every field. Still 8 policies.
- **Cloud Run:** 34 executions; none started by the reconciliation. All seven jobs at
  generation 6 on
  `sha256:a8680b2be8b5e4bea48392c2b5324b44a680b4c2a4d56e2f49b836bdb979c754`, every
  `METASPIKE_IMAGE` agreeing.
- **Security:** 0 secret versions; 0 Secret Manager access; 0 user-managed keys; no IAM
  difference; WIF provider ACTIVE.
- **BigQuery quota:** 10,240 MiB a day.
- **Sources:** none called. No job has run since the scheduler was paused at 20:43:48
  UTC on 9 October.

## The silence incident

- It opened at **00:07:08 UTC on 2026-10-10**, as expected with the scheduler paused and
  the policy still enabled. (I had estimated about 23:02; the policy's hourly alignment
  put it an hour later.) One alert email will have been sent.
- It was still **open at apply time**, and still listed as open a minute after the
  policy was disabled. Disabling the policy did not close it immediately.
- It was not closed or altered by hand. It is an artifact of an intentionally paused
  scheduler. With the policy disabled it sends nothing further; it should lapse by the
  platform's own auto-close. Whether it has closed can be read at the next checkpoint.
- Earlier history: the start-up incident of 9 October (02:44:29 to 03:07:05), closed by
  itself.

## C2-D: the permanent ruling

**ACCEPTED WITH DOCUMENTED OPERATIONAL LIMIT FAILURE.**

| | Result |
|---|---|
| Functional and source safety | PASS |
| Fail-closed control expiry | PASS |
| Collection, warehouse, request budget, security, unattended behaviour | PASS |
| Execution ceiling | **FAIL by 5** |

- Final executions for C2-D: **34** (baseline 14, ceiling 29).
- The five beyond the ceiling were fail-closed dispatchers only (16:00 to 20:00 UTC on
  9 October), each refusing with `control_expired`.
- Excess child jobs 0; excess source calls 0; excess work orders 0; excess ledger rows
  0.
- **Twelve consecutive `control_expired` refusals** were proven, 09:00 through 20:00.
- Wikimedia outbound attempts **14** (limit 22); retries **0**; 14 of 14 collected.
- Warehouse: 8 batches, 76 rows, dbt **PASS=28**, WARN 0, ERROR 0.
- Data: 1,372 observations (2026-07-08 to 2026-10-07), 28 ledger rows, 20 run rows; no
  duplicates, no truncation.
- BigQuery for the soak day: about 220 MiB billed, about 2 percent of the daily limit.
- Cause of the ceiling failure: cleanup about eight hours later than recommended while
  the hourly scheduler kept starting the dispatcher. Lesson: control expiry stops
  source work; it does not stop the scheduler.

Recorded permanently in `docs/phase4-c2-plan.md` section 26 and `docs/phase4-runbook.md`
hard gate 19.

## Still pending, on purpose

**32 landed run-log rows** from the sixteen dispatchers of 05:00 to 20:00 UTC on
9 October are not loaded. They are durable operational history: not deleted, not
rewritten, and no warehouse run was made for them. The next approved warehouse run
loads them by the existing mechanism. The routine load window is three days of landing
folders, so a run after 11 October needs the wider window the job already supports.

## What is and is not permitted next

- **C2-E: permitted.** It is review and analysis of what the soak measured. It has not
  been started.
- **C2-F: blocked.** A longer unattended soak may not start until there is a reviewed,
  bounded way to prevent indefinite hourly dispatcher executions after the control
  record expires: fail-closed, independent of the owner's machine, least-privilege with
  no broad Cloud Scheduler administration, deterministic, auditable and cheap. No
  solution is chosen yet.

## Open hardening items

1. **Deployments depend on an anonymous Docker Hub pull.** Every apply, however small,
   rebuilds the image in verification first, and a rate limit there blocked a one-line
   reconciliation twice. Needs its own decision.
2. The expiry-stop design above (the C2-F gate).
3. From earlier: the deferred Wikimedia freshness alert; the missed-tick review before
   any sampled source; the sampled collector not yet wired to a request budget; the
   branch-protection decision before any secret-backed source; local Terraform one patch
   behind CI; the Debian 13 base evaluation.

## State summary

| | |
|---|---|
| Scheduler | PAUSED |
| Control record | DISABLED |
| `dispatcher_silent` | DISABLED |
| Prod state serial | 11 |
| Executions | 34 |
| Deployed image | `sha256:a8680b2b…c754` on all seven jobs |
| Secret versions / keys | 0 / 0 |
| BigQuery daily query quota | 10,240 MiB |
| Repository variable `SCHEDULER_PAUSED` | `true` |

Git: only this handoff file changes in this commit. The portfolio repository was not
touched.

## Exact next approval required

Approval to begin C2-E (cost and reliability review from the measured soak). Separately,
whenever it is wanted: the expiry-stop design for the C2-F gate, and the Docker Hub
dependency.

STOP. C2-E has not begun.
