"""Strict source-URL identity evidence, and the bootstrap source-binding audit."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from metaspike import cli
from metaspike.catalogue.bindings import TelemetryEligibility, audit_bootstrap_bindings
from metaspike.catalogue.generate import CatalogueState, generate_catalogue
from metaspike.catalogue.legacy_ids import LEGACY_ID_TO_CANONICAL, canonical_id_for_legacy
from metaspike.catalogue.seed import BOOTSTRAP_ANCHORS
from metaspike.domain.games import Catalogue, RelationshipType
from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.source_records import (
    DatePrecision,
    ExternalId,
    ProductType,
    ReleaseDate,
    SourceRecord,
)
from metaspike.resolution.golden import load_golden_set
from metaspike.resolution.models import (
    DecisionKind,
    EntityView,
    QuarantineReason,
    ResolutionDecision,
    ResolverInput,
    Veto,
)
from metaspike.resolution.recorded import load_recorded_catalogue
from metaspike.resolution.resolver import resolve
from metaspike.resolution.source_urls import recognised_source_id, url_asserted_ids

FIXTURES = Path(__file__).parent / "fixtures"
AS_OF = datetime(2026, 10, 5, tzinfo=UTC)
NOW = AS_OF


# --- the extractor ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "app_id"),
    [
        ("https://store.steampowered.com/app/123456/", "123456"),
        ("https://store.steampowered.com/app/123456", "123456"),
        ("https://store.steampowered.com/app/123456/Some_Game/", "123456"),
        ("https://store.steampowered.com/app/123456/Some_Game", "123456"),
        ("http://store.steampowered.com/app/730/", "730"),
        ("https://STORE.SteamPowered.com/app/730/CounterStrike_2/", "730"),
        ("https://store.steampowered.com/app/2320/Quake_II/?utm_source=x&snr=1_5", "2320"),
        ("https://store.steampowered.com/app/2320/Quake_II/#reviews", "2320"),
        ("  https://store.steampowered.com/app/10/  ", "10"),
    ],
)
def test_canonical_app_urls_yield_their_app_id(url: str, app_id: str) -> None:
    assert recognised_source_id(url) == ExternalId(source="steam", value=app_id)


@pytest.mark.parametrize(
    "url",
    [
        # look-alike and tricked hosts
        "https://store.steampowered.com.evil.example/app/123456/",
        "https://evil.example/store.steampowered.com/app/123456/",
        "https://xstore.steampowered.com/app/123456/",
        "https://store-steampowered.com/app/123456/",
        "https://store.steampowered.co/app/123456/",
        "https://steampowered.com/app/123456/",
        "https://www.steampowered.com/app/123456/",
        "https://store.steampowered.com.:443/app/123456/",
        "https://store.steampowered.com./app/123456/",
        "https://store.steampowered.com@evil.example/app/123456/",
        "https://user:pass@store.steampowered.com/app/123456/",
        "https://store.steampowered.com:8443/app/123456/",
        "https://steamcommunity.com/app/123456",
        "https://x.com/mercurysteam",
        "https://www.mercurysteam.com/app/123456/",
        # unsupported or malformed forms
        "ftp://store.steampowered.com/app/123456/",
        "steam://store/123456",
        "//store.steampowered.com/app/123456/",
        "store.steampowered.com/app/123456/",
        "https://store.steampowered.com:notaport/app/123456/",
        "",
        "not a url",
        # other page types on the right host
        "https://store.steampowered.com/search/?term=123456",
        "https://store.steampowered.com/bundle/123456/Some_Bundle/",
        "https://store.steampowered.com/sub/123456/",
        "https://store.steampowered.com/publisher/Valve",
        "https://store.steampowered.com/developer/Valve",
        "https://store.steampowered.com/curator/123456",
        "https://store.steampowered.com/agecheck/app/123456/",
        "https://store.steampowered.com/dlc/123456/Game/",
        "https://store.steampowered.com/franchise/Valve",
        "https://store.steampowered.com/news/app/123456",
        "https://store.steampowered.com/",
        # malformed or non-numeric app ids
        "https://store.steampowered.com/app/",
        "https://store.steampowered.com/app//Some_Game/",
        "https://store.steampowered.com/app/abc/",
        "https://store.steampowered.com/app/123abc/",
        "https://store.steampowered.com/app/12 3456/",
        "https://store.steampowered.com/app/-123456/",
        "https://store.steampowered.com/app/0123456/",
        "https://store.steampowered.com/app/0/",
        "https://store.steampowered.com/app/12345678901/",
        "https://store.steampowered.com/app/123456/Some_Game/extra/",
        "https://store.steampowered.com/APP/123456/",
        "https://store.steampowered.com/apps/123456/",
        # numbers that are not an app path
        "https://store.steampowered.com/123456/",
        "https://store.steampowered.com/games/123456/",
        # an app id only in the query string or fragment
        "https://store.steampowered.com/?app=123456",
        "https://store.steampowered.com/search/?appid=123456&x=/app/123456/",
        "https://store.steampowered.com/#/app/123456/",
        "https://example.com/?next=https://store.steampowered.com/app/123456/",
        "https://example.com/#https://store.steampowered.com/app/123456/",
    ],
)
def test_everything_else_yields_nothing(url: str) -> None:
    assert recognised_source_id(url) is None


def test_a_records_urls_yield_each_asserted_id_once() -> None:
    urls = (
        "https://synthetic.example/",
        "https://store.steampowered.com/app/100/Game/",
        "https://store.steampowered.com/app/100",
        "https://store.steampowered.com/bundle/200/",
        "https://store.steampowered.com/app/300/Other/",
    )
    assert [link.value for link in url_asserted_ids(urls)] == ["100", "300"]
    assert url_asserted_ids(()) == ()


# --- URL evidence in the resolver: SYNTHETIC records -------------------------------------


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


def steam(app_id: int, title: str) -> SourceRecord:
    return SourceRecord(
        key=SourceRecordKey(source="steam_catalogue", source_id=str(app_id)),
        title=title,
        product_type=ProductType.UNKNOWN,
        source_category="games",
        fetched_at=NOW,
    )


def store(app_id: int) -> tuple[str, ...]:
    return (f"https://store.steampowered.com/app/{app_id}/Some_Name/",)


def linked(app_id: int) -> tuple[ExternalId, ...]:
    return (ExternalId(source="steam", value=str(app_id)),)


def decide(incoming: SourceRecord, *entities: EntityView, **fields: Any) -> ResolutionDecision:
    return resolve(ResolverInput(incoming=incoming, entities=entities, **fields))


def entity(ref: str, *records: SourceRecord) -> EntityView:
    return EntityView(ref=ref, records=records)


def test_a_url_claim_alone_never_merges_even_with_an_identical_title() -> None:
    """The false merge of the abandoned generic approach, in its simplest form."""
    game = igdb(1, "Synthetic Quest", websites=store(100), release_dates=released(2020))
    decision = decide(steam(100, "Synthetic Quest"), entity("game", game))

    assert decision.kind is DecisionKind.QUARANTINE
    assert decision.quarantine_reason is QuarantineReason.UNCONFIRMED_URL_CLAIM
    evidence = decision.candidates[0].evidence
    assert evidence.source_url_link and not evidence.id_link
    assert not evidence.url_claim_corroborated and not evidence.strong_link


def test_a_url_claim_confirmed_by_an_editions_structured_id_matches() -> None:
    base = igdb(1, "Synthetic Quest", websites=store(100))
    edition = igdb(
        2, "Synthetic Quest: GOTY Edition", external_ids=linked(100), version_parent=base.key
    )
    decision = decide(
        steam(100, "Synthetic Quest GOTY Edition"), entity("game", base), context=(edition,)
    )
    assert decision.kind is DecisionKind.AUTO_MATCH and decision.target_ref == "game"
    assert decision.path == "A" and "source URL confirmed" in decision.explanation
    assert decision.candidates[0].evidence.url_claim_corroborated


@pytest.mark.parametrize(
    "claimant",
    [
        # lists the app but is not a version of the game
        igdb(2, "Synthetic Quest: GOTY Edition", external_ids=linked(100)),
        # is a version of the game but lists a different app
        igdb(
            2,
            "Synthetic Quest: GOTY Edition",
            external_ids=linked(999),
            version_parent=SourceRecordKey(source="igdb", source_id="1"),
        ),
        # is a separate work derived from the game, not an edition of it
        igdb(
            2,
            "Synthetic Quest",
            product_type=ProductType.REMASTER,
            external_ids=linked(100),
            parent=SourceRecordKey(source="igdb", source_id="1"),
        ),
    ],
)
def test_only_an_edition_that_lists_the_same_app_confirms_a_url_claim(
    claimant: SourceRecord,
) -> None:
    base = igdb(1, "Synthetic Quest", websites=store(100))
    decision = decide(steam(100, "Synthetic Quest"), entity("game", base), context=(claimant,))
    assert decision.kind is DecisionKind.QUARANTINE
    assert not decision.candidates[0].evidence.url_claim_corroborated


def test_url_names_one_app_while_the_structured_id_names_another() -> None:
    """The Stronghold pattern: a record whose two assertions disagree."""
    record = igdb(1, "Synthetic Quest", external_ids=linked(200), websites=store(100))
    by_structured_id = decide(steam(200, "Synthetic Quest 2: Steam Edition"), entity("g", record))
    assert by_structured_id.kind is DecisionKind.QUARANTINE  # the id link meets a numeral
    by_url = decide(steam(100, "Synthetic Quest"), entity("g", record))
    assert by_url.kind is DecisionKind.QUARANTINE  # the URL alone is unconfirmed
    assert by_url.quarantine_reason is QuarantineReason.UNCONFIRMED_URL_CLAIM


@pytest.mark.parametrize(
    ("steam_title", "veto"),
    [
        ("Synthetic Quest 2", Veto.DIFFERENT_SEQUEL_NUMERAL),
        ("Synthetic Quest Remastered", Veto.REWORK_MARKER_MISMATCH),
        ("Synthetic Quest HD Deluxe Edition", Veto.REWORK_MARKER_MISMATCH),
    ],
)
def test_a_confirmed_url_claim_still_cannot_pass_a_hard_veto(steam_title: str, veto: Veto) -> None:
    base = igdb(1, "Synthetic Quest", websites=store(100))
    edition = igdb(2, steam_title, external_ids=linked(100), version_parent=base.key)
    decision = decide(steam(100, steam_title), entity("game", base), context=(edition,))
    assert decision.kind is DecisionKind.QUARANTINE
    assert decision.quarantine_reason is QuarantineReason.CONFLICTING_EVIDENCE
    assert veto in decision.candidates[0].vetoes
    assert decision.candidates[0].evidence.url_claim_corroborated  # confirmed, and still refused


def test_a_url_on_the_incoming_record_cannot_override_its_own_parent_link() -> None:
    """A remaster whose store URL names an app bound to the original stays a distinct work."""
    original = entity("original", igdb(1, "Synthetic Quest"), steam(100, "Synthetic Quest"))
    remaster = igdb(
        5,
        "Synthetic Quest",
        product_type=ProductType.REMASTER,
        parent=SourceRecordKey(source="igdb", source_id="1"),
        websites=store(100),
    )
    decision = decide(remaster, original)
    assert decision.kind is DecisionKind.NEW_ENTITY
    assert Veto.UPSTREAM_DISTINCT_WORK in decision.candidates[0].vetoes
    assert decision.relationships[0].relationship_type is RelationshipType.REMASTER


def test_a_bound_record_is_not_rebound_by_a_url_claim_elsewhere() -> None:
    claimed = igdb(1, "Synthetic Quest", websites=store(100))
    view = ResolverInput(
        incoming=steam(100, "Synthetic Quest"),
        entities=(entity("claimed", claimed), entity("current", igdb(2, "Unrelated"))),
        bound_to="current",
    )
    decision = resolve(view)
    assert decision.kind is not DecisionKind.AUTO_MATCH
    assert Veto.BOUND_ELSEWHERE in decision.candidates[0].vetoes


def test_a_competing_url_claim_blocks_an_otherwise_clean_id_match() -> None:
    """The Quake II pattern: one record claims the app by id, another by URL."""
    original = igdb(1, "Synthetic Quest", websites=store(100), release_dates=released(1997))
    remaster = igdb(
        2,
        "Synthetic Quest",
        product_type=ProductType.REMASTER,
        external_ids=linked(100),
        parent=original.key,
        release_dates=released(2023),
    )
    decision = decide(
        steam(100, "Synthetic Quest"), entity("original", original), entity("remaster", remaster)
    )
    assert decision.kind is DecisionKind.QUARANTINE
    assert decision.quarantine_reason is QuarantineReason.MULTIPLE_CANDIDATES
    assert "claims this record" in decision.explanation
    # Without the competing claim the structured id is clean, which is exactly why the
    # URL has to be read: it is what reveals the conflict.
    quiet = original.model_copy(update={"websites": ()})
    clean = decide(
        steam(100, "Synthetic Quest"), entity("original", quiet), entity("remaster", remaster)
    )
    assert clean.kind is DecisionKind.AUTO_MATCH and clean.target_ref == "remaster"


def test_a_malformed_or_foreign_url_is_no_evidence_at_all() -> None:
    for url in (
        "https://store.steampowered.com.evil.example/app/100/",
        "https://store.steampowered.com/bundle/100/",
        "https://store.steampowered.com/agecheck/app/100/",
        "https://store.steampowered.com/app/1000/",
        "https://example.com/?next=https://store.steampowered.com/app/100/",
    ):
        game = igdb(1, "Synthetic Quest", websites=(url,))
        decision = decide(steam(100, "Synthetic Quest"), entity("game", game))
        assert decision.quarantine_reason is QuarantineReason.TITLE_ONLY, url
        assert not decision.candidates[0].evidence.source_url_link


# --- the two false merges of the abandoned approach, on RECORDED evidence ----------------


@pytest.fixture(scope="module")
def recorded() -> dict[SourceRecordKey, SourceRecord]:
    return load_recorded_catalogue(FIXTURES / "catalogue")


def key(source: str, source_id: int) -> SourceRecordKey:
    return SourceRecordKey(source=source, source_id=str(source_id))


def test_quake_ii_cannot_be_merged_into_either_claimant(
    recorded: dict[SourceRecordKey, SourceRecord],
) -> None:
    app, original, remaster = (
        recorded[key("steam_catalogue", 2320)],
        recorded[key("igdb", 286)],
        recorded[key("igdb", 261085)],
    )
    # What the abandoned approach saw: only the 1997 record, whose URL names the app.
    alone = decide(app, entity("original", original))
    assert alone.kind is DecisionKind.QUARANTINE
    assert alone.quarantine_reason is QuarantineReason.UNCONFIRMED_URL_CLAIM
    # With IGDB's remaster record known as well, the two claims conflict.
    both = decide(app, entity("original", original), entity("remaster", remaster))
    assert both.kind is DecisionKind.QUARANTINE
    assert both.quarantine_reason is QuarantineReason.MULTIPLE_CANDIDATES
    # In neither state of knowledge is the app merged into the 1997 game.
    assert alone.target_ref is None and both.target_ref is None


def test_plants_vs_zombies_goty_merges_only_once_the_edition_record_confirms_it(
    recorded: dict[SourceRecordKey, SourceRecord],
) -> None:
    app, base, edition = (
        recorded[key("steam_catalogue", 3590)],
        recorded[key("igdb", 1277)],
        recorded[key("igdb", 20546)],
    )
    # What the abandoned approach saw: a URL on the base game and a look-alike title.
    alone = decide(app, entity("base", base))
    assert alone.kind is DecisionKind.QUARANTINE and alone.target_ref is None
    assert alone.quarantine_reason is QuarantineReason.UNCONFIRMED_URL_CLAIM
    # The edition record lists the app and names the base game as its version parent.
    confirmed = decide(app, entity("base", base), context=(edition,))
    assert confirmed.kind is DecisionKind.AUTO_MATCH and confirmed.target_ref == "base"


def test_stronghold_is_never_matched_through_its_wrong_upstream_link(
    recorded: dict[SourceRecordKey, SourceRecord],
) -> None:
    app, stronghold = recorded[key("steam_catalogue", 40960)], recorded[key("igdb", 965)]
    assert "40960" in {link.value for link in stronghold.external_ids}  # the wrong link exists
    decision = decide(app, entity("stronghold", stronghold))
    assert decision.kind is DecisionKind.QUARANTINE and decision.target_ref is None
    assert decision.quarantine_reason is QuarantineReason.ID_LINK_TITLE_MISMATCH


def test_no_golden_case_is_auto_matched_on_url_evidence_except_the_reviewed_one(
    recorded: dict[SourceRecordKey, SourceRecord],
) -> None:
    golden = load_golden_set(FIXTURES / "resolution_golden.json", recorded)
    by_url = []
    for case in golden.cases:
        view, _ = case.resolver_view()
        decision = resolve(view)
        chosen = [c for c in decision.candidates if c.entity_ref == decision.target_ref]
        if decision.kind is DecisionKind.AUTO_MATCH and not chosen[0].evidence.id_link:
            by_url.append(case.case_id)
    assert by_url == ["pos-editions_with_id_link-09"]


# --- the bootstrap source-binding audit --------------------------------------------------


@pytest.fixture(scope="module")
def all_records() -> list[SourceRecord]:
    return [
        record
        for directory in ("catalogue", "bootstrap_catalogue")
        for record in load_recorded_catalogue(FIXTURES / directory).values()
    ]


@pytest.fixture(scope="module")
def state(all_records: list[SourceRecord]) -> CatalogueState:
    return generate_catalogue(all_records, as_of=AS_OF)


def test_every_bootstrap_entity_has_an_explicit_binding_and_eligibility(
    state: CatalogueState, all_records: list[SourceRecord]
) -> None:
    audit = {row.slug: row for row in audit_bootstrap_bindings(state, all_records)}
    assert len(audit) == 15
    table = {
        slug: (row.legacy_id, row.igdb_ids, row.steam_app_ids, row.steam_telemetry)
        for slug, row in audit.items()
    }
    yes, no, unknown = (
        TelemetryEligibility.YES,
        TelemetryEligibility.NO,
        TelemetryEligibility.UNKNOWN,
    )
    assert table == {
        "gta-v": ("msg_0001", ("1020",), (), unknown),
        "gta-online": ("msg_0002", ("134710",), (), no),
        "fivem": ("msg_0003", ("146553",), (), no),
        "gta-vi": ("msg_0004", ("52189",), (), no),
        "escape-from-tarkov": ("msg_0005", ("15536",), ("3932890",), yes),
        "star-citizen": ("msg_0006", ("1595",), (), no),
        "no-mans-sky": ("msg_0007", ("3225",), ("275850",), yes),
        "counter-strike-2": ("msg_0008", ("242408",), ("730",), yes),
        "dota-2": ("msg_0009", ("2963",), ("570",), yes),
        "fortnite": ("msg_0010", ("1905",), (), no),
        "minecraft": ("msg_0011", (), (), unknown),
        "elden-ring": ("msg_0012", ("119133",), ("1245620",), yes),
        "baldurs-gate-3": ("msg_0013", ("119171",), ("1086940",), yes),
        "helldivers-2": ("msg_0014", ("250616",), ("553850",), yes),
        "rust": ("msg_0015", ("3277",), ("252490",), yes),
    }
    for row in audit.values():
        assert row.canonical_game_id == canonical_id_for_legacy(row.legacy_id)
        assert row.reason and len(row.binding_evidence) == len(row.igdb_ids) + len(
            row.steam_app_ids
        )
    assert audit["fivem"].wikimedia_title is None
    assert audit["no-mans-sky"].wikimedia_title == "No_Man's_Sky"


def test_a_steam_app_is_eligible_only_when_bound_and_asserted_by_structured_id(
    state: CatalogueState, all_records: list[SourceRecord]
) -> None:
    by_key = {record.key: record for record in all_records}
    for row in audit_bootstrap_bindings(state, all_records):
        if row.steam_telemetry is not TelemetryEligibility.YES:
            continue
        (app_id,) = row.steam_app_ids
        (igdb_id,) = row.igdb_ids
        game = by_key[key("igdb", int(igdb_id))]
        assert app_id in {e.value for e in game.external_ids if e.source == "steam"}
        assert key("steam_catalogue", int(app_id)) in by_key  # present in Steam's own list
        evidence = " ".join(row.binding_evidence)
        assert "bootstrap:anchor" in evidence and "path A: id link" in evidence


def test_gta_v_binding_is_recorded_exactly_as_the_evidence_stands(
    state: CatalogueState, all_records: list[SourceRecord]
) -> None:
    by_key = {record.key: record for record in all_records}
    gta_v = next(r for r in audit_bootstrap_bindings(state, all_records) if r.slug == "gta-v")

    assert gta_v.steam_telemetry is TelemetryEligibility.UNKNOWN
    assert gta_v.igdb_ids == ("1020",) and gta_v.steam_app_ids == ()
    # IGDB asserts app 271590 for GTA V; Steam's recorded list does not contain it.
    assert gta_v.steam_app_ids_asserted_unconfirmed == ("271590",)
    assert key("steam_catalogue", 271590) not in by_key
    assert "271590" in {e.value for e in by_key[key("igdb", 1020)].external_ids}
    # The listed 'Enhanced' app is not bound to GTA V: nothing recorded links them.
    assert gta_v.steam_apps_quarantined == ("3240220",)
    enhanced_app = key("steam_catalogue", 3240220)
    assert state.mapping.canonical_for(enhanced_app) is None
    assert state.quarantine.get(enhanced_app) is not None
    bundle = by_key[key("igdb", 334647)]
    assert bundle.product_type is ProductType.BUNDLE
    assert bundle.parent is None and bundle.version_parent is None
    assert "271590" in gta_v.reason and "3240220" in gta_v.reason


def test_anchors_are_bound_from_recorded_evidence_only(state: CatalogueState) -> None:
    for slug, anchor in BOOTSTRAP_ANCHORS.items():
        game = next(g for g in state.catalogue.games if g.slug == slug)
        assert state.mapping.canonical_for(anchor) == game.canonical_game_id
    minecraft = canonical_id_for_legacy("msg_0011")
    assert state.mapping.records_for(minecraft) == ()  # no single recorded IGDB main record


# --- catalogue integrity after the corrections -------------------------------------------


def test_generated_catalogue_integrity(state: CatalogueState) -> None:
    games = state.catalogue.games
    Catalogue(games=games)  # relationship graph valid: parents exist, no cycles
    assert len(games) == 234 and len({g.slug for g in games}) == 234
    assert set(LEGACY_ID_TO_CANONICAL.values()) <= {g.canonical_game_id for g in games}

    active = [b for b in state.mapping.bindings if b.valid_to is None]
    assert len(active) == len({b.key for b in active}) == 335  # no record bound twice
    ids = {g.canonical_game_id for g in games}
    assert {b.canonical_game_id for b in active} <= ids

    for legacy, canonical in LEGACY_ID_TO_CANONICAL.items():  # aliases resolve, no cycles
        assert state.identity.resolve(legacy) == canonical
        assert state.identity.resolve(canonical) == canonical
    assert len(state.quarantine.entries) == 44 and len(state.related_products) == 23


def test_url_evidence_introduced_exactly_one_binding_and_no_entity(
    state: CatalogueState, all_records: list[SourceRecord]
) -> None:
    by_key = {record.key: record for record in all_records}
    url_based = []
    for logged in state.decisions.decisions:
        decision = logged.decision
        if decision.kind is not DecisionKind.AUTO_MATCH:
            continue
        chosen = next(c for c in decision.candidates if c.entity_ref == decision.target_ref)
        if not chosen.evidence.id_link and chosen.evidence.source_url_link:
            url_based.append(logged.record)
    assert url_based == [key("steam_catalogue", 3590)]  # Plants vs. Zombies GOTY Edition
    plants = state.mapping.canonical_for(key("igdb", 1277))
    assert state.mapping.canonical_for(key("steam_catalogue", 3590)) == plants
    # The edition record then joins the same entity through its own structured id.
    assert state.mapping.canonical_for(key("igdb", 20546)) == plants
    assert by_key[key("igdb", 20546)].version_parent == key("igdb", 1277)
    # Quake II stays unresolved, bound to neither the original nor the remaster.
    assert state.mapping.canonical_for(key("steam_catalogue", 2320)) is None
    assert state.mapping.canonical_for(key("igdb", 286)) != state.mapping.canonical_for(
        key("igdb", 261085)
    )


def test_generation_is_deterministic_and_settled_with_all_recorded_evidence(
    all_records: list[SourceRecord],
) -> None:
    def ids() -> Any:
        issued: list[Any] = []

        def factory() -> Any:
            import uuid

            issued.append(uuid.UUID(int=50_000 + len(issued), version=4))
            return issued[-1]

        return factory

    forward = generate_catalogue(all_records, as_of=AS_OF, id_factory=ids())
    backward = generate_catalogue(list(reversed(all_records)), as_of=AS_OF, id_factory=ids())
    assert forward == backward
    again = generate_catalogue(all_records, as_of=AS_OF + timedelta(days=1), previous=forward)
    assert again.decisions == forward.decisions and again.mapping == forward.mapping
    assert again.catalogue == forward.catalogue and again.quarantine == forward.quarantine


def test_cli_prints_the_binding_audit(capsys: pytest.CaptureFixture[str]) -> None:
    arguments = [
        "audit-bootstrap-bindings",
        "--recorded",
        str(FIXTURES / "catalogue"),
        str(FIXTURES / "bootstrap_catalogue"),
    ]
    assert cli.main([*arguments, "--json"]) == cli.EXIT_OK
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 15
    assert {row["slug"]: row["steam_telemetry"] for row in rows}["gta-v"] == "unknown"
    assert sum(row["steam_telemetry"] == "yes" for row in rows) == 8
    assert cli.main(arguments) == cli.EXIT_OK
    assert "escape-from-tarkov" in capsys.readouterr().out
