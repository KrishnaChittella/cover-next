"""Quarantine: source records that could not be safely resolved, and nothing more.

A quarantined record is an unresolved SOURCE record. It has no canonical id, is not
scored, ranked or published, and is never merged. It waits, with everything known
about why, until the evidence changes. No person has to act on it: whenever the
evidence that bears on it changes, it is due for re-evaluation automatically.
"""

import hashlib
import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Self

from pydantic import AwareDatetime, Field

from metaspike.catalogue.overrides import OverrideSet
from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.model import FrozenModel
from metaspike.domain.source_records import ContentHashMemo, SourceRecord
from metaspike.resolution.models import (
    CandidateAssessment,
    DecisionKind,
    QuarantineReason,
    ResolutionDecision,
    ResolverInput,
)
from metaspike.resolution.resolver import candidate_refs


def evaluation_fingerprint(
    view: ResolverInput, overrides: OverrideSet, hashes: ContentHashMemo | None = None
) -> str:
    """Changes exactly when something that could change the decision changes:

    - the record itself (its content hash)
    - any candidate entity, or the set of candidates
    - linking evidence from another unresolved record
    - the override that applies to this record

    `hashes` only saves recomputing content hashes this run has already computed. With
    it or without it the payload, and so the fingerprint, is the same string.
    """
    record = view.incoming
    candidates = set(candidate_refs(view))
    override = overrides.for_record(record.key)
    content = _content_hash if hashes is None else hashes.of
    payload = {
        "record": content(record),
        "candidates": sorted(
            [entity.ref, sorted(content(r) for r in entity.records)]
            for entity in view.entities
            if entity.ref in candidates
        ),
        "linking": sorted(content(other) for other in view.context if _links(other, record)),
        "bound_to": view.bound_to,
        "override": None if override is None else [override.ref, override.added_in_version],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _content_hash(record: SourceRecord) -> str:
    return record.content_hash


def _links(other: SourceRecord, record: SourceRecord) -> bool:
    """Whether an unresolved record points at this one, or is pointed at by it."""
    mentions = {link.value for link in other.external_ids} | {
        reference.source_id for reference in (other.parent, other.version_parent) if reference
    }
    mentioned = {link.value for link in record.external_ids} | {
        reference.source_id for reference in (record.parent, record.version_parent) if reference
    }
    return record.key.source_id in mentions or other.key.source_id in mentioned


class QuarantineEntry(FrozenModel):
    record: SourceRecord
    reason: QuarantineReason
    candidates: tuple[CandidateAssessment, ...]
    explanation: str
    first_seen: AwareDatetime
    last_evaluated: AwareDatetime
    evaluations: int = Field(ge=1)
    fingerprint: str
    # References into the decision log, oldest first.
    decision_history: tuple[str, ...]

    def age_days(self, now: datetime) -> float:
        return (now - self.first_seen).total_seconds() / 86400


class QuarantineMetrics(FrozenModel):
    total: int
    by_source: dict[str, int]
    by_reason: dict[str, int]
    oldest_age_days: float | None
    median_age_days: float | None
    older_than_30_days: int


class Quarantine(FrozenModel):
    entries: tuple[QuarantineEntry, ...] = ()

    def get(self, key: SourceRecordKey) -> QuarantineEntry | None:
        for entry in self.entries:
            if entry.record.key == key:
                return entry
        return None

    def is_due(self, key: SourceRecordKey, fingerprint: str) -> bool:
        """A record is due for evaluation if it is new here or its evidence has changed."""
        entry = self.get(key)
        return entry is None or entry.fingerprint != fingerprint

    def put(
        self,
        record: SourceRecord,
        decision: ResolutionDecision,
        at: datetime,
        fingerprint: str,
        decision_ref: str,
    ) -> Self:
        """Record a quarantine decision, keeping when the record was first seen."""
        if decision.kind is not DecisionKind.QUARANTINE or decision.quarantine_reason is None:
            raise ValueError("only a QUARANTINE decision can be stored in quarantine")
        previous = self.get(record.key)
        entry = QuarantineEntry(
            record=record,
            reason=decision.quarantine_reason,
            candidates=decision.candidates,
            explanation=decision.explanation,
            first_seen=at if previous is None else previous.first_seen,
            last_evaluated=at,
            evaluations=1 if previous is None else previous.evaluations + 1,
            fingerprint=fingerprint,
            decision_history=(
                (decision_ref,) if previous is None else (*previous.decision_history, decision_ref)
            ),
        )
        kept = tuple(e for e in self.entries if e.record.key != record.key)
        return type(self)(entries=(*kept, entry))

    def release(self, key: SourceRecordKey) -> Self:
        """Remove a record that has now been resolved. Its history stays in the decision log."""
        return type(self)(entries=tuple(e for e in self.entries if e.record.key != key))

    def metrics(self, now: datetime) -> QuarantineMetrics:
        ages = [entry.age_days(now) for entry in self.entries]
        by_source: dict[str, int] = {}
        by_reason: dict[str, int] = {}
        for entry in self.entries:
            by_source[entry.record.key.source] = by_source.get(entry.record.key.source, 0) + 1
            by_reason[entry.reason.value] = by_reason.get(entry.reason.value, 0) + 1
        return QuarantineMetrics(
            total=len(self.entries),
            by_source=dict(sorted(by_source.items())),
            by_reason=dict(sorted(by_reason.items())),
            oldest_age_days=round(max(ages), 2) if ages else None,
            median_age_days=round(statistics.median(ages), 2) if ages else None,
            older_than_30_days=sum(age > 30 for age in ages),
        )


class LocalQuarantineStore:
    """Quarantine persisted as one JSON file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> Quarantine:
        if not self._path.is_file():
            return Quarantine()
        return Quarantine.model_validate_json(self._path.read_bytes())

    def save(self, quarantine: Quarantine) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.tmp")
        temporary.write_text(quarantine.model_dump_json(indent=1), encoding="utf-8")
        temporary.replace(self._path)
