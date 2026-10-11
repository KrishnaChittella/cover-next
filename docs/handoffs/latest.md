# Handoff: latest checkpoint (product branch)

This file is the short-lived handoff from Claude Code to ChatGPT. **This copy lives only
on the branch `product/local-ui`.** The handoff on `main` is the C2-F soak's and was not
touched. It supports communication only and replaces no code, test or document.

Production baseline: **PHASE 4 PART C2-F: ARMED AND SCHEDULED — SEVEN-DAY SOAK PENDING**
(true at 21:51 UTC on 2026-10-11: the first tick is 2026-10-12T00:00Z and no scheduler
attempt has been made)

Parallel checkpoint: **PRODUCT BUILD U4.1: EVENT SOURCE POLICY CLOSEOUT — CHATGPT REVIEW REQUIRED**

Offline only. No source was called, no probe or backfill was run, nothing was deployed.

## The bypass that existed

`publishing/build.py::_restricted_event_sources` collected the event sources in use and
then did:

```python
if inputs.source_policies is None:
    return ()
```

So when `EventInputs` carried no policy mapping, an event record, a coverage check or a
collected source summary went into a bundle marked `internal`, with no policy consulted
and no private-only marking. U4's handoff said "a source with no policy is refused
outright"; that was true for an empty or incomplete mapping and false for a missing
one. The CLI always passed `EVENT_SOURCE_POLICIES`, so the U4 probe was not affected.

## The invariant now

An event source is **used** when it is behind an event record or a coverage check, or
when its summary shows it was ever asked (before or after the evaluation moment).

- No source used: `source_policies` may be `None`; the bundle is `internal`.
- Any source used: `source_policies` must be present and must hold a `SourcePolicy` for
  every used source, filed under that source's own name, clearing the bundle's stage for
  event intelligence. Otherwise `SourcePolicyError`, raised before event intelligence
  is computed.

A missing mapping, an empty one, and one lacking only that source are all refused. So
is a policy filed under the wrong name. There is no path by which a caller vouches for
event data, and no source name is exempt: the gate contains no name test at all.

## Synthetic test policy

`tests/synthetic_policy.py`, test-only:

- `synthetic_event_policy(source)`: refuses any name not starting `synthetic_`. Cleared
  for private development only; the other three stages are explicitly not cleared, with
  the reason "Synthetic test data. It is not real and must never leave a test."
  Commercial use and redistribution prohibited. No endpoint, no network.
- `synthetic_event_policies(*sources)`: the mapping tests pass as `source_policies`.
- `with_synthetic_identifiers(catalogue)`: the same games named only by a synthetic id.

Under `tests/`; imported by nothing in `src/`; in no runtime registry (a test asserts
both). The U2 artifact tests now supply it. A bundle built from it is marked
`private_development_only`, like any data not cleared for the public.

One thing this surfaced: the U2 artifact tests had been attributing their invented
events to the real Wikimedia identifier, so those records carried the source name
`wikimedia_pageviews`. They now use the synthetic identifier, and no invented event is
attributed to a real source.

## Regression tests

In `tests/test_event_sources.py`. The first and third were run against U4's gate and
fail there ("DID NOT RAISE SourcePolicyError").

| Case | Result |
|---|---|
| A. event record, `source_policies=None` | `SourcePolicyError` |
| B. coverage check, `None` | `SourcePolicyError` |
| C. collected summary, `None` | `SourcePolicyError` |
| D. used source, `{}` | `SourcePolicyError` |
| E. used source, mapping missing only it | `SourcePolicyError` |
| also: summary of a source asked only after the evaluation moment | `SourcePolicyError` |
| also: a policy filed under the wrong name | `SourcePolicyError` |
| also: a `synthetic_` source with no policy | `SourcePolicyError` |
| F. nothing used, `None` (and `{}`, and a never-asked summary) | builds; `internal` |
| G. synthetic source with the test policy | builds; `private_development_only`; U2 tests pass |
| H. Steam News, real policy, private development | builds; `private_development_only` |
| I. same at private collection / public launch / commercial | refused, each for its own reason |

## Validator

Kept: private exactly when a source is restricted; a collected source not cleared for
public launch must be named as restricted.

Added, with no change to the contract: every source that an event contribution, a
game's coverage or a radar item is attributed to must be either named in
`restricted_sources` or described on source-health as cleared for public launch.
Otherwise the bundle is refused as carrying an undeclared event source. Tested by
tampering with a published bundle.

The frontend validator was not changed: it reads one artifact at a time and cannot make
that cross-file check.

## Confirmations

| | |
|---|---|
| Private-development-only design | unchanged; cases G and H |
| Steam News policy | unchanged: private development cleared; the other three blocked; derived and commercial unknown |
| Internal schema | **1.4.0**, unchanged; generated schemas and types have no diff |
| U4 probe numbers | unchanged: 3 requests, 15 posts, 0 classified, 0 promoted, 3 coverage checks. Local quota ledger still 3 |
| `event_classification_rules_v1` | unchanged; fingerprint `20103a107c30…1772` test passes; `src/metaspike/event_sources` has no diff |
| `event_heat_rules_v1` / event engine | unchanged; `events/` `333373c61ae6c37f`, `rules.py` `7358ea6563ddcad9` |
| Score v0 | unchanged; `scoring/` `3b857c551d5ab5b0`, `features/` `24cf7d170f57d8ab`; golden replay bit-for-bit |
| U3.1 | unchanged; `validation/` has no diff; the publication-lag regression passes |
| UI | unchanged; `apps/` has no diff |

## Gates

Backend: `ruff format --check` clean; `ruff check` clean; `mypy` clean (201 files);
`pytest` **1,455 passed**, 5 deselected (slow marker).
Frontend: `contract:check`, `typecheck`, `lint`, `test` (175 passed), `build` all pass.

## A neighbouring gap, not changed

Found while checking where else event data reaches an artifact. `metaspike backtest`
reads the event store and puts event intelligence into its snapshots with no policy
check, and the published validation report then carries `real_event_history` and a
count of snapshots that had event intelligence. That report can appear in a bundle
marked `internal` even if the events behind it came from a restricted source, because
the report does not say which sources its events came from.

It has had no effect so far: the store holds no promoted Steam observation, and the
three coverage checks are dated after the backtest's last prediction day. I left it
alone because closing it means either changing U3.1's report or adding a gate to the
backtest command, and both are outside this closeout. It needs a ruling before real
events are backtested.

## C2-F status and production isolation (read-only, 21:51 UTC 2026-10-11)

C2-F has **not begun**; there is no autonomous activity to report. Scheduler ENABLED,
`0 * 12-18 10 *`, next run 2026-10-12T00:00:00Z, no attempt. Control generation
`1791684706340016`, unchanged. Terraform state unchanged (latest generation
`1791685217422653`, the launch apply). Executions 35, last `ms-warehouse-lxqp9`. Seven
jobs, generation 6, image `sha256:a8680b2be8b5…`. Ten alert policies; IAM, schemas,
secrets and alerts identical to the previous snapshot. 0 secret versions, 0 user keys.
Repository variables unchanged. Last workflow run still 38104941975. `main` =
`cb33198`, level with origin, clean. No pull request. No Wikimedia, Steam or IGDB call
was made in U4.1. The portfolio repository was not touched.

## Commits

The fix commit "fix: fail closed on unpolicied event artifacts", then this handoff.
Pushed to `origin/product/local-ui` only. No PR, no merge to `main`.

## Exact next approval required

ChatGPT review of U4.1, and a ruling on the backtest gap above.

STOP. U5 has not been started.
