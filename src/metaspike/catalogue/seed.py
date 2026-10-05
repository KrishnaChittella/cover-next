"""Bootstrap entities: migration material, not routine catalogue management.

These are the 15 entities that existed before canonical resolution. They are kept
so that their identities survive: each keeps its fixed UUID, slug, relationships
and Wikimedia mapping. The runtime catalogue is no longer this list; it is GENERATED
(see `catalogue/generate.py`) from these bootstrap entities plus whatever source
records resolution admits. New games are never added here by hand.

Wikimedia mappings are exact English Wikipedia article titles, each verified to be
a real article rather than a redirect, because the Pageviews API counts a redirect
title separately from its target. FiveM has no article of its own (the title
redirects to a section of "Grand Theft Auto modding"), so it is deliberately left
unmapped instead of borrowing another page's traffic.
"""

from pydantic import ValidationError

from metaspike.catalogue.legacy_ids import (
    LegacyMappingError,
    canonical_id_for_legacy,
    verify_legacy_mapping,
)
from metaspike.domain.games import (
    CanonicalGame,
    Catalogue,
    GameRelationship,
    LifecycleState,
    RelationshipType,
    SourceIdentifier,
)
from metaspike.domain.identity import SourceRecordKey
from metaspike.sources.wikimedia import ENGLISH_WIKIPEDIA, SOURCE_ID


class CatalogueError(Exception):
    """The catalogue violates a domain rule."""


def _id(legacy_id: str) -> str:
    """The canonical id of a seed entity, named here by the legacy id it replaced."""
    return canonical_id_for_legacy(legacy_id)


def _wiki(article: str) -> tuple[SourceIdentifier, ...]:
    return (SourceIdentifier(source=SOURCE_ID, namespace=ENGLISH_WIKIPEDIA, value=article),)


GTA_V = _id("msg_0001")

BOOTSTRAP_GAMES: tuple[CanonicalGame, ...] = (
    CanonicalGame(
        canonical_game_id=GTA_V,
        slug="gta-v",
        name="Grand Theft Auto V",
        aliases=("GTA V", "GTA 5", "GTAV"),
        source_identifiers=_wiki("Grand_Theft_Auto_V"),
        lifecycle_state=LifecycleState.EVERGREEN,
    ),
    CanonicalGame(
        canonical_game_id=_id("msg_0002"),
        slug="gta-online",
        name="Grand Theft Auto Online",
        aliases=("GTA Online", "GTAO"),
        source_identifiers=_wiki("Grand_Theft_Auto_Online"),
        relationships=(
            GameRelationship(
                parent_game_id=GTA_V, relationship_type=RelationshipType.ONLINE_COMPONENT
            ),
        ),
        lifecycle_state=LifecycleState.LIVE_SERVICE,
    ),
    CanonicalGame(
        canonical_game_id=_id("msg_0003"),
        slug="fivem",
        name="FiveM",
        aliases=("Five M", "FiveM RP"),
        relationships=(
            GameRelationship(
                parent_game_id=GTA_V, relationship_type=RelationshipType.MOD_ECOSYSTEM
            ),
        ),
        lifecycle_state=LifecycleState.LIVE_SERVICE,
    ),
    CanonicalGame(
        canonical_game_id=_id("msg_0004"),
        slug="gta-vi",
        name="Grand Theft Auto VI",
        aliases=("GTA VI", "GTA 6"),
        source_identifiers=_wiki("Grand_Theft_Auto_VI"),
        relationships=(
            GameRelationship(parent_game_id=GTA_V, relationship_type=RelationshipType.SEQUEL),
        ),
        lifecycle_state=LifecycleState.PRE_RELEASE,
    ),
    CanonicalGame(
        canonical_game_id=_id("msg_0005"),
        slug="escape-from-tarkov",
        name="Escape from Tarkov",
        aliases=("Tarkov", "EFT"),
        source_identifiers=_wiki("Escape_from_Tarkov"),
        lifecycle_state=LifecycleState.LIVE_SERVICE,
    ),
    CanonicalGame(
        canonical_game_id=_id("msg_0006"),
        slug="star-citizen",
        name="Star Citizen",
        source_identifiers=_wiki("Star_Citizen"),
        lifecycle_state=LifecycleState.LIVE_SERVICE,
    ),
    CanonicalGame(
        canonical_game_id=_id("msg_0007"),
        slug="no-mans-sky",
        name="No Man's Sky",
        aliases=("NMS",),
        source_identifiers=_wiki("No_Man's_Sky"),
        lifecycle_state=LifecycleState.LIVE_SERVICE,
    ),
    CanonicalGame(
        canonical_game_id=_id("msg_0008"),
        slug="counter-strike-2",
        name="Counter-Strike 2",
        aliases=("CS2",),
        source_identifiers=_wiki("Counter-Strike_2"),
        lifecycle_state=LifecycleState.LIVE_SERVICE,
    ),
    CanonicalGame(
        canonical_game_id=_id("msg_0009"),
        slug="dota-2",
        name="Dota 2",
        source_identifiers=_wiki("Dota_2"),
        lifecycle_state=LifecycleState.LIVE_SERVICE,
    ),
    CanonicalGame(
        canonical_game_id=_id("msg_0010"),
        slug="fortnite",
        name="Fortnite",
        source_identifiers=_wiki("Fortnite"),
        lifecycle_state=LifecycleState.LIVE_SERVICE,
    ),
    CanonicalGame(
        canonical_game_id=_id("msg_0011"),
        slug="minecraft",
        name="Minecraft",
        source_identifiers=_wiki("Minecraft"),
        lifecycle_state=LifecycleState.EVERGREEN,
    ),
    CanonicalGame(
        canonical_game_id=_id("msg_0012"),
        slug="elden-ring",
        name="Elden Ring",
        source_identifiers=_wiki("Elden_Ring"),
        lifecycle_state=LifecycleState.RELEASED,
    ),
    CanonicalGame(
        canonical_game_id=_id("msg_0013"),
        slug="baldurs-gate-3",
        name="Baldur's Gate 3",
        aliases=("BG3",),
        source_identifiers=_wiki("Baldur's_Gate_3"),
        lifecycle_state=LifecycleState.RELEASED,
    ),
    CanonicalGame(
        canonical_game_id=_id("msg_0014"),
        slug="helldivers-2",
        name="Helldivers 2",
        source_identifiers=_wiki("Helldivers_2"),
        lifecycle_state=LifecycleState.LIVE_SERVICE,
    ),
    CanonicalGame(
        canonical_game_id=_id("msg_0015"),
        slug="rust",
        name="Rust",
        source_identifiers=_wiki("Rust_(video_game)"),
        lifecycle_state=LifecycleState.LIVE_SERVICE,
    ),
)


# The IGDB record each bootstrap entity corresponds to, as observed on 2026-10-05.
# These bind existing identities to source records once, at migration; they are not
# how games are resolved in routine operation. FiveM is bound by its explicit
# override instead, and Minecraft has no single matching IGDB main-game record.
BOOTSTRAP_ANCHORS: dict[str, SourceRecordKey] = {
    slug: SourceRecordKey(source="igdb", source_id=igdb_id)
    for slug, igdb_id in {
        "gta-v": "1020",
        "gta-online": "134710",
        "gta-vi": "52189",
        "escape-from-tarkov": "15536",
        "star-citizen": "1595",
        "no-mans-sky": "3225",
        "counter-strike-2": "242408",
        "dota-2": "2963",
        "fortnite": "1905",
        "elden-ring": "119133",
        "baldurs-gate-3": "119171",
        "helldivers-2": "250616",
        "rust": "3277",
    }.items()
}


def build_catalogue(games: tuple[CanonicalGame, ...]) -> Catalogue:
    try:
        return Catalogue(games=games)
    except ValidationError as exc:
        problems = "; ".join(error["msg"] for error in exc.errors())
        raise CatalogueError(problems) from None


def bootstrap_catalogue() -> Catalogue:
    """The bootstrap entities on their own, validated. Input to generation."""
    try:
        verify_legacy_mapping()
    except LegacyMappingError as error:
        raise CatalogueError(str(error)) from None
    return build_catalogue(BOOTSTRAP_GAMES)


def load_seed_catalogue() -> Catalogue:
    """The runtime catalogue when no source records have been resolved yet.

    It is produced by the generator, from the bootstrap entities alone.
    """
    from metaspike.catalogue.generate import generate_catalogue

    return generate_catalogue().catalogue
