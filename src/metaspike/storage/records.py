"""Local store for source-native catalogue records, kept apart from canonical identity.

One append-only JSON Lines file per source. A record is appended only when the
source's statement about the product changes (a new content hash), so the file is a
point-in-time history of what each source said and when we first saw it.
"""

import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.source_policy import DeploymentStage, SourcePolicy, SourcePurpose
from metaspike.domain.source_records import SourceRecord
from metaspike.storage.base import StorageCorruptionError, StoragePolicyError

_SAFE_SOURCE = re.compile(r"^[a-z0-9_]+$")


class LocalSourceRecordStore:
    def __init__(
        self,
        root: Path,
        policy_for: Callable[[str], SourcePolicy],
        stage: DeploymentStage = DeploymentStage.PRIVATE_DEVELOPMENT,
    ) -> None:
        self._root = root
        self._policy_for = policy_for
        self._stage = stage

    def write(self, records: list[SourceRecord]) -> int:
        """Append new and changed records. Returns how many versions were kept."""
        kept = 0
        for source in sorted({record.key.source for record in records}):
            refusal = self._policy_for(source).registration_refusal(
                SourcePurpose.CATALOGUE_METADATA, self._stage
            )
            if refusal is not None:
                raise StoragePolicyError(f"{source}: {refusal}")
            latest = {key: record.content_hash for key, record in self._latest(source).items()}
            fresh = []
            for record in records:
                if record.key.source == source and latest.get(record.key) != record.content_hash:
                    latest[record.key] = record.content_hash
                    fresh.append(record)
            if fresh:
                path = self._path(source)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as stream:
                    stream.write("".join(record.model_dump_json() + "\n" for record in fresh))
            kept += len(fresh)
        return kept

    def get(self, key: SourceRecordKey, as_of: datetime | None = None) -> SourceRecord | None:
        """The record as it was last seen at `as_of` (now if omitted)."""
        return self._latest(key.source, as_of).get(key)

    def history(self, key: SourceRecordKey) -> tuple[SourceRecord, ...]:
        return tuple(record for record in self._read(key.source) if record.key == key)

    def all(self, source: str, as_of: datetime | None = None) -> tuple[SourceRecord, ...]:
        return tuple(self._latest(source, as_of).values())

    def _latest(
        self, source: str, as_of: datetime | None = None
    ) -> dict[SourceRecordKey, SourceRecord]:
        latest: dict[SourceRecordKey, SourceRecord] = {}
        for record in self._read(source):
            if as_of is None or record.fetched_at <= as_of:
                latest[record.key] = record  # file order is write order
        return latest

    def _path(self, source: str) -> Path:
        if not _SAFE_SOURCE.fullmatch(source):
            raise ValueError(f"unsafe source name: {source!r}")
        return self._root / "records" / f"{source}.jsonl"

    def _read(self, source: str) -> list[SourceRecord]:
        path = self._path(source)
        if not path.is_file():
            return []
        records = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            try:
                records.append(SourceRecord.model_validate_json(line))
            except ValidationError:
                raise StorageCorruptionError(
                    f"{path.name}: line {number} is not a valid source record"
                ) from None
        return records
