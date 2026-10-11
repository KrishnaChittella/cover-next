# Handoff: latest checkpoint (product branch)

This file is the short-lived handoff from Claude Code to ChatGPT. **This copy lives only
on the branch `product/local-ui`.** The handoff on `main` is the C2-F soak's and was not
touched. It supports communication only and replaces no code, test or document.

Production baseline: **PHASE 4 PART C2-F: ARMED AND SCHEDULED — SEVEN-DAY SOAK PENDING**
(true at 04:28 UTC on 2026-10-11: the first tick is 2026-10-12T00:00Z and no scheduler
attempt has been made)

Parallel checkpoint: **PRODUCT BUILD U2.1: EVENT INTELLIGENCE CORRECTNESS CLOSED — CHATGPT REVIEW REQUIRED**

Both issues found in review are fixed, each with regression tests. Nothing else in the
event model changed: `event_heat_rules_v1` is byte-identical, the heat arithmetic is
untouched, the schema is still 1.1.0, and score v0 is unchanged.

| | |
|---|---|
| Branch | `product/local-ui` |
| U2 | `9cf5564`, `030240a` |
| U2.1 commit | `ea3a85768219e95268c64a97f2ac944449ff2c15`, "fix: harden event identity and heat precision" |

## Issue one: a positive Event Heat could publish as zero

**The bug.** `publishing/event_views.py` rounded `event_heat` to one decimal, and
strength and contribution to four. One second inside a major patch's five-day ramp the
engine gives a strength of about 0.0000009 and a heat of about 0.00006. Both published
as `0.0`, beside a counted event. That said "a source looked and nothing contributes"
when something did, and it contradicted the validator's own rule.

**The fix, at the contract boundary.** Nothing there rounds any more. `event_heat`,
`strength`, `contribution`, and the radar's `hours_until_start`, `strength`,
`contribution` and `game_event_heat` are published exactly as computed. The three
rounding constants are gone. The validator gained one check (a counted event must
contribute more than 0) beside the existing one (heat is 0 exactly when nothing is
counted). The engine was not touched and remains deterministic.

**Published states, now distinct in every case:**

| State | `event_heat` |
|---|---|
| Available, nothing contributes | exactly `0` |
| Available, any contribution, however small | above `0`, unrounded |
| Unavailable | `null` |

**In the interface.** Formatting moved to where it belongs. A positive heat below the
displayed precision reads `<0.1`, and a positive strength or contribution reads `<0.01`.
A positive value is never shown as zero, and the "a source looked; nothing is relevant
now" line appears only for an exact 0. The contract stays numeric.

## Issue two: the same-source fallback merged on too little

**The bug.** `same_source_type_and_day` joined two reports from one source with the same
type and UTC start day whenever they did not carry different native ids, even with
entirely different titles. "Winter Event" and "Holiday Event" on one day became one
event. That is a wrong merge where a safe non-merge was intended.

**The fix.** The fallback now also requires the normalised title to be equal. It is
renamed to say what it does.

**The dedupe rules, exactly, after the fix.** An observation continues a known event of
the same game by the first rule that applies:

1. `same_source_native_id`: the same source reports the same native event id. This is
   the only rule that carries a substantive revision: date, title (however different),
   end and status may all change.
2. `same_source_type_day_and_title`: the fallback when there is no shared native id.
   Same source, same type, same UTC start day, **equal normalised title** (case,
   accents and punctuation folded; equality, not similarity), and not two different
   native ids.
3. `cross_source_same_reference`: another source, same type, exactly the same
   reference. Unchanged.
4. `cross_source_same_type_day_and_title`: another source, same type, same UTC start
   day, equal normalised title. Unchanged.

Otherwise it is a new event. Consequences, all tested:

- "Free Weekend" then "free  weekend!" from one source: one event, title revision
  recorded.
- "Winter Event" and "Holiday Event", one source, same type and day, no native id:
  **two events**.
- The same native id with a materially changed title: one event, a revision.
- A source that starts supplying a native id joins its earlier report only if type,
  day and normalised title agree; afterwards the id carries revisions. A native id
  arriving with a different title does not join.
- Two different native ids never join, even under one title.
- A substantive title change with no native id is, safely, a separate event. This is
  documented as a limitation.

No fuzzy matching was introduced. The `MatchRule` comments and the module's
documentation now match the code.

## Tests added

Backend, 12 new (94 event tests in all):

- `test_the_same_source_fallback_merges_only_an_equal_normalised_title`
- `test_a_native_id_carries_a_material_title_change_as_a_revision`
- `test_a_native_id_added_later_joins_only_where_the_fallback_evidence_agrees`
- two new cases in the "never merged" list (same source with different titles; a
  native id arriving with a different title)
- `test_at_or_before_the_ramp_boundary_the_published_heat_is_exactly_zero` (boundary,
  one second outside, three hours outside)
- `test_one_second_inside_the_ramp_publishes_a_heat_above_zero`: the file holds a heat
  above 0, equal to the engine's value, which would round to 0.0; the event is counted
  at rank 1; `validate_published` succeeds; the radar carries the same unrounded
  figures; score v0 beside it equals the golden file
- `test_the_three_published_states_stay_distinct`: positive, exactly 0 and null in one
  published run
- `test_validation_refuses_a_zero_heat_beside_a_counted_event`
- `test_nothing_at_the_contract_boundary_rounds_an_event_value`

The existing engine tests for one second before, exactly at, and one second after the
ramp boundary are unchanged and still pass. One existing test was renamed with the
rule it names; its assertions are the same.

Frontend, 2 new (135 in all): a tiny positive heat renders `<0.1` and never `0.0`, with
its event shown as counted and its strength and contribution as `<0.01`; and the
formatting boundaries for heat and shares.

## Unchanged, with proof

- **`event_heat_rules_v1`**: `src/metaspike/events/rules.py` has the same SHA-256 as at
  the U2 commit (`7358ea65…`), and `heat.py` has no diff. No importance, ramp, peak,
  duration, half-life, floor, decay window, contributor limit, coverage age or radar
  window changed.
- **Schema**: still 1.1.0. `export-schemas --check` reports the schemas up to date and
  no schema or generated type file changed; only values are no longer rounded.
- **Score v0**: the pre-U2 golden comparison and the with-and-without-events comparison
  both pass: score, rank, confidence, features, explanation, history, coverage and the
  leaderboard are identical. Nothing under `scoring/` or `features/` changed.
- No event source was added. Event Heat is still not in score v0. The UI was not
  redesigned.

## Results

Backend: `ruff format --check` clean (188 files); `ruff check` clean; `mypy` strict
clean (179 files); `pytest` **1,293 passed**, 5 deselected (was 1,281).

Frontend: `contract:check` current (6 schemas); type check clean; lint clean;
**135 tests** in 9 files; production build succeeds.

No test contacts a network. No source was called.

## Documentation

`docs/event-intelligence.md` now states that artifacts preserve positive Event Heat
unrounded, that the interface may show `<0.1`, that the same-source fallback needs an
equal normalised title, and that a substantive title change without a native id is
safely a separate event. `apps/dev-ui/README.md` describes the display rule. The U2
handoff is in Git history at `030240a` and was not rewritten.

## Production isolation

Read at 04:28 UTC on 2026-10-11:

- `main` and `origin/main` at `cb33198e2510965e41dff0f5fe7e75cacb7ee2ca`. No product
  work merged. Original worktree clean and on `main`.
- Prod Terraform state serial 14, 94 resources.
- Scheduler ENABLED, `0 * 12-18 10 *`, `Etc/UTC`, next run 2026-10-12T00:00:00Z, no
  attempt yet. C2-F has not started; executions are still 35 and the newest is still
  the catch-up of 01:06 UTC.
- Control record byte-identical to the launch record, generation `1791684706340016`.
- All seven jobs on `sha256:a8680b2b…c754`. Registry 11 images.
- No IAM, schema, secret, alert or scheduler difference. 0 secret versions, 0 keys.
  Repository variables unchanged. No workflow run since the launch commit's push.
- No Wikimedia, Steam or IGDB call; the only pipeline runs were offline replays inside
  tests. Nothing under `.github/`, `infra/`, `warehouse/` or the lockfiles changed.
  krishnachittella.dev was not modified.

## Branch push

Workflow files are unchanged on this branch, so a push of `product/local-ui` starts no
workflow. **The branch was pushed**; no run followed. No pull request, no merge.

## Exact next approval required

Review of U2.1. U3 has not been started.

STOP.
