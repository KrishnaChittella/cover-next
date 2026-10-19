---
name: metaspike-signals
description: MetaSpike's signal and intelligence layers - feature engine, baselines, score v0 and confidence, signal semantics, evidence reading, provenance standings, events and Event Heat, creator-opportunity rubric, historical validation. Use when changing how numbers are computed, read, ranked or explained.
---

# Signals and intelligence

## Layers, bottom to top

| Layer | Where | Version |
|---|---|---|
| Daily attention features (score v0 inputs) | `features/attention.py` | frozen with v0 |
| One feature engine for any bucketed signal; `BaselineState` | `features/engine.py`, `config.py`, `robust.py`, `aggregate.py` | `baseline_rules_v1` (pinned, never edited) |
| Normalised signals, availability, freshness | `features/normalise.py`, `semantics.py` | `signal_semantics_v2` |
| Shadow-only guard (change points may not drive anything) | `features/access.py` | |
| Score v0 + confidence (Wikimedia only, confidence ≤ 60) | `scoring/v0.py`, `explanations.py` | `metaspike_score_v0` (frozen) |
| Events, Event Heat, radar | `events/` | `event_heat_rules_v1` |
| Steam News classification | `event_sources/candidates.py` | `event_classification_rules_v1` |
| Multi-source evidence reading at a moment | `evidence/assemble.py`, `model.py` | |
| Provenance: `verified` / `unverified` / `synthetic` | `evidence/provenance.py` (`PINNED_RECORDINGS`) | |
| Creator-opportunity rubric (candidate / watchlist / insufficient) | `opportunity/rules.py` | `creator_opportunity_rules_v1` |
| Backtests and outcome labels | `validation/`, `opportunity/history.py` | `validation_protocol_v1`, `outcome_rules_v1` |

A change in meaning is a new version beside the old one. Never edit a frozen version to
make a number come out differently.

## Semantics to keep

- Each family is judged against its own history only. Raw units of different sources are
  never added, averaged or compared.
- A bucket is evaluated against earlier buckets, then folded in. No look-ahead.
- States: available, stale, missing, inapplicable, unknown, unresolved, not_collected,
  not_integrated, incomplete. None of them is zero.
- An entity with several Steam apps is `unresolved` (no combination rule yet).
- Pageview freshness uses the source's due period (publication lag), not the wall clock.

## Known gap to fix before launch

Only `verified` evidence counts in the rubric, and `verified` today means "bytes match a
digest pinned by hand in `provenance.py`". Continuous production collection can never be
pinned by hand, so live data would always read `insufficient_evidence`. Planned fix: a
`production_collected` standing stamped at collection time by MetaSpike's own jobs
(the acquisition-record contract in `evidence/acquisition.py` is the starting point).

## Commands

```
uv run metaspike run --replay tests/fixtures/wikimedia --as-of 2026-10-03   # v0 end to end, offline
uv run metaspike evidence --as-of 2026-10-05T12:00:00+00:00 --wikimedia-replay tests/fixtures/wikimedia --recorded-through 2026-10-03 --opportunities
uv run metaspike backtest --replay tests/fixtures/wikimedia --recorded-through 2026-10-03 --from 2026-07-07 --to 2026-10-05
```

Deep detail, only if needed: `docs/multi-source-evidence.md`,
`docs/creator-opportunity-intelligence.md`, `docs/event-intelligence.md`.
