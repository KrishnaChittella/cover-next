# Phase 4 Part C1: implementation and closeout report

**PHASE 4 PART C1: COMPLETE. C1 SECURITY CLOSEOUT: PASS.**

This is the permanent record of what Part C1 built, what was proven against the real
Google Cloud services, and the state it left behind. It is written to be checked: each
claim names the commit, workflow run, execution or object that supports it.

Nothing here is public. Nothing is collecting on its own: the one scheduler trigger is
paused and has never fired.

| | |
|---|---|
| Source at closeout | `ed0a873d774079ef70d28f8bb41e1afea90ea2e8` on `main` |
| Deployed image (all seven jobs) | `…/metaspike/metaspike@sha256:6810b0961ca1fdfc992c53881212525ade23d9c087d7483ea08ff1aaeae028f0` |
| Image built from | `a990ab965b0f70e509aae274e8d96295aa82e37d` |
| Project and region | `metaspike-prod-kc`, `us-central1` |
| Closed | 2026-10-07 |

The deployed image is one commit behind `main` on purpose. The later commit changes
bootstrap IAM, tests and documentation only, so nothing in the running image differs.
An image built from `ed0a873` (`sha256:1b373a41…`) exists in the registry because the
deploy workflow builds one on every plan; it was produced only to prove the narrowed
deploy identity still works and **must not be deployed** on the strength of existing.

## 1. Purpose and scope

Part B left an application that ran offline against stand-ins. Part C1 had three jobs:

1. make the seven Cloud Run jobs runnable, and close the contracts Part B left open
   (decisions D1 to D8 below);
2. prove the design against the real services under supervision, in four steps: S1
   storage IAM, S2 Cloud Storage, BigQuery and dbt with synthetic data, S3 one real
   Wikimedia collection and one real warehouse run, S4 alert delivery;
3. close the security gates: authoritative dataset access, a review of every unfixed
   image finding, and a least-privilege review of the deploy identity.

Out of scope, and still not done: unattended collection, any Steam or IGDB call, any
secret value, broad catalogue discovery, anything public.

## 2. Architecture

```
Cloud Scheduler: one job, every 15 minutes, PAUSED, never fired
   -> ms-dispatch        decides what is due, records the intent, starts jobs
        -> ms-steam-players   refuses today: Steam is not cleared
        -> ms-wikimedia       pageviews; needs no secret
        -> ms-catalogue       IGDB and Steam metadata; not cleared for unattended use
        -> ms-warehouse       loads landed batches into BigQuery, then runs dbt
        -> ms-features        Python feature engine
        -> ms-parity          rebuilds baseline state and compares
```

One image runs every job with a different command and identity. It carries two
separately locked Python environments: the application in `/app/.venv` and dbt in
`/opt/dbt`, with the dbt project at `/app/warehouse`. It runs as uid 10001 and neither
environment is writable.

Data moves in one direction: a collector lands an immutable batch in Cloud Storage,
the warehouse job loads it into BigQuery exactly once, and dbt builds the staging and
mart models from the raw tables. Scoring stays in Python; dbt cleans and aggregates.

## 3. Deployed resources

`infra/prod` (applied only by the deploy workflow) manages 86 resources at state
serial 7: five application buckets with 26 prefix grants, six datasets with one
authoritative policy each, twelve tables, three secret containers with four reader
bindings, seven Cloud Run jobs, two job invoker bindings, one scheduler job, five log
metrics and four alert policies.

`infra/bootstrap` (applied by the owner by hand) manages 59 resources at state serial
8: enabled APIs, the GitHub workload identity pool and provider, the deploy identity
and its roles, the eight runtime identities, the image registry, the notification
channel and the budget.

## 4. Runtime identities

| Identity | Responsibility | Holds a source secret |
|---|---|---|
| `ms-scheduler` | May start the dispatcher and nothing else | no |
| `ms-dispatch` | Decides what is due; may start the six other jobs | no |
| `ms-steam-players` | Samples Steam concurrent players | reader binding exists; no version exists |
| `ms-wikimedia` | Collects Wikimedia pageviews | no |
| `ms-catalogue` | IGDB and Steam catalogue metadata | reader bindings exist; no version exists |
| `ms-warehouse` | Loads landed files into BigQuery and runs dbt | no |
| `ms-features` | Feature engine and baseline state | no |
| `ms-parity` | Rebuilds state from history and compares | no |

No identity is shared, none has a key, and no job may start another job except the
dispatcher.

## 5. Bucket and prefix access

Five private regional buckets (`landing`, `raw`, `state`, `ops`, `tokens`), uniform
access, public access prevention enforced, no retention lock. Access is granted per
identity, per bucket and per object prefix with an IAM condition.

| Identity | landing | raw | state | ops | tokens |
|---|---|---|---|---|---|
| `ms-dispatch` | create under `dispatch/` | none | none | read all; read-write `dispatch/` | none |
| `ms-steam-players` | create under `steam_current_players/` | none | read `catalogue/` | read-write own prefix and `quota/steam_web_api/` | none |
| `ms-wikimedia` | create under `wikimedia_pageviews/` | create under `wikimedia_pageviews/` | read `catalogue/` | read-write own prefix | none |
| `ms-catalogue` | create under `igdb/`, `steam_catalogue/`, `catalogue/` | create under `igdb/` | read-write `catalogue/` | read-write `catalogue/` and `quota/steam_web_api/` | read-write |
| `ms-warehouse` | read all | read all | none | read-write `warehouse/` | none |
| `ms-features` | none | none | read-write `baseline/` | read-write `features/` | none |
| `ms-parity` | none | none | read `baseline/` | read-write `parity/` | none |

Three properties were established live in S1 and are relied on:

- Landing is create-only for collectors. They can neither read back nor replace what
  they landed, which is immutability without a retention lock.
- A prefix-conditional identity cannot list. Nothing in a collector lists.
- Reading an object that does not exist, under a prefix the identity may read,
  returns 404, not 403 (confirmed for a read-only grant by a diagnostic execution and
  for read-write grants by the S3 collection, whose marker lookups succeeded).

## 6. Dataset access

Each of the six datasets has one authoritative policy in Terraform
(`google_bigquery_dataset_iam_policy`). BigQuery's `projectOwners` special group is
the only owner. The three warehouse-facing identities hold exactly these roles and
nothing is merged with what BigQuery adds on its own:

| Identity | Editor on | Viewer on |
|---|---|---|
| `ms-warehouse` | `ms_raw`, `ms_catalogue`, `ms_staging`, `ms_marts`, `ms_ops` | |
| `ms-features` | `ms_features` | `ms_raw`, `ms_marts`, `ms_catalogue` |
| `ms-parity` | `ms_ops` | `ms_raw`, `ms_marts`, `ms_catalogue`, `ms_features` |

The deploy identity is not a member of any dataset. The earlier per-member grants
were forgotten from state, not destroyed, so no binding was removed out of order.

## 7. Warehouse tables and contracts

Twelve tables, all day-partitioned, clustered, deletion-protected and created with
`require_partition_filter`, so a query that names no time range is refused.

| Dataset | Table | Partition |
|---|---|---|
| `ms_raw` | `sample_attempts` | `slot_start` |
| `ms_raw` | `period_observations` | `period_start` |
| `ms_raw` | `source_records` | `fetched_at` |
| `ms_raw` | `sample_native_ids` | `first_slot_start` |
| `ms_catalogue` | `binding_events`, `quarantine_events`, `entity_events`, `lifecycle_events` | `occurred_at` |
| `ms_catalogue` | `resolution_decisions` | `decided_at` |
| `ms_features` | `bucket_evaluations` | `bucket_start` |
| `ms_ops` | `collection_runs` | `started_at` |
| `ms_ops` | `expected_targets` | `slot_start` |

Raw tables are append-only in normal operation. Every loaded row carries the landed
object it came from and that object's content hash.

dbt builds five staging models (`stg_sample_attempts`, `stg_expected_targets`,
`stg_period_observations`, `stg_source_records`, `stg_collection_runs`) and four mart
models (`resolved_sample_slots`, `sample_slot_timeline`, `sample_coverage_daily`,
`source_health_daily`), with 19 data tests. The three mart tables are built from
sample attempts; period observations stop at staging.

## 8. The eight C1 decisions

**D1, completion markers.** After a batch is durably landed, the collector writes one
marker per target and slot under its own `ops` prefix, named
`{source}/slots/{metric}/dt={date}/{HHMM}/{sha256(native id)[:32]}.json`. A marker is
created only from a committed batch manifest and records that batch's object and
hash. A retry that finds the marker does not ask the source again. This is
at-least-once collection made safe to repeat, not exactly-once: a crash between
landing and marking causes one more request and a second batch, which the loader and
the models tolerate.

**D2, expected-target ledger.** Before it starts anything, the dispatcher lands what
it intends to be collected into `ms_ops.expected_targets`: source, metric,
granularity, mode, native id, slot, schedule version and the dispatching run. The
slot timeline is the ledger joined to the resolved slots, so a slot nobody attempted
is still known to have been expected. `NOT_SAMPLED` exists only in that view and in
counts derived from it. It is never stored as a raw row; a test enforces this.

**D3, one image, two environments.** Described in section 2. dbt is not importable
from the application and the application's lock file is unaffected by dbt's.

**D4, entity and lifecycle events.** `ms_catalogue.entity_events` and
`ms_catalogue.lifecycle_events` persist the catalogue's own history about canonical
entities, landed under the `catalogue/` prefix. They are MetaSpike's records, not
source data. The catalogue itself is stored as append-only events, immutable
snapshots and one conditionally replaced pointer (`catalogue/CURRENT`), and folding
the events reproduces the snapshot.

**D5, authoritative dataset access.** Section 6.

**D6, `source_timestamp`.** A nullable timestamp appended to `period_observations`:
the time the source attributes to the value, as distinct from `observed_at` (when
MetaSpike read it) and `period_start` (the period it describes). For Wikimedia daily
pageviews it equals `period_start` on every row.

**D7, sample native ids.** `ms_raw.sample_native_ids` is an index the loader maintains
of which native ids have samples and since when, so a store can answer "which ids"
without scanning sample history.

**D8, late data.** If a loaded batch contains data older than the normal three-day
lookback, the warehouse job records a pending rebuild (`ops/warehouse/pending_rebuild.json`)
before it writes the load receipt, passes the earliest affected date to dbt, and
clears the record only after dbt succeeds. If dbt fails the job fails and the pending
rebuild survives for the next run. A pending rebuild in a non-hourly group widens an
hourly run to the daily selector.

## 9. Landing, manifests and the loader

A batch is immutable NDJSON at
`{source}/{table}/dt={YYYY-MM-DD}/{run_id}/part-NNNN.ndjson` with a manifest written
last. The manifest records the format (`landing_v1`), schema version, row count, byte
count, content hash, policy version and retrieval time. A data object without its
manifest is incomplete and is never loaded. Steam raw payloads are never landed.

The loader is idempotent by construction:

- the batch's identity is a digest of its table, object and content hash;
- the BigQuery load job id is deterministic, `ms_load_{table}_{digest}_a{n}`, so
  submitting the same batch twice is refused by BigQuery (HTTP 409, proven in S2);
- a receipt object, `warehouse/loads/{dataset.table}/{digest}.json`, is written after
  the load and checked before it;
- a load that succeeded but whose receipt was lost is recovered by finding the job,
  not by loading again.

## 10. Full refresh and retained history

A dbt full refresh originally rebuilt from a fixed date. Once real history existed
that would have dropped everything older, so `ms_history_start` now derives the
earliest populated day partition from `INFORMATION_SCHEMA.PARTITIONS` (the minimum of
the column on DuckDB). The `history_start` variable defaults to null and exists only
as an explicit override. An empty history resolves to today. Fixed in `a990ab9`,
deployed by apply run `37587896040`.

One limit is recorded as a hard gate in the runbook: an incremental run replaces only
the partitions in its new result, so deleting raw rows needs a full or partition
rebuild before the removal is complete.

## 11. Real-service proofs

### S1, storage IAM as each runtime identity

Seven executions, one per identity, run id `s1-67bf6ce`, 25 probe sets across the
seven permission profiles. Every probe returned its expected verdict: allowed
operations succeeded, operations outside a prefix were denied, and no identity could
list. Fixtures were created and removed by the owner; no role was widened to make
cleanup possible.

Executions: `ms-dispatch-2zhtm`, `ms-steam-players-j67zp`, `ms-wikimedia-tgfqf`,
`ms-catalogue-sw7mj`, `ms-warehouse-96dgn`, `ms-features-659gs`, `ms-parity-vlj2w`.

### S2, real Cloud Storage, BigQuery and dbt with synthetic data

Run with the owner's credentials, run id `s2-67bf6ce`, all scratch data removed
afterwards.

- Cloud Storage: create-only write, conditional replace and stale replace returned
  200, 200 and 412.
- BigQuery: a load with nested JSON, a repeated column and microsecond timestamps
  loaded correctly; a duplicate job id returned 409; a bounded parameterised query
  honoured `maximumBytesBilled`.
- dbt: `dbt build` on the `prod` target passed on empty and on synthetic data.
- D6, D7 and D8 behaved against the real services as they had against the stand-ins.

Two findings: every BigQuery query is billed a 10 MB minimum, and an insert-overwrite
model does not remove a partition whose raw rows were deleted (section 10).

### S3, first real Wikimedia collection and warehouse run

One normal `ms-wikimedia` execution and one normal `ms-warehouse` execution, no
command override, on 2026-10-07.

| | `ms-wikimedia-qsdbm` | `ms-warehouse-cpb5q` |
|---|---|---|
| Task attempts | 1 | 1 |
| Exit | 0 | 0 |
| Wall time, creation to completion | 146 s | 197 s |
| Application work | 3.2 s | 62.6 s |
| Delay before first log line | 136 s | 99 s |

The collection used the bootstrap catalogue: 14 English Wikipedia articles (FiveM
excluded), one request each, 14 requests sent, 0 failures, 0 retries. It landed one
batch of 1,260 rows (14 articles by 90 days, 2026-07-08 to 2026-10-05), two run-log
batches and 14 completion markers. The manifest matched the data object on bytes,
hash and row count; there were no duplicate logical keys, no negative, null or future
values, and no stored `NOT_SAMPLED`.

The warehouse run loaded three batches (0 already loaded, 0 incomplete), detected the
late data, passed 2026-07-08 to dbt as the rebuild boundary, and dbt finished PASS=28,
WARN=0, ERROR=0. `stg_period_observations` holds the same 1,260 rows across the same
90 partitions, of which 1,190 predate 1 October: no truncation. A raw-to-staging join
found no mismatch.

There is no packet-level egress logging in this project. That only Wikimedia was
contacted rests on the command's code path, the request count, the absence of any
secret version, and the absence of any Secret Manager access in the audit log.

### S4, alert delivery end to end

One synthetic log entry (marker `s4-alert-test-a990ab9`, attributed to a job name
that does not exist) incremented the `ms_baseline_parity_mismatch` metric, opened an
incident on the policy "MetaSpike: baseline state parity mismatch" about five minutes
later, and the notification arrived in the recipient's inbox, confirmed by the owner.
One event was emitted; no configuration was changed. The path synthetic event, log
metric, alert policy, incident, email channel, inbox is proven.

## 12. Image findings

Trivy 0.70.0 (the CI gate's version) against the deployed digest: 53 HIGH and 2
CRITICAL, none fixable, all in Debian 12 packages, 15 distinct CVEs. Every finding
has a disposition: 21 ACCEPT, 34 MONITOR, 0 BLOCK.

- **CVE-2025-7458 (CRITICAL, SQLite).** Needs attacker-controlled SQL run against
  SQLite. The application never loads SQLite and no source response becomes SQL.
  ACCEPT.
- **CVE-2023-45853 (CRITICAL, zlib MiniZip).** Debian does not build MiniZip into
  `zlib1g`; the symbol is absent from the library in the image. ACCEPT.
- **Four util-linux `mount` CVEs (32 rows).** setuid-root `mount` and `umount` are in
  the image. Exploitation needs code already running in the container. MONITOR, and
  removing the setuid bit is carried to C2.
- **CVE-2026-84782 (OpenSSL DTLS, 2 rows).** The library is used for every HTTPS
  call but only as a TLS client; DTLS is never used. Fixed in Debian 13, pending in
  Debian 12. MONITOR.

Seven of the fifteen CVEs, including both CRITICAL ones, disappear on a Debian 13
base. That evaluation is carried to C2. The application and dbt dependency audits are
clean.

## 13. The deploy identity

`ms-deploy` is reachable only from GitHub Actions through workload identity
federation. The provider accepts a token only when all of these hold: this repository
and this owner by immutable numeric id, the repository is private, the ref is
`refs/heads/main`, the workflow is `deploy.yml` as it exists on main, the event is
`workflow_dispatch`, and the actor is the owner. There is no key, the repository has
no Actions secret, and no static credential is in the tree.

Its final bindings:

| Scope | Role |
|---|---|
| Project | `roles/run.admin`, `roles/cloudscheduler.admin`, `roles/monitoring.editor`, `roles/logging.configWriter` |
| Project | `msSecretContainerAdmin` (custom: secret containers and their IAM; no version access or add) |
| Project | `msDeployBigQueryConfig` (custom: 9 permissions for datasets, tables and dataset access) |
| Project | `msDeployBucketConfig` (custom: 5 permissions for bucket settings and bucket IAM) |
| Eight runtime service accounts | `roles/iam.serviceAccountUser` |
| Registry `metaspike` | `roles/artifactregistry.writer` |
| Terraform state bucket | `roles/storage.objectUser` |

The review found `roles/bigquery.admin` and `roles/storage.admin`, which carry rows,
objects and query jobs that deploying never needs. They were replaced by the two
custom roles in `ed0a873`, applied by the owner from a saved plan. The identity now
has **no direct application-data permission**: it cannot read or write a row, submit
a query job, or read, list, write or delete an object outside the state bucket, and
it cannot delete a dataset, table or bucket or drop a column.

What remains, deliberately and on the record: it can set IAM on a dataset, a bucket
or a secret container, and it can deploy a job that runs as a runtime identity. Those
are administrative paths, visible in the audit log, and the control on them is the
federation condition. It holds no Owner, Editor, Token Creator, Key Admin or project
IAM role.

Deploy-workflow run `37697996887` (plan only) proved the narrowed identity still
authenticates, reads the state backend, refreshes all 86 resources and produces a
plan. That plan proposed only the rebuilt image on the seven jobs and was not applied.

## 14. Final state at closeout

| | |
|---|---|
| Scheduler `ms-dispatch-15m` | PAUSED; never attempted |
| Cloud Run executions | 10: seven S1 smoke, one diagnostic, one Wikimedia, one warehouse |
| Secret versions | 0 on all three containers |
| User-managed service-account keys | 0 across 10 service accounts |
| `ms_raw.period_observations` | 1,260 rows, 90 partitions, 2026-07-08 to 2026-10-05 |
| `ms_ops.collection_runs` | 4 rows |
| `ms_staging.stg_period_observations` | 1,260 rows |
| `ms_staging.stg_collection_runs` | 3 rows |
| Other raw, catalogue, feature and ops tables | 0 rows |
| Mart tables | 0 rows (they are built from sample attempts) |
| `landing` bucket | 6 objects, 531,262 bytes |
| `ops` bucket | 18 objects: 14 markers, 3 load receipts, 1 pending-rebuild record |
| `raw`, `state`, `tokens` buckets | empty |

The Wikimedia data is MetaSpike's first retained source history. It is kept.

## 15. Measurements

**Cloud Run.** Both S3 executions spent most of their wall time before the first log
line (136 s and 99 s), as the S1 executions had (95 to 239 s of wall time for under
3 s of work). The image is about 1.1 GB uncompressed. The `billable_instance_time`
metric recorded about 60 s for the warehouse execution, which tracks its application
runtime and not the start delay; the figure for the Wikimedia execution was too small
to be trusted. Whether start-up time is billed is therefore **not settled** and is
measured again in C2 against invoice lines.

**BigQuery.** The warehouse run issued 65 jobs (41 top level): 5 loads, 8 scripts, 8
create-table statements, 1 view and 19 tests. It processed 1.42 MB and was billed
136.3 MB, almost all of it the 10 MB per-query minimum. Cost scales with the number
of dbt steps and runs, not with data volume at this size.

**Spend.** Compute, storage and queries were all inside the free tiers. No
extrapolation to thousands of games was made from one run.

## 16. Limitations carried into C2

1. The Wikimedia collector fetches 90 days on every run. A steady-state window and
   cadence must be decided before anything is scheduled.
2. The dispatcher's schedule is a fixed table of hours, loads every target on every
   tick, and has no tiers, caps, backlog policy or circuit breaker.
3. Three log metrics have no alert policy: `ms_warehouse_failed`,
   `ms_slot_not_observed`, `ms_source_rate_limited`.
4. `roles/monitoring.editor` and `roles/logging.configWriter` on the deploy identity
   are broader than the resources they manage.
5. setuid `mount` and `umount` remain in the image; the Debian 13 base is not
   evaluated; the Debian 12 OpenSSL fix is pending.
6. Start-up delay and its billing are unmeasured beyond two executions.
7. A dbt build bills about 136 MB whatever the data volume.
8. Steam and IGDB are not cleared for unattended use, no secret has a value, and the
   secret-IAM review has not been done.
9. Broad catalogue discovery is off. The catalogue is the 14-entity bootstrap set.
10. `features` and `parity` do not yet write bucket evaluations (Part D).
11. `stg_collection_runs` trails the raw table by the warehouse job's own closing row
    until the next run.
12. Deleting raw rows needs a rebuild to be complete (runbook hard gate 13).
13. The repository's `main` branch cannot be protected on the current GitHub plan, so
    the deploy trust chain rests on the owner's GitHub account.

## 17. Completion criteria and evidence

| Criterion | Evidence |
|---|---|
| D1 to D8 built and tested | Commits `1bce7a1`, `481ed6a`, `f663a97`, `b64201a`; 1,009 tests passing at `ed0a873` |
| C1 infrastructure applied from a reviewed plan | Plan run `37565558405`, hash `faa89178…1821`, apply run `37566720387` |
| S1 passed | Seven executions, run id `s1-67bf6ce`; suite deployed by plan `37572384567`, apply `37578218350` |
| S2 passed | Run id `s2-67bf6ce`, owner credentials, scratch data removed |
| History boundary corrected and deployed | Commit `a990ab9`; plan `37586469160`, hash `7eff5e4f…6062`, apply `37587896040` |
| S3 passed | `ms-wikimedia-qsdbm`, `ms-warehouse-cpb5q`; dbt PASS=28 |
| S4 passed | Incident on the parity-mismatch policy; inbox delivery confirmed by the owner |
| Every HIGH and CRITICAL finding dispositioned | 55 of 55; 0 BLOCK; 0 fixable |
| Deploy identity holds no direct application-data permission | Commit `ed0a873`; owner plan hash `31cf6135…244a`; bootstrap state serial 7 to 8 |
| Narrowed deploy identity still works | Deploy-workflow plan run `37697996887`: 86 resources refreshed |
| Actions on supported Node releases, pinned by commit | Commit `c6488b1` |

Two plans were rejected during C1 and must never be applied: runs `37560932748` and
`37562589022`.

What comes next is in [phase4-c2-plan.md](phase4-c2-plan.md). Nothing in it has been
started.
