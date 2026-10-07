"""Warehouse tables as the stores and the loader need them.

Three operations, deliberately narrow:

    job    what became of a load job with a given id (None if it was never submitted)
    load   append a batch of NDJSON rows to a table, as one atomic batch load job
           with a caller-chosen id. An id can be used once: submitting it again
           changes nothing and raises JobAlreadyExists
    rows   rows of a table within a bounded range of its partition column

`MemoryWarehouse` is the offline implementation. It enforces the properties the real
tables have and the code relies on: a job id is single-use, a load is all-or-nothing,
a row must match the table's declared schema, and a read must bound the partition
column. `BigQueryWarehouse` speaks the BigQuery REST API with a metadata-server token.
Every query it sends carries a maximum-bytes-billed limit.
"""

import contextlib
import json
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

import httpx

from metaspike.warehouse.landing import decode_rows

_BQ = "https://bigquery.googleapis.com"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,1024}$")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
# No read in this module scans more than this. Hourly windows over clustered,
# day-partitioned tables are megabytes; the limit is a guard, not a budget.
DEFAULT_MAX_BYTES_BILLED = 1024**3


class WarehouseError(Exception):
    """A warehouse operation failed."""


class JobAlreadyExists(WarehouseError):
    """A job with this id was submitted before. Nothing new happened."""


class LoadFailed(WarehouseError):
    """The load job ended in an error. Nothing was appended."""


class JobState(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class LoadJob:
    job_id: str
    state: JobState
    rows: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class TableRef:
    dataset: str
    table: str

    def __post_init__(self) -> None:
        if not (_IDENTIFIER.fullmatch(self.dataset) and _IDENTIFIER.fullmatch(self.table)):
            raise ValueError(f"unsafe table reference: {self.dataset}.{self.table}")

    def __str__(self) -> str:
        return f"{self.dataset}.{self.table}"


class Warehouse(Protocol):
    def job(self, job_id: str) -> LoadJob | None: ...

    def load(self, job_id: str, table: TableRef, ndjson: bytes) -> LoadJob: ...

    def rows(
        self,
        table: TableRef,
        partition: str,
        start: datetime,
        end: datetime,
        equals: Mapping[str, str | Sequence[str]] | None = None,
    ) -> list[dict[str, Any]]:
        """Rows whose partition column is in [start, end], filtered by exact matches."""
        ...

    def create_like(self, new: TableRef, existing: TableRef) -> None:
        """Create an empty table with the schema of an existing one. Fails if it exists."""
        ...


# --- offline -------------------------------------------------------------------------------

_SCALAR_CHECKS: dict[str, Callable[[Any], bool]] = {
    "STRING": lambda v: isinstance(v, str),
    "INT64": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "FLOAT64": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "BOOL": lambda v: isinstance(v, bool),
    "TIMESTAMP": lambda v: isinstance(v, str) and _parse_timestamp(v) is not None,
    "JSON": lambda v: isinstance(v, dict | list | str | int | float | bool),
    "DATE": lambda v: isinstance(v, str) and _is_date(v),
}


def _is_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return len(text) == 10


def _parse_timestamp(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment.astimezone(UTC) if moment.tzinfo is not None else None


class MemoryWarehouse:
    """Tables in memory, with the guarantees of the real ones. Offline only."""

    def __init__(self, schemas: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
        self._schemas = {
            name: [dict(field) for field in fields] for name, fields in schemas.items()
        }
        self._tables: dict[str, list[dict[str, Any]]] = {name: [] for name in schemas}
        self._jobs: dict[str, LoadJob] = {}
        self.queries = 0

    def job(self, job_id: str) -> LoadJob | None:
        return self._jobs.get(job_id)

    def load(self, job_id: str, table: TableRef, ndjson: bytes) -> LoadJob:
        if not _JOB_ID.fullmatch(job_id):
            raise ValueError(f"invalid job id: {job_id!r}")
        if job_id in self._jobs:
            raise JobAlreadyExists(job_id)
        name = str(table)
        if name not in self._schemas:
            job = LoadJob(job_id=job_id, state=JobState.FAILED, error=f"{name} does not exist")
            self._jobs[job_id] = job
            raise LoadFailed(job.error)
        try:
            rows = [self._typed(name, row) for row in decode_rows(ndjson)]
        except ValueError as error:
            # All or nothing, as a load job is: one bad row and no row is appended.
            job = LoadJob(job_id=job_id, state=JobState.FAILED, error=str(error))
            self._jobs[job_id] = job
            raise LoadFailed(str(error)) from None
        self._tables[name].extend(rows)
        job = LoadJob(job_id=job_id, state=JobState.DONE, rows=len(rows))
        self._jobs[job_id] = job
        return job

    def rows(
        self,
        table: TableRef,
        partition: str,
        start: datetime,
        end: datetime,
        equals: Mapping[str, str | Sequence[str]] | None = None,
    ) -> list[dict[str, Any]]:
        self.queries += 1
        name = str(table)
        columns = {field["name"]: field for field in self._schemas[name]}
        if columns.get(partition, {}).get("type") != "TIMESTAMP":
            raise WarehouseError(f"{name}: {partition} is not a timestamp column")
        wanted = {
            column: {value} if isinstance(value, str) else set(value)
            for column, value in (equals or {}).items()
        }
        for column in wanted:
            if column not in columns:
                raise WarehouseError(f"{name}: no column {column}")
        return [
            dict(row)
            for row in self._tables[name]
            if start <= row[partition] <= end
            and all(row[column] in values for column, values in wanted.items())
        ]

    def create_like(self, new: TableRef, existing: TableRef) -> None:
        if str(new) in self._schemas:
            raise WarehouseError(f"{new} already exists")
        self._schemas[str(new)] = [dict(field) for field in self._schemas[str(existing)]]
        self._tables[str(new)] = []

    def all_rows(self, table: TableRef) -> list[dict[str, Any]]:
        """Test access only: the real tables refuse an unbounded read."""
        return [dict(row) for row in self._tables[str(table)]]

    def _typed(self, name: str, row: dict[str, Any]) -> dict[str, Any]:
        fields = self._schemas[name]
        unknown = set(row) - {field["name"] for field in fields}
        if unknown:
            raise ValueError(f"{name}: unknown column {sorted(unknown)[0]}")
        typed: dict[str, Any] = {}
        for field in fields:
            column, kind, mode = field["name"], field["type"], field.get("mode", "NULLABLE")
            value = row.get(column)
            check = _SCALAR_CHECKS[kind]
            if mode == "REPEATED":
                if not isinstance(value, list) or not all(check(item) for item in value):
                    raise ValueError(f"{name}.{column}: not a list of {kind}")
                typed[column] = list(value)
                continue
            if value is None:
                if mode == "REQUIRED":
                    raise ValueError(f"{name}.{column}: required")
                typed[column] = None
                continue
            if not check(value):
                raise ValueError(f"{name}.{column}: not {kind}")
            typed[column] = _parse_timestamp(value) if kind == "TIMESTAMP" else value
        return typed


# --- BigQuery ------------------------------------------------------------------------------


class BigQueryWarehouse:
    """BigQuery over REST. Batch load jobs and bounded, byte-limited queries only."""

    def __init__(
        self,
        client: httpx.Client,
        project: str,
        location: str,
        token: Callable[[], str],
        *,
        max_bytes_billed: int = DEFAULT_MAX_BYTES_BILLED,
        sleep: Callable[[float], None] = lambda _: None,
        poll_seconds: float = 2.0,
        max_polls: int = 150,
    ) -> None:
        if max_bytes_billed < 1:
            raise ValueError("a query must have a positive byte limit")
        self._client = client
        self._project = project
        self._location = location
        self._token = token
        self._max_bytes = max_bytes_billed
        self._sleep = sleep
        self._poll_seconds = poll_seconds
        self._max_polls = max_polls

    def job(self, job_id: str) -> LoadJob | None:
        response = self._client.get(
            f"{_BQ}/bigquery/v2/projects/{self._project}/jobs/{job_id}",
            params={"location": self._location},
            headers=self._headers(),
        )
        if response.status_code == 404:
            return None
        self._check(response)
        return _job_from(job_id, response.json())

    def load(self, job_id: str, table: TableRef, ndjson: bytes) -> LoadJob:
        if not _JOB_ID.fullmatch(job_id):
            raise ValueError(f"invalid job id: {job_id!r}")
        configuration = {
            "jobReference": {
                "projectId": self._project,
                "jobId": job_id,
                "location": self._location,
            },
            "configuration": {
                "load": {
                    "destinationTable": {
                        "projectId": self._project,
                        "datasetId": table.dataset,
                        "tableId": table.table,
                    },
                    "sourceFormat": "NEWLINE_DELIMITED_JSON",
                    # Append to a table Terraform created; never create or reshape one.
                    "writeDisposition": "WRITE_APPEND",
                    "createDisposition": "CREATE_NEVER",
                    "ignoreUnknownValues": False,
                    "maxBadRecords": 0,
                }
            },
        }
        boundary = f"ms-{uuid.uuid4().hex}"
        body = b"".join(
            (
                f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
                json.dumps(configuration).encode("utf-8"),
                f"\r\n--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n".encode(),
                ndjson,
                f"\r\n--{boundary}--\r\n".encode(),
            )
        )
        response = self._client.post(
            f"{_BQ}/upload/bigquery/v2/projects/{self._project}/jobs",
            params={"uploadType": "multipart"},
            content=body,
            headers={
                **self._headers(),
                "Content-Type": f"multipart/related; boundary={boundary}",
            },
        )
        if response.status_code == 409:
            raise JobAlreadyExists(job_id)
        self._check(response)
        job = _job_from(job_id, response.json())
        for _ in range(self._max_polls):
            if job.state is not JobState.RUNNING:
                break
            self._sleep(self._poll_seconds)
            polled = self.job(job_id)
            if polled is None:
                raise WarehouseError(f"load job {job_id} disappeared")
            job = polled
        if job.state is JobState.FAILED:
            raise LoadFailed(job.error or "the load job failed")
        if job.state is JobState.RUNNING:
            raise WarehouseError(f"load job {job_id} did not finish in time")
        return job

    def rows(
        self,
        table: TableRef,
        partition: str,
        start: datetime,
        end: datetime,
        equals: Mapping[str, str | Sequence[str]] | None = None,
    ) -> list[dict[str, Any]]:
        conditions = [f"`{_safe(partition)}` BETWEEN @range_start AND @range_end"]
        parameters = [
            _timestamp_parameter("range_start", start),
            _timestamp_parameter("range_end", end),
        ]
        for index, (column, value) in enumerate(sorted((equals or {}).items())):
            name = f"p{index}"
            if isinstance(value, str):
                conditions.append(f"`{_safe(column)}` = @{name}")
                parameters.append(
                    {
                        "name": name,
                        "parameterType": {"type": "STRING"},
                        "parameterValue": {"value": value},
                    }
                )
            else:
                conditions.append(f"`{_safe(column)}` IN UNNEST(@{name})")
                parameters.append(
                    {
                        "name": name,
                        "parameterType": {"type": "ARRAY", "arrayType": {"type": "STRING"}},
                        "parameterValue": {"arrayValues": [{"value": item} for item in value]},
                    }
                )
        sql = (
            f"SELECT * FROM `{self._project}.{table.dataset}.{table.table}` "
            f"WHERE {' AND '.join(conditions)}"
        )
        return self._query(sql, parameters)

    def create_like(self, new: TableRef, existing: TableRef) -> None:
        self._query(
            f"CREATE TABLE `{self._project}.{new.dataset}.{new.table}` "
            f"LIKE `{self._project}.{existing.dataset}.{existing.table}`",
            [],
        )

    def _query(self, sql: str, parameters: list[dict[str, Any]]) -> list[dict[str, Any]]:
        response = self._client.post(
            f"{_BQ}/bigquery/v2/projects/{self._project}/queries",
            json={
                "query": sql,
                "useLegacySql": False,
                "location": self._location,
                "parameterMode": "NAMED",
                "queryParameters": parameters,
                "maximumBytesBilled": str(self._max_bytes),
                "formatOptions": {"useInt64Timestamp": True},
                "timeoutMs": 30000,
            },
            headers=self._headers(),
        )
        self._check(response)
        body = response.json()
        reference = body.get("jobReference", {})
        for _ in range(self._max_polls):
            if body.get("jobComplete"):
                break
            self._sleep(self._poll_seconds)
            body = self._results(reference["jobId"], None)
        else:
            raise WarehouseError("a query did not finish in time")
        fields = body.get("schema", {}).get("fields", [])
        rows = [_row(fields, row) for row in body.get("rows", ())]
        while body.get("pageToken"):
            body = self._results(reference["jobId"], body["pageToken"])
            rows.extend(_row(fields, row) for row in body.get("rows", ()))
        return rows

    def _results(self, job_id: str, page: str | None) -> dict[str, Any]:
        params = {
            "location": self._location,
            "formatOptions.useInt64Timestamp": "true",
            "timeoutMs": "30000",
        }
        if page is not None:
            params["pageToken"] = page
        response = self._client.get(
            f"{_BQ}/bigquery/v2/projects/{self._project}/queries/{job_id}",
            params=params,
            headers=self._headers(),
        )
        self._check(response)
        body: dict[str, Any] = response.json()
        return body

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}"}

    def _check(self, response: httpx.Response) -> None:
        if response.status_code >= 400:
            reason = ""
            with contextlib.suppress(ValueError, KeyError, IndexError, TypeError):
                reason = str(response.json()["error"]["errors"][0]["reason"])
            # The status and BigQuery's short reason code only: never a header or a body.
            raise WarehouseError(f"BigQuery answered {response.status_code} {reason}".rstrip())


def _safe(identifier: str) -> str:
    if not _IDENTIFIER.fullmatch(identifier):
        raise ValueError(f"unsafe identifier: {identifier!r}")
    return identifier


def _timestamp_parameter(name: str, moment: datetime) -> dict[str, Any]:
    return {
        "name": name,
        "parameterType": {"type": "TIMESTAMP"},
        "parameterValue": {"value": moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f+00:00")},
    }


def _job_from(job_id: str, body: Mapping[str, Any]) -> LoadJob:
    status = body.get("status", {})
    if status.get("state") != "DONE":
        return LoadJob(job_id=job_id, state=JobState.RUNNING)
    error = status.get("errorResult")
    if error:
        return LoadJob(
            job_id=job_id, state=JobState.FAILED, error=str(error.get("reason", "failed"))
        )
    written = body.get("statistics", {}).get("load", {}).get("outputRows")
    return LoadJob(
        job_id=job_id, state=JobState.DONE, rows=None if written is None else int(written)
    )


def _row(fields: Sequence[Mapping[str, Any]], row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        field["name"]: _value(field, cell["v"])
        for field, cell in zip(fields, row["f"], strict=True)
    }


def _value(field: Mapping[str, Any], raw: Any) -> Any:
    if field.get("mode") == "REPEATED":
        return [_scalar(field["type"], item["v"]) for item in raw]
    return None if raw is None else _scalar(field["type"], raw)


def _scalar(kind: str, raw: Any) -> Any:
    if kind in ("INTEGER", "INT64"):
        return int(raw)
    if kind in ("FLOAT", "FLOAT64"):
        return float(raw)
    if kind in ("BOOLEAN", "BOOL"):
        return raw == "true"
    if kind == "TIMESTAMP":
        return _EPOCH + timedelta(microseconds=int(raw))
    if kind == "JSON":
        return json.loads(raw)
    return raw  # STRING, and DATE as its ISO text
