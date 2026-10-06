"""Source families: which observations are independent of which.

A family is one independent observation process. Every metric belongs to exactly one
family, and a family gets one voice however many metrics, aggregates or features are
derived from it: Steam's hourly mean, daily peak and velocity are three views of the
same players, not three pieces of corroboration.

Applicability is about the entity, not the data. A family that cannot observe an
entity is INAPPLICABLE: it is not a missing source and not a failing one.
"""

from enum import StrEnum


class SourceFamily(StrEnum):
    WIKIMEDIA_READERSHIP = "wikimedia_readership"
    STEAM_PLAYERS = "steam_players"
    NEWS_COVERAGE = "news_coverage"
    SEARCH_INTEREST = "search_interest"
    OFFICIAL_EVENTS = "official_events"
    # Never part of base opportunity.
    CREATOR_SUPPLY = "creator_supply"


class FamilyApplicability(StrEnum):
    APPLICABLE_AND_AVAILABLE = "applicable_and_available"
    APPLICABLE_BUT_MISSING = "applicable_but_missing"
    INAPPLICABLE = "inapplicable"
    UNKNOWN = "unknown"


# Families with a metric today. The rest are vocabulary for sources not built yet.
_FAMILY_OF_METRIC: dict[tuple[str, str], SourceFamily] = {
    ("wikimedia_pageviews", "pageviews"): SourceFamily.WIKIMEDIA_READERSHIP,
    ("steam_current_players", "concurrent_players"): SourceFamily.STEAM_PLAYERS,
}

ACTIVE_FAMILIES = (SourceFamily.WIKIMEDIA_READERSHIP, SourceFamily.STEAM_PLAYERS)


class UnknownFamilyError(KeyError):
    """A metric was used that belongs to no declared family."""


def family_of(source: str, metric: str) -> SourceFamily:
    try:
        return _FAMILY_OF_METRIC[(source, metric)]
    except KeyError:
        raise UnknownFamilyError(f"{source}/{metric}") from None
