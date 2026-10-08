# Handoff: latest checkpoint

This file is the short-lived handoff from Claude Code to ChatGPT. It is overwritten at
each checkpoint; Git history is the archive. It supports communication only and
replaces no code, test, audit or document.

Previous accepted checkpoint: **PHASE 4 PART C2-C.1: COMPLETE**

Current checkpoint: **PHASE 4 PART C2-D PREP.1: PLAN READY**

The deploy workflow can now make an infrastructure-only plan that reuses the image the
jobs already run. With it, a clean unpause plan exists: **0 add, 2 change, 0 destroy**,
on the deployed image, with exactly the two expected changes. It is saved and **not
applied**. The scheduler is paused, the repository variable is `true`, the control
record is disabled, and nothing ran.

## The earlier block

C2-D preparation was blocked because the only way to make a plan rebuilt the image.
Plan run `37738729342` proposed 9 changes: seven jobs moving to a freshly built digest
(`sha256:e2d35755…`, not approved and not used), plus the two changes that an unpause
really consists of. That plan was not applied and is left to expire.

Rulings since: the unpause is expected to change **two** resources (the scheduler, and
the dispatcher-silence alert, which is defined as enabled exactly while the scheduler
is not paused); the alert is not to be decoupled; and the new image is not accepted.

## Workflow design

A new `workflow_dispatch` input on the deploy workflow, `reuse_deployed_image`, boolean,
default `false`.

- **Default (false): unchanged.** The plan job builds with `--pull --no-cache`, smokes,
  scans, authenticates, pushes, and plans with the digest it pushed.
- **True: an infrastructure-only plan.** Build, smoke, scan and push are skipped. After
  authenticating, the job runs `.github/scripts/resolve-deployed-image.sh`, which reads
  the image from the jobs and accepts it only if:
  1. the project has exactly the seven MetaSpike jobs, no fewer and no other;
  2. each has one container;
  3. all seven run the same image;
  4. each job's `METASPIKE_IMAGE` equals the image it runs;
  5. the image is in `us-central1-docker.pkg.dev/<project>/metaspike/metaspike`;
  6. it is named by `@sha256:` and 64 lowercase hex characters;
  7. the registry holds exactly that digest.
  Any failure ends the job with an error. Nothing falls back to building or pushing.
- A further step, "Choose the image to plan with", always runs. It takes the built
  image in one mode and the resolved image in the other, fails if both or neither are
  present, re-checks the repository and digest pattern, and records the origin. The
  plan uses that step's output only, and the run summary states the image and its
  origin.
- The reusable verification job still runs first in both modes, so this commit's image
  is still built and scanned by CI; it is simply not pushed or planned with.

Nobody can name an image. The script takes two arguments, the project and the region.
The workflow has no input that carries an image or a digest.

## Files changed

Workflow commit `55393ea84fa7605a97b058b41205e653ccda1989`:

- `.github/workflows/deploy.yml` — the input, the conditional steps, the resolve and
  choose steps, the summary line
- `.github/scripts/resolve-deployed-image.sh` — new
- `tests/test_deploy_image_reuse.py` — new, 23 tests
- `tests/test_cloud_foundation.py` — one expectation updated
- `docs/phase4-runbook.md` — a paragraph on infrastructure-only plans

Earlier in C2-D preparation, documents only (`a82778d`): the re-pause procedure was
corrected and the quota recorded as set. No application code or Terraform changed in
either commit.

## Security analysis

- Same trigger: `workflow_dispatch` on `main` only. The job's `if` is unchanged, and a
  push still cannot reach authentication.
- Same identity: the existing restricted WIF path to `ms-deploy`. No key, no secret, no
  new permission. The two reads it makes (job descriptions, one registry lookup) are
  covered by roles it already holds; the run succeeded with them.
- WIF trust is untouched. The provider pins this workflow file on `main`; the script is
  checked out from the same commit.
- The apply job is byte-for-byte as it was in behaviour: it downloads the plan named by
  run id, refuses a hash mismatch, refuses an unannounced destroy, and applies that
  plan. It does not reference the new input.
- The mode cannot select an unreviewed or historical image: only the image all seven
  jobs currently run is eligible, and it must still exist in the registry.
- The mode reduces registry churn: an infrastructure-only plan pushes nothing.

## Tests and CI

`tests/test_deploy_image_reuse.py` runs the real script against a stand-in `gcloud`
that answers from a description of the jobs and the registry:

- resolves the one image; reads all seven jobs; makes only read calls;
- refuses when the jobs disagree; when a job's `METASPIKE_IMAGE` differs or is unset;
  when the image is in another repository, project, region or registry, is a tag, or
  has a digest of the wrong length, case, alphabet or algorithm (13 cases); when the
  registry lacks the digest or answers with another; when a job is missing, extra, or
  has two containers; and it prints no image whenever it refuses;
- by inspection of the workflow: the default still builds, scans and pushes and plans
  with the built digest; the infrastructure-only path resolves after authenticating
  and before planning; there is no fallback expression; no input can carry an image;
  the apply job, triggers, permissions and WIF conditions are unchanged.

Results: ruff and format clean; mypy clean (164 files); **1,160 passed, 5 deselected**,
dbt tests included. Push CI run `37741656087` on the workflow commit: success, with
plan, apply and identity-check skipped.

## The unpause plan (not applied)

| | |
|---|---|
| Run | `37742520427`, `operation = plan`, `reuse_deployed_image = true` |
| Head commit | `55393ea84fa7605a97b058b41205e653ccda1989` |
| Image planned with | `sha256:a8680b2be8b5e4bea48392c2b5324b44a680b4c2a4d56e2f49b836bdb979c754` |
| Image origin | reused: resolved live from all seven jobs and confirmed in the registry |
| Plan SHA-256 | `163df18caa122253ce9086d95cdb604b4cb29ac7612ed3246dd8daf4a08a63fe` |
| Artifact | `tfplan`, expires 2026-10-11T07:27:34Z |
| Counts | **0 add, 2 change, 0 destroy, 0 replace** |
| Prod state serial | 9 |

Live resolution proof: in that run the steps Build, Smoke, Scan and Push were skipped;
"Resolve the image the jobs already run" and "Choose the image to plan with" succeeded;
the plan command received `-var "image=…@sha256:a8680b2b…c754"` and
`-var "scheduler_paused=false"`. All 91 resources were refreshed. The same script, run
read-only with the owner's login beforehand, returned the same digest. The plan hash
was recomputed locally from the downloaded artifact and matches the one the run
published.

Complete diff:

```
# google_cloud_scheduler_job.tick will be updated in-place
  ~ paused = true -> false
    (name ms-dispatch-15m; 8 unchanged attributes and 2 unchanged blocks)

# google_monitoring_alert_policy.dispatcher_silent will be updated in-place
  ~ enabled = false -> true
    (8 unchanged attributes and 1 unchanged block)

Plan: 0 to add, 2 to change, 0 to destroy.
```

No Cloud Run job, image, `METASPIKE_IMAGE`, scheduler cron, time zone, target, identity
or retry setting, IAM, WIF, secret, quota, dataset, table, bucket or other alert policy
changes.

## Repository variable

`SCHEDULER_PAUSED` was `true`; set to `false` at 07:17:26 UTC for the plan run only;
restored to `true` at 07:27:44 UTC, immediately after the run finished; read back
`true` then and again at the end. No apply was requested while it was false.

## Final state

- **Scheduler:** PAUSED, `0 * * * *`, no attempt time.
- **Control record:** present, `enabled: false`, generation `1791432826876698`,
  unchanged.
- **Cloud Run executions:** 14. No Cloud Run log entry since C2-D preparation began.
- **Jobs:** all seven on `sha256:a8680b2b…c754`. Prod state serial 9 and bootstrap 9:
  nothing applied.
- **BigQuery quota:** 10,240 MiB a day on the project limit (set in C2-D preparation by
  a consumer override; the per-user limit is untouched).
- **Secrets and keys:** 0 versions; 0 user-managed keys. No IAM, schema, alert or
  scheduler drift; the silence policy is still disabled.
- **Sources called:** none.
- **Git:** `main` level with origin, clean. The portfolio repository was not touched.

## Carried forward, unchanged

- Soak window: the next period tick is **2026-10-09 03:00 UTC** (a Friday; routine
  4-day window).
- Control template, validated and not written: enabled, a finite `armed_until`,
  Wikimedia only, `max_requests_per_day` 22 (physical outbound attempts),
  `max_targets_per_execution` 14, `max_job_starts_per_tick` 1,
  `max_executions_per_day` 3, no pins.
- The stop is the one-action command, not a saved plan:
  `gcloud scheduler jobs pause ms-dispatch-15m --location=us-central1 --project=metaspike-prod-kc`,
  read back with `gcloud scheduler jobs describe … --format='value(state)'` → `PAUSED`.
- Ceilings for the soak: at most 22 outbound attempts to Wikimedia; one child start a
  tick and three a day; at most 15 new executions in all.

## Warnings

1. **The plan expires 2026-10-11T07:27:34Z** and is valid only while prod state stays
   at serial 9. Any other apply before it makes it stale.
2. **After the soak the silence alert stays enabled** until Terraform is reconciled.
   Pausing by command leaves the policy on; with no dispatcher succeeding it would
   fire about three hours after the pause unless the reconciling plan (made with
   `scheduler_paused = true`, infrastructure-only) is applied first.
3. **How the soak is launched and watched** if the owner's machine may be offline is
   undecided, as ruled. The apply needs someone to start a workflow run and to write
   the control record shortly before 03:00 UTC, and to pause afterwards.
4. The registry holds three undeployed images from earlier plan runs, one of which must
   never be deployed (`sha256:1b373a41…`) and one that was explicitly not accepted
   (`sha256:e2d35755…`). This run added none.
5. Local Terraform is one patch behind CI, so the plan was checked from the plan run's
   rendered output and its hash.
6. Still open from earlier: the deferred freshness alert; the missed-tick review before
   any sampled source; the branch-protection decision before any secret-backed source.

## Exact next approval required

The C2-D launch itself, which needs, together:

- a decision on how it is launched and supervised (warning 3);
- the exact `armed_until` for the control record;
- approval to apply this plan by run id and hash: run `37742520427`, SHA-256
  `163df18caa122253ce9086d95cdb604b4cb29ac7612ed3246dd8daf4a08a63fe`, expected 0 add,
  2 change, 0 destroy;
- a decision on when the reconciling pause plan is made and applied after the soak
  (warning 2).

STOP. The plan is not applied; the control record is not armed; the soak has not
started.
