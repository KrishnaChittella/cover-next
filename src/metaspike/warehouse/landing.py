"""The landing format: immutable NDJSON batches, each with a manifest.

A batch is two objects, written in this order and never changed afterwards:

    {source}/{table}/dt={YYYY-MM-DD}/{run_id}/part-{NNNN}.ndjson           the rows
    {source}/{table}/dt={YYYY-MM-DD}/{run_id}/part-{NNNN}.manifest.json    the manifest

The manifest is written last, so a batch without one is an interrupted write and is
never loaded. It states the run, the source, when the data was retrieved, the SHA-256
of the data object, the row-schema version and the version of the source policy that
permitted the write. The names are derived from the run alone: a writer never needs
to list or read the bucket, which matters because collectors may only create objects
under their own prefix.

The format is source-policy aware. A batch is refused unless the source's policy
permits the purpose at the private-collection stage, and a raw payload (a response
body as the source sent it) is refused unless the policy explicitly allows raw
storage. Steam's does not, so no Steam payload can be landed: only the normalised
sample rows, which have no payload field to put one in.
"""

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, Field, ValidationError

from metaspike.domain.model import FrozenModel
from metaspike.domain.observations import ObservationStatus, TimeSeries
from metaspike.domain.samples import SampleAttempt
from metaspike.domain.source_policy import DeploymentStage, SourcePolicy, SourcePurpose
from metaspike.domain.source_records import SourceRecord
from metaspike.storage.base import StorageCorruptionError, StoragePolicyError
from metaspike.warehouse.objects import ObjectStore, PreconditionFailed

LANDING_FORMAT = "landing_v1"
MANIFEST_SUFFIX = ".manifest.json"
# Added to every row by the loader, never present in a landed file.
LOAD_COLUMNS = ("landed_object", "content_sha256", "loaded_at")

_SAFE_SOURCE = re.compile(r"^[a-z0-9_]+$")
_SAFE_RUN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class LandingError(Exception):
    """A batch could not be written as asked."""


class BatchAlreadyLanded(LandingError):
    """This run already wrote this part. Landed data is never overwritten."""


class Table(StrEnum):
    SAMPLE_ATTEMPTS = "sample_attempts"
    PERIOD_OBSERVATIONS = "period_observations"
    SOURCE_RECORDS = "source_records"
    BINDING_EVENTS = "binding_events"
    RESOLUTION_DECISIONS = "resolution_decisions"
    QUARANTINE_EVENTS = "quarantine_events"
    COLLECTION_RUNS = "collection_runs"


@dataclass(frozen=True)
class TableSpec:
    table: Table
    dataset: str
    # The day-partition column; every read must bound it.
    partition: str
    # The column that names the source a row came from.
    source_column: str
    # What the source must be permitted to do for a row to exist. None: our own
    # operational record, which is not source data.
    purpose: SourcePurpose | None
    # Exactly the columns of a landed row, in schema order.
    columns: tuple[str, ...]
    schema_version: str

    @property
    def qualified(self) -> str:
        return f"{self.dataset}.{self.table.value}"


def _spec(
    table: Table,
    dataset: str,
    partition: str,
    purpose: SourcePurpose | None,
    columns: str,
    source_column: str = "source",
) -> TableSpec:
    return TableSpec(
        table=table,
        dataset=dataset,
        partition=partition,
        source_column=source_column,
        purpose=purpose,
        columns=tuple(columns.split()),
        schema_version=f"{table.value}_v1",
    )


TABLES: dict[Table, TableSpec] = {
    spec.table: spec
    for spec in (
        _spec(
            Table.SAMPLE_ATTEMPTS,
            "ms_raw",
            "slot_start",
            SourcePurpose.DEMAND_SIGNALS,
            "source metric source_native_id value granularity scheduled_for slot_start "
            "observed_at received_at canonical_game_id mapping_version binding_ref "
            "collection_run_id attempt_id status quality_flags error_code "
            "cache_age_seconds cache_ttl_seconds",
        ),
        _spec(
            Table.PERIOD_OBSERVATIONS,
            "ms_raw",
            "period_start",
            SourcePurpose.DEMAND_SIGNALS,
            "source metric source_identifier canonical_game_id granularity period_start "
            "value status observed_at collection_run_id",
        ),
        _spec(
            Table.SOURCE_RECORDS,
            "ms_raw",
            "fetched_at",
            SourcePurpose.CATALOGUE_METADATA,
            "source source_id content_hash title product_type fetched_at source_updated_at "
            "record collection_run_id",
        ),
        _spec(
            Table.BINDING_EVENTS,
            "ms_catalogue",
            "occurred_at",
            SourcePurpose.CATALOGUE_METADATA,
            "source source_id action canonical_game_id mapping_version occurred_at "
            "decision_ref catalogue_generation",
        ),
        _spec(
            Table.RESOLUTION_DECISIONS,
            "ms_catalogue",
            "decided_at",
            SourcePurpose.CATALOGUE_METADATA,
            "sequence record_source record_source_id record_content_hash decided_at kind "
            "canonical_game_id resolver_version decision catalogue_generation",
            source_column="record_source",
        ),
        _spec(
            Table.QUARANTINE_EVENTS,
            "ms_catalogue",
            "occurred_at",
            SourcePurpose.CATALOGUE_METADATA,
            "record_source record_source_id action reason fingerprint occurred_at "
            "decision_ref catalogue_generation",
            source_column="record_source",
        ),
        _spec(
            Table.COLLECTION_RUNS,
            "ms_ops",
            "started_at",
            None,
            "run_id job source phase started_at finished_at outcome requests_sent "
            "halted_by policy_version image_digest detail",
        ),
    )
}


class BatchManifest(FrozenModel):
    format: str = LANDING_FORMAT
    table: Table
    source: str
    run_id: str
    part: int = Field(ge=0)
    retrieved_at: AwareDatetime
    schema_version: str
    policy_version: str
    rows: int = Field(ge=1)
    bytes: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    data_object: str


class RawPayloadManifest(FrozenModel):
    format: str = LANDING_FORMAT
    source: str
    run_id: str
    retrieved_at: AwareDatetime
    policy_version: str
    bytes: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    data_object: str


def timestamp(moment: datetime) -> str:
    """UTC, microseconds, 'Z': one spelling for every timestamp in a landed row."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def policy_version(policy: SourcePolicy) -> str:
    """Names the exact policy text that permitted a write: review date and content."""
    digest = hashlib.sha256(policy.model_dump_json().encode("utf-8")).hexdigest()
    return f"{policy.reviewed_at.isoformat()}:{digest[:12]}"


def encode_rows(rows: Sequence[Mapping[str, Any]]) -> bytes:
    """Canonical NDJSON: sorted keys, no padding, UTF-8, one row per line."""
    return "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        for row in rows
    ).encode("utf-8")


def decode_rows(data: bytes) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(data.decode("utf-8").splitlines(), start=1):
        try:
            row = json.loads(line)
        except ValueError:
            raise StorageCorruptionError(f"line {number} is not JSON") from None
        if not isinstance(row, dict):
            raise StorageCorruptionError(f"line {number} is not a row")
        rows.append(row)
    return rows


def batch_prefix(source: str, table: Table, day: str | None = None) -> str:
    if not _SAFE_SOURCE.fullmatch(source):
        raise ValueError(f"unsafe source name: {source!r}")
    prefix = f"{source}/{table.value}/"
    return prefix if day is None else f"{prefix}dt={day}/"


def batch_name(source: str, table: Table, run_id: str, part: int, retrieved_at: datetime) -> str:
    if not _SAFE_RUN.fullmatch(run_id):
        raise ValueError(f"unsafe run id: {run_id!r}")
    day = retrieved_at.astimezone(UTC).date().isoformat()
    return f"{batch_prefix(source, table, day)}{run_id}/part-{part:04d}.ndjson"


def manifest_name(data_object: str) -> str:
    return data_object.removesuffix(".ndjson") + MANIFEST_SUFFIX


class LandingWriter:
    """Writes batches into the landing bucket. Create-only; nothing is ever replaced."""

    def __init__(
        self,
        store: ObjectStore,
        policy_for: Callable[[str], SourcePolicy],
        stage: DeploymentStage = DeploymentStage.PRIVATE_COLLECTION,
    ) -> None:
        self._store = store
        self._policy_for = policy_for
        self._stage = stage

    def land(
        self,
        table: Table,
        source: str,
        run_id: str,
        retrieved_at: datetime,
        rows: Sequence[Mapping[str, Any]],
        part: int = 0,
    ) -> BatchManifest:
        spec = TABLES[table]
        if not rows:
            raise LandingError("an empty batch is not landed")
        policy = self._policy_for(source)
        if spec.purpose is not None:
            refusal = policy.registration_refusal(spec.purpose, self._stage)
            if refusal is not None:
                raise StoragePolicyError(f"{source}: {refusal}")
        for row in rows:
            if set(row) != set(spec.columns):
                raise LandingError(
                    f"{table.value}: a row does not have exactly the table's columns"
                )
            if row[spec.source_column] != source:
                raise LandingError(f"{table.value}: a row belongs to another source")
        data = encode_rows(rows)
        name = batch_name(source, table, run_id, part, retrieved_at)
        manifest = BatchManifest(
            table=table,
            source=source,
            run_id=run_id,
            part=part,
            retrieved_at=retrieved_at,
            schema_version=spec.schema_version,
            policy_version=policy_version(policy),
            rows=len(rows),
            bytes=len(data),
            content_sha256=hashlib.sha256(data).hexdigest(),
            data_object=name,
        )
        try:
            self._store.create(name, data, "application/x-ndjson")
        except PreconditionFailed:
            raise BatchAlreadyLanded(name) from None
        self._store.create(manifest_name(name), manifest.model_dump_json().encode("utf-8"))
        return manifest


def land_raw_payload(
    store: ObjectStore,
    policy: SourcePolicy,
    run_id: str,
    name: str,
    retrieved_at: datetime,
    payload: bytes,
    stage: DeploymentStage = DeploymentStage.PRIVATE_COLLECTION,
) -> RawPayloadManifest:
    """Keep a response body exactly as the source sent it, where the source allows that."""
    source = policy.source
    if not policy.raw_storage_allowed.permits:
        raise StoragePolicyError(
            f"{source}: raw storage is {policy.raw_storage_allowed.value}, so no payload is kept"
        )
    if not policy.cleared_for(stage):
        raise StoragePolicyError(f"{source}: {policy.clearance(stage).reason}")
    if not (_SAFE_SOURCE.fullmatch(source) and _SAFE_RUN.fullmatch(run_id)):
        raise ValueError("unsafe source or run id")
    if not _SAFE_NAME.fullmatch(name):
        raise ValueError(f"unsafe payload name: {name!r}")
    if not payload:
        raise LandingError("an empty payload is not kept")
    day = retrieved_at.astimezone(UTC).date().isoformat()
    data_object = f"{source}/dt={day}/{run_id}/{name}"
    manifest = RawPayloadManifest(
        source=source,
        run_id=run_id,
        retrieved_at=retrieved_at,
        policy_version=policy_version(policy),
        bytes=len(payload),
        content_sha256=hashlib.sha256(payload).hexdigest(),
        data_object=data_object,
    )
    try:
        store.create(data_object, payload, "application/octet-stream")
    except PreconditionFailed:
        raise BatchAlreadyLanded(data_object) from None
    store.create(data_object + MANIFEST_SUFFIX, manifest.model_dump_json().encode("utf-8"))
    return manifest


def read_manifest(store: ObjectStore, name: str) -> BatchManifest:
    try:
        return BatchManifest.model_validate_json(store.get(name).data)
    except ValidationError:
        raise StorageCorruptionError(f"{name} is not a valid batch manifest") from None


def read_batch(store: ObjectStore, manifest: BatchManifest) -> bytes:
    """The batch's bytes, verified against its manifest before anything uses them."""
    data = store.get(manifest.data_object).data
    if hashlib.sha256(data).hexdigest() != manifest.content_sha256:
        raise StorageCorruptionError(f"{manifest.data_object} does not match its manifest")
    if data.count(b"\n") != manifest.rows or len(data) != manifest.bytes:
        raise StorageCorruptionError(f"{manifest.data_object} is not the size its manifest states")
    return data


# --- rows --------------------------------------------------------------------------------


def sample_attempt_row(attempt: SampleAttempt) -> dict[str, Any]:
    row = attempt.model_dump(mode="json")
    for column in ("scheduled_for", "slot_start", "observed_at", "received_at"):
        row[column] = timestamp(getattr(attempt, column))
    return {column: row[column] for column in TABLES[Table.SAMPLE_ATTEMPTS].columns}


def sample_attempt_from_row(row: Mapping[str, Any]) -> SampleAttempt:
    fields = {column: row[column] for column in TABLES[Table.SAMPLE_ATTEMPTS].columns}
    try:
        return SampleAttempt.model_validate(fields)
    except ValidationError:
        raise StorageCorruptionError("a stored row is not a valid sample attempt") from None


def period_observation_rows(
    series: TimeSeries, source_identifier: str, run_id: str
) -> list[dict[str, Any]]:
    """One row per OBSERVED point. A missing period is never a row."""
    return [
        {
            "source": point.source,
            "metric": point.metric,
            "source_identifier": source_identifier,
            "canonical_game_id": point.canonical_game_id,
            "granularity": point.granularity.value,
            "period_start": timestamp(point.period_start),
            "value": point.value,
            "status": point.status.value,
            "observed_at": timestamp(point.observed_at),
            "collection_run_id": run_id,
        }
        for point in series.points
        if point.status is ObservationStatus.OBSERVED
    ]


def source_record_row(record: SourceRecord, run_id: str) -> dict[str, Any]:
    return {
        "source": record.key.source,
        "source_id": record.key.source_id,
        "content_hash": record.content_hash,
        "title": record.title,
        "product_type": record.product_type.value,
        "fetched_at": timestamp(record.fetched_at),
        "source_updated_at": (
            None if record.source_updated_at is None else timestamp(record.source_updated_at)
        ),
        "record": record.model_dump(mode="json"),
        "collection_run_id": run_id,
    }


def source_record_from_row(row: Mapping[str, Any]) -> SourceRecord:
    try:
        return SourceRecord.model_validate(row["record"])
    except ValidationError:
        raise StorageCorruptionError("a stored row is not a valid source record") from None
