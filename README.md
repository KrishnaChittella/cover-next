# MetaSpike

MetaSpike ultimately answers one question:

> What game has the strongest content opportunity right now, and why?

This repository is the data-intelligence pipeline behind that question. It is a
separate project from the portfolio site that will eventually display its output
at `krishnachittella.dev/metaspike`.

## Status

**Internal engineering foundation. Not a product release.** MetaSpike has one
public release, after the full approved scope is built and validated. Everything
here, including `metaspike_score_v0` and artifact schema `1.x`, is an internal
milestone: nothing outside this repository may depend on it.

Phase 1 of the roadmap (foundation refactor) is complete: the code no longer
assumes one source or daily data, and v0 output is unchanged.

Phase 2 (canonical entity resolution and lifecycle) is built:

- **Identity:** UUIDv4 canonical ids with a frozen legacy mapping, aliases, merge
  and split lineage, and versioned point-in-time source bindings.
- **Resolver:** deterministic evidence and hard gates, no learning and no fuzzy
  matching. Decisions are `AUTO_MATCH`, `NEW_ENTITY`, `QUARANTINE`,
  `MANUAL_OVERRIDE`, or "not an entity" for related products.
- **Quarantine:** an unresolved source record gets no canonical id, is never
  scored or published, and is re-evaluated automatically when its evidence changes.
- **Golden set:** 200 cases built from recorded IGDB and Steam responses, frozen
  (version 3, hash pinned, with a changelog of every corrected label and the hash
  of the version it replaced). A label is never changed because the resolver fails it.
- **Lifecycle:** deterministic, versioned rules that never read a score, with
  append-only history.
- **Generated catalogue:** the runtime catalogue is produced by the generator; the
  15 original entities are bootstrap material and keep their identities.

Resolver quality on the frozen set (`uv run metaspike evaluate-resolver`):
0 false automatic merges, precision 52/52, new-entity accuracy 70/70. Recall is
reported in three parts and never blended: 44/44 on positives judged with their real
evidence, 0/18 on the masked stress test (positives whose id link is deliberately
withheld), 44/62 overall. Every miss is a safe quarantine. The masked positives are
all quarantined because Steam's catalogue carries no dates or companies to
corroborate a title with.

A store URL is identity evidence only in one strict form: `store.steampowered.com`
exactly, path `/app/<id>` with an optional single name segment. It is an assertion,
not a link. It can satisfy the id-link path only when a structured id on an edition
of the same game confirms it; unconfirmed, it quarantines, and it blocks an
otherwise clean match that it contradicts. It never overrides a veto.

`uv run metaspike audit-bootstrap-bindings` lists, for each of the 15 original
entities, the source ids that recorded evidence proves and whether the entity is
eligible for Steam telemetry (`yes`, `no` or `unknown`). No Steam id is inferred
from a title.

Phase 3 Part A (sample-now sources and Steam concurrent-player history) is built:

- **Source modes:** a source is `FETCH_HISTORY` (Wikimedia) or `SAMPLE_NOW` (Steam
  concurrent players). A sampling adapter takes one source-native id and no window:
  it cannot be asked for the past and cannot backfill.
- **Samples:** every attempt is an append-only event with its own id. A logical slot
  (source, metric, source-native id, slot start) can hold several attempts; the
  earliest valid observation speaks for it. Missing is never zero: an unsampled
  slot is `not_sampled`, and only a successful response reporting 0 is a zero.
- **Sample store:** JSON Lines, one file per app per UTC month, keyed by the
  source's own id. Reads are point-in-time by when an attempt was stored.
- **Rebinding:** which entity a sample belongs to is resolved from the mapping
  history at the moment it was observed. Stored samples are never rewritten.
- **Eligibility:** `eligible`, `ineligible` or `unknown`, derived from trusted
  bindings at a moment, never from a title. Today the bootstrap scope yields eight
  apps; GTA V and Minecraft are `unknown`. An entity with several eligible apps has
  each sampled and stored separately, and no combined series
  (`multi_app_combination_unresolved`) until a combination rule exists.
- **Policy:** `steam_current_players` is cleared for private development only. Its
  derived-metrics and commercial permissions are `unknown`, so nothing sampled or
  computed from it can be registered for a public stage or enter a published
  artifact. An unanswered permission is never read as granted.

Sampling is one-shot. No scheduler, cron entry or background process is installed,
and no history is being accumulated; unattended collection belongs to Phase 4.

Phase 3 Part B (sampled-signal baselines and cross-source normalisation) is built,
as INTERNAL development features validated on synthetic and replayed histories:

- **One feature engine.** It folds contiguous buckets of any signal through a state.
  What differs between hourly concurrent players and daily pageviews is a
  per-signal configuration under a rule version (`baseline_rules_v1`). A feature
  with no approved definition at a granularity is null with the reason
  `unsupported_for_granularity`; daily pageviews get no hourly constants.
- **Aggregation.** Resolved 15-minute slots become hourly means and maxima (an hour
  needs 3 of 4 samples) and daily peak, typical and mean of the valid hourly means
  (a day needs 18 valid hours and 72 of 96 samples). Concurrent players are a level
  sampled over time and are never summed.
- **Order of evaluation.** A bucket is compared with baselines built only from
  buckets before it, then used to update the running state. It never influences the
  baseline it is judged against, and appending later data never changes an earlier
  result.
- **Baselines and seasonality** in `log1p` space: trailing median, MAD and a floored
  robust scale over 24h, 7d and 28d; the expected value from the same hour of week,
  else hour of day, else the trailing level (daily signals: same weekday), with the
  basis recorded.
- **Lift, velocity, acceleration, trend.** Log lift against the expected level;
  velocity is the rate of change of smoothed lift per day and acceleration the rate
  of change of velocity; trend is a Theil-Sen slope of daily typical levels.
- **Change points.** Incremental two-sided CUSUM on clipped, standardised,
  deseasonalised residuals, with a 3-of-4 confirmation rule. A confirmed surge is
  kept out of later baselines for at most seven days.
- **Normalised signals, families and applicability.** Each signal is described
  against its own history only. A family that cannot observe an entity is
  inapplicable, never missing. Signal quality and evidence breadth are separate
  outputs; no public Confidence formula is defined.
- **State contract.** A serialisable `BaselineState` carries the bounded window that
  medians and percentiles need, plus EWMA and change-point state. Resuming from a
  serialised state gives bit-identical results to folding the full history.

Steam-derived features are unpublishable under the current policy and are absent
from every artifact and schema. Maturity states, reason codes and the constants
that were introduced in this part without separate approval are listed in
`src/metaspike/domain/baseline_rules.py`.

Phase 4 Part A (signal semantics v2 and private cloud foundation code) is written,
and Part A2 applied the foundation to one private project. **Nothing is collecting**:
no job has run, the scheduler is paused, no secret has a value, and every bucket and
table is empty.

- **`signal_semantics_v2`** changes how a signal is read, not what the engine
  computes. Raw freshness (a sampled source is stale when its latest valid observation
  is older than two sampling slots, 30 minutes for Steam) is separate from aggregate
  freshness (bucket age and `valid_through`). Availability has six states:
  available, missing, stale, inapplicable, unknown and unresolved. An entity with
  several Steam apps is unresolved and adds nothing to observed evidence breadth.
  `signal_semantics_v1` remains callable.
- **`baseline_rules_v1` is pinned.** A fixed synthetic history, its complete
  evaluations and its `BaselineState` are stored with content hashes; any drift fails
  the tests. v1 is internal, reproducible and uncalibrated, and is never edited.
- **Shadow-only guard.** Change-point output is computed and stored but may not be
  used by a score, Event Heat, monitoring tiers, notifications or public artifacts.
  Consumers read features through `metaspike.features.access`, which enforces it.
- **Infrastructure as code** in `infra/`: one production project in `us-central1`,
  private buckets and regional BigQuery datasets, secret containers with no values,
  one identity per job, a dispatcher that cannot read source secrets, one scheduler
  trigger that starts paused, GitHub trust by OIDC with no service-account key.
- **One image** (`Dockerfile`) and two workflows: pull-request CI with no cloud
  credentials, and a deploy workflow that only the owner can start by hand and that
  applies only a plan named by its hash.

Phase 4 Part B (warehouse history and the persistent catalogue) is built and tested
offline. It has not run in the cloud.

- **Landing and loading.** Immutable NDJSON batches with manifests, checked against
  each source's policy (no Steam payload can be landed), and a loader that appends
  each batch at most once and can rebuild a table from landing.
- **Warehouse stores.** The sample, observation and source-record contracts kept in
  the warehouse, each tested against its local counterpart.
- **dbt** (`warehouse/`): staging, de-duplication, resolved sample slots, coverage and
  source health. The SQL slot rule is held to the Python one on the same attempts.
  dbt computes no feature and no score.
- **Catalogue.** Candidate blocking and incremental resolution that return exactly
  what all-pairs resolution returns, and a catalogue stored as append-only events,
  immutable snapshots and one conditionally replaced pointer. Only bootstrap
  entities and records linked to them are in scope; broad discovery is off.

Phase 4 Part C1 (job commands and gate closure) makes the seven jobs runnable and
closes the gaps Part B left open. Nothing is scheduled.

- **Seven commands and `smoke`.** Each job refuses to run unless its source's reviewed
  policy clears the stage. The Steam collector refuses today.
- **Completion markers** stop a normal retry from asking a source again once a result
  is durable, without ever letting a marker stand in for a result.
- **Expected targets.** The dispatcher records what it intended to collect, so a slot
  nobody attempted is known to have been expected.
- **Late data** is rebuilt into the models by the warehouse job, or the job fails.
- **One image** with the application and dbt in separately locked environments.
- **Dataset access** is authoritative in Terraform.

See `docs/phase4-runbook.md` for the bootstrap steps, secret loading, the one-action
stop, the layout, the job commands and the hard gates that must clear before
unattended collection.

Part C1 is complete and its security closeout passed. Each job has run once under
supervision against the real services, and one real Wikimedia collection (14 articles,
90 days, 1,260 observations) is retained as the first source history. Nothing runs on
its own: the scheduler is paused and no secret has a value. See
`docs/phase4-c1-implementation-report.md` for what was built and proven.

Part C2-A (unattended scheduling, offline) is built and not switched on: a control
record that fails closed, a partitioned schedule read only where work can be due,
bounded dispatch with work orders, source health and a circuit breaker, steady-state
Wikimedia collection, and a scale simulation at up to 50,000 catalogued entities.
Tiering is shadow only. Nothing is deployed; see `docs/phase4-c2-plan.md`.

## What is implemented

An end-to-end, locally runnable pipeline. One command:

```
uv run metaspike run
```

1. loads a canonical game catalogue (15 entities)
2. collects real daily pageviews from the Wikimedia Analytics API
3. validates and normalises them into gap-free daily series
4. computes reproducible attention features, each game against its own normal
5. produces a versioned score, `metaspike_score_v0` (0–100)
6. produces a separate confidence value (0–100, capped at 60 with one source)
7. renders deterministic, template-based "why now?" explanations
8. publishes validated, versioned JSON artifacts atomically

**The v0 score is not a creator-opportunity score.** It measures relative
attention anomaly plus recent momentum from a single source. Every artifact says
so (`scope_note`), and confidence is capped accordingly.

## What it does not implement yet

No unattended collection, no accumulated history, no R2 publishing, no ML, no LLM, no
public web UI and no event detection. The cloud foundation exists and is idle; the
warehouse code and dbt models exist and have run only offline. Nothing is public
before the single production release.

## Setup

Requires [uv](https://docs.astral.sh/uv/). uv installs and manages the project's
own Python 3.12 (pinned in `.python-version`); your system Python is not touched.

```
uv sync
```

## Commands

| Command | What it does |
| --- | --- |
| `uv run metaspike run` | Full pipeline against live Wikimedia, publishes to `artifacts/` |
| `uv run metaspike run --replay tests/fixtures/wikimedia --as-of 2026-10-03` | Full pipeline offline, against recorded responses |
| `uv run metaspike validate-catalogue` | Validate the seed catalogue |
| `uv run metaspike collect` | Collect only, into `.metaspike/cache/` |
| `uv run metaspike build` | Score and publish from the cached collection |
| `uv run metaspike validate-artifacts` | Validate what is published in `artifacts/` |
| `uv run metaspike export-schemas` | Regenerate `schemas/` from the contract models (`--check` to verify) |

`uv run metaspike evaluate-resolver` evaluates the resolver on the frozen golden
set and fails if it makes a single false automatic merge. `uv run metaspike
generate-catalogue --stage private_development` resolves recorded source records into a
LOCAL catalogue state file. It is not the production catalogue; every source of the
records must clear the stated stage, and a provenance file beside the state says so.

`uv run --env-file .env metaspike probe-sources` makes one small authenticated
call per catalogue source and reports the field names and vocabularies it sees. It
is never run by tests, and it never prints a credential.
`--source steam-players --app APPID` makes one concurrent-player call for one
eligible app instead; it is never part of `all`.

`uv run --env-file .env metaspike sample` samples concurrent players once, for the
current 15-minute slot, for every eligible app in the bootstrap scope, and appends
every outcome to `.metaspike/samples/`. It is safe to rerun: a slot that already has
its observation is not requested again. It prints statuses, not values.
`--replay tests/fixtures/steam_players` answers from the recorded response instead
of calling Steam. `uv run metaspike sample-status` reports coverage, failures,
quota and policy clearance, and never a value or a secret.

`run` and `collect` accept `--store-dir DIR` to also append what they collect to a
local point-in-time observation store.

Add `-v` before the command for progress logging, e.g. `uv run metaspike -v run`.

Quality checks:

```
uv run ruff format          # format   (add --check to verify only)
uv run ruff check           # lint
uv run mypy                 # strict type check
uv run pytest               # tests, offline
uv run pytest -m live       # one explicit live smoke test
```

Credentials are read from the environment only, typically from a git-ignored
`.env`: `METASPIKE_IGDB_CLIENT_ID`, `METASPIKE_IGDB_CLIENT_SECRET` and
`METASPIKE_STEAM_WEB_API_KEY`. The IGDB access token is generated from the first
two and cached in `.metaspike/tokens/` (git-ignored, owner-only); it is never
configured by hand.

Exit codes: `0` success (including partial source failures, which are recorded in
source health), `1` unexpected error, `2` invalid catalogue or usage, `3` no
source returned any data (nothing published), `4` invalid output such as a
contract or score-invariant violation (nothing published), `5` source-policy
violation, `6` a catalogue source was unavailable (for example missing credentials).

The Wikimedia User-Agent identifies MetaSpike with a contact URL. Override the
contact with the `METASPIKE_CONTACT` environment variable. No secret is needed.

## Private developer UI

`apps/dev-ui/` is a local, private interface over the published artifacts: the
leaderboard, a page per game with its history, source health and the methodology. It
reads the artifact bundle and computes nothing itself. It is internal development, it
is not deployed, and score v0 is not the final opportunity score.

```
cd apps/dev-ui
npm install
npm run data:replay     # offline: recorded responses only
npm run dev             # http://localhost:3000
```

See [apps/dev-ui/README.md](apps/dev-ui/README.md).

## Event intelligence (internal, uncalibrated)

`src/metaspike/events/` is a source-independent engine for dated events: append-only
observations, deterministic identity and revisions, a versioned Event Heat
(`event_heat_rules_v1`) and an upcoming radar. No event source is collected yet, so for
every game it publishes "unavailable", which is never the same as zero. It is not part
of `metaspike_score_v0`. See [docs/event-intelligence.md](docs/event-intelligence.md).

## Historical validation (internal)

`src/metaspike/validation/` checks past rankings against what followed, with no
look-ahead: `uv run metaspike backtest --replay tests/fixtures/wikimedia
--recorded-through 2026-10-03 --from 2026-07-07 --to 2026-10-05` (offline; `--from` and
`--to` are the days recommendations are simulated on, at 00:00 UTC, each seeing only
what its source had published by then). On the recorded fixture the evidence status is
`framework_only`: six games show that the method works, and nothing about whether the
score predicts demand. See
[docs/historical-validation.md](docs/historical-validation.md).

## Event sources (internal, private development only)

`src/metaspike/event_sources/` reads announcements from a source and decides which are
events. The one source is Steam News, through the documented keyless method
`ISteamNews/GetNewsForApp/v2`. A post is evidence, not an event: it becomes a
candidate, and a candidate is promoted to an event observation only when a
deterministic rule set (`event_classification_rules_v1`) establishes both its type and
its timing. Everything else stays a candidate and never reaches Event Heat.

`uv run metaspike ingest-events --replay tests/fixtures/steam_news --apps 730 570` runs
it offline on synthetic fixtures. Nothing Steam-derived is cleared for public or
commercial use. See [docs/event-sources.md](docs/event-sources.md).

## Catalogue expansion (internal, offline evaluation only)

Broad catalogue discovery is off in production. `uv run metaspike evaluate-catalogue
--stage private_development` answers, offline and in memory, what a broad catalogue
would hold from the recorded source records: what would be admitted, quarantined or
kept as a related product, and which monitoring sources could be asked about each
entity. It calls no source, stores nothing and requires every source's policy to clear
the stated stage. See [docs/catalogue-expansion.md](docs/catalogue-expansion.md).

`uv run metaspike evidence --as-of <ISO time>` reads, offline, what the given local
inputs held about each game at that moment: its identity, each source's coverage and
freshness, movements against each source's own history, known events, and what is
missing. It is a private reading, not an artifact, and is never published. See
[docs/multi-source-evidence.md](docs/multi-source-evidence.md).

With `--synthetic-scenario` or `--creator-supply-dir` the reading also carries a
creator-supply sample: YouTube-shaped responses parsed offline, each video associated
with a game or not, and the sample described without ever being called the market.
MetaSpike has never called YouTube; see
[docs/youtube-creator-supply.md](docs/youtube-creator-supply.md). With
`--synthetic-scenario` or `--community-dir` it also carries a community-interest sample
from Reddit-shaped posts, with themes and candidate angles; MetaSpike has never called
Reddit; see [docs/reddit-community-interest.md](docs/reddit-community-interest.md).
`--opportunities` reads the same evidence through `creator_opportunity_rules_v1` and
prints a private, explained shortlist; on today's real data it justifies nothing. See
[docs/creator-opportunity-intelligence.md](docs/creator-opportunity-intelligence.md).

## Architecture

```
catalogue ─► collect ─► normalise ─► features ─► score + confidence ─► explain ─► publish
```

```
src/metaspike/
  cli.py                 command-line entry point and exit codes
  domain/                typed models only, no I/O
    games.py             CanonicalGame, Catalogue, lifecycle, relationships
    identity.py          UUIDv4 ids, aliases, lineage, versioned source bindings
    source_records.py    source-native catalogue records and admissibility
    events.py            event vocabulary (detection not implemented)
    timeseries.py        Granularity and aligned TimeWindow
    metrics.py           metric registry: what is measured, its role and aggregation
    observations.py      Observation and gap-free TimeSeries at any granularity
    signals.py           SignalKey, Signal and EntitySignals (many signals, any missing)
    scores.py            score, confidence and explanation results
    source_policy.py     permissions, stage-aware gates, rate limits, quota, display rules
    families.py          source families and applicability
    samples.py           sample attempts, logical slots and slot resolution
    baseline_rules.py    baseline_rules_v1: constants and reason codes
  catalogue/
    seed.py              bootstrap entities and migration anchors
    bindings.py          audit of the bootstrap entities' proven source bindings
    eligibility.py       point-in-time Steam eligibility and sampling targets
    legacy_ids.py        frozen msg_NNNN -> UUIDv4 migration table
    overrides.py         versioned identity overrides (FiveM only)
    generate.py          generates the canonical catalogue from source records
  sources/
    base.py              source modes, SourceAdapter and SamplingAdapter protocols
    registry.py          refuses adapters whose policy does not clear the stage
    limiter.py           per-budget-group rate limiter and quota ledger
    http.py              the limited HTTP client every adapter must use; redaction
    credentials.py       secrets from the environment that cannot be printed
    wikimedia.py         Wikimedia Pageviews adapter and its policy
    igdb_auth.py         Twitch OAuth client-credentials with a cached token
    igdb.py              IGDB identity-metadata adapter and its policy
    steam_catalogue.py   Steam app-list adapter and its policy
    steam_players.py     Steam concurrent-player sampler and its policy
    catalogue_sources.py wiring for the catalogue sources
    sampling_sources.py  wiring and offline replay for sample-now sources
    replay.py            offline client that replays recorded responses
  features/
    attention.py         daily feature maths for score v0 (unchanged)
    config.py            per-signal feature configuration, by rule version
    robust.py            median/MAD, robust scale, mid-rank percentile, Theil-Sen
    aggregate.py         15-minute slots to hourly and daily aggregates
    engine.py            the one feature engine and the BaselineState contract
    normalise.py         normalised signals, signal quality and evidence breadth (semantics v1)
    semantics.py         signal_semantics_v2: raw vs aggregate freshness, availability
    access.py            rule status and the shadow-only feature guard
  scoring/
    v0.py                metaspike_score_v0, confidence v0, and the one signal v0 reads
    explanations.py      deterministic explanation templates
  pipeline/
    collection.py        per-signal failure isolation
    normalize.py         gap-free series, publication-lag awareness
    assess.py            per-signal features -> score -> confidence -> explanation
    run.py               orchestration
    sampling.py          one sampling run for one slot; health summary
    history.py           entity history resolved from bindings at observation time
    signal_features.py   reference path from stored history to normalised signals
  storage/
    base.py              ObservationStore contract (append-only, point-in-time)
    local.py             local JSON Lines implementation
    records.py           local store for source-native records
    samples.py           SampleStore contract and local JSON Lines implementation
  resolution/
    normalize.py         deterministic title normalisation
    source_urls.py       strict extraction of a source id from a store URL
    models.py            resolver input, evidence, decisions, decision log
    resolver.py          candidates, evidence, vetoes and gates
    quarantine.py        unresolved records and re-evaluation triggers
    evaluate.py          metrics against the frozen golden set
    golden.py            the golden evaluation set's model and integrity rules
    recorded.py          loads recorded IGDB/Steam rows through the real parsers
  lifecycle/
    rules.py             lifecycle rules, version 1
    history.py           append-only lifecycle history, hold period, overrides
  publishing/
    artifacts.py         the artifact contract: view models only, no domain imports
    mapping.py           the only place domain models become contract views
    build.py             assemble a bundle (pure)
    publisher.py         atomic local publishing
    validation.py        validate written artifacts
    schemas.py           JSON Schema export
schemas/                 generated JSON Schemas, checked in
tests/                   offline tests, recorded fixtures, v0 golden file,
                         recorded IGDB/Steam rows and the resolution golden set
docs/architecture.md     intended eventual flow, cloud parts labelled FUTURE
```

See [docs/architecture.md](docs/architecture.md) for the intended eventual flow.

## Domain model

- **Identity**: `canonical_game_id` is a lowercase UUIDv4, never derived from a
  title or a source id. The 15 original `msg_NNNN` ids map to fixed UUIDs in a
  frozen table and remain resolvable as aliases. Merges and splits are recorded
  as lineage; nothing is rewritten. Which source record belongs to which entity
  is a versioned, point-in-time binding.
- **Source records**: what a source says about one of its own products, keyed
  only by the source's id. DLC, demos, betas, playtests, bundles and soundtracks
  are never eligible to become entities.
- **CanonicalGame**: `canonical_game_id` (opaque, stable), `slug`, `name`,
  `aliases`, `source_identifiers` (source + namespace + value, so any platform
  can be added), `relationships` (`parent_game_id` + `relationship_type`), and
  `lifecycle_state`.
- GTA V, GTA Online and FiveM are three distinct entities. GTA Online is an
  `ONLINE_COMPONENT` of GTA V; FiveM is a `MOD_ECOSYSTEM` of GTA V.
- **Observation**: one value for one game, source, metric and period, with
  `observed_at`, `source_timestamp`, freshness and quality metadata. A period
  with no data is an explicit `missing` observation, never a zero.
- **TimeSeries**: one point per period at a `Granularity` (15 minutes, hourly or
  daily), aligned to UTC. It also records `latest_due`, the latest period the
  source was expected to have published when it was collected.
- **Metric registry**: a metric says what is measured, never how often. Each has
  a role (`demand` or `creator_supply`) and an aggregation rule. Only `demand`
  metrics may ever feed the base opportunity score.
- **Signals**: a signal is a (source, metric, granularity). `EntitySignals` holds
  every signal an entity could have, each either collected, failed or not mapped.
  Each score version names the signals it reads; `metaspike_score_v0` reads only
  daily Wikimedia pageviews, so other signals cannot change it.

## Source policy

Compliance is part of the type system. Every source has a `SourcePolicy` covering
raw storage, retention, historical storage, derived metrics, commercial use,
redistribution, attribution and approval, plus a `reviewed_at` date and the
official references it was read from. It also carries the operational limits a
scheduler must obey (rate limit, quota budget, cache duration, expected
publication lag, each tagged documented, self-imposed or observed), display
requirements, and per-field rules that can only narrow the source-level policy. A permission is one of `allowed`,
`restricted`, `prohibited` or `unknown`, and only `allowed` permits.

- Each policy states explicitly whether the source is cleared for four stages:
  private development, private collection, public launch and commercial
  production. A registry serves one stage and refuses any source not cleared for
  it. IGDB and Steam are cleared for private development only: IGDB needs a
  partnership for monetized use, and Steam's terms do not address commercial use.
- Limits are enforced, not just recorded: every request goes through the rate
  limiter for its budget group (requests per interval, concurrency, daily quota,
  Retry-After, bounded backoff). An exhausted quota refuses the request before it
  is sent.
- An adapter cannot be registered without a policy, with a policy for a different
  source, or with a policy that does not permit derived metrics and settled
  approval.
- Daily values appear in public artifacts only if the policy allows both
  redistribution and historical storage.
- The collection cache is refused if the policy does not allow raw storage.
- The observation store refuses a metric whose policy does not allow history.
- MetaSpike is engineered to be commercial-capable. A source is eligible for a
  monetized production configuration only if `commercial_use_allowed` is
  explicitly `allowed`; anything unclear stays `unknown` or `restricted`.

Wikimedia Pageviews: the Analytics API states its data is available under CC0
1.0, requires a descriptive User-Agent, and asks clients to make one request at
a time; the documented rate limit is 200 requests per minute for identified
unauthenticated clients. No publication schedule is documented for daily data,
so a 24-hour lag is allowed for (marked `observed`, to be measured).
Reviewed 2026-10-04. These are recorded assumptions from the official
documentation, not legal advice.

## Features

Daily granularity only. All windows are anchored on `t`, the latest day with an
observation. Missing days are excluded from every window.

| Window | Days | Purpose |
| --- | --- | --- |
| recent short | `[t-2, t]` | current level |
| recent long | `[t-6, t]` | smoothed current level |
| prior short | `[t-5, t-3]` | velocity comparison |
| baseline | `[t-34, t-7]` | the game's own normal; excludes the recent 7 days |

| Feature | Definition | Valid when |
| --- | --- | --- |
| `latest_views` | `x[t]` | any observation |
| `avg_3d`, `avg_7d` | mean of observed days in the window | ≥ 2 of 3, ≥ 5 of 7 |
| `baseline_mean_28d`, `baseline_std_28d` | rolling mean and sample std (n−1) | ≥ 14 of 28 |
| `baseline_median_28d`, `baseline_mad_28d` | median `m`; MAD = median of absolute deviations from `m` | ≥ 14 of 28 |
| `lift_ratio`, `lift_pct` | `avg_3d / m`; `(ratio − 1) × 100` | `m > 0` |
| `lift_7d_pct` | `(avg_7d / m − 1) × 100` | `m > 0` |
| `velocity_ratio`, `velocity_pct` | `avg_3d / prior_avg_3d` | prior average > 0 |
| `consecutive_rising_days` | day-over-day increases ending at `t` | always |
| `z_score` | `(x[t] − mean) / std` | `std > 0` |
| `robust_z_score` | `(x[t] − m) / (1.4826 × MAD)`; uses `1.2533 ×` mean absolute deviation when MAD is 0 | scale > 0 |
| `history_percentile` | `100 × (count below x[t] + 0.5 × count equal) / n` over prior days | `n ≥ 30` |

An invalid feature is `null`, with a flag saying why. NaN and Infinity are
rejected by the models and again by the JSON writer.

## Score v0

```
significance = clamp(robust_z_score / 7, 0, 1)
magnitude    = clamp(log2(lift_ratio) / 3, 0, 1)
anomaly      = sqrt(significance × magnitude)
momentum     = clamp(log2(velocity_ratio), 0, 1)
score        = 100 × (0.7 × anomaly + 0.3 × momentum)
```

- **significance**: 3.5 is the conventional outlier threshold for a median/MAD
  z-score and earns half credit; twice that saturates.
- **magnitude**: measured in doublings because attention is multiplicative; 8×
  normal saturates.
- **anomaly**: a geometric mean, so a move must be both statistically unusual
  and large. Significance alone over-rewards tiny moves in very stable series;
  magnitude alone over-rewards noise in small volatile ones.
- **momentum**: a doubling of the 3-day average in three days saturates.
- **0.7 / 0.3**: anomaly is primary, momentum secondary. These are stated
  priors, not fitted values; there is no outcome data yet to calibrate them.

A game without enough evidence is `unscored` with a reason. It is never given a
zero, and it is listed separately from the ranked leaderboard.

## Confidence v0

```
source_ceiling = 1 − 0.4 ^ n_sources            (1 source → 0.60)
freshness      = max(0, 1 − staleness_days / 4)
history        = min(1, history_span_days / 90)
completeness   = observed_days / history_span_days
samples        = min(baseline_samples / 28, recent_7d_samples / 7)
confidence     = 100 × source_ceiling × freshness × history × completeness × samples
```

With one source, confidence cannot exceed **60**. One uncorroborated source
cannot tell real interest in a game from an artefact of that source. Bands:
low `< 30`, medium `30–60`, high `> 60` (unreachable in this phase).

## Artifacts

**The `1.x` contract is INTERNAL and non-binding.** Every artifact carries
`contract_status: "internal"`. The public contract is defined once, for the
production release.

The contract is a set of view models that import nothing from the domain. Domain
models reach an artifact only through `publishing/mapping.py`, so a field added
internally is not published until a view opts in, and a field a view needs cannot
disappear silently.

Published to `artifacts/` (git-ignored, reproducible):

```
artifacts/
  manifest.json                    pointer to the selected version
  versions/{run_id}/               immutable, complete, validated run
    current.json  source-health.json  methodology.json  games/{slug}.json
  current.json  source-health.json  methodology.json  games/     copy of the selected version
```

- `run_id` is `{UTC timestamp}-{first 8 hex of content hash}`.
- `content_hash` covers everything except generation metadata, so identical
  input always gives an identical hash. Re-running changes only `generated_at`,
  `updated_at`, `run_id` and `last_success`.
- Every artifact carries `schema_version` (`1.0.0`). JSON Schemas are generated
  from the typed models into `schemas/`; a test fails if they drift.

**Atomic publishing.** A run is written to a staging directory, validated from
disk, renamed into `versions/`, and only then selected by atomically replacing
`manifest.json`. A failure before that point leaves the previous manifest and
everything it references untouched. The top-level copy is refreshed last and is
a convenience; `manifest.json` is the source of truth. The same sequence maps to
an object store: upload the version prefix, then overwrite the manifest object.
The newest five versions are kept.

## Tests

```
uv run pytest
```

Tests never touch the network. They cover catalogue validation, relationships,
the source-policy requirement, Wikimedia parsing and failure behaviour, missing
data, insufficient history, baselines, lift and velocity, robust outlier
behaviour, score and confidence bounds, the single-source ceiling, determinism,
explanations, JSON Schema validation, atomic publishing and idempotent rebuilds.
Fixtures under `tests/fixtures/wikimedia/` are real responses recorded for six
articles; the other games have no recording, which exercises partial failure.

## Known limitations

- One source. Nothing is corroborated.
- Wikipedia readership is a proxy for public attention, not player counts,
  viewership, search demand or creator supply.
- Daily, UTC. The newest point is the last complete day. Staleness is measured
  against the day the source was due to have published, using its expected
  publication lag, so a normal delay is not penalised.
- English Wikipedia only. Redirect titles are not counted. A game without its
  own article (FiveM) has no coverage.
- Weekly seasonality is not modelled.
- Momentum is additive, so a rebound from a dip back to normal can earn up to
  30 points without any anomaly.
- No event detection: the score says attention moved, not why.

## Next planned step

Phase 4 Part C2 (safe, low-cost, unattended collection), planned in
`docs/phase4-c2-plan.md` and not started, then Part D (baseline state, parity and soak).
Nothing is public before the single production release.
