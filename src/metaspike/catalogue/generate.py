"""Catalogue generation: the canonical catalogue is an OUTPUT, not a hand-kept list.

Inputs: the bootstrap entities (identities that pre-date resolution), the source
records collected so far, the override set, and optionally the previous state.

Every source record is resolved against the entities that exist at that moment:

    AUTO_MATCH       the record is bound to that entity
    NEW_ENTITY       a new canonical entity is created with a fresh UUID
    MANUAL_OVERRIDE  the override's instruction is applied
    QUARANTINE       the record waits, unresolved, with no canonical id
    NOT_AN_ENTITY    the record is kept as a related product

Generation is stable: a record that is already bound stays bound to the same entity,
so running it again over the same input creates no new ids and changes nothing.
"""

import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import AwareDatetime

from metaspike.catalogue.legacy_ids import MIGRATED_AT, legacy_identity_ledger
from metaspike.catalogue.overrides import PRODUCTION_OVERRIDES, OverrideSet
from metaspike.catalogue.seed import BOOTSTRAP_ANCHORS, bootstrap_catalogue
from metaspike.domain.games import (
    CanonicalGame,
    Catalogue,
    GameId,
    GameRelationship,
    LifecycleState,
    RelationshipType,
)
from metaspike.domain.identity import (
    IdentityLedger,
    MappingHistory,
    SourceRecordKey,
    new_canonical_id,
)
from metaspike.domain.model import FrozenModel
from metaspike.domain.source_records import Admissibility, ProductType, SourceRecord
from metaspike.lifecycle.history import OVERRIDABLE_STATES, LifecycleHistory, LifecycleOverride
from metaspike.lifecycle.rules import RULE_VERSION, ActivityEvidence, first_public_availability
from metaspike.resolution.models import (
    DecisionKind,
    DecisionLog,
    EntityView,
    ResolutionDecision,
    ResolverInput,
)
from metaspike.resolution.normalize import normalize_title
from metaspike.resolution.quarantine import Quarantine, evaluation_fingerprint
from metaspike.resolution.resolver import RESOLVER_VERSION, resolve

BOOTSTRAP_SOURCE = "metaspike_bootstrap"
MAX_SETTLING_PASSES = 6
# Relationship types that link two canonical entities (the others describe products).
_ENTITY_RELATIONSHIPS = frozenset(
    {
        RelationshipType.MOD_ECOSYSTEM,
        RelationshipType.ONLINE_COMPONENT,
        RelationshipType.SEQUEL,
        RelationshipType.REMAKE,
        RelationshipType.REMASTER,
        RelationshipType.STANDALONE_EXPANSION,
    }
)
_ADMISSIBILITY_ORDER = [
    Admissibility.ENTITY_ELIGIBLE,
    Admissibility.EXPLICIT_MODEL_ONLY,
    Admissibility.VARIANT_OF_PARENT,
    Admissibility.RELATED_PRODUCT,
    Admissibility.NOT_A_GAME,
    Admissibility.UNDETERMINED,
]


class RelatedProduct(FrozenModel):
    """A product that belongs with an entity but is never one itself."""

    record: SourceRecordKey
    title: str
    product_type: ProductType
    relationship_type: RelationshipType | None = None
    entity_ids: tuple[GameId, ...] = ()


class CatalogueState(FrozenModel):
    """Everything generation produced, and everything the next run needs to stay stable."""

    generated_at: AwareDatetime
    resolver_version: str
    lifecycle_rule_version: str
    overrides_version: int
    catalogue: Catalogue
    identity: IdentityLedger
    mapping: MappingHistory
    decisions: DecisionLog
    quarantine: Quarantine
    lifecycle: tuple[LifecycleHistory, ...]
    related_products: tuple[RelatedProduct, ...] = ()

    def lifecycle_of(self, canonical_game_id: str) -> LifecycleHistory:
        return next(h for h in self.lifecycle if h.canonical_game_id == canonical_game_id)


@dataclass
class _Entity:
    game: CanonicalGame
    bootstrap: bool
    records: list[SourceRecord] = field(default_factory=list)

    def view(self) -> EntityView:
        return EntityView(ref=self.game.canonical_game_id, records=tuple(self.records))


def _bootstrap_record(game: CanonicalGame) -> SourceRecord:
    """What we already knew about a bootstrap entity, in the shape of a source record.

    It lets an incoming record with the same title find the entity (and be
    quarantined for lack of corroboration) instead of being admitted as a duplicate.
    """
    return SourceRecord(
        key=SourceRecordKey(source=BOOTSTRAP_SOURCE, source_id=game.slug),
        title=game.name,
        alternative_titles=game.aliases,
        product_type=ProductType.UNKNOWN,
        fetched_at=MIGRATED_AT,
    )


def generate_catalogue(
    records: Iterable[SourceRecord] = (),
    *,
    as_of: datetime = MIGRATED_AT,
    overrides: OverrideSet = PRODUCTION_OVERRIDES,
    id_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    previous: CatalogueState | None = None,
) -> CatalogueState:
    available = {record.key: record for record in records}
    bootstrap = {game.canonical_game_id: game for game in bootstrap_catalogue().games}
    known = (
        bootstrap
        if previous is None
        else {game.canonical_game_id: game for game in previous.catalogue.games}
    )
    entities = {
        identifier: _Entity(game=game, bootstrap=identifier in bootstrap, records=[])
        for identifier, game in known.items()
    }
    for entity in entities.values():
        if entity.bootstrap:
            entity.records.append(_bootstrap_record(entity.game))

    mapping = MappingHistory() if previous is None else previous.mapping
    decisions = DecisionLog() if previous is None else previous.decisions
    quarantine = Quarantine() if previous is None else previous.quarantine
    related = {} if previous is None else {p.record: p for p in previous.related_products}
    by_slug = {entity.game.slug: entity for entity in entities.values()}

    # Bindings made at migration: each bootstrap entity's known source record.
    for slug, key in BOOTSTRAP_ANCHORS.items():
        if key in available and mapping.canonical_for(key) is None:
            target = by_slug[slug].game.canonical_game_id
            decision = ResolutionDecision(
                kind=DecisionKind.MANUAL_OVERRIDE,
                target_ref=target,
                override_ref=f"bootstrap:anchor:{slug}",
                explanation=f"bootstrap entity '{slug}' is this source record, bound at migration",
                resolver_version=RESOLVER_VERSION,
            )
            decisions, logged = decisions.append(available[key], decision, as_of, target)
            mapping = mapping.bind(key, target, as_of, logged.ref)

    for key, record in available.items():
        bound = mapping.canonical_for(key)
        if bound is not None and bound in entities:
            entities[bound].records.append(record)

    linked = _linked_records(available)
    ordered = sorted(available.values(), key=_processing_order)
    # A record resolved late in a pass can be exactly the evidence an earlier, quarantined
    # record was waiting for, so passes repeat until one changes nothing.
    for _ in range(MAX_SETTLING_PASSES):
        progressed = False
        for record in ordered:
            if mapping.canonical_for(record.key) is not None or record.key in related:
                continue
            view = ResolverInput(
                incoming=record,
                entities=tuple(entity.view() for entity in entities.values() if entity.records),
                context=tuple(
                    entry.record for entry in quarantine.entries if entry.record.key != record.key
                ),
            )
            # The fingerprint looks at every record that links to this one, whatever order
            # the records happen to be processed in, so it is the same on every run.
            fingerprint_view = view.model_copy(update={"context": linked.get(record.key, ())})
            if quarantine.get(record.key) is not None and not quarantine.is_due(
                record.key, evaluation_fingerprint(fingerprint_view, overrides)
            ):
                continue  # still quarantined, and nothing that bears on it has changed

            decision = resolve(view, overrides)
            resulting = _apply(decision, record, entities, overrides, id_factory)
            decisions, logged = decisions.append(record, decision, as_of, resulting)
            progressed = True
            if decision.kind is DecisionKind.QUARANTINE:
                fingerprint = evaluation_fingerprint(fingerprint_view, overrides)
                quarantine = quarantine.put(record, decision, as_of, fingerprint, logged.ref)
                continue
            quarantine = quarantine.release(record.key)
            if decision.kind is DecisionKind.NOT_AN_ENTITY:
                related[record.key] = _related_product(record, decision)
            elif resulting is not None:
                mapping = mapping.bind(record.key, resulting, as_of, logged.ref)
                entities[resulting].records.append(record)
        if not progressed:
            break

    lifecycle = _lifecycle(entities, bootstrap, previous, as_of)
    states = {history.canonical_game_id: history for history in lifecycle}
    games = tuple(
        entity.game.model_copy(update={"lifecycle_state": _state(states[identifier], entity.game)})
        for identifier, entity in entities.items()
    )
    return CatalogueState(
        generated_at=as_of,
        resolver_version=RESOLVER_VERSION,
        lifecycle_rule_version=RULE_VERSION,
        overrides_version=overrides.version,
        catalogue=Catalogue(games=games),
        identity=legacy_identity_ledger() if previous is None else previous.identity,
        mapping=mapping,
        decisions=decisions,
        quarantine=quarantine,
        lifecycle=tuple(lifecycle),
        related_products=tuple(related[key] for key in sorted(related, key=_key_order)),
    )


def _linked_records(
    available: dict[SourceRecordKey, SourceRecord],
) -> dict[SourceRecordKey, tuple[SourceRecord, ...]]:
    """For each record, the other records that name it or are named by it."""
    by_id: dict[str, list[SourceRecord]] = {}
    mentioned_by: dict[str, list[SourceRecord]] = {}
    mentions: dict[SourceRecordKey, set[str]] = {}
    for record in available.values():
        by_id.setdefault(record.key.source_id, []).append(record)
        named = {link.value for link in record.external_ids} | {
            reference.source_id for reference in (record.parent, record.version_parent) if reference
        }
        mentions[record.key] = named
        for value in named:
            mentioned_by.setdefault(value, []).append(record)
    linked: dict[SourceRecordKey, tuple[SourceRecord, ...]] = {}
    for key in available:
        others = {
            other.key: other
            for other in (
                *mentioned_by.get(key.source_id, ()),
                *(found for value in mentions[key] for found in by_id.get(value, ())),
            )
            if other.key != key
        }
        linked[key] = tuple(others[k] for k in sorted(others, key=_key_order))
    return linked


def _processing_order(record: SourceRecord) -> tuple[int, int, str, str]:
    """Typed, entity-eligible records first, so later records have entities to join."""
    return (
        _ADMISSIBILITY_ORDER.index(record.admissibility),
        0 if record.key.source == "igdb" else 1,
        record.key.source,
        record.key.source_id.zfill(12),
    )


def _key_order(key: SourceRecordKey) -> tuple[str, str]:
    return key.source, key.source_id.zfill(12)


def _apply(
    decision: ResolutionDecision,
    record: SourceRecord,
    entities: dict[str, _Entity],
    overrides: OverrideSet,
    id_factory: Callable[[], uuid.UUID],
) -> str | None:
    """Carry out a decision on the working set. Returns the canonical id it resulted in."""
    if decision.kind is DecisionKind.AUTO_MATCH:
        return decision.target_ref
    if decision.kind is DecisionKind.MANUAL_OVERRIDE:
        override = overrides.for_record(record.key)
        if override is None or override.canonical_game_id not in entities:
            raise ValueError(f"override for {record.key.source_id} names an unknown entity")
        entity = entities[override.canonical_game_id]
        if override.relationship_type is not None and override.related_game_id is not None:
            entity.game = _with_relationship(
                entity.game, override.related_game_id, override.relationship_type
            )
        return override.canonical_game_id
    if decision.kind is DecisionKind.NEW_ENTITY:
        identifier = new_canonical_id(id_factory)
        game = CanonicalGame(
            canonical_game_id=identifier,
            slug=_unique_slug(record.title, {e.game.slug for e in entities.values()}),
            name=record.title,
            aliases=tuple(dict.fromkeys(t for t in record.alternative_titles if t != record.title)),
            lifecycle_state=LifecycleState.RELEASED,  # replaced by the derived state below
        )
        for proposed in decision.relationships:
            if proposed.relationship_type in _ENTITY_RELATIONSHIPS:
                game = _with_relationship(game, proposed.entity_ref, proposed.relationship_type)
        entities[identifier] = _Entity(game=game, bootstrap=False)
        return identifier
    return None


def _with_relationship(
    game: CanonicalGame, parent: str, relationship_type: RelationshipType
) -> CanonicalGame:
    if any(r.parent_game_id == parent for r in game.relationships):
        return game
    relationship = GameRelationship(parent_game_id=parent, relationship_type=relationship_type)
    return game.model_copy(update={"relationships": (*game.relationships, relationship)})


def _related_product(record: SourceRecord, decision: ResolutionDecision) -> RelatedProduct:
    relationships = decision.relationships
    return RelatedProduct(
        record=record.key,
        title=record.title,
        product_type=record.product_type,
        relationship_type=relationships[0].relationship_type if relationships else None,
        entity_ids=tuple(proposed.entity_ref for proposed in relationships),
    )


def _unique_slug(title: str, taken: set[str]) -> str:
    words = [re.sub(r"[^a-z0-9]", "", token) for token in normalize_title(title).tokens]
    base = "-".join(word for word in words if word) or "game"
    slug, suffix = base, 2
    while slug in taken:
        slug, suffix = f"{base}-{suffix}", suffix + 1
    return slug


def _lifecycle(
    entities: dict[str, _Entity],
    bootstrap: dict[str, CanonicalGame],
    previous: CatalogueState | None,
    as_of: datetime,
) -> list[LifecycleHistory]:
    earlier = {} if previous is None else {h.canonical_game_id: h for h in previous.lifecycle}
    histories = []
    for identifier, entity in entities.items():
        history = earlier.get(identifier) or LifecycleHistory(canonical_game_id=identifier)
        seeded = bootstrap.get(identifier)
        if not history.records and seeded is not None:
            history = history.started(seeded.lifecycle_state, MIGRATED_AT.date(), "bootstrap")
        override = None
        if seeded is not None and seeded.lifecycle_state in OVERRIDABLE_STATES:
            override = LifecycleOverride(
                canonical_game_id=identifier,
                state=seeded.lifecycle_state,
                reason="Seed classification, kept until event and activity evidence exists.",
            )
        sourced = tuple(r for r in entity.records if r.key.source != BOOTSTRAP_SOURCE)
        histories.append(
            history.advance(
                first_public_availability(sourced), ActivityEvidence(), as_of.date(), override
            )
        )
    return histories


def _state(history: LifecycleHistory, game: CanonicalGame) -> LifecycleState:
    return history.current.state if history.current is not None else game.lifecycle_state
