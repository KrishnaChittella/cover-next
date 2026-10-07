"""The catalogue job: fetch cleared catalogue sources, resolve, persist, publish targets.

Each source is a separate portion and each is gated on its own policy. A portion
whose policy does not clear the stage is refused and nothing is fetched from it; the
others proceed. If every portion is refused the job is refused.

Scope is the bootstrap entities and records explicitly linked to them. Broad
catalogue discovery is off and this job has no way to turn it on.

The job cannot query the warehouse, so the evidence it resolves against (the latest
version of every record it has fetched) is kept beside the catalogue in the state
bucket, read and replaced by exact name.
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from metaspike.catalogue.persistence import (
    PREFIX,
    CatalogueRepository,
    CatalogueScope,
    advance_catalogue,
    entity_and_lifecycle_rows,
    land_catalogue_events,
)
from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.model import FrozenModel
from metaspike.domain.source_policy import SourcePurpose
from metaspike.domain.source_records import SourceRecord
from metaspike.jobs.runtime import Refused, Runtime
from metaspike.jobs.targets import publish_targets, targets_for
from metaspike.storage.base import StorageCorruptionError
from metaspike.warehouse.landing import Table, source_record_row
from metaspike.warehouse.objects import ObjectNotFound, PreconditionFailed

EVIDENCE_OBJECT = f"{PREFIX}evidence.json"
# One fetcher per catalogue source: the records in scope, as the source states them now.
Fetcher = Callable[[], Sequence[SourceRecord]]


class Evidence(FrozenModel):
    """The latest version of every source record the catalogue has been shown."""

    records: tuple[SourceRecord, ...] = ()


def _load_evidence(runtime: Runtime) -> tuple[dict[SourceRecordKey, SourceRecord], int | None]:
    try:
        stored = runtime.state.get(EVIDENCE_OBJECT)
    except ObjectNotFound:
        return {}, None
    try:
        evidence = Evidence.model_validate_json(stored.data)
    except ValidationError:
        raise StorageCorruptionError(f"{EVIDENCE_OBJECT} is not valid evidence") from None
    return {record.key: record for record in evidence.records}, stored.generation


def collect_catalogue(runtime: Runtime, fetchers: Mapping[str, Fetcher]) -> Mapping[str, Any]:
    cleared: list[str] = []
    refused: dict[str, str] = {}
    for source in sorted(fetchers):
        refusal = runtime.policy_for(source).registration_refusal(
            SourcePurpose.CATALOGUE_METADATA, runtime.stage
        )
        if refusal is None:
            cleared.append(source)
        else:
            refused[source] = refusal
            runtime.log("source_portion_refused", severity="WARNING", source=source, reason=refusal)
    if not cleared:
        raise Refused("; ".join(f"{source}: {reason}" for source, reason in refused.items()))

    known, evidence_generation = _load_evidence(runtime)
    changed: dict[str, list[SourceRecord]] = {}
    for source in cleared:
        for record in fetchers[source]():
            if record.key.source != source:
                raise ValueError(f"the {source} fetcher returned a record of another source")
            earlier = known.get(record.key)
            if earlier is None or earlier.content_hash != record.content_hash:
                changed.setdefault(source, []).append(record)
            known[record.key] = record

    # What each source said, as landed history. Only new and changed versions.
    for source, records in changed.items():
        runtime.writer.land(
            Table.SOURCE_RECORDS,
            source,
            runtime.run_id,
            runtime.now(),
            [source_record_row(record, runtime.run_id) for record in records],
        )

    repository = CatalogueRepository(runtime.state, runtime.policy_for, runtime.stage)
    before = repository.load()
    generation = advance_catalogue(
        repository,
        list(known.values()),
        as_of=runtime.now(),
        scope=CatalogueScope.BOOTSTRAP_REACHABLE,
    )
    landed_events = 0
    if generation is not None:
        events = repository.events(generation)
        landed_events += land_catalogue_events(runtime.writer, generation, events)
        after = repository.load()
        if after is None:
            raise StorageCorruptionError("the catalogue was committed and cannot be read back")
        rows = entity_and_lifecycle_rows(
            None if before is None else before.state, after.state, generation.generation
        )
        for table, table_rows in rows.items():
            if table_rows:
                runtime.writer.land_internal(
                    table, "catalogue", generation.generation, generation.generated_at, table_rows
                )
                landed_events += len(table_rows)
        publish_targets(runtime.state, targets_for(after.state, generation.generation))
        publish_targets(runtime.operations, targets_for(after.state, generation.generation))

    body = Evidence(records=tuple(known.values())).model_dump_json().encode("utf-8")
    try:
        if evidence_generation is None:
            runtime.state.create(EVIDENCE_OBJECT, body)
        else:
            runtime.state.replace(EVIDENCE_OBJECT, body, evidence_generation)
    except PreconditionFailed:
        raise StorageCorruptionError("the evidence changed during the run; run again") from None

    return {
        "outcome": "succeeded" if not refused else "partial",
        "sources_collected": cleared,
        "sources_refused": refused,
        "records_seen": len(known),
        "records_changed": sum(len(records) for records in changed.values()),
        "generation": None if generation is None else generation.generation,
        "event_rows_landed": landed_events,
    }
