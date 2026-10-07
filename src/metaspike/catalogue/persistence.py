"""The persistent production catalogue: append-only events, immutable snapshots, one pointer.

Everything lives under `catalogue/` in the private state bucket and is read by exact
name, never by listing, because the identities that use it hold grants conditional
on that prefix.

    catalogue/snapshots/{generation}.json     the whole CatalogueState. Immutable.
    catalogue/events/{generation}.ndjson      what changed in that generation. Immutable.
    catalogue/generations/{generation}.json   the generation's record: where its
                                              snapshot and events are, their hashes,
                                              and the generation before it. Immutable.
    catalogue/CURRENT                         the head generation's record. The only
                                              object that is ever replaced.

A generation is written snapshot first, then events, then its record, and only then
is CURRENT moved, with a precondition on the generation of CURRENT that was read.
If another writer got there first the move fails, nothing that existed is changed,
and the caller must reload and start again: two catalogue runs can never interleave.

The events are the history; a snapshot is a cache of them. Folding every generation's
events from the first reproduces the head snapshot exactly, and a test requires it.
Events cover bindings, decisions, quarantine and lifecycle, and also the entities,
related products and identity ledger, without which the state could not be rebuilt.

Stage (a) of the staged rollout: only the bootstrap entities and the records
directly reachable from them by an explicit link are in scope. Broad catalogue
discovery is switched off here and stays off until it is explicitly approved.
"""

import hashlib
import uuid
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, ValidationError

from metaspike.catalogue.generate import BOOTSTRAP_SOURCE, CatalogueState, _key_order
from metaspike.catalogue.incremental import GenerationStats, generate_catalogue_blocked
from metaspike.catalogue.overrides import PRODUCTION_OVERRIDES, OverrideSet
from metaspike.catalogue.seed import BOOTSTRAP_ANCHORS
from metaspike.domain.games import Catalogue
from metaspike.domain.identity import IdentityLedger, MappingHistory, SourceRecordKey
from metaspike.domain.model import FrozenModel
from metaspike.domain.source_policy import DeploymentStage, SourcePolicy, SourcePurpose
from metaspike.domain.source_records import SourceRecord
from metaspike.resolution.models import DecisionLog
from metaspike.resolution.quarantine import Quarantine
from metaspike.resolution.resolver import _EXTERNAL_NAME
from metaspike.resolution.source_urls import url_asserted_ids
from metaspike.storage.base import StorageCorruptionError, StoragePolicyError
from metaspike.warehouse.landing import LandingWriter, Table, timestamp
from metaspike.warehouse.objects import ObjectNotFound, ObjectStore, PreconditionFailed

PREFIX = "catalogue/"
POINTER = f"{PREFIX}CURRENT"
# Broad catalogue discovery (resolving records that nothing links to a bootstrap
# entity) is OFF. Turning it on is a reviewed change, made only after the blocked
# resolver's equivalence has been accepted. It is a constant, not a setting.
BROAD_DISCOVERY_ENABLED = False


class CatalogueConflict(Exception):
    """Another run moved the catalogue first. Nothing was changed; reload and retry."""


class BroadDiscoveryDisabled(Exception):
    """Broad catalogue discovery was asked for and is not approved."""


class CatalogueScope(StrEnum):
    BOOTSTRAP_REACHABLE = "bootstrap_reachable"
    BROAD = "broad"


class EventKind(StrEnum):
    GENERATED = "generated"
    ENTITY = "entity"
    IDENTITY = "identity"
    BINDING_BOUND = "binding_bound"
    BINDING_CLOSED = "binding_closed"
    DECISION = "decision"
    QUARANTINED = "quarantined"
    QUARANTINE_RELEASED = "quarantine_released"
    LIFECYCLE = "lifecycle"
    RELATED_PRODUCT = "related_product"


class CatalogueEvent(FrozenModel):
    """One thing that changed in one generation. Never edited once written."""

    kind: EventKind
    occurred_at: AwareDatetime
    payload: dict[str, Any]


class GenerationRecord(FrozenModel):
    generation: str
    sequence: int
    previous_generation: str | None
    generated_at: AwareDatetime
    resolver_version: str
    snapshot_object: str
    snapshot_sha256: str
    events_object: str
    events_sha256: str
    events: int
    entities: int
    bindings: int
    quarantined: int


class LoadedCatalogue(FrozenModel):
    state: CatalogueState
    record: GenerationRecord
    # The storage generation of CURRENT as it was read: the precondition for moving it.
    pointer_generation: int


# --- scope -------------------------------------------------------------------------------


def directly_reachable(records: Iterable[SourceRecord]) -> list[SourceRecord]:
    """The bootstrap entities' own records and every record an explicit link ties to them.

    A link is a structured external id, a recognised source URL, or a parent or
    version-parent reference, in either direction, followed transitively. A title is
    never a link: a record that merely shares a name with a bootstrap game is out of
    scope until broad discovery is approved.
    """
    available = {record.key: record for record in records}
    links: dict[SourceRecordKey, set[SourceRecordKey]] = {key: set() for key in available}
    source_of = {name: source for source, name in _EXTERNAL_NAME.items()}
    for record in available.values():
        named = [reference for reference in (record.parent, record.version_parent) if reference]
        for link in (*record.external_ids, *url_asserted_ids(record.websites)):
            if link.source in source_of:
                named.append(SourceRecordKey(source=source_of[link.source], source_id=link.value))
        for other in named:
            if other in available:
                links[record.key].add(other)
                links[other].add(record.key)
    reached = {key for key in BOOTSTRAP_ANCHORS.values() if key in available}
    frontier = list(reached)
    while frontier:
        for neighbour in links[frontier.pop()]:
            if neighbour not in reached:
                reached.add(neighbour)
                frontier.append(neighbour)
    return [record for key, record in available.items() if key in reached]


def in_scope(records: Iterable[SourceRecord], scope: CatalogueScope) -> list[SourceRecord]:
    if scope is CatalogueScope.BROAD:
        if not BROAD_DISCOVERY_ENABLED:
            raise BroadDiscoveryDisabled(
                "broad catalogue discovery is off; only bootstrap-reachable records are resolved"
            )
        return list(records)
    return directly_reachable(records)


# --- events ------------------------------------------------------------------------------


def events_between(
    previous: CatalogueState | None, new: CatalogueState
) -> tuple[CatalogueEvent, ...]:
    """Everything that is in `new` and was not in `previous`, as events.

    Empty when nothing changed apart from the time of the run.
    """
    at = new.generated_at
    events: list[CatalogueEvent] = []

    def emit(kind: EventKind, payload: dict[str, Any]) -> None:
        events.append(CatalogueEvent(kind=kind, occurred_at=at, payload=payload))

    before = {} if previous is None else {g.canonical_game_id: g for g in previous.catalogue.games}
    for game in new.catalogue.games:
        if before.get(game.canonical_game_id) != game:
            emit(EventKind.ENTITY, game.model_dump(mode="json"))
    if previous is None or previous.identity != new.identity:
        emit(EventKind.IDENTITY, new.identity.model_dump(mode="json"))

    old_bindings = (
        {} if previous is None else {b.mapping_version: b for b in previous.mapping.bindings}
    )
    for binding in new.mapping.bindings:
        earlier = old_bindings.get(binding.mapping_version)
        if earlier is None:
            emit(EventKind.BINDING_BOUND, binding.model_dump(mode="json"))
            if binding.valid_to is not None:
                emit(EventKind.BINDING_CLOSED, binding.model_dump(mode="json"))
        elif earlier != binding:
            emit(EventKind.BINDING_CLOSED, binding.model_dump(mode="json"))

    known = 0 if previous is None else len(previous.decisions.decisions)
    for decision in new.decisions.decisions[known:]:
        emit(EventKind.DECISION, decision.model_dump(mode="json"))

    held = {} if previous is None else {e.record.key: e for e in previous.quarantine.entries}
    still = {entry.record.key for entry in new.quarantine.entries}
    for key in held:
        if key not in still:
            emit(EventKind.QUARANTINE_RELEASED, {"record": key.model_dump(mode="json")})
    for entry in new.quarantine.entries:
        if held.get(entry.record.key) != entry:
            emit(EventKind.QUARANTINED, entry.model_dump(mode="json"))

    lifecycles = {} if previous is None else {h.canonical_game_id: h for h in previous.lifecycle}
    for history in new.lifecycle:
        if lifecycles.get(history.canonical_game_id) != history:
            emit(EventKind.LIFECYCLE, history.model_dump(mode="json"))

    products = {} if previous is None else {p.record: p for p in previous.related_products}
    for product in new.related_products:
        if products.get(product.record) != product:
            emit(EventKind.RELATED_PRODUCT, product.model_dump(mode="json"))

    if not events and previous is not None:
        return ()
    header = CatalogueEvent(
        kind=EventKind.GENERATED,
        occurred_at=at,
        payload={
            "generated_at": timestamp(at),
            "resolver_version": new.resolver_version,
            "lifecycle_rule_version": new.lifecycle_rule_version,
            "overrides_version": new.overrides_version,
        },
    )
    return (header, *events)


def fold(generations: Sequence[Sequence[CatalogueEvent]]) -> CatalogueState:
    """Rebuild the catalogue state from every generation's events, oldest first."""
    header: dict[str, Any] = {}
    games: dict[str, Any] = {}
    identity: Any = None
    bindings: dict[int, Any] = {}
    decisions: list[Any] = []
    quarantine: dict[str, Any] = {}
    lifecycle: dict[str, Any] = {}
    related: dict[str, Any] = {}
    for events in generations:
        for event in events:
            payload = event.payload
            if event.kind is EventKind.GENERATED:
                header = payload
            elif event.kind is EventKind.ENTITY:
                games[payload["canonical_game_id"]] = payload
            elif event.kind is EventKind.IDENTITY:
                identity = payload
            elif event.kind in (EventKind.BINDING_BOUND, EventKind.BINDING_CLOSED):
                bindings[payload["mapping_version"]] = payload
            elif event.kind is EventKind.DECISION:
                decisions.append(payload)
            elif event.kind is EventKind.QUARANTINE_RELEASED:
                quarantine.pop(_key_text(payload["record"]), None)
            elif event.kind is EventKind.QUARANTINED:
                # Re-evaluation moves an entry to the end, as it does in the live state.
                quarantine.pop(_key_text(payload["record"]["key"]), None)
                quarantine[_key_text(payload["record"]["key"])] = payload
            elif event.kind is EventKind.LIFECYCLE:
                lifecycle[payload["canonical_game_id"]] = payload
            elif event.kind is EventKind.RELATED_PRODUCT:
                related[_key_text(payload["record"])] = payload
    if not header:
        raise StorageCorruptionError("the catalogue's events do not start with a generation")
    products = sorted(
        related.values(), key=lambda p: _key_order(SourceRecordKey.model_validate(p["record"]))
    )
    try:
        return CatalogueState(
            generated_at=header["generated_at"],
            resolver_version=header["resolver_version"],
            lifecycle_rule_version=header["lifecycle_rule_version"],
            overrides_version=header["overrides_version"],
            catalogue=Catalogue.model_validate({"games": list(games.values())}),
            identity=IdentityLedger.model_validate(identity or {}),
            mapping=MappingHistory.model_validate(
                {"bindings": [bindings[version] for version in sorted(bindings)]}
            ),
            decisions=DecisionLog.model_validate({"decisions": decisions}),
            quarantine=Quarantine.model_validate({"entries": list(quarantine.values())}),
            lifecycle=tuple(lifecycle[identifier] for identifier in games),
            related_products=tuple(products),
        )
    except (ValidationError, KeyError):
        raise StorageCorruptionError("the catalogue's events do not form a valid state") from None


def _key_text(key: dict[str, Any]) -> str:
    return f"{key['source']}\x1f{key['source_id']}"


def _encode(events: Sequence[CatalogueEvent]) -> bytes:
    return "".join(event.model_dump_json() + "\n" for event in events).encode("utf-8")


def _decode(data: bytes, name: str) -> tuple[CatalogueEvent, ...]:
    try:
        return tuple(
            CatalogueEvent.model_validate_json(line) for line in data.decode("utf-8").splitlines()
        )
    except ValidationError:
        raise StorageCorruptionError(f"{name} is not a valid event file") from None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- the repository ----------------------------------------------------------------------


class CatalogueRepository:
    def __init__(
        self,
        store: ObjectStore,
        policy_for: Callable[[str], SourcePolicy],
        stage: DeploymentStage = DeploymentStage.PRIVATE_COLLECTION,
    ) -> None:
        self._store = store
        self._policy_for = policy_for
        self._stage = stage

    def load(self) -> LoadedCatalogue | None:
        """The head generation, verified against its recorded hash. None before the first."""
        try:
            pointer = self._store.get(POINTER)
        except ObjectNotFound:
            return None
        record = self._record(pointer.data, POINTER)
        snapshot = self._store.get(record.snapshot_object).data
        if _sha(snapshot) != record.snapshot_sha256:
            raise StorageCorruptionError(f"{record.snapshot_object} does not match its record")
        try:
            state = CatalogueState.model_validate_json(snapshot)
        except ValidationError:
            raise StorageCorruptionError(f"{record.snapshot_object} is not a catalogue") from None
        return LoadedCatalogue(state=state, record=record, pointer_generation=pointer.generation)

    def commit(
        self, state: CatalogueState, head: LoadedCatalogue | None
    ) -> GenerationRecord | None:
        """Write `state` as the generation after `head`, if `head` is still the head.

        Returns None, writing nothing, when the state holds no change. Raises
        CatalogueConflict when another run has moved the catalogue since `head` was read.
        """
        self._refuse_unless_permitted(state)
        events = events_between(None if head is None else head.state, state)
        if not events:
            return None
        snapshot = state.model_dump_json().encode("utf-8")
        encoded = _encode(events)
        sequence = 1 if head is None else head.record.sequence + 1
        generation = f"g{sequence:08d}-{_sha(snapshot)[:16]}"
        record = GenerationRecord(
            generation=generation,
            sequence=sequence,
            previous_generation=None if head is None else head.record.generation,
            generated_at=state.generated_at,
            resolver_version=state.resolver_version,
            snapshot_object=f"{PREFIX}snapshots/{generation}.json",
            snapshot_sha256=_sha(snapshot),
            events_object=f"{PREFIX}events/{generation}.ndjson",
            events_sha256=_sha(encoded),
            events=len(events),
            entities=len(state.catalogue.games),
            bindings=len(state.mapping.bindings),
            quarantined=len(state.quarantine.entries),
        )
        body = record.model_dump_json().encode("utf-8")
        self._create_exactly(record.snapshot_object, snapshot)
        self._create_exactly(record.events_object, encoded)
        self._create_exactly(f"{PREFIX}generations/{generation}.json", body)
        try:
            if head is None:
                self._store.create(POINTER, body)
            else:
                self._store.replace(POINTER, body, head.pointer_generation)
        except PreconditionFailed:
            raise CatalogueConflict(
                "the catalogue was advanced by another run; nothing was changed"
            ) from None
        return record

    def history(self) -> tuple[GenerationRecord, ...]:
        """Every generation, oldest first, found by following each record to the one before."""
        head = self.load()
        if head is None:
            return ()
        chain = [head.record]
        while chain[-1].previous_generation is not None:
            name = f"{PREFIX}generations/{chain[-1].previous_generation}.json"
            chain.append(self._record(self._store.get(name).data, name))
        return tuple(reversed(chain))

    def events(self, record: GenerationRecord) -> tuple[CatalogueEvent, ...]:
        data = self._store.get(record.events_object).data
        if _sha(data) != record.events_sha256:
            raise StorageCorruptionError(f"{record.events_object} does not match its record")
        return _decode(data, record.events_object)

    def rebuild(self) -> CatalogueState:
        """The head state, rebuilt from the events alone. The snapshot is not read."""
        return fold([self.events(record) for record in self.history()])

    def _record(self, data: bytes, name: str) -> GenerationRecord:
        try:
            return GenerationRecord.model_validate_json(data)
        except ValidationError:
            raise StorageCorruptionError(f"{name} is not a generation record") from None

    def _create_exactly(self, name: str, data: bytes) -> None:
        """Create an immutable object, accepting one that already holds these bytes.

        A run that died after writing a snapshot and before moving CURRENT leaves the
        snapshot behind; the retry computes the same name and the same bytes.
        """
        try:
            self._store.create(name, data)
        except PreconditionFailed:
            if self._store.get(name).data != data:
                raise StorageCorruptionError(f"{name} exists with different contents") from None

    def _refuse_unless_permitted(self, state: CatalogueState) -> None:
        sources = {binding.key.source for binding in state.mapping.bindings}
        sources |= {entry.record.key.source for entry in state.quarantine.entries}
        sources |= {product.record.source for product in state.related_products}
        sources |= {decision.record.source for decision in state.decisions.decisions}
        for source in sorted(sources - {BOOTSTRAP_SOURCE}):
            refusal = self._policy_for(source).registration_refusal(
                SourcePurpose.CATALOGUE_METADATA, self._stage
            )
            if refusal is not None:
                raise StoragePolicyError(f"{source}: {refusal}")


def advance_catalogue(
    repository: CatalogueRepository,
    records: Iterable[SourceRecord],
    *,
    as_of: datetime,
    scope: CatalogueScope = CatalogueScope.BOOTSTRAP_REACHABLE,
    overrides: OverrideSet = PRODUCTION_OVERRIDES,
    id_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    stats: GenerationStats | None = None,
    cross_check: bool = False,
) -> GenerationRecord | None:
    """Resolve what is new against the stored catalogue and commit the result.

    `records` is the latest version of every stored source record: the evidence. Only
    new records, and quarantined records whose evidence changed, are resolved.
    """
    scoped = in_scope(records, scope)
    head = repository.load()
    state = generate_catalogue_blocked(
        scoped,
        as_of=as_of,
        overrides=overrides,
        id_factory=id_factory,
        previous=None if head is None else head.state,
        stats=stats,
        cross_check=cross_check,
    )
    return repository.commit(state, head)


# --- the warehouse copy ------------------------------------------------------------------


def catalogue_event_rows(
    generation: str, events: Sequence[CatalogueEvent]
) -> dict[tuple[Table, str], list[dict[str, Any]]]:
    """Rows for the three catalogue tables, grouped by table and by the record's source.

    Bindings, decisions and quarantine are stated about a source record, so they land
    under that source's prefix and are subject to that source's policy. Lifecycle and
    entity events are about canonical entities and have no warehouse table; they are
    kept in the state bucket only.
    """
    rows: dict[tuple[Table, str], list[dict[str, Any]]] = {}
    for event in events:
        payload, at = event.payload, timestamp(event.occurred_at)
        if event.kind in (EventKind.BINDING_BOUND, EventKind.BINDING_CLOSED):
            source = payload["key"]["source"]
            closed = event.kind is EventKind.BINDING_CLOSED
            rows.setdefault((Table.BINDING_EVENTS, source), []).append(
                {
                    "source": source,
                    "source_id": payload["key"]["source_id"],
                    "action": "closed" if closed else "bound",
                    "canonical_game_id": payload["canonical_game_id"],
                    "mapping_version": payload["mapping_version"],
                    "occurred_at": _row_time(payload["valid_to" if closed else "valid_from"]),
                    "decision_ref": payload["decision_ref"],
                    "catalogue_generation": generation,
                }
            )
        elif event.kind is EventKind.DECISION:
            source = payload["record"]["source"]
            rows.setdefault((Table.RESOLUTION_DECISIONS, source), []).append(
                {
                    "sequence": payload["sequence"],
                    "record_source": source,
                    "record_source_id": payload["record"]["source_id"],
                    "record_content_hash": payload["record_content_hash"],
                    "decided_at": _row_time(payload["decided_at"]),
                    "kind": payload["decision"]["kind"],
                    "canonical_game_id": payload["canonical_game_id"],
                    "resolver_version": payload["decision"]["resolver_version"],
                    "decision": payload["decision"],
                    "catalogue_generation": generation,
                }
            )
        elif event.kind in (EventKind.QUARANTINED, EventKind.QUARANTINE_RELEASED):
            released = event.kind is EventKind.QUARANTINE_RELEASED
            key = payload["record"] if released else payload["record"]["key"]
            rows.setdefault((Table.QUARANTINE_EVENTS, key["source"]), []).append(
                {
                    "record_source": key["source"],
                    "record_source_id": key["source_id"],
                    "action": "released" if released else "quarantined",
                    "reason": None if released else payload["reason"],
                    "fingerprint": None if released else payload["fingerprint"],
                    "occurred_at": at,
                    "decision_ref": None if released else payload["decision_history"][-1],
                    "catalogue_generation": generation,
                }
            )
    return rows


def _row_time(text: str) -> str:
    return timestamp(datetime.fromisoformat(text))


def land_catalogue_events(
    writer: LandingWriter, record: GenerationRecord, events: Sequence[CatalogueEvent]
) -> int:
    """Land a generation's bindings, decisions and quarantine events for the warehouse."""
    landed = 0
    grouped = catalogue_event_rows(record.generation, events)
    for (table, source), rows in sorted(
        grouped.items(), key=lambda item: (item[0][0].value, item[0][1])
    ):
        writer.land(table, source, record.generation, record.generated_at, rows)
        landed += len(rows)
    return landed


def entity_and_lifecycle_rows(
    previous: CatalogueState | None, new: CatalogueState, generation: str
) -> dict[Table, list[dict[str, Any]]]:
    """Rows for `ms_catalogue.entity_events` and `ms_catalogue.lifecycle_events`.

    These describe canonical entities, which are MetaSpike's own derived records and
    not anything a source said, so they are landed under the catalogue's own prefix.
    Each row states what changed in this generation, with the state before it.
    """
    at = timestamp(new.generated_at)
    before = {} if previous is None else {g.canonical_game_id: g for g in previous.catalogue.games}
    entities: list[dict[str, Any]] = []
    for game in new.catalogue.games:
        earlier = before.get(game.canonical_game_id)
        if earlier != game:
            entities.append(
                {
                    "canonical_game_id": game.canonical_game_id,
                    "event_type": "created" if earlier is None else "updated",
                    "occurred_at": at,
                    "previous": None if earlier is None else earlier.model_dump(mode="json"),
                    "entity": game.model_dump(mode="json"),
                    "catalogue_generation": generation,
                    "schema_version": "entity_events_v1",
                }
            )

    histories = {} if previous is None else {h.canonical_game_id: h for h in previous.lifecycle}
    lifecycle: list[dict[str, Any]] = []
    for history in new.lifecycle:
        earlier_history = histories.get(history.canonical_game_id)
        known = 0 if earlier_history is None else len(earlier_history.records)
        for position in range(known, len(history.records)):
            record = history.records[position]
            lifecycle.append(
                {
                    "canonical_game_id": history.canonical_game_id,
                    "event_type": "state_recorded",
                    "occurred_at": at,
                    "previous_state": (
                        None if position == 0 else history.records[position - 1].state.value
                    ),
                    "new_state": record.state.value,
                    "valid_from": record.valid_from.isoformat(),
                    "rule_id": record.rule_id,
                    "rule_version": record.rule_version,
                    "detail": record.model_dump(mode="json"),
                    "catalogue_generation": generation,
                    "schema_version": "lifecycle_events_v1",
                }
            )
        pending = (history.pending_state, history.pending_since)
        earlier_pending = (
            (None, None)
            if earlier_history is None
            else (earlier_history.pending_state, earlier_history.pending_since)
        )
        if pending != earlier_pending:
            current = history.current
            lifecycle.append(
                {
                    "canonical_game_id": history.canonical_game_id,
                    "event_type": "pending_changed",
                    "occurred_at": at,
                    "previous_state": None if current is None else current.state.value,
                    "new_state": None if pending[0] is None else pending[0].value,
                    "valid_from": None if pending[1] is None else pending[1].isoformat(),
                    "rule_id": None,
                    "rule_version": None,
                    "detail": {
                        "pending_state": None if pending[0] is None else pending[0].value,
                        "pending_since": None if pending[1] is None else pending[1].isoformat(),
                    },
                    "catalogue_generation": generation,
                    "schema_version": "lifecycle_events_v1",
                }
            )
    return {Table.ENTITY_EVENTS: entities, Table.LIFECYCLE_EVENTS: lifecycle}
