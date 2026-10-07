"""Catalogue generation with blocking: the same catalogue, without the all-pairs work.

`generate_catalogue` offers every entity to the resolver for every unresolved record,
and rebuilds its append-only histories by copying them, which is right for a few
hundred records and quadratic beyond that. This module produces THE SAME
`CatalogueState` from the same inputs, in the same order, with:

    blocking       each record is resolved against its candidate entities only
                   (see `resolution.blocking`)
    incrementality resolver work happens only for records that are new, and for
                   quarantined records whose evidence fingerprint has changed. A
                   bound record stays bound, exactly as before, and is not re-examined
    plain builders the binding history, decision log and quarantine are accumulated
                   in place and frozen once at the end

It is not a second resolver and holds no identity rule. `resolve`,
`evaluation_fingerprint` and every helper of `generate_catalogue` are called as they
are; only which entities are offered, and how the bookkeeping is stored, differ.
Tests require the result to equal `generate_catalogue`'s exactly, and `cross_check`
re-asks every question the slow way and fails on the first difference.

All known records are still passed in: they are the evidence entities are made of.
What is incremental is the resolving, not the reading.
"""

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from metaspike.catalogue.generate import (
    _ENTITY_RELATIONSHIPS,
    MAX_SETTLING_PASSES,
    CatalogueState,
    RelatedProduct,
    _bootstrap_record,
    _Entity,
    _key_order,
    _lifecycle,
    _processing_order,
    _related_product,
    _state,
    _unique_slug,
    _with_relationship,
)
from metaspike.catalogue.legacy_ids import MIGRATED_AT, legacy_identity_ledger
from metaspike.catalogue.overrides import PRODUCTION_OVERRIDES, OverrideSet
from metaspike.catalogue.seed import BOOTSTRAP_ANCHORS, bootstrap_catalogue
from metaspike.domain.games import CanonicalGame, Catalogue, LifecycleState
from metaspike.domain.identity import (
    MappingHistory,
    SourceBinding,
    SourceRecordKey,
    new_canonical_id,
)
from metaspike.domain.source_records import SourceRecord
from metaspike.lifecycle.rules import RULE_VERSION
from metaspike.resolution.blocking import CandidateIndex, ContextIndex, wanted_refs
from metaspike.resolution.models import (
    DecisionKind,
    DecisionLog,
    LoggedDecision,
    ResolutionDecision,
    ResolverInput,
)
from metaspike.resolution.quarantine import Quarantine, QuarantineEntry, evaluation_fingerprint
from metaspike.resolution.resolver import RESOLVER_VERSION, resolve


class BlockingMismatch(AssertionError):
    """Blocking changed an answer. This must never happen; it is a defect."""


@dataclass
class GenerationStats:
    """How much work a run did. Counts only; nothing here feeds a decision."""

    records: int = 0
    entities_at_end: int = 0
    passes: int = 0
    # Records that needed any attention: not already bound and not a related product.
    examined: int = 0
    resolver_calls: int = 0
    fingerprint_checks: int = 0
    skipped_unchanged_quarantine: int = 0
    # Entities offered to the resolver across all calls, against what all-pairs offers.
    entities_offered: int = 0
    entities_all_pairs: int = 0
    most_entities_offered: int = 0
    full_scans: int = 0
    cross_checked: int = 0


def generate_catalogue_blocked(
    records: Iterable[SourceRecord] = (),
    *,
    as_of: datetime = MIGRATED_AT,
    overrides: OverrideSet = PRODUCTION_OVERRIDES,
    id_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    previous: CatalogueState | None = None,
    stats: GenerationStats | None = None,
    cross_check: bool = False,
) -> CatalogueState:
    """`generate_catalogue`, with blocking. Same arguments, same result."""
    tally = GenerationStats() if stats is None else stats
    available = {record.key: record for record in records}
    tally.records = len(available)
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
    index = CandidateIndex()
    order = {identifier: position for position, identifier in enumerate(entities)}
    slugs = {entity.game.slug for entity in entities.values()}

    populated = 0  # entities that have at least one record, and so can be offered

    def attach(identifier: str, record: SourceRecord) -> None:
        nonlocal populated
        if not entities[identifier].records:
            populated += 1
        entities[identifier].records.append(record)
        index.add(identifier, record)

    for identifier, entity in entities.items():
        if entity.bootstrap:
            attach(identifier, _bootstrap_record(entity.game))

    bindings: list[SourceBinding] = [] if previous is None else list(previous.mapping.bindings)
    active: dict[SourceRecordKey, str] = {}
    for binding in bindings:
        if binding.valid_to is None:
            active.setdefault(binding.key, binding.canonical_game_id)
    mapping_version = max((binding.mapping_version for binding in bindings), default=0)
    logged: list[LoggedDecision] = [] if previous is None else list(previous.decisions.decisions)
    quarantined: dict[SourceRecordKey, QuarantineEntry] = (
        {} if previous is None else {e.record.key: e for e in previous.quarantine.entries}
    )
    context = ContextIndex()
    for entry in quarantined.values():
        context.add(entry.record)
    related: dict[SourceRecordKey, RelatedProduct] = (
        {} if previous is None else {p.record: p for p in previous.related_products}
    )
    by_slug = {entity.game.slug: entity for entity in entities.values()}

    def log(
        record: SourceRecord, decision: ResolutionDecision, resulting: str | None
    ) -> LoggedDecision:
        entry = LoggedDecision(
            sequence=len(logged) + 1,
            record=record.key,
            record_content_hash=record.content_hash,
            decided_at=as_of,
            decision=decision,
            canonical_game_id=resulting,
        )
        logged.append(entry)
        return entry

    def bind(key: SourceRecordKey, canonical: str, decision_ref: str) -> None:
        nonlocal mapping_version
        mapping_version += 1
        bindings.append(
            SourceBinding(
                key=key,
                canonical_game_id=canonical,
                mapping_version=mapping_version,
                valid_from=as_of,
                decision_ref=decision_ref,
            )
        )
        active[key] = canonical

    # Bindings made at migration: each bootstrap entity's known source record.
    for slug, key in BOOTSTRAP_ANCHORS.items():
        if key in available and active.get(key) is None:
            target = by_slug[slug].game.canonical_game_id
            decision = ResolutionDecision(
                kind=DecisionKind.MANUAL_OVERRIDE,
                target_ref=target,
                override_ref=f"bootstrap:anchor:{slug}",
                explanation=f"bootstrap entity '{slug}' is this source record, bound at migration",
                resolver_version=RESOLVER_VERSION,
            )
            bind(key, target, log(available[key], decision, target).ref)

    for key, record in available.items():
        bound = active.get(key)
        if bound is not None and bound in entities:
            attach(bound, record)

    links = _Links(available)
    pending = [
        record
        for record in sorted(available.values(), key=_processing_order)
        if active.get(record.key) is None and record.key not in related
    ]
    tally.examined = len(pending)

    def full_view(record: SourceRecord) -> ResolverInput:
        return ResolverInput(
            incoming=record,
            entities=tuple(entity.view() for entity in entities.values() if entity.records),
            context=tuple(
                entry.record for entry in quarantined.values() if entry.record.key != record.key
            ),
        )

    def view_for(record: SourceRecord) -> ResolverInput:
        wanted = wanted_refs(record, index, overrides)
        tally.entities_all_pairs += populated
        if wanted is None:
            tally.full_scans += 1
            tally.entities_offered += populated
            return full_view(record)
        chosen = sorted(
            (ref for ref in wanted if ref in entities and entities[ref].records),
            key=order.__getitem__,
        )
        tally.entities_offered += len(chosen)
        tally.most_entities_offered = max(tally.most_entities_offered, len(chosen))
        return ResolverInput(
            incoming=record,
            entities=tuple(entities[ref].view() for ref in chosen),
            context=context.claimants(record),
        )

    for _ in range(MAX_SETTLING_PASSES):
        tally.passes += 1
        progressed = False
        still_pending: list[SourceRecord] = []
        for record in pending:
            view = view_for(record)
            # The fingerprint looks at every record that links to this one, whatever
            # order the records happen to be processed in, so it is the same on every run.
            fingerprint_view = view.model_copy(update={"context": links.of(record.key)})
            fingerprint: str | None = None
            held = quarantined.get(record.key)
            if held is not None:
                tally.fingerprint_checks += 1
                fingerprint = evaluation_fingerprint(fingerprint_view, overrides)
                if cross_check:
                    _same_fingerprint(record, fingerprint, full_view(record), links, overrides)
                if held.fingerprint == fingerprint:
                    tally.skipped_unchanged_quarantine += 1
                    still_pending.append(record)
                    continue  # still quarantined, and nothing that bears on it has changed

            decision = resolve(view, overrides)
            tally.resolver_calls += 1
            if cross_check:
                tally.cross_checked += 1
                slow = resolve(full_view(record), overrides)
                if slow != decision:
                    raise BlockingMismatch(
                        f"{record.key.source}:{record.key.source_id}: blocked resolution gave "
                        f"{decision.kind.value}, all-pairs gave {slow.kind.value}"
                    )
            resulting = _apply(decision, record, entities, order, slugs, overrides, id_factory)
            decided = log(record, decision, resulting)
            progressed = True
            if decision.kind is DecisionKind.QUARANTINE:
                if fingerprint is None:
                    fingerprint = evaluation_fingerprint(fingerprint_view, overrides)
                    if cross_check:
                        _same_fingerprint(record, fingerprint, full_view(record), links, overrides)
                quarantined.pop(record.key, None)
                kept = Quarantine(entries=() if held is None else (held,))
                quarantined[record.key] = kept.put(
                    record, decision, as_of, fingerprint, decided.ref
                ).entries[-1]
                context.add(record)
                still_pending.append(record)
                continue
            quarantined.pop(record.key, None)
            context.remove(record.key)
            if decision.kind is DecisionKind.NOT_AN_ENTITY:
                related[record.key] = _related_product(record, decision)
            elif resulting is not None:
                bind(record.key, resulting, decided.ref)
                attach(resulting, record)
            else:
                still_pending.append(record)
        pending = still_pending
        if not progressed:
            break

    lifecycle = _lifecycle(entities, bootstrap, previous, as_of)
    states = {history.canonical_game_id: history for history in lifecycle}
    games = tuple(
        entity.game.model_copy(update={"lifecycle_state": _state(states[identifier], entity.game)})
        for identifier, entity in entities.items()
    )
    tally.entities_at_end = len(entities)
    return CatalogueState(
        generated_at=as_of,
        resolver_version=RESOLVER_VERSION,
        lifecycle_rule_version=RULE_VERSION,
        overrides_version=overrides.version,
        catalogue=Catalogue(games=games),
        identity=legacy_identity_ledger() if previous is None else previous.identity,
        mapping=MappingHistory(bindings=tuple(bindings)),
        decisions=DecisionLog(decisions=tuple(logged)),
        quarantine=Quarantine(entries=tuple(quarantined.values())),
        lifecycle=tuple(lifecycle),
        related_products=tuple(related[key] for key in sorted(related, key=_key_order)),
    )


def _apply(
    decision: ResolutionDecision,
    record: SourceRecord,
    entities: dict[str, _Entity],
    order: dict[str, int],
    slugs: set[str],
    overrides: OverrideSet,
    id_factory: Callable[[], uuid.UUID],
) -> str | None:
    """Carry out a decision on the working set, exactly as `generate._apply` does."""
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
            slug=_unique_slug(record.title, slugs),
            name=record.title,
            aliases=tuple(dict.fromkeys(t for t in record.alternative_titles if t != record.title)),
            lifecycle_state=LifecycleState.RELEASED,  # replaced by the derived state
        )
        for proposed in decision.relationships:
            if proposed.relationship_type in _ENTITY_RELATIONSHIPS:
                game = _with_relationship(game, proposed.entity_ref, proposed.relationship_type)
        entities[identifier] = _Entity(game=game, bootstrap=False)
        order[identifier] = len(order)
        slugs.add(game.slug)
        return identifier
    return None


class _Links:
    """For a record, the other records that name it or are named by it.

    The same relation `generate._linked_records` builds for every record at once,
    answered one record at a time, because only quarantined records ever need it.
    """

    def __init__(self, available: dict[SourceRecordKey, SourceRecord]) -> None:
        self._by_id: dict[str, list[SourceRecord]] = {}
        self._mentioned_by: dict[str, list[SourceRecord]] = {}
        self._mentions: dict[SourceRecordKey, set[str]] = {}
        self._cache: dict[SourceRecordKey, tuple[SourceRecord, ...]] = {}
        for record in available.values():
            self._by_id.setdefault(record.key.source_id, []).append(record)
            named = {link.value for link in record.external_ids} | {
                reference.source_id
                for reference in (record.parent, record.version_parent)
                if reference
            }
            self._mentions[record.key] = named
            for value in named:
                self._mentioned_by.setdefault(value, []).append(record)

    def of(self, key: SourceRecordKey) -> tuple[SourceRecord, ...]:
        if key not in self._cache:
            others = {
                other.key: other
                for other in (
                    *self._mentioned_by.get(key.source_id, ()),
                    *(
                        found
                        for value in self._mentions.get(key, ())
                        for found in self._by_id.get(value, ())
                    ),
                )
                if other.key != key
            }
            self._cache[key] = tuple(others[k] for k in sorted(others, key=_key_order))
        return self._cache[key]


def _same_fingerprint(
    record: SourceRecord,
    blocked: str,
    full: ResolverInput,
    links: _Links,
    overrides: OverrideSet,
) -> None:
    slow = evaluation_fingerprint(
        full.model_copy(update={"context": links.of(record.key)}), overrides
    )
    if slow != blocked:
        raise BlockingMismatch(
            f"{record.key.source}:{record.key.source_id}: the quarantine fingerprint differs "
            "between blocked and all-pairs evaluation"
        )
