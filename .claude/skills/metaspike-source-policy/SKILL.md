---
name: metaspike-source-policy
description: How MetaSpike sources and their SourcePolicy work, and the checklist for adding or changing a source (Wikimedia, Steam, IGDB, Twitch, YouTube, Reddit, Steam News). Use for any adapter, permission or clearance change.
---

# Sources and source policy

Every adapter is registered together with a `SourcePolicy`
(`src/metaspike/domain/source_policy.py`). The registry refuses an adapter without one.
Code that exists for a source is not permission to collect it.

## The model in one screen

- `Permission`: `allowed | restricted | prohibited | unknown`. Only `allowed` permits.
- `DeploymentStage`: `private_development` (local) → `private_collection` (unattended
  cloud) → `public_launch` (shown publicly) → `commercial_production`.
- `SourcePurpose`: `demand_signals`, `catalogue_metadata`, `event_intelligence`,
  `creator_supply`, `community_interest`.
- `stage_clearance`: one `StageClearance(stage, cleared, reason)` per stage.
  `policy.cleared_for(stage)` is the gate. The reason text is shown in readiness reports.
- `operations`: rate limit, quota, publication lag, cache. The limiter and the request
  budget (physical HTTP attempts, retries included) are driven from it.
- `display`, `field_rules`, `attribution_text`, `reviewed_at`, `references`.
- `uv run metaspike source-readiness` prints every source's state from the code.

## Current state (Oct 2026)

| Source | Module | Furthest cleared | Public? |
|---|---|---|---|
| Wikimedia pageviews | `sources/wikimedia.py` | commercial (CC0) | yes |
| IGDB | `sources/igdb.py` | private_collection | attribution + partnership answer |
| Steam app list | `sources/steam_catalogue.py` | private_development | no |
| Steam concurrent players | `sources/steam_players.py` | private_development | Valve answer on derived metrics |
| Steam News | `event_sources/steam_news.py` | private_development | Valve answer |
| YouTube | `creator_sources/youtube.py` | private_development (offline) | no |
| Reddit | `community_sources/reddit.py` | none (approval pending) | no |

Steam `private_collection` needs, per its policy: a public privacy notice with a Steam
Data section at krishnachittella.dev/privacy, US-only storage (us-central1), the API key
registered to the MetaSpike / krishnachittella.dev app, and far below 100,000 calls a day.
Changing a clearance is a reviewed commit to the policy object, with the owner's OK.

## Adding a source: checklist

1. **Read the terms** from the provider's own pages. Record `reviewed_at`, `references`.
   Anything not stated stays `unknown`.
2. **Policy object** next to the adapter: permissions, per-stage clearance with reasons,
   operations (documented rate limit or a self-imposed one, quota, publication lag).
3. **Adapter** behind the right protocol (`sources/base.py`): `FETCH_HISTORY` sources take
   a window; `SAMPLE_NOW` sources take one id and cannot backfill. All HTTP goes through
   `sources/http.py::LimitedHttpClient`. Credentials via `sources/credentials.py`
   (`secret_from_env`), never logged.
4. **Identity**: map the source's id to a canonical game through a binding, never a title
   (see `/metaspike-identity`). IGDB `external_games` already carries Twitch, Steam and
   other ids; IGDB `websites` carries Wikipedia URLs.
5. **Replay fixture** in `tests/fixtures/<source>/`, plus tests: parsing, malformed
   input, rate limit/429, budget exhaustion, policy refusal at uncleared stages.
6. **Wire it**: registry/wiring module, `source_readiness.py`, and the families model
   (`domain/families.py`) if it is a new evidence family.
7. **Production**: a secret container in `infra/prod/secrets.tf` if it needs a key, a
   job or job command, a schedule entry. Each is an owner-approved step.

Missing data from a source is a state, never zero. A source that cannot observe a game
is `inapplicable`, not `missing`.
