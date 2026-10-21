# The 16 October Wikimedia gap: completion markers and a repair

Status: **open**. Nothing here has been run against production. Every production step
below needs the owner's approval, and each is listed with what it writes.

## What is wrong

The C2-F collection at 03:02 UTC on 18 October asked each of the 14 articles for slot
16 October with a four-day window. Wikimedia's answer held 13 to 15 October only. The
collector at that time counted any non-empty answer as done, so it:

- landed the 42 rows it got (`wikimedia_pageviews/period_observations/dt=2026-10-18/20261018T030336Z-ms-wikimedia-h5797/`);
- wrote a completion marker for slot 16 October for all 14 articles, each naming that
  batch, `completion: collected`.

Read-only listing on 21 October (`gs://metaspike-prod-kc-ops/wikimedia_pageviews/slots/pageviews/dt=2026-10-16/0000/`)
shows exactly those 14 markers. The period 16 October is in no landed batch and no
BigQuery row. See `docs/c2f-acceptance-audit.md`, section 4.

The collector fix (`111d40f`, on `main`, **not deployed**) writes a marker only when the
answer holds the due period. It does not touch markers that already exist.

## What the bad markers do, and do not do

A marker is read by exact name: source, metric, article and slot. The 14 bad markers
can only stop a run whose due slot is 16 October. No order will name that slot again:
the soak ended, and a steady-state order names the slot two days before its tick. So:

- they block nothing in steady state, and are **left as they are**. They are
  create-only audit records; deleting or rewriting them is not part of any repair;
- they do not stop a later run from collecting 16 October, because a later window
  that reaches back to 16 October is asked under a different slot.

What does matter is the gap itself. Days 17 October onwards were never planned (the
scheduler stopped after 18 October), so they are gaps too, though no check fails on
them.

## How the production checks see it (PR #3)

`period_due_day_status` records, for every planned period, `on_time`, `late`,
`missing` or `pending`. The 16 October slot was planned (ledger rows from the 18
October tick), and its deadline passed on 19 October, so it is `missing`.
`assert_every_due_day_arrived_in_time` fails on `missing` for 14 days after the slot.
**Until the gap is repaired, or until 31 October when the slot leaves that window,
every 13:00 warehouse run fails and sends the "warehouse failed" alert.**

Once 16 October lands, the next daily build marks it `late`. A late arrival stays on
record as an SLA breach and does not fail the check again.

## Repair

### Option A: wait for the Monday reconciliation window (no manual step)

On Mondays the order asks for 14 days. The Monday 26 October 12:00 UTC order names slot
24 October, so its window covers 11 to 24 October, including 16 October and the
unplanned days after it. This only works if steady state is deployed before then. The
next Monday window (2 November) starts at 18 October and is too late. Each daily
warehouse run between the deploy and 26 October fails, as described above.

### Option B: one supervised collection, right after the deploy (recommended)

One execution of the existing `ms-wikimedia` job, without a work order, with a window
that reaches back to 16 October. Per-execution overrides only; the job definition, the
scheduler and the control record are not changed.

1. Pick the day `T` of the run. The run's due slot is `T − 2 days`, so the window is
   `N = (T − 2 days) − 16 October + 1` days. For example, `T` = 24 October gives `N` = 7.
2. Run it **before 12:00 UTC on `T`**. After the routine 12:00 collection the slot
   `T − 2` is marked done, and a supervised run for the same slot would ask nothing.
   ```
   gcloud run jobs execute ms-wikimedia \
     --project metaspike-prod-kc --region us-central1 \
     --update-env-vars METASPIKE_WORK_ORDERS=supervised,METASPIKE_WIKIMEDIA_DAYS=N \
     --wait
   ```
   **What it writes:**
   - 14 requests to Wikimedia;
   - one landing batch;
   - one run record;
   - 14 markers for slot `T − 2`. The fixed collector writes these only where that day
     is present.

   Values for days that were already collected land as new versions with a later
   `observed_at`. The duplicate check fails only on two different values with the same
   `observed_at`, so these do not trip it.
3. Do nothing else. At 12:00 the routine order finds slot `T − 2` done and asks nothing.
   At 13:00 the warehouse job loads the batch. 16 October is older than the seven-day
   period lookback, so the job logs `late_data_loaded` and rebuilds the period models
   from 16 October. `period_due_day_status` then shows the slot as `late`, and the check
   passes.

**Verify (read-only):**
```
bq query --use_legacy_sql=false --project_id metaspike-prod-kc '
select status, count(*) from ms_marts.period_due_day_status
where slot_start >= timestamp "2026-10-16" and slot_start < timestamp "2026-10-17"
group by status'
```
Expect `late` for 14 rows. Then check that the 13:00 run finished without
`warehouse_failed`.

**If 16 October is still absent from Wikimedia's answer:** the run logs
`due_period_missing` only when the *due* slot is absent, so check the landed rows
directly. If the day is still missing, Wikimedia has no data for it. Record that here.
The check stops failing on it on 31 October.

### Not to do

- Do not delete, rewrite or "fix" the 14 markers, or the 18 October batch.
- Do not insert rows into BigQuery by hand. Landing is the only way in.
- Do not mark the slot recovered in any table. `late` is derived from what arrived.
