"""Offline catalogue expansion: the broad evaluation path, its policy gate, identity
safety, monitoring readiness and scale.

Everything is offline. Two kinds of input are used and kept apart:

- RECORDED: `tests/fixtures/catalogue`, rows as IGDB and Steam returned them.
- SYNTHETIC: invented records, in this file or from `catalogue_samples`, used where a
  situation has to be constructed or where size is the point. Never mixed into the
  recorded results.
"""

import hashlib
import inspect
import json
import socket
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from metaspike import cli
from metaspike.catalogue import coverage as coverage_module
from metaspike.catalogue import evaluation as evaluation_module
from metaspike.catalogue import persistence
from metaspike.catalogue.coverage import monitoring_readiness, summarise_coverage
from metaspike.catalogue.eligibility import SourceEligibility, steam_eligibility
from metaspike.catalogue.evaluation import (
    EVALUATION_SCOPE,
    BroadEvaluation,
    evaluate_broad_catalogue,
    require_catalogue_sources,
)
from metaspike.catalogue.generate import BOOTSTRAP_SOURCE, generate_catalogue
from metaspike.catalogue.incremental import generate_catalogue_blocked
from metaspike.catalogue.legacy_ids import LEGACY_ID_TO_CANONICAL
from metaspike.catalogue.persistence import (
    BroadDiscoveryDisabled,
    CatalogueScope,
    directly_reachable,
    in_scope,
)
from metaspike.catalogue.seed import bootstrap_catalogue
from metaspike.domain.games import RelationshipType
from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.source_policy import DeploymentStage, Permission, SourcePolicy
from metaspike.domain.source_records import (
    Admissibility,
    DatePrecision,
    ExternalId,
    ProductType,
    ReleaseDate,
    SourceRecord,
)
from metaspike.resolution.evaluate import evaluate
from metaspike.resolution.golden import load_golden_set
from metaspike.resolution.models import DecisionKind
from metaspike.resolution.recorded import load_recorded_catalogue
from metaspike.resolution.resolver import RESOLVER_VERSION
from metaspike.sources.catalogue_sources import CATALOGUE_SOURCE_POLICIES
from metaspike.sources.igdb import IGDB_POLICY
from metaspike.sources.registry import SourcePolicyError
from metaspike.sources.steam_catalogue import STEAM_CATALOGUE_POLICY
from metaspike.sources.steam_players import STEAM_CURRENT_PLAYERS_POLICY
from metaspike.sources.wikimedia import SOURCE_ID as WIKIMEDIA
from tests.catalogue_samples import stratified_sample
from tests.catalogue_scale import CATALOGUE_POLICIES, counting_ids, evaluate_synthetic

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
AS_OF = datetime(2026, 10, 5, tzinfo=UTC)
PRIVATE = DeploymentStage.PRIVATE_DEVELOPMENT
FETCHED = datetime(2026, 10, 5, 2, 0, tzinfo=UTC)
STEAM = "steam_current_players"
# sha256 of tests/fixtures/resolution_golden.json: the frozen identity labels.
GOLDEN_FILE_SHA256 = "e7b47303409cd6f03d6741017902738d638568fee9c4a167ae195b8b267853e3"


def recorded(name: str = "catalogue") -> list[SourceRecord]:
    return list(load_recorded_catalogue(FIXTURES / name).values())


def run(records: list[SourceRecord], **options: object) -> BroadEvaluation:
    settings: dict[str, object] = {
        "policies": CATALOGUE_POLICIES,
        "stage": PRIVATE,
        "as_of": AS_OF,
        "id_factory": counting_ids(),
    }
    settings.update(options)
    return evaluate_broad_catalogue(records, **settings)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def recorded_evaluation() -> BroadEvaluation:
    return run(recorded())


# SYNTHETIC records, for situations that have to be built. Every title is invented.


def igdb_game(
    identifier: str,
    title: str,
    *,
    steam: str | None = None,
    kind: ProductType = ProductType.MAIN_GAME,
    parent: SourceRecord | None = None,
    year: int = 2019,
    studio: str = "Synthetic Studio",
) -> SourceRecord:
    return SourceRecord(
        key=SourceRecordKey(source="igdb", source_id=identifier),
        title=title,
        product_type=kind,
        source_category=kind.value,
        release_dates=(ReleaseDate(starts_on=date(year, 3, 1), precision=DatePrecision.DAY),),
        developers=(studio,),
        external_ids=() if steam is None else (ExternalId(source="steam", value=steam),),
        parent=None if parent is None else parent.key,
        fetched_at=FETCHED,
    )


def steam_app(identifier: str, title: str) -> SourceRecord:
    return SourceRecord(
        key=SourceRecordKey(source="steam_catalogue", source_id=identifier),
        title=title,
        product_type=ProductType.UNKNOWN,
        source_category="games",
        fetched_at=FETCHED,
    )


# --- the gate stays closed, and the evaluation is not a way round it -------------------


def test_production_broad_discovery_is_still_off_and_the_evaluation_does_not_use_it() -> None:
    assert persistence.BROAD_DISCOVERY_ENABLED is False
    with pytest.raises(BroadDiscoveryDisabled):
        in_scope(recorded(), CatalogueScope.BROAD)
    # The production entry point still defaults to the narrow scope.
    scope = inspect.signature(persistence.advance_catalogue).parameters["scope"]
    assert scope.default is CatalogueScope.BOOTSTRAP_REACHABLE
    assert EVALUATION_SCOPE == "offline_broad_evaluation"
    # The evaluation has no way to fetch, store or move a pointer: none of it is imported.
    source = Path(evaluation_module.__file__).read_text()
    code = source.split('"""', 2)[2]
    for forbidden in (
        "CatalogueRepository",
        "advance_catalogue",
        "ObjectStore",
        "LandingWriter",
        "httpx",
        "LimitedHttpClient",
        "Adapter",
        "CatalogueScope",
        "in_scope",
        "BROAD_DISCOVERY_ENABLED",
        "POINTER",
        "open(",
        "write_text",
    ):
        assert forbidden not in code, forbidden
    # It is not a second resolver: it calls the blocked engine and holds no identity rule.
    assert code.count("generate_catalogue_blocked(") == 2
    for rule in ("resolve(", "normalize_title", "AUTO_MATCH", "title ==", "fuzz", "similar"):
        assert rule not in code, rule
    parameters = inspect.signature(evaluate_broad_catalogue).parameters
    # Neither the stage nor the policies has a default that grants anything.
    assert parameters["stage"].default is inspect.Parameter.empty
    assert parameters["policies"].default is inspect.Parameter.empty
    assert not {"repository", "store", "client", "adapter", "scope"} & set(parameters)


def test_the_evaluation_fails_closed_on_source_policy() -> None:
    records = recorded()
    refusals: list[tuple[dict[str, object], str]] = [
        ({"policies": None}, "no source policy"),
        ({"policies": {}}, "no source policy"),
        ({"policies": {"igdb": IGDB_POLICY}}, "steam_catalogue: no source policy"),
        ({"policies": {"steam_catalogue": STEAM_CATALOGUE_POLICY}}, "igdb: no source policy"),
        ({"stage": None}, "must state the deployment stage"),
        # A policy filed under another source's name does not cover it.
        (
            {"policies": {"igdb": IGDB_POLICY, "steam_catalogue": STEAM_CURRENT_PLAYERS_POLICY}},
            "is for 'steam_current_players'",
        ),
        # The stated stage is enforced: Steam's catalogue is cleared for nothing beyond
        # private development, and IGDB for nothing public.
        ({"stage": DeploymentStage.PRIVATE_COLLECTION}, "not cleared for private_collection"),
        ({"stage": DeploymentStage.PUBLIC_LAUNCH}, "not cleared for public_launch"),
        ({"stage": DeploymentStage.COMMERCIAL_PRODUCTION}, "not cleared for commercial_production"),
    ]
    for options, message in refusals:
        with pytest.raises(SourcePolicyError, match=message):
            run(records, **options)
    # IGDB alone is cleared for private collection, and is evaluated there.
    igdb_only = [record for record in records if record.key.source == "igdb"]
    collection = run(igdb_only, stage=DeploymentStage.PRIVATE_COLLECTION)
    assert collection.summary.stage is DeploymentStage.PRIVATE_COLLECTION
    assert [use.source for use in collection.summary.sources] == ["igdb"]
    with pytest.raises(SourcePolicyError, match="not cleared for public_launch"):
        run(igdb_only, stage=DeploymentStage.PUBLIC_LAUNCH)
    # An unsupported source is refused, whatever it calls itself.
    stranger = steam_app("1", "Synthetic Stranger").model_copy(
        update={"key": SourceRecordKey(source="somewhere_else", source_id="1")}
    )
    with pytest.raises(SourcePolicyError, match="somewhere_else: no source policy"):
        run([*records, stranger])
    # A record cannot pose as bootstrap evidence.
    forged = stranger.model_copy(
        update={"key": SourceRecordKey(source=BOOTSTRAP_SOURCE, source_id="gta-v")}
    )
    with pytest.raises(SourcePolicyError, match="not source data"):
        run([forged])
    # A prohibition or an unanswered storage permission stops it at every stage.
    for change in (
        {"historical_storage_allowed": Permission.UNKNOWN},
        {"historical_storage_allowed": Permission.PROHIBITED},
    ):
        altered: dict[str, SourcePolicy] = {
            **CATALOGUE_POLICIES,
            "igdb": IGDB_POLICY.model_copy(update=change),
        }
        with pytest.raises(SourcePolicyError, match="storing the source's metadata"):
            run(records, policies=altered)
    with pytest.raises(ValueError, match="more than once"):
        run([*records, records[0]])
    # The real policies were not loosened to make any of this pass.
    assert [c.cleared for c in STEAM_CATALOGUE_POLICY.stage_clearance] == [
        True,
        False,
        False,
        False,
    ]
    assert [c.cleared for c in IGDB_POLICY.stage_clearance] == [True, True, False, False]
    assert require_catalogue_sources([], None, PRIVATE) == {}
    assert dict(CATALOGUE_SOURCE_POLICIES) == dict(CATALOGUE_POLICIES)


# --- what the recorded inputs come to --------------------------------------------------


def test_the_recorded_evaluation_reports_each_kind_of_record_separately(
    recorded_evaluation: BroadEvaluation,
) -> None:
    summary = recorded_evaluation.summary
    assert summary.scope == "offline_broad_evaluation" and summary.stage is PRIVATE
    assert summary.resolver_version == RESOLVER_VERSION
    assert [(use.source, use.records) for use in summary.sources] == [
        ("igdb", 266),
        ("steam_catalogue", 128),
    ]
    assert summary.sources[1].cleared_stages == ("private_development",)
    assert summary.source_records == 394
    # Production's scope sees 17 of them; the other 377 are only seen here.
    assert (summary.records_in_production_scope, summary.records_only_broad_sees) == (17, 377)
    assert summary.bootstrap_entities == 15 == summary.entities_in_production_scope
    assert summary.canonical_entities == 234
    assert summary.entities_admitted_by_broad == 219
    # Every record is in exactly one of three states.
    assert (summary.bound_records, summary.quarantined_records, summary.related_products) == (
        327,
        44,
        23,
    )
    assert summary.bound_records + summary.quarantined_records + summary.related_products == 394
    assert summary.records_admitted_as_new_entities == 219
    assert summary.records_resolved_to_existing_entities == 108
    assert summary.quarantine_reasons == {
        "conflicting_evidence": 12,
        "id_link_title_mismatch": 8,
        "multiple_candidates": 1,
        "title_only": 21,
        "undetermined_product_type": 2,
    }
    assert sum(summary.quarantine_reasons.values()) == 44
    assert summary.related_product_types == {
        "bundle": 5,
        "dlc": 8,
        "episode": 2,
        "expansion": 1,
        "pack": 3,
        "season": 1,
        "update": 3,
    }
    # The summary is counts and versions only: nothing a source said is in it.
    dumped = summary.model_dump_json()
    for record in recorded()[:60]:
        assert record.title not in dumped or len(record.title) < 4
    # Deterministic: the same records give the same summary, timings aside.
    again = run(recorded()).summary
    volatile = {"scoped_work": {"seconds"}, "broad_work": {"seconds"}}
    assert again.model_dump(exclude=volatile) == summary.model_dump(exclude=volatile)


def test_the_two_step_evaluation_gives_the_catalogue_a_single_broad_pass_would(
    recorded_evaluation: BroadEvaluation,
) -> None:
    """Resolving production's scope first and the rest after changes no decision."""
    records = recorded()
    single = generate_catalogue_blocked(records, as_of=AS_OF, id_factory=counting_ids())
    state = recorded_evaluation.state

    def shape(target: object) -> tuple[object, ...]:
        s = target
        bound = {
            (b.key.source, b.key.source_id): s.catalogue.get(b.canonical_game_id).slug  # type: ignore[attr-defined]
            for b in s.mapping.bindings  # type: ignore[attr-defined]
            if b.valid_to is None
        }
        return (
            sorted(game.slug for game in s.catalogue.games),  # type: ignore[attr-defined]
            bound,
            sorted((e.record.key.source_id, e.reason.value) for e in s.quarantine.entries),  # type: ignore[attr-defined]
            sorted(p.record.source_id for p in s.related_products),  # type: ignore[attr-defined]
        )

    assert shape(state) == shape(single)
    # And the reference, all-pairs engine agrees with both.
    reference = generate_catalogue(records, as_of=AS_OF, id_factory=counting_ids())
    assert shape(reference) == shape(single)
    # G. Asked every question both ways, the blocked engine never differs.
    checked = run(records, cross_check=True)
    assert checked.summary.broad_work.cross_checked == checked.summary.broad_work.resolver_calls
    assert checked.summary.broad_work.cross_checked > 300
    assert shape(checked.state) == shape(state)
    # The scoped catalogue is what production's path builds from the same records.
    scoped = generate_catalogue_blocked(
        directly_reachable(records), as_of=AS_OF, id_factory=counting_ids()
    )
    assert shape(recorded_evaluation.scoped) == shape(scoped)


# --- identity safety -------------------------------------------------------------------


def test_bootstrap_entities_keep_their_canonical_ids_and_relationships(
    recorded_evaluation: BroadEvaluation,
) -> None:
    before = {game.canonical_game_id: game for game in bootstrap_catalogue().games}
    after = {game.canonical_game_id: game for game in recorded_evaluation.state.catalogue.games}
    # A. Every bootstrap id is still there, under the same slug and name.
    assert set(before) <= set(after) and len(before) == 15
    assert set(LEGACY_ID_TO_CANONICAL.values()) == set(before)
    for identifier, game in before.items():
        kept = after[identifier]
        assert (kept.slug, kept.name) == (game.slug, game.name)
        # I. Source identifiers are exactly what the catalogue already held: none inferred.
        assert kept.source_identifiers == game.source_identifiers
        # J. Typed parent relationships are intact.
        assert set(game.relationships) <= set(kept.relationships)
    online = next(game for game in after.values() if game.slug == "gta-online")
    assert [(r.relationship_type, after[r.parent_game_id].slug) for r in online.relationships] == [
        (RelationshipType.ONLINE_COMPONENT, "gta-v")
    ]
    fivem = next(game for game in after.values() if game.slug == "fivem")
    assert fivem.relationships[0].relationship_type is RelationshipType.MOD_ECOSYSTEM
    # No entity admitted by the broad pass has a source identifier: none is inferred
    # from a title. A Wikimedia mapping exists only where the catalogue recorded one.
    admitted = [game for identifier, game in after.items() if identifier not in before]
    assert len(admitted) == 219 and all(game.source_identifiers == () for game in admitted)
    # J. Every entity has a lifecycle history, and the bootstrap ones keep theirs.
    histories = {h.canonical_game_id: h for h in recorded_evaluation.state.lifecycle}
    assert set(histories) == set(after)
    assert all(histories[identifier].records for identifier in before)
    # Parents named by relationships all exist.
    for game in after.values():
        assert all(r.parent_game_id in after for r in game.relationships)


def test_proven_identity_joins_an_entity_and_never_duplicates_it(
    recorded_evaluation: BroadEvaluation,
) -> None:
    state = recorded_evaluation.state
    slug_of = {game.canonical_game_id: game.slug for game in state.catalogue.games}
    active = {
        (b.key.source, b.key.source_id): slug_of[b.canonical_game_id]
        for b in state.mapping.bindings
        if b.valid_to is None
    }
    # B. Counter-Strike 2: its IGDB record and the Steam app that record names resolve
    # to the one bootstrap entity. No second Counter-Strike 2 is created.
    assert active[("igdb", "242408")] == "counter-strike-2"
    assert active[("steam_catalogue", "730")] == "counter-strike-2"
    names = [game.name for game in state.catalogue.games]
    for name in ("Counter-Strike 2", "Dota 2", "Elden Ring", "Rust", "Minecraft"):
        assert names.count(name) == 1, name
    assert len(set(slug_of.values())) == len(slug_of)
    # Every binding names an entity that exists, and no record is bound twice.
    assert len(active) == recorded_evaluation.summary.bound_records
    # C. A genuinely new, eligible game becomes NEW_ENTITY under the existing rules.
    created = [d for d in state.decisions.decisions if d.decision.kind is DecisionKind.NEW_ENTITY]
    assert len(created) == 219
    by_key = {record.key: record for record in recorded()}
    for decision in created:
        record = by_key[decision.record]
        assert record.admissibility in (
            Admissibility.ENTITY_ELIGIBLE,
            Admissibility.EXPLICIT_MODEL_ONLY,
        ), record.product_type
        assert decision.canonical_game_id in slug_of
    # Every automatic match went through one of the resolver's two gated paths.
    matched = [d for d in state.decisions.decisions if d.decision.kind is DecisionKind.AUTO_MATCH]
    assert matched and {d.decision.path for d in matched} <= {"A", "B"}
    assert all(d.decision.resolver_version == RESOLVER_VERSION for d in state.decisions.decisions)


def test_ambiguous_evidence_is_quarantined_and_related_products_are_not_entities(
    recorded_evaluation: BroadEvaluation,
) -> None:
    state = recorded_evaluation.state
    by_key = {record.key: record for record in recorded()}
    active = {b.key for b in state.mapping.bindings if b.valid_to is None}
    # D. A quarantined record has no canonical id at all.
    assert len(state.quarantine.entries) == 44
    for entry in state.quarantine.entries:
        assert entry.record.key not in active
        assert state.mapping.canonical_for(entry.record.key) is None
    # A shared title is never enough: 21 records wait for corroboration.
    title_only = [e for e in state.quarantine.entries if e.reason.value == "title_only"]
    assert len(title_only) == 21
    # E. DLC, bundles, packs and the like are kept as related products, never as games.
    slugs = {game.name for game in state.catalogue.games}
    assert len(state.related_products) == 23
    for product in state.related_products:
        record = by_key[product.record]
        assert record.admissibility is Admissibility.RELATED_PRODUCT, record.product_type
        assert product.record not in active
        assert state.mapping.canonical_for(product.record) is None
        assert not (record.title in slugs and record.product_type is ProductType.DLC)
    # No DLC, demo, soundtrack or bundle was admitted as an entity by any route.
    created = {
        d.record for d in state.decisions.decisions if d.decision.kind is DecisionKind.NEW_ENTITY
    }
    never = (Admissibility.RELATED_PRODUCT, Admissibility.NOT_A_GAME, Admissibility.UNDETERMINED)
    assert not [key for key in created if by_key[key].admissibility in never]
    # A packaging variant never creates an entity of its own either.
    assert not [
        key for key in created if by_key[key].admissibility is Admissibility.VARIANT_OF_PARENT
    ]


def test_repeating_the_same_inputs_changes_nothing(recorded_evaluation: BroadEvaluation) -> None:
    # F. The same records, evaluated again on top of the first result, a day later.
    again = run(recorded(), previous=recorded_evaluation.state, as_of=AS_OF + timedelta(days=1))
    first, second = recorded_evaluation.state, again.state
    assert [g.canonical_game_id for g in second.catalogue.games] == [
        g.canonical_game_id for g in first.catalogue.games
    ]
    assert second.mapping == first.mapping  # no binding added, closed or rewritten
    assert second.decisions == first.decisions  # nothing was decided again
    assert second.quarantine == first.quarantine
    assert second.related_products == first.related_products
    assert again.summary.broad_work.resolver_calls == 0
    assert again.summary.entities_admitted_by_broad == 0  # nothing new was admitted
    assert again.summary.canonical_entities == 234


def test_new_evidence_resolves_a_quarantine_without_rewriting_history() -> None:
    """SYNTHETIC. A Steam app that shares a title with a game waits; an id link frees it."""
    game = igdb_game("9000001", "Glimmerfall Odyssey")
    app = steam_app("9000002", "Glimmerfall Odyssey")
    first = run([game, app])
    assert first.summary.records_admitted_as_new_entities == 1
    assert first.summary.quarantine_reasons == {"title_only": 1}
    (entity,) = [g for g in first.state.catalogue.games if g.name == "Glimmerfall Odyssey"]
    assert first.state.mapping.canonical_for(app.key) is None  # I. no id from a title
    held = first.state.quarantine.get(app.key)
    assert held is not None

    # H. Later the IGDB record names the Steam app as a structured external id.
    later = AS_OF + timedelta(days=3)
    linked = igdb_game("9000001", "Glimmerfall Odyssey", steam="9000002")
    assert linked.content_hash != game.content_hash
    second = run([linked, app], previous=first.state, as_of=later, id_factory=counting_ids(50_000))
    assert second.state.mapping.canonical_for(app.key) == entity.canonical_game_id
    assert second.state.quarantine.get(app.key) is None
    assert second.summary.quarantined_records == 0
    assert second.summary.records_resolved_to_existing_entities == 1
    assert second.summary.canonical_entities == first.summary.canonical_entities  # no duplicate
    # Nothing that was recorded before was changed: the log and the bindings only grew.
    earlier = first.state.decisions.decisions
    assert second.state.decisions.decisions[: len(earlier)] == earlier
    assert len(second.state.decisions.decisions) == len(earlier) + 1
    waited = [d for d in earlier if d.record == app.key]
    assert [d.decision.kind for d in waited] == [DecisionKind.QUARANTINE]
    assert all(d.decided_at == AS_OF for d in earlier)
    freed = second.state.decisions.decisions[-1]
    assert freed.record == app.key and freed.decision.kind is DecisionKind.AUTO_MATCH
    assert freed.decision.path == "A" and freed.decided_at == later
    old_bindings = first.state.mapping.bindings
    assert second.state.mapping.bindings[: len(old_bindings)] == old_bindings
    new_binding = second.state.mapping.bindings[-1]
    assert new_binding.valid_from == later and new_binding.key == app.key
    # Point in time: before the evidence arrived, the app was bound to nothing.
    assert second.state.mapping.canonical_for(app.key, AS_OF + timedelta(days=1)) is None
    # The same title on a different game, with no link, is still not an identity.
    stranger = steam_app("9000003", "Glimmerfall Odyssey")
    third = run([linked, app, stranger], previous=second.state, as_of=later + timedelta(days=1))
    assert third.state.mapping.canonical_for(stranger.key) is None
    assert third.summary.quarantined_records == 1


def test_related_and_reworked_products_are_placed_by_the_existing_rules() -> None:
    """SYNTHETIC. DLC is not a game; a remaster is its own game with a typed parent."""
    base = igdb_game("9100001", "Hollowmere Chronicle", year=2012)
    dlc = igdb_game("9100002", "Hollowmere Chronicle: Ashes", kind=ProductType.DLC, parent=base)
    remaster = igdb_game(
        "9100003",
        "Hollowmere Chronicle Remastered",
        kind=ProductType.REMASTER,
        parent=base,
        year=2022,
    )
    sequel = igdb_game("9100004", "Hollowmere Chronicle 2", year=2016)
    result = run([base, dlc, remaster, sequel])
    state = result.state
    games = {game.name: game for game in state.catalogue.games}
    # The base game, its remaster and its sequel are three entities. The DLC is none.
    assert {
        "Hollowmere Chronicle",
        "Hollowmere Chronicle Remastered",
        "Hollowmere Chronicle 2",
    } <= set(games)
    assert "Hollowmere Chronicle: Ashes" not in games
    assert result.summary.related_products == 1
    assert result.summary.related_product_types == {"dlc": 1}
    (product,) = state.related_products
    assert product.record == dlc.key
    assert product.product_type is ProductType.DLC
    assert state.mapping.canonical_for(dlc.key) is None  # a related product has no identity
    # The remaster carries a typed relationship to its original, not a merged identity.
    rework = games["Hollowmere Chronicle Remastered"]
    assert [(r.relationship_type, r.parent_game_id) for r in rework.relationships] == [
        (RelationshipType.REMASTER, games["Hollowmere Chronicle"].canonical_game_id)
    ]
    ids = {game.canonical_game_id for game in games.values()}
    assert len(ids) == len(games)
    assert state.mapping.canonical_for(sequel.key) != state.mapping.canonical_for(base.key)


def test_the_frozen_identity_golden_set_is_untouched_and_still_passes() -> None:
    path = FIXTURES / "resolution_golden.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == GOLDEN_FILE_SHA256
    golden = load_golden_set(path, load_recorded_catalogue(FIXTURES / "catalogue"))
    report = evaluate(golden)
    # L. The one thing that must never happen: a false automatic merge.
    assert report.passes_safety_gate
    assert report.overall.false_auto_merges == 0
    # The same frozen set, with the same results, as before this checkpoint.
    assert (report.golden_version, report.overall.cases, report.overall.correct) == (3, 200, 178)
    assert report.golden_hash == (
        "c2b3aef7b3728828ea63a16f1a445bc24d7db53ce1150450252fc4154af189bb"
    )


# --- monitoring readiness --------------------------------------------------------------


def test_coverage_says_which_sources_could_be_asked_and_never_a_value(
    recorded_evaluation: BroadEvaluation,
) -> None:
    coverage = recorded_evaluation.summary.coverage
    assert coverage.entities == 234
    assert coverage.by_source == {
        WIKIMEDIA: {"eligible": 14, "ineligible": 0, "unknown": 220},
        STEAM: {"eligible": 90, "ineligible": 69, "unknown": 75},
    }
    for states in coverage.by_source.values():
        assert sum(states.values()) == coverage.entities
    assert coverage.entities_with_wikimedia_mapping == 14
    assert coverage.entities_with_confirmed_steam_app == 90
    assert coverage.confirmed_steam_apps == 91  # one entity has two confirmed apps
    assert coverage.entities_ready_for_both == 5
    assert coverage.entities_without_an_eligible_source == 135
    # 14 + 90 - 5 entities have at least one source; the rest have none.
    assert coverage.entities == 14 + 90 - 5 + 135

    state = recorded_evaluation.state
    readiness = {r.canonical_game_id: r for r in monitoring_readiness(state, recorded())}
    slug = {game.slug: game.canonical_game_id for game in state.catalogue.games}
    # The Steam side IS the existing eligibility, entity for entity.
    existing = {e.canonical_game_id: e for e in steam_eligibility(state, recorded())}
    assert set(existing) == set(readiness)
    for identifier, own in readiness.items():
        assert own.steam is existing[identifier].state
        assert own.steam_app_ids == existing[identifier].app_ids
    # Wikimedia is never "inapplicable": a missing mapping is not evidence of no article.
    assert all(own.wikimedia is not SourceEligibility.INELIGIBLE for own in readiness.values())
    # GTA Online: Wikimedia mapped; its bound IGDB record asserts no Steam app, so Steam
    # is inapplicable. Fortnite: mapped, and nothing bound says either way: unknown.
    online = readiness[slug["gta-online"]]
    assert (online.wikimedia, online.steam) == (
        SourceEligibility.ELIGIBLE,
        SourceEligibility.INELIGIBLE,
    )
    assert readiness[slug["fortnite"]].steam is SourceEligibility.UNKNOWN
    # Counter-Strike 2: both.
    assert readiness[slug["counter-strike-2"]].steam_app_ids == ("730",)
    assert readiness[slug["counter-strike-2"]].any_source_ready
    # FiveM: no article of its own and no Steam app. Catalogued, and not coverable.
    fivem = readiness[slug["fivem"]]
    assert (fivem.wikimedia, fivem.steam) == (
        SourceEligibility.UNKNOWN,
        SourceEligibility.INELIGIBLE,
    )
    assert not fivem.any_source_ready
    # K. Nothing here is a measurement. No field can hold a count of players or views,
    # and a source that cannot be asked is a state, never a zero.
    for model in (coverage_module.EntityReadiness, coverage_module.CoverageSummary):
        for name in model.model_fields:
            assert not any(word in name for word in ("player", "view", "demand", "score", "value"))
    dumped = json.loads(coverage.model_dump_json())
    assert set(dumped["by_source"][WIKIMEDIA]) == {"eligible", "ineligible", "unknown"}
    assert {state.value for state in SourceEligibility} == {"eligible", "ineligible", "unknown"}
    # Readiness is point-in-time like the bindings it rests on: before any of them
    # existed, no Steam app was confirmed for anything.
    earlier = summarise_coverage(state, recorded(), AS_OF - timedelta(days=30))
    assert earlier.entities_with_confirmed_steam_app == 0
    assert earlier.by_source[STEAM]["eligible"] == 0
    # One definition of coverage: this module adds no eligibility rule of its own.
    code = Path(coverage_module.__file__).read_text().split('"""', 2)[2]
    assert "steam_eligibility(" in code and "class SourceEligibility" not in code
    assert "external_ids" not in code and "normalize" not in code and ".name" not in code


def test_readiness_is_not_permission_to_collect(recorded_evaluation: BroadEvaluation) -> None:
    """Ninety entities have a confirmed Steam app. None of them may be sampled in production."""
    assert recorded_evaluation.summary.coverage.entities_with_confirmed_steam_app == 90
    players = STEAM_CURRENT_PLAYERS_POLICY
    assert [c.cleared for c in players.stage_clearance] == [True, False, False, False]
    assert players.derived_metrics_allowed is Permission.UNKNOWN
    # Nothing in the evaluation enables sampling, scheduling or scoring.
    for module in (evaluation_module, coverage_module):
        code = Path(str(module.__file__)).read_text().split('"""', 2)[2]
        for forbidden in ("scheduling", "sampling_targets", "scoring", "features", "tier"):
            assert forbidden not in code, forbidden
    for package in ("scoring", "features", "events", "validation"):
        for path in (REPO / "src/metaspike" / package).glob("*.py"):
            text = path.read_text()
            assert "catalogue.evaluation" not in text and "catalogue.coverage" not in text


# --- scale (SYNTHETIC records only) ----------------------------------------------------


def test_a_thousand_synthetic_records_resolve_with_a_fraction_of_all_pairs_work() -> None:
    evaluation = evaluate_synthetic(1_000)
    summary = evaluation.summary
    assert summary.source_records == 1_000
    assert (summary.canonical_entities, summary.quarantined_records, summary.related_products) == (
        432,
        402,
        80,
    )
    assert summary.bound_records + summary.quarantined_records + summary.related_products == 1_000
    work = summary.broad_work
    assert (work.resolver_calls, work.fingerprint_checks, work.full_scans) == (1_030, 804, 60)
    assert (work.entities_offered, work.entities_all_pairs) == (25_176, 675_566)
    assert work.share_of_all_pairs is not None and work.share_of_all_pairs < 0.05
    assert work.most_entities_offered <= 8 and work.passes == 3
    # Bootstrap identities survive any amount of synthetic company.
    ids = {game.canonical_game_id for game in evaluation.state.catalogue.games}
    assert set(LEGACY_ID_TO_CANONICAL.values()) <= ids
    # Every decision kind and both match paths were exercised.
    assert set(summary.decision_kinds) == {kind.value for kind in DecisionKind} - {
        "manual_override"
    }
    # Deterministic, and nothing synthetic was mixed with recorded rows.
    assert evaluate_synthetic(1_000).summary.model_dump(
        exclude={"scoped_work": {"seconds"}, "broad_work": {"seconds"}}
    ) == summary.model_dump(exclude={"scoped_work": {"seconds"}, "broad_work": {"seconds"}})
    synthetic_keys = {record.key for record in stratified_sample(1_000).records}
    assert not synthetic_keys & {record.key for record in recorded()}
    # Synthetic games have no monitoring identifier, and that is reported as unknown.
    coverage = summary.coverage
    assert coverage.entities_with_wikimedia_mapping == 14  # the bootstrap mappings only
    assert coverage.by_source[WIKIMEDIA]["unknown"] == coverage.entities - 14


@pytest.mark.slow
@pytest.mark.parametrize("size", [5_000, 10_000, 20_000])
def test_larger_synthetic_catalogues_stay_far_below_all_pairs(
    size: int, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.catalogue_scale import measure

    measured = measure(size)
    with capsys.disabled():
        print(f"\\n{json.dumps(measured)}")
    assert measured["records"] == size
    assert measured["share_of_all_pairs"] < 0.05  # type: ignore[operator]
    assert measured["most_entities_offered"] <= 8  # type: ignore[operator]
    assert (
        measured["entities"] + measured["quarantined"] + measured["related_products"]  # type: ignore[operator]
        > size * 0.5
    )


# --- the command -----------------------------------------------------------------------


def test_the_command_is_offline_states_its_stage_and_prints_counts_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_network(*_: object, **__: object) -> None:
        raise AssertionError("the catalogue evaluation tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(cli, "build_client", no_network)
    monkeypatch.chdir(tmp_path)  # anything it wrote would land here
    base = [
        "evaluate-catalogue",
        "--recorded",
        str(FIXTURES / "catalogue"),
        "--as-of",
        "2026-10-05",
    ]
    assert cli.main([*base, "--stage", "private_development"]) == cli.EXIT_OK
    output = capsys.readouterr().out
    assert "Catalogue evaluation (offline_broad_evaluation), stage private_development" in output
    assert "No source called; nothing stored; CURRENT untouched." in output
    assert "Broad discovery in production: off" in output
    for line in (
        "supplied                         394",
        "in production's scope            17",
        "bound to an entity               327",
        "admitted as a new entity       219",
        "resolved to an existing entity 108",
        "quarantined (no identity)        44",
        "related products (not entities)  23",
        "with a broad evaluation          234",
        "without any eligible source      135",
        "They never mean zero demand.",
    ):
        assert line in output, line
    assert "eligible     14   inapplicable      0   unknown    220" in output
    assert "eligible     90   inapplicable     69   unknown     75" in output
    # Counts only: no game is named.
    for name in ("Counter-Strike", "Elden Ring", "Minecraft", "Dota"):
        assert name not in output
    assert list(tmp_path.iterdir()) == []  # nothing was written anywhere
    # The same as JSON.
    assert cli.main([*base, "--stage", "private_development", "--json"]) == cli.EXIT_OK
    document = json.loads(capsys.readouterr().out)
    assert document["scope"] == "offline_broad_evaluation"
    assert document["canonical_entities"] == 234 and document["coverage"]["entities"] == 234
    # A stage must be stated, and is enforced.
    with pytest.raises(SystemExit):
        cli.main(base)
    assert "--stage" in capsys.readouterr().err
    for stage in ("private_collection", "public_launch", "commercial_production"):
        assert cli.main([*base, "--stage", stage]) == cli.EXIT_SOURCE_POLICY
        assert f"not cleared for {stage}" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []
