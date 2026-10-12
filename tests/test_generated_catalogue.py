"""The generated catalogue: bootstrap identities preserved, new entities admitted safely."""

import re
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from metaspike import cli
from metaspike.catalogue.generate import BOOTSTRAP_SOURCE, CatalogueState, generate_catalogue
from metaspike.catalogue.legacy_ids import LEGACY_ID_TO_CANONICAL, canonical_id_for_legacy
from metaspike.catalogue.overrides import NO_OVERRIDES
from metaspike.catalogue.seed import (
    BOOTSTRAP_ANCHORS,
    BOOTSTRAP_GAMES,
    bootstrap_catalogue,
    load_seed_catalogue,
)
from metaspike.domain.games import CANONICAL_ID_PATTERN, LifecycleState, RelationshipType
from metaspike.domain.identity import MappingHistory, SourceRecordKey
from metaspike.domain.source_records import ExternalId, ProductType, SourceRecord
from metaspike.domain.timeseries import TimeWindow
from metaspike.lifecycle.rules import RULE_VERSION, LifecycleFlag
from metaspike.resolution.models import DecisionKind
from metaspike.resolution.recorded import load_recorded_catalogue
from metaspike.resolution.resolver import RESOLVER_VERSION
from metaspike.scoring.v0 import V0_INPUT_SIGNAL
from metaspike.sources.wikimedia import WIKIMEDIA_PAGEVIEWS_POLICY
from metaspike.storage.local import LocalObservationStore
from tests.conftest import make_id, series_of, spiking
from tests.test_identity import LOGICAL_IDENTITY

CATALOGUE_FIXTURES = Path(__file__).parent / "fixtures" / "catalogue"
AS_OF = datetime(2026, 10, 5, tzinfo=UTC)
SEED_IDS = set(LEGACY_ID_TO_CANONICAL.values())
GTA_V, GTA_ONLINE, FIVEM, GTA_VI = (canonical_id_for_legacy(f"msg_000{n}") for n in (1, 2, 3, 4))


def igdb(source_id: int) -> SourceRecordKey:
    return SourceRecordKey(source="igdb", source_id=str(source_id))


def counting_ids() -> "tuple[list[uuid.UUID], object]":
    issued: list[uuid.UUID] = []

    def factory() -> uuid.UUID:
        issued.append(uuid.UUID(int=10_000 + len(issued), version=4))
        return issued[-1]

    return issued, factory


@pytest.fixture(scope="module")
def recorded() -> list[SourceRecord]:
    return list(load_recorded_catalogue(CATALOGUE_FIXTURES).values())


@pytest.fixture(scope="module")
def state(recorded: list[SourceRecord]) -> CatalogueState:
    return generate_catalogue(recorded, as_of=AS_OF)


# --- bootstrap only: the runtime catalogue before any source record --------------------


def test_the_runtime_catalogue_is_generated_and_matches_the_bootstrap_identities() -> None:
    generated = load_seed_catalogue()
    assert generated == generate_catalogue().catalogue  # produced by the generator
    assert generated == bootstrap_catalogue()  # and identical in every field
    assert len(generated.games) == 15 == len(BOOTSTRAP_GAMES)
    by_id = {game.canonical_game_id: game for game in generated.games}
    for legacy, slug in LOGICAL_IDENTITY.items():
        assert by_id[canonical_id_for_legacy(legacy)].slug == slug


def test_bootstrap_generation_logs_nothing_and_quarantines_nothing() -> None:
    empty = generate_catalogue()
    assert empty.decisions.decisions == () and empty.quarantine.entries == ()
    assert empty.mapping.bindings == () and empty.related_products == ()
    assert (empty.resolver_version, empty.lifecycle_rule_version) == (
        RESOLVER_VERSION,
        RULE_VERSION,
    )
    for legacy, canonical in LEGACY_ID_TO_CANONICAL.items():
        assert empty.identity.resolve(legacy) == canonical


# --- with recorded source records ------------------------------------------------------


def test_all_fifteen_seed_entities_keep_their_identity(state: CatalogueState) -> None:
    by_id = {game.canonical_game_id: game for game in state.catalogue.games}
    seeds = {game.canonical_game_id: game for game in bootstrap_catalogue().games}
    assert set(by_id) >= SEED_IDS
    for identifier, seed in seeds.items():
        generated = by_id[identifier]
        assert (generated.slug, generated.name, generated.aliases) == (
            seed.slug,
            seed.name,
            seed.aliases,
        )
        assert generated.source_identifiers == seed.source_identifiers
        assert generated.relationships == seed.relationships
        assert generated.lifecycle_state is seed.lifecycle_state, seed.slug
    assert [g.canonical_game_id for g in state.catalogue.games[:15]] == list(seeds)


def test_seed_entities_are_bound_to_their_recorded_source_records(state: CatalogueState) -> None:
    for slug, key in BOOTSTRAP_ANCHORS.items():
        bound = state.mapping.canonical_for(key)
        game = next(g for g in state.catalogue.games if g.slug == slug)
        if bound is not None:  # only anchors whose record is in the recorded fixtures
            assert bound == game.canonical_game_id
    assert state.mapping.canonical_for(igdb(1020)) == GTA_V
    assert state.mapping.canonical_for(igdb(134710)) == GTA_ONLINE
    assert state.mapping.canonical_for(igdb(52189)) == GTA_VI
    rust = canonical_id_for_legacy("msg_0015")
    assert set(state.mapping.records_for(rust)) == {
        igdb(3277),
        SourceRecordKey(source="steam_catalogue", source_id="252490"),
    }
    anchor = state.decisions.latest(igdb(1020))
    assert anchor is not None and anchor.decision.override_ref == "bootstrap:anchor:gta-v"


def test_gta_v_gta_online_and_fivem_stay_distinct_and_correctly_related(
    state: CatalogueState,
) -> None:
    by_id = {game.canonical_game_id: game for game in state.catalogue.games}
    assert len({GTA_V, GTA_ONLINE, FIVEM}) == 3
    assert state.mapping.canonical_for(igdb(146553)) == FIVEM  # admitted by the override

    assert by_id[GTA_V].relationships == ()
    assert [(r.parent_game_id, r.relationship_type) for r in by_id[FIVEM].relationships] == [
        (GTA_V, RelationshipType.MOD_ECOSYSTEM)
    ]
    assert [(r.parent_game_id, r.relationship_type) for r in by_id[GTA_ONLINE].relationships] == [
        (GTA_V, RelationshipType.ONLINE_COMPONENT)
    ]
    fivem_decision = state.decisions.latest(igdb(146553))
    assert fivem_decision is not None
    assert fivem_decision.decision.kind is DecisionKind.MANUAL_OVERRIDE
    assert fivem_decision.decision.override_ref == "override:fivem-mod-ecosystem"
    assert sum(game.name == "Grand Theft Auto V" for game in state.catalogue.games) == 1
    assert sum(game.name == "FiveM" for game in state.catalogue.games) == 1


def test_without_the_override_fivem_is_a_related_product_and_no_other_mod_is_admitted(
    recorded: list[SourceRecord],
) -> None:
    without = generate_catalogue(recorded, as_of=AS_OF, overrides=NO_OVERRIDES)
    assert without.mapping.canonical_for(igdb(146553)) is None
    assert igdb(146553) in {product.record for product in without.related_products}
    assert FIVEM in {g.canonical_game_id for g in without.catalogue.games}  # bootstrap entity stays

    with_override = generate_catalogue(recorded, as_of=AS_OF)
    mods = [r for r in recorded if r.product_type is ProductType.MOD]
    admitted = [r for r in mods if with_override.mapping.canonical_for(r.key) is not None]
    assert [r.title for r in admitted] == ["FiveM"]


def test_new_entities_get_fresh_uuid4_ids_and_unique_slugs(state: CatalogueState) -> None:
    created = [g for g in state.catalogue.games if g.canonical_game_id not in SEED_IDS]
    assert len(created) == 219
    for game in created:
        assert re.fullmatch(CANONICAL_ID_PATTERN, game.canonical_game_id)
        assert game.slug and game.source_identifiers == ()
    assert len({g.canonical_game_id for g in state.catalogue.games}) == 234
    assert len({g.slug for g in state.catalogue.games}) == 234
    portals = [g for g in state.catalogue.games if g.name == "Portal"]
    assert len(portals) == 3  # three different games called Portal, three entities
    assert len({g.slug for g in portals}) == 3 and all(g.slug.startswith("portal") for g in portals)


def test_a_canonical_id_is_never_derived_from_a_title_or_a_source_id(
    recorded: list[SourceRecord],
) -> None:
    issued, factory = counting_ids()
    generated = generate_catalogue(recorded, as_of=AS_OF, id_factory=factory)  # type: ignore[arg-type]
    created = {
        g.canonical_game_id
        for g in generated.catalogue.games
        if g.canonical_game_id not in SEED_IDS
    }
    assert created == {str(value) for value in issued}  # every id came from the factory
    assert len(issued) == 219  # one per new entity, none for matches or quarantine
    titles = {g.canonical_game_id: g.slug for g in generated.catalogue.games}
    assert all(slug not in identifier for identifier, slug in titles.items())


def test_derived_works_are_separate_entities_related_to_their_originals(
    state: CatalogueState,
) -> None:
    kinds = Counter(
        r.relationship_type
        for g in state.catalogue.games
        if g.canonical_game_id not in SEED_IDS
        for r in g.relationships
    )
    assert kinds[RelationshipType.REMASTER] == 7  # includes the 2023 Quake II remaster
    assert kinds[RelationshipType.STANDALONE_EXPANSION] == 4
    assert kinds[RelationshipType.REMAKE] >= 3
    ids = {g.canonical_game_id for g in state.catalogue.games}
    for game in state.catalogue.games:
        assert all(r.parent_game_id in ids for r in game.relationships)


def test_bundles_and_dlc_are_related_products_and_never_entities(state: CatalogueState) -> None:
    products = {product.record: product for product in state.related_products}
    assert Counter(p.product_type for p in products.values()) == {
        ProductType.DLC: 8,
        ProductType.BUNDLE: 5,
        ProductType.PACK: 3,
        ProductType.UPDATE: 3,
        ProductType.EPISODE: 2,
        ProductType.EXPANSION: 1,
        ProductType.SEASON: 1,
    }
    for key in products:
        assert state.mapping.canonical_for(key) is None  # no canonical id, ever

    gta_bundle = products[igdb(239064)]
    assert gta_bundle.relationship_type is RelationshipType.PACKAGING_VARIANT
    assert gta_bundle.entity_ids == (GTA_V,)
    enhanced = products[igdb(334647)]  # 'Grand Theft Auto V Enhanced': no recorded link
    assert enhanced.relationship_type is None and enhanced.entity_ids == ()
    relations = Counter(
        p.relationship_type for p in products.values() if p.product_type is ProductType.BUNDLE
    )
    assert relations[RelationshipType.PACKAGING_VARIANT] == 3
    assert relations[RelationshipType.COLLECTION] == 1


def test_quarantined_records_have_no_canonical_id_and_are_not_in_the_catalogue(
    state: CatalogueState,
) -> None:
    assert len(state.quarantine.entries) == 44
    names = {game.name for game in state.catalogue.games}
    for entry in state.quarantine.entries:
        assert state.mapping.canonical_for(entry.record.key) is None
        assert entry.record.key.source != BOOTSTRAP_SOURCE
        if entry.record.key.source == "steam_catalogue":
            assert entry.record.title not in names or entry.candidates
    metrics = state.quarantine.metrics(AS_OF + timedelta(days=10))
    assert metrics.total == 44 and metrics.oldest_age_days == 10.0
    assert set(metrics.by_source) == {"igdb", "steam_catalogue"}
    assert metrics.by_reason["title_only"] > 0
    enhanced_app = SourceRecordKey(source="steam_catalogue", source_id="3240220")
    assert state.quarantine.get(enhanced_app) is not None  # not merged into GTA V


def test_every_decision_is_logged_and_explains_itself(
    state: CatalogueState, recorded: list[SourceRecord]
) -> None:
    assert len(recorded) == 394
    latest = [state.decisions.latest(record.key) for record in recorded]
    assert all(decision is not None for decision in latest)  # every record was judged
    kinds = Counter(decision.decision.kind for decision in latest if decision is not None)
    assert kinds[DecisionKind.NEW_ENTITY] == 219 and kinds[DecisionKind.NOT_AN_ENTITY] == 23
    assert kinds[DecisionKind.QUARANTINE] == len(state.quarantine.entries) == 44
    # Six records were judged twice within the run, once their evidence had been resolved.
    assert len(state.decisions.decisions) == 400
    assert {d.decision.path for d in state.decisions.decisions if d.decision.path} == {"A"}
    for logged in state.decisions.decisions:
        assert logged.decision.explanation and logged.decision.resolver_version == RESOLVER_VERSION
        bound = state.mapping.canonical_for(logged.record)
        if logged.decision.kind in (DecisionKind.AUTO_MATCH, DecisionKind.NEW_ENTITY):
            assert logged.canonical_game_id == bound is not None


# --- stability --------------------------------------------------------------------------


def test_one_run_reaches_a_fixed_point_and_later_runs_change_nothing(
    recorded: list[SourceRecord],
) -> None:
    first = generate_catalogue(recorded, as_of=AS_OF)
    second = generate_catalogue(recorded, as_of=AS_OF + timedelta(days=1), previous=first)
    third = generate_catalogue(recorded, as_of=AS_OF + timedelta(days=2), previous=second)

    for later in (second, third):
        assert later.catalogue == first.catalogue
        assert later.mapping == first.mapping
        assert later.decisions == first.decisions  # nothing was re-judged
        assert later.quarantine == first.quarantine
        assert later.related_products == first.related_products
        assert later.identity == first.identity


def test_generation_is_deterministic_given_the_same_id_factory(
    recorded: list[SourceRecord],
) -> None:
    _, first_factory = counting_ids()
    _, second_factory = counting_ids()
    first = generate_catalogue(recorded, as_of=AS_OF, id_factory=first_factory)  # type: ignore[arg-type]
    second = generate_catalogue(
        list(reversed(recorded)),
        as_of=AS_OF,
        id_factory=second_factory,  # type: ignore[arg-type]
    )
    assert first == second  # input order does not matter


def test_a_quarantined_record_resolves_itself_when_linking_evidence_arrives(
    recorded: list[SourceRecord],
) -> None:
    first = generate_catalogue(recorded, as_of=AS_OF)
    app = SourceRecordKey(source="steam_catalogue", source_id="38480")  # 'Earthworm Jim'
    waiting = first.quarantine.get(app)
    assert waiting is not None and waiting.reason.value == "title_only"
    entity = waiting.candidates[0].entity_ref
    game_record = next(k for k in first.mapping.records_for(entity) if k.source == "igdb")

    updated = []
    for record in recorded:
        if record.key == game_record:  # IGDB starts listing the Steam app id on the game
            link = ExternalId(source="steam", value="38480")
            record = record.model_copy(update={"external_ids": (*record.external_ids, link)})
        updated.append(record)
    later = AS_OF + timedelta(days=20)
    second = generate_catalogue(updated, as_of=later, previous=first)

    assert second.quarantine.get(app) is None
    assert second.mapping.canonical_for(app) == entity
    history = second.decisions.for_record(app)
    assert [d.decision.kind for d in history] == [DecisionKind.QUARANTINE, DecisionKind.AUTO_MATCH]
    assert second.catalogue == first.catalogue  # no new entity was needed
    assert len(second.quarantine.entries) == len(first.quarantine.entries) - 1


# --- lifecycle in the generated catalogue ------------------------------------------------


def test_lifecycle_is_derived_versioned_and_changes_automatically_with_time(
    recorded: list[SourceRecord],
) -> None:
    before = generate_catalogue(recorded, as_of=AS_OF)
    history = before.lifecycle_of(GTA_VI)
    assert history.current is not None and history.current.state is LifecycleState.PRE_RELEASE
    assert history.current.rule_id == "first_release_in_future"
    assert history.current.rule_version == RULE_VERSION
    assert history.records[0].flags == (LifecycleFlag.BOOTSTRAP,)
    release_day = history.current.first_available
    assert release_day is not None and release_day > AS_OF.date()

    launch = datetime.combine(release_day, datetime.min.time(), tzinfo=UTC) + timedelta(days=3)
    during = generate_catalogue(recorded, as_of=launch, previous=before)
    after = generate_catalogue(recorded, as_of=launch + timedelta(days=40), previous=during)
    states = {s.catalogue.get(GTA_VI).lifecycle_state for s in (before, during, after)}
    assert during.catalogue.get(GTA_VI).lifecycle_state is LifecycleState.LAUNCH_WINDOW
    assert after.catalogue.get(GTA_VI).lifecycle_state is LifecycleState.RELEASED
    assert len(states) == 3
    final = after.lifecycle_of(GTA_VI)
    assert final.state_at(AS_OF.date()) is LifecycleState.PRE_RELEASE  # history is kept
    assert final.state_at(launch.date()) is LifecycleState.LAUNCH_WINDOW


def test_seed_lifecycle_overrides_are_labelled(state: CatalogueState) -> None:
    gta_v = state.lifecycle_of(GTA_V).current
    assert gta_v is not None and gta_v.state is LifecycleState.EVERGREEN
    assert gta_v.rule_id == "labelled_override"
    assert LifecycleFlag.LABELLED_OVERRIDE in gta_v.flags
    elden_ring = state.lifecycle_of(canonical_id_for_legacy("msg_0012")).current
    assert elden_ring is not None and elden_ring.state is LifecycleState.RELEASED
    assert elden_ring.rule_id == "released_default" and elden_ring.flags == ()


# --- a source id whose meaning changes over time -----------------------------------------


def test_one_source_id_can_mean_entity_a_then_entity_b_without_rewriting_history(
    tmp_path: Path,
) -> None:
    """Synthetic. No real transition date is claimed for any real app id."""
    entity_a, entity_b = make_id(501), make_id(502)
    app = SourceRecordKey(source="steam_catalogue", source_id="424242")
    before = datetime(2023, 1, 1, tzinfo=UTC)
    boundary = datetime(2024, 6, 1, tzinfo=UTC)
    after = datetime(2026, 12, 1, tzinfo=UTC)

    history = MappingHistory().bind(app, entity_a, at=before, decision_ref="decision:1")
    history = history.bind(app, entity_b, at=boundary, decision_ref="decision:2")

    assert history.canonical_for(app, as_of=boundary - timedelta(days=1)) == entity_a
    assert history.canonical_for(app, as_of=boundary) == entity_b
    assert history.canonical_for(app) == entity_b
    versions = history.history_of(app)
    assert [(b.canonical_game_id, b.valid_to) for b in versions] == [
        (entity_a, boundary),
        (entity_b, None),
    ]
    assert [b.decision_ref for b in versions] == ["decision:1", "decision:2"]

    store = LocalObservationStore(tmp_path, lambda source: WIKIMEDIA_PAGEVIEWS_POLICY)
    old_series = series_of(spiking()).model_copy(update={"canonical_game_id": entity_a})
    old_series = type(old_series).model_validate(
        old_series.model_dump()
        | {"points": [p.model_dump() | {"canonical_game_id": entity_a} for p in old_series.points]}
    )
    store.write(old_series)
    stored = next(tmp_path.rglob("*.jsonl"))
    bytes_before = stored.read_bytes()

    # The rebinding happened above; nothing stored under entity A was touched.
    assert stored.read_bytes() == bytes_before and stored.stem == entity_a
    assert history.records_for(entity_a, as_of=before) == (app,)
    assert history.records_for(entity_a) == () and history.records_for(entity_b) == (app,)
    window = TimeWindow(start=old_series.window_start, end=old_series.window_end)
    kept = store.read(entity_a, V0_INPUT_SIGNAL, window, as_of=after)
    assert kept.values() == old_series.values()
    assert set(store.read(entity_b, V0_INPUT_SIGNAL, window, as_of=after).values()) == {None}


# --- command line ------------------------------------------------------------------------


def test_cli_generates_a_catalogue_state_and_reuses_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state_file = tmp_path / "catalogue" / "state.json"
    arguments = [
        "generate-catalogue",
        "--recorded",
        str(CATALOGUE_FIXTURES),
        "--state",
        str(state_file),
        "--stage",
        "private_development",
    ]
    # The fixtures were fetched on 5 October at 02:00 UTC, so the state is dated after.
    assert cli.main([*arguments, "--as-of", "2026-10-06"]) == cli.EXIT_OK
    assert "234 entities (219 new this run)" in capsys.readouterr().out
    assert cli.main([*arguments, "--as-of", "2026-10-07"]) == cli.EXIT_OK
    assert "234 entities (0 new this run)" in capsys.readouterr().out

    saved = CatalogueState.model_validate_json(state_file.read_bytes())
    assert len(saved.catalogue.games) == 234 and saved.overrides_version == 1
    assert saved.identity.resolve("msg_0003") == FIVEM
