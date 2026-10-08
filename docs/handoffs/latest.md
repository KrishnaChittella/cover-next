# Handoff: latest checkpoint

This file is the short-lived handoff from Claude Code to ChatGPT. It is overwritten at
each checkpoint; Git history is the archive. It supports communication only and
replaces no code, test, audit or document.

Previous accepted checkpoint: **PHASE 4 PART C2-C: COMPLETE**

Current checkpoint: **PHASE 4 PART C2-C.1: COMPLETE**

The exact saved plan was applied. All seven Cloud Run jobs now run the image that
enforces request budgets on outbound HTTP attempts. Nothing was executed and no source
was called: the scheduler is paused, the control record is disabled, and the execution
count is unchanged at 14. C2-D has not begun.

## What was approved and applied

| | |
|---|---|
| Source commit | `e99812ca6b59e1b10145db2bca5fdf044cac1029` |
| Plan run | `37734819174` |
| Plan SHA-256 | `15a0c0599bdb0e053f501c1aa09c36868df2a18fe3fd128c40d5a0f0116af358` |
| Apply run | `37736381749` |
| Apply result | `Apply complete! Resources: 0 added, 7 changed, 0 destroyed.` |
| Deployed image | `us-central1-docker.pkg.dev/metaspike-prod-kc/metaspike/metaspike@sha256:a8680b2be8b5e4bea48392c2b5324b44a680b4c2a4d56e2f49b836bdb979c754` |

## Pre-apply gate

All 17 checks passed:

- the source commit exists and is unchanged; HEAD was one handoff-only commit ahead
  (the only difference is `docs/handoffs/latest.md`); tree clean;
- the plan artifact was downloaded again, was not expired (expires
  2026-10-11T06:02:52Z), and hashed to the approved value exactly; its run was built
  from the approved commit;
- the plan's rendered contents: 0 add, 7 change, 0 destroy, 0 replace; seven in-place
  updates; only the old and the approved digests appear, and no changed line concerns
  anything but `image` and `METASPIKE_IMAGE`;
- scheduler PAUSED, `0 * * * *`, never attempted; control record present and disabled;
  14 executions; all seven jobs on `sha256:f452c801…e100`; 0 secret versions; 0
  user-managed keys; no quota override; no IAM, schema, alert, scheduler or
  job-template drift; prod state serial 8.

As before, the plan binary cannot be decoded locally (Terraform 1.16.5 in CI, 1.16.4
locally), so its contents were checked from the plan run's own rendered output and the
hash.

## Apply

The deploy workflow's apply operation, given the plan's run id and hash, by the
restricted WIF identity. No new plan was made. Seven `Modifications complete` lines,
one per job, and no permission error. Prod state serial 8 → 9; managed resources 91,
unchanged in number. Bootstrap state untouched (serial 9).

## Seven-job audit

All seven jobs are at generation 6 with the approved digest in both `image` and
`METASPIKE_IMAGE`:

| Job | Args | Identity | Memory | Timeout | Retries | Work orders |
|---|---|---|---|---|---|---|
| `ms-dispatch` | `dispatch` | `ms-dispatch` | 512Mi | 300 s | 0 | no |
| `ms-wikimedia` | `collect-wikimedia` | `ms-wikimedia` | 512Mi | 900 s | 1 | `required` |
| `ms-steam-players` | `collect-steam-players` | `ms-steam-players` | 512Mi | 840 s | 0 | no |
| `ms-catalogue` | `collect-catalogue` | `ms-catalogue` | 2Gi | 3600 s | 1 | no |
| `ms-warehouse` | `warehouse` | `ms-warehouse` | 1Gi | 1800 s | 1 | no |
| `ms-features` | `features` | `ms-features` | 1Gi | 1800 s | 1 | no |
| `ms-parity` | `parity` | `ms-parity` | 1Gi | 3600 s | 0 | no |

Each has 1 vCPU and 0 secret references. Only `ms-wikimedia` carries
`METASPIKE_WORK_ORDERS=required`. Args, identities, memory, timeouts and retries are
as they were.

## Image

The deployed digest is the one audited in the plan checkpoint; it was not rebuilt. The
accepted result stands: uid and gid 10001; `/app` and `/opt/dbt` not writable; 0 setuid
or setgid files; the two Python environments isolated; `pip-audit` clean for both;
Trivy 53 HIGH / 2 CRITICAL with 0 fixable; Debian 12.

## State after the apply

- **Scheduler:** PAUSED, `0 * * * *`, no attempt time.
- **Control record:** present, `enabled: false`, generation `1791432826876698`,
  unchanged since it was disabled at the end of C2-C.
- **Cloud Run executions: 14.** Updating the job definitions created none.
- **No source call.** The only Cloud Run log entries since the gate are the seven
  `UpdateJob` audit records (and their system events). No job ran, so no request to
  Wikimedia or any other source was possible. No work order was created.
- **Secrets and keys:** 0 versions on all three secrets; 0 Secret Manager access log
  entries; 0 user-managed keys.
- **IAM and WIF:** no difference in project IAM, service-account IAM, bucket IAM,
  dataset access or secret IAM against the pre-apply snapshot; WIF provider ACTIVE and
  unchanged.
- **Schema and monitoring:** `ms_ops.expected_targets` still has 18 columns and 14
  rows; 8 alert policies, 7 enabled (dispatcher silence disabled), unchanged.
- **BigQuery quota:** unchanged default, 209,715,200 MiB a day, no override.
- **Retained data and state:** unchanged. 1,316 observations, 14 ledger rows, 12 run
  rows; landing 22 objects and operations 69 objects, both identical object for object
  to before the apply; raw, state and tokens empty.
- **Git:** only this handoff file changes. The portfolio repository was not touched.

## Not done, by instruction

- No dispatcher, collector or warehouse execution; no test work order; no request to
  Wikimedia to try the new image. The request-budget behaviour is proven offline only,
  against a transport that counts sends.
- The BigQuery quota was not set.
- No control record was created or enabled; the scheduler was not unpaused; no unpause
  plan was prepared.

## Warnings

1. **The new image has not run in the cloud.** Its first execution will be whatever
   step is approved next.
2. **The soak's request cap is now a real choice.** With budgets counting physical
   attempts, a cap of 14 for 14 targets allows no retry; 22 restores full retry room.
3. **The sampled-source collector is not wired to a budget.** It is inactive; wiring
   it belongs with activating any sampled source.
4. **Receipts are not readable backwards.** A receipt written by this image cannot be
   read by the previous one. All jobs are on one image, so it only matters for a
   rollback while an order is outstanding; none is.
5. The registry holds two undeployed images: the previous one (`sha256:f452c801…`,
   kept as the rollback target) and one that must never be deployed
   (`sha256:1b373a41…`).
6. Still open from earlier: the deferred freshness alert; the missed-tick review
   before any sampled source; the branch-protection decision before any secret-backed
   source; local Terraform one patch behind CI.

## Exact next approval required

C2-D preparation, as its own checkpoint. None of it is approved yet:

1. set and read back the BigQuery daily query quota, 10,240 MiB, by the owner
   operation recorded in the runbook;
2. review the soak's control record: its expiry, its caps, and in particular its
   `max_requests_per_day`, now that the number means outbound attempts;
3. review the single-change plan that unpauses the scheduler, together with the
   prepared plan that pauses it again.

STOP. C2-D has not begun.
