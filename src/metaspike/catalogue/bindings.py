"""Source-binding audit for the bootstrap entities.

Before any telemetry is sampled for an entity, the id it is sampled under must be
trustworthy. This audit states, for each of the 15 bootstrap entities, exactly which
source records are bound to it, on what evidence, and whether a Steam app id is
proven well enough to sample.

    YES      a Steam app is bound to the entity AND the entity's IGDB record lists
             that app as a structured external id
    NO       an IGDB record is bound and asserts no Steam app
    UNKNOWN  an asserted app is not confirmed by Steam's own list, or nothing is
             bound to the entity at all

A Steam app id is never inferred from a title.
"""

from enum import StrEnum

from metaspike.catalogue.generate import BOOTSTRAP_SOURCE, CatalogueState
from metaspike.catalogue.legacy_ids import LEGACY_ID_TO_CANONICAL
from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.model import FrozenModel
from metaspike.domain.source_records import SourceRecord
from metaspike.sources.wikimedia import SOURCE_ID as WIKIMEDIA

_STEAM_RECORDS = "steam_catalogue"
_STEAM_EXTERNAL = "steam"
_IGDB = "igdb"


class TelemetryEligibility(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class BootstrapBinding(FrozenModel):
    name: str
    slug: str
    canonical_game_id: str
    legacy_id: str
    igdb_ids: tuple[str, ...]
    # Steam apps bound to the entity: present in Steam's list and resolved to it.
    steam_app_ids: tuple[str, ...]
    # Steam apps the entity's IGDB record asserts but Steam's recorded list lacks.
    steam_app_ids_asserted_unconfirmed: tuple[str, ...]
    # Steam apps that name this entity as a candidate but remain quarantined.
    steam_apps_quarantined: tuple[str, ...]
    wikimedia_title: str | None
    binding_evidence: tuple[str, ...]
    steam_telemetry: TelemetryEligibility
    reason: str


def audit_bootstrap_bindings(
    state: CatalogueState, records: list[SourceRecord]
) -> tuple[BootstrapBinding, ...]:
    by_key = {record.key: record for record in records}
    legacy_of = {canonical: legacy for legacy, canonical in LEGACY_ID_TO_CANONICAL.items()}
    audit = []
    for game in state.catalogue.games:
        if game.canonical_game_id not in legacy_of:
            continue
        bound = [
            key
            for key in state.mapping.records_for(game.canonical_game_id)
            if key.source != BOOTSTRAP_SOURCE
        ]
        igdb = sorted(key.source_id for key in bound if key.source == _IGDB)
        steam = sorted((k.source_id for k in bound if k.source == _STEAM_RECORDS), key=int)
        asserted = sorted(
            {
                link.value
                for key in bound
                if key.source == _IGDB and key in by_key
                for link in by_key[key].external_ids
                if link.source == _STEAM_EXTERNAL
            },
            key=int,
        )
        quarantined = sorted(
            entry.record.key.source_id
            for entry in state.quarantine.entries
            if entry.record.key.source == _STEAM_RECORDS
            and any(c.entity_ref == game.canonical_game_id for c in entry.candidates)
        )
        confirmed = [app for app in steam if app in asserted]
        unconfirmed = [app for app in asserted if app not in steam]
        eligibility, reason = _eligibility(igdb, steam, confirmed, unconfirmed, quarantined)
        wikimedia = game.identifier_for(WIKIMEDIA)
        audit.append(
            BootstrapBinding(
                name=game.name,
                slug=game.slug,
                canonical_game_id=game.canonical_game_id,
                legacy_id=legacy_of[game.canonical_game_id],
                igdb_ids=tuple(igdb),
                steam_app_ids=tuple(steam),
                steam_app_ids_asserted_unconfirmed=tuple(unconfirmed),
                steam_apps_quarantined=tuple(quarantined),
                wikimedia_title=None if wikimedia is None else wikimedia.value,
                binding_evidence=tuple(_evidence(state, key) for key in sorted(bound, key=_order)),
                steam_telemetry=eligibility,
                reason=reason,
            )
        )
    return tuple(audit)


def _order(key: SourceRecordKey) -> tuple[str, int]:
    return key.source, int(key.source_id) if key.source_id.isdigit() else 0


def _evidence(state: CatalogueState, key: SourceRecordKey) -> str:
    logged = state.decisions.latest(key)
    if logged is None:
        return f"{key.source}:{key.source_id} bound, decision not recorded"
    decision = logged.decision
    basis = decision.override_ref or decision.explanation
    return f"{key.source}:{key.source_id} {decision.kind.value} ({basis})"


def _eligibility(
    igdb: list[str],
    steam: list[str],
    confirmed: list[str],
    unconfirmed: list[str],
    quarantined: list[str],
) -> tuple[TelemetryEligibility, str]:
    waiting = f" Steam app(s) {', '.join(quarantined)} are quarantined." if quarantined else ""
    if confirmed and not unconfirmed:
        return (
            TelemetryEligibility.YES,
            f"Steam app {', '.join(confirmed)} is in Steam's list, bound to this entity, and "
            "listed as a structured external id by its IGDB record.",
        )
    if unconfirmed:
        return (
            TelemetryEligibility.UNKNOWN,
            f"IGDB asserts Steam app {', '.join(unconfirmed)}, but Steam's recorded app list "
            f"does not contain it, so the binding is not confirmed by Steam itself.{waiting}",
        )
    if steam:
        return (
            TelemetryEligibility.UNKNOWN,
            f"Steam app {', '.join(steam)} is bound, but no IGDB record lists it as a "
            "structured external id.",
        )
    if not igdb:
        return (
            TelemetryEligibility.UNKNOWN,
            f"No source record is bound to this entity, so nothing asserts a Steam app.{waiting}",
        )
    return (
        TelemetryEligibility.NO,
        f"The bound IGDB record asserts no Steam app.{waiting}",
    )
