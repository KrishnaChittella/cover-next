# Handoff: latest checkpoint

This file is the short-lived handoff from Claude Code to ChatGPT. It is overwritten at
each checkpoint; Git history is the archive. It supports communication only and
replaces no code, test, audit or document.

Previous checkpoint: **PHASE 4 PART C2-F: POST-SOAK ACCEPTANCE AUDIT — FINDINGS FOR
REVIEW** (`3b8befb`)

Current checkpoint: **PHASE 4 PART C2-F: POST-SOAK ACCEPTANCE AUDIT — MONITORING
INCIDENT EVIDENCE RECORDED**

**Recommendation: INDETERMINATE. C2-F is NOT accepted.**

- Criterion 9 fails: a real coverage gap.
- Criterion 10 fails as written: the volume alert fired.
- Criterion 11 is unresolved: the metric reported 32, but a recount from the
  execution records gives 26 for the same window. That gap is unexplained.
- Nothing was reconciled.

## Review status

The independent review of `3b8befb` ruled:

- **The branch:** this branch is the place for C2-F audit records. It is not to be
  merged or deployed. `main` and `product/local-ui` are not to be modified.
- **The record:** the audit record is accepted for review purposes. C2-F is not
  accepted.
- **Criterion 9:** a real coverage failure. The review independently confirmed that
  the collector's due-slot completion logic has no check that the due period is
  present.
- **Criteria 10 and 11:** unresolved, pending Cloud Monitoring evidence.
- **Not authorised:** waiving any criterion, the collector fix, backfills, warehouse
  jobs, Terraform, and reconciliation.

## How the evidence was collected

- **Owner approval:** a strictly read-only audit of `metaspike-prod-kc`, approved on
  2026-10-19.
- **Who ran what:** the owner ran every production command, one at a time, and pasted
  the output back (21 commands). Claude Code ran no production command. Its own
  commands were Git and reads of repository files.
- **Monitoring incident evidence (Option A):** the owner obtained it from Cloud
  Monitoring and transcribed it in section 2. **Claude Code has not seen the
  screenshot itself.** The values in section 2 are the owner's reading of it.
- **No changes were made:**
  - no job executed;
  - no Scheduler, Terraform, IAM or deploy action;
  - no source API contacted;
  - no secret payload read.
- **The audit's own footprint:**
  - read-only API calls;
  - one read of a Cloud Storage object, the control record;
  - four billed BigQuery SELECT jobs, about 90 MiB in total, and one refused query;
  - the gcloud `beta` component installed on the owner's machine.
- **Branches:** this branch, `ops/c2f-acceptance-review`, was cut from `main` at
  `cb33198e2510965e41dff0f5fe7e75cacb7ee2ca`. `main` and `product/local-ui`
  (`ec679aa`) are unchanged.

## 1. The fourteen criteria (section 31 of `docs/phase4-c2-plan.md`)

| # | Criterion | Verdict | Evidence |
|---|---|---|---|
| 1 | 168 dispatcher executions from 168 ticks | **PASS** | 168 scheduler attempts, each HTTP 200 once; 168 `ms-dispatch` executions, one per hour from 12 Oct 00:00 to 18 Oct 23:00, all succeeded |
| 2 | No attempt or execution after the 18 Oct 23:00 tick | **PASS** | Last attempt 23:00:38; last execution `ms-dispatch-psqlh`, completed 23:02:14; next scheduled run 2027-10-12T00:00:03Z |
| 3 | No wake-up outside the window, no refusal inside | **PASS** | No attempt or execution outside 12–18 Oct; no `tick_refused` or `job_refused` (both WARNING-level) |
| 4 | Wikimedia only | **PASS** | Only `ms-dispatch`, `ms-wikimedia` and `ms-warehouse` ran; the ledger planned 98 targets, all `wikimedia_pageviews`; 0 Steam samples, 0 catalogue records, 0 secret versions |
| 5 | 14 requests a day, 98 in all (>22 review, >44 incident) | **PASS** | 14 every day, 98 in all, in both the collector logs and `collection_runs`; budget 22; 0 retries |
| 6 | Seven collections, 14 of 14, no 401/403/429 | **PASS, qualified** | Seven runs `succeeded`, each 14 of 14 collected and 0 failed; no `source_refused` or `source_failed`. **But** the 18 Oct run's due slot (16 Oct) was absent for every article and still counted as collected (see 9) |
| 7 | Monday 14-day window, other six days 4-day | **PASS** | `window_days` 14 on Monday (slot 10 Oct), 4 on the other days (slots 11–16 Oct) |
| 8 | Seven warehouse runs, new batches only, dbt passing | **PASS, dbt inferred** | Seven runs, 0 incomplete batches, each loading only new batches; Monday rebuilt periods from 27 Sep, the designed late-data path (`late_data_loaded`, the soak's only warning). dbt passing is inferred: `job_finished` is written only after dbt exits 0, and there was no `warehouse_failed`. dbt's test counts were not observed |
| 9 | No duplicates, full coverage, counts reconcile | **FAIL** (confirmed by review) | 0 duplicate logical keys in the raw and staging tables; 518 rows = 518 rows = 196 + 5×56 + 42. **16 October is missing for all 14 articles** |
| 10 | No silence incident, no volume alert, failure alerts explained | **FAIL as written** | **A volume alert fired:** policy "MetaSpike: unusually many job executions in a day", observed value 32 against a threshold of 31, opened about 01:07 UTC on 16 Oct and closed after 1 h 58 m 46 s (section 2). **Other alert records exist:** the closed-alert list shows seven alerts opened on 15 Oct CDT, plus older incidents; their policies and triggering values are not all verified. No silence incident: the silence policy is disabled, as designed. No failure events. Ten policies match the approved definitions, and none changed after the launch apply |
| 11 | ≤ 189 executions, ≤ 31 completed in any rolling 24 h | **Total PASS. Rolling 24 h UNRESOLVED** | 182 executions in the soak. The metric reported **32** (data point 16 Oct 00:00 UTC). The execution records give **26** for the 24 hours ending at that point and **≤ 28** for any 24 hours. Not reconciled (section 3) |
| 12 | About $0.30; BigQuery < 2 GiB a day | **BigQuery PASS. Cost NOT VERIFIED** | 0.3027 GiB billed on each soak day, 2.12 GiB in all, about $0.013. The execution count matches the projection exactly. There is no billing export, and the invoice was not seen |
| 13 | Security unchanged | **PASS** | 0 secret versions across 3 secrets; 0 user-managed keys across 10 service accounts; no Admin Activity entry after the apply's last write at 02:20:16 on 11 Oct (the same filter shows the apply's own writes, so it does catch changes); one image digest in every run, `…49b836bdb979c754`, as at launch |
| 14 | Scheduler paused, no window; control record disabled | **NOT YET DONE** (expected) | Scheduler ENABLED, `0 * 12-18 10 *`, `userUpdateTime` 02:20:16 on 11 Oct. Control record generation `1791684706340016`, unchanged, `enabled: true`, and expired since 23:30 on 18 Oct, so it fails closed |

## 2. The 16 October volume alert

**Incident evidence.** Owner-obtained from Cloud Monitoring and transcribed by the
owner; not seen first-hand by Claude Code.

| | |
|---|---|
| Policy | MetaSpike: unusually many job executions in a day |
| Condition | more than 31 completed executions in 24 hours |
| Observed value | **32** |
| Threshold | 31 |
| Data-point timestamp | 15 Oct 2026 6:00 PM UTC−6, which is **16 Oct 00:00 UTC** |
| Incident opened | **about 16 Oct 01:07 UTC**, about 67 minutes after the data point's timestamp. These are two different times and are not to be conflated |
| Duration | 1 h 58 m 46 s, so closed about 03:06 UTC on 16 Oct |
| Status | closed |

**Ruled out by evidence:**

| Hypothesis | Evidence |
|---|---|
| H1: children above the daily cap | Exactly 2 children every day |
| H2: executions not started by the dispatcher | Exactly the expected 182 executions; all seven jobs exist only in `us-central1` |
| H4: a scheduler retry or duplicate delivery | 168 attempts, one each, all HTTP 200, no ERROR |
| H5: retries counted as executions | `retriedCount` empty on every execution |
| A misconfigured policy | `completed_execution_count` over all `cloud_run_job`, `ALIGN_SUM` over 86,400 s, `REDUCE_SUM`, `> 31`, duration 0; last changed by the launch apply |

**Closed-alert list.** This is a second owner-provided screenshot, recorded here as
described in the review. Claude Code has not seen it first-hand.

- It shows **seven separate alerts opened on 15 October CDT**, plus older incidents.
- It establishes that **other alert records exist** besides the incident above.
- It does **not** verify the policy of each alert, or the value that triggered it.
  Those remain unverified.

**What remains open.** Cloud Monitoring aggregation (H3) is **one unconfirmed
possibility** for the 32-against-26 discrepancy. It is **not** the only remaining
explanation. Nothing so far rules out:

- how the incident's value is interpreted, including how the incident chart presents
  the aligned value;
- how the 86,400 s alignment and the evaluation time line up with the UTC−6 display;
- an error in the audit's own reconstruction.

H1, H2, H4 and H5 are ruled out only as **execution** explanations. They do not
settle how the metric arrived at 32. The incident is **not** classified as a false
positive, and excess executions are **not** assumed.

## 3. Execution-count reconciliation

The metric's aggregated value and the counts rebuilt from execution records are kept
apart.

**Counts rebuilt from execution records**, using the completion times read by the
owner in the audit:

| Window | Completed executions |
|---|---|
| The 24 h ending 16 Oct 00:00 UTC (the data point) | **26**: 24 dispatchers + 2 children (15 Oct) |
| Calendar day 15 Oct UTC | 26 |
| The 24 h ending 01:07, 02:00 and 03:06 UTC on 16 Oct (while the incident was open) | 26 each |
| Any 24 h in the soak | ≤ 28 |

**Metric value, from the incident:** **32** at 16 Oct 00:00 UTC. That is **6 more**
than the records give for the same 24 hours.

**What could produce 32.** These are constraints on two of the possibilities, not
findings, and the list is not exhaustive (section 2):

- **A longer window.** A lookback ending at 16 Oct 00:00 contains exactly 32
  executions only if it starts between 14 Oct 17:02:51 and 18:01:47 UTC, a span of
  about 30 to 31 hours. Shorter windows give 28 to 31; longer ones give 33 or more.
- **But a sliding window of that length doesn't fit the recovery.** A 30-hour window
  would still hold about 33 executions at 03:06 UTC, when the incident closed.
- **Or six extra or duplicated data points** inside a 24-hour window.
- Neither mechanism has evidence, and other explanations are not excluded. The
  metric's own per-job series would help to tell them apart.

**Other counts:**

| Source | Count |
|---|---|
| Scheduler attempts | 168 |
| `ms-dispatch` executions | 168 |
| `ms-wikimedia` + `ms-warehouse` executions | 7 + 7 |
| **Soak total** (ceiling 189) | **182**, exactly section 28's projection B |
| Project total (35 before the soak + 182; limit 224) | 217 |
| `ms-dispatch` rows in `ms_ops.collection_runs` | 149 |

The missing 19 rows (168 − 149) are the dispatchers of 05:00–23:00 on 18 Oct. They ran
after the last warehouse load, so their run-log batches have landed but are not yet
loaded. This is inferred; the landing bucket was not listed.

## 4. The 16 October coverage gap (criterion 9)

**What happened:**
- The 18 Oct collection asked for slot 16 Oct with a 4-day window.
- It landed 42 rows, covering 13–15 Oct only, where 56 were expected.
- 16 Oct appears in no soak collection, because the 17 Oct window ended on 15 Oct.
- The warehouse loaded everything it was given: 106 rows that day, against 120 on the
  other days.

**The cause in the code:** `src/metaspike/jobs/collect.py:295-316`.
- A target counts as collected, and its due slot is marked complete, as soon as the
  response holds any rows.
- Nothing checks that the due period itself is present.
- So the gap raised no warning and no alert.
- The review has independently confirmed this.

**NOT VERIFIED:** why 16 Oct was absent. The likeliest explanation is that Wikimedia
had not yet published that day at 03:02 on 18 Oct. Confirming it needs the raw
response, and no source was contacted.

## 5. Blockers and uncertainties

1. **The metric is 6 above the records.** For the 16 Oct 00:00 UTC data point, the
   metric gives 32 and the execution records give 26. The cause is unknown.
   Aggregation (H3) is one unconfirmed possibility; others are not excluded
   (section 2).
2. **Other alert records exist.** The closed-alert list shows seven alerts opened on
   15 Oct CDT, plus older incidents. Not yet verified:
   - each alert's policy;
   - each alert's triggering value;
   - each alert's open and close times;
   - each alert's notification count.
3. **Monitoring evidence not yet provided:**
   - the details of those alerts;
   - the number of notifications for the 16 Oct incident;
   - the metric's own series: Metrics Explorer, `completed_execution_count`, 14–16 Oct,
     grouped by `job_name`, one-hour sum.
4. **The collector defect:** a due slot is marked complete without its due period
   (criterion 9).
5. **The cost invoice was not seen.**
6. **dbt test counts are inferred.**
7. **19 run-log batches** are inferred to be waiting to be loaded.

## 6. Post-soak cleanup plan (separately gated; nothing here was done)

Each step needs its own owner approval and review.

1. **Disable the control record.** One write, conditional on generation
   `1791684706340016`, then read it back.
2. **Reconciliation:**
   - set `SCHEDULER_PAUSED=true`;
   - delete `SOAK_MONTH`, `SOAK_START_DAY`, `SOAK_END_DAY` and
     `SOAK_DAILY_EXECUTION_CEILING`;
   - make one infrastructure plan, review it, then apply it;
   - verify: the scheduler is PAUSED with `0 * * * *` and no window, and the volume
     threshold is back to 60.
3. **Load the waiting run-log batches.** One supervised warehouse execution for the 19
   batches, as in the 11 Oct catch-up.
4. **Collector fix.** Not authorised yet. When it is:
   - mark a slot complete only when its due period is present;
   - warn and count when it is absent;
   - add tests.
   
   A backfill of 16 Oct needs a supervised live Wikimedia collection, and so its own
   approval.
5. **The volume alert's definition.** Revisit it once H3 is resolved, as a reviewed
   infrastructure change.

## Exact next approval required

1. **Owner:** the remaining Monitoring evidence in section 5, items 2 and 3. Optionally, the
   billing figure for 12–18 Oct.
2. **ChatGPT:** review of this update, including the 26-against-32 reconciliation for
   criterion 11.
3. **Each cleanup step in section 6,** approved separately.

STOP. C2-F is not accepted. Nothing is reconciled and nothing is to be triggered.
