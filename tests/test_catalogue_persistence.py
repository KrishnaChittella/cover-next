"""The persistent catalogue: append-only events, immutable snapshots, one guarded pointer."""

import json
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from metaspike.catalogue import persistence
from metaspike.catalogue.generate import generate_catalogue
from metaspike.catalogue.incremental import GenerationStats
from metaspike.catalogue.persistence import (
    POINTER,
    BroadDiscoveryDisabled,
    CatalogueConflict,
    CatalogueRepository,
    CatalogueScope,
    EventKind,
    advance_catalogue,
    catalogue_event_rows,
    directly_reachable,
    events_between,
    fold,
    in_scope,
    land_catalogue_events,
)
from metaspike.catalogue.seed import BOOTSTRAP_ANCHORS
from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.source_policy import SourcePolicy
from metaspike.domain.source_records import ProductType, SourceRecord
from metaspike.resolution.recorded import load_recorded_catalogue
from metaspike.storage.base import StorageCorruptionError, StoragePolicyError
from metaspike.warehouse.landing import Table
from metaspike.warehouse.objects import AccessDenied, MemoryObjectStore
from metaspike.warehouse.tables import TableRef
from tests.warehouse_support import cleared_policy, real_policy, world

FIXTURES = Path(__file__).parent / "fixtures"
AS_OF = datetime(2026, 10, 6, tzinfo=UTC)


def ids(start: int = 10_000) -> Callable[[], uuid.UUID]:
    issued = [0]

    def factory() -> uuid.UUID:
        issued[0] += 1
        return uuid.UUID(int=start + issued[0], version=4)

    return factory


@pytest.fixture(scope="module")
def recorded() -> list[SourceRecord]:
    return list(load_recorded_catalogue(FIXTURES / "catalogue").values())


def state_bucket() -> tuple[MemoryObjectStore, MemoryObjectStore]:
    """The bucket, and the catalogue job's view of it: its own prefix, by name, no listing."""
    bucket = MemoryObjectStore()
    job = bucket.view(
        writable_prefixes=("catalogue/",), readable_prefixes=("catalogue/",), can_list=False
    )
    return bucket, job


def repository(
    store: MemoryObjectStore, policy_for: Callable[[str], SourcePolicy] = cleared_policy
) -> CatalogueRepository:
    return CatalogueRepository(store, policy_for)


# --- scope -------------------------------------------------------------------------------


def test_only_records_linked_to_a_bootstrap_entity_are_in_scope(
    recorded: list[SourceRecord],
) -> None:
    reachable = directly_reachable(recorded)
    keys = {record.key for record in reachable}
    anchors = {key for key in BOOTSTRAP_ANCHORS.values() if key in {r.key for r in recorded}}
    assert anchors and anchors <= keys
    assert 10 < len(reachable) < len(recorded) / 2
    by_key = {record.key: record for record in recorded}
    # Counter-Strike 2's IGDB record asserts Steam app 730, so the app is reachable...
    assert by_key[BOOTSTRAP_ANCHORS["counter-strike-2"]].external_ids
    steam = {k.source_id for k in keys if k.source == "steam_catalogue"}
    assert "730" in steam
    # ...and a record that only shares a name with a bootstrap game is not.
    anchor = by_key[BOOTSTRAP_ANCHORS["elden-ring"]]
    namesake = SourceRecord(
        key=SourceRecordKey(source="steam_catalogue", source_id="999999001"),
        title=anchor.title,
        product_type=ProductType.UNKNOWN,
        fetched_at=anchor.fetched_at,
    )
    assert namesake.key not in {r.key for r in directly_reachable([*recorded, namesake])}
    assert len(directly_reachable([*recorded, namesake])) == len(reachable)
    assert directly_reachable(reachable) == reachable  # closed under its own links
    assert directly_reachable([]) == []


def test_broad_discovery_is_off_and_cannot_be_asked_for(recorded: list[SourceRecord]) -> None:
    assert persistence.BROAD_DISCOVERY_ENABLED is False
    with pytest.raises(BroadDiscoveryDisabled):
        in_scope(recorded, CatalogueScope.BROAD)
    _, job = state_bucket()
    with pytest.raises(BroadDiscoveryDisabled):
        advance_catalogue(repository(job), recorded, as_of=AS_OF, scope=CatalogueScope.BROAD)
    assert job.writes == 0
    assert in_scope(recorded, CatalogueScope.BOOTSTRAP_REACHABLE) == directly_reachable(recorded)


# --- writing and reading -----------------------------------------------------------------


def test_the_first_generation_is_a_snapshot_its_events_and_a_pointer(
    recorded: list[SourceRecord],
) -> None:
    bucket, job = state_bucket()
    repo = repository(job)
    assert repo.load() is None and repo.history() == ()
    record = advance_catalogue(repo, recorded, as_of=AS_OF, id_factory=ids())
    assert record is not None and record.sequence == 1 and record.previous_generation is None
    assert bucket.list("") == (
        POINTER,
        f"catalogue/events/{record.generation}.ndjson",
        f"catalogue/generations/{record.generation}.json",
        f"catalogue/snapshots/{record.generation}.json",
    )
    assert (
        bucket.get(POINTER).data
        == bucket.get(f"catalogue/generations/{record.generation}.json").data
    )
    head = repo.load()
    assert head is not None and head.record == record
    expected = generate_catalogue(directly_reachable(recorded), as_of=AS_OF, id_factory=ids())
    assert head.state == expected
    assert (record.entities, record.bindings) == (
        len(expected.catalogue.games), len(expected.mapping.bindings)
    )  # fmt: skip
    assert len(expected.catalogue.games) >= 15  # the bootstrap entities are all there


def test_the_events_alone_rebuild_the_snapshot(recorded: list[SourceRecord]) -> None:
    _, job = state_bucket()
    repo = repository(job)
    reachable = directly_reachable(recorded)
    advance_catalogue(repo, reachable[: len(reachable) // 2], as_of=AS_OF, id_factory=ids())
    first = repo.load()
    assert first is not None and repo.rebuild() == first.state
    kinds = Counter(event.kind for event in repo.events(first.record))
    assert kinds[EventKind.GENERATED] == 1
    assert kinds[EventKind.ENTITY] == len(first.state.catalogue.games)
    assert kinds[EventKind.LIFECYCLE] == len(first.state.lifecycle)
    assert kinds[EventKind.DECISION] == len(first.state.decisions.decisions)
    assert kinds[EventKind.BINDING_BOUND] == len(first.state.mapping.bindings)
    assert kinds[EventKind.QUARANTINED] == len(first.state.quarantine.entries)

    later = AS_OF + timedelta(days=1)
    second = advance_catalogue(repo, reachable, as_of=later, id_factory=ids(50_000))
    assert second is not None and second.sequence == 2
    assert second.previous_generation == first.record.generation
    head = repo.load()
    assert head is not None and head.state != first.state
    assert repo.rebuild() == head.state  # folded from both generations' events
    assert [r.generation for r in repo.history()] == [first.record.generation, second.generation]
    new = Counter(event.kind for event in repo.events(second))
    assert (
        new[EventKind.DECISION] == len(head.state.decisions.decisions) - kinds[EventKind.DECISION]
    )
    assert new[EventKind.DECISION] > 0
    # A generation's events say only what changed in it.
    assert new[EventKind.ENTITY] < len(head.state.catalogue.games)


def test_events_are_empty_when_nothing_changed_and_complete_when_something_did(
    recorded: list[SourceRecord],
) -> None:
    reachable = directly_reachable(recorded)
    state = generate_catalogue(reachable, as_of=AS_OF, id_factory=ids())
    same = generate_catalogue(reachable, as_of=AS_OF, id_factory=ids(), previous=state)
    assert events_between(state, same) == ()
    assert fold([events_between(None, state)]) == state
    with pytest.raises(StorageCorruptionError):
        fold([])


def test_a_run_that_changes_nothing_writes_nothing(recorded: list[SourceRecord]) -> None:
    bucket, job = state_bucket()
    repo = repository(job)
    advance_catalogue(repo, recorded, as_of=AS_OF, id_factory=ids())
    before, writes = {n: bucket.get(n) for n in bucket.list("")}, bucket.writes
    stats = GenerationStats()
    assert advance_catalogue(repo, recorded, as_of=AS_OF, stats=stats) is None
    assert bucket.writes == writes and {n: bucket.get(n) for n in bucket.list("")} == before
    assert stats.resolver_calls == 0


def test_earlier_generations_are_never_rewritten(recorded: list[SourceRecord]) -> None:
    bucket, job = state_bucket()
    repo = repository(job)
    reachable = directly_reachable(recorded)
    advance_catalogue(repo, reachable[:10], as_of=AS_OF, id_factory=ids())
    first = {name: bucket.get(name) for name in bucket.list("") if name != POINTER}
    replaced: list[str] = []
    real = MemoryObjectStore.replace

    def spy(self: MemoryObjectStore, name: str, *rest: object, **more: object) -> int:
        replaced.append(name)
        return real(self, name, *rest, **more)  # type: ignore[arg-type]

    MemoryObjectStore.replace = spy  # type: ignore[method-assign]
    try:
        advance_catalogue(repo, reachable, as_of=AS_OF + timedelta(days=1), id_factory=ids(50_000))
    finally:
        MemoryObjectStore.replace = real  # type: ignore[method-assign]
    assert replaced == [POINTER]  # the pointer is the only object ever replaced
    assert {name: bucket.get(name) for name in first} == first
    assert len(bucket.list("")) == 7


# --- the conditional pointer -------------------------------------------------------------


def test_two_runs_cannot_both_advance_the_same_head(recorded: list[SourceRecord]) -> None:
    bucket, job = state_bucket()
    repo = repository(job)
    reachable = directly_reachable(recorded)
    advance_catalogue(repo, reachable[:10], as_of=AS_OF, id_factory=ids())
    head = repo.load()
    assert head is not None
    later = AS_OF + timedelta(days=1)
    one = generate_catalogue(
        reachable[:13], as_of=later, id_factory=ids(50_000), previous=head.state
    )
    two = generate_catalogue(reachable, as_of=later, id_factory=ids(70_000), previous=head.state)
    won = repo.commit(one, head)
    assert won is not None
    pointer = bucket.get(POINTER)
    with pytest.raises(CatalogueConflict, match="nothing was changed"):
        repo.commit(two, head)  # it read the head before the other run moved it
    assert bucket.get(POINTER) == pointer
    current = repo.load()
    assert current is not None and current.state == one and current.record == won
    assert repo.rebuild() == one
    # The loser reloads and tries again from the new head.
    retried = generate_catalogue(
        reachable, as_of=later + timedelta(hours=1), id_factory=ids(90_000), previous=current.state
    )
    assert repo.commit(retried, current) is not None and repo.rebuild() == retried


def test_two_first_runs_cannot_both_create_the_catalogue(recorded: list[SourceRecord]) -> None:
    _, job = state_bucket()
    repo = repository(job)
    reachable = directly_reachable(recorded)
    one = generate_catalogue(reachable[:10], as_of=AS_OF, id_factory=ids())
    two = generate_catalogue(reachable, as_of=AS_OF, id_factory=ids())
    assert repo.commit(one, None) is not None
    with pytest.raises(CatalogueConflict):
        repo.commit(two, None)
    head = repo.load()
    assert head is not None and head.state == one


def test_a_run_that_died_before_moving_the_pointer_can_simply_run_again(
    recorded: list[SourceRecord],
) -> None:
    bucket, job = state_bucket()
    repo = repository(job)
    state = generate_catalogue(directly_reachable(recorded), as_of=AS_OF, id_factory=ids())
    real = job.create

    def die_at_pointer(name: str, data: bytes, content_type: str = "application/json") -> int:
        if name == POINTER:
            raise RuntimeError("the job was stopped")
        return real(name, data, content_type)

    job.create = die_at_pointer  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        repo.commit(state, None)
    assert repo.load() is None and len(bucket.list("")) == 3  # orphans, and no head
    job.create = real  # type: ignore[method-assign]
    assert repo.commit(state, None) is not None
    head = repo.load()
    assert head is not None and head.state == state and len(bucket.list("")) == 4


# --- integrity and access ----------------------------------------------------------------


def test_a_snapshot_or_event_file_that_was_altered_is_refused(recorded: list[SourceRecord]) -> None:
    bucket, job = state_bucket()
    repo = repository(job)
    record = advance_catalogue(repo, recorded, as_of=AS_OF, id_factory=ids())
    assert record is not None
    for name, reader in (
        (record.events_object, repo.rebuild),
        (record.snapshot_object, repo.load),
    ):
        original = bucket.get(name)
        generation = bucket.replace(name, original.data + b" ", original.generation)
        with pytest.raises(StorageCorruptionError, match="does not match its record"):
            reader()
        bucket.replace(name, original.data, generation)
    assert repo.load() is not None


def test_readers_follow_the_pointer_by_name_and_cannot_write(recorded: list[SourceRecord]) -> None:
    bucket, job = state_bucket()
    advance_catalogue(repository(job), recorded, as_of=AS_OF, id_factory=ids())
    # A collector's grant: read-only on the prefix, and no listing.
    reader = bucket.view(writable_prefixes=(), readable_prefixes=("catalogue/",), can_list=False)
    with pytest.raises(AccessDenied):
        reader.list("catalogue/")
    seen = repository(reader).load()
    written = repository(job).load()
    assert seen is not None and written is not None and seen.state == written.state
    with pytest.raises(AccessDenied):
        repository(reader).commit(generate_catalogue(as_of=AS_OF + timedelta(days=400)), None)
    # And the catalogue job itself never lists: its view of the bucket refuses it.
    with pytest.raises(AccessDenied):
        job.list("catalogue/")


# --- source policy -----------------------------------------------------------------------


def test_the_real_policies_keep_steam_records_out_of_the_persisted_catalogue(
    recorded: list[SourceRecord],
) -> None:
    bucket, job = state_bucket()
    repo = repository(job, real_policy)
    with pytest.raises(
        StoragePolicyError, match="steam_catalogue: not cleared for private_collection"
    ):
        advance_catalogue(repo, recorded, as_of=AS_OF, id_factory=ids())
    assert bucket.list("") == ()
    igdb_only = [record for record in recorded if record.key.source == "igdb"]
    record = advance_catalogue(repo, igdb_only, as_of=AS_OF, id_factory=ids())
    assert record is not None
    head = repo.load()
    assert head is not None
    assert {b.key.source for b in head.state.mapping.bindings} == {"igdb"}
    assert len(head.state.catalogue.games) >= 15


# --- the warehouse copy ------------------------------------------------------------------


def test_bindings_decisions_and_quarantine_reach_the_warehouse_tables(
    recorded: list[SourceRecord],
) -> None:
    _, job = state_bucket()
    repo = repository(job)
    reachable = directly_reachable(recorded)
    first = advance_catalogue(repo, reachable[: len(reachable) // 2], as_of=AS_OF, id_factory=ids())
    second = advance_catalogue(
        repo, reachable, as_of=AS_OF + timedelta(days=1), id_factory=ids(50_000)
    )
    assert first is not None and second is not None
    w = world(lambda: AS_OF + timedelta(days=2))
    landed = sum(land_catalogue_events(w.writer, r, repo.events(r)) for r in (first, second))
    assert w.load_everything() == landed > 0

    head = repo.load()
    assert head is not None
    decisions = w.warehouse.all_rows(TableRef("ms_catalogue", "resolution_decisions"))
    assert sorted(row["sequence"] for row in decisions) == [
        d.sequence for d in head.state.decisions.decisions
    ]
    assert {row["catalogue_generation"] for row in decisions} == {
        first.generation,
        second.generation,
    }
    stored = next(row for row in decisions if row["sequence"] == 1)
    assert stored["decision"] == json.loads(
        head.state.decisions.decisions[0].decision.model_dump_json()
    )
    bindings = w.warehouse.all_rows(TableRef("ms_catalogue", "binding_events"))
    bound = [row for row in bindings if row["action"] == "bound"]
    assert sorted(row["mapping_version"] for row in bound) == [
        b.mapping_version for b in head.state.mapping.bindings
    ]
    assert {(r["source"], r["source_id"], r["canonical_game_id"]) for r in bound} == {
        (b.key.source, b.key.source_id, b.canonical_game_id) for b in head.state.mapping.bindings
    }
    quarantine = w.warehouse.all_rows(TableRef("ms_catalogue", "quarantine_events"))
    held = {
        (r["record_source"], r["record_source_id"])
        for r in quarantine
        if r["action"] == "quarantined"
    }
    released = {
        (r["record_source"], r["record_source_id"]) for r in quarantine if r["action"] == "released"
    }
    assert {
        (e.record.key.source, e.record.key.source_id) for e in head.state.quarantine.entries
    } <= held
    assert released <= held
    # Each row sits under the prefix of the source it is about.
    for name in w.landing.list(""):
        source, table = name.split("/")[:2]
        assert source in {"igdb", "steam_catalogue"}
        assert Table(table) in {
            Table.BINDING_EVENTS,
            Table.RESOLUTION_DECISIONS,
            Table.QUARANTINE_EVENTS,
        }


def test_catalogue_rows_for_an_uncleared_source_are_not_landed(
    recorded: list[SourceRecord],
) -> None:
    _, job = state_bucket()
    repo = repository(job)  # test-only clearance, so a state with Steam records exists
    record = advance_catalogue(repo, recorded, as_of=AS_OF, id_factory=ids())
    assert record is not None
    events = repo.events(record)
    assert any(
        source == "steam_catalogue" for _, source in catalogue_event_rows(record.generation, events)
    )
    w = world(lambda: AS_OF, policy_for=real_policy)
    with pytest.raises(StoragePolicyError, match="steam_catalogue"):
        land_catalogue_events(w.writer, record, events)
    assert not [name for name in w.landing.list("") if name.startswith("steam_catalogue/")]
