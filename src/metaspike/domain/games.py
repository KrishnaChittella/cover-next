"""Canonical game entities and the catalogue that holds them."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, StringConstraints, model_validator

from metaspike.domain.model import FrozenModel

# Opaque and stable: a lowercase UUIDv4. A game keeps its id even if its slug, name or
# source identifiers change. It is never derived from a title or a source's id.
CANONICAL_ID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
GameId = Annotated[str, StringConstraints(pattern=CANONICAL_ID_PATTERN)]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
NonEmptyStr = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class LifecycleState(StrEnum):
    PRE_RELEASE = "PRE_RELEASE"
    LAUNCH_WINDOW = "LAUNCH_WINDOW"
    RELEASED = "RELEASED"
    EVERGREEN = "EVERGREEN"
    LIVE_SERVICE = "LIVE_SERVICE"
    DORMANT = "DORMANT"


class RelationshipType(StrEnum):
    MOD_ECOSYSTEM = "MOD_ECOSYSTEM"
    ONLINE_COMPONENT = "ONLINE_COMPONENT"
    SEQUEL = "SEQUEL"


class SourceIdentifier(FrozenModel):
    """How one source refers to a game, e.g. an article title on a given wiki."""

    source: NonEmptyStr
    namespace: NonEmptyStr
    value: NonEmptyStr


class GameRelationship(FrozenModel):
    parent_game_id: GameId
    relationship_type: RelationshipType


class CanonicalGame(FrozenModel):
    canonical_game_id: GameId
    slug: Slug
    name: NonEmptyStr
    aliases: tuple[NonEmptyStr, ...] = ()
    source_identifiers: tuple[SourceIdentifier, ...] = ()
    relationships: tuple[GameRelationship, ...] = ()
    lifecycle_state: LifecycleState

    @model_validator(mode="after")
    def _check_game(self) -> Self:
        sources = [identifier.source for identifier in self.source_identifiers]
        if len(sources) != len(set(sources)):
            raise ValueError(f"{self.slug}: more than one identifier for the same source")
        parents = [relationship.parent_game_id for relationship in self.relationships]
        if self.canonical_game_id in parents:
            raise ValueError(f"{self.slug}: a game cannot be its own parent")
        if len(parents) != len(set(parents)):
            raise ValueError(f"{self.slug}: duplicate relationship to the same parent")
        return self

    def identifier_for(self, source: str) -> SourceIdentifier | None:
        for identifier in self.source_identifiers:
            if identifier.source == source:
                return identifier
        return None


class Catalogue(FrozenModel):
    games: tuple[CanonicalGame, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_catalogue(self) -> Self:
        _require_unique("canonical_game_id", [g.canonical_game_id for g in self.games])
        _require_unique("slug", [g.slug for g in self.games])
        _require_unique(
            "source identifier",
            [
                f"{i.source}:{i.namespace}:{i.value}"
                for game in self.games
                for i in game.source_identifiers
            ],
        )
        parents_of = {
            game.canonical_game_id: [r.parent_game_id for r in game.relationships]
            for game in self.games
        }
        for game_id, parents in parents_of.items():
            for parent in parents:
                if parent not in parents_of:
                    raise ValueError(f"{game_id}: unknown parent_game_id {parent}")
        _require_acyclic(parents_of)
        return self

    def get(self, canonical_game_id: str) -> CanonicalGame:
        for game in self.games:
            if game.canonical_game_id == canonical_game_id:
                return game
        raise KeyError(canonical_game_id)


def _require_unique(label: str, values: list[str]) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate {label}: {value}")
        seen.add(value)


def _require_acyclic(parents_of: dict[str, list[str]]) -> None:
    resolved: set[str] = set()

    def visit(game_id: str, path: tuple[str, ...]) -> None:
        if game_id in path:
            raise ValueError(f"relationship cycle: {' -> '.join((*path, game_id))}")
        if game_id in resolved:
            return
        for parent in parents_of[game_id]:
            visit(parent, (*path, game_id))
        resolved.add(game_id)

    for game_id in parents_of:
        visit(game_id, ())
