import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from metaspike.catalogue import legacy_ids
from metaspike.catalogue.legacy_ids import (
    LEGACY_ID_TO_CANONICAL,
    MIGRATED_AT,
    LegacyMappingError,
    canonical_id_for_legacy,
    legacy_identity_ledger,
    mapping_digest,
    verify_legacy_mapping,
)
from metaspike.catalogue.seed import CatalogueError, load_seed_catalogue
from metaspike.domain.games import CANONICAL_ID_PATTERN, Catalogue, RelationshipType
from metaspike.domain.identity import (
    AliasKind,
    IdentityAlias,
    IdentityError,
    IdentityLedger,
    LineageKind,
    MappingHistory,
    SourceRecordKey,
    new_canonical_id,
)
from metaspike.domain.observations import TimeSeries
from metaspike.domain.timeseries import TimeWindow
from metaspike.scoring.v0 import V0_INPUT_SIGNAL
from metaspike.sources.wikimedia import WIKIMEDIA_PAGEVIEWS_POLICY
from metaspike.storage.local import LocalObservationStore
from tests.conftest import RECORDED_END, RUN_TIME, make_id, series_of, spiking

LATER = MIGRATED_AT + timedelta(days=30)
A, B, C, D = (make_id(n) for n in (1, 2, 3, 4))

# What each legacy id stood for before the migration. Identity is the slug and name,
# not the id: this table is the definition of "the same logical entity".
LOGICAL_IDENTITY = {
    "msg_0001": "gta-v",
    "msg_0002": "gta-online",
    "msg_0003": "fivem",
    "msg_0004": "gta-vi",
    "msg_0005": "escape-from-tarkov",
    "msg_0006": "star-citizen",
    "msg_0007": "no-mans-sky",
    "msg_0008": "counter-strike-2",
    "msg_0009": "dota-2",
    "msg_0010": "fortnite",
    "msg_0011": "minecraft",
    "msg_0012": "elden-ring",
    "msg_0013": "baldurs-gate-3",
    "msg_0014": "helldivers-2",
    "msg_0015": "rust",
}


# --- the frozen migration ----------------------------------------------------------


def test_every_legacy_id_maps_exactly_once_to_a_unique_valid_uuid4() -> None:
    verify_legacy_mapping()
    assert sorted(LEGACY_ID_TO_CANONICAL) == [f"msg_{n:04d}" for n in range(1, 16)]
    canonical = list(LEGACY_ID_TO_CANONICAL.values())
    assert len(set(canonical)) == 15
    for value in canonical:
        assert re.fullmatch(CANONICAL_ID_PATTERN, value)
        parsed = uuid.UUID(value)
        assert parsed.version == 4 and str(parsed) == value  # canonical lowercase form


def test_the_mapping_is_pinned_and_cannot_be_regenerated_or_edited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert mapping_digest() == "bc254cf7d70e417b1bbfa2e0aa74146f3783376bf61bec14778ba6d3f160f551"
    assert canonical_id_for_legacy("msg_0001") == "780b4850-6248-4a81-8fe0-7f0762376cfc"
    assert canonical_id_for_legacy("msg_0015") == "2128bf4d-7afc-4d2b-9803-3afae37bb7f3"
    with pytest.raises(TypeError):
        LEGACY_ID_TO_CANONICAL["msg_0001"] = make_id(1)  # type: ignore[index]

    regenerated = dict(LEGACY_ID_TO_CANONICAL) | {"msg_0003": str(uuid.uuid4())}
    monkeypatch.setattr(legacy_ids, "LEGACY_ID_TO_CANONICAL", regenerated)
    with pytest.raises(LegacyMappingError, match="has been modified"):
        verify_legacy_mapping()
    with pytest.raises(CatalogueError, match="has been modified"):
        load_seed_catalogue()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"msg_0016": make_id(16)}, "expected 15"),
        ({"msg_0002": "780b4850-6248-4a81-8fe0-7f0762376cfc"}, "same canonical id"),
        ({"msg_0002": "9ABC9607-45EA-4441-9DA2-3EB00AF9D4A3"}, "not a lowercase UUIDv4"),
        ({"msg_0002": str(uuid.uuid1())}, "not a lowercase UUIDv4"),
    ],
)
def test_a_tampered_mapping_is_rejected(
    change: dict[str, str], message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(legacy_ids, "LEGACY_ID_TO_CANONICAL", dict(LEGACY_ID_TO_CANONICAL) | change)
    with pytest.raises(LegacyMappingError, match=message):
        verify_legacy_mapping()


def test_unknown_legacy_ids_are_refused_not_invented() -> None:
    with pytest.raises(LegacyMappingError, match="unknown legacy id"):
        canonical_id_for_legacy("msg_0099")


def test_all_fifteen_seed_entities_keep_their_logical_identity(catalogue: Catalogue) -> None:
    by_id = {game.canonical_game_id: game for game in catalogue.games}
    assert len(by_id) == 15
    for legacy, slug in LOGICAL_IDENTITY.items():
        assert by_id[canonical_id_for_legacy(legacy)].slug == slug

    gta_v = canonical_id_for_legacy("msg_0001")
    relationships = {
        game.slug: [(r.parent_game_id, r.relationship_type) for r in game.relationships]
        for game in catalogue.games
        if game.relationships
    }
    assert relationships == {
        "gta-online": [(gta_v, RelationshipType.ONLINE_COMPONENT)],
        "fivem": [(gta_v, RelationshipType.MOD_ECOSYSTEM)],
        "gta-vi": [(gta_v, RelationshipType.SEQUEL)],
    }


def test_canonical_ids_are_independent_of_slugs_names_and_sources(catalogue: Catalogue) -> None:
    for game in catalogue.games:
        identifier = game.canonical_game_id
        assert game.slug not in identifier and not identifier.startswith("msg_")
        assert all(i.value not in identifier for i in game.source_identifiers)


def test_new_ids_are_uuid4_and_deterministic_under_an_injected_factory() -> None:
    fixed = uuid.UUID("12345678-1234-4234-8234-123456789abc")
    assert new_canonical_id(lambda: fixed) == "12345678-1234-4234-8234-123456789abc"
    assert re.fullmatch(CANONICAL_ID_PATTERN, new_canonical_id())
    assert new_canonical_id() != new_canonical_id()
    with pytest.raises(IdentityError, match="version 4"):
        new_canonical_id(uuid.uuid1)


# --- aliases and lineage -----------------------------------------------------------


def test_legacy_ids_stay_resolvable() -> None:
    ledger = legacy_identity_ledger()
    for legacy, canonical in LEGACY_ID_TO_CANONICAL.items():
        assert ledger.resolve(legacy) == canonical
        assert ledger.resolve(canonical) == canonical  # a canonical id resolves to itself
        assert ledger.aliases_of(canonical) == (legacy,)
    alias = ledger.aliases[0]
    assert alias.kind is AliasKind.LEGACY and alias.decision_ref and alias.reason


def test_alias_resolution_is_point_in_time() -> None:
    ledger = legacy_identity_ledger()
    before = MIGRATED_AT - timedelta(days=1)
    assert ledger.resolve("msg_0001", as_of=before) == "msg_0001"
    assert ledger.resolve("msg_0001", as_of=LATER) == canonical_id_for_legacy("msg_0001")


def test_a_merge_retires_the_loser_and_keeps_its_lineage() -> None:
    ledger = IdentityLedger().merge(B, A, at=LATER, reason="same game", decision_ref="dec-1")

    assert ledger.resolve(B) == A and ledger.resolve(A) == A
    assert ledger.is_retired(B) and not ledger.is_retired(A)
    assert ledger.aliases_of(A) == (B,)
    assert ledger.resolve(B, as_of=LATER - timedelta(days=1)) == B  # not yet merged then
    (record,) = ledger.lineage_of(B)
    assert (record.kind, record.subject_id, record.related_id) == (
        LineageKind.MERGED_INTO,
        B,
        A,
    )
    assert record.decision_ref == "dec-1" and ledger.lineage_of(A) == (record,)


def test_merges_chain_to_one_terminal_id() -> None:
    ledger = IdentityLedger().merge(C, B, at=LATER, reason="dup")
    ledger = ledger.merge(B, A, at=LATER + timedelta(days=1), reason="dup")
    assert ledger.resolve(C) == A and ledger.resolve(B) == A
    assert ledger.aliases_of(A) == (B, C)


def test_merging_cannot_create_a_cycle_or_reuse_a_retired_entity() -> None:
    ledger = IdentityLedger().merge(B, A, at=LATER, reason="dup")
    with pytest.raises(IdentityError, match="current canonical entities"):
        ledger.merge(A, B, at=LATER + timedelta(days=1), reason="reverse")
    with pytest.raises(IdentityError, match="current canonical entities"):
        ledger.merge(B, C, at=LATER + timedelta(days=1), reason="already retired")
    with pytest.raises(IdentityError, match="into itself"):
        ledger.merge(A, A, at=LATER, reason="self")


def test_a_hand_built_alias_cycle_is_rejected() -> None:
    def alias(source: str, target: str) -> IdentityAlias:
        return IdentityAlias(
            alias=source,
            canonical_game_id=target,
            kind=AliasKind.MERGED,
            valid_from=LATER,
            reason="test",
        )

    with pytest.raises(ValueError, match="alias cycle"):
        IdentityLedger(aliases=(alias(A, B), alias(B, C), alias(C, A)))
    with pytest.raises(ValueError, match="two ids at once"):
        IdentityLedger(aliases=(alias(A, B), alias(A, C)))
    with pytest.raises(ValueError, match="alias of itself"):
        alias(A, A)


def test_an_alias_can_end_and_be_replaced_without_overlap() -> None:
    first = IdentityAlias(
        alias="old-id",
        canonical_game_id=A,
        kind=AliasKind.LEGACY,
        valid_from=MIGRATED_AT,
        valid_to=LATER,
        reason="first mapping",
    )
    second = first.model_copy(
        update={"canonical_game_id": B, "valid_from": LATER, "valid_to": None}
    )
    ledger = IdentityLedger(aliases=(first, second))
    assert ledger.resolve("old-id", as_of=MIGRATED_AT) == A
    assert ledger.resolve("old-id") == B
    with pytest.raises(ValueError, match="end after it starts"):
        IdentityAlias.model_validate(first.model_dump() | {"valid_to": MIGRATED_AT})


def test_a_split_creates_a_new_id_with_lineage_and_keeps_the_origin() -> None:
    ledger = IdentityLedger().split(A, D, at=LATER, reason="two games shared one entity")

    assert ledger.resolve(A) == A and ledger.resolve(D) == D
    (record,) = ledger.lineage_of(D)
    assert (record.kind, record.subject_id, record.related_id) == (LineageKind.SPLIT_FROM, D, A)
    merged = ledger.merge(B, A, at=LATER, reason="dup")
    with pytest.raises(IdentityError, match="never been used"):
        merged.split(A, B, at=LATER, reason="reuse a retired id")
    with pytest.raises(IdentityError, match="has been retired"):
        merged.split(B, C, at=LATER + timedelta(days=1), reason="split a retired entity")


# --- versioned source bindings -----------------------------------------------------

STEAM_APP = SourceRecordKey(source="steam_catalogue", source_id="271590")
IGDB_GAME = SourceRecordKey(source="igdb", source_id="1020")


def test_bindings_are_versioned_and_point_in_time() -> None:
    t1, t2 = LATER, LATER + timedelta(days=10)
    history = MappingHistory().bind(STEAM_APP, A, at=t1, decision_ref="dec-1")
    history = history.bind(IGDB_GAME, A, at=t1, decision_ref="dec-2")
    rebound = history.bind(STEAM_APP, B, at=t2, decision_ref="dec-3")

    assert rebound.version == 3
    assert rebound.canonical_for(STEAM_APP) == B
    assert rebound.canonical_for(STEAM_APP, as_of=t1 + timedelta(days=1)) == A
    assert rebound.canonical_for(STEAM_APP, as_of=t1 - timedelta(days=1)) is None
    assert rebound.records_for(A) == (IGDB_GAME,)
    assert set(rebound.records_for(A, as_of=t1)) == {STEAM_APP, IGDB_GAME}
    assert [b.mapping_version for b in rebound.history_of(STEAM_APP)] == [1, 3]
    assert [b.valid_to for b in rebound.history_of(STEAM_APP)] == [t2, None]
    assert history.canonical_for(STEAM_APP) == A  # the earlier history object is untouched


def test_rebinding_to_the_same_entity_changes_nothing() -> None:
    history = MappingHistory().bind(STEAM_APP, A, at=LATER, decision_ref="dec-1")
    assert history.bind(STEAM_APP, A, at=LATER + timedelta(days=1), decision_ref="dec-2") is history


def test_a_record_can_become_unresolved_again_without_losing_history() -> None:
    history = MappingHistory().bind(STEAM_APP, A, at=LATER, decision_ref="dec-1")
    unbound = history.unbind(STEAM_APP, at=LATER + timedelta(days=5))
    assert unbound.canonical_for(STEAM_APP) is None
    assert unbound.canonical_for(STEAM_APP, as_of=LATER + timedelta(days=1)) == A
    assert unbound.records_for(A) == ()


def test_a_record_cannot_be_bound_twice_at_once_or_rebound_into_the_past() -> None:
    history = MappingHistory().bind(STEAM_APP, A, at=LATER, decision_ref="dec-1")
    with pytest.raises(IdentityError, match="must be later"):
        history.bind(STEAM_APP, B, at=LATER - timedelta(days=1), decision_ref="dec-2")
    duplicate = history.bindings[0].model_copy(update={"canonical_game_id": B})
    with pytest.raises(ValueError, match="bound twice at once"):
        MappingHistory(bindings=(history.bindings[0], duplicate))


def test_one_entity_may_hold_several_records_from_the_same_source() -> None:
    other_app = SourceRecordKey(source="steam_catalogue", source_id="3240220")
    history = MappingHistory().bind(STEAM_APP, A, at=LATER, decision_ref="dec-1")
    history = history.bind(other_app, A, at=LATER, decision_ref="dec-2")
    assert set(history.records_for(A)) == {STEAM_APP, other_app}


# --- history is never orphaned -----------------------------------------------------


def test_observations_stored_under_a_legacy_id_are_read_under_its_uuid(tmp_path: Path) -> None:
    canonical = canonical_id_for_legacy("msg_0001")
    current = LocalObservationStore(
        tmp_path, lambda source: WIKIMEDIA_PAGEVIEWS_POLICY, legacy_identity_ledger()
    )
    current.write(series_of(spiking()))
    written = next(tmp_path.rglob("*.jsonl"))
    legacy_file = written.with_name("msg_0001.jsonl")
    legacy_file.write_text(written.read_text().replace(canonical, "msg_0001"))
    written.unlink()  # only the pre-migration file is left on disk

    window = series_of(spiking())
    as_of = datetime(2026, 12, 1, tzinfo=UTC)
    for identifier in (canonical, "msg_0001"):
        restored = current.read(identifier, V0_INPUT_SIGNAL, _window(window), as_of=as_of)
        assert restored.canonical_game_id == canonical
        assert restored.values() == window.values()

    without_ledger = LocalObservationStore(tmp_path, lambda source: WIKIMEDIA_PAGEVIEWS_POLICY)
    orphaned = without_ledger.read(canonical, V0_INPUT_SIGNAL, _window(window), as_of=as_of)
    assert set(orphaned.values()) == {None}  # the ledger is what keeps history reachable
    assert as_of > RUN_TIME and as_of.date() > RECORDED_END


def _window(series: TimeSeries) -> TimeWindow:
    return TimeWindow(start=series.window_start, end=series.window_end)
