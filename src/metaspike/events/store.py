"""A local, append-only store of event observations and coverage checks.

    {root}/observations/{observed day}/{content hash}.ndjson
    {root}/coverage/{checked day}/{content hash}.ndjson

A file is one batch, named by the hash of its content and created once. Nothing is
ever rewritten or deleted: a correction is a new observation, and the reconciler turns
the history into the current picture. Reading "as of" a moment uses only what had been
observed by then, so a past evaluation can be reproduced exactly.

This is local and offline. It is laid out so that it maps onto a warehouse later
without change of meaning: one row per observation and one per check, both append-only
(see docs/event-intelligence.md).
"""

import hashlib
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from metaspike.events.model import CoverageCheck, EventObservation, EventRecord
from metaspike.events.reconcile import ordered, reconcile
from metaspike.storage.base import StorageCorruptionError

_OBSERVATIONS = "observations"
_COVERAGE = "coverage"


def _ndjson(lines: list[str]) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


class EventStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _write(self, area: str, day: str, lines: list[str]) -> bool:
        """Create one batch file. False when that exact batch is already there."""
        body = _ndjson(lines)
        name = hashlib.sha256(body).hexdigest()[:32]
        path = self._root / area / day / f"{name}.ndjson"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as handle:  # "x": never overwrite
                handle.write(body)
        except FileExistsError:
            return False
        return True

    def _read(self, area: str) -> list[str]:
        directory = self._root / area
        if not directory.is_dir():
            return []
        lines: list[str] = []
        for path in sorted(directory.glob("*/*.ndjson")):
            lines.extend(line for line in path.read_text(encoding="utf-8").splitlines() if line)
        return lines

    def append(self, observations: Iterable[EventObservation]) -> int:
        """Add observations. Returns how many were new; a repeat adds nothing."""
        known = {item.observation_id for item in self.observations()}
        fresh = [item for item in ordered(observations) if item.observation_id not in known]
        by_day: dict[str, list[str]] = {}
        for item in fresh:
            day = item.observed_at.astimezone(UTC).date().isoformat()
            by_day.setdefault(day, []).append(item.model_dump_json())
        for day, lines in sorted(by_day.items()):
            self._write(_OBSERVATIONS, day, lines)
        return len(fresh)

    def record_checks(self, checks: Iterable[CoverageCheck]) -> int:
        """Add coverage checks. Returns how many were new."""
        known = set(self.checks())
        fresh = sorted(
            {check for check in checks if check not in known},
            key=lambda check: (check.checked_at, check.source, check.canonical_game_id),
        )
        by_day: dict[str, list[str]] = {}
        for check in fresh:
            day = check.checked_at.astimezone(UTC).date().isoformat()
            by_day.setdefault(day, []).append(check.model_dump_json())
        for day, lines in sorted(by_day.items()):
            self._write(_COVERAGE, day, lines)
        return len(fresh)

    def observations(self, as_of: datetime | None = None) -> tuple[EventObservation, ...]:
        """Every observation made by `as_of`, in observed order."""
        try:
            loaded = [
                EventObservation.model_validate_json(line) for line in self._read(_OBSERVATIONS)
            ]
        except ValidationError as error:
            raise StorageCorruptionError(f"event observations are unreadable: {error}") from None
        return tuple(item for item in ordered(loaded) if as_of is None or item.observed_at <= as_of)

    def checks(self, as_of: datetime | None = None) -> tuple[CoverageCheck, ...]:
        try:
            loaded = {CoverageCheck.model_validate_json(line) for line in self._read(_COVERAGE)}
        except ValidationError as error:
            raise StorageCorruptionError(f"event coverage is unreadable: {error}") from None
        kept = [check for check in loaded if as_of is None or check.checked_at <= as_of]
        return tuple(
            sorted(
                kept, key=lambda check: (check.checked_at, check.source, check.canonical_game_id)
            )
        )

    def events(self, as_of: datetime | None = None) -> tuple[EventRecord, ...]:
        """The events the stored observations describe, as known at `as_of`."""
        return reconcile(self.observations(as_of), as_of)
