"""Source-native records, admissibility, the record store, and golden-set integrity.

Records here are SYNTHETIC: they exercise the models, not any real game's identity.
The real golden set is drafted from recorded source responses, which need credentials.
"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.source_policy import DeploymentStage, Permission
from metaspike.domain.source_records import (
    Admissibility,
    DatePrecision,
    ExternalId,
    ProductType,
    ReleaseDate,
    SourceRecord,
    admissibility_of,
)
from metaspike.resolution.golden import (
    CATEGORY_TARGETS,
    GROUP_TARGETS,
    DroppedCase,
    EvidenceCitation,
    ExpectedDecision,
    GoldenCase,
    GoldenEntity,
    GoldenGroup,
    GoldenSet,
)
from metaspike.sources.igdb import IGDB_POLICY
from metaspike.sources.steam_catalogue import STEAM_CATALOGUE_POLICY
from metaspike.storage.base import StorageCorruptionError, StoragePolicyError
from metaspike.storage.records import LocalSourceRecordStore

FETCHED = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def record(
    source: str = "igdb",
    source_id: str = "1",
    title: str = "Synthetic Quest",
    product_type: ProductType = ProductType.MAIN_GAME,
    **fields: Any,
) -> SourceRecord:
    return SourceRecord(
        key=SourceRecordKey(source=source, source_id=source_id),
        title=title,
        product_type=product_type,
        fetched_at=fields.pop("fetched_at", FETCHED),
        **fields,
    )


# --- admissibility -----------------------------------------------------------------


@pytest.mark.parametrize(
    "product_type",
    [
        ProductType.MAIN_GAME,
        ProductType.STANDALONE_EXPANSION,
        ProductType.REMAKE,
        ProductType.REMASTER,
    ],
)
def test_games_expansions_remakes_and_remasters_may_become_entities(
    product_type: ProductType,
) -> None:
    assert admissibility_of(product_type) is Admissibility.ENTITY_ELIGIBLE


@pytest.mark.parametrize(
    "product_type",
    [
        ProductType.DLC,
        ProductType.EXPANSION,
        ProductType.DEMO,
        ProductType.BETA,
        ProductType.PLAYTEST,
        ProductType.BUNDLE,
        ProductType.PACK,
        ProductType.SOUNDTRACK,
        ProductType.EPISODE,
        ProductType.SEASON,
        ProductType.UPDATE,
    ],
)
def test_dlc_demos_betas_bundles_and_soundtracks_never_become_entities(
    product_type: ProductType,
) -> None:
    assert admissibility_of(product_type) is Admissibility.RELATED_PRODUCT
    assert record(product_type=product_type).admissibility is not Admissibility.ENTITY_ELIGIBLE


def test_every_other_type_has_a_deliberate_non_entity_classification() -> None:
    assert admissibility_of(ProductType.MOD) is Admissibility.EXPLICIT_MODEL_ONLY
    assert admissibility_of(ProductType.PORT) is Admissibility.VARIANT_OF_PARENT
    assert admissibility_of(ProductType.EXPANDED_GAME) is Admissibility.VARIANT_OF_PARENT
    assert admissibility_of(ProductType.SOFTWARE) is Admissibility.NOT_A_GAME
    assert admissibility_of(ProductType.UNKNOWN) is Admissibility.UNDETERMINED
    eligible = {t for t in ProductType if admissibility_of(t) is Admissibility.ENTITY_ELIGIBLE}
    assert len(eligible) == 4  # nothing becomes eligible by accident when a type is added
    assert all(admissibility_of(product_type) for product_type in ProductType)


# --- source-native records ---------------------------------------------------------


def test_a_source_record_has_source_identity_only() -> None:
    steam = record("steam_catalogue", "424242", product_type=ProductType.UNKNOWN)
    assert "canonical_game_id" not in SourceRecord.model_fields
    assert (steam.key.source, steam.key.source_id) == ("steam_catalogue", "424242")
    assert steam.developers == () and steam.release_dates == () and steam.parent is None


def test_release_dates_must_state_their_precision_honestly() -> None:
    ReleaseDate(starts_on=date(2026, 1, 1), precision=DatePrecision.QUARTER)
    ReleaseDate(precision=DatePrecision.TBD)
    with pytest.raises(ValueError, match="exactly when its precision"):
        ReleaseDate(precision=DatePrecision.DAY)
    with pytest.raises(ValueError, match="exactly when its precision"):
        ReleaseDate(starts_on=date(2026, 1, 1), precision=DatePrecision.TBD)


def test_parent_links_stay_inside_the_records_own_source() -> None:
    parent = SourceRecordKey(source="igdb", source_id="1")
    assert record(source_id="2", product_type=ProductType.DLC, parent=parent).parent == parent
    with pytest.raises(ValueError, match="own source"):
        record(source_id="2", parent=SourceRecordKey(source="steam_catalogue", source_id="1"))
    with pytest.raises(ValueError, match="own parent"):
        record(source_id="1", version_parent=parent)


def test_content_hash_changes_only_when_the_sources_statement_changes() -> None:
    original = record()
    refetched = record(fetched_at=FETCHED + timedelta(days=3))
    renamed = record(title="Synthetic Quest: Reborn")
    linked = record(external_ids=(ExternalId(source="steam", value="424242"),))

    assert refetched.content_hash == original.content_hash
    assert len({original.content_hash, renamed.content_hash, linked.content_hash}) == 3


# --- record store ------------------------------------------------------------------


def policy_for(source: str) -> Any:
    return {"igdb": IGDB_POLICY, "steam_catalogue": STEAM_CATALOGUE_POLICY}[source]


def test_record_store_keeps_a_version_only_when_a_record_changes(tmp_path: Path) -> None:
    store = LocalSourceRecordStore(tmp_path, policy_for)
    first = record()
    assert (
        store.write([first, record("steam_catalogue", "9", product_type=ProductType.UNKNOWN)]) == 2
    )
    assert store.write([record(fetched_at=FETCHED + timedelta(days=1))]) == 0  # unchanged

    later = FETCHED + timedelta(days=2)
    renamed = record(title="Synthetic Quest II", fetched_at=later)
    assert store.write([renamed]) == 1

    assert store.get(first.key) == renamed
    assert store.get(first.key, as_of=FETCHED + timedelta(hours=1)) == first
    assert store.get(first.key, as_of=FETCHED - timedelta(days=1)) is None
    assert [r.title for r in store.history(first.key)] == ["Synthetic Quest", "Synthetic Quest II"]
    assert [r.key.source_id for r in store.all("steam_catalogue")] == ["9"]
    assert store.get(SourceRecordKey(source="igdb", source_id="404")) is None


def test_record_store_is_refused_at_a_stage_the_policy_has_not_cleared(tmp_path: Path) -> None:
    production = LocalSourceRecordStore(tmp_path, policy_for, DeploymentStage.PRIVATE_COLLECTION)
    with pytest.raises(StoragePolicyError, match="not cleared for private_collection"):
        production.write([record()])
    prohibited = IGDB_POLICY.model_copy(
        update={"historical_storage_allowed": Permission.PROHIBITED}
    )
    refusing = LocalSourceRecordStore(tmp_path, lambda source: prohibited)
    with pytest.raises(StoragePolicyError, match="does not permit storing"):
        refusing.write([record()])
    assert not (tmp_path / "records").exists()


def test_record_store_reports_corruption_and_unsafe_names(tmp_path: Path) -> None:
    store = LocalSourceRecordStore(tmp_path, policy_for)
    store.write([record()])
    path = tmp_path / "records" / "igdb.jsonl"
    path.write_text(path.read_text() + "{broken\n")
    with pytest.raises(StorageCorruptionError, match="line 2"):
        store.all("igdb")
    with pytest.raises(ValueError, match="unsafe source name"):
        store.all("../etc")


# --- golden-set integrity ----------------------------------------------------------

BASE = record(
    "igdb", "1001", "Synthetic Quest", external_ids=(ExternalId(source="steam", value="424242"),)
)
EXISTING = GoldenEntity(label="synthetic-quest", records=(BASE,))
STEAM_APP = record("steam_catalogue", "424242", "Synthetic Quest", ProductType.UNKNOWN)
SEQUEL = record("igdb", "1002", "Synthetic Quest 2")
DLC = record("igdb", "1003", "Synthetic Quest: Lost Levels", ProductType.DLC, parent=BASE.key)
ID_LINK = EvidenceCitation(record=BASE.key, field="external_ids", value="424242")


def case(**changes: Any) -> GoldenCase:
    fields: dict[str, Any] = {
        "case_id": "pos-001",
        "category": "exact_cross_source",
        "incoming": STEAM_APP,
        "existing": (EXISTING,),
        "expected": ExpectedDecision.AUTO_MATCH,
        "target": "synthetic-quest",
        "rationale": "IGDB lists this Steam app id as an external id of the game.",
        "evidence": (ID_LINK,),
    }
    return GoldenCase.model_validate(fields | changes)


def negative(**changes: Any) -> GoldenCase:
    return case(
        **{
            "case_id": "neg-001",
            "category": "numbered_sequels",
            "incoming": SEQUEL,
            "expected": ExpectedDecision.NEW_ENTITY,
            "target": None,
            "must_not_match": ("synthetic-quest",),
            "rationale": "The title carries a sequel numeral the existing game lacks.",
            "evidence": (EvidenceCitation(record=SEQUEL.key, field="title", value="Quest 2"),),
        }
        | changes
    )


def test_the_approved_distribution_totals_two_hundred() -> None:
    assert len(CATEGORY_TARGETS) == 37  # 34 approved plus 3 proposed substitutes at target 0
    assert GROUP_TARGETS == {
        GoldenGroup.POSITIVE: 62,
        GoldenGroup.NEGATIVE: 85,
        GoldenGroup.QUARANTINE: 33,
        GoldenGroup.NEW_ENTITY: 20,
    }
    assert sum(GROUP_TARGETS.values()) == 200
    for required in (
        "gta_family",
        "counter_strike_lineage",
        "call_of_duty_family",
        "short_ambiguous_names",
        "remasters",
        "remakes",
        "dlc",
        "demos",
        "betas_playtests",
        "bundles",
        "soundtracks",
        "roman_numerals",
        "renamed_games",
        "conflicting_release_dates",
    ):
        assert required in CATEGORY_TARGETS


def test_a_well_formed_case_cites_recorded_evidence() -> None:
    positive = case()
    assert positive.group is GoldenGroup.POSITIVE and positive.truth == "established"
    assert negative().group is GoldenGroup.NEGATIVE
    non_entity = negative(
        case_id="neg-dlc",
        category="dlc",
        incoming=DLC,
        expected=ExpectedDecision.NOT_AN_ENTITY,
        evidence=(EvidenceCitation(record=DLC.key, field="product_type", value="dlc"),),
        rationale="IGDB types this record as DLC of the existing game.",
    )
    assert non_entity.expected is ExpectedDecision.NOT_AN_ENTITY


def test_evidence_must_exist_in_the_recorded_records_it_cites() -> None:
    with pytest.raises(ValueError, match="does not contain the cited value"):
        case(evidence=(EvidenceCitation(record=BASE.key, field="external_ids", value="999"),))
    with pytest.raises(ValueError, match="unknown field"):
        case(evidence=(EvidenceCitation(record=BASE.key, field="metacritic", value="90"),))
    elsewhere = SourceRecordKey(source="igdb", source_id="555")
    with pytest.raises(ValueError, match="not in the case"):
        case(evidence=(EvidenceCitation(record=elsewhere, field="title", value="Synthetic"),))
    with pytest.raises(ValueError):
        case(evidence=())  # a label with no evidence is a guess


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"category": "vibes"}, "unknown category"),
        ({"target": None}, "target is given exactly for AUTO_MATCH"),
        ({"target": "someone-else"}, "not one of the existing entities"),
        ({"expected": ExpectedDecision.QUARANTINE, "target": None}, "positive case expects"),
        ({"must_not_match": ("synthetic-quest",)}, "also listed in must_not_match"),
        ({"must_not_match": ("ghost",)}, "names an unknown entity"),
        ({"existing": (EXISTING, EXISTING)}, "share a label"),
    ],
)
def test_inconsistent_positive_cases_are_rejected(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        case(**changes)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"must_not_match": ()}, "names what it must not match"),
        ({"expected": ExpectedDecision.QUARANTINE}, "must name its right outcome"),
        ({"expected": ExpectedDecision.NOT_AN_ENTITY}, "cannot be NOT_AN_ENTITY"),
        ({"incoming": DLC}, "only an entity-eligible record can be NEW_ENTITY"),
        ({"category": "title_only_missing_companies"}, "quarantine case expects QUARANTINE"),
        (
            {"category": "no_candidates", "expected": ExpectedDecision.NOT_AN_ENTITY},
            "new-entity case",
        ),
    ],
)
def test_inconsistent_negative_cases_are_rejected(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        negative(**changes)


def test_a_set_reports_its_distribution_against_the_approved_targets() -> None:
    draft = GoldenSet(
        version=1,
        status="draft",
        created_on=date(2026, 10, 4),
        sources_recorded_on=date(2026, 10, 4),
        cases=(case(), negative()),
        dropped=(
            DroppedCase(
                description="Two records titled alike with no dates or companies",
                category="same_name_different_years",
                reason="Recorded fields cannot show whether they are the same game.",
            ),
        ),
    )
    assert draft.count_by_group() == {
        GoldenGroup.POSITIVE: 1,
        GoldenGroup.NEGATIVE: 1,
        GoldenGroup.QUARANTINE: 0,
        GoldenGroup.NEW_ENTITY: 0,
    }
    gaps = draft.distribution_gaps()
    assert gaps["exact_cross_source"] == (1, 14) and gaps["numbered_sequels"] == (1, 9)
    assert len(gaps) == 34  # nowhere near complete, and it says so

    same = GoldenSet.model_validate(draft.model_dump())
    edited = GoldenSet.model_validate(draft.model_dump() | {"cases": [negative()]})
    assert same.content_hash == draft.content_hash != edited.content_hash
    with pytest.raises(ValueError, match="share a case_id"):
        GoldenSet.model_validate(draft.model_dump() | {"cases": [case(), case()]})
