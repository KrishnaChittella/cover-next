# MetaSpike

Answers one question for gaming creators: **which game has the strongest content
opportunity right now, and why?** A Python data pipeline on GCP that will publish static
JSON for a website at `krishnachittella.dev/metaspike`. Owner: Krishna Chittella.

## Product contract (for every coding agent)

Read [the Product charter in docs/engineering.md](docs/engineering.md#product-charter--why-metaspike-exists) before designing scores, source integrations, roadmaps or UI. **MetaSpike helps gaming creators choose a specific game/event/video angle, decide why to cover it now and how it fits their audience, and see the supporting evidence and uncertainty.** It is not an API collection project or a generic popularity leaderboard. Cross-source data is complementary and optional: official events ground the reason, Steam/Twitch/Wikimedia may indicate different kinds of interest, YouTube may describe creator coverage **only where permissions allow**, and game catalogues establish identity. Missing inputs are unknown, not zero. Never claim a video opportunity is underserved or guaranteed to grow without defensible evidence. Preserve current-source clearance and low-cost production boundaries. Treat personalization and broad discovery as product objectives, not features already proven in production. This charter is the shared contract for Claude Code, Grok, ChatGPT and other agents.

## Working mode (current)

- **Ship small and often:** develop, test, deploy, repeat. No soaks. Automatic checks
  (CI, dbt data tests, artifact validation) replace supervised observation windows.
- **Cost target:** $5–10 a month, hard review above $20. Prefer jobs and static files
  over anything always on.
- **Handoffs are short** (see `/metaspike-handoff`): the PR description is the record.
- **Ask the owner before:** IAM or WIF changes, adding a secret version, enabling a new
  source in production, any destroy in a Terraform plan, anything made public. Everything
  else proceeds without asking.

## Read only what the task needs

`docs/` is about 400 KB. Do NOT read it by default. Load the matching skill instead:

| Task | Skill |
|---|---|
| Running tests, lint, types, choosing what to run | `/metaspike-checks` |
| Adding or changing a source, its policy or permissions | `/metaspike-source-policy` |
| Catalogue, resolver, bindings, quarantine, golden set | `/metaspike-identity` |
| Features, baselines, score v0, evidence, opportunity rubric | `/metaspike-signals` |
| Landing, loader, BigQuery tables, dbt models and tests | `/metaspike-warehouse` |
| GCP, Terraform, deploy workflow, scheduler, control record, secrets | `/metaspike-cloud-ops` |
| Ending a task | `/metaspike-handoff` |

Open a specific doc only when a skill points to it.

## Layout

```
src/metaspike/
  domain/            typed models, no I/O (source_policy.py, identity.py, games.py ...)
  sources/           adapters + their SourcePolicy (wikimedia, igdb, steam_*)
  event_sources/     Steam News; events/ is the event engine and radar
  creator_sources/   YouTube (offline only); community_sources/ Reddit (offline only)
  catalogue/ resolution/  canonical identity and the resolver
  features/ scoring/ evidence/ opportunity/ validation/   the intelligence layers
  scheduling/ jobs/  dispatcher, collectors, warehouse job (Cloud Run commands)
  warehouse/         landing, loader, object and table ports
  publishing/        artifact contract (1.5.0), build, validate, atomic publish
  cli.py             every command
warehouse/           dbt project (own locked env)
infra/bootstrap      owner-applied: APIs, WIF, identities, budget
infra/prod           CI-applied: buckets, datasets, jobs, scheduler, alerts, secrets
apps/dev-ui/         Next.js developer UI over the artifacts
tests/               offline; fixtures are recorded or synthetic
```

## Commands

```
uv sync                                   # Python 3.12 via uv
uv run ruff format && uv run ruff check   # format, lint
uv run mypy                               # strict
uv run pytest -q -x tests/test_<area>.py  # targeted; see /metaspike-checks
uv run metaspike --help                   # every CLI command
cd apps/dev-ui && npm run check           # UI: contract, types, lint, tests, build
```

The full pytest suite takes 9–13 minutes. Run it in CI, not after every edit.

## Invariants that must not break

- **Missing is never zero.** Absent data is a state (missing, stale, unknown, ...), never 0.
- **Unknown permission is not permission.** Only `Permission.ALLOWED` permits.
- **Point in time.** Nothing reads data that was not known at the evaluated moment.
- **No title is identity.** The resolver never merges on title alone; 0 false merges on
  the golden set is a hard test.
- **Frozen rule versions** (`baseline_rules_v1`, `metaspike_score_v0`, ...) are never
  edited; a change is a new version.
- **Secrets** come from the environment or Secret Manager, never from files in Git,
  never printed, never passed through a chat.

## Production facts

Project `metaspike-prod-kc`, region `us-central1`. Buckets `metaspike-prod-kc-{landing,
raw,state,ops,tokens}`. Scheduler job `ms-dispatch-15m` (hourly). Control record
`gs://metaspike-prod-kc-ops/dispatch/control.json`. Seven Cloud Run jobs `ms-*`.
Wikimedia is the only source collected in production today.

## Branches

`main` is what production runs. `product/local-ui` and `product/multisource` hold the
product layers and the collector fix, not yet merged. New work: a short-lived branch and
a PR.
