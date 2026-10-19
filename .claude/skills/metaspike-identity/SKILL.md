---
name: metaspike-identity
description: MetaSpike's canonical game identity - catalogue, resolver_v2, bindings, quarantine, related products, golden set, catalogue expansion and source-id mapping (Steam, Twitch, Wikipedia). Use when touching catalogue/ or resolution/ or mapping a new source id to games.
---

# Identity

A false merge corrupts every downstream number, so the resolver prefers a safe
quarantine to a guess.

## Pieces

| Concept | Where |
|---|---|
| `CanonicalGame`, `Catalogue`, relationships (`ONLINE_COMPONENT`, `MOD_ECOSYSTEM`, `REMASTER`) | `domain/games.py` |
| UUIDv4 ids, aliases, lineage, versioned point-in-time bindings (`MappingHistory.active_at`) | `domain/identity.py` |
| Source records (what a source says about its own product) | `domain/source_records.py` |
| Resolver: decisions `auto_match`, `new_entity`, `quarantine`, `manual_override`, `not_an_entity` | `resolution/resolver.py`, `resolution/models.py` |
| Blocking (no all-pairs) and incremental generation | `resolution/blocking.py`, `catalogue/incremental.py` |
| Persistent catalogue: events, snapshots, `catalogue/CURRENT` pointer | `catalogue/persistence.py` |
| `BROAD_DISCOVERY_ENABLED = False` (production scope gate) | `catalogue/persistence.py` |
| Offline broad evaluation, coverage per source | `catalogue/evaluation.py`, `catalogue/coverage.py` |
| Steam eligibility (bound app + structured id on a bound record) | `catalogue/eligibility.py` |
| 15 bootstrap entities, frozen legacy ids, FiveM override | `catalogue/seed.py`, `legacy_ids.py`, `overrides.py` |

## Rules that tests enforce

- A merge needs a structured cross-source id link with a compatible title, or title +
  timing + shared company. A title alone is never identity (`title_only` quarantine).
- A Steam store URL is an assertion, not a link; it never overrides a veto.
- DLC, bundles, packs, soundtracks, demos are related products, never entities.
- GTA V, GTA Online and FiveM are three entities with typed relationships.
- Bootstrap ids never change. Repeating inputs changes nothing.
- `uv run metaspike evaluate-resolver` must report **0 false automatic merges** on the
  frozen golden set (`tests/fixtures/resolution_golden.json`, version 3, hash pinned).
  Never relabel a golden case because the resolver fails it.

## Mapping a source id to games

Bindings come from records, never titles. What the recorded IGDB data already holds
(`tests/fixtures/catalogue/igdb_games.json`, 266 games):

- `external_games` with source `Twitch`: 227 games; `Steam`: 172 games.
- `websites` with an `en.wikipedia.org/wiki/...` URL: 233 games.

These are structured links. Using one for a new source means: extract it strictly (like
`resolution/source_urls.py` does for Steam), bind it to the entity the IGDB record is
bound to, and add tests that a title is never turned into an id.

## Useful commands

```
uv run metaspike evaluate-resolver
uv run metaspike evaluate-catalogue --stage private_development --recorded tests/fixtures/catalogue --as-of 2026-10-06
uv run metaspike audit-bootstrap-bindings
```

Deep detail, only if needed: `docs/catalogue-expansion.md`.
