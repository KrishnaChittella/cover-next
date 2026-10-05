"""Local filesystem observation store: one append-only JSON Lines file per signal.

    {root}/{source}/{metric}/{granularity}/{canonical_game_id}.jsonl

It exists for development and tests. The warehouse implementation will satisfy the
same `ObservationStore` contract.
"""

import json
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from metaspike.domain.identity import IdentityLedger
from metaspike.domain.observations import Observation, TimeSeries
from metaspike.domain.signals import SignalKey
from metaspike.domain.source_policy import SourcePolicy
from metaspike.domain.timeseries import Granularity, TimeWindow
from metaspike.pipeline.normalize import build_time_series
from metaspike.storage.base import StorageCorruptionError, StoragePolicyError

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]+$")


class LocalObservationStore:
    def __init__(
        self,
        root: Path,
        policy_for: Callable[[str], SourcePolicy],
        identity: IdentityLedger | None = None,
    ) -> None:
        self._root = root
        self._policy_for = policy_for
        # Lets history written under a retired id be read under the id that replaced it.
        self._identity = identity or IdentityLedger()

    def write(self, series: TimeSeries) -> int:
        policy = self._policy_for(series.source)
        if not policy.field_storage_permitted(series.metric):
            raise StoragePolicyError(
                f"{series.source}: policy does not permit storing '{series.metric}' history"
            )
        key = SignalKey(source=series.source, metric=series.metric, granularity=series.granularity)
        path = self._path(series.canonical_game_id, key)
        latest = {
            record.period_start: record for record in self._records(path, series.canonical_game_id)
        }
        fresh = [
            point
            for point in series.points
            if point.value is not None
            and (
                point.period_start not in latest or latest[point.period_start].value != point.value
            )
        ]
        if fresh:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write("".join(point.model_dump_json() + "\n" for point in fresh))
        return len(fresh)

    def read(
        self, canonical_game_id: str, key: SignalKey, window: TimeWindow, as_of: datetime
    ) -> TimeSeries:
        canonical = self._identity.resolve(canonical_game_id, as_of)
        # Oldest identity first, so a later write under the current id wins.
        stored_under = (*self._identity.aliases_of(canonical, as_of), canonical)
        known: dict[datetime, Observation] = {}
        for identifier in stored_under:
            for record in self._records(self._path(identifier, key), canonical):
                if record.observed_at <= as_of and window.contains(record.period_start):
                    known[record.period_start] = record  # write order: last version wins
        return build_time_series(canonical, key, window, list(known.values()), as_of)

    def signals(self, canonical_game_id: str) -> tuple[SignalKey, ...]:
        keys = [
            SignalKey(
                source=path.parts[-4],
                metric=path.parts[-3],
                granularity=Granularity(path.parts[-2]),
            )
            for path in self._root.glob(f"*/*/*/{_segment(canonical_game_id)}.jsonl")
        ]
        return tuple(sorted(keys, key=lambda key: (key.source, key.metric, key.granularity.value)))

    def _path(self, canonical_game_id: str, key: SignalKey) -> Path:
        return (
            self._root
            / _segment(key.source)
            / _segment(key.metric)
            / _segment(key.granularity.value)
            / f"{_segment(canonical_game_id)}.jsonl"
        )

    def _records(self, path: Path, canonical_game_id: str) -> list[Observation]:
        """Stored observations, attributed to the canonical id they are being read for.

        A file written under a retired id still carries that id on every line; it is
        replaced on the way in, which is what keeps old history reachable.
        """
        if not path.is_file():
            return []
        records = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            try:
                stored = json.loads(line)
                if not isinstance(stored, dict):
                    raise ValueError("not an object")
                stored["canonical_game_id"] = canonical_game_id
                records.append(Observation.model_validate(stored))
            except (ValidationError, ValueError):
                raise StorageCorruptionError(
                    f"{path.name}: line {number} is not a valid observation"
                ) from None
        return records


def _segment(value: str) -> str:
    """Reject anything that could escape the store's directory."""
    if not _SAFE_SEGMENT.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"unsafe storage path segment: {value!r}")
    return value
