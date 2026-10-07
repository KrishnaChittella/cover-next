"""The loader: landed batches into warehouse tables, at most once each.

Three things make a load idempotent, and any one of them is enough on its own.

    the receipt   an object written after a batch is loaded, at a name derived from
                  the batch. A batch with a receipt is skipped without a query.
    the job id    derived from the target table, the batch's object name and its
                  SHA-256. The warehouse accepts a job id once, so a load that was
                  submitted but whose receipt was lost cannot be submitted again.
    the row       every loaded row carries its batch's object name and SHA-256, so
                  the staging models can drop a repeat even if both guards failed.

The receipts are the load manifest: one per loaded batch, read by exact name, never
listed (the loader's grant on the operations bucket is conditional on a prefix).

A load is one atomic batch job. The loader reads the batch, checks it against its
manifest, adds the three load columns and submits the rows with the job. Replay
rebuilds a table from landing into a separate, new table under different job ids,
so it can be compared with the original before anything is replaced.
"""

import contextlib
import hashlib
from collections.abc import Callable, Sequence
from datetime import datetime
from enum import StrEnum

from pydantic import AwareDatetime

from metaspike.domain.model import FrozenModel
from metaspike.warehouse.landing import (
    MANIFEST_SUFFIX,
    TABLES,
    BatchManifest,
    Table,
    batch_prefix,
    decode_rows,
    encode_rows,
    read_batch,
    read_manifest,
    timestamp,
)
from metaspike.warehouse.objects import ObjectNotFound, ObjectStore, PreconditionFailed
from metaspike.warehouse.tables import (
    JobAlreadyExists,
    JobState,
    LoadFailed,
    LoadJob,
    TableRef,
    Warehouse,
    WarehouseError,
)

LOADER_VERSION = "loader_v1"
# A failed job id cannot be reused, so each new try takes the next id. Three failures
# of the same verified batch is a defect, not bad luck, and needs a person.
MAX_LOAD_ATTEMPTS = 3
RECEIPT_PREFIX = "warehouse/loads/"


class LoadInProgress(WarehouseError):
    """An earlier run's job for this batch has not finished. It is left alone."""


class LoadAbandoned(WarehouseError):
    """Every permitted attempt to load this batch failed."""


class BatchOutcome(StrEnum):
    LOADED = "loaded"
    ALREADY_LOADED = "already_loaded"  # a receipt exists
    RECOVERED = "recovered"  # the job had finished; only the receipt was missing


class LoadReceipt(FrozenModel):
    loader_version: str = LOADER_VERSION
    table: str
    data_object: str
    content_sha256: str
    job_id: str
    rows: int
    loaded_at: AwareDatetime
    load_run_id: str


class LoadedBatch(FrozenModel):
    data_object: str
    outcome: BatchOutcome
    rows: int
    job_id: str


class LoadReport(FrozenModel):
    table: str
    batches: tuple[LoadedBatch, ...] = ()
    # Data objects with no manifest: interrupted writes, never loaded.
    incomplete: tuple[str, ...] = ()

    def count(self, outcome: BatchOutcome) -> int:
        return sum(1 for batch in self.batches if batch.outcome is outcome)

    @property
    def rows_loaded(self) -> int:
        return sum(b.rows for b in self.batches if b.outcome is BatchOutcome.LOADED)


def batch_digest(manifest: BatchManifest, target: TableRef) -> str:
    identity = f"{target}|{manifest.data_object}|{manifest.content_sha256}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:40]


def load_job_id(manifest: BatchManifest, target: TableRef, attempt: int = 0) -> str:
    """The same batch into the same table always asks for the same job."""
    return f"ms_load_{target.table}_{batch_digest(manifest, target)}_a{attempt}"


def receipt_name(manifest: BatchManifest, target: TableRef) -> str:
    return f"{RECEIPT_PREFIX}{target}/{batch_digest(manifest, target)}.json"


class Loader:
    def __init__(
        self,
        landing: ObjectStore,
        operations: ObjectStore,
        warehouse: Warehouse,
        *,
        now: Callable[[], datetime],
        run_id: str,
    ) -> None:
        self._landing = landing
        self._operations = operations
        self._warehouse = warehouse
        self._now = now
        self._run_id = run_id

    def load_batch(self, manifest: BatchManifest, target: TableRef | None = None) -> LoadedBatch:
        spec = TABLES[manifest.table]
        table = TableRef(spec.dataset, spec.table.value) if target is None else target
        receipt = receipt_name(manifest, table)
        try:
            stored = LoadReceipt.model_validate_json(self._operations.get(receipt).data)
        except ObjectNotFound:
            pass
        else:
            return LoadedBatch(
                data_object=manifest.data_object,
                outcome=BatchOutcome.ALREADY_LOADED,
                rows=stored.rows,
                job_id=stored.job_id,
            )

        for attempt in range(MAX_LOAD_ATTEMPTS):
            job_id = load_job_id(manifest, table, attempt)
            job = self._warehouse.job(job_id)
            outcome = BatchOutcome.RECOVERED
            if job is None:
                job = self._submit(job_id, table, manifest)
                outcome = BatchOutcome.LOADED
            if job.state is JobState.RUNNING:
                raise LoadInProgress(f"{manifest.data_object}: job {job_id} is still running")
            if job.state is JobState.FAILED:
                continue  # that id is spent; the next attempt has its own
            self._write_receipt(receipt, manifest, table, job)
            return LoadedBatch(
                data_object=manifest.data_object,
                outcome=outcome,
                rows=manifest.rows,
                job_id=job_id,
            )
        raise LoadAbandoned(
            f"{manifest.data_object}: {MAX_LOAD_ATTEMPTS} load jobs failed; it needs a person"
        )

    def load_day(
        self, source: str, table: Table, day: str, target: TableRef | None = None
    ) -> LoadReport:
        """Load every complete batch one source landed for one table on one UTC day."""
        names = self._landing.list(batch_prefix(source, table, day))
        manifests = sorted(name for name in names if name.endswith(MANIFEST_SUFFIX))
        complete = {name.removesuffix(MANIFEST_SUFFIX) + ".ndjson" for name in manifests}
        spec = TABLES[table]
        return LoadReport(
            table=str(TableRef(spec.dataset, table.value) if target is None else target),
            batches=tuple(
                self.load_batch(read_manifest(self._landing, name), target) for name in manifests
            ),
            incomplete=tuple(
                name for name in names if name.endswith(".ndjson") and name not in complete
            ),
        )

    def replay(
        self, table: Table, sources: Sequence[str], days: Sequence[str], target: TableRef
    ) -> LoadReport:
        """Rebuild a table from landing into a new table, leaving the original alone."""
        spec = TABLES[table]
        original = TableRef(spec.dataset, table.value)
        if target == original:
            raise ValueError("replay builds a separate table; it never loads into the original")
        self._warehouse.create_like(target, original)
        reports = [self.load_day(source, table, day, target) for source in sources for day in days]
        return LoadReport(
            table=str(target),
            batches=tuple(batch for report in reports for batch in report.batches),
            incomplete=tuple(name for report in reports for name in report.incomplete),
        )

    def _submit(self, job_id: str, table: TableRef, manifest: BatchManifest) -> LoadJob:
        data = read_batch(self._landing, manifest)  # verified against the manifest
        loaded_at = timestamp(self._now())
        rows = [
            {
                **row,
                "landed_object": manifest.data_object,
                "content_sha256": manifest.content_sha256,
                "loaded_at": loaded_at,
            }
            for row in decode_rows(data)
        ]
        try:
            return self._warehouse.load(job_id, table, encode_rows(rows))
        except JobAlreadyExists:
            # Another run submitted it between our check and our submission.
            existing = self._warehouse.job(job_id)
            if existing is None:
                raise WarehouseError(f"job {job_id} is reported both taken and absent") from None
            return existing
        except LoadFailed as error:
            # Stop here. The next run uses the next job id, so a batch that fails for
            # a lasting reason is tried a bounded number of times, never in a loop.
            raise LoadFailed(f"{manifest.data_object}: {error}") from None

    def _write_receipt(
        self, name: str, manifest: BatchManifest, table: TableRef, job: LoadJob
    ) -> None:
        receipt = LoadReceipt(
            table=str(table),
            data_object=manifest.data_object,
            content_sha256=manifest.content_sha256,
            job_id=job.job_id,
            rows=manifest.rows,
            loaded_at=self._now(),
            load_run_id=self._run_id,
        )
        # If the name is taken, another run recorded the same load.
        with contextlib.suppress(PreconditionFailed):
            self._operations.create(name, receipt.model_dump_json().encode("utf-8"))


def table_fingerprint(
    warehouse: Warehouse, table: TableRef, partition: str, start: datetime, end: datetime
) -> tuple[int, str]:
    """Row count and a content hash of a table's rows in a range, ignoring load time.

    Two tables built from the same landed batches have the same fingerprint, whatever
    order the batches were loaded in and whenever that happened.
    """
    canonical = sorted(
        encode_rows([{k: _plain(v) for k, v in row.items() if k != "loaded_at"}])
        for row in warehouse.rows(table, partition, start, end)
    )
    return len(canonical), hashlib.sha256(b"".join(canonical)).hexdigest()


def _plain(value: object) -> object:
    return timestamp(value) if isinstance(value, datetime) else value
