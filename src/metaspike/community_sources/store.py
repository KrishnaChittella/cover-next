"""A local, append-only store of listing pages, read as of a moment.

    {root}/listings/{retrieved day}/{content hash}.ndjson   one page per line

Created once, never rewritten. A reading uses only pages retrieved by its moment. What
has passed the retention limit is listed by `expired`; nothing deletes it yet, and a
deletion job (including for content later deleted on Reddit) is a condition of any
real collection.
"""

import hashlib
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from metaspike.community_sources.reddit import RETENTION_HOURS, ListingFetch
from metaspike.storage.base import StorageCorruptionError

_LISTINGS = "listings"


class CommunityStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def add_listings(self, fetches: Iterable[ListingFetch]) -> int:
        """Keep pages. Returns how many were new; a repeat adds nothing."""
        known = {fetch.fetch_id for fetch in self.listings()}
        fresh = [fetch for fetch in fetches if fetch.fetch_id not in known]
        by_day: dict[str, list[str]] = {}
        for fetch in sorted(fresh, key=lambda f: (f.retrieved_at, f.page_index, f.fetch_id)):
            day = fetch.retrieved_at.astimezone(UTC).date().isoformat()
            by_day.setdefault(day, []).append(fetch.model_dump_json())
        for day, lines in sorted(by_day.items()):
            body = ("\n".join(lines) + "\n").encode("utf-8")
            path = self._root / _LISTINGS / day / f"{hashlib.sha256(body).hexdigest()[:32]}.ndjson"
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("xb") as handle:
                    handle.write(body)
            except FileExistsError:
                pass
        return len(fresh)

    def listings(self, as_of: datetime | None = None) -> tuple[ListingFetch, ...]:
        """Every page retrieved by `as_of`, in retrieval order."""
        directory = self._root / _LISTINGS
        lines = (
            []
            if not directory.is_dir()
            else [
                line
                for path in sorted(directory.glob("*/*.ndjson"))
                for line in path.read_text(encoding="utf-8").splitlines()
                if line
            ]
        )
        try:
            loaded = [ListingFetch.model_validate_json(line) for line in lines]
        except ValidationError as error:
            raise StorageCorruptionError(f"stored listings are unreadable: {error}") from None
        kept = [f for f in loaded if as_of is None or f.retrieved_at <= as_of]
        return tuple(sorted(kept, key=lambda f: (f.retrieved_at, f.page_index, f.fetch_id)))

    def expired(self, now: datetime) -> tuple[str, ...]:
        limit = now - timedelta(hours=RETENTION_HOURS)
        return tuple(sorted(f.fetch_id for f in self.listings(now) if f.retrieved_at < limit))
