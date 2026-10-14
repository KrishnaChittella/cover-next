"""Is this discussion about this game? `community_association_rules_v1`. Deterministic.

Reddit gives no structured classification of a post the way YouTube gives a video a
category. What it gives is WHERE the post was made. So the evidence is the community,
as DECLARED in the query plan, together with the title:

    supported   - the post is in the subreddit the plan declares as this game's own
                  community, and its title does not name only other catalogue games; or
                - the post is in a subreddit the plan declares as about games in
                  general, its title names this game (catalogue name or alias, whole
                  words), and it names no other catalogue game and no related product.
    excluded    - the post is in a subreddit the plan declares as NOT about this game
                  (a programming language, a film); or
                - the words that match are part of a longer catalogue name, so the
                  title names a different game.
    uncertain   everything else: a site-wide search with no declared community; a
                general community where several games or a related product are named,
                or where the title does not name the game; the game's own community
                where the title names only other games.

A declared scope is a human assertion written into the plan, and the reading reports
it as such. No probability, no weight, no resolver involvement, no game created,
merged or renamed. Questions and comparisons are not penalised: in the game's own
community, "Rust or DayZ?" is still a discussion in Rust's community.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from metaspike.community_sources.reddit import CommunityQuery, CommunityScope, RedditPost
from metaspike.creator_sources.association import AssociationState, GameNames
from metaspike.event_sources.candidates import words

ASSOCIATION_RULES_VERSION: Final = "community_association_rules_v1"


class Reason(StrEnum):
    GAME_COMMUNITY = "in_the_game_community_declared_by_the_plan"
    NAMED_IN_GAMING_COMMUNITY = "named_in_a_declared_gaming_community"
    DECLARED_UNRELATED_COMMUNITY = "in_a_community_declared_unrelated"
    PART_OF_A_LONGER_GAME_NAME = "part_of_a_longer_game_name"
    NO_DECLARED_COMMUNITY = "no_declared_community"
    NAME_NOT_IN_TITLE = "name_not_in_title"
    SEVERAL_GAMES_NAMED = "several_games_named"
    RELATED_PRODUCT_NAMED = "related_product_named"
    ONLY_OTHER_GAMES_NAMED = "only_other_games_named"


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


def named_games(title: str, catalogue: Sequence[GameNames]) -> tuple[set[str], set[str]]:
    """The games a title names (longest name at each place), and every game it touches."""
    tokens = words(title)
    found = [
        (span, entry.canonical_game_id)
        for entry in catalogue
        for name in entry.names
        for span in _spans(tokens, name)
    ]
    kept = {
        game_id
        for span, game_id in found
        if not any(
            wider != span and wider[0] <= span[0] and span[1] <= wider[1] for wider, _ in found
        )
    }
    return kept, {game_id for _, game_id in found}


def _same(left: str | None, right: str) -> bool:
    return left is not None and left.casefold() == right.casefold()


def associate(
    game: GameNames,
    catalogue: Sequence[GameNames],
    related_titles: Sequence[str],
    post: RedditPost,
    queries: Sequence[CommunityQuery],
) -> Association:
    """The post's standing for `game`, from every query of the game that returned it."""
    ours = game.canonical_game_id
    named, touched = named_games(post.title, catalogue)
    if any(
        any(_same(unrelated, post.subreddit) for unrelated in query.unrelated_subreddits)
        for query in queries
    ):
        return Association(AssociationState.EXCLUDED, (Reason.DECLARED_UNRELATED_COMMUNITY,))
    if ours in touched and ours not in named:
        return Association(AssociationState.EXCLUDED, (Reason.PART_OF_A_LONGER_GAME_NAME,))
    scopes = {
        query.scope
        for query in queries
        if query.scope is CommunityScope.SITE_WIDE or _same(query.subreddit, post.subreddit)
    }
    if CommunityScope.DEDICATED in scopes:
        if named and ours not in named:
            return Association(AssociationState.UNCERTAIN, (Reason.ONLY_OTHER_GAMES_NAMED,))
        return Association(AssociationState.SUPPORTED, (Reason.GAME_COMMUNITY,))
    doubts: list[Reason] = []
    if CommunityScope.GENERAL_GAMING not in scopes:
        doubts.append(Reason.NO_DECLARED_COMMUNITY)
    if ours not in named:
        doubts.append(Reason.NAME_NOT_IN_TITLE)
    if named - {ours}:
        doubts.append(Reason.SEVERAL_GAMES_NAMED)
    tokens = words(post.title)
    if any(_spans(tokens, phrase) for phrase in map(words, related_titles) if phrase):
        doubts.append(Reason.RELATED_PRODUCT_NAMED)
    if doubts:
        return Association(AssociationState.UNCERTAIN, tuple(doubts))
    return Association(AssociationState.SUPPORTED, (Reason.NAMED_IN_GAMING_COMMUNITY,))
