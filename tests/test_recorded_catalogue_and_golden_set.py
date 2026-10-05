"""Recorded IGDB and Steam responses, and the draft golden set built from them.

Unlike the synthetic payloads in the adapter tests, everything here was returned by
the real APIs on 2026-10-05 and is replayed offline through the real parsers.
"""

import json
from collections import Counter
from pathlib import Path

import pytest

from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.source_records import Admissibility, DatePrecision, ProductType, SourceRecord
from metaspike.resolution.golden import (
    CATEGORY_TARGETS,
    GROUP_TARGETS,
    ExpectedDecision,
    GoldenCase,
    GoldenGroup,
    GoldenSet,
    GoldenSetFile,
    load_golden_set,
)
from metaspike.resolution.recorded import IGDB_FIXTURE, STEAM_FIXTURE, load_recorded_catalogue
from metaspike.sources.igdb import GAME_FIELDS

FIXTURES = Path(__file__).parent / "fixtures"
CATALOGUE = FIXTURES / "catalogue"
GOLDEN = FIXTURES / "resolution_golden.json"

# How the draft differs from the approved category plan. Group totals are unchanged.
# These deviations are reported for approval; a frozen set must not carry surprises.
APPROVED_DEVIATIONS = {
    "punctuation_casing": (7, 6),
    "alternate_names": (8, 6),
    "subtitles": (6, 5),
    "renamed_games": (1, 5),
    "demos": (0, 5),
    "betas_playtests": (0, 4),
    "packs_addons": (3, 0),
    "episodes_seasons": (3, 0),
    "updates": (3, 0),
    "title_only_missing_companies": (9, 8),
    "conflicting_companies": (6, 5),
    "conflicting_release_dates": (4, 5),
    "id_link_incompatible_title": (7, 5),
    "edition_without_id_link": (6, 4),
    "regional_titles": (0, 3),
    "csgo_cs2_single_app": (0, 2),
}


def igdb(source_id: int) -> SourceRecordKey:
    return SourceRecordKey(source="igdb", source_id=str(source_id))


def steam(app_id: int) -> SourceRecordKey:
    return SourceRecordKey(source="steam_catalogue", source_id=str(app_id))


@pytest.fixture(scope="module")
def recorded() -> dict[SourceRecordKey, SourceRecord]:
    return load_recorded_catalogue(CATALOGUE)


@pytest.fixture(scope="module")
def golden(recorded: dict[SourceRecordKey, SourceRecord]) -> GoldenSet:
    return load_golden_set(GOLDEN, recorded)


def by_category(golden: GoldenSet, category: str) -> list[GoldenCase]:
    return [case for case in golden.cases if case.category == category]


# --- recorded IGDB responses -------------------------------------------------------


def test_recorded_igdb_rows_carry_only_the_requested_identity_fields() -> None:
    fixture = json.loads((CATALOGUE / IGDB_FIXTURE).read_text())
    assert fixture["fields"] == list(GAME_FIELDS)
    allowed = {field.split(".")[0] for field in GAME_FIELDS} | {"id"}
    assert len(fixture["rows"]) == 263
    for row in fixture["rows"]:
        assert set(row) <= allowed


def test_every_live_igdb_vocabulary_value_is_understood(
    recorded: dict[SourceRecordKey, SourceRecord],
) -> None:
    rows = json.loads((CATALOGUE / IGDB_FIXTURE).read_text())["rows"]
    for row in rows:
        record = recorded[igdb(row["id"])]
        assert record.product_type is not ProductType.UNKNOWN, row["name"]
        assert record.source_category == row["game_type"]["type"]
        # No release date is silently dropped for having an unrecognised format.
        assert len(record.release_dates) == len(row.get("release_dates", [])), row["name"]

    types = {r.source_category for r in recorded.values() if r.key.source == "igdb"}
    assert {
        "Main Game",
        "DLC",
        "Bundle",
        "Remake",
        "Remaster",
        "Standalone Expansion",
        "Mod",
    } <= types
    assert "Pack / Addon" in types  # a value the documentation's enum does not list
    precisions = {d.precision for r in recorded.values() for d in r.release_dates}
    assert {DatePrecision.DAY, DatePrecision.YEAR} <= precisions


def test_recorded_igdb_identity_of_the_gta_family(
    recorded: dict[SourceRecordKey, SourceRecord],
) -> None:
    gta_v, online, fivem = recorded[igdb(1020)], recorded[igdb(134710)], recorded[igdb(146553)]

    assert gta_v.title == "Grand Theft Auto V" and gta_v.product_type is ProductType.MAIN_GAME
    assert ("steam", "271590") in {(e.source, e.value) for e in gta_v.external_ids}
    assert "Rockstar North" in gta_v.developers and gta_v.parent is None
    assert min(d.starts_on for d in gta_v.release_dates if d.starts_on).year == 2013

    assert online.title == "Grand Theft Auto Online"
    assert online.product_type is ProductType.STANDALONE_EXPANSION and online.parent == gta_v.key
    assert online.admissibility is Admissibility.ENTITY_ELIGIBLE

    assert fivem.title == "FiveM" and fivem.product_type is ProductType.MOD
    assert fivem.parent == gta_v.key
    assert fivem.admissibility is Admissibility.EXPLICIT_MODEL_ONLY  # never automatic
    assert len({gta_v.key, online.key, fivem.key}) == 3


# --- recorded Steam responses ------------------------------------------------------


def test_recorded_steam_apps_have_the_observed_fields_and_no_key() -> None:
    text = (CATALOGUE / STEAM_FIXTURE).read_text()
    fixture = json.loads(text)
    assert fixture["endpoint"] == "IStoreService/GetAppList/v1"
    assert len(fixture["apps"]) == 128
    for entry in fixture["apps"]:
        assert set(entry["app"]) == {"appid", "name", "last_modified", "price_change_number"}
        assert entry["kind"] in {"games", "dlc"}
    assert "key" not in fixture and "?key=" not in text


def test_steam_records_say_only_what_steam_said(
    recorded: dict[SourceRecordKey, SourceRecord],
) -> None:
    rust, soundtrack = recorded[steam(252490)], recorded[steam(26803)]

    assert rust.title == "Rust" and rust.source_category == "games"
    assert rust.product_type is ProductType.UNKNOWN  # the games list does not type it
    assert rust.developers == () and rust.release_dates == () and rust.external_ids == ()
    assert rust.source_updated_at is not None

    assert soundtrack.title == "Braid Soundtrack" and soundtrack.source_category == "dlc"
    assert soundtrack.product_type is ProductType.DLC
    assert soundtrack.admissibility is Admissibility.RELATED_PRODUCT


# --- the draft golden set ----------------------------------------------------------


def test_golden_set_has_two_hundred_cases_in_the_approved_groups(golden: GoldenSet) -> None:
    assert len(golden.cases) == 200
    assert golden.count_by_group() == GROUP_TARGETS
    assert golden.count_by_group() == {
        GoldenGroup.POSITIVE: 62,
        GoldenGroup.NEGATIVE: 85,
        GoldenGroup.QUARANTINE: 33,
        GoldenGroup.NEW_ENTITY: 20,
    }
    assert golden.status == "draft" and golden.version == 1


def test_category_deviations_from_the_approved_plan_are_exactly_the_reported_ones(
    golden: GoldenSet,
) -> None:
    assert golden.distribution_gaps() == APPROVED_DEVIATIONS
    counts = golden.count_by_category()
    on_plan = [c for c in CATEGORY_TARGETS if c not in APPROVED_DEVIATIONS]
    assert len(on_plan) == 21 and all(counts[c] == CATEGORY_TARGETS[c][1] for c in on_plan)


def test_every_case_is_built_only_from_recorded_records_and_all_fixtures_are_used(
    golden: GoldenSet, recorded: dict[SourceRecordKey, SourceRecord]
) -> None:
    used: set[SourceRecordKey] = set()
    for case in golden.cases:
        used.add(case.incoming.key)
        used.update(record.key for record in case.context)
        used.update(record.key for entity in case.existing for record in entity.records)
        assert len(case.evidence) >= 2 and case.rationale
    assert used == set(recorded)  # nothing invented, nothing recorded without a purpose
    incoming = [case.incoming.key for case in golden.cases]
    assert len(incoming) == len(set(incoming)) == 200


def test_every_positive_is_proven_by_an_id_link_the_source_itself_asserts(
    golden: GoldenSet,
) -> None:
    positives = [case for case in golden.cases if case.group is GoldenGroup.POSITIVE]
    assert len(positives) == 62
    for case in positives:
        assert case.incoming.key.source == "steam_catalogue"
        target = next(entity for entity in case.existing if entity.label == case.target)
        claimed = {
            e.value for record in target.records for e in record.external_ids if e.source == "steam"
        }
        assert case.incoming.key.source_id in claimed, case.case_id
        assert any(c.field == "external_ids" for c in case.evidence)


def test_expected_decisions_match_what_the_records_can_be(golden: GoldenSet) -> None:
    decisions = Counter(case.expected for case in golden.cases)
    assert sum(decisions.values()) == 200
    assert decisions[ExpectedDecision.QUARANTINE] == 33
    for case in golden.cases:
        eligible = case.incoming.admissibility is Admissibility.ENTITY_ELIGIBLE
        if case.expected is ExpectedDecision.NEW_ENTITY:
            assert eligible, case.case_id
        if case.expected is ExpectedDecision.NOT_AN_ENTITY:
            assert not eligible, case.case_id
        if case.group is GoldenGroup.QUARANTINE:
            assert case.truth == "difficult", case.case_id


def test_non_entity_products_never_become_entities(golden: GoldenSet) -> None:
    non_entities = [c for c in golden.cases if c.expected is ExpectedDecision.NOT_AN_ENTITY]
    kinds = Counter(case.incoming.product_type for case in non_entities)
    assert len(non_entities) == 23
    assert kinds == {
        ProductType.DLC: 8,
        ProductType.EXPANSION: 1,
        ProductType.BUNDLE: 4,
        ProductType.PACK: 3,
        ProductType.EPISODE: 2,
        ProductType.SEASON: 1,
        ProductType.UPDATE: 3,
        ProductType.MOD: 1,
    }
    for case in non_entities:
        assert case.must_not_match and case.target is None


def test_gta_v_gta_online_and_fivem_are_never_one_entity(golden: GoldenSet) -> None:
    cases = {case.incoming.title: case for case in by_category(golden, "gta_family")}
    assert len(cases) == 5

    online = cases["Grand Theft Auto Online"]
    assert online.expected is ExpectedDecision.NEW_ENTITY and len(online.must_not_match) == 1
    fivem = cases["FiveM"]
    assert fivem.expected is ExpectedDecision.NOT_AN_ENTITY  # admitted only by the override
    assert len(fivem.must_not_match) == 2  # neither GTA V nor GTA Online
    assert {c.value for c in fivem.evidence} >= {"mod", "1020"}
    assert cases["Grand Theft Auto VI"].expected is ExpectedDecision.NEW_ENTITY
    assert cases["Grand Theft Auto V"].incoming.product_type is ProductType.BUNDLE

    enhanced = next(c for c in golden.cases if c.incoming.key == steam(3240220))
    assert enhanced.expected is ExpectedDecision.QUARANTINE
    assert enhanced.context[0].product_type is ProductType.BUNDLE


def test_rust_matches_only_the_record_igdb_links_to_it(golden: GoldenSet) -> None:
    rust = next(c for c in golden.cases if c.incoming.key == steam(252490))
    assert rust.category == "short_ambiguous_names"
    assert rust.expected is ExpectedDecision.AUTO_MATCH and rust.target == "igdb-3277-rust"
    assert len(rust.must_not_match) == 3
    confusable = [e.records[0].title for e in rust.existing if e.label in rust.must_not_match]
    assert all("Rust" in title and title != "Rust" for title in confusable)


def test_counter_strike_and_call_of_duty_families(golden: GoldenSet) -> None:
    app_730 = next(c for c in golden.cases if c.incoming.key == steam(730))
    assert app_730.expected is ExpectedDecision.AUTO_MATCH
    assert app_730.target is not None and "counter-strike-2" in app_730.target
    assert any("global-offensive" in label for label in app_730.must_not_match)
    assert len(by_category(golden, "counter_strike_lineage")) == 5

    cod = by_category(golden, "call_of_duty_family")
    assert len(cod) == 6
    assert all(c.expected is ExpectedDecision.NEW_ENTITY for c in cod)
    assert all(c.incoming.title.startswith("Call of Duty") for c in cod)
    launcher = next(c for c in golden.cases if c.incoming.key == steam(1938090))
    assert launcher.expected is ExpectedDecision.QUARANTINE  # same title as the 2003 game


def test_remasters_and_remakes_are_distinct_entities_related_to_their_originals(
    golden: GoldenSet,
) -> None:
    for category, product_type in (
        ("remasters", ProductType.REMASTER),
        ("remakes", ProductType.REMAKE),
    ):
        cases = by_category(golden, category)
        assert len(cases) == 6
        for case in cases:
            assert case.incoming.product_type is product_type
            assert case.expected is ExpectedDecision.NEW_ENTITY
            assert case.incoming.parent == case.existing[0].records[0].key


def test_dropped_cases_are_recorded_with_reasons() -> None:
    stored = GoldenSetFile.model_validate_json(GOLDEN.read_bytes())
    dropped = {d.category for d in stored.dropped}
    assert {"demos", "betas_playtests", "regional_titles", "csgo_cs2_single_app"} <= dropped
    assert all(len(d.reason) > 40 for d in stored.dropped)
    assert any("awaits approval" in note for note in stored.notes)


def test_golden_set_fingerprint_is_stable(
    golden: GoldenSet, recorded: dict[SourceRecordKey, SourceRecord]
) -> None:
    assert load_golden_set(GOLDEN, recorded).content_hash == golden.content_hash
    assert len(golden.content_hash) == 64


def test_a_case_citing_an_unrecorded_record_cannot_be_loaded(
    recorded: dict[SourceRecordKey, SourceRecord], tmp_path: Path
) -> None:
    stored = json.loads(GOLDEN.read_text())
    stored["cases"][0]["incoming"]["source_id"] = "999999999"
    tampered = tmp_path / "golden.json"
    tampered.write_text(json.dumps(stored))
    with pytest.raises(ValueError, match="not in the recorded fixtures"):
        load_golden_set(tampered, recorded)

    stored = json.loads(GOLDEN.read_text())
    stored["cases"][0]["evidence"][0]["value"] = "0000000"
    tampered.write_text(json.dumps(stored))
    with pytest.raises(ValueError, match="does not contain the cited value"):
        load_golden_set(tampered, recorded)
