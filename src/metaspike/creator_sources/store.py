"""A local, append-only store of what YouTube was asked and what it answered.

    {root}/searches/{retrieved day}/{content hash}.ndjson   one search page per line
    {root}/videos/{retrieved day}/{content hash}.ndjson     one videos.list call per line

A file is one batch, named by the hash of its content and created once; nothing is
rewritten. Reading "as of" a moment returns only what had been retrieved by then: a
search answers for the moment it was asked, so a later answer says nothing about
earlier, whatever dates its videos carry.

Retention. YouTube's Developer Policies allow non-authorised API Data to be kept for no
more than 30 calendar days before it is deleted or refreshed. `expired` lists what has
passed that age. Nothing has ever been collected, so nothing deletes yet: a deletion
job is a condition of real collection (see docs/youtube-creator-supply.md).
"""

import hashlib
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from metaspike.creator_sources.youtube import RETENTION_DAYS, SearchFetch, VideosFetch
from metaspike.storage.base import StorageCorruptionError

_SEARCHES = "searches"
_VIDEOS = "videos"
_Fetch = TypeVar("_Fetch", SearchFetch, VideosFetch)


class CreatorSupplyStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def _append(self, area: str, fetches: Iterable[_Fetch], known: set[str]) -> int:
        fresh = [fetch for fetch in fetches if fetch.fetch_id not in known]
        by_day: dict[str, list[str]] = {}
        for fetch in sorted(fresh, key=lambda f: (f.retrieved_at, f.fetch_id)):
            day = fetch.retrieved_at.astimezone(UTC).date().isoformat()
            by_day.setdefault(day, []).append(fetch.model_dump_json())
        for day, lines in sorted(by_day.items()):
            body = ("\n".join(lines) + "\n").encode("utf-8")
            path = self._root / area / day / f"{hashlib.sha256(body).hexdigest()[:32]}.ndjson"
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("xb") as handle:  # "x": never overwrite
                    handle.write(body)
            except FileExistsError:
                pass
        return len(fresh)

    def _lines(self, area: str) -> list[str]:
        directory = self._root / area
        if not directory.is_dir():
            return []
        return [
            line
            for path in sorted(directory.glob("*/*.ndjson"))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]

    def add_searches(self, fetches: Iterable[SearchFetch]) -> int:
        """Keep search pages. Returns how many were new; a repeat adds nothing."""
        return self._append(_SEARCHES, fetches, {f.fetch_id for f in self.searches()})

    def add_videos(self, fetches: Iterable[VideosFetch]) -> int:
        return self._append(_VIDEOS, fetches, {f.fetch_id for f in self.videos()})

    def searches(self, as_of: datetime | None = None) -> tuple[SearchFetch, ...]:
        """Every search page retrieved by `as_of`, in the order they were retrieved."""
        try:
            loaded = [SearchFetch.model_validate_json(line) for line in self._lines(_SEARCHES)]
        except ValidationError as error:
            raise StorageCorruptionError(f"stored searches are unreadable: {error}") from None
        kept = [f for f in loaded if as_of is None or f.retrieved_at <= as_of]
        return tuple(sorted(kept, key=lambda f: (f.retrieved_at, f.page_index, f.fetch_id)))

    def videos(self, as_of: datetime | None = None) -> tuple[VideosFetch, ...]:
        try:
            loaded = [VideosFetch.model_validate_json(line) for line in self._lines(_VIDEOS)]
        except ValidationError as error:
            raise StorageCorruptionError(f"stored video details are unreadable: {error}") from None
        kept = [f for f in loaded if as_of is None or f.retrieved_at <= as_of]
        return tuple(sorted(kept, key=lambda f: (f.retrieved_at, f.fetch_id)))

    def expired(self, now: datetime) -> tuple[str, ...]:
        """Fetch ids older than the retention limit at `now`. They must go or be refreshed."""
        limit = now - timedelta(days=RETENTION_DAYS)
        everything: list[SearchFetch | VideosFetch] = [*self.searches(now), *self.videos(now)]
        return tuple(sorted(f.fetch_id for f in everything if f.retrieved_at < limit))
