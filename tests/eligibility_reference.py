"""REFERENCE ONLY: Steam eligibility exactly as it was before it was indexed.

A verbatim copy of `src/metaspike/catalogue/eligibility.py` at commit c89ea4b (U5.1.2),
kept under `tests/` so the indexed implementation can be compared with it output for
output. Nothing in `src/` imports it. It scans the whole mapping history once per
entity, which is the cost the index removes; its rule is the rule.

Do not edit the logic below. If the eligibility rule is ever changed on purpose, this
file is replaced along with the tests that use it.
"""

from datetime import datetime
from enum import StrEnum

from metaspike.catalogue.generate import BOOTSTRAP_SOURCE, CatalogueState
from metaspike.catalogue.legacy_ids import LEGACY_ID_TO_CANONICAL
from metaspike.domain.games import GameId
from metaspike.domain.identity import SourceBinding, SourceRecordKey
from metaspike.domain.model import FrozenModel
from metaspike.domain.source_records import SourceRecord

STEAM_RECORD_SOURCE = "steam_catalogue"
_STEAM_EXTERNAL = "steam"
_METADATA_SOURCE = "igdb"


class SourceEligibility(StrEnum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNKNOWN = "unknown"


class SteamEligibility(FrozenModel):
    canonical_game_id: GameId
    slug: str
    name: str
    state: SourceEligibility
    # Apps that may be sampled for this entity. Each is sampled and stored on its own.
    app_ids: tuple[str, ...] = ()
    # Apps a bound record asserts but Steam's recorded list does not contain.
    asserted_unconfirmed: tuple[str, ...] = ()
    # Apps that name this entity as a candidate but are still quarantined.
    quarantined: tuple[str, ...] = ()
    reason: str


class SamplingTarget(FrozenModel):
    """One source-native id to sample, with the binding that makes it trustworthy."""

    source_native_id: str
    # None when the id is sampled with no entity bound to it. Such samples are kept
    # under the source's id and belong to no entity until a binding says otherwise.
    canonical_game_id: GameId | None = None
    mapping_version: int | None = None
    binding_ref: str | None = None


def steam_eligibility(
    state: CatalogueState, records: list[SourceRecord], as_of: datetime | None = None
) -> tuple[SteamEligibility, ...]:
    """Eligibility of every entity, as the bindings stood at `as_of` (None means now).

    Quarantine is not point-in-time: `quarantined` always reflects the current state.
    """
    by_key = {record.key: record for record in records}
    return tuple(
        _entity(state, by_key, game.canonical_game_id, as_of) for game in state.catalogue.games
    )


def _entity(
    state: CatalogueState,
    by_key: dict[SourceRecordKey, SourceRecord],
    canonical_game_id: str,
    as_of: datetime | None,
) -> SteamEligibility:
    game = state.catalogue.get(canonical_game_id)
    bound = [
        key
        for key in state.mapping.records_for(canonical_game_id, as_of)
        if key.source != BOOTSTRAP_SOURCE
    ]
    metadata = [key for key in bound if key.source == _METADATA_SOURCE]
    steam = _numeric({key.source_id for key in bound if key.source == STEAM_RECORD_SOURCE})
    asserted = _numeric(
        {
            link.value
            for key in bound
            if key in by_key and key.source != STEAM_RECORD_SOURCE
            for link in by_key[key].external_ids
            if link.source == _STEAM_EXTERNAL
        }
    )
    quarantined = _numeric(
        {
            entry.record.key.source_id
            for entry in state.quarantine.entries
            if entry.record.key.source == STEAM_RECORD_SOURCE
            and any(c.entity_ref == canonical_game_id for c in entry.candidates)
        }
    )
    confirmed = [app for app in steam if app in asserted]
    unconfirmed = [app for app in asserted if app not in steam]
    waiting = f" Steam app(s) {', '.join(quarantined)} are quarantined." if quarantined else ""
    if confirmed and not unconfirmed:
        verdict = SourceEligibility.ELIGIBLE
        reason = (
            f"Steam app {', '.join(confirmed)} is in Steam's list, bound to this entity, "
            "and listed as a structured external id by a record bound to it."
        )
    elif unconfirmed:
        verdict = SourceEligibility.UNKNOWN
        reason = (
            f"A bound record asserts Steam app {', '.join(unconfirmed)}, but Steam's "
            f"recorded app list does not contain it.{waiting}"
        )
    elif steam:
        verdict = SourceEligibility.UNKNOWN
        reason = (
            f"Steam app {', '.join(steam)} is bound, but no bound record lists it as a "
            "structured external id."
        )
    elif not metadata:
        verdict = SourceEligibility.UNKNOWN
        reason = f"No source record is bound to this entity.{waiting}"
    else:
        verdict = SourceEligibility.INELIGIBLE
        reason = f"The bound catalogue metadata asserts no Steam app.{waiting}"
    return SteamEligibility(
        canonical_game_id=canonical_game_id,
        slug=game.slug,
        name=game.name,
        state=verdict,
        app_ids=tuple(confirmed) if verdict is SourceEligibility.ELIGIBLE else (),
        asserted_unconfirmed=tuple(unconfirmed),
        quarantined=tuple(quarantined),
        reason=reason,
    )


def bootstrap_scope() -> frozenset[str]:
    """The Phase 3 collection scope: the original entities. A scope, not an app list."""
    return frozenset(LEGACY_ID_TO_CANONICAL.values())


def sampling_targets(
    state: CatalogueState,
    eligibility: tuple[SteamEligibility, ...],
    scope: frozenset[str] | None = None,
    as_of: datetime | None = None,
) -> tuple[SamplingTarget, ...]:
    """Every app to sample: each eligible app of each in-scope entity, separately.

    An entity with several eligible apps yields several targets. They are never
    summed, averaged or reduced to a primary app here.
    """
    targets = []
    for entity in eligibility:
        if entity.state is not SourceEligibility.ELIGIBLE:
            continue
        if scope is not None and entity.canonical_game_id not in scope:
            continue
        for app_id in entity.app_ids:
            binding = active_binding(
                state, SourceRecordKey(source=STEAM_RECORD_SOURCE, source_id=app_id), as_of
            )
            if binding is None or binding.canonical_game_id != entity.canonical_game_id:
                continue  # eligibility and the mapping must agree before anything is sampled
            targets.append(
                SamplingTarget(
                    source_native_id=app_id,
                    canonical_game_id=binding.canonical_game_id,
                    mapping_version=binding.mapping_version,
                    binding_ref=binding.decision_ref,
                )
            )
    return tuple(sorted(targets, key=lambda target: int(target.source_native_id)))


def active_binding(
    state: CatalogueState, key: SourceRecordKey, as_of: datetime | None = None
) -> SourceBinding | None:
    return next(
        (b for b in state.mapping.history_of(key) if b.active_at(as_of)),
        None,
    )


def _numeric(values: set[str]) -> list[str]:
    return sorted(values, key=lambda value: (len(value), value))
