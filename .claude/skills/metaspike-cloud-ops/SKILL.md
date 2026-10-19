---
name: metaspike-cloud-ops
description: Operating MetaSpike on GCP - deploy workflow, Terraform roots, scheduler, control record, secrets, read-only production status, and costs. Use for any production action or question about what is running.
disable-model-invocation: true
---

# Cloud operations

Project `metaspike-prod-kc`, region `us-central1`, everything private.

## Ask the owner before

- applying a Terraform plan (always show the plan summary and its SHA-256 first);
- any plan that destroys or replaces a resource (`allow_destroy`);
- changing IAM, WIF or `infra/bootstrap` (owner-applied by hand, never through CI);
- enabling a source in the control record, or arming it;
- adding a secret version (see Secrets: the owner types the value, never Claude).

Read-only commands need no approval.

## Read-only status (paste back only the summary)

```
P=metaspike-prod-kc; R=us-central1
gcloud scheduler jobs describe ms-dispatch-15m --location=$R --project=$P --format='value(state,schedule)'
gcloud run jobs executions list --region=$R --project=$P --limit=10 --format='table(name,job,status.completionTime,status.succeededCount,status.failedCount)'
gcloud storage cat gs://$P-ops/dispatch/control.json
gcloud secrets versions list steam-web-api-key --project=$P --format='value(name,state)'
bq query --project_id=$P --use_legacy_sql=false --maximum_bytes_billed=200000000 \
 'SELECT DATE(period_start) d, COUNT(*) n FROM ms_staging.stg_period_observations
  WHERE period_start >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 10 DAY) GROUP BY d ORDER BY d'
gcloud alpha monitoring policies list --project=$P --format='value(displayName,enabled)'
```

## Deploying (GitHub Actions, `deploy.yml`, manual start on `main`)

1. Merge the PR to `main` (CI must be green).
2. Run the workflow with `operation=plan` (add `reuse_deployed_image=true` for an
   infrastructure-only change). Read the summary: resources added/changed/destroyed and
   the image.
3. Run it again with `operation=apply`, `plan_run_id` and `plan_sha256` from step 2.

`gh workflow run deploy.yml -f operation=plan` then `gh run view <id>` works from a terminal.

Repository variables the plan reads: `GCP_PROJECT_ID`, `GCP_WORKLOAD_IDENTITY_PROVIDER`,
`GCP_DEPLOY_SERVICE_ACCOUNT`, `GCP_NOTIFICATION_CHANNEL`, `TF_STATE_BUCKET`,
`SCHEDULER_PAUSED`, and the four soak variables `SOAK_*` (all or none). The workflow does
not yet pass `schedule_mode` or any `*_version` secret variable; wiring those is a
workflow change.

## Scheduler and control record

- Stop everything: `gcloud scheduler jobs pause ms-dispatch-15m --location=us-central1 --project=metaspike-prod-kc`
- The dispatcher does nothing unless `gs://metaspike-prod-kc-ops/dispatch/control.json`
  is valid, `enabled`, before `armed_until`, and enables the source by name. Fields:
  `version` (1), `enabled`, `armed_until`, `max_job_starts_per_tick`,
  `max_executions_per_day`, `sources: {name: {enabled, max_requests_per_day,
  max_targets_per_execution}}`, `pins`. It can lower limits, never raise them.
- Replace it only conditionally: read the generation, then
  `gcloud storage cp control.json gs://.../dispatch/control.json --if-generation-match=<gen>`.

## Secrets

Containers exist: `steam-web-api-key`, `igdb-client-id`, `igdb-client-secret`. A value
is added by the owner, in their own terminal, never through Claude:

```
gcloud secrets versions add steam-web-api-key --project=metaspike-prod-kc --data-file=-
```

A job receives a secret only when its version NUMBER is set in Terraform
(`steam_web_api_key_version` etc.; `latest` is refused). Locally the same values live in
the git-ignored `.env` as `METASPIKE_STEAM_WEB_API_KEY`, `METASPIKE_IGDB_CLIENT_ID`,
`METASPIKE_IGDB_CLIENT_SECRET`.

## Cost

Measured: about $1.20 a month for Wikimedia (hourly dispatcher at the 60 s minimum is
most of it). Budget alert at $20. Each Cloud Run execution bills at least 60 s; each
BigQuery statement at least 10 MiB. Cost follows the number of executions and dbt
statements, not data volume.
