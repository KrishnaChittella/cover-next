# Phase 4 runbook: private cloud foundation

**No cloud resource exists.** Phase 4 Part A wrote this configuration and validated it
on a laptop. Nothing has been applied, no project has been created, and no schedule
is running. This document is what an operator needs when that changes.

Nothing is public, at any stage of Phase 4. There is no public bucket, no endpoint
without authentication, and no public MetaSpike deployment. Phase 4 is internal
infrastructure for the one public release that comes later.

## What exists in the repository

| Path | What it is |
|---|---|
| `infra/bootstrap` | Applied by the owner, by hand, rarely: APIs, GitHub trust, the deploy identity, the runtime identities, the budget |
| `infra/prod` | Applied through CI only: buckets, datasets, secret containers, jobs, the one scheduler trigger, alerts |
| `Dockerfile` | One application image; every job runs it with a different command and identity |
| `.github/workflows/ci.yml` | Pull-request checks. No cloud credentials |
| `.github/workflows/deploy.yml` | By hand only: identity check, or plan (build, scan, push by digest, plan), or apply a plan named by its hash |

## Architecture in one picture

```
Cloud Scheduler (one job, every 15 minutes, starts PAUSED)
   -> ms-dispatch        decides what is due; holds NO source secret
        -> ms-steam-players   15-minute samples      (Steam key)
        -> ms-wikimedia       once a day             (no secret)
        -> ms-catalogue       daily metadata         (IGDB credentials, Steam key)
        -> ms-warehouse       hourly load + dbt
        -> ms-features        hourly feature engine
        -> ms-parity          daily state rebuild and comparison
```

Each job has its own service account. The dispatcher may start the other jobs and
cannot read what they read. No job may start another job.

All persistent data is in `us-central1`, United States: five regional buckets and six
regional BigQuery datasets (not the `US` multi-region).

## Before Part A2: what the owner must have

1. A billing account, and a decision on the project id. Project ids are globally
   unique, so the id is a variable; the display name can be "MetaSpike Production".
2. **A private GitHub repository** for MetaSpike. The repository's numeric id and the
   owner's numeric id are needed for the trust configuration; names are not the
   boundary. No GitHub environment or reviewer is relied on (see "Who can deploy").
3. `gcloud` and `terraform` installed locally, for the one-time bootstrap only.

## One-time bootstrap (Part A2, by hand)

Terraform cannot create the bucket that holds its own first state, so the state bucket
is made by hand, once:

```
gcloud projects create <PROJECT_ID> --name="MetaSpike Production"
gcloud billing projects link <PROJECT_ID> --billing-account=<BILLING_ACCOUNT_ID>
gcloud storage buckets create gs://<PROJECT_ID>-tfstate \
    --project=<PROJECT_ID> --location=us-central1 \
    --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update gs://<PROJECT_ID>-tfstate --versioning
```

Then the bootstrap root, once, with the owner's own login:

```
cd infra/bootstrap
terraform init -backend-config="bucket=<PROJECT_ID>-tfstate"
terraform apply -var-file=<a tfvars file that is NOT committed>
```

Its outputs (the workload identity provider, the deploy service account and the
notification channel) are stored as GitHub repository **variables**. They are
identifiers, not credentials. No service-account key is ever created or downloaded.

From then on `infra/prod` is applied only by the deploy workflow.

## Who can deploy

Google accepts a GitHub token for the deploy identity only when every one of these
holds: this repository and this owner (by immutable numeric id), while the repository
is private, on `refs/heads/main`, from `.github/workflows/deploy.yml` as it exists on
main, started by hand (`workflow_dispatch`), by the owner's own account (numeric id).

A push to main runs verification only. The jobs that authenticate do not start for a
push, and Google would refuse a push's token if they did.

A deployment is always two manual runs of the deploy workflow:

1. `operation = plan` builds, scans and pushes the image by digest, plans, and
   publishes the plan with its run id and SHA-256. It changes nothing.
2. `operation = apply`, given that run id and that SHA-256, applies exactly that plan.
   A plan that destroys or replaces anything also needs `allow_destroy`.

`operation = identity-check` (the default) only proves federation works.

The image registry is created by the bootstrap root, because the production root
cannot plan without an image digest and an image cannot be pushed to a registry that
does not exist.

## Security review items before Part C

Nothing below blocks the foundation, because no job runs and no secret has a value
yet. Each must be reviewed before unattended collection starts.

1. **The deploy identity is privileged.** `ms-deploy` cannot read a secret's value, but
   it can set IAM on the three secret containers and it can deploy jobs that run as
   the identities that do read them. Once secret versions exist, whoever controls
   `ms-deploy` could reach them. The control is the federation condition: one
   workflow, on main, started by hand, by the owner. It may act as the eight runtime
   service accounts only, each granted individually, never project-wide.
2. **Unfixed base-image vulnerabilities.** The image has 0 fixable HIGH or CRITICAL
   findings, and the scanner also lists 53 HIGH and 2 CRITICAL findings in Debian
   packages for which no fix has been published. The scan policy is unchanged.
3. **Google's default compute service account** appeared when the Cloud Run API was
   enabled. It holds no project role and has no key, and no MetaSpike job uses it.
4. **The OIDC issuer** is `https://token.actions.githubusercontent.com` with no
   trailing slash, as in GitHub's own discovery document and the Terraform provider's
   example. Google's deployment-pipeline documentation shows a trailing-slash
   example; it is not used. The live identity check is the operational proof.
5. `actions/checkout` v4 runs on a Node.js version GitHub now warns about.

## Loading secrets

Terraform creates three empty secret containers and nothing else:
`steam-web-api-key`, `igdb-client-id`, `igdb-client-secret`. A value is added once,
by the owner, from standard input, so that it never appears in shell history, a file,
Terraform state, Git, or the image:

```
gcloud secrets versions add steam-web-api-key --project=<PROJECT_ID> --data-file=-
```

Type or paste the value, then end input. Repeat for the other two. Do not put a secret
in a `.tfvars` file, a workflow, a test, or a message.

Adding a version does not give it to a job. Each job is handed a source secret only
when the exact version NUMBER is supplied to the production root
(`steam_web_api_key_version`, `igdb_client_id_version`, `igdb_client_secret_version`).
They are unset by default, and while unset the jobs carry no reference to the secret
at all: Cloud Run will not create a job that points at a version that does not exist.
`latest` is refused, so a deployment always states which version it runs with. A
version number is metadata, not the secret, and may appear in Terraform.

The short-lived IGDB OAuth access token is not a managed secret. The catalogue job
mints it from the client id and secret and caches it in the private tokens bucket,
which only that job's identity can read.

## The one-action stop

Pause the single scheduler job. Nothing else in the system starts work on its own.

```
gcloud scheduler jobs pause ms-dispatch-15m --location=us-central1 --project=<PROJECT_ID>
```

or set `scheduler_paused = true` and deploy. The variable defaults to `true`: after
the first apply nothing is collected until it is deliberately released.

## Policy gates before Part C (unattended collection)

A source is scheduled only when its policy clears `private_collection`.

**Wikimedia pageviews:** cleared.

**IGDB:** conditionally cleared, for identity and catalogue metadata only. The Twitch
Developer Services Agreement (Schedule 1) is the general rule and limits storage to a
short cache unless specifically authorised; IGDB's documentation gives that
authorisation for its metadata. Do not collect fields MetaSpike does not need. Public
launch and commercial production remain blocked.

**Steam (current players and catalogue):** NOT cleared. All of these must be true first:

1. A privacy/data notice with a MetaSpike / Steam Data section is public at
   https://krishnachittella.dev/privacy
2. Persistent Steam Data is stored only in the United States.
3. GCS and BigQuery data are in `us-central1`.
4. The Steam API key registration identifies the MetaSpike / krishnachittella.dev
   application.
5. Collection stays far below 100,000 Web API calls a day.
6. Nothing Steam-derived is published.
7. No Steam-derived commercial feature exists.

Two facts are recorded together. The Steam Web API Terms expressly license
implementing the Web API in an Application. The Steam Subscriber Agreement, section
4.C, restricts automation broadly ("in any manner"). The operating interpretation is
that low-volume, private, licensed API collection is permitted under the API-specific
terms once the conditions above hold. That is a residual policy ambiguity, and a
clarification request to Valve is to be sent before any public use. Public launch and
commercial production stay blocked for Steam-derived metrics.

### Draft privacy notice text (NOT published; owner action before Part C)

> **MetaSpike and Steam data.** MetaSpike collects aggregate data from the Steam Web
> API. It may store the number of players currently in a game, and public Steam app
> identifiers and names. Each observation is stored with the time it was made, and
> these observations are retained so that changes over time can be analysed.
> Persistent Steam data is stored in the United States. MetaSpike does not collect
> Steam user identifiers, profiles, friends lists or play histories. MetaSpike is
> independent of Valve Corporation and is not affiliated with, endorsed by or
> sponsored by Valve. Steam data is provided "as is", without warranty of any kind.
> Stored Steam data will be removed when Valve requires it or when MetaSpike ends its
> licensed use of the Steam Web API. Steam and the Steam logo are trademarks of Valve
> Corporation.

## Shadow-only features

`baseline_rules_v1` is internal, reproducible and uncalibrated. Its change-point
output is SHADOW only: it is computed and stored for study, and nothing may score,
promote a monitoring tier, notify or publish from it. Code that consumes features
reads them through `metaspike.features.access`, which refuses a shadow-only feature
to every operational consumer. The first decision on a recalibrated rule version is
taken no earlier than eight weeks of real history; 28 days is a sanity check.

## Cloud Run billing: what is known, and what will be measured

The design does not rest on any assumption about a minimum billed duration. The
dispatcher and the collectors are separate jobs because least privilege asks for it.

What Google's Cloud Run pricing page said when it was read on 2026-10-05:

- "Cloud Run charges you only for the resources you use, rounded up to the nearest 100 millisecond."
- Tier 1 (which includes `us-central1`): $0.000018 per vCPU-second and $0.000002 per
  GiB-second, with a monthly free allowance for jobs of 240,000 vCPU-seconds and
  450,000 GiB-seconds per billing account.
- Under "Billable instance time for Cloud Run jobs", the same page also said that jobs
  "are billed at the Instance-based billing rate, for the entire lifetime of any
  instance started, with a minimum of 1 minute."

Cloud Run jobs need at least 1 vCPU. The two statements above were both on the page;
which one governs a job that finishes in a few seconds is not something to assume in
either direction. **Billable instance time per job is therefore measured in Part A2**
from the `container/billable_instance_time` metric and the first invoice lines, and
the cost for 8, 100 and 500 sampled apps is recalculated from those measurements
before unattended collection is enabled. Earlier estimates are withdrawn until then.

One thing does not depend on that question: 500 apps sampled at one request per second
is about 500 seconds of real work every 15 minutes, which is materially expensive
whatever the rounding. Anything expected above $20 a month needs the owner's explicit
approval.

## Budget

One budget, $20 a month, with email alerts at 25, 50, 75 and 100 percent of actual
spend and on a forecast of exceeding it. A budget warns. It stops nothing: the stop is
the scheduler pause above.

## What cannot be checked without a real project

`terraform validate` proves the configuration is internally consistent and matches the
provider's schema. It does not prove that an API is enabled, that an IAM condition
behaves as intended on object listing, that a name is free, or what a plan would
change. Those are verified in Part A2 by reading the first plan before approving it.
