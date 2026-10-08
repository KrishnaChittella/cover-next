# Handoff: latest checkpoint

This file is the short-lived handoff from Claude Code to ChatGPT. It is overwritten at
each checkpoint; Git history is the archive. It supports communication only and
replaces no code, test, audit or document.

Previous accepted checkpoint: **PHASE 4 PART C2-B: COMPLETE**

Current checkpoint: **PHASE 4 PART C2-C: BLOCKED (incomplete, not failed)**

## Read this first

The instruction to publish said C2-C was already finished. **It was not.** When that
instruction arrived, only the first half of the supervised tick had run:

- done: the 03:00 UTC dispatcher tick, and the Wikimedia collection it started;
- **not done:** the 04:00 UTC dispatcher tick, the warehouse run it should start, the
  BigQuery/dbt/ledger-load proof, and the deliberate disabling of the control record.

Nothing in the first half failed. The checkpoint is reported as BLOCKED because the
completion gate cannot be met with half the path unexecuted, and because the
instruction forbade any further run or any change to the control record. Nothing has
been invented to fill the gap: every section below marked "not run" or "not measured"
is exactly that.

**One state needs a decision.** The control record is still `enabled: true` and is
armed until **2026-10-08T05:00:00Z**. It was not disabled, because the instruction
said not to alter it. The scheduler is PAUSED and has never fired, so nothing will
start the dispatcher on its own, and the record expires by itself at 05:00 UTC. Until
then a dispatcher started by hand would be permitted to work within the record's caps.

Last read-only check: 2026-10-08 03:27:54 UTC.

## Baseline before C2-C (all verified)

- Deployed image on all seven jobs:
  `sha256:f452c80108ac0ab8fd8d7e2d4b9d0da931686ed2ed6d3454f683b016c943e100`. Only
  `ms-wikimedia` carries `METASPIKE_WORK_ORDERS=required`.
- Git HEAD was one handoff-only commit ahead of `ab58078`; the only difference was
  `docs/handoffs/latest.md`.
- Scheduler `0 * * * *`, PAUSED, no attempt time. 10 Cloud Run executions.
- 0 secret versions; 0 user-managed keys; no IAM, schema, alert or scheduler drift.
- `dispatch/control.json` absent; nothing under `dispatch/`; no work order.
- 14 bootstrap markers present. Retained history unchanged (1,260 observations).
- Configuration confirmed from the deployed source: period hour 3 UTC; hourly tick;
  Wikimedia the only configured source; work-order lifetime 50 minutes; steady window
  4 days; reconciliation weekday Monday (the test day was a Thursday); warehouse daily
  hour 4; shadow tiers not applied.
- Timing gate: the first dispatcher was launched at about 03:14 UTC, inside the
  03:00 to 03:30 window.

## Control record

Created once, create-only (`if-generation-match=0`), from the repository's
`ControlRecord` model, at 03:14:17 UTC.

```
{"version":1,"enabled":true,"armed_until":"2026-10-08T05:00:00Z","max_job_starts_per_tick":1,"max_executions_per_day":2,"sources":{"wikimedia_pageviews":{"enabled":true,"max_requests_per_day":14,"max_targets_per_execution":14}},"pins":[]}
```

- SHA-256 `d54226c706d6ca80bd670e93c4b6e315306eb722451cebb06ad19ec62378d607`, 238 bytes.
- Generation `1791429257847245`, metageneration 1.
- Read back by exact name before any execution and verified: enabled, not expired,
  Wikimedia only, request cap 14, target cap 14, one job start per tick, two
  dispatcher-started executions a day, no pins.
- **Current state: unchanged and still enabled.** The planned disabling was not done.

## First dispatcher execution (03:00 UTC tick) — succeeded

- Execution `ms-dispatch-8ltv9`, run id `20261008T031812Z-ms-dispatch-8ltv9`, started
  by hand with no command or environment override. Identity `ms-dispatch`, args
  `['dispatch']`, 1 vCPU, 512Mi. One task attempt, exit 0.
- Tick `2026-10-08T03:00:00+00:00`. Control accepted.
- Schedule generated (none existed): version `s9939a2accd9a17c152bc`, from the
  bootstrap targets; catalogued 14, monitored 14, eligible 14, high frequency 0; one
  period cohort, `wikimedia_pageviews/period/c00`, with 14 entries, all tier 4 with no
  shadow proposal.
- Partitions read: 1. Expected targets: 14. Dispositions: `planned` 14.
- One work order: `34e4f8be49cf8a61e3709cc9c36cc478`.
- Jobs started: `ms-wikimedia` only. Executions counted today: 1. Requests counted
  for Wikimedia: 14.
- Source state: `wikimedia_pageviews` healthy.

Persisted state after it, all under the dispatcher's prefix in the operations bucket:
the schedule pointer, manifest and partition; the tick plan
`plans/tick-20261008T0300Z.json`; the lease `ticks/tick-20261008T0300Z.json`;
counters for 2026-10-08; health (order pending, expiring 03:50); an empty backlog;
`warehouse.json` with `due_since` 03:00 and `last_started` null; the order and the
`CURRENT` pointer.

The ledger batch landed at
`dispatch/expected_targets/dt=2026-10-08/tick-20261008T0300Z/` with 14 rows. Every row
carries `tier` 4, `due_at` 03:00, `due_reason` `default_long_tail`, `policy_status`
`cleared:2026-10-04:c363edef976a`, `disposition` `planned`, and `work_order_id` equal
to the order above; slot `2026-10-06`. No row mentions `NOT_SAMPLED`.

## Work order

- Immutable object
  `dispatch/orders/wikimedia_pageviews/34e4f8be49cf8a61e3709cc9c36cc478.json`, format
  `work_order_v1`: source `wikimedia_pageviews`, metric `pageviews`, daily,
  fetch-history, slot `2026-10-06`, `window_days` 4, tick 03:00, expires 03:50,
  schedule version `s9939a2accd9a17c152bc`, policy version `2026-10-04:c363edef976a`,
  14 targets, `max_targets` 14, `max_requests` 22, not a probe.
- Pointer `…/CURRENT` names that order; its recorded hash
  (`b6d356dbed0c7f9460731eba71feffdaaadee33b18d52c86b51d3a96d8cde2a4`) matches the
  order's bytes.

## Wikimedia child execution — succeeded

- Execution `ms-wikimedia-mld5x`, run id `20261008T031955Z-ms-wikimedia-mld5x`.
  **Created by `ms-dispatch`**, not by the owner. Identity `ms-wikimedia`, args
  `['collect-wikimedia']`, same image, 1 vCPU, 512Mi. One task attempt, exit 0.
- The collector claimed the order by exact name, so the new work-order read grant
  works live.
- Requests sent 14 (the cap); targets collected 14, failed 0, skipped 0.
- Window 4 days; `history_filled` 0; `history_fill_deferred` 0. **No 90-day refetch.**
- Rows landed 56: 14 articles by 4 days, 2026-10-03 to 2026-10-06. No duplicate keys,
  no negative values (range 398 to 11,710), all `observed`. The manifest matches the
  data object on hash
  (`e92021bc5bf4c8c54a2f348a6e73763821365f4a4d6bf8d68956df5b5e850be2`), bytes (23,488)
  and rows.
- 14 completion markers written for slot 2026-10-06.
- Receipt `wikimedia_pageviews/orders/34e4f8be49cf8a61e3709cc9c36cc478.json`: outcome
  `succeeded`, 14 requests, 14 collected, 0 failed, completed 03:19:59 UTC.
- Bootstrap markers still 14; none was added.
- Source contact: there is no packet-level egress logging. That only Wikimedia was
  contacted rests on the command's code path, the request count, and the absence of
  any secret version.
- Retry count, throttling and response bytes are not logged by the job: not measured.

## Second dispatcher execution (04:00 UTC tick) — NOT RUN

It had not been started when the stop instruction arrived (03:27 UTC). No lease or
plan exists for the 04:00 tick. The first order's receipt has therefore not been
folded into source health, and `warehouse.json` still says a warehouse run is owed
since 03:00.

## Warehouse child execution — NOT RUN

Consequently none of the warehouse proofs exist:

- the 56 new observations are landed and **not loaded**;
- the 14 ledger rows are landed and **not loaded**: `ms_ops.expected_targets` has not
  yet received a row through BigQuery;
- that a four-day overlap is not classified late under the seven-day window is
  **not proven live** (it is proven offline and against DuckDB only);
- dbt has not run on the new image against BigQuery;
- history-regression, duplicate-key and plausibility checks in the warehouse were
  **not performed** for the new data.

BigQuery job counts, bytes processed and billed, and slot time for C2-C: **none; not
measured**, because no BigQuery job ran.

## Timing

| | `ms-dispatch-8ltv9` | `ms-wikimedia-mld5x` |
|---|---|---|
| Created | 03:14:27 | 03:18:14 |
| Task start | 03:15:17 | 03:18:25 |
| First application log | 03:18:12 | 03:19:55 |
| Application finish | 03:18:14 | 03:19:59 |
| Platform completion | 03:18:17 | 03:20:04 |
| Wall, creation to completion | 3 min 51 s | 1 min 50 s |
| Platform-reported duration | 3 min 0.4 s | 1 min 39.1 s |
| Application work | about 2.1 s | about 4.3 s |
| Delay from creation to first log | about 3 min 45 s | about 1 min 41 s |
| Task attempts | 1 | 1 |

The long delay before the first log line is still there with the 1.08 GB image, and
was longest on the dispatcher. `billable_instance_time` was **not read** for these
executions, so nothing is concluded about whether that delay is billed.

## Cost

- Cloud Run: two executions, durations above. Billable time not measured.
- BigQuery: nothing ran.
- Cloud Storage added: 12 objects (16,408 bytes) of dispatcher state; 1 receipt (253
  bytes); 14 completion markers (about 7 KB); 12 landing objects (about 36 KB: one
  observation batch, one ledger batch and four run-log parts, each with a manifest).
  The control record is one of the 12 state objects.
- Wikimedia: 14 requests. Response bytes not measured.
- No cost projection is updated from half a checkpoint.

## Alerts and security

Checked read-only at 03:27 UTC, or by the pre-run audit where stated:

- Scheduler PAUSED, schedule `0 * * * *`, no attempt time: confirmed at 03:27.
- Cloud Run executions: **12** (10 before, plus one dispatcher and one collector).
- Secret versions 0, keys 0, IAM and job templates unchanged: confirmed by the pre-run
  audit at 03:14. **Not re-audited after the two executions.**
- Alert incidents, and Secret Manager access logs, for the C2-C window: **not
  checked**. The planned post-run alert and security audit did not take place.

## Final state as left

- Control record: present, **enabled**, expiring 05:00 UTC. Not disabled.
- Scheduler: PAUSED.
- Executions: 12. New in C2-C: 2 of the planned 4.
- Landed and unloaded: 56 observations, 14 ledger rows, run-log rows for both
  executions.
- Dispatcher state retained as legitimate automation records; nothing was deleted.
- BigQuery quota: untouched by this work.
- No code, Terraform, IAM, secret or Steam/IGDB change. The portfolio repository was
  not touched.
- A local shell wait for 04:00 UTC was left running in the background. It performs no
  cloud action; nothing will be done when it ends.

## Warnings

1. **The control record is still enabled** until 05:00 UTC (see the top).
2. **A warehouse run is owed.** The dispatcher's state says so since 03:00. If the
   dispatcher is next run on a later day, it will start the warehouse then; the landed
   batches wait safely in the bucket in the meantime.
3. **The order's receipt is not yet folded.** The dispatcher's health record still
   shows the order as pending. The receipt exists, so the next dispatcher tick will
   fold it as a success; it will not be mistaken for a lost run.
4. **Late-data caution for a delayed second half.** The warehouse loads landing folders
   for the last three days. If the second half is run more than two days after
   2026-10-08, the batches under `dt=2026-10-08` fall outside what a routine run
   loads, and the run would need the load window widened by its existing setting.
5. Half of the C2-C completion gate is unmet. Nothing here should be read as proof of
   the warehouse, ledger-load or dbt paths.

## Exact next approval required

One of:

- **Resume C2-C today:** before 05:00 UTC, run exactly one normal `ms-dispatch`
  execution just after 04:00 UTC (expected to start only `ms-warehouse`), wait for
  that warehouse run, perform the warehouse, ledger, alert and security audits, then
  disable the control record by generation-matched replacement. This is the path the
  original C2-C instruction described.
- **Stop here:** authorise disabling the control record now (one generation-matched
  replacement with `enabled: false`), and schedule the second half as a fresh,
  separately approved step with a new bounded control record.

Until one is chosen: no job is run, the control record is left as it is, and the
scheduler stays paused. C2-D has not begun.
