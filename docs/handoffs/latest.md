# Handoff: latest checkpoint (product branch)

This file is the short-lived handoff from Claude Code to ChatGPT. **This copy lives only
on the branch `product/local-ui`.** The handoff on `main` is the C2-F soak's and was not
touched. It supports communication only and replaces no code, test or document.

Production baseline: **PHASE 4 PART C2-F: ARMED AND SCHEDULED — SEVEN-DAY SOAK PENDING**
(true at 22:07 UTC on 2026-10-11: the first tick is 2026-10-12T00:00Z and no scheduler
attempt has been made)

Parallel checkpoint: **PRODUCT BUILD U4.2: EVENT VALIDATION PROVENANCE CLOSED — CHATGPT REVIEW REQUIRED**

Offline only. No source was called. Provenance and policy plumbing; no intelligence
changed.

## The gap

`run_backtest` could be given an event store and would put event intelligence from
whatever sources it held into its snapshots, with no policy consulted. The resulting
`ValidationReport` did not record which sources those were. Handed to `build_bundle`
later, with no current event inputs, the report went into a bundle marked `internal`.

No existing result was affected: no Steam observation was promoted, and the three
coverage checks postdate the backtest's range.

## Backtest: fail closed

`run_backtest(..., event_source_policies=None, stage=None)`. Both are explicit and
neither has a default value that grants anything.

For each prediction day, before that day is predicted, the sources of every event
record and coverage check visible at that moment are collected. For each such source:

- a stage must have been stated;
- a `SourcePolicy` must be present for it;
- `policy.source` must equal the source's name;
- the policy must clear that stage for `EVENT_INTELLIGENCE`.

Otherwise `SourcePolicyError`, raised before `predict` is called for that day, so the
event intelligence never enters a snapshot (a test counts the calls). If nothing is
ever visible to any prediction, neither argument is needed.

No caller-vouches path, no source-name test, no default stage. The `backtest` command
passes `EVENT_SOURCE_POLICIES` and `DeploymentStage.PRIVATE_DEVELOPMENT` explicitly.

## Provenance on the report

`ValidationReport.events` (`EventBacktest`), format now `validation_report_v3`:

| Field | Meaning |
|---|---|
| `event_sources_used: tuple[str, ...]` | **New.** Sorted set. Every source whose record or coverage check was visible to at least one prediction. Not every registered source; not every source in the store |
| `snapshots_with_positive_event_heat: int` | **New.** Snapshots where an event actually counted |
| `snapshots_with_event_intelligence: int` | Unchanged: snapshots with event coverage, heat 0 included |
| `real_event_history: bool` | Meaning made explicit: at least one event *observation* was visible to at least one prediction |

Two things about `real_event_history`. It used to be "the store holds any observation",
which was true even when every observation postdated the range; it now requires
visibility. And coverage alone leaves it false while still putting the source in
`event_sources_used`.

The model refuses a report whose snapshots carried event intelligence and that names no
source, and one whose list is not a sorted set. The sources are part of the
`report_id`. The report's note and its event limitation line now state the sources when
any were used; with none, both read exactly as before.

## Publishing

`_restricted_event_sources(inputs, stage, validation)` adds the report's
`event_sources_used` to the sources the bundle uses, and every one goes through the
same gate with the same mapping, `EventInputs.source_policies`. There is one mapping
for current event intelligence and historical provenance, handed in by the caller; no
global lookup.

One implementation: `event_sources/gate.py::require_event_source`, called by the
backtest and by the build.

| Stage | Report used a source not cleared for public launch |
|---|---|
| private development, cleared | builds; `private_development_only`; source in `restricted_sources` |
| private collection | refused unless that stage is cleared |
| public launch | refused unless that stage and its permissions are cleared |
| commercial production | refused unless that stage and its permissions are cleared |
| any, with no policy for the source | refused |

A report derived from `steam_news` cannot enter an `internal` bundle, with current
event inputs empty or absent.

The artifact validator also counts the report's sources among those that must be
declared, and refuses a published report that carried event intelligence but names no
source.

Stored reports in an earlier format stay on disk and are never read as current: a v2
report cannot say which event sources it used.

## Schema

Internal contract **1.4.0 → 1.5.0**, Python models → JSON Schema → generated
TypeScript. `/validation` gained one line: "Event sources used: …", or that no event
source took part, and the count of snapshots with a heat above 0. No redesign; no title
or candidate text.

## Tests added

Backend (8 new, plus existing ones moved onto the policy path):

| Case | Where |
|---|---|
| A. visible record, no policy → fail | `test_a_backtest_refuses_visible_event_data_without_a_policy` (nine refusal variants: nothing stated; stage only; `{}`; policy only; other sources' policies; wrong-name policy; three stages not cleared) |
| B. visible coverage check, no policy → fail | same test; also proves refusal happens before `predict` |
| C. data never visible is not provenance | `test_event_data_never_visible_to_a_prediction_is_not_provenance`: report **equal** to the no-event report |
| D, E, F, K | `test_a_report_records_exactly_the_event_sources_its_predictions_saw` |
| J. coverage alone is provenance | `test_coverage_alone_is_provenance` |
| G, I | `test_a_report_that_used_an_event_source_carries_its_policy_into_the_bundle` |
| H. Steam News policy at public launch → refused | `test_a_backtest_report_that_used_steam_news_cannot_enter_an_internal_bundle` (synthetic posts, real policy) |
| no-event result unchanged | `test_the_no_event_fixture_backtest_is_exactly_what_it_was` |
| earlier-format report not read | `test_a_report_from_before_provenance_is_kept_and_never_read_as_current` |
| L | U3.1's `test_a_period_that_had_ended_but_was_not_yet_published_is_withheld`, untouched and passing |

Frontend: 4 new (the line in both states; three contradictions refused).

Synthetic policy: every backtest test that feeds invented events passes
`synthetic_event_policies()` at `PRIVATE_DEVELOPMENT`. `tests/synthetic_policy.py` is
imported by nothing in `src/`; the backtest and the gate contain no source name and no
name test (asserted).

## Proofs of no change

| | |
|---|---|
| No-event fixture backtest | `snapshot_set_hash` `8a0f192d…e748` and `label_set_hash` `6b8aa3ce…abdf`, identical to U3.1; pinned in a test. Every metric as before (v0 top-1 +114.4% / 82%, association +0.62; lift +0.71). `report_id` changed with the format: `report_9e3a97a9725a587c5570ba2e42e7cd1a` |
| Score v0 | `scoring/` `3b857c551d5ab5b0`, `features/` `24cf7d170f57d8ab`; golden replay bit-for-bit; identical with and without event intelligence in three tests |
| U3.1 leakage behaviour | `validation/history.py`, `snapshot.py`, `outcomes.py`, `metrics.py`, `rules.py`: no diff. Only `backtest.py`, `report.py`, `store.py` changed. Five leakage checks unchanged |
| `outcome_rules_v1`, `validation_protocol_v1` | fingerprint tests pass |
| `event_classification_rules_v1` | `event_sources/` unchanged apart from the new `gate.py`; fingerprint `20103a107c30…1772` test passes |
| `event_heat_rules_v1` / event engine | `events/` `333373c61ae6c37f`, `rules.py` `7358ea6563ddcad9` |
| U4 probe | unchanged: 3 requests, 15 posts, 0 classified, 0 promoted, 3 coverage checks. Local Steam quota ledger still 3 |

## Gates

Backend: `ruff format --check` clean; `ruff check` clean; `mypy` clean (202 files);
`pytest` **1,463 passed**, 5 deselected (slow marker).
Frontend: `contract:check`, `typecheck`, `lint`, `test` (**179 passed**), `build` all
pass.

## C2-F status and production isolation (read-only, 22:07 UTC 2026-10-11)

C2-F has **not begun**; there is no autonomous activity to report. Scheduler ENABLED,
`0 * 12-18 10 *`, next run 2026-10-12T00:00:00Z, no attempt. Control generation
`1791684706340016`, unchanged. Terraform state unchanged (latest generation
`1791685217422653`). Executions 35, last `ms-warehouse-lxqp9`. Seven jobs, generation
6, image `sha256:a8680b2be8b5…`. Ten alert policies; IAM, schemas, secrets and alerts
identical to the previous snapshot. 0 secret versions, 0 user keys. Repository
variables unchanged. Last workflow run still 38104941975. `main` = `cb33198`, level
with origin, clean. No pull request. No Wikimedia, Steam or IGDB call was made in U4.2.
The portfolio repository was not touched.

## Commits

"fix: preserve event-source provenance in validation", then this handoff. Pushed to
`origin/product/local-ui` only. No PR, no merge to `main`.

## Exact next approval required

ChatGPT review of U4.2.

STOP. U5 has not been started.
