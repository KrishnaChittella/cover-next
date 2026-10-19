"""Is this video about this game? `creator_association_rules_v1`. Deterministic.

A search returns videos that matched words. A game's name in a title is not proof the
video is about the game: "Rust" is a language, a film and a chemical reaction. So a
video is only ever in one of three states for a game, and only the first is counted:

    supported   every one of these holds, on evidence retrieved by the moment read at:
                  1. the video's details were retrieved (`videos.list`), not only its
                     search snippet;
                  2. the game's catalogue name, or one of its catalogue aliases, appears
                     as whole consecutive words in the title, or is exactly one of the
                     video's tags;
                  3. YouTube's own structured classification says it is a gaming video:
                     its category is Gaming, or one of its topic categories is a video
                     game topic;
                  4. no other catalogue game is named in the title, and the title does
                     not name a related product of this game (a DLC, an edition).
    excluded    there is contradicting evidence:
                  - the words that match are part of a LONGER catalogue name, so the
                    title names a different game (a sequel, a spin-off); or
                  - details were retrieved and YouTube classifies the video as not
                    gaming: a category that is not Gaming and no video game topic.
    uncertain   everything else: the name is not in the title or tags; only a search
                snippet is held; the classification is absent; several games are named;
                or a related product is named.

Nothing here is a probability and nothing is weighed. No rule merges, creates or renames
a canonical game: names are read from the catalogue state as it stood, and the frozen
resolver takes no part. An uncertain or excluded video stays in the reading, labelled.

Known limits, by construction: a video about the game whose title does not name it is
uncertain; a gaming video that names the game only in passing is supported; and a
game whose name is a common word leans entirely on YouTube's own classification.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from urllib.parse import urlsplit

from metaspike.creator_sources.youtube import GAMING_CATEGORY_ID, VideoDetail
from metaspike.event_sources.candidates import words

ASSOCIATION_RULES_VERSION: Final = "creator_association_rules_v1"


class AssociationState(StrEnum):
    SUPPORTED = "supported"
    UNCERTAIN = "uncertain"
    EXCLUDED = "excluded"


class Reason(StrEnum):
    NAME_AND_GAMING_CLASSIFICATION = "name_and_gaming_classification"
    NO_VIDEO_DETAILS = "no_video_details"
    NAME_NOT_IN_TITLE_OR_TAGS = "name_not_in_title_or_tags"
    NO_STRUCTURED_CLASSIFICATION = "no_structured_classification"
    SEVERAL_GAMES_NAMED = "several_games_named"
    RELATED_PRODUCT_NAMED = "related_product_named"
    PART_OF_A_LONGER_GAME_NAME = "part_of_a_longer_game_name"
    CLASSIFIED_NOT_GAMING = "classified_not_gaming"


@dataclass(frozen=True)
class GameNames:
    """One catalogue game as the association rule sees it: an id and its names."""

    canonical_game_id: str
    names: tuple[tuple[str, ...], ...]

    @classmethod
    def of(cls, canonical_game_id: str, name: str, aliases: Sequence[str] = ()) -> "GameNames":
        tokens = dict.fromkeys(words(text) for text in (name, *aliases) if usable_name(text))
        return cls(canonical_game_id, tuple(t for t in tokens if t))


def usable_name(text: str) -> bool:
    """Whether a catalogue name or alias can be matched against a title as words.

    The title tokeniser keeps Latin letters and digits only. A name written in another
    script loses its letters to it ("데드 스페이스 3" would become just "3") and would
    then match every title containing that number, so it is not used. Neither is an
    executable file name ("portal2.exe"), nor a name of under three letters and digits
    ("BG", "D2"), which matches unrelated words far more often than the game. The name
    itself is always kept in the catalogue; only its use for matching titles is limited.
    (Found in U6.2; the effect before was conservative: titles with a bare number were
    read as naming several games and left uncertain, never wrongly supported.)
    """
    tokens = words(text)
    if not tokens or not any(any(char.isalpha() for char in token) for token in tokens):
        return False
    if text.casefold().strip().endswith(".exe"):
        return False
    if len("".join(tokens)) < 3:
        return False
    # Every letter of the name must have survived the tokeniser.
    letters = [char for char in text.casefold() if char.isalpha()]
    kept = "".join(tokens)
    return all(char.isascii() for char in letters) and len(kept) >= 3


@dataclass(frozen=True)
class Association:
    state: AssociationState
    reasons: tuple[Reason, ...]
    rules_version: str = ASSOCIATION_RULES_VERSION


def _spans(tokens: tuple[str, ...], phrase: tuple[str, ...]) -> list[tuple[int, int]]:
    size = len(phrase)
    return [
        (start, start + size)
        for start in range(len(tokens) - size + 1)
        if tokens[start : start + size] == phrase
    ]


def is_game_topic(topic_category: str) -> bool:
    """Whether a topic category URL names a video game topic, such as `Action_game`."""
    slug = urlsplit(topic_category).path.rsplit("/", 1)[-1].casefold()
    return slug == "video_game" or slug.endswith("_game") or "video_game" in slug


def gaming_classification(detail: VideoDetail) -> bool | None:
    """True: YouTube classifies it as gaming. False: as something else. None: no say."""
    if detail.category_id == GAMING_CATEGORY_ID:
        return True
    if any(is_game_topic(topic) for topic in detail.topic_categories):
        return True
    if detail.category_id is None:
        return None
    return False


def associate(
    game: GameNames,
    catalogue: Sequence[GameNames],
    related_titles: Sequence[str],
    search_title: str,
    detail: VideoDetail | None,
) -> Association:
    """The video's standing for `game`, from what is held about it."""
    title = words(search_title if detail is None else detail.title)
    # Every place a catalogue name appears, then only the longest name at each place.
    found = [
        (span, other.canonical_game_id)
        for other in catalogue
        for name in other.names
        for span in _spans(title, name)
    ]
    kept = {
        (span, game_id)
        for span, game_id in found
        if not any(
            wider != span and wider[0] <= span[0] and span[1] <= wider[1] for wider, _ in found
        )
    }
    named = {game_id for _, game_id in kept}
    ours = game.canonical_game_id
    appears = any(game_id == ours for _, game_id in found)
    if appears and ours not in named:
        return Association(AssociationState.EXCLUDED, (Reason.PART_OF_A_LONGER_GAME_NAME,))
    tagged = detail is not None and any(words(tag) in game.names for tag in detail.tags)
    classified = None if detail is None else gaming_classification(detail)
    if classified is False:
        return Association(AssociationState.EXCLUDED, (Reason.CLASSIFIED_NOT_GAMING,))
    doubts: list[Reason] = []
    if detail is None:
        doubts.append(Reason.NO_VIDEO_DETAILS)
    if ours not in named and not tagged:
        doubts.append(Reason.NAME_NOT_IN_TITLE_OR_TAGS)
    if detail is not None and classified is None:
        doubts.append(Reason.NO_STRUCTURED_CLASSIFICATION)
    if named - {ours}:
        doubts.append(Reason.SEVERAL_GAMES_NAMED)
    if any(_spans(title, phrase) for phrase in map(words, related_titles) if phrase):
        doubts.append(Reason.RELATED_PRODUCT_NAMED)
    if doubts:
        return Association(AssociationState.UNCERTAIN, tuple(doubts))
    return Association(AssociationState.SUPPORTED, (Reason.NAME_AND_GAMING_CLASSIFICATION,))
