"""Canonical identity: ids, aliases, lineage, and versioned source bindings.

A `canonical_game_id` is an opaque UUIDv4. It is never derived from a title or from
any source's identifier, and it never changes. Everything else about identity is
history layered on top of it:

- an alias makes an old or retired id resolve to the id that now stands for it
- lineage records why an id was retired (merge) or created from another (split)
- a binding says which source record belongs to which canonical entity, and when

All of it is append-only and point-in-time, so a past resolution can be replayed
exactly as it was known then.
"""

import uuid
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, model_validator

from metaspike.domain.games import GameId, NonEmptyStr
from metaspike.domain.model import FrozenModel

_MAX_ALIAS_HOPS = 32


class IdentityError(Exception):
    """An identity operation would corrupt canonical identity."""


def new_canonical_id(factory: Callable[[], uuid.UUID] = uuid.uuid4) -> str:
    """A fresh canonical id. Tests pass a deterministic factory."""
    value = factory()
    if value.version != 4:
        raise IdentityError("canonical ids must be UUID version 4")
    return str(value)


class AliasKind(StrEnum):
    LEGACY = "legacy"  # an id from a retired id format
    MERGED = "merged"  # an entity that was merged into another


class IdentityAlias(FrozenModel):
    alias: NonEmptyStr
    canonical_game_id: GameId
    kind: AliasKind
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None = None
    reason: NonEmptyStr
    decision_ref: str | None = None

    @model_validator(mode="after")
    def _check_alias(self) -> Self:
        if self.alias == self.canonical_game_id:
            raise ValueError("an id cannot be an alias of itself")
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("alias validity must end after it starts")
        return self

    def active_at(self, moment: datetime | None) -> bool:
        if moment is None:
            return self.valid_to is None
        return self.valid_from <= moment and (self.valid_to is None or moment < self.valid_to)


class LineageKind(StrEnum):
    MERGED_INTO = "merged_into"  # subject was retired in favour of related
    SPLIT_FROM = "split_from"  # subject was created out of related


class LineageRecord(FrozenModel):
    kind: LineageKind
    subject_id: GameId
    related_id: GameId
    occurred_at: AwareDatetime
    reason: NonEmptyStr
    decision_ref: str | None = None

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        if self.subject_id == self.related_id:
            raise ValueError("lineage must relate two different entities")
        return self


class IdentityLedger(FrozenModel):
    """Append-only alias and lineage history. Every change returns a new ledger."""

    aliases: tuple[IdentityAlias, ...] = ()
    lineage: tuple[LineageRecord, ...] = ()

    @model_validator(mode="after")
    def _check_ledger(self) -> Self:
        for index, alias in enumerate(self.aliases):
            for other in self.aliases[index + 1 :]:
                if alias.alias == other.alias and _overlap(alias, other):
                    raise ValueError(f"alias '{alias.alias}' resolves to two ids at once")
        for alias in self.aliases:
            self._walk(alias.alias, alias.valid_from)  # raises on a cycle
        return self

    def resolve(self, identifier: str, as_of: datetime | None = None) -> str:
        """The terminal canonical id `identifier` stands for (itself if it is not an alias).

        `as_of` resolves as the ledger stood at that moment; None means now.
        """
        return self._walk(identifier, as_of)

    def _walk(self, identifier: str, as_of: datetime | None) -> str:
        seen = [identifier]
        current = identifier
        for _ in range(_MAX_ALIAS_HOPS):
            target = next(
                (
                    alias.canonical_game_id
                    for alias in self.aliases
                    if alias.alias == current and alias.active_at(as_of)
                ),
                None,
            )
            if target is None:
                return current
            if target in seen:
                raise ValueError(f"alias cycle: {' -> '.join([*seen, target])}")
            seen.append(target)
            current = target
        raise ValueError(f"alias chain too long starting at '{identifier}'")

    def aliases_of(self, canonical_game_id: str, as_of: datetime | None = None) -> tuple[str, ...]:
        """Every id that resolves to `canonical_game_id`, directly or through a chain."""
        return tuple(
            sorted(
                {
                    alias.alias
                    for alias in self.aliases
                    if alias.active_at(as_of)
                    and self.resolve(alias.alias, as_of) == canonical_game_id
                }
            )
        )

    def is_retired(self, identifier: str, as_of: datetime | None = None) -> bool:
        return self.resolve(identifier, as_of) != identifier

    def with_aliases(self, *aliases: IdentityAlias) -> Self:
        return self._extended(aliases=(*self.aliases, *aliases))

    def merge(
        self,
        loser: str,
        survivor: str,
        at: datetime,
        reason: str,
        decision_ref: str | None = None,
    ) -> Self:
        """Retire `loser` in favour of `survivor`. Nothing is deleted or rewritten."""
        if self.is_retired(loser, at) or self.is_retired(survivor, at):
            raise IdentityError("both sides of a merge must be current canonical entities")
        if loser == survivor:
            raise IdentityError("an entity cannot be merged into itself")
        return self._extended(
            aliases=(
                *self.aliases,
                IdentityAlias(
                    alias=loser,
                    canonical_game_id=survivor,
                    kind=AliasKind.MERGED,
                    valid_from=at,
                    reason=reason,
                    decision_ref=decision_ref,
                ),
            ),
            lineage=(
                *self.lineage,
                LineageRecord(
                    kind=LineageKind.MERGED_INTO,
                    subject_id=loser,
                    related_id=survivor,
                    occurred_at=at,
                    reason=reason,
                    decision_ref=decision_ref,
                ),
            ),
        )

    def split(
        self,
        origin: str,
        new_id: str,
        at: datetime,
        reason: str,
        decision_ref: str | None = None,
    ) -> Self:
        """Record that `new_id` was created out of `origin`, which keeps its own id."""
        if self.is_retired(origin, at):
            raise IdentityError("cannot split an entity that has been retired")
        if any(new_id in (alias.alias, alias.canonical_game_id) for alias in self.aliases):
            raise IdentityError("a split must produce an id that has never been used")
        return self._extended(
            lineage=(
                *self.lineage,
                LineageRecord(
                    kind=LineageKind.SPLIT_FROM,
                    subject_id=new_id,
                    related_id=origin,
                    occurred_at=at,
                    reason=reason,
                    decision_ref=decision_ref,
                ),
            )
        )

    def lineage_of(self, canonical_game_id: str) -> tuple[LineageRecord, ...]:
        return tuple(
            record
            for record in self.lineage
            if canonical_game_id in (record.subject_id, record.related_id)
        )

    def _extended(
        self,
        aliases: tuple[IdentityAlias, ...] | None = None,
        lineage: tuple[LineageRecord, ...] | None = None,
    ) -> Self:
        try:
            return type(self)(
                aliases=self.aliases if aliases is None else aliases,
                lineage=self.lineage if lineage is None else lineage,
            )
        except ValueError as error:
            raise IdentityError(str(error)) from None


def _overlap(first: IdentityAlias, second: IdentityAlias) -> bool:
    first_end = first.valid_to
    second_end = second.valid_to
    return (second_end is None or first.valid_from < second_end) and (
        first_end is None or second.valid_from < first_end
    )


class SourceRecordKey(FrozenModel):
    """A source's own identity for one of its records. Never a canonical id."""

    source: NonEmptyStr
    source_id: NonEmptyStr


class SourceBinding(FrozenModel):
    """One version of the statement 'this source record is that canonical entity'."""

    key: SourceRecordKey
    canonical_game_id: GameId
    mapping_version: int
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None = None
    decision_ref: NonEmptyStr

    def active_at(self, moment: datetime | None) -> bool:
        if moment is None:
            return self.valid_to is None
        return self.valid_from <= moment and (self.valid_to is None or moment < self.valid_to)


class MappingHistory(FrozenModel):
    """Append-only, point-in-time history of source-record bindings."""

    bindings: tuple[SourceBinding, ...] = ()

    @model_validator(mode="after")
    def _one_active_binding_per_record(self) -> Self:
        for index, binding in enumerate(self.bindings):
            for other in self.bindings[index + 1 :]:
                if binding.key == other.key and _bindings_overlap(binding, other):
                    raise ValueError(
                        f"{binding.key.source}:{binding.key.source_id} is bound twice at once"
                    )
        return self

    @property
    def version(self) -> int:
        return max((binding.mapping_version for binding in self.bindings), default=0)

    def canonical_for(self, key: SourceRecordKey, as_of: datetime | None = None) -> str | None:
        for binding in self.bindings:
            if binding.key == key and binding.active_at(as_of):
                return binding.canonical_game_id
        return None

    def records_for(
        self, canonical_game_id: str, as_of: datetime | None = None
    ) -> tuple[SourceRecordKey, ...]:
        return tuple(
            binding.key
            for binding in self.bindings
            if binding.canonical_game_id == canonical_game_id and binding.active_at(as_of)
        )

    def history_of(self, key: SourceRecordKey) -> tuple[SourceBinding, ...]:
        return tuple(binding for binding in self.bindings if binding.key == key)

    def bind(
        self, key: SourceRecordKey, canonical_game_id: str, at: datetime, decision_ref: str
    ) -> Self:
        """Bind a record, closing any binding it had. The old binding stays in history."""
        current = next((b for b in self.bindings if b.key == key and b.valid_to is None), None)
        if current is not None and current.canonical_game_id == canonical_game_id:
            return self
        if current is not None and at <= current.valid_from:
            raise IdentityError("a rebinding must be later than the binding it replaces")
        kept = tuple(
            binding.model_copy(update={"valid_to": at}) if binding is current else binding
            for binding in self.bindings
        )
        return type(self)(
            bindings=(
                *kept,
                SourceBinding(
                    key=key,
                    canonical_game_id=canonical_game_id,
                    mapping_version=self.version + 1,
                    valid_from=at,
                    decision_ref=decision_ref,
                ),
            )
        )

    def unbind(self, key: SourceRecordKey, at: datetime) -> Self:
        """Close a record's binding without replacing it (the record becomes unresolved)."""
        return type(self)(
            bindings=tuple(
                binding.model_copy(update={"valid_to": at})
                if binding.key == key and binding.valid_to is None
                else binding
                for binding in self.bindings
            )
        )


def _bindings_overlap(first: SourceBinding, second: SourceBinding) -> bool:
    return (second.valid_to is None or first.valid_from < second.valid_to) and (
        first.valid_to is None or second.valid_from < first.valid_to
    )
