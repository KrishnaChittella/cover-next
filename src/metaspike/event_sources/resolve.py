"""Which canonical game a Steam app id stands for: the catalogue's binding, or nothing.

The same standard the player-count sampler uses, and no other: an app counts only when
it is bound to an entity AND a record bound to that entity lists the app as a structured
external id. There is no title here at all, so there is nothing to match one against.
An app that is not bound resolves to nothing, and what it announces can only ever be a
source-level candidate.
"""

from collections.abc import Callable
from datetime import datetime

from metaspike.catalogue.eligibility import sampling_targets, steam_eligibility
from metaspike.catalogue.generate import CatalogueState
from metaspike.domain.source_records import SourceRecord
from metaspike.event_sources.base import SourceGame
from metaspike.event_sources.candidates import GameBinding
from metaspike.event_sources.steam_news import APP_NAMESPACE, SOURCE_ID

Resolver = Callable[[SourceGame], GameBinding | None]


def steam_app_bindings(
    state: CatalogueState, records: list[SourceRecord], as_of: datetime | None = None
) -> dict[str, GameBinding]:
    """Every Steam app id that is trustworthily bound, as the bindings stood at `as_of`."""
    targets = sampling_targets(state, steam_eligibility(state, records, as_of), as_of=as_of)
    return {
        target.source_native_id: GameBinding(
            canonical_game_id=target.canonical_game_id,
            binding_ref=target.binding_ref,
            mapping_version=target.mapping_version,
        )
        for target in targets
        if target.canonical_game_id is not None
    }


def steam_news_resolver(bindings: dict[str, GameBinding]) -> Resolver:
    def resolve(game: SourceGame) -> GameBinding | None:
        if game.source != SOURCE_ID or game.namespace != APP_NAMESPACE:
            return None
        return bindings.get(game.value)

    return resolve
