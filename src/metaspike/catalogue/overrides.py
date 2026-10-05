"""Explicit identity overrides: domain knowledge the sources cannot express.

An override is the exception, never the routine path. Each one is versioned, names
exactly one source record, and states its reason. The resolver consults overrides
before any generic rule, so an override can admit a record the generic rules would
turn away, but it can never do so for a record it does not name.
"""

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from metaspike.catalogue.legacy_ids import canonical_id_for_legacy
from metaspike.domain.games import GameId, NonEmptyStr, RelationshipType
from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.model import FrozenModel


class OverrideAction(StrEnum):
    # Make this record a canonical entity although its product type would not qualify.
    ADMIT_ENTITY = "admit_entity"
    # Bind this record to an existing canonical entity.
    BIND_TO_ENTITY = "bind_to_entity"


class IdentityOverride(FrozenModel):
    ref: NonEmptyStr
    record: SourceRecordKey
    action: OverrideAction
    canonical_game_id: GameId
    relationship_type: RelationshipType | None = None
    related_game_id: GameId | None = None
    reason: NonEmptyStr
    added_in_version: int = Field(ge=1)

    @model_validator(mode="after")
    def _relationship_is_complete(self) -> Self:
        if (self.relationship_type is None) != (self.related_game_id is None):
            raise ValueError("an override names a relationship type and its entity together")
        if self.related_game_id == self.canonical_game_id:
            raise ValueError("an override cannot relate an entity to itself")
        return self


class OverrideSet(FrozenModel):
    version: int = Field(ge=0)
    overrides: tuple[IdentityOverride, ...] = ()

    @model_validator(mode="after")
    def _one_override_per_record(self) -> Self:
        records = [override.record for override in self.overrides]
        if len(records) != len(set(records)):
            raise ValueError("a source record has more than one override")
        refs = [override.ref for override in self.overrides]
        if len(refs) != len(set(refs)):
            raise ValueError("two overrides share a reference")
        if any(override.added_in_version > self.version for override in self.overrides):
            raise ValueError("an override is newer than the set that contains it")
        return self

    def for_record(self, key: SourceRecordKey) -> IdentityOverride | None:
        for override in self.overrides:
            if override.record == key:
                return override
        return None


NO_OVERRIDES = OverrideSet(version=0)

# IGDB records FiveM as a Mod of Grand Theft Auto V. Mods are not canonical entities,
# but the FiveM ecosystem is a product-defining exception: it has its own audience,
# its own events and its own demand, distinct from GTA V and from GTA Online.
FIVEM_OVERRIDE = IdentityOverride(
    ref="override:fivem-mod-ecosystem",
    record=SourceRecordKey(source="igdb", source_id="146553"),
    action=OverrideAction.ADMIT_ENTITY,
    canonical_game_id=canonical_id_for_legacy("msg_0003"),
    relationship_type=RelationshipType.MOD_ECOSYSTEM,
    related_game_id=canonical_id_for_legacy("msg_0001"),
    reason=(
        "FiveM is a canonical MetaSpike ecosystem entity by product decision. The "
        "relationship to Grand Theft Auto V is domain knowledge that upstream sources "
        "express only as 'mod of', which would otherwise exclude it."
    ),
    added_in_version=1,
)

PRODUCTION_OVERRIDES = OverrideSet(version=1, overrides=(FIVEM_OVERRIDE,))
