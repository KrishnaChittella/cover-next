# Phase 4 runbook: private cloud foundation

**Nothing is collecting.** The foundation was applied in Part A2 and is inert: no job
has ever run, the one scheduler trigger is paused, no secret has a value, and every
bucket and table is empty. Part B added application code and a dbt project that run
offline only. "No cloud resource exists" was true when Part A wrote this document and
is no longer true; what is true is that no cloud resource is doing anything.

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
either direction. Billable instance time per job was to be measured in Part A2 from
the `container/billable_instance_time` metric and the first invoice lines. **It was
not measured, because no job has run.** The Part A2 cost model is therefore an
estimate built on the documented one-minute minimum, and re-measuring it from real
job durations is a hard gate before unattended collection (see below).

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

## Part B: warehouse history and the persistent catalogue

Part B is application code and dbt. It was developed and tested offline; nothing in
it has run in the cloud, and it changed no cloud resource.

| Path | What it is |
|---|---|
| `src/metaspike/warehouse/landing.py` | The landing format: immutable NDJSON batches, each with a manifest |
| `src/metaspike/warehouse/loader.py` | The idempotent loader, its receipts (the load manifest) and replay |
| `src/metaspike/warehouse/stores.py` | The storage contracts kept in the warehouse |
| `src/metaspike/warehouse/objects.py`, `tables.py`, `auth.py` | Narrow ports for object storage and tables, in-memory implementations, and REST adapters for Cloud Storage and BigQuery |
| `warehouse/` | The dbt project and its own locked tool environment |
| `src/metaspike/resolution/blocking.py` | Candidate blocking for the resolver |
| `src/metaspike/catalogue/incremental.py` | Catalogue generation with blocking and incremental resolution |
| `src/metaspike/catalogue/persistence.py` | Events, snapshots and the conditional `CURRENT` pointer |

### Object layout

```
landing bucket   {source}/{table}/dt={YYYY-MM-DD}/{run_id}/part-NNNN.ndjson
                 {source}/{table}/dt={YYYY-MM-DD}/{run_id}/part-NNNN.manifest.json
raw bucket       {source}/dt={YYYY-MM-DD}/{run_id}/{name}        (only where raw storage is allowed)
ops bucket       warehouse/loads/{dataset.table}/{digest}.json   (one receipt per loaded batch)
state bucket     catalogue/CURRENT
                 catalogue/generations/{generation}.json
                 catalogue/snapshots/{generation}.json
                 catalogue/events/{generation}.ndjson
```

Bindings, decisions and quarantine events are stated about a source record, so their
warehouse rows land under that source's prefix (`igdb/binding_events/...`) and obey
that source's policy. Lifecycle and entity events are about canonical entities; they
are in the state bucket only, because no table and no landing prefix exists for them.

### Identities that cannot list

An identity whose storage grant is conditional on an object-name prefix is expected
to be refused a listing of the bucket, because the condition does not match the
bucket itself. This is unproven until the live smoke test, and the design assumes
the refusal:

- A collector writes a batch at a name derived from its run id. It never reads or
  lists the landing bucket.
- The catalogue job reads `catalogue/CURRENT` by name and follows it to a snapshot.
  History is walked from each generation record to the one before it.
- The loader's receipts are read by exact name. Only the landing bucket is listed,
  and only by `ms-warehouse`, whose grant there is on the whole bucket.
- A collector cannot find out from landing whether a slot was already sampled: it
  can neither list nor read there, and the warehouse sees a batch only after the next
  load. It reads a completion marker instead, by exact name, under its own operations
  prefix (see "Completion markers" below).

### dbt

```
uv run --project warehouse --locked dbt build --selector hourly \
    --project-dir warehouse --profiles-dir warehouse
```

The default target is `offline` (DuckDB). The `prod` target is BigQuery with the job's
own identity and a 1 GiB maximum-bytes-billed limit on every job. dbt computes no
feature and no score. A model reads from one of three starting points:

- **Normal run:** the three whole UTC days before now.
- **Late data:** the day the late rows reach back to, passed in by the warehouse job
  (see "Late data" below).
- **First build or full refresh:** ALL retained history. The starting point is not a
  date written anywhere. It is read from the warehouse: the earliest day-partition
  that holds a row in the raw tables a model is built from. That read is of partition
  metadata, so the raw tables still require a partition filter on every data read.
  With no raw history the build is simply empty. `history_start` can be passed to
  replay deliberately from a later point; it is unset by default.

### Catalogue scope

Only the bootstrap entities and records tied to them by an explicit link are resolved.
Broad catalogue discovery is switched off in code (`BROAD_DISCOVERY_ENABLED = False`)
and stays off until the owner approves it.

## Part C1: job commands

Part C1 made the seven jobs runnable and added a safe permission check. Nothing runs
unattended: the scheduler is still paused.

| Command | Job | What it does |
|---|---|---|
| `dispatch` | `ms-dispatch` | Records what it intends to be collected, takes the tick's lease, starts the jobs that are due |
| `collect-wikimedia` | `ms-wikimedia` | Daily pageviews for every entity with an article |
| `collect-steam-players` | `ms-steam-players` | **Refuses**: Steam is not cleared for private collection |
| `collect-catalogue` | `ms-catalogue` | Collects cleared catalogue sources, resolves, persists. Refuses the Steam portion |
| `warehouse` | `ms-warehouse` | Loads landed batches, then runs dbt from the image's own dbt environment |
| `features` | `ms-features` | Continues baseline states for a cleared sampled source. Nothing to do today |
| `parity` | `ms-parity` | Rebuilds baseline states and compares. Nothing to do today |
| `smoke` | any | Checks the identity's storage permissions. Calls no source |

A job reads its configuration from the environment, writes a start and a finish record
to the run log, logs one JSON object per line, and exits non-zero on failure. A job
whose source policy does not clear the stage exits with code 5 before it builds
anything that needs a credential. There is no policy override: the policies are the
reviewed ones in the source modules.

### Completion markers

For each target and slot the order is fixed: read the marker by exact name; if a valid
one exists, skip the source request; otherwise ask, land the result, commit the landing
manifest, and only then create the marker, create-only. A marker holds which run and
which landed object completed the slot, and never a value.

This is not exactly-once. A run that dies after landing and before marking leaves a
durable result and no marker, and the retry asks the source again. What is guaranteed
is narrower: once a result is durable and marked, a normal retry does not ask again;
and a marker can never cause a sample to be skipped, because it cannot exist before
the batch it names. A failure or a refusal is never marked.

### Expected targets

The dispatcher records, for each tick, which targets it intended to be collected in
which slot (`ms_ops.expected_targets`). dbt reconstructs expected slots from that
ledger and joins what actually happened to it. A target that was scheduled and never
attempted is therefore `not_sampled`; a target that was not scheduled has no expected
slot; and a later change of eligibility adds rows for later slots without touching
earlier ones. `not_sampled` exists only in a view and in counts. It is never a row.

A source that is not cleared is never planned, so nothing about it is recorded as
intent either.

### Late data

The models normally recompute three days. When the warehouse job loads rows older
than that, it records a pending rebuild by name BEFORE it writes the batch's receipt,
passes the day the late data reaches back to into dbt, and clears the record only
after dbt has succeeded. A crash or a failed dbt run keeps the record and the job
exits non-zero. No successful run leaves known late data out of the marts.

### The image

One image for all seven jobs. It carries two separately locked environments that do
not import each other: the application at `/app/.venv`, and dbt with the BigQuery
adapter at `/opt/dbt`. `metaspike warehouse` starts `/opt/dbt/bin/dbt` as a separate
process. Both are audited and both are in the image scan. The image runs as uid 10001
and cannot write to `/app` or `/opt/dbt`.

### Dataset access

Each BigQuery dataset has one authoritative policy in Terraform: BigQuery's
`projectOwners` special group owns it, and the three warehouse-facing runtime identities hold exactly the roles listed
in `dataset_grants`. The entries BigQuery adds on its own (the creating identity as
owner, project editors, project viewers) are not in the policy and are removed on
apply. `ms-deploy` manages tables through its project-level role and holds nothing at
dataset level.

### What the deploy identity may do to data stores

`ms-deploy` holds two custom project roles instead of the predefined admin roles:
`msDeployBigQueryConfig` (datasets, tables, dataset access) and `msDeployBucketConfig`
(bucket settings, bucket IAM). With them it cannot read or write a row, submit a query
job, or read, list, write or delete an object; its only object access is the Terraform
state bucket. It also cannot delete a dataset, a table or a bucket, and it cannot drop
a column (the provider does that with a query job). Those are the owner's decisions,
made with the owner's login. Both roles live in `infra/bootstrap`, so changing either
is an owner-run bootstrap apply: `ms-deploy` cannot grant itself a role.

Two things remain true and are not hidden. The roles can set IAM on a dataset or a
bucket, so the identity could grant itself data access; that is a recorded IAM change.
And changing a table's or dataset's expiry is a configuration change that removes data.

### Running the permission check

It runs as the job's own identity, by overriding one job execution's arguments. No
impersonation is granted to anyone, and no grant is widened to make the check tidy.

Each probe is judged against the shape of grant the identity holds, and gets one of
four verdicts: `ALLOWED`, `EXPECTED_DENIAL`, `UNEXPECTED_FAILURE`, `UNEXPECTED_ALLOW`.
Only the last two fail the check.

| Shape | Grant | Create | Read | Overwrite | Delete | List | Cleanup |
|---|---|---|---|---|---|---|---|
| `user` | `objectUser` on a prefix | yes | yes | yes | yes | no | the runtime identity |
| `creator` | `objectCreator` on a prefix | yes | no | no | no | no | an administrator, afterwards |
| `viewer` | `objectViewer` on a prefix | no | yes | n/a | n/a | no | nothing is written |
| `bucket_viewer` | `objectViewer` on the bucket | no | yes | n/a | n/a | yes | nothing is written |
| `bucket_user` | `objectUser` on the bucket | yes | yes | yes | yes | yes | the runtime identity |

Every prefix shape also requires a create outside the prefix to be denied.

A create-only identity cannot delete what it wrote. That is what keeps landed data
immutable, so its refused delete is an `EXPECTED_DENIAL` and the check passes. The
object stays under `<prefix>_smoke/`, where the loader never looks, and the result
names it. The project owner removes it afterwards with access the runtime identity
never had:

```
gcloud storage rm gs://<PROJECT_ID>-landing/<prefix>_smoke/<run-id>.txt
```

Read is probed on an object that exists. An identity that cannot create needs one
placed for it first (`--read-object`), because the service answers "forbidden", not
"not found", to an identity that may not list. Listing is reported and judged but
does not fail the check unless `--strict-list` is given. Where another reviewed
grant independently gives read or listing on the same bucket, say so with
`--expect-read allowed` or `--expect-list allowed`.

A single probe set can be run on its own (`--bucket`, `--mode`, `--allowed-prefix`,
`--outside-prefix`). The normal way is a suite: one execution per runtime identity
that runs every probe set that identity's grants call for.

```
gcloud run jobs execute ms-wikimedia --region us-central1 --project <PROJECT_ID> --wait \
    --args=smoke,--suite=ms-wikimedia,--run-id=<RUN_ID>
```

- `--run-id` is required and appears in every object name:
  `<prefix>_smoke/<RUN_ID>/<profile>-probe.txt`. Nothing a loader or a collector
  reads lives under `_smoke/`.
- A suite refuses to run as any job but its own.
- It stops at the first unexpected verdict and still prints one result document,
  saying where it stopped. Listing is required to be as expected.
- Read-only shapes read a fixture an administrator places first, at
  `<prefix>_smoke/<RUN_ID>/<profile>-fixture.txt`. The suite never creates one. If a
  fixture is missing, an identity that can list is told so; one that cannot is told
  "forbidden", which the result flags as indistinguishable from a denial.
- The result lists every object left behind with `cleanup_actor: administrator`:
  the create-only objects and the fixtures. They are removed afterwards by exact name.
- The smoke path builds no source client and no warehouse client. It reaches the
  metadata server for the job's own token, and Cloud Storage.

## Hard gates before Part C (unattended collection)

Every one of these must be closed, in writing, before the scheduler is enabled.

1. **Live prefix-IAM smoke test.** Once the application has a safe runtime smoke
   command, run it as an actual runtime service account: one harmless write inside an
   allowed prefix must succeed, one outside must be denied, the allowed object is
   cleaned up, and no long-lived impersonation permission is added. The same run
   must establish whether a prefix-conditional identity can list, which the design
   above assumes it cannot.
2. **BigQuery dataset IAM review.** Decide whether MetaSpike manages dataset access
   authoritatively in Terraform, removing the creator (`ms-deploy` OWNER) and default
   `projectOwners` / `projectWriters` / `projectReaders` entries before collection
   starts.
3. **The 53 HIGH / 2 CRITICAL unfixed image findings.** Review each before any job
   runs unattended. The scan policy is unchanged.
4. **`ms-deploy` as a privileged identity.** It can set IAM on the secret containers
   and deploy jobs that run as the identities that read them. Review before any
   secret has a version.
5. **Alert delivery proof.** Send a test notification through the channel and confirm
   it arrives. No alert or budget threshold has ever fired.
6. **Steam and IGDB policy conditions.** Every Steam condition listed under "Policy
   gates" above, the Valve clarification request, and IGDB collection limited to the
   catalogue fields MetaSpike uses. The real Steam policies refuse private-collection
   storage in code until they are changed by a reviewed commit.
7. **Cost re-measurement.** Measure billable instance time from real executions and
   recompute the 8 / 100 / 500 app model. Anything expected above $20 a month needs
   the owner's explicit approval; 500 apps at a flat 15-minute cadence is estimated
   above it.
8. **Node.js action migration.** `actions/checkout`, `google-github-actions/auth`,
   `hashicorp/setup-terraform` and `actions/upload-artifact` as pinned target a
   Node.js version GitHub is retiring.

13. **Deleting raw rows needs a rebuild to be complete.** Raw warehouse tables are
    append-only during normal operation. If a source, privacy or data-removal request
    requires deleting raw rows, the deletion workflow MUST be followed by an
    appropriate full or partition rebuild before the removal is considered complete.
    An ordinary incremental dbt run is NOT sufficient: it replaces only the partitions
    present in its new result, so deleted raw rows can remain represented in derived
    tables. This is part of any future data-removal or privacy operation, including
    removing Steam data if Valve requires it.
14. **Wikimedia steady-state window.** The supervised bootstrap collection fetches
    about 90 days of history per article, which is right for a first run and for the
    single S3 measurement. Before unattended scheduling, decide a cheaper steady-state
    window and cadence, so that a routine run does not refetch 90 days and trigger a
    90-day rebuild of the period models every time. The collector already takes the
    window from `METASPIKE_WIKIMEDIA_DAYS`; what is missing is the decision.

Part B added these, which are gates for the same reason:

9. **First contact with the real services.** The Cloud Storage and BigQuery adapters
   and the BigQuery dialect of the dbt models have only been exercised against
   stand-ins. Before anything depends on them: one create-only write, one
   conditional replace and one refused overwrite against a real bucket; one load job
   and one bounded query against a real table, including a JSON column and a
   repeated column; and `dbt build` against BigQuery on an empty dataset.
10. **dbt in the image.** Built in Part C1: one image, two locked environments. It is
    deployed only by a reviewed plan.
11. **Job commands.** Built in Part C1. Each still has to run once under supervision
    before anything is scheduled.
12. **Broad catalogue discovery** stays off until the blocked resolver's equivalence
    results are accepted and discovery is approved.
