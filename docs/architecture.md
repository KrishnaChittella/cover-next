# MetaSpike architecture

## Status

MetaSpike has one public release. Everything in this repository today is an
internal engineering milestone, not a product version.

| Component | Status |
| --- | --- |
| Catalogue, Wikimedia collector, source policy, generic time series and signals, metric registry, features, score v0, confidence v0, explanations, local point-in-time store, local atomic artifact publishing | **Implemented**, runs locally |
| More sources, entity resolution, event detection, launch scoring model | **FUTURE** |
| Raw storage (GCS), BigQuery, dbt, scheduled collection (Cloud Run Jobs + Scheduler), R2 artifact publishing, web app | **FUTURE**, not built and not deployed |

Nothing in this repository talks to a cloud provider.

## Intended eventual flow

```
sources                      Wikimedia today; more sources FUTURE
   │
collectors                   one adapter per source, per-game failure isolation
   │
source policy                gate: may we collect, store, derive, redistribute?
   │
raw storage where permitted  FUTURE: GCS, retention enforced from SourcePolicy.retention_days
   │
BigQuery                     FUTURE: observations and history
   │
dbt                          FUTURE: normalised models and tests
   │
feature / scoring paths      implemented in Python today; SQL or Python FUTURE
   │
R2 artifacts                 FUTURE: versions/{run_id}/ prefix + manifest.json pointer
   │
MetaSpike web app            FUTURE: krishnachittella.dev/metaspike reads manifest.json
```

## What runs today

```
seed catalogue ─► Wikimedia adapter ─► normalise ─► features ─► score v0 ─► confidence v0
                  (registry + policy)                                           │
        artifacts/ ◄─ atomic publish ◄─ validate ◄─ build bundle ◄─ explanations ┘
```

## Decisions that carry forward

- **The artifact contract is the interface.** The web app depends only on
  `manifest.json` and the files it references, each carrying `schema_version`.
  Moving computation into BigQuery and dbt later must not change the contract.
- **Publish by pointer.** A complete version is written first; a single manifest
  write selects it. Object stores cannot rename directories, but they can
  overwrite one object atomically, so the local design transfers unchanged.
- **Policy before storage.** A raw layer may only hold what a `SourcePolicy`
  allows, for no longer than its `retention_days`. A source whose permissions are
  `unknown` is not collected.
- **Missing is not zero.** Absence of data is explicit at every layer, from the
  observation through to the artifact.
- **Scores are versioned.** `metaspike_score_v0` is frozen by its tests. A change
  in meaning becomes `v1`, published alongside its own methodology.
- **One scoring engine.** Features, score, confidence and explanations are
  computed in Python only. The warehouse cleans, aggregates and builds baselines;
  it never re-implements scoring.
- **Supply never enters the base score.** Metrics carry a role. Creator-supply
  metrics from permission-gated sources are reported separately so the base score
  means the same thing for every game.
- **Storage is point-in-time.** Observations are append-only with the time they
  were seen, so a past score can be replayed without look-ahead.
- **Contract views are separate from domain models.** The current `1.x` contract
  is internal and non-binding.
- **False merges are worse than missed merges.** The resolver merges only on a
  cross-source id link with a compatible title, or on title plus timing plus a
  shared company. Anything else plausible is quarantined, with no canonical id.
  A store URL is an assertion, not a link: it counts only when a structured id on
  an edition of the same game confirms it, and it never overrides a veto.
- **The catalogue is generated.** Bootstrap entities keep their identities; every
  other entity is admitted by resolution. Overrides are versioned exceptions.
- **Lifecycle is versioned and score-free.** Thresholds are initial rules under a
  rule version, not domain truth.
- **Canonical ids are opaque.** Slugs and names can change; `canonical_game_id`
  cannot. Related entities (GTA V, GTA Online, FiveM) stay separate and are
  linked by typed relationships.
- **A source either has history or is sampled.** A `SAMPLE_NOW` source answers
  only for the instant it is asked; a missed slot cannot be recovered. Collection
  writes to the sample store and assessment reads from it, point-in-time. Nothing
  downstream uses the return value of a network call as data.
- **Samples belong to the source's id, not to an entity.** The entity is resolved
  from the mapping history at each sample's observed time, so a rebinding changes
  who a later sample belongs to and never rewrites an earlier one. A binding later
  found to have been wrong for its whole history is not corrected retroactively
  yet; the samples and their audit stamps are kept intact for that later mechanism.
- **Permissions are checked against the stage.** An unanswered permission may be
  lived with privately, where nothing it governs happens. It blocks every public
  stage and every published artifact, and is never treated as granted.
- **Several apps are not one number.** An entity with several eligible apps has no
  combined series until an explicit rule exists (`multi_app_combination_unresolved`).
  That is unresolved semantics, not missing data.
- **Confidence has two parts, kept apart.** Signal-quality confidence (maturity,
  freshness, coverage, source quality) and evidence breadth (which independent,
  applicable source families were observed). The public Confidence formula is not
  defined yet, but it must account for breadth: one mature, fresh family is not
  the same evidence as several agreeing ones. A family that cannot observe an
  entity is inapplicable, never a missing or failing source; breadth is counted
  over applicable families only.
- **Sampled-signal constants are versioned.** `baseline_rules_v1` holds the starting
  values. The 50-player low-base floor only nulls a percentage lift (`low_base`);
  it never disables log lift, robust anomaly, absolute movement, change-point
  detection, eligibility or opportunity consideration.
