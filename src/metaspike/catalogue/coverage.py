"""Which monitoring sources could be asked about each entity. Not what they would say.

Three stages are easily confused, and this module is about the second only:

    catalogued   the entity exists, with a canonical identity
    coverable    a source has a trustworthy identifier for it, so it COULD be asked
    monitored    it is actually being collected, on some schedule

Being catalogued says nothing about being coverable, and being coverable says nothing
about being monitored, permitted, or popular.

Readiness uses the vocabulary that already exists for Steam, `SourceEligibility`, for
every source, and nothing new:

    ELIGIBLE     a trustworthy identifier exists for this source
    INELIGIBLE   evidence says this source cannot observe the entity: inapplicable
    UNKNOWN      nothing settles it either way

None of the three is a measurement. An entity that is INELIGIBLE or UNKNOWN for a
source has no value from that source: not zero players, not zero attention, no value.

Steam readiness is exactly `catalogue.eligibility.steam_eligibility`. Wikimedia
readiness is the catalogue's own exact article mapping, which is never inferred: an
entity without one is UNKNOWN, because the absence of a recorded article title is not
evidence that no article exists. Nothing here reads a title to find an identifier.
"""

from collections import Counter
from collections.abc import Sequence
from datetime import datetime

from pydantic import Field

from metaspike.catalogue.eligibility import SourceEligibility, steam_eligibility
from metaspike.catalogue.generate import CatalogueState
from metaspike.domain.games import GameId
from metaspike.domain.model import FrozenModel
from metaspike.domain.source_records import SourceRecord
from metaspike.sources.steam_players import SOURCE_ID as STEAM_PLAYERS
from metaspike.sources.wikimedia import SOURCE_ID as WIKIMEDIA


class EntityReadiness(FrozenModel):
    """One entity, and whether each monitoring source could be asked about it."""

    canonical_game_id: GameId
    wikimedia: SourceEligibility
    steam: SourceEligibility
    steam_app_ids: tuple[str, ...] = ()

    @property
    def any_source_ready(self) -> bool:
        ready = SourceEligibility.ELIGIBLE
        return self.wikimedia is ready or self.steam is ready


class CoverageSummary(FrozenModel):
    """Readiness across a catalogue, in counts. Identifiers only: no demand, no values."""

    entities: int = Field(ge=0)
    # Per source: how many entities are eligible, ineligible (inapplicable) or unknown.
    by_source: dict[str, dict[str, int]]
    entities_with_wikimedia_mapping: int = Field(ge=0)
    entities_with_confirmed_steam_app: int = Field(ge=0)
    confirmed_steam_apps: int = Field(ge=0)
    entities_ready_for_both: int = Field(ge=0)
    # No source has a trustworthy identifier. These cannot be monitored at all, and
    # nothing is known about their demand: that is absence of data, not low demand.
    entities_without_an_eligible_source: int = Field(ge=0)


def monitoring_readiness(
    state: CatalogueState, records: Sequence[SourceRecord], as_of: datetime | None = None
) -> tuple[EntityReadiness, ...]:
    """Readiness of every entity, as the bindings stood at `as_of` (None means now)."""
    steam = {
        item.canonical_game_id: item for item in steam_eligibility(state, list(records), as_of)
    }
    readiness = []
    for game in state.catalogue.games:
        mapped = game.identifier_for(WIKIMEDIA) is not None
        own = steam[game.canonical_game_id]
        readiness.append(
            EntityReadiness(
                canonical_game_id=game.canonical_game_id,
                wikimedia=SourceEligibility.ELIGIBLE if mapped else SourceEligibility.UNKNOWN,
                steam=own.state,
                steam_app_ids=own.app_ids,
            )
        )
    return tuple(readiness)


def summarise_coverage(
    state: CatalogueState, records: Sequence[SourceRecord], as_of: datetime | None = None
) -> CoverageSummary:
    readiness = monitoring_readiness(state, records, as_of)
    ready = SourceEligibility.ELIGIBLE

    def tally(values: Counter[str]) -> dict[str, int]:
        return {state.value: values.get(state.value, 0) for state in SourceEligibility}

    return CoverageSummary(
        entities=len(readiness),
        by_source={
            WIKIMEDIA: tally(Counter(item.wikimedia.value for item in readiness)),
            STEAM_PLAYERS: tally(Counter(item.steam.value for item in readiness)),
        },
        entities_with_wikimedia_mapping=sum(item.wikimedia is ready for item in readiness),
        entities_with_confirmed_steam_app=sum(item.steam is ready for item in readiness),
        confirmed_steam_apps=sum(len(item.steam_app_ids) for item in readiness),
        entities_ready_for_both=sum(
            item.wikimedia is ready and item.steam is ready for item in readiness
        ),
        entities_without_an_eligible_source=sum(not item.any_source_ready for item in readiness),
    )
