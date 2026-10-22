"""The resolver against real IGDB rows from the owner-approved live probe of 22 Oct 2026.

The recording (tests/fixtures/igdb_live_20261022) holds 139 IGDB game records: the
games IGDB links to 24 Steam apps (the 8 bootstrap apps and 16 hard cases: remakes,
remasters, editions, DLC, shared names) and IGDB's own search results for 'Doom',
'Prey', 'Resident Evil 4' and 'Dark Souls'. It is UNVERIFIED live data, used here as an
identity regression set beside the frozen golden set, which it does not change.

Each record is resolved against every other record with exactly the same normalised
title (the hardest case for a title-blind resolver), and against its IGDB parent.
"""

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from metaspike.domain.source_records import ProductType, SourceRecord
from metaspike.resolution.models import (
    CandidateReason,
    DecisionKind,
    EntityView,
    ResolutionDecision,
    ResolverInput,
)
from metaspike.resolution.resolver import resolve
from metaspike.sources.igdb import STEAM_EXTERNAL_SOURCE, parse_game_row

FIXTURES = Path(__file__).parent / "fixtures" / "igdb_live_20261022"
GAMES = json.loads((FIXTURES / "games.json").read_text())
LINKS = json.loads((FIXTURES / "external_games_by_uid.json").read_text())
RECORDED_AT = datetime.fromisoformat(GAMES["recorded_at"])
ROWS = {row["id"]: row for row in GAMES["rows"]}
RECORDS = {gid: parse_game_row(row, RECORDED_AT) for gid, row in ROWS.items()}

# Packages and add-ons never become canonical entities.
NOT_GAMES = {
    ProductType.BUNDLE,
    ProductType.DLC,
    ProductType.EXPANSION,
    ProductType.MOD,
    ProductType.PACK,
    ProductType.UPDATE,
}


def _title(record: SourceRecord) -> str:
    return re.sub(r"[^a-z0-9]", "", record.title.lower())


def _entity(gid: int) -> EntityView:
    return EntityView(ref=f"igdb-{gid}", records=(RECORDS[gid],))


def _same_title(gid: int) -> tuple[EntityView, ...]:
    mine = _title(RECORDS[gid])
    return tuple(_entity(o) for o in sorted(RECORDS) if o != gid and _title(RECORDS[o]) == mine)


def _decide(gid: int, entities: tuple[EntityView, ...]) -> ResolutionDecision:
    return resolve(ResolverInput(incoming=RECORDS[gid], entities=entities))


def test_the_recording_is_what_the_probe_returned() -> None:
    assert len(RECORDS) == 139
    assert len(LINKS["steam_apps"]) == 24 and len(LINKS["rows"]) == 35
    # Titles shared by several distinct IGDB games: the cases title-only joins get wrong.
    shared = {t: sum(_title(r) == t for r in RECORDS.values()) for t in ("doom", "prey")}
    assert shared["doom"] >= 6 and shared["prey"] >= 3
    assert sum(_title(r) == "residentevil4" for r in RECORDS.values()) >= 4


def test_an_external_uid_is_only_meaningful_with_its_source() -> None:
    """IGDB's external_games uid is not unique across sources: the Steam app id 570 is
    Dota 2 on Steam and a different game's id on GiantBomb; 730 is Counter-Strike 2 on
    Steam and Tetris DX on Twitch. Only a (source, uid) pair identifies anything."""
    by_uid: dict[str, set[tuple[str, int]]] = {}
    for row in LINKS["rows"]:
        source = row["external_game_source"]["name"].lower()
        by_uid.setdefault(row["uid"], set()).add((source, row["game"]))
    colliding = {uid for uid, links in by_uid.items() if len({g for _, g in links}) > 1}
    assert {"570", "730", "72850", "2280", "3970", "39140"} <= colliding
    steam = {
        uid: {g for s, g in links if s == STEAM_EXTERNAL_SOURCE} for uid, links in by_uid.items()
    }
    assert all(len(games) == 1 for games in steam.values()), "one Steam link per app id"
    assert steam["570"] == {2963} and steam["730"] == {242408}


@pytest.mark.parametrize("gid", sorted(g for g in RECORDS if _same_title(g)))
def test_no_record_merges_with_another_on_its_title_alone(gid: int) -> None:
    """Against every other record that has exactly its title, a record is never
    auto-matched unless that record is the parent IGDB itself names."""
    decision = _decide(gid, _same_title(gid))
    if decision.kind is DecisionKind.AUTO_MATCH:
        parent = RECORDS[gid].parent
        assert parent is not None and decision.target_ref == f"igdb-{parent.source_id}"
        assert all(
            CandidateReason.UPSTREAM_LINK in c.reasons
            for c in decision.candidates
            if c.entity_ref == decision.target_ref
        )


@pytest.mark.parametrize(
    "gid", sorted(g for g, r in RECORDS.items() if r.product_type in NOT_GAMES)
)
def test_packages_and_add_ons_never_become_entities(gid: int) -> None:
    decision = _decide(gid, _same_title(gid))
    assert decision.kind is DecisionKind.NOT_AN_ENTITY


@pytest.mark.parametrize(
    ("remake", "original"),
    [
        (132181, 974),  # Resident Evil 4 (2023 remake) / Resident Evil 4 (2005)
        (20065, 974),  # Resident Evil 4 (remaster, the 2014 Steam release) / 2005
        (81085, 2155),  # Dark Souls: Remastered / Dark Souls
        (19457, 472),  # Skyrim Special Edition / Skyrim
    ],
)
def test_a_rework_is_never_merged_into_its_original(remake: int, original: int) -> None:
    decision = _decide(remake, (_entity(original),))
    assert decision.kind is not DecisionKind.AUTO_MATCH
    assert decision.target_ref is None


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (7351, 673),  # Doom (2016) / Doom (1993)
        (19531, 546),  # Prey (2017) / Prey (2006)
        (250616, 14523),  # Helldivers 2 / Helldivers
    ],
)
def test_distinct_games_that_share_a_name_stay_apart(first: int, second: int) -> None:
    for incoming, existing in ((first, second), (second, first)):
        decision = _decide(incoming, (_entity(existing),))
        assert decision.kind is not DecisionKind.AUTO_MATCH, (incoming, existing)


def test_storefront_duplicates_without_a_type_or_link_are_quarantined() -> None:
    """IGDB returned high-numbered records with no game type and no external links that
    duplicate well-known titles (e.g. 'ELDEN RING', 'Baldur's Gate 3'). With nothing but
    a title in common they are quarantined, never merged."""
    for duplicate, game in ((2084186, 119133), (1725312, 119171), (2710035, 250616)):
        assert ROWS[duplicate].get("game_type") is None and not ROWS[duplicate].get(
            "external_games"
        )
        decision = _decide(duplicate, (_entity(game),))
        assert decision.kind is DecisionKind.QUARANTINE, duplicate


def test_a_port_follows_the_parent_its_source_names_even_when_timing_disagrees() -> None:
    """Recorded behaviour, contested: IGDB types 'Prey Evil' (2006 mobile) as a port of
    Prey (2006), and resolver_v2 attaches a port to its named parent (path A) although
    its own candidate assessment rejects the pair on release timing. Kept visible here;
    changing it needs a new resolver version and a golden-set review."""
    decision = _decide(225437, (_entity(546),))
    assert decision.kind is DecisionKind.AUTO_MATCH and decision.target_ref == "igdb-546"
    assert decision.candidates[0].verdict.value == "rejected"
