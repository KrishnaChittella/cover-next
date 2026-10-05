# MetaSpike

MetaSpike ultimately answers one question:

> What game has the strongest content opportunity right now, and why?

This repository is the data-intelligence pipeline behind that question. It is a
separate project from the portfolio site that will eventually display its output
at `krishnachittella.dev/metaspike`.

## What this phase implements

The first end-to-end, locally runnable vertical slice. One command:

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

No cloud (GCP, BigQuery, GCS, R2, Cloud Run, Scheduler), no Terraform, no dbt, no
ML, no LLM, no public web UI, no event detection, no second source, and no
permanent raw-data store. Those come after this slice is reviewed.

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
| `uv run metaspike export-schemas` | Regenerate `schemas/` from the typed models (`--check` to verify) |

Add `-v` before the command for progress logging, e.g. `uv run metaspike -v run`.

Quality checks:

```
uv run ruff format          # format   (add --check to verify only)
uv run ruff check           # lint
uv run mypy                 # strict type check
uv run pytest               # tests, offline
uv run pytest -m live       # one explicit live smoke test
```

Exit codes: `0` success (including partial source failures, which are recorded in
source health), `1` unexpected error, `2` invalid catalogue or usage, `3` no
source returned any data (nothing published), `4` invalid output such as a
contract or score-invariant violation (nothing published), `5` source-policy
violation.

The Wikimedia User-Agent identifies MetaSpike with a contact URL. Override the
contact with the `METASPIKE_CONTACT` environment variable. No secret is needed.

## Architecture

```
catalogue ─► collect ─► normalise ─► features ─► score + confidence ─► explain ─► publish
```

```
src/metaspike/
  cli.py                 command-line entry point and exit codes
  domain/                typed models only, no I/O
    games.py             CanonicalGame, Catalogue, lifecycle, relationships
    events.py            event vocabulary (detection not implemented)
    observations.py      Observation, DailySeries, explicit missing data
    scores.py            score, confidence and explanation results
    source_policy.py     SourcePolicy and permissions
  catalogue/seed.py      bootstrap catalogue
  sources/
    base.py              SourceAdapter protocol, SourceError
    registry.py          registry that refuses adapters without a usable policy
    wikimedia.py         Wikimedia Pageviews adapter and its policy
    replay.py            offline client that replays recorded responses
  features/attention.py  feature maths and machine-readable definitions
  scoring/
    v0.py                metaspike_score_v0 and confidence v0
    explanations.py      deterministic explanation templates
  pipeline/
    collection.py        per-game failure isolation
    normalize.py         gap-free series, missing days made explicit
    assess.py            features -> score -> confidence -> explanation
    run.py               orchestration
  publishing/
    artifacts.py         the public artifact contract
    build.py             assemble a bundle (pure)
    publisher.py         atomic local publishing
    validation.py        validate written artifacts
    schemas.py           JSON Schema export
schemas/                 generated JSON Schemas, checked in
tests/                   offline tests and recorded Wikimedia fixtures
docs/architecture.md     intended eventual flow, cloud parts labelled FUTURE
```

See [docs/architecture.md](docs/architecture.md) for the intended eventual flow.

## Domain model

- **CanonicalGame**: `canonical_game_id` (opaque, stable), `slug`, `name`,
  `aliases`, `source_identifiers` (source + namespace + value, so any platform
  can be added), `relationships` (`parent_game_id` + `relationship_type`), and
  `lifecycle_state`.
- GTA V, GTA Online and FiveM are three distinct entities. GTA Online is an
  `ONLINE_COMPONENT` of GTA V; FiveM is a `MOD_ECOSYSTEM` of GTA V.
- **Observation**: one value for one game, source, metric and UTC day, with
  `observed_at`, `source_timestamp`, freshness and quality metadata. A day with
  no data is an explicit `missing` observation, never a zero.

## Source policy

Compliance is part of the type system. Every source has a `SourcePolicy` covering
raw storage, retention, historical storage, derived metrics, commercial use,
redistribution, attribution and approval, plus a `reviewed_at` date and the
official references it was read from. A permission is one of `allowed`,
`restricted`, `prohibited` or `unknown`, and only `allowed` permits.

- An adapter cannot be registered without a policy, with a policy for a different
  source, or with a policy that does not permit derived metrics and settled
  approval.
- Daily values appear in public artifacts only if the policy allows both
  redistribution and historical storage.
- The collection cache is refused if the policy does not allow raw storage.

Wikimedia Pageviews: the Analytics API states its data is available under CC0
1.0, requires a descriptive User-Agent, and asks clients to make one request at
a time. Reviewed 2026-10-04. These are recorded assumptions from the official
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
- Daily, UTC. The newest point is the last complete day. Wikimedia publishes a
  day some hours after it ends, so a run shortly after 00:00 UTC sees data one
  day stale and loses a quarter of its freshness factor.
- English Wikipedia only. Redirect titles are not counted. A game without its
  own article (FiveM) has no coverage.
- Weekly seasonality is not modelled.
- Momentum is additive, so a rebound from a dip back to normal can earn up to
  30 points without any anomaly.
- No event detection: the score says attention moved, not why.

## Next planned step

Review this slice and the artifact contract. Then: a second independent source
to lift the confidence ceiling, followed by the cloud foundation (scheduled
collection, policy-aware raw storage, warehouse, and artifact publishing to
object storage). Nothing cloud-related exists yet.
