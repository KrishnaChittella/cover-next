"""Seed catalogue: bootstrap configuration, not observations.

It holds identity, relationships, lifecycle state and source mappings only. No
player counts, release dates or statistics belong here.

Wikimedia mappings are exact English Wikipedia article titles, each verified to be
a real article rather than a redirect, because the Pageviews API counts a redirect
title separately from its target. FiveM has no article of its own (the title
redirects to a section of "Grand Theft Auto modding"), so it is deliberately left
unmapped instead of borrowing another page's traffic.
"""

from pydantic import ValidationError

from metaspike.domain.games import (
    CanonicalGame,
    Catalogue,
    GameRelationship,
    LifecycleState,
    RelationshipType,
    SourceIdentifier,
)
from metaspike.sources.wikimedia import ENGLISH_WIKIPEDIA, SOURCE_ID


class CatalogueError(Exception):
    """The catalogue violates a domain rule."""


def _wiki(article: str) -> tuple[SourceIdentifier, ...]:
    return (SourceIdentifier(source=SOURCE_ID, namespace=ENGLISH_WIKIPEDIA, value=article),)


GTA_V = "msg_0001"

_SEED_GAMES: tuple[CanonicalGame, ...] = (
    CanonicalGame(
        canonical_game_id=GTA_V,
        slug="gta-v",
        name="Grand Theft Auto V",
        aliases=("GTA V", "GTA 5", "GTAV"),
        source_identifiers=_wiki("Grand_Theft_Auto_V"),
        lifecycle_state=LifecycleState.EVERGREEN,
    ),
    CanonicalGame(
        canonical_game_id="msg_0002",
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
        canonical_game_id="msg_0003",
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
        canonical_game_id="msg_0004",
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
        canonical_game_id="msg_0005",
        slug="escape-from-tarkov",
        name="Escape from Tarkov",
        aliases=("Tarkov", "EFT"),
        source_identifiers=_wiki("Escape_from_Tarkov"),
        lifecycle_state=LifecycleState.LIVE_SERVICE,
    ),
    CanonicalGame(
        canonical_game_id="msg_0006",
        slug="star-citizen",
        name="Star Citizen",
        source_identifiers=_wiki("Star_Citizen"),
        lifecycle_state=LifecycleState.LIVE_SERVICE,
    ),
    CanonicalGame(
        canonical_game_id="msg_0007",
        slug="no-mans-sky",
        name="No Man's Sky",
        aliases=("NMS",),
        source_identifiers=_wiki("No_Man's_Sky"),
        lifecycle_state=LifecycleState.LIVE_SERVICE,
    ),
    CanonicalGame(
        canonical_game_id="msg_0008",
        slug="counter-strike-2",
        name="Counter-Strike 2",
        aliases=("CS2",),
        source_identifiers=_wiki("Counter-Strike_2"),
        lifecycle_state=LifecycleState.LIVE_SERVICE,
    ),
    CanonicalGame(
        canonical_game_id="msg_0009",
        slug="dota-2",
        name="Dota 2",
        source_identifiers=_wiki("Dota_2"),
        lifecycle_state=LifecycleState.LIVE_SERVICE,
    ),
    CanonicalGame(
        canonical_game_id="msg_0010",
        slug="fortnite",
        name="Fortnite",
        source_identifiers=_wiki("Fortnite"),
        lifecycle_state=LifecycleState.LIVE_SERVICE,
    ),
    CanonicalGame(
        canonical_game_id="msg_0011",
        slug="minecraft",
        name="Minecraft",
        source_identifiers=_wiki("Minecraft"),
        lifecycle_state=LifecycleState.EVERGREEN,
    ),
    CanonicalGame(
        canonical_game_id="msg_0012",
        slug="elden-ring",
        name="Elden Ring",
        source_identifiers=_wiki("Elden_Ring"),
        lifecycle_state=LifecycleState.RELEASED,
    ),
    CanonicalGame(
        canonical_game_id="msg_0013",
        slug="baldurs-gate-3",
        name="Baldur's Gate 3",
        aliases=("BG3",),
        source_identifiers=_wiki("Baldur's_Gate_3"),
        lifecycle_state=LifecycleState.RELEASED,
    ),
    CanonicalGame(
        canonical_game_id="msg_0014",
        slug="helldivers-2",
        name="Helldivers 2",
        source_identifiers=_wiki("Helldivers_2"),
        lifecycle_state=LifecycleState.LIVE_SERVICE,
    ),
    CanonicalGame(
        canonical_game_id="msg_0015",
        slug="rust",
        name="Rust",
        source_identifiers=_wiki("Rust_(video_game)"),
        lifecycle_state=LifecycleState.LIVE_SERVICE,
    ),
)


def build_catalogue(games: tuple[CanonicalGame, ...]) -> Catalogue:
    try:
        return Catalogue(games=games)
    except ValidationError as exc:
        problems = "; ".join(error["msg"] for error in exc.errors())
        raise CatalogueError(problems) from None


def load_seed_catalogue() -> Catalogue:
    return build_catalogue(_SEED_GAMES)
