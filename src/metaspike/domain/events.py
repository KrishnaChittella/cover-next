"""Event vocabulary. Event detection is not implemented in this phase."""

from datetime import date
from enum import StrEnum

from metaspike.domain.games import GameId, NonEmptyStr
from metaspike.domain.model import FrozenModel


class EventType(StrEnum):
    RELEASE = "release"
    MAJOR_PATCH = "major_patch"
    MINOR_PATCH = "minor_patch"
    SEASON = "season"
    WIPE = "wipe"
    EXPANSION = "expansion"
    DLC = "dlc"
    EXPEDITION = "expedition"
    BETA = "beta"
    PLAYTEST = "playtest"
    FREE_WEEKEND = "free_weekend"
    TRAILER = "trailer"
    SHOWCASE = "showcase"
    PLATFORM_RELEASE = "platform_release"
    MAJOR_IN_GAME_EVENT = "major_in_game_event"


class GameEvent(FrozenModel):
    canonical_game_id: GameId
    event_type: EventType
    title: NonEmptyStr
    starts_on: date
    ends_on: date | None = None
    source: NonEmptyStr
    reference: str | None = None
