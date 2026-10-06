"""Resolver gates, vetoes, overrides and product rules, on SYNTHETIC records.

These records are hand-built to isolate one rule at a time. They say nothing about
real games; the frozen golden set (recorded evidence) is evaluated separately.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from metaspike.catalogue.overrides import (
    FIVEM_OVERRIDE,
    NO_OVERRIDES,
    PRODUCTION_OVERRIDES,
    IdentityOverride,
    OverrideAction,
    OverrideSet,
)
from metaspike.domain.games import RelationshipType
from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.source_records import (
    DatePrecision,
    ExternalId,
    ProductType,
    ReleaseDate,
    SourceRecord,
)
from metaspike.resolution.models import (
    CandidateVerdict,
    DecisionKind,
    DecisionLog,
    EntityView,
    QuarantineReason,
    ResolutionDecision,
    ResolverInput,
    TitleRelation,
    Tri,
    Veto,
)
from metaspike.resolution.resolver import RESOLVER_VERSION, candidate_refs, resolve
from tests.conftest import make_id

NOW = datetime(2026, 10, 5, tzinfo=UTC)


def released(year: int) -> tuple[ReleaseDate, ...]:
    return (ReleaseDate(starts_on=date(year, 3, 1), precision=DatePrecision.DAY),)


def igdb(source_id: int, title: str, **fields: Any) -> SourceRecord:
    return SourceRecord(
        key=SourceRecordKey(source="igdb", source_id=str(source_id)),
        title=title,
        product_type=fields.pop("product_type", ProductType.MAIN_GAME),
        fetched_at=NOW,
        **fields,
    )


def steam(app_id: int, title: str, kind: str = "games") -> SourceRecord:
    return SourceRecord(
        key=SourceRecordKey(source="steam_catalogue", source_id=str(app_id)),
        title=title,
        product_type=ProductType.DLC if kind == "dlc" else ProductType.UNKNOWN,
        source_category=kind,
        fetched_at=NOW,
    )


def other(source_id: int, title: str, **fields: Any) -> SourceRecord:
    """A record from a second metadata source that, unlike Steam, carries dates and companies."""
    return SourceRecord(
        key=SourceRecordKey(source="second_catalogue", source_id=str(source_id)),
        title=title,
        product_type=fields.pop("product_type", ProductType.MAIN_GAME),
        fetched_at=NOW,
        **fields,
    )


def entity(ref: str, *records: SourceRecord) -> EntityView:
    return EntityView(ref=ref, records=records)


def decide(incoming: SourceRecord, *entities: EntityView, **fields: Any) -> ResolutionDecision:
    overrides = fields.pop("overrides", NO_OVERRIDES)
    return resolve(ResolverInput(incoming=incoming, entities=entities, **fields), overrides)


def linked(app_id: int) -> tuple[ExternalId, ...]:
    return (ExternalId(source="steam", value=str(app_id)),)


QUEST = igdb(
    1,
    "Synthetic Quest",
    alternative_titles=("SynQuest",),
    release_dates=released(2020),
    developers=("Dev Co",),
    publishers=("Pub Co",),
    external_ids=linked(100),
)


# --- path A: a cross-source id link plus a compatible title --------------------------


@pytest.mark.parametrize(
    ("steam_title", "relation"),
    [
        ("Synthetic Quest", TitleRelation.EXACT),
        ("SYNTHETIC QUEST™", TitleRelation.EXACT),
        ("SynQuest", TitleRelation.ALTERNATE),
        ("Synthetic Quest Deluxe Edition", TitleRelation.PACKAGING),
        ("Synthetic Quest Game of the Year Edition", TitleRelation.PACKAGING),
        ("Synthetic Quest (2020) Edition", TitleRelation.PACKAGING),
        ("Synthetic Quest: The Lost Kingdom", TitleRelation.SUBTITLE),
    ],
)
def test_path_a_matches_an_id_linked_record_with_a_compatible_title(
    steam_title: str, relation: TitleRelation
) -> None:
    decision = decide(steam(100, steam_title), entity("quest", QUEST))
    assert decision.kind is DecisionKind.AUTO_MATCH and decision.target_ref == "quest"
    assert decision.path == "A" and decision.resolver_version == RESOLVER_VERSION
    (assessment,) = decision.candidates
    assert assessment.evidence.id_link and assessment.evidence.title is relation
    assert assessment.verdict is CandidateVerdict.MATCH_STRONG and assessment.vetoes == ()


def test_an_id_link_works_in_either_direction() -> None:
    existing = entity("quest", steam(100, "Synthetic Quest"))
    decision = decide(QUEST, existing)
    assert decision.kind is DecisionKind.AUTO_MATCH and decision.path == "A"


def test_an_id_link_with_an_incompatible_title_is_quarantined() -> None:
    decision = decide(steam(100, "Completely Different Name"), entity("quest", QUEST))
    assert decision.kind is DecisionKind.QUARANTINE
    assert decision.quarantine_reason is QuarantineReason.ID_LINK_TITLE_MISMATCH


@pytest.mark.parametrize(
    "steam_title",
    ["Synthetic Quest 2", "Synthetic Quest Remastered", "Synthetic Quest HD Deluxe Edition"],
)
def test_an_id_link_does_not_override_a_contradicting_title(steam_title: str) -> None:
    decision = decide(steam(100, steam_title), entity("quest", QUEST))
    assert decision.kind is DecisionKind.QUARANTINE
    assert decision.quarantine_reason is QuarantineReason.CONFLICTING_EVIDENCE
    assert decision.candidates[0].vetoes  # a link and a veto together are a conflict


def test_a_year_in_the_title_must_be_the_candidates_own_release_year() -> None:
    wrong_year = decide(steam(100, "Synthetic Quest (2016) Edition"), entity("quest", QUEST))
    assert wrong_year.kind is DecisionKind.QUARANTINE


def test_two_id_linked_entities_are_never_chosen_between() -> None:
    twin = igdb(2, "Synthetic Quest", external_ids=linked(100))
    decision = decide(steam(100, "Synthetic Quest"), entity("a", QUEST), entity("b", twin))
    assert decision.kind is DecisionKind.QUARANTINE
    assert decision.quarantine_reason is QuarantineReason.MULTIPLE_CANDIDATES


def test_a_same_titled_entity_without_a_link_does_not_compete_with_the_linked_one() -> None:
    namesake = igdb(2, "Synthetic Quest", release_dates=released(1995))
    decision = decide(steam(100, "Synthetic Quest"), entity("a", QUEST), entity("b", namesake))
    assert decision.kind is DecisionKind.AUTO_MATCH and decision.target_ref == "a"


# --- path B: no link, but title + timing + a shared company ---------------------------


def incoming_quest(**fields: Any) -> SourceRecord:
    base: dict[str, Any] = {
        "release_dates": released(2020),
        "developers": ("Dev Co",),
        "publishers": ("Another Publisher",),
    }
    return other(9, fields.pop("title", "Synthetic Quest"), **(base | fields))


def test_path_b_matches_on_title_timing_and_a_shared_company() -> None:
    decision = decide(incoming_quest(), entity("quest", QUEST))
    assert decision.kind is DecisionKind.AUTO_MATCH and decision.path == "B"
    evidence = decision.candidates[0].evidence
    assert not evidence.id_link and evidence.title is TitleRelation.EXACT
    assert evidence.release_timing is Tri.YES and evidence.developer_overlap is Tri.YES
    assert evidence.publisher_overlap is Tri.NO  # one shared company is enough


def test_path_b_accepts_an_alternative_title_and_a_one_year_stagger() -> None:
    decision = decide(
        incoming_quest(title="SynQuest", release_dates=released(2021), developers=("dev co",)),
        entity("quest", QUEST),
    )
    assert decision.kind is DecisionKind.AUTO_MATCH and decision.path == "B"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"release_dates": ()}, QuarantineReason.TITLE_ONLY),  # timing unknown
        ({"developers": (), "publishers": ()}, QuarantineReason.TITLE_ONLY),  # no company data
        (
            {"developers": ("Someone Else",), "publishers": ("Nobody",)},
            QuarantineReason.TITLE_ONLY,
        ),
        ({"title": "Synthetic Quest Deluxe Edition"}, QuarantineReason.TITLE_ONLY),  # packaging
        ({"release_dates": released(2026)}, QuarantineReason.CONFLICTING_EVIDENCE),
    ],
)
def test_path_b_needs_every_part_of_its_evidence(
    changes: dict[str, Any], reason: QuarantineReason
) -> None:
    decision = decide(incoming_quest(**changes), entity("quest", QUEST))
    assert decision.kind is DecisionKind.QUARANTINE and decision.quarantine_reason is reason


def test_a_title_only_steam_record_can_never_take_path_b() -> None:
    """Steam's list has no dates or companies, so without a link it is title-only."""
    unlinked = igdb(1, "Synthetic Quest", release_dates=released(2020), developers=("Dev Co",))
    decision = decide(steam(100, "Synthetic Quest"), entity("quest", unlinked))
    assert decision.kind is DecisionKind.QUARANTINE
    assert decision.quarantine_reason is QuarantineReason.TITLE_ONLY
    assert decision.candidates[0].evidence.release_timing is Tri.UNKNOWN


def test_path_b_refuses_when_another_plausible_candidate_exists() -> None:
    undated_namesake = igdb(2, "Synthetic Quest")
    decision = decide(incoming_quest(), entity("a", QUEST), entity("b", undated_namesake))
    assert decision.kind is DecisionKind.QUARANTINE
    assert decision.quarantine_reason is QuarantineReason.MULTIPLE_CANDIDATES


def test_two_equally_corroborated_candidates_are_quarantined() -> None:
    twin = igdb(2, "Synthetic Quest", release_dates=released(2020), developers=("Dev Co",))
    decision = decide(incoming_quest(), entity("a", QUEST), entity("b", twin))
    assert decision.kind is DecisionKind.QUARANTINE


# --- vetoes and new entities ----------------------------------------------------------


def test_same_title_far_apart_and_unrelated_companies_is_a_new_game() -> None:
    reboot = other(9, "Synthetic Quest", release_dates=released(2027), developers=("New Studio",))
    decision = decide(reboot, entity("quest", QUEST))
    assert decision.kind is DecisionKind.NEW_ENTITY
    (assessment,) = decision.candidates
    assert assessment.vetoes == (Veto.INCOMPATIBLE_RELEASE_TIMING,)
    assert assessment.verdict is CandidateVerdict.REJECTED


def test_a_sequel_is_not_even_a_candidate_for_its_predecessor() -> None:
    sequel = other(9, "Synthetic Quest II", release_dates=released(2023), developers=("Dev Co",))
    decision = decide(sequel, entity("quest", QUEST))
    assert decision.kind is DecisionKind.NEW_ENTITY and decision.candidates == ()
    assert candidate_refs(ResolverInput(incoming=sequel, entities=(entity("quest", QUEST),))) == ()


@pytest.mark.parametrize(
    ("product_type", "relationship"),
    [
        (ProductType.REMASTER, RelationshipType.REMASTER),
        (ProductType.REMAKE, RelationshipType.REMAKE),
        (ProductType.STANDALONE_EXPANSION, RelationshipType.STANDALONE_EXPANSION),
    ],
)
def test_a_derived_work_is_a_new_entity_related_to_its_parent(
    product_type: ProductType, relationship: RelationshipType
) -> None:
    derived = igdb(5, "Synthetic Quest", product_type=product_type, parent=QUEST.key)
    decision = decide(derived, entity("quest", QUEST))
    assert decision.kind is DecisionKind.NEW_ENTITY
    assert Veto.UPSTREAM_DISTINCT_WORK in decision.candidates[0].vetoes
    (proposed,) = decision.relationships
    assert (proposed.relationship_type, proposed.entity_ref) == (relationship, "quest")


def test_a_remaster_typed_record_never_merges_into_the_original_by_title() -> None:
    remaster = igdb(5, "Synthetic Quest", product_type=ProductType.REMASTER)
    decision = decide(remaster, entity("quest", QUEST))
    assert decision.kind is DecisionKind.NEW_ENTITY
    assert Veto.INCOMPATIBLE_PRODUCT_TYPE in decision.candidates[0].vetoes


def test_a_record_bound_elsewhere_cannot_be_matched_to_another_entity() -> None:
    view = ResolverInput(
        incoming=steam(100, "Synthetic Quest"),
        entities=(entity("quest", QUEST), entity("other", igdb(2, "Unrelated"))),
        bound_to="other",
    )
    decision = resolve(view)
    assert decision.kind is not DecisionKind.AUTO_MATCH
    assert Veto.BOUND_ELSEWHERE in decision.candidates[0].vetoes


def test_no_candidate_and_an_eligible_type_is_a_new_entity() -> None:
    decision = decide(igdb(7, "Entirely New Game"), entity("quest", QUEST))
    assert decision.kind is DecisionKind.NEW_ENTITY and decision.target_ref is None


def test_no_candidate_and_an_undetermined_type_is_quarantined_not_admitted() -> None:
    decision = decide(steam(555, "Entirely New Game"), entity("quest", QUEST))
    assert decision.kind is DecisionKind.QUARANTINE
    assert decision.quarantine_reason is QuarantineReason.UNDETERMINED_PRODUCT_TYPE


def test_similar_but_different_titles_are_not_fuzzy_matched() -> None:
    for near_miss in ("Synthetic Quests", "Synthetc Quest", "Synthetic Guest", "Quest Synthetic"):
        decision = decide(igdb(7, near_miss), entity("quest", QUEST))
        assert decision.kind is DecisionKind.NEW_ENTITY and decision.candidates == ()


def test_the_score_orders_candidates_but_never_decides() -> None:
    rich_namesake = igdb(
        2,
        "Synthetic Quest",
        release_dates=released(2020),
        developers=("Dev Co",),
        publishers=("Pub Co",),
        platforms=("PC",),
        franchises=("Synthetic",),
    )
    incoming = other(
        9,
        "Synthetic Quest",
        release_dates=released(2020),
        developers=("Dev Co",),
        publishers=("Pub Co",),
        platforms=("PC",),
        franchises=("Synthetic",),
    )
    decision = decide(incoming, entity("a", rich_namesake), entity("b", rich_namesake))
    assert decision.candidates[0].score == decision.candidates[1].score > 0
    assert decision.kind is DecisionKind.QUARANTINE  # a high score is not a unique match


# --- products that are never entities -------------------------------------------------


@pytest.mark.parametrize(
    "product_type",
    [
        ProductType.DLC,
        ProductType.EXPANSION,
        ProductType.PACK,
        ProductType.EPISODE,
        ProductType.SEASON,
        ProductType.UPDATE,
        ProductType.DEMO,
        ProductType.BETA,
        ProductType.PLAYTEST,
        ProductType.SOUNDTRACK,
        ProductType.SOFTWARE,
    ],
)
def test_related_products_never_become_or_join_an_entity(product_type: ProductType) -> None:
    product = igdb(8, "Synthetic Quest", product_type=product_type, parent=QUEST.key)
    decision = decide(product, entity("quest", QUEST))
    assert decision.kind is DecisionKind.NOT_AN_ENTITY and decision.target_ref is None
    assert "never a canonical game" in decision.explanation


def test_steam_dlc_is_not_an_entity_even_when_titled_like_the_game() -> None:
    decision = decide(steam(101, "Synthetic Quest", kind="dlc"), entity("quest", QUEST))
    assert decision.kind is DecisionKind.NOT_AN_ENTITY


def test_bundle_that_packages_one_game_is_a_packaging_variant() -> None:
    for title in ("Synthetic Quest: Deluxe Edition", "Synthetic Quest Game of the Year Edition"):
        bundle = igdb(8, title, product_type=ProductType.BUNDLE, version_parent=QUEST.key)
        decision = decide(bundle, entity("quest", QUEST))
        assert decision.kind is DecisionKind.NOT_AN_ENTITY
        (proposed,) = decision.relationships
        assert proposed.relationship_type is RelationshipType.PACKAGING_VARIANT
        assert proposed.entity_ref == "quest"


def test_bundle_of_several_games_is_a_collection() -> None:
    sequel = igdb(2, "Synthetic Quest II", external_ids=linked(300))
    collection = igdb(
        8,
        "Synthetic Quest Collection",
        product_type=ProductType.BUNDLE,
        external_ids=(*linked(100), *linked(300)),
    )
    members = (entity("first", steam(100, "Synthetic Quest")), entity("second", steam(300, "SQ2")))
    decision = decide(collection, *members)
    assert decision.kind is DecisionKind.NOT_AN_ENTITY
    assert {(p.relationship_type, p.entity_ref) for p in decision.relationships} == {
        (RelationshipType.COLLECTION, "first"),
        (RelationshipType.COLLECTION, "second"),
    }
    assert sequel.product_type is ProductType.MAIN_GAME


def test_an_ambiguous_bundle_is_a_related_product_with_no_claimed_relationship() -> None:
    """Titled exactly like the game, but nothing links it: never a new game, never merged."""
    bundle = igdb(8, "Synthetic Quest", product_type=ProductType.BUNDLE)
    decision = decide(bundle, entity("quest", QUEST))
    assert decision.kind is DecisionKind.NOT_AN_ENTITY
    assert decision.relationships == () and decision.target_ref is None
    assert "no provable link" in decision.explanation


def test_a_bundle_with_no_candidates_at_all_is_still_not_a_new_game() -> None:
    decision = decide(igdb(8, "Mystery Box Bundle", product_type=ProductType.BUNDLE))
    assert decision.kind is DecisionKind.NOT_AN_ENTITY


def test_a_port_joins_its_parent_and_is_quarantined_without_one() -> None:
    port = igdb(8, "Synthetic Quest", product_type=ProductType.PORT, parent=QUEST.key)
    assert decide(port, entity("quest", QUEST)).kind is DecisionKind.AUTO_MATCH
    orphan = igdb(8, "Synthetic Quest", product_type=ProductType.PORT)
    decision = decide(orphan, entity("quest", QUEST))
    assert decision.quarantine_reason is QuarantineReason.VARIANT_WITHOUT_PARENT


# --- overrides: FiveM, and only FiveM -------------------------------------------------

GTA_V = igdb(1020, "Grand Theft Auto V", external_ids=linked(271590))
GTA_ONLINE = igdb(
    134710,
    "Grand Theft Auto Online",
    product_type=ProductType.STANDALONE_EXPANSION,
    parent=GTA_V.key,
)
FIVEM = igdb(146553, "FiveM", product_type=ProductType.MOD, parent=GTA_V.key)
ORDINARY_MOD = igdb(900001, "Some Other Mod", product_type=ProductType.MOD, parent=GTA_V.key)
GTA_ENTITIES = (
    entity(FIVEM_OVERRIDE.related_game_id or "", GTA_V),
    entity(make_id(2), GTA_ONLINE),
)


def test_an_ordinary_mod_is_never_an_entity_even_with_overrides_in_force() -> None:
    for overrides in (NO_OVERRIDES, PRODUCTION_OVERRIDES):
        decision = decide(ORDINARY_MOD, *GTA_ENTITIES, overrides=overrides)
        assert decision.kind is DecisionKind.NOT_AN_ENTITY and decision.override_ref is None


def test_fivem_without_its_override_is_just_a_mod() -> None:
    assert decide(FIVEM, *GTA_ENTITIES).kind is DecisionKind.NOT_AN_ENTITY


def test_the_override_is_evaluated_before_the_generic_mod_rule() -> None:
    decision = decide(FIVEM, *GTA_ENTITIES, overrides=PRODUCTION_OVERRIDES)

    assert decision.kind is DecisionKind.MANUAL_OVERRIDE
    assert decision.override_ref == "override:fivem-mod-ecosystem"
    assert decision.target_ref is None  # admitted as its own entity, merged into nothing
    (proposed,) = decision.relationships
    assert proposed.relationship_type is RelationshipType.MOD_ECOSYSTEM
    assert proposed.entity_ref == FIVEM_OVERRIDE.related_game_id  # GTA V, not GTA Online
    assert "product decision" in decision.explanation  # the reason travels with the decision


def test_the_override_finds_gta_v_through_the_records_parent_link_when_refs_are_opaque() -> None:
    anonymous = (entity("entity-1", GTA_V), entity("entity-2", GTA_ONLINE))
    decision = decide(FIVEM, *anonymous, overrides=PRODUCTION_OVERRIDES)
    assert [(p.relationship_type, p.entity_ref) for p in decision.relationships] == [
        (RelationshipType.MOD_ECOSYSTEM, "entity-1")
    ]


def test_overrides_are_versioned_and_name_exactly_one_record() -> None:
    assert PRODUCTION_OVERRIDES.version == 1 and len(PRODUCTION_OVERRIDES.overrides) == 1
    assert FIVEM_OVERRIDE.action is OverrideAction.ADMIT_ENTITY
    assert FIVEM_OVERRIDE.added_in_version == 1 and len(FIVEM_OVERRIDE.reason) > 40
    assert PRODUCTION_OVERRIDES.for_record(ORDINARY_MOD.key) is None
    with pytest.raises(ValueError, match="more than one override"):
        OverrideSet(version=1, overrides=(FIVEM_OVERRIDE, FIVEM_OVERRIDE))
    with pytest.raises(ValueError, match="newer than the set"):
        OverrideSet(version=0, overrides=(FIVEM_OVERRIDE,))
    with pytest.raises(ValueError, match="together"):
        IdentityOverride.model_validate(FIVEM_OVERRIDE.model_dump() | {"related_game_id": None})


def test_gta_v_gta_online_and_fivem_stay_three_things() -> None:
    online = decide(GTA_ONLINE, entity("gta-v", GTA_V), overrides=PRODUCTION_OVERRIDES)
    assert online.kind is DecisionKind.NEW_ENTITY
    assert online.relationships[0].relationship_type is RelationshipType.STANDALONE_EXPANSION
    fivem = decide(FIVEM, *GTA_ENTITIES, overrides=PRODUCTION_OVERRIDES)
    assert fivem.target_ref is None and fivem.kind is DecisionKind.MANUAL_OVERRIDE


# --- decision log ---------------------------------------------------------------------


def test_decisions_are_logged_append_only_with_the_record_version_they_judged() -> None:
    record = steam(100, "Synthetic Quest")
    first = decide(record, entity("quest", igdb(1, "Synthetic Quest")))
    log, logged = DecisionLog().append(record, first, NOW)
    assert logged.ref == "decision:1" and logged.record_content_hash == record.content_hash
    assert logged.decision.kind is DecisionKind.QUARANTINE and logged.canonical_game_id is None

    second = decide(record, entity("quest", QUEST))
    later, matched = log.append(record, second, NOW + timedelta(days=3), make_id(1))
    assert [d.decision.kind for d in later.for_record(record.key)] == [
        DecisionKind.QUARANTINE,
        DecisionKind.AUTO_MATCH,
    ]
    assert later.latest(record.key) == matched and matched.canonical_game_id == make_id(1)
    assert len(log.decisions) == 1  # the earlier log object is untouched
    assert later.latest(QUEST.key) is None


def test_decision_shape_is_enforced() -> None:
    base = {"explanation": "x", "resolver_version": RESOLVER_VERSION}
    with pytest.raises(ValueError, match="path is recorded exactly for AUTO_MATCH"):
        ResolutionDecision.model_validate(base | {"kind": "auto_match", "target_ref": "a"})
    with pytest.raises(ValueError, match="quarantine reason"):
        ResolutionDecision.model_validate(base | {"kind": "quarantine"})
    with pytest.raises(ValueError, match="override reference"):
        ResolutionDecision.model_validate(base | {"kind": "manual_override"})
    with pytest.raises(ValueError, match="share a reference"):
        ResolverInput(incoming=QUEST, entities=(entity("a", QUEST), entity("a", QUEST)))
