# Handoff: latest checkpoint

This file is the short-lived handoff from Claude Code to ChatGPT. It is overwritten at
each checkpoint; Git history is the archive. It supports communication only and
replaces no code, test, audit or document.

Previous accepted checkpoint: **PHASE 4 PART C2-C: COMPLETE**

Current checkpoint: **PHASE 4 PART C2-C.1: PLAN READY**

The request-budget fix is implemented, tested, committed and pushed. A production plan
that moves the seven jobs to the new image is saved and **not applied**. Nothing ran in
the cloud: no job execution, no source call, no control change, no quota change. The
scheduler is paused and the control record is disabled.

## 1. The bug and the invariant

In C2-C the control record capped Wikimedia at 14 requests a day. Three things did not
match that:

- the work order issued under it allowed 22 (`targets × 1.5 + 1`), uncapped by what
  was left of the day;
- the dispatcher's daily counter advanced by the 14 planned targets, not by what the
  order permitted;
- retries were sent inside the shared HTTP client, below the collector's count, and
  the collector counted fetch calls, not physical requests.

With a transient failure, more physical requests could have left the process than the
day's cap, unnoticed. **In C2-C none did:** 14 were sent with no retry.

The invariant now: a request budget means **actual outbound HTTP attempts**. Every
physical request, each retry included, takes one unit. With none left, nothing more is
sent.

## 2. Design

- One source-agnostic primitive, `AttemptBudget`, in the shared HTTP client
  (`sources/http.py`). `LimitedHttpClient` always has one and takes a unit from it
  **immediately before each send, inside the retry loop**. Unlimited by default (it
  then only counts); a limit can be lowered and never raised.
- When the budget is spent the client raises `RequestBudgetExhausted`. This is
  deliberately **not** a `SourceError` and has no source error code.
- The collector restricts the adapter's budget to the work order's `max_requests` (or
  the source's execution ceiling when supervised), and reads the count of attempts
  from it.
- The dispatcher caps an order's budget by the day's remainder and reserves all of it.

## 3. Files changed

Source commit `e99812ca6b59e1b10145db2bca5fdf044cac1029`, 9 files:

- `src/metaspike/sources/http.py` — `AttemptBudget`, `RequestBudgetExhausted`,
  enforcement in `LimitedHttpClient.request`
- `src/metaspike/sources/wikimedia.py` — the adapter accepts and exposes its budget
- `src/metaspike/jobs/collect.py` — budget use, summary fields, receipt fields, outcome
- `src/metaspike/scheduling/tick.py` — order budget capped by the day; reservation
- `src/metaspike/scheduling/workorder.py` — `max_requests` meaning; receipt fields
- `tests/test_c2_request_budget.py` — new, 71 tests
- `tests/test_c2_scheduling.py` — three expectations updated
- `docs/phase4-runbook.md`, `docs/phase4-c2-plan.md`

No Terraform, workflow or Dockerfile change.

## 4. The attempt-budget primitive

`AttemptBudget(limit=None)`: `take(label)` spends one unit or raises
`RequestBudgetExhausted` and spends nothing; `used`, `remaining`, `exhausted`;
`restrict(limit)` lowers the limit only. The unit is taken inside the rate limiter's
slot, directly before the transport call, so a request refused by the limiter's own
quota is not counted as sent.

## 5. Retry semantics

Bounded retries are kept. For one logical fetch, retries stop at the retry policy's
maximum or at the budget, whichever comes first. If the budget is spent between an
attempt and its retry, the retry is not sent and no backoff is waited out for it. The
exception then carries the source error from the attempt that was sent (`after`); when
nothing was sent for the request at all, `after` is none.

| Case | Result (proven by counting transport sends) |
|---|---|
| A. 14 targets, budget 14, no failure | 14 sends, 14 collected, 0 retries |
| B. 14 targets, budget 14, one retry | 14 sends, never 15; 13 collected; 1 target deferred by the budget, not failed |
| C. 14 targets, budget 22, eight retries wanted | 22 sends, 14 collected; with one retry more than fits, 22 sends and 2 deferred |
| D. budget spent before a retry | the retry never leaves the process |
| E. HTTP 429 before the budget is spent | a real rate limit, breaker opens as before |
| F. budget spent with no 429 | not a rate limit; breaker does not move |

## 6. Dispatcher reservation

- An order's `max_requests` is the smallest of: the source's execution ceiling,
  `targets × 1.5 + 1`, and **what is left of the day** (the source's daily ceiling, or
  the control record's lower one, minus what is already reserved).
- When the order is issued the dispatcher adds **the whole of that budget** to the
  day's counter. The counter is therefore attempts reserved, not attempts sent.
- Unused retry room is not returned until the next day. That is conservative by
  intent: the orders of a day cannot add up to more than its ceiling, by construction,
  with no reconciliation step.
- With the C2-C control record (`max_requests_per_day = 14`) the order would now carry
  14, not 22, and the counter would read 14.

## 7. Work-order semantics

`WorkOrder.max_requests` has one meaning: the most physical outbound HTTP attempts the
collector may cause for the order, retries included. `len(targets) <= max_requests`
still holds and is still enforced when an order is issued. It is not assumed to equal
the number of targets.

## 8. Receipt compatibility

`OrderReceipt` gained four fields, all defaulted: `request_budget`, `targets_attempted`
and `retry_attempts` (none when absent), and `targets_budget_deferred` (0 when absent).
`requests_sent` is kept and now defined as outbound attempts.

The C2-C receipt for order `34e4f8be49cf8a61e3709cc9c36cc478` is used byte for byte in
a test: it still parses, means the same (succeeded, 14, 14 collected, 0 failed), and
folds into the same health the production record holds. The production object was not
rewritten.

One direction is not compatible, by the model's strictness: a receipt written by the
new image cannot be read by the old image. Dispatcher and collector share one image, so
this only matters if two images ran side by side, which a single apply does not allow.

## 9. Local budget versus source failure

- Log event `request_budget_exhausted` (WARNING), never `source_refused`, so the
  `ms_source_rate_limited` metric cannot count it.
- `halted_by` is `request_budget_exhausted`. Targets not reached are reported as
  `targets_budget_deferred`, separate from `targets_failed`.
- The run is `partial`. The receipt's outcome is PARTIAL, which the breaker treats as
  a success: it does not open and the source stays healthy.
- If an attempt was sent and failed and only its retry was withheld, that target is
  recorded as failed with the real error code (for example `timeout`) and a note that
  the retry was not sent.
- A real 429, including one on the last attempt the budget had, is still
  `rate_limited` and still opens the breaker.
- No observation is created for a target that was not asked. The ledger already says
  it was planned; its slot remains expected and not sampled downstream.

Collector summary fields: `targets_intended`, `targets_attempted`, `targets_collected`,
`targets_failed`, `targets_budget_deferred`, `outbound_attempts`, `retry_attempts`,
`request_budget`, and `requests_sent` (equal to `outbound_attempts`).

## 10. Tests

`tests/test_c2_request_budget.py`: 71 tests, with a transport that counts every request
it is handed, so "outbound attempts" is the number of transport sends. They cover all
25 points of the requested matrix, including a sweep of budgets 1 to 39 against a
transport that fails in mixed ways (sends never exceed the budget and every target is
accounted for exactly once), reservations over several executions in one day, and the
exact C2-C receipt bytes.

Results: ruff and format clean; mypy clean (163 files); **1,137 passed, 5 deselected**,
dbt tests included (was 1,066). No test skipped or weakened. Three existing
expectations changed because the semantics changed: a daily counter now reads the
reserved 22 instead of 14 targets; a run stopped by its budget reports targets as
deferred instead of failed; and the simulated 50,000-entity day asks Wikimedia for
about 6,700 targets, not 10,000, because retry room is reserved inside the ceiling.

## 11–12. Source commit and CI

- Source commit: `e99812ca6b59e1b10145db2bca5fdf044cac1029` on `main`.
- Push CI run `37734314381`: success (application, image and infrastructure
  verification; plan, apply and identity-check skipped).

## 13–14. Image

- `us-central1-docker.pkg.dev/metaspike-prod-kc/metaspike/metaspike@sha256:a8680b2be8b5e4bea48392c2b5324b44a680b4c2a4d56e2f49b836bdb979c754`,
  built from the source commit by the plan run. 226 MB compressed. In the registry,
  **not deployed**.
- Verified on that exact digest, offline: uid and gid 10001; `/app` and `/opt/dbt` not
  writable; 0 setuid or setgid files; the two Python environments isolated; Debian 12;
  no control record, credential, key or tfvars file (the one matching variable name is
  the base image's public `GPG_KEY`); the attempt budget and the new receipt fields are
  present.
- `pip-audit`: no known vulnerabilities, both environments.
- Trivy 0.70.0 on the exact digest: **53 HIGH, 2 CRITICAL, 0 fixable**, 0 Python
  findings; the identical set to the deployed image.

## 15–18. Production plan (not applied)

- Deploy-workflow run `37734819174`, plan only, on the source commit, by the restricted
  WIF identity. All 91 resources refreshed.
- Plan SHA-256 `15a0c0599bdb0e053f501c1aa09c36868df2a18fe3fd128c40d5a0f0116af358`
  (recomputed locally from the downloaded artifact; matches).
- Artifact `tfplan`, expires 2026-10-11T06:02:52Z.
- **0 to add, 7 to change, 0 to destroy, 0 replaced.**
- Complete diff: the seven `google_cloud_run_v2_job.this[…]` resources, updated in
  place, each changing only `image` and the `METASPIKE_IMAGE` value from
  `sha256:f452c801…e100` to `sha256:a8680b2b…c754`. Nothing else: no scheduler, cron,
  paused state, work-order setting, table, bucket, dataset, deploy IAM, WIF, secret,
  monitoring or service-account change.

## 19–24. Live state (read-only audit after the plan)

- **No apply:** prod state serial still 8, bootstrap still 9; all seven jobs at
  generation 5 on `sha256:f452c801…e100`.
- **Scheduler:** PAUSED, `0 * * * *`, no attempt time.
- **Control record:** present, `enabled: false`, generation `1791432826876698`
  (unchanged since it was disabled).
- **Cloud Run executions:** 14.
- **Secrets and keys:** 0 versions; 0 user-managed keys. No IAM, schema, alert or
  scheduler drift; buckets and row counts unchanged.
- **BigQuery quota:** unchanged default (209,715,200 MiB a day), no override.
- No source was called. C2-D has not begun.

## 25. Warnings and open questions

1. **The soak's request cap is now a real choice.** A cap of 14 for 14 targets leaves
   no retry: one transient failure means one article is not asked that day (the next
   day's window covers it). A cap of 22 restores full retry room. This checkpoint
   chooses neither.
2. **Reservation wastes retry room.** Unused attempts are not returned the same day.
   For 14 targets against 10,000 a day this is irrelevant; near a ceiling it reduces
   targets asked (visible in the 50,000-entity simulation).
3. **The sampled-source collector is not wired to a budget.** The primitive is in the
   shared client and ready, but `collect_samples` does not restrict or report it. It
   is inactive; wiring it belongs with activating any sampled source.
4. **A whole-fetch retry policy of four attempts** means one stubborn target can spend
   four units. That is bounded by the budget, but worth knowing when a cap is chosen.
5. **Not validated live**, by instruction. The proof is offline, against a counting
   transport.
6. The registry now also holds this undeployed image, beside one that must never be
   deployed (`sha256:1b373a41…`).
7. Still open from earlier: the 10,240 MiB daily query quota (hard gate before C2-D);
   the deferred freshness alert; the missed-tick review before any sampled source; the
   branch-protection decision before any secret-backed source; local Terraform one
   patch behind CI, so the plan was checked from the plan run's rendered output and
   its hash.

## 26. Exact next approval required

Approve or reject applying the saved plan, by its run id and hash, through the deploy
workflow:

- run `37734819174`
- SHA-256 `15a0c0599bdb0e053f501c1aa09c36868df2a18fe3fd128c40d5a0f0116af358`
- expected: 0 add, 7 change, 0 destroy, image `sha256:a8680b2b…c754`, scheduler still
  paused, before 2026-10-11T06:02:52Z

After that, and separately: the quota gate, the soak's control record (including its
request cap), and the scheduler-unpause plan for C2-D. None of those is approved here.

STOP. The plan is not applied. C2-D has not begun.
