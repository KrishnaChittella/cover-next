"""The boundary every event source sits behind.

An event source returns `SourceItem`s: what the source published, as the source
published it, about a game named the way the source names it. Three things are
deliberately absent from an adapter:

- no classification: an adapter does not say what kind of event an item is;
- no canonical game: it names the source's own id and never a MetaSpike game;
- no scoring: nothing here knows that Event Heat exists.

A fetch always comes back as an `EventSourceFetch`, succeeded or failed. A failure is a
value with an error code and no items; it is never an empty success, because "the
source was asked and has nothing" and "the source could not be asked" must not look
alike further on.
"""

import hashlib
import json
from collections.abc import Iterator
from datetime import datetime
from enum import StrEnum
from typing import Protocol, Self

from pydantic import AwareDatetime, Field, model_validator

from metaspike.domain.games import NonEmptyStr
from metaspike.domain.model import FrozenModel
from metaspike.domain.signals import SourceErrorCode


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(*parts: object) -> str:
    return hashlib.sha256(_canonical(parts).encode("utf-8")).hexdigest()


class SourceGame(FrozenModel):
    """A game as the source names it. Resolving it is not the adapter's business."""

    source: NonEmptyStr
    namespace: NonEmptyStr
    value: NonEmptyStr


class FetchProvenance(FrozenModel):
    """Which request an item came from, without anything that could be a credential."""

    fetch_id: str = Field(pattern=r"^fetch_[0-9a-f]{32}$")
    endpoint: NonEmptyStr
    # The source game the request asked about. An item may name a different one.
    requested_game: SourceGame
    position: int = Field(ge=0)


class ItemContent(FrozenModel):
    """Everything about an item except its ids."""

    source: NonEmptyStr
    game: SourceGame
    source_item_id: NonEmptyStr
    published_at: AwareDatetime
    title: NonEmptyStr
    # Kept only where the source's policy allows the field to be retained.
    summary: str | None = None
    reference: NonEmptyStr | None = None
    # Structured classification data exactly as the source gave it. Never free text.
    feed_name: str | None = None
    feed_label: str | None = None
    feed_type: int | None = None
    tags: tuple[str, ...] = ()
    is_external_reference: bool | None = None
    observed_at: AwareDatetime
    provenance: FetchProvenance


class SourceItem(ItemContent):
    """One thing a source published, as observed at one moment. Immutable.

    `content_id` identifies what was said, whenever it was seen: the same post fetched
    twice has the same content id. `item_version_id` identifies one sighting of it and
    also covers `observed_at`.
    """

    item_version_id: str = Field(pattern=r"^itemv_[0-9a-f]{32}$")
    content_id: str = Field(pattern=r"^itemc_[0-9a-f]{32}$")

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.content_id != content_id(self):
            raise ValueError("content_id does not match the item's content")
        if self.item_version_id != item_version_id(self):
            raise ValueError("item_version_id does not match the item")
        return self

    @property
    def key(self) -> tuple[str, str]:
        """The source's own identity for the item, across every revision of it."""
        return self.source, self.source_item_id


_CONTENT_FIELDS = (
    "source",
    "game",
    "source_item_id",
    "published_at",
    "title",
    "summary",
    "reference",
    "feed_name",
    "feed_label",
    "feed_type",
    "tags",
    "is_external_reference",
)


def _as_data(item: "ItemContent | dict[str, object]") -> dict[str, object]:
    return item.model_dump(mode="json") if isinstance(item, ItemContent) else dict(item)


def content_id(item: "ItemContent | dict[str, object]") -> str:
    data = _as_data(item)
    return f"itemc_{digest({name: data.get(name) for name in _CONTENT_FIELDS})[:32]}"


def item_version_id(item: "ItemContent | dict[str, object]") -> str:
    data = _as_data(item)
    return f"itemv_{digest(content_id(data), data['observed_at'])[:32]}"


def make_item(content: ItemContent) -> SourceItem:
    """Give content its ids. The only way an item should be made."""
    return SourceItem(
        **content.model_dump(),
        content_id=content_id(content),
        item_version_id=item_version_id(content),
    )


class FetchStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"
    # MetaSpike declined to send: its own request budget was spent. Not a source failure.
    NOT_SENT = "not_sent"


class RejectedEntry(FrozenModel):
    """An entry of an otherwise valid response that could not be read. Never a payload."""

    position: int = Field(ge=0)
    reason: NonEmptyStr


class EventSourceFetch(FrozenModel):
    """The outcome of asking a source about one game once."""

    fetch_id: str = Field(pattern=r"^fetch_[0-9a-f]{32}$")
    source: NonEmptyStr
    game: SourceGame
    endpoint: NonEmptyStr
    # The parameters sent, minus anything secret. Enough to audit and to repeat.
    parameters: dict[str, int | str]
    observed_at: AwareDatetime
    status: FetchStatus
    error_code: SourceErrorCode | None = None
    http_status: int | None = None
    # Outbound attempts this fetch spent, a retry included.
    attempts: int = Field(default=0, ge=0)
    items: tuple[SourceItem, ...] = ()
    rejected: tuple[RejectedEntry, ...] = ()
    # What the source says its total is, when it says. Not the number returned.
    reported_total: int | None = None
    # Field names seen, never values: what the response's shape actually was.
    observed_envelope_fields: tuple[str, ...] = ()
    observed_item_fields: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> Self:
        if (self.status is FetchStatus.FAILED) != (self.error_code is not None):
            raise ValueError("a fetch has a source error code exactly when the source failed")
        if self.status is not FetchStatus.OK and self.items:
            raise ValueError("a fetch that did not succeed carries no items")
        return self

    @property
    def succeeded(self) -> bool:
        return self.status is FetchStatus.OK


def fetch_id(source: str, game: SourceGame, parameters: object, observed_at: datetime) -> str:
    moment = observed_at.isoformat()
    return f"fetch_{digest(source, game.model_dump(), parameters, moment)[:32]}"


class EventSourceAdapter(Protocol):
    """A source of announcements about games, named by the source's own ids."""

    @property
    def source(self) -> str: ...

    @property
    def namespace(self) -> str:
        """The kind of id the source names a game by, e.g. "app"."""
        ...

    @property
    def description(self) -> str:
        """One sentence saying exactly what this source returns."""
        ...

    def fetch(self, source_game_id: str, *, before: datetime | None = None) -> EventSourceFetch:
        """Ask once about one game. Never raises for a source failure.

        `before`, where the source documents such a parameter, asks for items published
        earlier than that moment.
        """
        ...

    def backfill(
        self, source_game_id: str, *, max_requests: int, before: datetime | None = None
    ) -> Iterator[EventSourceFetch]:
        """Walk backwards through a game's history, at most `max_requests` requests.

        Stops at the first fetch that did not succeed, returned nothing, or made no
        progress. Each fetch is yielded as it is made, so a caller can stop early.
        """
        ...
