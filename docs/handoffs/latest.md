# Handoff: latest checkpoint

This file is the short-lived handoff from Claude Code to ChatGPT. It is overwritten at
each checkpoint; Git history is the archive. It supports communication only and
replaces no code, test, audit or document.

Previous accepted checkpoint: **PHASE 4 PART C2-B: COMPLETE**

Current checkpoint: **PHASE 4 PART C2-C: COMPLETE**

C2-C was **resumed, not restarted**. Its first half (the 03:00 UTC dispatcher tick and
the Wikimedia collection it started) ran earlier and was accepted; nothing in it was
repeated. This report adds the second half (the 04:00 UTC dispatcher tick and the
warehouse run it started), the audits, and the disabling of the control record. Every
item of the completion gate is met. Automation is **not** unattended: the scheduler is
paused and has never fired, and the control record is disabled.

All times are UTC on 2026-10-08.

## Baseline before C2-C

- Deployed image on all seven jobs:
  `sha256:f452c80108ac0ab8fd8d7e2d4b9d0da931686ed2ed6d3454f683b016c943e100`. Only
  `ms-wikimedia` carries `METASPIKE_WORK_ORDERS=required`.
- Scheduler `0 * * * *`, PAUSED, no attempt time. 10 Cloud Run executions.
- 0 secret versions; 0 user-managed keys; no IAM, schema, alert or scheduler drift.
- No control record, nothing under `dispatch/`, no work order. 14 bootstrap markers.
- Retained history: 1,260 observations, 2026-07-08 to 2026-10-05.
- Deployed configuration: period hour 3; hourly tick; Wikimedia the only configured
  source; work-order lifetime 50 minutes; steady window 4 days; reconciliation on
  Mondays (the test day was a Thursday); warehouse daily hour 4; shadow tiers off.

## Control record

Created once, create-only, from the repository's `ControlRecord` model at 03:14:17:

```
{"version":1,"enabled":true,"armed_until":"2026-10-08T05:00:00Z","max_job_starts_per_tick":1,"max_executions_per_day":2,"sources":{"wikimedia_pageviews":{"enabled":true,"max_requests_per_day":14,"max_targets_per_execution":14}},"pins":[]}
```

SHA-256 `d54226c706d6ca80bd670e93c4b6e315306eb722451cebb06ad19ec62378d607`, generation
`1791429257847245`. Read back and verified exactly before any execution.

**Disabled after the warehouse finished**, at 04:13:48, by replacement on that exact
generation. Only `enabled` changed; the expiry, caps and source settings were kept as
audit context:

```
{"version":1,"enabled":false,"armed_until":"2026-10-08T05:00:00Z","max_job_starts_per_tick":1,"max_executions_per_day":2,"sources":{"wikimedia_pageviews":{"enabled":true,"max_requests_per_day":14,"max_targets_per_execution":14}},"pins":[]}
```

- Previous generation `1791429257847245`; new generation `1791432826876698`.
- Payload SHA-256 `a7749cad04d87debcf212a9dcb5f0d28d2c2022474f600b410d3b12545f7f5eb`,
  confirmed by exact-name read back.
- The repository's own reader returns the refusal `control_disabled` for these bytes.
  No dispatcher was run to prove it.

## First half (accepted earlier; not repeated)

**Dispatcher `ms-dispatch-8ltv9`**, run id `20261008T031812Z-ms-dispatch-8ltv9`, started
by hand with no override, one attempt, exit 0. Tick `2026-10-08T03:00:00Z`. It accepted
the control record; generated schedule `s9939a2accd9a17c152bc` from the bootstrap
targets (14 catalogued, monitored and eligible; one period cohort); read 1 partition;
planned 14 targets; issued one work order; and started `ms-wikimedia` only.

**Work order `34e4f8be49cf8a61e3709cc9c36cc478`**: Wikimedia pageviews, slot
2026-10-06, `window_days` 4, 14 targets, `max_requests` 22, expiring 03:50. The
`CURRENT` pointer's hash matches the order's bytes.

**Collector `ms-wikimedia-mld5x`**, run id `20261008T031955Z-ms-wikimedia-mld5x`,
created by `ms-dispatch`, one attempt, exit 0. It claimed the order by exact name (so
the work-order read grant works live), sent 14 requests, collected 14 targets with 0
failed and 0 skipped, used a 4-day window with 0 history fills and 0 deferred, and
landed 56 rows (14 articles by 4 days, 2026-10-03 to 2026-10-06). No 90-day refetch.
Receipt: `succeeded`, 14 requests, completed 03:19:59. 14 completion markers written
for the slot; bootstrap markers still 14.

## Second dispatcher (04:00 tick)

Pre-resume check at 04:04, all 20 points matching: scheduler PAUSED with no attempt; 12
executions; control enabled, unexpired, Wikimedia only, caps 1 and 2; receipt present
and `succeeded` for the order above; `warehouse.due_since` 03:00 and `last_started`
null; no 04:00 plan or lease; no warehouse or unexpected collector execution; 0 secret
versions; no drift.

- Execution `ms-dispatch-x2cgd`, run id `20261008T040826Z-ms-dispatch-x2cgd`, started by
  hand with no override. Identity `ms-dispatch`, same image, 1 vCPU, 512Mi. One
  attempt, exit 0.
- Tick `2026-10-08T04:00:00Z`. Partitions read 0; expected targets 0; no dispositions;
  **no work order**; schedule not regenerated.
- **Jobs started: `ms-warehouse` only.** Executions counted today: 2, the control
  record's ceiling. Requests counted for Wikimedia: still 14.
- The receipt was folded into source health: before, the order was pending with no
  attempt recorded; after, `last_attempt` and `last_success` 03:19:59, 0 consecutive
  failures, breaker closed, no pending order, last outcome `succeeded`. Source state
  healthy.
- Persisted state: plan and lease for `tick-20261008T0400Z`; backlog empty;
  `warehouse.json` now `due_since` null and `last_started` 04:00.
- The work-order area still holds exactly the one order and its pointer.

## Warehouse (started by the dispatcher)

- Execution `ms-warehouse-c28jj`, run id `20261008T041140Z-ms-warehouse-c28jj`,
  **created by `ms-dispatch`**. Identity `ms-warehouse`, args `['warehouse']`, same
  image, 1 vCPU, 1Gi. One attempt, exit 0.
- Batches: 8 loaded, 3 already loaded (the three from the first supervised collection
  on 7 October), 0 incomplete, 0 recovered. Rows loaded: 76.

| Table | Batches | Rows |
|---|---|---|
| `ms_raw.period_observations` | 1 | 56 |
| `ms_ops.expected_targets` | 1 | 14 |
| `ms_ops.collection_runs` | 6 | 6 |

- Deterministic load jobs, all DONE:
  `ms_load_period_observations_027f5662e219196b0c3c1d7b9200c16ba9906c15_a0`,
  `ms_load_expected_targets_4499e793fb6e30d44f7f8c96cbaec4210edf0a19_a0`, and six
  `ms_load_collection_runs_…_a0` (`c787add8…`, `2be4ca97…`, `42a9e6d7…`, `2d33ae3e…`,
  `b2fdbdf7…`, `31813228…`). Plus two direct run-log loads for the warehouse job
  itself. Eight new receipts were written, one per batch.
- **Late data: none.** `rebuilt_from` is empty, no `late_data_loaded` event was logged,
  and the pending-rebuild record is empty. The four-day overlap (earliest day 3
  October) was ordinary work under the seven-day period window.
- **No full refresh.** Selector `daily` (the 04:00 run builds everything), incremental.
- **dbt:** 1.11.15, 9 models and 19 tests: PASS=28, WARN=0, ERROR=0, SKIP=0.

## Ledger proof

`ms_ops.expected_targets` holds 14 rows, one per article, loaded through BigQuery with
the six new columns populated on every row (0 missing):

| Column | Value on all 14 rows |
|---|---|
| `tier` | 4 |
| `due_at` | 2026-10-08 03:00:00 |
| `due_reason` | `default_long_tail` |
| `policy_status` | `cleared:2026-10-04:c363edef976a` |
| `disposition` | `planned` |
| `work_order_id` | `34e4f8be49cf8a61e3709cc9c36cc478` |

Slot 2026-10-06, source `wikimedia_pageviews`, mode `fetch_history`. No observation
row has a `not_sampled` status (0 found). `stg_expected_targets` has 14 rows.

## Data after processing

| | Before C2-C | After |
|---|---|---|
| `ms_raw.period_observations` | 1,260 | 1,316 |
| `ms_staging.stg_period_observations` | 1,260 | 1,316 |
| `ms_ops.expected_targets` | 0 | 14 |
| `ms_ops.collection_runs` | 4 | 12 |
| `ms_staging.stg_collection_runs` | 3 | 11 |

- Period range in raw and staging: 2026-07-08 to 2026-10-06. Earlier it ended
  2026-10-05; one new day arrived.
- The first batch is intact: 1,260 rows, 2026-07-08 to 2026-10-05. 1,190 rows predate
  1 October in both raw and staging: no truncation.
- No exact duplicate keys in raw or staging (0).
- 42 logical periods (14 articles by the three overlapping days) now have two versions
  each, as the model intends for a re-observed period. **All 42 agree with the earlier
  values; none was revised.**
- 14 articles; no negative, null or future values; values from 290 to 96,567.
- Mart tables remain empty: they are built from sample attempts, of which there are
  none.

## Timing

| | Dispatcher 03:00 | Wikimedia | Dispatcher 04:00 | Warehouse |
|---|---|---|---|---|
| Execution | `ms-dispatch-8ltv9` | `ms-wikimedia-mld5x` | `ms-dispatch-x2cgd` | `ms-warehouse-c28jj` |
| Created | 03:14:27 | 03:18:14 | 04:05:31 | 04:08:28 |
| Task start | 03:15:17 | 03:18:25 | 04:05:47 | 04:08:45 |
| First application log | 03:18:12 | 03:19:55 | 04:08:26 | 04:11:40 |
| Application finish | 03:18:14 | 03:19:59 | 04:08:28 | 04:13:00 |
| Platform completion | 03:18:17 | 03:20:04 | 04:08:33 | 04:13:09 |
| Wall, creation to completion | 3 min 51 s | 1 min 50 s | 3 min 3 s | 4 min 41 s |
| Platform-reported duration | 3 min 0 s | 1 min 39 s | 2 min 46 s | 4 min 24 s |
| Application work | 2.1 s | 4.3 s | 1.7 s | 80 s |
| Creation to first log | 3 min 45 s | 1 min 41 s | 2 min 55 s | 3 min 12 s |
| `billable_instance_time` recorded | 0.1 s | none recorded | 0.1 s | 60.2 s |
| vCPU / memory | 1 / 512Mi | 1 / 512Mi | 1 / 512Mi | 1 / 1Gi |
| Task attempts | 1 | 1 | 1 | 1 |

The long delay before the first log line persists with the 1.08 GB image: between
1 min 41 s and 3 min 45 s on all four, against seconds of real work on three of them.
The billable-time metric shows about 60 s for the warehouse and almost nothing for the
other three. That is consistent with the delay not being billed, but the metric is
visibly under-sampled for short executions (it recorded nothing at all for the
collector), so **this is not evidence that start-up is free.** It remains to be settled
against billing lines.

## Cost of this checkpoint

- **Cloud Run:** four executions, durations above.
- **BigQuery:** 70 jobs, 46 top level (10 loads, 8 scripts, 8 create-table statements,
  1 view, 19 tests). 174,678 bytes processed; **230,686,720 bytes (about 220 MiB)
  billed**; 60,207 slot-ms. No job errored. The first supervised run billed about
  136 MB: this one built against non-empty tables, and almost all of the billing is
  still the 10 MB per-statement minimum.
- **Cloud Storage added by C2-C:** landing 16 objects, 38,155 bytes (one observation
  batch, one ledger batch, six run-log parts, each with a manifest); operations bucket
  37 objects, about 28 KB (dispatcher state, the order and pointer, the receipt, 14
  completion markers, eight load receipts).
- **Wikimedia:** 14 requests. Response bytes are not logged: not measured.
- **Preliminary reading:** a full daily build costs about 0.2 GiB of billed query
  volume, so one a day is about 6.5 GiB a month, far inside the allowance and under
  the proposed 10 GiB daily quota by a wide margin. Nothing here changes the budget or
  the quota, and no wider extrapolation is made.

## Alerts and security

- **Alert incidents in the C2-C window: none.** The only incident on record is the S4
  synthetic one from 7 October, already closed. No warehouse-failure, rate-limit,
  job-failure or parity alert. Dispatcher silence is still disabled.
- No WARNING or ERROR log entry from any Cloud Run job in the window.
- Secret versions 0; Secret Manager access log entries in the window 0; user-managed
  keys 0.
- IAM, dataset access, bucket IAM, secrets, alert policies and the scheduler are
  unchanged against the pre-run snapshot; the only differences are the row metadata of
  the three tables that received data. Job templates unchanged. WIF provider ACTIVE and
  unchanged.
- Audit log for runtime identities in the window shows BigQuery calls by
  `ms-warehouse` only.
- Source contact: Wikimedia only, 14 requests. No Steam, IGDB, Twitch or YouTube. There
  is no packet-level egress logging; this rests on the command's code path, the
  request count, the absence of any secret version and of any secret access.

## Final state

- **Scheduler:** PAUSED, `0 * * * *`, no attempt time.
- **Control record:** present and **disabled**.
- **Cloud Run executions: 14.** C2-C added exactly four: two dispatchers started by
  hand, and one collector and one warehouse started by the dispatcher. No retry at
  task level, and no manual retry.
- **Automation state retained**, as legitimate private records: the schedule, both tick
  plans and leases, the order and pointer, the receipt, health, backlog, counters,
  `warehouse.json`, the ledger, the observations and the markers. Nothing was deleted.
- **Buckets:** landing 22 objects (569,417 bytes); operations 69 objects (43,144
  bytes); raw, state and tokens empty.
- **BigQuery quota:** unchanged, still the default, no override.
- **Git:** only this handoff file changes. No code, Terraform, IAM or secret change.
  The portfolio repository was not touched.

## Completion gate

| Requirement | Result |
|---|---|
| First dispatcher accepted the bounded control | yes |
| One work order issued for Wikimedia | yes |
| Dispatcher itself started the collector | yes |
| Work-order IAM worked live | yes |
| Collector used the 4-day window | yes |
| At most 14 requests | 14 |
| No 90-day refill | none |
| Source result landed; receipt recorded | yes |
| Second dispatcher folded the outcome | yes |
| Second dispatcher started only the warehouse | yes |
| Warehouse loaded the new batches | 8 batches, 76 rows |
| Ledger loaded with the six fields correct | 14 rows |
| Normal overlap not classified late | not late |
| dbt passed | PASS=28 |
| No duplicate or history regression | none |
| No unexpected source or secret use | none |
| Caps held | yes |
| Scheduler stayed PAUSED | yes |
| New executions | 4, total 14 |
| Control disabled afterwards | yes |

## Warnings

1. **The publish instruction midway was mistaken about the state.** It said C2-C was
   finished when only the first half had run. The checkpoint was reported as blocked
   and incomplete, then resumed on a later instruction. No step was repeated.
2. **A local wait command failed in zsh** and had to be stopped by the owner. It
   performed no cloud action. The 04:00 tick was launched at 04:05, inside its window.
3. **Start-up delay** of two to four minutes per execution is real and unexplained in
   billing terms (see Timing).
4. **The ledger slot sits close to the sample lookback.** A period source's ledger rows
   carry the source's period (two days back) and are grouped with the sample models,
   whose window is three days. It was not late here. A run delayed by more than a day
   could flag those ledger rows as late and trigger a rebuild of the sample models:
   harmless, but worth knowing before a soak.
5. **Overlap doubles rows by design.** Each daily run re-lands the three previous days;
   staging keeps both versions. Storage grows about fourfold per period until a model
   that picks the latest version is used downstream.
6. **Two `ms-dispatch` run-log rows per tick** will dominate `collection_runs` under an
   hourly tick (about 48 a day).
7. Still open from earlier: the 10 GiB daily query quota (a hard gate before C2-D); the
   deferred freshness alert; the missed-tick review before any sampled source; the
   branch-protection decision before any secret-backed source; local Terraform one
   patch behind CI.

## Exact next approval required

STOP. C2-D (the first unattended six-hour soak) has **not** begun and needs its own
approval. Before it: the BigQuery daily quota of 10,240 MiB must be set and read back
by the owner operation recorded in the runbook; a new bounded control record with an
expiry must be reviewed; and the single-change plan that unpauses the scheduler, with
its prepared re-pause plan, must be reviewed. Until then the scheduler stays paused and
the control record stays disabled.
