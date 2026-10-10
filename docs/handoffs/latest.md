# Handoff: latest checkpoint

This file is the short-lived handoff from Claude Code to ChatGPT. It is overwritten at
each checkpoint; Git history is the archive. It supports communication only and
replaces no code, test, audit or document.

Previous checkpoint: **PHASE 4 PART C2-D: CLOSED — FUNCTIONAL PASS / EXECUTION CEILING FAIL**

Current checkpoint: **PHASE 4 PART C2-E: REVIEW COMPLETE — CHATGPT APPROVAL REQUIRED**

A read-only review made on 2026-10-10 from execution records, logs, Scheduler logs,
BigQuery job records and bucket contents. Nothing was run, changed, unpaused or armed.
**No source was called.** The permanent version is section 27 of
`docs/phase4-c2-plan.md`. C2-D's ruling is unchanged: functional pass, execution
ceiling failed by five (34 against 29).

## Three things that need a decision

1. **Cloud Run billing is still unsettled, and it matters at scale.** An execution
   exists for about two minutes before its container starts. If that time is billed,
   the broad-catalogue projections roughly triple, and 50,000 catalogued crosses $20.
   Only invoice lines can settle it; there is no billing export.
2. **Expiry stop: a finite schedule is recommended** (no new permission, code or
   drift).
3. **Docker Hub: take the same pinned digest from Google's public mirror first** is
   recommended (no secret, no gate skipped).

## Safe start state, confirmed at 02:24 UTC

Scheduler PAUSED, `0 * * * *`, `Etc/UTC`; control disabled (generation unchanged);
`dispatcher_silent` disabled, the other seven policies enabled; prod state serial 11;
34 executions; all seven jobs on `sha256:a8680b2b…c754`; 0 secret versions; 0
user-managed keys; quota 10,240 MiB a day; `SCHEDULER_PAUSED` = `true`. It was the same
at the end.

## Execution latency

C2-D dispatcher, 18 executions, seconds:

| | min | median | p90 | max |
|---|---|---|---|---|
| On the hour to created (Scheduler delivery) | 1.1 | 6.7 | 42 | 52.3 |
| Created to execution start | 9.5 | 16.9 | 20.9 | 51.9 |
| **Created to container start** | **37.0** | **128.2** | **185.2** | **244.3** |
| Container start to first log line | 2.1 | 4.0 | 9.9 | 15.0 |
| First to last log line (the work) | 0.3 | 0.4 | 1.3 | 2.4 |
| Container start to exit | 5.5 | 8.9 | 12.1 | 18.2 |
| Created to completed (lifetime) | 77.1 | 140.0 | 194.8 | 264.8 |

| One execution each, no distribution | Created to container start | Start to first log | Work | Container | Lifetime |
|---|---|---|---|---|---|
| Wikimedia collector | 129.3 | 3.1 | 4.5 | 9.5 | 141.4 |
| Warehouse | 179.0 | 3.9 | 109.9 | 118.9 | 301.2 |

All 20 soak executions succeeded on the first attempt; task retries 0. Across all 34
executions since C1, created-to-container-start is 37 to 245 s, median 134, p90 208.

**The start-up delay.** It is spent before the container starts. It is not application
initialisation (2 to 15 s), not logging delay (entries received at most 0.34 s after
being written), and not the recorded image import (1 to 35 s). It is Cloud Run's own
provisioning. Whether that is instance scheduling or fetching the 226 MB image into the
second-generation sandbox **cannot be distinguished with current telemetry**. No
runtime setting was changed.

## Reliability: what C2-D proved

| | Evidence |
|---|---|
| Tick delivery | 18 of 18 hourly ticks, none missed, none doubled |
| Scheduler retries | 0; 18 attempts, 18 HTTP 200 |
| Dispatcher | 18 of 18 succeeded; 6 ran a tick, 12 refused `control_expired` |
| Child starts | 2 of 2; 0 after expiry |
| Work order | 1, 14 targets, budget 22, honoured; none after expiry |
| Receipt folding | Folded by the next tick; no pending order, empty backlog |
| Completion markers | 14 of 14; nothing refetched |
| Request budget | 22 reserved, 14 physical attempts |
| Retries | 0 |
| Breaker and health | Closed and healthy; never exercised by a real failure |
| Control expiry | 12 consecutive refusals from the first tick after 08:30 |
| Warehouse idempotence | 8 batches loaded, 11 recognised as already loaded, 0 incomplete |
| dbt | PASS=28, WARN 0, ERROR 0 |
| BigQuery loads | 10 of 10; 0 failed jobs on the day |
| Source availability | 14 of 14 |

Wikimedia: planned 14, attempted 14, collected 14, physical attempts 14, retries 0,
HTTP or source errors 0, 429s 0, missing 0, duplicates 0, unexpected refetches 0.

**Not proven**, because it never happened: a failed execution and its recovery, a
breaker opening on a real source, a missed tick, a rate limit, a weekly reconciliation
run, consecutive collection days. One collection and one build are a demonstration,
not a rate.

**Monitoring.** It behaved as built and showed two weaknesses. The silence alert fired
at launch and again after the pause, both artifacts of a finite soak. The
execution-count alert's threshold is 60 a day, so it could not see a ceiling of 29
being passed. The second silence incident (opened 00:07:08 UTC on 10 October) had no
auto-resolve event in the logs when they were read at about 02:30; it was not touched.

**Kept separate:** executions 34 against a ceiling of 29, failed by 5. Not a clean pass.

## Measured cost of C2-D

No invoice line can be read. Durations, bytes and counts are measured; dollars are list
prices applied to them.

| Item | Measured | At list price | Class |
|---|---|---|---|
| Cloud Run, 20 executions | 303 s container time; 3,109 s lifetime | $0.006, $0.02 or $0.06 depending on the billing rule | ESTIMATED from measured time; free-tier absorbed |
| Cloud Scheduler | 1 job, 18 attempts | $0.003 | free-tier absorbed |
| BigQuery queries | 220 MiB warehouse + 140 MiB owner audit | $0.002 | ACTUAL bytes; free-tier absorbed |
| BigQuery storage | 0.5 MB | negligible | NEGLIGIBLE |
| Cloud Storage | 0.7 MB landed, 0.07 MB operational | negligible | NEGLIGIBLE |
| Artifact Registry | 11 images, at most 2.1 GB | at most $0.005 a day | ESTIMATED |
| Logging | 1.3 MB ingested | negligible | NEGLIGIBLE |
| Monitoring | 8 alert policies | not measured | UNKNOWN |

Total: at most about seven cents, most likely nothing. The five excess executions cost
between a tenth of a cent and two cents.

The billable-time metric shows 181 s for the 18 dispatchers, which matches container
time. But it shows 0.1 s for a 9.5 s collector and 72 s for a 119 s warehouse run, so
it is incomplete and not authoritative.

Against the earlier model: dispatcher 60 s assumed, 9 s container and 140 s lifetime
measured; warehouse 65 s assumed, 119 s measured (63, 80, 110 over three runs, cause
not explainable from three samples); daily build 130 MiB assumed, 220 MiB measured
(C2 added the ledger models); registry 1.4 GB assumed, at most 2.1 GB. None of these
moves the total by a cent. The start-up time is the only discrepancy that matters.

Not checked: Google's announced per-condition charge for alert policies. If in force,
eight policies are of the order of a dollar a month.

## Projections

Three readings of Cloud Run billing: **low** (container time), **middle** (one-minute
minimum), **conservative** (work plus 208 s, the p90 start-up). Decide on the
conservative figure without allowances. Each includes $0.16 a month for the registry
and excludes the unverified alerting charge.

| Configuration | Low | Middle | Conservative | Conservative, after allowances | Verdict (conservative) |
|---|---|---|---|---|---|
| A. 14 articles, hourly, one build a day, per month | $0.45 | $1.13 | **$3.54** | $0.16 | below the preferred range |
| B. Seven-day C2-F soak, 182 executions | $0.07 | $0.23 | **$0.79** | $0 | negligible |
| C. 1,000 catalogued | $3.09 | $4.28 | **$11.77** | $5.92 | acceptable with scrutiny |
| C. 5,000 | $4.03 | $4.69 | **$12.95** | $6.98 | acceptable with scrutiny |
| C. 10,000 | $5.10 | $5.76 | **$14.26** | $8.16 | acceptable with scrutiny |
| C. 50,000 | $10.90 | $11.55 | **$21.47** | $14.53 | **ABOVE $20. NOT APPROVED** |

The C rows are the existing quiet-day simulations: adaptive tiers decide what is due,
not every entity is fetched every hour, and they include a stand-in sampled source that
is not approved. They show the shape of the cost only.

Under the conservative reading cost follows the **number of executions**, not requests
or catalogue size: tenfold the requests from 1,000 to 10,000 adds a fifth to the bill.
The simulated configuration starts 72 to 88 executions a day, 23 of them warehouse
builds. If that reading is true, the levers are fewer builds and fewer, larger
collector executions. Nothing is decided.

## BigQuery

Verified from job records for 2026-10-09: warehouse identity 70 jobs, 46 top-level
(36 queries, 10 loads), 24 children, **220 MiB billed, 59,775 slot-ms**. The previous
day was identical.

- 0.2 MiB was actually processed. The bill is 13 statements at the 10 MiB minimum per
  table read. Billed bytes follow statement count, not data.
- Staging models 120 MiB, daily marts 40 MiB, five tests 60 MiB.
- 220 MiB a day; 1.5 GiB over seven days; 6.4 GiB a month; $0.04 at list price.
- The 10 GiB daily quota is 2 percent used (3.5 with audit queries). Still
  appropriately conservative. Not changed.
- Scans are not a scaling concern. Build frequency is: 23 builds a day would bill about
  4 GiB a day, 40 percent of the quota.

## The 32 pending run-log rows

Still present and untouched: 36 dispatcher run rows landed for 9 October, 4 loaded,
32 not. No warehouse run was made.

A normal run on 9, 10 or 11 October UTC loads them automatically. From 12 October the
ordinary three-day window no longer reaches that folder, and the run needs
`METASPIKE_LOAD_DAYS` set wide enough, an execution-time setting on a supervised run
(a dispatcher-started run uses three). Since C2-F cannot start before its hardening,
the wider window is the likely path.

## Expiry stop: options

| | 1. Finite schedule | 2. Dispatcher pauses its trigger | 3. Separate one-shot stopper | 4. Self-renewing chain |
|---|---|---|---|---|
| New permission | None | Pause, project-wide | Pause, project-wide, new identity | Enqueue |
| Depends on owner's machine | No | No | No | No |
| Deterministic stop | Yes, fixed at review | After one refusal | At a set time, if its call succeeds | Yes |
| Terraform drift | None | **Yes, by design** | **Yes, by design** | Replaces the trigger |
| Re-arm | New reviewed plan | Reviewed unpause plan | Reviewed plan, two schedules | Manual restart |
| Leaves production disabled by accident | No | Possible in steady state | Possible | Likely, on any failure |
| During an API failure | Nothing needs to succeed | Retries next tick | Runs on if retries fail | Chain breaks |
| Cost and complexity | None; two variables, one test | New code and failure branch | New job and identity | High |

**Recommended: option 1.** The cron expression names the soak's days (for example
`0 * 12-18 10 *`), set by the same reviewed plan that unpauses. No permission, identity,
code or drift, and the stop needs nothing to succeed when it happens. Limits: the
window must sit inside one calendar month; day granularity; it is a second clock beside
`armed_until` (disagreement is still fail-closed); a window left in place repeats a
year later against an expired control record. Options 2 and 3 need a project-wide
Scheduler permission and make Terraform disagree with the live system. What stops an
indefinite steady-state schedule belongs to C2-G. Nothing is implemented.

## Docker Hub: options

| Option | Weakens verification | Secret | Complexity | Dependency afterwards |
|---|---|---|---|---|
| A. Skip the rebuild for infrastructure-only applies | **Yes** | No | Low | Removed for those applies only |
| B. Same digest from another registry, Docker Hub as fallback | No | No | Low | Google's public mirror, then Docker Hub |
| C. Private copy of the base in Artifact Registry | No | No | Higher; brings cloud credentials nearer the test job | None at build time |
| D. Authenticated Docker Hub pulls | No | **Yes** | Low | Docker Hub |
| E. Retry with backoff | No | No | Trivial | Unchanged |

Checked without credentials: Google's public mirror serves the pinned base digest
today.

**Recommended: option B.** The digest in the Dockerfile stays the trust anchor; the
registry is only transport. No secret, permission or new resource, no gate skipped,
and it helps push CI too. Residual risk: the mirror is a cache. Option C is the
stronger second step if needed. A is not recommended while a fix exists that skips
nothing; D is rejected. Nothing is implemented.

## C2-F entry gates

1. Expiry stop implemented, reviewed and tested; a test fails if the unpaused schedule
   is not finite.
2. The unpause plan shows a tick count known in advance; the execution ceiling is
   derived from it.
3. Zero dispatcher executions after the window without a new reviewed plan.
4. Source fail-closed behaviour unchanged; its tests pass.
5. Request budgets unchanged.
6. Sampled collector inactive; no sampled source enabled.
7. Scheduler configuration reviewed in the plan output.
8. Monitoring fits a finite soak: no silence alert before the first tick or after the
   end; execution-count threshold consistent with the ceiling.
9. Alerting charge read from the pricing page; conservative projection under $1 for
   the soak and under $10 a month.
10. No plan with a destroy; state matches live.
11. Security clean: 0 secret versions, 0 keys, no unreviewed IAM or WIF change,
    approved image.
12. The deploy path no longer fails on a Docker Hub rate limit, or that risk is
    accepted explicitly.
13. The 32 pending rows accounted for in the first warehouse run's plan.
14. Wikimedia only, 14 articles, tiering shadow only.

**C2-F: BLOCKED** pending the expiry-stop implementation (gates 1 to 3) and the
monitoring adjustment (gate 8). No launch time is chosen.

## Confirmations

- Scheduler remained PAUSED and the control record DISABLED throughout.
- Source calls during C2-E: 0. Cloud Run executions: 0 new (still 34).
- No infrastructure, quota, IAM, image or data change. No warehouse run.
- One anonymous read of a public registry manifest was made to check the mirror.
- Documents changed: `docs/phase4-c2-plan.md` (new section 27),
  `docs/phase4-runbook.md` (one measured note), this file. No code. Local tests:
  1,160 passed. The portfolio repository was not touched.

## Exact next approval required

Approval of this review, and rulings on: the expiry-stop option; the Docker Hub option;
whether to set up a billing export so the Cloud Run billing rule can be settled before
any widening of scope.

STOP. C2-F has not begun.
