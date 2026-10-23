"""Which discovered games the site and YouTube look at, and how each is named and keyed.

Identity is the Wikidata item: the canonical id is derived from the QID alone, so the same
game always gets the same id and no title ever decides it. The site shows the top risers
plus a pinned set the owner asked for, which is always looked at whatever it is doing.
"""

import hashlib
import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Final

from metaspike.discovery.wikidata import WikidataGame

# The original bootstrap games, plus games the owner asked for by name (2026-10-23).
PINNED: Final = (
    "Grand_Theft_Auto_V",
    "Grand_Theft_Auto_Online",
    "Grand_Theft_Auto_VI",
    "Escape_from_Tarkov",
    "Star_Citizen",
    "No_Man's_Sky",
    "Counter-Strike_2",
    "Dota_2",
    "Fortnite",
    "Minecraft",
    "Elden_Ring",
    "Baldur's_Gate_3",
    "Helldivers_2",
    "Rust_(video_game)",
    "Wardogs",
    "Apex_Legends",
    "Valorant",
    "Hollow_Knight",
)
TOP_RISERS: Final = 100
# Games with no normal level yet are few and are the likeliest to be unknown to anyone.
NEW_GAMES: Final = 20


def game_id(qid: str) -> str:
    """A canonical (UUID v4-shaped) id that depends on the Wikidata QID only."""
    digest = hashlib.sha256(f"wikidata:{qid}".encode()).digest()
    return str(uuid.UUID(bytes=digest[:16], version=4))


def display_name(article: str) -> str:
    """'Anthem_(video_game)' -> 'Anthem'. Only a trailing note about being a game is cut."""
    name = article.replace("_", " ")
    return re.sub(r"\s*\((?:[^()]*\b)?(?:video game|game)\)$", "", name).strip() or name


def slug_of(game: WikidataGame) -> str:
    if game.igdb:
        return game.igdb[0]
    return re.sub(r"[^a-z0-9]+", "-", display_name(game.article).lower()).strip("-")


def select(
    report: Sequence[Mapping[str, Any]], games: Sequence[WikidataGame], top: int = TOP_RISERS
) -> list[tuple[WikidataGame, Mapping[str, Any] | None, bool]]:
    """Top risers first, then pinned games not already among them: (game, riser, pinned).

    The top risers with a known trend (a ratio), then up to NEW_GAMES games with no normal
    level yet, then pinned games, which may have no riser row at all.
    """
    by_article = {g.article: g for g in games}
    by_qid = {g.qid: g for g in games}
    pinned = set(PINNED)
    rows = {row["qid"]: row for row in report}
    chosen: list[tuple[WikidataGame, Mapping[str, Any] | None, bool]] = []
    seen: set[str] = set()
    known = [row for row in report if row.get("ratio") is not None][:top]
    new = [row for row in report if row.get("ratio") is None][:NEW_GAMES]
    for row in [*known, *new]:
        game = by_qid.get(row["qid"])
        if game is not None and game.qid not in seen:
            chosen.append((game, row, game.article in pinned))
            seen.add(game.qid)
    for article in PINNED:
        game = by_article.get(article)
        if game is not None and game.qid not in seen:
            chosen.append((game, rows.get(game.qid), True))
            seen.add(game.qid)
    # Two Wikidata items can share an IGDB slug; the page path must still be unique.
    slugs: set[str] = set()
    unique: list[tuple[WikidataGame, Mapping[str, Any] | None, bool]] = []
    for game, riser, is_pinned in chosen:
        if slug_of(game) not in slugs:
            slugs.add(slug_of(game))
            unique.append((game, riser, is_pinned))
    return unique
