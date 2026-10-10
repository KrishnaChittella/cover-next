# Handoff: latest checkpoint

This file is the short-lived handoff from Claude Code to ChatGPT. It is overwritten at
each checkpoint; Git history is the archive. It supports communication only and
replaces no code, test, audit or document.

Previous checkpoint: **PHASE 4 PART C2-E: REVIEW COMPLETE — CHATGPT APPROVAL REQUIRED**

Current checkpoint: **PHASE 4 PART C2-F PREP: HARDENING READY — PLAN REVIEW REQUIRED**

The hardening is built, tested and pushed (`0250e61`). One infrastructure-only plan was
made for review and **not applied**. Nothing in production changed. The scheduler
stayed paused, the control record disabled, and no source was called. C2-F has not
started and has no dates.

## The plan to review

| | |
|---|---|
| Plan run | `38022130278` (commit `0250e61`, `reuse_deployed_image=true`) |
| Plan SHA-256 | `7d441d9e92d300032b4676daeda297409e50611760cc4b6a9329d25074da4914` |
| Artifact expires | 2026-10-13T03:59:46Z |
| Result | **3 to add, 0 to change, 0 to destroy** |
| Image | reused: the image all seven jobs already run. No job changes |

Exact diff:

1. `+ google_logging_metric.counter["ms_scheduler_attempt_failed"]`
   filter `resource.type="cloud_scheduler_job" AND severity>=ERROR`, a DELTA INT64
   counter.
2. `+ google_monitoring_alert_policy.scheduler_delivery_failed`
   "MetaSpike: the scheduler could not start the dispatcher": that counter above 0 in
   an hour; enabled; the existing notification channel.
3. `+ google_monitoring_alert_policy.dispatcher_failed`
   "MetaSpike: a dispatcher execution failed": failed `ms-dispatch` executions above 0
   in an hour, on Cloud Run's own metric; enabled; the existing channel.

Outputs added: `dispatch_schedule = "0 * * * *"`, `daily_execution_alert_threshold = 60`.

Not in the diff, as intended: the scheduler (still paused, still `0 * * * *`), the
silence alert (still disabled), the volume alert (still 60), every job, every IAM
binding, every secret. No destroy, no IAM, no secret change, no image change. The
finite schedule itself appears only in the later unpause plan, when dates exist.

## Billing-model correction

Recorded in section 28 of `docs/phase4-c2-plan.md` and in the runbook. Jobs are billed
for the lifetime of an instance once its container has started, minimum one minute.
Time before the container starts is not billed and is kept as a reliability
measurement (37 to 245 s, median 134). The approved projection is the one-minute
model; the earlier "conservative" column is marked superseded and left as written.

| Configuration | Approved projection, list price | Verdict |
|---|---|---|
| 14 articles, hourly, one build a day, per month | about $1.20 | below the preferred range |
| Seven-day C2-F soak | about $0.30 | negligible |
| 1,000 catalogued | $4.28 | below the preferred range |
| 5,000 | $4.69 | below the preferred range |
| 10,000 | $5.76 | within the preferred range |
| 50,000 | $11.55 | acceptable with scrutiny |

Kept separate in the document: measured container time (9 s dispatcher, billed 60 s),
the unbilled provisioning time, BigQuery ($0.04), Scheduler ($0.10), storage (under a
cent), registry ($0.16). With allowances the 14-article month is about $0.16. The
catalogue rows are planning figures from the existing simulations and approve nothing.
$20 remains hard.

## Monitoring-pricing correction

The alert-condition charge begins no sooner than 2027-09-01 and is in no current
projection. It is recorded as a later consideration (ten single-condition policies
after this plan). No billing export was created; it is not a C2-F gate.

## Finite scheduler

- Terraform variable `soak_window = { month, start_day, end_day }` produces
  `0 * <start>-<end> <month> *` in `Etc/UTC`; one day produces `0 * <day> <month> *`.
- `schedule_mode` defaults to `finite_soak`: unpausing without a window is refused, and
  a window while paused is refused, so paused always means the plain `0 * * * *`.
  `steady_state` is the unbounded schedule and the deploy workflow cannot pass it.
- One calendar month only: end before start, a day past the month's end, month 13 and
  29 February are refused.
- The deploy workflow reads four repository variables, all or none: `SOAK_MONTH`,
  `SOAK_START_DAY`, `SOAK_END_DAY`, `SOAK_DAILY_EXECUTION_CEILING`. None is set.
- The plan shows the cron on the scheduler and in the outputs `dispatch_schedule` and
  `soak_scheduled_ticks`.
- No new permission, identity or runtime code. The dispatcher cannot pause anything.
  `armed_until` is untouched and independent.

## Annual recurrence

**This is not a one-shot schedule.** Cloud Scheduler's cron has no year and the service
has no end date. A window is eligible again on the same dates the following year. What
it guarantees is that hourly ticks stop at the end of its last day and nothing fires
for the rest of that year (358 days for a seven-day window). The safeguards:

- A. `armed_until` carries the year and fails closed.
- B. No execution in the post-soak period: tested hour by hour for thirty days and on
  to the recurrence.
- C. Post-soak reconciliation pauses the scheduler and removes the window; Terraform
  refuses a paused scheduler that still carries one.
- D. Recorded in the plan document, the runbook, the Terraform variables and the
  module's docstring.
- E. Tested, including a test that asserts the recurrence so it cannot later be
  described as year-bounded.

## Schedule tests

`tests/test_c2f_finite_soak.py` walks a calendar with the generated cron:

| Requirement | Test |
|---|---|
| Hourly ticks inside the window | 168 consecutive hours, none missing or doubled, on the hour only |
| First and last eligible tick | 12 Oct 00:00 and 18 Oct 23:00 UTC for the example window |
| No tick just before | 11 Oct 23:00, and nothing from 1 January to the start |
| No tick after, same year | 19 Oct 00:00, thirty days hour by hour, and to 31 December |
| Exact count | Seven windows: 24, 168 and 720 ticks, including a leap year and December |
| End before start refused | Python and Terraform |
| Cross-month refused | Python and Terraform, including February 29 |
| Unbounded refused in finite-soak mode | Terraform `unpausing_is_refused_without_a_finite_window` |
| Steady state separate | Terraform: its own mode, takes no window, keeps the silence alert and 60 |
| Recurrence | The same window fires again on 12 Oct of the next year, and not before |

The Python cron and the Terraform cron are pinned to the same literal strings.

**Ceiling.** `24 x days + days x max_executions_per_day`: the ticks are exact and the
tick enforces the control record's daily child cap. 168 + 7 x 3 = 189 with C2-D's cap.
The final figure is not set; it depends on the C2-F control record.

## Monitoring for finite soaks

- The silence alert is on only for an unpaused, unbounded schedule. In a finite soak it
  is off, so it cannot fire before the first tick or after the last.
- In its place, on in every mode: a failed scheduler attempt, and any failed
  dispatcher execution. Neither can fire when nothing runs. The second closes an older
  gap: one dispatcher failure an hour never reached the "two failures in an hour"
  alert.
- The volume alert takes `soak_daily_execution_ceiling` (24 to 59) in a finite soak, 60
  otherwise. Terraform refuses a finite soak without it. The value that never fires in
  a correct soak is `24 + 2 x max_executions_per_day`, because a rolling day can hold
  two UTC days' children.
- No failure alert was weakened. Not alerted on: a scheduler that silently attempts
  nothing; the post-soak audit compares executions with the exact tick count.

## Docker mirror and fallback

- The Dockerfile takes its base as `${BASE_REGISTRY}/python:3.12-slim-bookworm@sha256:54c85f3c…8cb3`.
  The digest is unchanged and is the trust anchor.
- `.github/scripts/build-image.sh` tries `mirror.gcr.io/library`, then
  `docker.io/library`. For each: fetch the manifest by digest, check the bytes hash to
  the pinned digest, pull by digest. It builds from the first that passes, with
  `--pull --no-cache`. If neither passes it fails and builds nothing.
- No credential, no secret. No workflow step removed: build, run, dbt and scan are as
  before.
- **Proof on the runner:** push CI run `38021586494` and plan run `38022130278` both
  logged "base python@sha256:54c85f3c…8cb3 verified and pulled from
  mirror.gcr.io/library", and the image built, ran and passed the scan.
- No image was pushed to the registry and none deployed.

## Pending run-log rows

Still 36 landed for 2026-10-09, 4 loaded, **32 pending**. Untouched; no warehouse run.

The first approved warehouse execution of C2-F needs
`METASPIKE_LOAD_DAYS = (UTC date of that run - 2026-10-09) + 1`: 4 on 12 October, 10 on
18 October, 24 on 1 November. It is an execution-time setting on one supervised run
before the scheduler is unpaused. The exact value goes into the launch instruction
when the start date is chosen.

## Tests

- Offline suite: **1,199 passed**, 5 deselected (was 1,160). Ruff and strict mypy clean.
- Terraform: format clean, both roots valid, **25** production tests passed (was 12),
  bootstrap tests passed in CI.
- New: `tests/test_c2f_finite_soak.py`, `tests/test_base_image_mirror.py`, 13 Terraform
  runs. The build script is run for real in tests against stand-in `curl` and `docker`.
- CI: push run `38021586494` passed, cloud jobs skipped. Plan run `38022130278` passed.

## Limits of what was proven

- A finite-soak plan has only been made against the mocked provider. The real one is
  the unpause plan, once dates are chosen.
- The scheduler-error counter has never seen a real failed attempt; its filter is by
  the documented severity, not by an observed entry.
- A new log-based metric can take a short while to become visible to a new alert
  policy. If the apply of this plan fails on that, nothing is half-done that matters,
  and it is to be reported, not retried.
- A full local build was started and interrupted after the base was verified and
  pulled from the mirror; the complete build is the CI one.

## Security state

Scheduler PAUSED, `0 * * * *`, `Etc/UTC`. Control disabled, generation unchanged.
Executions 34. All seven jobs on `sha256:a8680b2b…c754`. Prod state serial 11. 0 secret
versions. 0 user-managed keys. 8 alert policies live (10 after this plan). 11 images in
the registry, none new. No IAM or WIF change, none planned. Repository variables: only
`SCHEDULER_PAUSED` = `true`; no soak variable set. Wikimedia calls 0, Steam 0, IGDB 0.
The portfolio repository was not touched.

## C2-F entry gates

| Gate | State |
|---|---|
| 1. Expiry stop implemented, reviewed, tested | Built and tested; awaiting this review |
| 2. Tick count known in advance, ceiling derived | Met by construction; final figure at launch |
| 3. Zero executions after the window | Tested; live proof is the soak itself |
| 4. Source fail-closed unchanged | Met; no control-path code changed |
| 5. Request budgets unchanged | Met |
| 6. Sampled collector inactive | Met |
| 7. Scheduler configuration reviewed in plan output | At the unpause plan |
| 8. Monitoring fits a finite soak | Built; **needs this plan applied** |
| 9. Alerting charge read; projection inside bounds | Met: none until 2027-09-01; $0.30 soak, $1.20 a month |
| 10. No destroy; state matches live | Met: this plan destroys nothing; serial 11 |
| 11. Security clean | Met |
| 12. Deploy path survives a Docker Hub rate limit | Met for the base image, proven in CI |
| 13. 32 pending rows accounted for | Met: rule recorded; value at launch |
| 14. Wikimedia only, 14 articles, tiering shadow only | Met |

Everything except date selection and launch passes **except gate 8, which needs this
plan reviewed and applied**. Until then: C2-F BLOCKED on plan review.

## Exact next approval required

Review of plan `38022130278` (SHA-256 above), and approval to apply exactly that plan.
After it: the choice of soak dates, the C2-F control record, and the unpause plan.

STOP. C2-F has not begun.
