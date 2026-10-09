# Handoff: latest checkpoint

This file is the short-lived handoff from Claude Code to ChatGPT. It is overwritten at
each checkpoint; Git history is the archive. It supports communication only and
replaces no code, test, audit or document.

Previous checkpoint: **PHASE 4 PART C2-D: CLEANUP AUDITED — REVIEW REQUIRED**

Current checkpoint: **PHASE 4 PART C2-D: RULED FUNCTIONAL PASS / EXECUTION CEILING FAIL —
RECONCILIATION NOT YET APPLIED**

The ruling is recorded in the permanent documents. **The reconciliation plan has not
been applied.** Two attempts to apply it stopped before Terraform ran, both for the same
external reason. Nothing changed live in either attempt. The requested headline
("CLOSED … RECONCILIATION: APPLIED") would be untrue, so it is not used.

The scheduler is paused and the control record is disabled, so the system is safe. What
is outstanding is one alert policy that is still enabled.

**Time-sensitive:** `dispatcher_silent` is still enabled. With the scheduler paused it
will open an incident and send an email at about **23:02 UTC on 2026-10-09** (three
hours after the last successful dispatcher, at 20:02), unless the reconciliation is
applied first. It is harmless.

All times are UTC on 2026-10-09.

## Why the reconciliation is not applied

The deploy workflow runs the full verification before any cloud step, and that
verification builds the image from scratch. Both apply runs failed in that build, pulling
the base image from Docker Hub:

`python:3.12-slim-bookworm@sha256:54c85f3c… : 429 Too Many Requests`

| Apply run | Verification | Apply job | Terraform |
|---|---|---|---|
| `37991467940` (21:07) | image build failed, 429 | skipped | not started |
| `37992781735` (21:20) | image build failed, 429 | skipped | not started |

The push run for the previous handoff commit failed the same way at the same time. This
is Docker Hub's rate limit on anonymous pulls from shared GitHub runner addresses, not a
change in the repository: application and infrastructure verification passed in both
runs.

After the first failure I started the same apply once more, having confirmed that
nothing had changed. That second start was my own judgement: the plan was untouched and
the apply had never begun. When it failed identically I stopped. No third attempt, no
new plan, no change to the workflow, and the alert was not altered by hand.

## Pre-apply checks (21:06), all met

Scheduler PAUSED (`0 * * * *`, `Etc/UTC`); control disabled; prod state serial 10;
`SCHEDULER_PAUSED` = `true`; all seven jobs on `sha256:a8680b2b…c754`; the plan artifact
present, unexpired (2026-10-12T20:56:59Z) and hashing exactly to
`74b80a4212f4fa04c15a310b4d4c5ef421b762386f3738dda30e80673ffe1de6`; no infrastructure
apply since the plan was made.

The plan remains valid: state is still at serial 10.

| | |
|---|---|
| Plan run | `37989297604` |
| Plan SHA-256 | `74b80a4212f4fa04c15a310b4d4c5ef421b762386f3738dda30e80673ffe1de6` |
| Expected | 0 add, 1 change, 0 destroy, 0 replace |
| The one change | `google_monitoring_alert_policy.dispatcher_silent`: `enabled: true -> false` |

## State now (21:29)

- **Scheduler:** PAUSED, `0 * * * *`, `Etc/UTC`.
- **Control record:** disabled, generation `1791578645841911`.
- **`dispatcher_silent`:** still **enabled**. No incident is open yet.
- **Prod state serial:** 10 (it would be 11 after the reconciliation).
- **Executions:** 34. The failed apply runs started none.
- **Jobs:** all seven on
  `sha256:a8680b2be8b5e4bea48392c2b5324b44a680b4c2a4d56e2f49b836bdb979c754`.
- **Secrets and keys:** 0 versions; 0 user-managed keys.
- **BigQuery quota:** 10,240 MiB a day.
- **Repository variable:** `SCHEDULER_PAUSED` = `true`.
- No source was called.

## The ruling, as recorded

**PHASE 4 PART C2-D: ACCEPTED WITH DOCUMENTED OPERATIONAL LIMIT FAILURE.** Not a clean
pass.

| | Result |
|---|---|
| Source safety | PASS |
| Fail-closed control expiry | PASS |
| Collection | PASS |
| Warehouse | PASS |
| Request budget | PASS |
| Security | PASS |
| Unattended behaviour | PASS |
| Execution-count criterion | **FAIL by 5** |

- Baseline 14; allowed total 29; **actual total 34; exceeded by 5.**
- The five excess executions are the dispatchers of 16:00, 17:00, 18:00, 19:00 and
  20:00. Each refused with `control_expired`.
- Excess source calls 0; excess child jobs 0; excess work orders 0; excess
  expected-target rows 0; no movement in the source budget.
- Twelve consecutive expired-control refusals were observed, 09:00 through 20:00, none
  with any child or source effect.
- Cause: cleanup happened at 20:43, roughly eight hours after the recommended time,
  while the hourly scheduler kept starting the dispatcher.
- Lesson: control expiry stops source work; it does not stop the scheduler.

No repeat of the soak is required. The failure stays on the record.

## Soak figures (unchanged from the audit)

- **Wikimedia:** 14 physical outbound attempts of a limit of 22; 0 retries; 14 targets
  collected; no source error, no 429, no budget exhaustion; no call after the 03:00
  collection.
- **Warehouse (`ms-warehouse-d726r`, 04:00):** 8 batches loaded, 11 already loaded, 76
  rows; no late data; no full refresh; dbt PASS=28, WARN=0, ERROR=0, SKIP=0.
- **Data:** `ms_raw.period_observations` 1,372 (+56); staging 1,372; `expected_targets`
  28 (+14); `collection_runs` 20 (+8). Range 2026-07-08 to 2026-10-07; no duplicates,
  no truncation, no revision among overlapping periods.
- **BigQuery:** 70 jobs (46 top level), about 220 MiB billed, 59,775 slot-ms, no error;
  about 2 percent of the daily limit.
- **Alerts during the soak:** only the start-up silence incident, opened 02:44:29 and
  closed by itself at 03:07:05.
- **Security:** no secret version, no secret access, no key, no IAM or WIF drift.

## Pending by design: 32 run-log rows

The run logs of the sixteen dispatchers from 05:00 to 20:00 are landed and not loaded.
They are durable operational history, left exactly as they are. No warehouse run was
made for them. The next approved warehouse run loads them by the existing mechanism.

## Permanent documentation

Updated in this commit, documents only:

- `docs/phase4-c2-plan.md`, new section 26, "C2-D closeout: functional pass, execution
  ceiling failed": the two results kept apart, the cause, the impact, the lesson, the
  twelve refusals, the pending rows.
- `docs/phase4-runbook.md`, hard gate 19: stopping the scheduler when a soak expires.

## Hard gate before C2-F

A longer unattended soak may not start until there is a reviewed, bounded way to prevent
indefinite hourly dispatcher executions after the control record expires. No solution
is chosen. It must keep source safety fail-closed; not depend on the owner's machine;
leave no indefinite scheduler execution after the finite window; be least-privilege,
with no broad Cloud Scheduler administration; and be deterministic, auditable and cheap.

- **C2-E:** permitted by the ruling **after reconciliation**, which has not happened.
- **C2-F:** not permitted.

## A second gap this exposed

Any apply, however small, depends on Docker Hub answering an anonymous pull, because
verification rebuilds the image first. Today that blocked a one-line reconciliation
twice. It is worth its own decision (for example an authenticated or mirrored pull of
the pinned base image, or verification that does not rebuild for an infrastructure-only
apply). Nothing was changed for it here.

## Exact next approval required

One of:

- **Retry the same apply later**, once Docker Hub's limit has cleared: the deploy
  workflow, `operation = apply`, `plan_run_id = 37989297604`, `plan_sha256 =
  74b80a4212f4fa04c15a310b4d4c5ef421b762386f3738dda30e80673ffe1de6`, `allow_destroy =
  false`. Valid while prod state stays at serial 10 and until 2026-10-12T20:56:59Z.
- **Or accept that the alert fires once** at about 23:02 UTC and apply when convenient.

After it applies: verify state serial 11, the policy disabled, everything else
unchanged; then C2-D can be marked closed and C2-E may begin on its own approval.

STOP. C2-E has not begun.
