# Handoff: latest checkpoint

This file is the short-lived handoff from Claude Code to ChatGPT. It is overwritten at
each checkpoint; Git history is the archive. It supports communication only and
replaces no code, test, audit or document.

Current checkpoint: **PHASE 4 PART C2-B: COMPLETE**

Both plans applied cleanly, all post-apply audits pass, and the 14 fill markers are
written and verified. The scheduler is still paused with no attempt, no control record
exists, and nothing ran or called a source. C2 automation is not active.

## Plan A (owner, bootstrap IAM)

- **Pre-apply state:** HEAD = origin = `ab5807841706b99287badb3ea1385d0ef172bd7d`, tree
  clean; scheduler PAUSED and never attempted; 10 executions; all jobs on
  `sha256:6810b096…28f0`; 0 secret versions; 0 keys; no `dispatch/control.json`; no
  bootstrap marker; retained data unchanged; bootstrap state serial 8, prod serial 7.
- **Hash:** recomputed
  `8e95988e97e4ad2d4c0a92a979467d4c5d9b630eae0b0ba05884b247705691ea`, exact match.
  Re-inspected: 4 add, 0 change, 2 destroy, only the six approved resources.
- **Apply:** `Apply complete! Resources: 4 added, 0 changed, 2 destroyed.` No new plan
  was run.
- **Bootstrap state:** serial 8 → 9; managed resources 59 → 61.

### Live ms-deploy roles

- **Project:** `roles/run.admin`, `roles/cloudscheduler.admin`,
  `roles/monitoring.alertPolicyEditor`, `roles/monitoring.notificationChannelViewer`,
  `msSecretContainerAdmin`, `msDeployBigQueryConfig`, `msDeployBucketConfig`,
  `msDeployLogMetrics`.
- **Removed:** `roles/monitoring.editor`, `roles/logging.configWriter`.
- **Unchanged:** `roles/iam.serviceAccountUser` on the eight runtime and scheduler
  identities, `roles/artifactregistry.writer` on `metaspike`,
  `roles/storage.objectUser` on the Terraform state bucket.
- **`msDeployLogMetrics`** (GA), exactly: `logging.logMetrics.create`, `.delete`,
  `.get`, `.list`, `.update`.

## Plan B (production)

- **Hash:** the artifact from deploy-workflow run `37715238212` was downloaded again
  and hashes to `39da6f5194159bd36923f904616d02fac4fc4fcf30c8a02711c2f0d22ae5eee0`,
  exact match. Source commit `ab58078`, artifact not expired, prod state serial 7.
- The plan binary could not be decoded locally (created by Terraform 1.16.5; the local
  binary is 1.16.4). Its contents were re-checked from the plan run's own rendered
  output: 5 created, 10 updated in place, `paused` nowhere in the diff, and only the
  old and the approved image digests.
- **Apply:** run `37718468463`:
  `Apply complete! Resources: 5 added, 10 changed, 0 destroyed.` No permission error,
  so the narrowed roles are proven sufficient to create and update alert policies.
- **Prod state:** serial 7 → 8; managed resources 86 → 91.
- **Added (5):** alert policies `warehouse_failed`, `source_rate_limited`,
  `execution_volume`, `landing_storage`; the object grant for `ms-wikimedia`.
- **Changed in place (10):** the seven Cloud Run jobs, `ms_ops.expected_targets`, the
  scheduler, the dispatcher-silence policy.

## Deployed image

All seven jobs are at generation 5 on
`us-central1-docker.pkg.dev/metaspike-prod-kc/metaspike/metaspike@sha256:f452c80108ac0ab8fd8d7e2d4b9d0da931686ed2ed6d3454f683b016c943e100`,
in both `image` and `METASPIKE_IMAGE`. Args, identities, memory, timeouts and retries
are unchanged, with 0 secret references. Only `ms-wikimedia` carries
`METASPIKE_WORK_ORDERS=required`.

Hardening, verified on this exact digest: uid 10001; `/app` and `/opt/dbt` not
writable; 0 setuid or setgid files; the two Python environments isolated; Debian 12
base; 53 HIGH / 2 CRITICAL findings with 0 fixable.

## Work-order grant

`ms-wikimedia` on `metaspike-prod-kc-ops`: `roles/storage.objectViewer` with the
condition
`resource.name.startsWith("projects/_/buckets/metaspike-prod-kc-ops/objects/dispatch/orders/wikimedia_pageviews/")`,
beside its existing `objectUser` on `wikimedia_pageviews/`. No broader `dispatch/`
prefix, no write or delete there, and being conditional it gives no listing.
`ms-dispatch` keeps `objectUser` on `dispatch/` and its whole-bucket viewer. Not
exercised live yet; C2-C is the real proof.

## expected_targets schema

Six columns appended, all NULLABLE: `tier` (INT64; BigQuery reports it as INTEGER),
`due_at` (TIMESTAMP), `due_reason`, `policy_status`, `disposition`, `work_order_id`
(STRING). The twelve original columns are unchanged. Still partitioned by day on
`slot_start`, clustered on `source, metric, source_native_id`, partition filter
required. 0 rows; creation time unchanged, so the update was in place. No staging
change.

## Scheduler

Schedule `0 * * * *`, state PAUSED, no attempt time, same resource name
`ms-dispatch-15m`, updated and not recreated.

## Monitoring

Eight policies, all on the proven notification channel:

| Policy | State | Condition |
|---|---|---|
| Warehouse load or build failed | new, enabled | `ms_warehouse_failed` > 0 in 1 h |
| A source keeps rate limiting | new, enabled | `ms_source_rate_limited` > 2 in 1 h, all jobs |
| Unusually many job executions | new, enabled | completed executions > 60 in 24 h |
| Landing bucket above 5 GB | new, enabled | `storage/v2/total_bytes` > 5,000,000,000 on the landing bucket |
| Dispatcher silence | updated, disabled | absent for 3 h |
| Steam refusal, job failures, parity | unchanged, enabled | |

The five log metrics are unchanged; `ms_slot_not_observed` has no policy. No freshness
policy was created (deferred: it cannot be expressed reliably with built-in metrics).
No incident is open and no test alert was sent. The S4 synthetic incident closed by
itself at 20:14 UTC on 7 October.

## Fill markers

- **Pre-write reconciliation:** re-read from the warehouse and the bucket just before
  writing. All 14 articles have exactly 90 rows, 2026-07-08 to 2026-10-05, in one
  batch; the canonical id matches the target; the data object's hash recomputed to
  `8dc374978744de6bf90672fd015b7e71b0e51b653474d23ed36b94af78779570`, equal to its
  manifest; each regenerated payload matches the reviewed hash and size; no marker
  existed.
- **Write:** 14 of 14 written under `wikimedia_pageviews/bootstrap/` in the operations
  bucket, each by exact name with `if-generation-match=0`. No wildcard, no overwrite,
  no source call.
- **Verification:** by exact-name read all 14 verify: payload SHA-256 exact,
  `source_native_id` exact, `days` 90, `data_object`, `content_sha256` and
  `collection_run_id` matching the S3 batch, `completed_at` equal to the manifest's
  time, metageneration 1. The operations bucket gained exactly 14 objects, all under
  that prefix.
- One deliberate repeat write of the first marker was made after verification to
  confirm the precondition. It was refused with 412 and the object's generation is
  unchanged. That attempt was not in the instructions.

## Retained data

Unchanged: 1,260 observation rows and 4 run rows; landing bucket identical (6
objects); existing completion markers and receipts untouched; dataset access, the
other buckets' IAM, service accounts, secrets and the WIF condition unchanged.

## Final state

- **BigQuery quota:** unchanged, 209,715,200 MiB a day, no override. 10 GiB a day
  (10,240 MiB) remains a hard gate before C2-D.
- **Control record:** absent; nothing exists under `dispatch/`. No work order exists.
- **Executions:** 10. The only Cloud Run log entries since the apply are the seven
  `UpdateJob` audit records.
- **Secrets and keys:** 0 versions on all three secrets; 0 user-managed keys.
- **Security regression:** the union of ms-deploy's project roles holds no
  application-data permission, no secret version access or add, no key or token
  creation, no project IAM or role editing, no notification-channel change, and no
  log sinks, exclusions or log buckets. No Owner or Editor role.
- **Git:** `main` at `ab58078`, level with origin, clean. The portfolio repository was
  not touched.

## Warnings

- Local Terraform is one patch version behind CI, so saved production plans cannot be
  decoded locally; the plan run's rendered output and the hash are relied on.
- The registry holds one image that must never be deployed (`sha256:1b373a41…`).
- The scheduler's name still says 15m while it is hourly, as accepted.
- Still open: the quota gate (before C2-D); the deferred freshness alert; the
  missed-tick review before any sampled source; the branch-protection decision before
  any secret-backed source.

## Next approval required

STOP. C2-C (one supervised end-to-end tick) had not begun when this was written.
