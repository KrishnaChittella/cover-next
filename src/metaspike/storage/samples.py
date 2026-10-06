"""The storage contract for samples, and its local implementation.

Samples are stored by SOURCE-NATIVE id, never by canonical entity. Which entity a
sample belongs to is answered on read, from the mapping history at the moment the
sample was observed, so a later rebinding can never rewrite what was stored.

The local store is one append-only JSON Lines file per target per UTC month:

    {root}/{source}/{metric}/{source_native_id}/{YYYY-MM}.jsonl

A month is chosen by the attempt's slot, so a bounded window reads only the months
it overlaps. Every line is one attempt, written whole and flushed to disk. If the
process dies mid-write the file ends in a fragment; that fragment is not JSON, so it
is skipped on read and counted, and the next append starts on a fresh line.

Known limit: this suits tens to a few hundred targets. Each append scans the slot's
month file, and there is one directory per target; past roughly 500 targets at a
15-minute cadence the warehouse implementation of the same contract takes over.
"""

import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Protocol

from pydantic import ValidationError

from metaspike.domain.samples import (
    LogicalSlotKey,
    SampleAttempt,
    SlotResolution,
    attempt_order,
    resolve_slot,
)
from metaspike.domain.signals import SignalKey
from metaspike.domain.source_policy import DeploymentStage, SourcePolicy, SourcePurpose
from metaspike.domain.timeseries import TimeWindow
from metaspike.storage.base import StorageCorruptionError, StoragePolicyError

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]+$")


class SampleStore(Protocol):
    def append(self, attempt: SampleAttempt) -> None:
        """Durably record one attempt. Nothing already stored is changed.

        Raises StoragePolicyError if the source's policy does not allow its history
        to be stored at this deployment stage.
        """
        ...

    def attempts(
        self, key: SignalKey, source_native_id: str, window: TimeWindow, as_of: datetime
    ) -> tuple[SampleAttempt, ...]:
        """Every attempt whose slot starts in `window`, as stored by `as_of`."""
        ...

    def resolve(
        self, key: SignalKey, source_native_id: str, slot_start: datetime, as_of: datetime
    ) -> SlotResolution:
        """What one logical slot meant as of a moment; not_sampled if nothing was stored."""
        ...

    def read(
        self, key: SignalKey, source_native_id: str, window: TimeWindow, as_of: datetime
    ) -> tuple[SlotResolution, ...]:
        """One resolution per slot of `window`, in order; unsampled slots are explicit."""
        ...

    def native_ids(self, key: SignalKey) -> tuple[str, ...]:
        """Every source-native id that has at least one stored attempt."""
        ...


class LocalSampleStore:
    def __init__(
        self,
        root: Path,
        policy_for: Callable[[str], SourcePolicy],
        stage: DeploymentStage = DeploymentStage.PRIVATE_DEVELOPMENT,
    ) -> None:
        self._root = root
        self._policy_for = policy_for
        self._stage = stage
        # Fragments left by an interrupted write and skipped by reads so far.
        self.torn_lines_skipped = 0

    def append(self, attempt: SampleAttempt) -> None:
        refusal = self._policy_for(attempt.source).registration_refusal(
            SourcePurpose.DEMAND_SIGNALS, self._stage
        )
        if refusal is not None:
            raise StoragePolicyError(f"{attempt.source}: {refusal}")
        path = self._month_path(
            attempt.source, attempt.metric, attempt.source_native_id, attempt.slot_start
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        line = attempt.model_dump_json() + "\n"
        with path.open("a+b") as stream:
            if _ends_mid_line(stream):
                stream.write(b"\n")  # never extend a fragment left by an interrupted write
            stream.write(line.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())

    def attempts(
        self, key: SignalKey, source_native_id: str, window: TimeWindow, as_of: datetime
    ) -> tuple[SampleAttempt, ...]:
        found = [
            attempt
            for month in _months(window)
            for attempt in self._load(
                self._month_path(key.source, key.metric, source_native_id, month)
            )
            if attempt.received_at <= as_of
            and window.contains(attempt.slot_start)
            and attempt.granularity is key.granularity
        ]
        return tuple(sorted(found, key=attempt_order))

    def resolve(
        self, key: SignalKey, source_native_id: str, slot_start: datetime, as_of: datetime
    ) -> SlotResolution:
        (resolution,) = self.read(
            key, source_native_id, TimeWindow(start=slot_start, end=slot_start), as_of
        )
        return resolution

    def read(
        self, key: SignalKey, source_native_id: str, window: TimeWindow, as_of: datetime
    ) -> tuple[SlotResolution, ...]:
        by_slot: dict[datetime, list[SampleAttempt]] = {}
        for attempt in self.attempts(key, source_native_id, window, as_of):
            by_slot.setdefault(attempt.slot_start, []).append(attempt)
        return tuple(
            resolve_slot(
                LogicalSlotKey(
                    source=key.source,
                    metric=key.metric,
                    source_native_id=source_native_id,
                    slot_start=slot_start,
                ),
                key.granularity,
                tuple(by_slot.get(slot_start, ())),
            )
            for slot_start in window.period_starts(key.granularity)
        )

    def native_ids(self, key: SignalKey) -> tuple[str, ...]:
        directory = self._root / _segment(key.source) / _segment(key.metric)
        if not directory.is_dir():
            return ()
        return tuple(
            sorted(
                (path.name for path in directory.iterdir() if any(path.glob("*.jsonl"))),
                key=lambda name: (len(name), name),
            )
        )

    def _month_path(self, source: str, metric: str, native_id: str, moment: datetime) -> Path:
        month = moment.astimezone(UTC)
        return (
            self._root
            / _segment(source)
            / _segment(metric)
            / _segment(native_id)
            / f"{month:%Y-%m}.jsonl"
        )

    def _load(self, path: Path) -> list[SampleAttempt]:
        if not path.is_file():
            return []
        loaded = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                # A fragment from an interrupted write. A whole line is always valid JSON,
                # so this cannot hide a real record.
                self.torn_lines_skipped += 1
                continue
            try:
                loaded.append(SampleAttempt.model_validate(payload))
            except ValidationError:
                raise StorageCorruptionError(
                    f"{path}: line {number} is not a valid sample attempt"
                ) from None
        return loaded


def _ends_mid_line(stream: IO[bytes]) -> bool:
    stream.seek(0, os.SEEK_END)
    if stream.tell() == 0:
        return False
    stream.seek(-1, os.SEEK_END)
    return bool(stream.read(1) != b"\n")


def _months(window: TimeWindow) -> list[datetime]:
    """The first instant of every UTC month the window touches."""
    start, end = window.start.astimezone(UTC), window.end.astimezone(UTC)
    months = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append(datetime(year, month, 1, tzinfo=UTC))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _segment(value: str) -> str:
    if not _SAFE_SEGMENT.match(value) or value in (".", ".."):
        raise ValueError(f"unsafe path segment: {value!r}")
    return value
