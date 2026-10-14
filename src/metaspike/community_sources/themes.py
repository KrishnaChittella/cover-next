"""What a discussion is about, from its title alone. `community_theme_rules_v1`.

Deliberately narrow. Each rule is a list of words or phrases, and a title either has
one or it does not. No model, no sentiment, no guess. A title can carry several themes
or none; none is `unclassified`, and nothing is made up for it.

    question      ends with "?" or begins with a question word
    how_to        "how to", "how do i", "how can i", or "guide", "tips", "tutorial"
    update        "patch", "update", "hotfix", "nerf", "buff", "wipe", "season", ...
    issue         "bug", "crash", "lag", "stutter", "cheater", "exploit", "fps", ...
    comparison    "vs", "versus", "better than", "worth it", "should i buy"

A CANDIDATE ANGLE needs at least two distinct SUPPORTED posts that share a theme and
something specific: the same version number for an update, or at least one shared
content word for anything else. The angle names its theme, the shared terms, and every
supporting post (id, community, creation and retrieval time). A theme seen in one post
is listed as observed once and is not an angle. An angle is a candidate for a future
recommendation, not a validated opportunity: it says what was being discussed in the
sample, not that a video about it would do well.
"""

import re
from collections.abc import Sequence
from enum import StrEnum
from typing import Final

from pydantic import AwareDatetime, Field

from metaspike.domain.model import FrozenModel
from metaspike.event_sources.candidates import has_phrase, words

THEME_RULES_VERSION: Final = "community_theme_rules_v1"


class Theme(StrEnum):
    QUESTION = "question"
    HOW_TO = "how_to"
    UPDATE = "update"
    ISSUE = "issue"
    COMPARISON = "comparison"


_QUESTION_WORDS = frozenset(
    [
        "how",
        "why",
        "what",
        "when",
        "where",
        "which",
        "who",
        "is",
        "are",
        "can",
        "could",
        "does",
        "do",
        "did",
        "should",
        "will",
        "would",
        "any",
        "anyone",
        "anybody",
    ]
)
_HOW_TO = (("how", "to"), ("how", "do", "i"), ("how", "can", "i"))
_HOW_TO_WORDS = frozenset({"guide", "tips", "tutorial"})
_UPDATE_WORDS = frozenset(
    [
        "patch",
        "patches",
        "update",
        "updated",
        "hotfix",
        "nerf",
        "nerfed",
        "buff",
        "buffed",
        "wipe",
        "season",
        "changelog",
        "devblog",
    ]
)
_ISSUE_WORDS = frozenset(
    [
        "bug",
        "bugs",
        "bugged",
        "crash",
        "crashes",
        "crashing",
        "broken",
        "lag",
        "laggy",
        "stutter",
        "stuttering",
        "cheater",
        "cheaters",
        "hacker",
        "hackers",
        "exploit",
        "exploits",
        "fps",
        "desync",
    ]
)
_COMPARISON = (("better", "than"), ("worth", "it"), ("should", "i", "buy"))
_COMPARISON_WORDS = frozenset({"vs", "versus"})
_VERSION = re.compile(r"\b\d+(?:\.\d+)+\b")
# Words too common to say what a discussion is about.
_STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "of",
        "for",
        "to",
        "in",
        "on",
        "at",
        "by",
        "with",
        "from",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "my",
        "your",
        "our",
        "their",
        "i",
        "you",
        "we",
        "they",
        "he",
        "she",
        "me",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "am",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "not",
        "no",
        "yes",
        "so",
        "just",
        "any",
        "anyone",
        "anybody",
        "how",
        "why",
        "what",
        "when",
        "where",
        "which",
        "who",
        "can",
        "could",
        "should",
        "would",
        "will",
        "now",
        "new",
        "get",
        "got",
        "still",
        "really",
        "after",
        "before",
        "about",
        "into",
        "out",
        "up",
        "down",
        "over",
        "than",
        "then",
        "there",
        "here",
        "vs",
        "versus",
        "game",
        "games",
    ]
)
_TRIGGERS = _UPDATE_WORDS | _ISSUE_WORDS | _HOW_TO_WORDS | _COMPARISON_WORDS | _QUESTION_WORDS

ANGLE_KINDS: Final = {
    Theme.QUESTION: "explainer answering a question asked more than once",
    Theme.HOW_TO: "how-to or guide",
    Theme.UPDATE: "update or patch breakdown",
    Theme.ISSUE: "problem and fix",
    Theme.COMPARISON: "comparison or buying advice",
}


def themes_of(title: str) -> tuple[Theme, ...]:
    tokens = words(title)
    found: list[Theme] = []
    if title.rstrip().endswith("?") or (tokens and tokens[0] in _QUESTION_WORDS):
        found.append(Theme.QUESTION)
    if any(has_phrase(tokens, p) for p in _HOW_TO) or _HOW_TO_WORDS & set(tokens):
        found.append(Theme.HOW_TO)
    if _UPDATE_WORDS & set(tokens):
        found.append(Theme.UPDATE)
    if _ISSUE_WORDS & set(tokens):
        found.append(Theme.ISSUE)
    if any(has_phrase(tokens, p) for p in _COMPARISON) or _COMPARISON_WORDS & set(tokens):
        found.append(Theme.COMPARISON)
    return tuple(found)


def content_words(title: str, game_words: frozenset[str]) -> frozenset[str]:
    """The words of a title that could say what it is about."""
    return frozenset(
        token
        for token in words(title)
        if len(token) >= 4
        and not token.isdigit()
        and token not in _STOPWORDS
        and token not in _TRIGGERS
        and token not in game_words
    )


class SupportingPost(FrozenModel):
    post_id: str
    subreddit: str
    created_at: AwareDatetime
    retrieved_at: AwareDatetime


class CandidateAngle(FrozenModel):
    """Something the sample shows being discussed more than once. A candidate only."""

    # Every theme the same supporting posts share. One discussion is one angle.
    themes: tuple[Theme, ...] = Field(min_length=1)
    kinds: tuple[str, ...] = Field(min_length=1)
    # What the posts share: a version number, or content words. Never invented.
    about: tuple[str, ...]
    supporting_posts: tuple[SupportingPost, ...] = Field(min_length=2)
    distinct_subreddits: int = Field(ge=1)
    status: str = "candidate_not_validated"


class ThemeCount(FrozenModel):
    theme: Theme
    supported_posts: int = Field(ge=0)


def candidate_angles(
    posts: Sequence[tuple[str, str, str, AwareDatetime, AwareDatetime]],
    game_words: frozenset[str],
) -> tuple[tuple[CandidateAngle, ...], tuple[ThemeCount, ...], tuple[str, ...]]:
    """Angles from SUPPORTED posts: (post id, subreddit, title, created, retrieved).

    Returns the angles, how many posts carry each theme, and the ids of posts that carry
    a theme but share it with no other post (observed once, not an angle).
    """
    by_theme: dict[Theme, list[tuple[str, str, str, AwareDatetime, AwareDatetime]]] = {}
    for post in posts:
        for theme in themes_of(post[2]):
            by_theme.setdefault(theme, []).append(post)
    found: dict[
        tuple[str, ...],
        tuple[list[Theme], list[str], list[tuple[str, str, str, AwareDatetime, AwareDatetime]]],
    ] = {}
    grouped: set[str] = set()
    for theme in Theme:
        members = sorted(by_theme.get(theme, []), key=lambda p: (p[3], p[0]))
        groups: dict[tuple[str, ...], list[tuple[str, str, str, AwareDatetime, AwareDatetime]]] = {}
        for post in members:
            versions = sorted(set(_VERSION.findall(post[2])))
            keys: list[tuple[str, ...]] = (
                [(f"version {v}",) for v in versions]
                if theme is Theme.UPDATE and versions
                else [(w,) for w in sorted(content_words(post[2], game_words))]
            )
            for key in keys:
                groups.setdefault(key, []).append(post)
        # Merge keys that hold exactly the same posts, so one discussion is one angle.
        by_members: dict[tuple[str, ...], list[str]] = {}
        for key, group in groups.items():
            ids = tuple(sorted({p[0] for p in group}))
            if len(ids) >= 2:
                by_members.setdefault(ids, []).extend(key)
        for ids, about in by_members.items():
            entry = found.setdefault(ids, ([], [], [p for p in members if p[0] in ids]))
            entry[0].append(theme)
            entry[1].extend(about)
            grouped.update(ids)
    angles = [
        CandidateAngle(
            themes=tuple(themes),
            kinds=tuple(ANGLE_KINDS[theme] for theme in themes),
            about=tuple(sorted(set(about))),
            supporting_posts=tuple(
                SupportingPost(post_id=p[0], subreddit=p[1], created_at=p[3], retrieved_at=p[4])
                for p in chosen
            ),
            distinct_subreddits=len({p[1].casefold() for p in chosen}),
        )
        for ids, (themes, about, chosen) in sorted(
            found.items(), key=lambda item: (-len(item[0]), item[0])
        )
    ]
    counts = tuple(
        ThemeCount(theme=theme, supported_posts=len({p[0] for p in by_theme.get(theme, [])}))
        for theme in Theme
    )
    once = tuple(sorted({p[0] for group in by_theme.values() for p in group} - grouped))
    return tuple(angles), counts, once
