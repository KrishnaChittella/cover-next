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
- **Canonical ids are opaque.** Slugs and names can change; `canonical_game_id`
  cannot. Related entities (GTA V, GTA Online, FiveM) stay separate and are
  linked by typed relationships.
