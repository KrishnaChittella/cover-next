---
name: metaspike-warehouse
description: MetaSpike's warehouse path - immutable landing batches, completion markers, the idempotent BigQuery loader, dbt models and data tests, late-data rebuilds. Use when changing jobs/collect.py, warehouse/, the dbt project, or adding a data-quality check.
---

# Warehouse

```
collector ──► landing bucket (NDJSON + manifest, create-only)
          ──► completion marker in ops bucket (only after the manifest is committed)
warehouse job ──► BigQuery raw tables (load once, deterministic job id, receipt)
              ──► dbt build (staging, marts, tests)
```

## Code

| Piece | Where |
|---|---|
| Batch format, manifests, row builders | `warehouse/landing.py` |
| Completion markers (`{source}/slots/{metric}/dt=.../...json`) | `warehouse/markers.py` |
| Loader, receipts, recovery | `warehouse/loader.py` |
| Table and object ports; in-memory stand-ins for tests | `warehouse/tables.py`, `objects.py`, `stores.py` |
| Collectors (Wikimedia, sample-now) | `jobs/collect.py` |
| Warehouse job: load, pick selector, run dbt, late data | `jobs/warehouse_job.py` |
| Table schemas | `infra/prod/schemas/*.json` |
| dbt project (own env: `uv run --project warehouse ...`) | `warehouse/` |

## Rules

- Landing is append-only and create-only. Nothing rewrites a batch.
- A marker is written only from a committed manifest, and only when the due period is
  actually present in the answer (the C2-F fix, `due_period_missing`).
- `NOT_SAMPLED` is derived in views from the expected-target ledger; never stored.
- dbt cleans and aggregates. It never computes a feature or a score (Python does).
- Every raw table requires a partition filter; every dbt job has a 1 GiB bytes cap.
- Late data (older than the lookback: 3 days samples, 7 days periods) triggers a pending
  rebuild that clears only after dbt succeeds.
- Deleting raw rows needs a full or partition rebuild afterwards.

## dbt

Selectors (`warehouse/selectors.yml`): `hourly`, `daily` (hourly + daily tags),
`periods`. Offline target is DuckDB, so tests run without the cloud:

```
uv run --project warehouse --locked dbt build --selector daily --project-dir warehouse --profiles-dir warehouse
uv run pytest -q tests/test_warehouse_dbt.py
```

Data tests live in `warehouse/tests/*.sql` (a test fails when its query returns rows)
and in `schema.yml`. A failing dbt test fails the warehouse job, which fires the
existing "warehouse failed" email alert. This is how production data checks work: add a
SQL test, not a new job or alert. Good checks: every tracked target has its due period
within 36 h; no duplicate logical keys; daily row count not far below the 7-day median;
a collector receipt in the last 26 h.

Each new BigQuery statement is billed at least 10 MiB, so prefer one test that checks
several things over many tiny ones.
