---
name: metaspike-checks
description: Run MetaSpike's lint, type and test checks with the least output and time. Use before committing, when a test fails, or when deciding which tests cover a change.
---

# Checks

Goal: prove a change works with the smallest run that covers it. The full suite
(9–13 min, ~1,700 tests) belongs in CI on the PR, not in the edit loop.

## Order

1. `uv run ruff format` then `uv run ruff check --fix` (fast; fix before anything else).
2. `uv run mypy` (strict; whole project, about a minute).
3. Targeted tests for what changed, quietly:
   `uv run pytest -q -x --tb=short -p no:cacheprovider <files>`
4. Only before opening a PR that touches shared code: the full suite,
   `uv run pytest -q --tb=line 2>&1 | tail -15`.

Always pipe long output through `tail`. Never paste a full log into the conversation.

## Which tests cover what

| Changed | Run |
|---|---|
| `sources/wikimedia.py`, `pageview_recorder.py` | `test_wikimedia.py test_pageview_recorder.py` |
| `sources/steam_*`, `pipeline/sampling.py` | `test_steam_players.py test_steam_catalogue.py test_sampling.py` |
| `sources/igdb*` | `test_igdb.py` |
| `domain/source_policy.py`, any policy object | `test_source_policy.py test_policy_stages_and_limiter.py test_policy_operations_and_storage.py test_source_readiness.py` |
| `jobs/collect.py`, `jobs/*` | `test_jobs.py test_c2_request_budget.py` |
| `scheduling/*` | `test_c2_scheduling.py test_c2f_finite_soak.py test_c2_request_budget.py` |
| `resolution/*`, `catalogue/*` | `test_resolver.py test_resolver_evaluation_and_quarantine.py test_catalogue*.py test_generated_catalogue.py test_steam_eligibility_index.py` |
| `features/*` | `test_feature_engine.py test_features.py test_normalised_signals.py test_signal_semantics_v2.py test_v1_reproduction.py` |
| `scoring/*` | `test_scoring.py test_v0_parity.py test_explanations.py` |
| `evidence/*`, `opportunity/*` | `test_evidence.py test_opportunity.py test_opportunity_history.py test_integrated_recorded.py` |
| `events/*`, `event_sources/*` | `test_event_intelligence.py test_event_sources.py test_event_artifacts.py` |
| `validation/*` | `test_historical_validation.py` |
| `warehouse/*`, `warehouse/` dbt | `test_warehouse_*.py` (dbt tests run inside `test_warehouse_dbt.py`) |
| `publishing/*`, `schemas/` | `test_pipeline.py test_contract_decoupling.py` then `uv run metaspike export-schemas --check` |
| `infra/` | `test_cloud_foundation.py test_deploy_image_reuse.py`, and `terraform fmt -check -recursive infra` |
| `apps/dev-ui/` | `cd apps/dev-ui && npm run check` |

Markers: `slow` and `live` are excluded by default. Never run `-m live` without the
owner's approval: it calls real sources.

## When a test fails

- Read the assertion and the test's docstring first; most tests name the invariant.
- A pinned hash or fingerprint test failing (`v1_reproduction`, golden sets,
  `RECORDED_FINGERPRINT`) means behaviour changed. Do not update the pin to make it pass
  unless the change is intended, and say so in the PR.
- Known: `test_opportunity_history.py::test_the_recorded_command_result_is_unchanged`
  gives a different fingerprint on Linux x86 than on the owner's Mac. Fix the cause
  (platform-dependent output), not the pin.
