"""A local, append-only store of what event sources returned and how it was read.

    {root}/event-source-v1/fetches/{observed day}/{content hash}.ndjson
    {root}/event-source-v1/items/{observed day}/{content hash}.ndjson
    {root}/event-source-v1/candidates/{observed day}/{content hash}.ndjson

Separate from `events.store.EventStore` on purpose. That store holds only promoted
`EventObservation`s and coverage checks, and it is all Event Heat ever reads. This one
holds every announcement and every candidate, promoted or not. Nothing in the event
engine reads it, so a candidate that was refused cannot reach Event Heat by being in
the wrong place.

A file is one batch, named by the hash of its content and created once. Nothing is
rewritten or deleted:

- a post is kept once per distinct content. Fetching it again unchanged adds nothing;
- a post whose content changed (an edited title, say) is a new version beside the old;
- a candidate is a reading of one version under one version of the rules. A new
  version of the post, or of the rules, gives a new candidate; the old one stays.

Every read takes an `as_of`, and uses only what had been observed by then.

Storage is policy-aware: an item is refused if it carries a field its source's policy
does not allow to be kept. It is refused, not trimmed, because trimming would store
something other than what was observed under the same id.
"""

import hashlib
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from pydantic import AwareDatetime, Field, ValidationError

from metaspike.domain.games import GameId, NonEmptyStr
from metaspike.domain.model import FrozenModel
from metaspike.domain.signals import SourceErrorCode
from metaspike.domain.source_policy import SourcePolicy
from metaspike.event_sources.base import (
    EventSourceFetch,
    FetchStatus,
    RejectedEntry,
    SourceGame,
    SourceItem,
)
from metaspike.event_sources.candidates import EventCandidate
from metaspike.storage.base import StorageCorruptionError, StoragePolicyError

STORE_GENERATION = "event-source-v1"
_FETCHES = "fetches"
_ITEMS = "items"
_CANDIDATES = "candidates"

# Which policy field rule governs each stored field of an item that came from a
# source's own text. Structured tags and ids are not on this list: they are not text.
STORED_FIELD_RULES = {"title": "title", "reference": "url", "summary": "contents"}


class FetchRecord(FrozenModel):
    """One request and what came of it. Never the response itself."""

    fetch_id: str = Field(pattern=r"^fetch_[0-9a-f]{32}$")
    source: NonEmptyStr
    game: SourceGame
    # The canonical game the requested id was bound to when the request was made.
    canonical_game_id: GameId | None = None
    endpoint: NonEmptyStr
    parameters: dict[str, int | str]
    observed_at: AwareDatetime
    status: FetchStatus
    error_code: SourceErrorCode | None = None
    http_status: int | None = None
    attempts: int = Field(ge=0)
    items_returned: int = Field(ge=0)
    item_version_ids: tuple[str, ...] = ()
    rejected: tuple[RejectedEntry, ...] = ()
    reported_total: int | None = None
    observed_envelope_fields: tuple[str, ...] = ()
    observed_item_fields: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.status is FetchStatus.OK


def fetch_record(fetch: EventSourceFetch, canonical_game_id: str | None) -> FetchRecord:
    return FetchRecord(
        **fetch.model_dump(exclude={"items"}),
        canonical_game_id=canonical_game_id,
        items_returned=len(fetch.items),
        item_version_ids=tuple(item.item_version_id for item in fetch.items),
    )


def storage_refusal(item: SourceItem, policy: SourcePolicy) -> str | None:
    """Why this item may not be kept under its source's policy, or None if it may."""
    if policy.source != item.source:
        return f"policy is for '{policy.source}' but the item is from '{item.source}'"
    for field, rule in STORED_FIELD_RULES.items():
        if getattr(item, field) is not None and not policy.field_retention_permitted(rule):
            return f"policy does not permit keeping the source's '{rule}' field"
    return None


_Model = TypeVar("_Model", bound=FrozenModel)


class EventSourceStore:
    def __init__(self, root: Path) -> None:
        self._root = root / STORE_GENERATION

    def _write(self, area: str, by_day: dict[str, list[str]]) -> None:
        for day, lines in sorted(by_day.items()):
            body = ("\n".join(lines) + "\n").encode("utf-8")
            name = hashlib.sha256(body).hexdigest()[:32]
            path = self._root / area / day / f"{name}.ndjson"
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("xb") as handle:  # "x": never overwrite
                    handle.write(body)
            except FileExistsError:
                continue

    def _read(self, area: str, model: type[_Model]) -> list[_Model]:
        directory = self._root / area
        if not directory.is_dir():
            return []
        try:
            return [
                model.model_validate_json(line)
                for path in sorted(directory.glob("*/*.ndjson"))
                for line in path.read_text(encoding="utf-8").splitlines()
                if line
            ]
        except ValidationError as error:
            raise StorageCorruptionError(
                f"stored event-source {area} are unreadable: {error}"
            ) from None

    @staticmethod
    def _day(moment: datetime) -> str:
        return moment.astimezone(UTC).date().isoformat()

    # --- fetches ------------------------------------------------------------------

    def record_fetches(self, records: Iterable[FetchRecord]) -> int:
        known = {record.fetch_id for record in self.fetches()}
        by_day: dict[str, list[str]] = {}
        added = 0
        for record in sorted(records, key=lambda r: (r.observed_at, r.fetch_id)):
            if record.fetch_id in known:
                continue
            known.add(record.fetch_id)
            by_day.setdefault(self._day(record.observed_at), []).append(record.model_dump_json())
            added += 1
        self._write(_FETCHES, by_day)
        return added

    def fetches(self, as_of: datetime | None = None) -> tuple[FetchRecord, ...]:
        loaded = self._read(_FETCHES, FetchRecord)
        kept = [record for record in loaded if as_of is None or record.observed_at <= as_of]
        return tuple(sorted(kept, key=lambda r: (r.observed_at, r.fetch_id)))

    # --- items --------------------------------------------------------------------

    def add_items(
        self, items: Iterable[SourceItem], policy: SourcePolicy
    ) -> tuple[SourceItem, ...]:
        """Keep each item whose content differs from the latest version already held.

        Returns the versions that were new, in observed order. Raises
        `StoragePolicyError`, storing nothing, if any item may not be kept.
        """
        incoming = sorted(items, key=lambda item: (item.observed_at, item.item_version_id))
        for item in incoming:
            refusal = storage_refusal(item, policy)
            if refusal is not None:
                raise StoragePolicyError(f"{item.source}: {refusal}")
        latest = {item.key: item for item in self.items()}
        known = {item.item_version_id for item in self.items()}
        fresh: list[SourceItem] = []
        by_day: dict[str, list[str]] = {}
        for item in incoming:
            current = latest.get(item.key)
            if item.item_version_id in known:
                continue
            if current is not None and current.content_id == item.content_id:
                continue  # seen again, unchanged
            latest[item.key] = item
            known.add(item.item_version_id)
            fresh.append(item)
            by_day.setdefault(self._day(item.observed_at), []).append(item.model_dump_json())
        self._write(_ITEMS, by_day)
        return tuple(fresh)

    def items(self, as_of: datetime | None = None) -> tuple[SourceItem, ...]:
        """Every version of every item observed by `as_of`, in observed order."""
        loaded = self._read(_ITEMS, SourceItem)
        kept = [item for item in loaded if as_of is None or item.observed_at <= as_of]
        return tuple(sorted(kept, key=lambda item: (item.observed_at, item.item_version_id)))

    def latest_items(self, as_of: datetime | None = None) -> tuple[SourceItem, ...]:
        """The current version of each item, as known at `as_of`."""
        latest: dict[tuple[str, str], SourceItem] = {}
        for item in self.items(as_of):
            latest[item.key] = item
        return tuple(sorted(latest.values(), key=lambda item: (item.published_at, item.key)))

    def versions_of(self, source: str, source_item_id: str) -> tuple[SourceItem, ...]:
        return tuple(item for item in self.items() if item.key == (source, source_item_id))

    # --- candidates ---------------------------------------------------------------

    def add_candidates(self, candidates: Iterable[EventCandidate]) -> int:
        known = {candidate.candidate_id for candidate in self.candidates()}
        by_day: dict[str, list[str]] = {}
        added = 0
        for candidate in sorted(candidates, key=_candidate_order):
            if candidate.candidate_id in known:
                continue
            known.add(candidate.candidate_id)
            by_day.setdefault(self._day(candidate.observed_at), []).append(
                candidate.model_dump_json()
            )
            added += 1
        self._write(_CANDIDATES, by_day)
        return added

    def candidates(self, as_of: datetime | None = None) -> tuple[EventCandidate, ...]:
        """Every candidate made from what was observed by `as_of`."""
        loaded = self._read(_CANDIDATES, EventCandidate)
        kept = [item for item in loaded if as_of is None or item.observed_at <= as_of]
        return tuple(sorted(kept, key=_candidate_order))

    def latest_candidates(
        self, as_of: datetime | None = None, rules_version: str | None = None
    ) -> tuple[EventCandidate, ...]:
        """The current reading of each item, as known at `as_of`.

        With `rules_version`, only readings made under that version of the rules.
        """
        latest: dict[tuple[str, str], EventCandidate] = {}
        for candidate in self.candidates(as_of):
            if rules_version is None or candidate.rules_version == rules_version:
                latest[candidate.key] = candidate
        return tuple(sorted(latest.values(), key=lambda c: (c.announced_at, c.key)))


def _candidate_order(candidate: EventCandidate) -> tuple[datetime, str, str]:
    return candidate.observed_at, candidate.rules_version, candidate.candidate_id
