"""Landed batches and the loader, offline. Nothing here reaches a network."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.source_policy import Permission
from metaspike.domain.source_records import ProductType, SourceRecord
from metaspike.storage.base import StorageCorruptionError, StoragePolicyError
from metaspike.warehouse.auth import MetadataToken, TokenUnavailable
from metaspike.warehouse.landing import (
    LANDING_FORMAT,
    LOAD_COLUMNS,
    TABLES,
    BatchAlreadyLanded,
    BatchManifest,
    LandingError,
    LandingWriter,
    Table,
    decode_rows,
    land_raw_payload,
    manifest_name,
    policy_version,
    read_manifest,
    sample_attempt_from_row,
    sample_attempt_row,
    source_record_row,
)
from metaspike.warehouse.loader import (
    MAX_LOAD_ATTEMPTS,
    RECEIPT_PREFIX,
    BatchOutcome,
    LoadAbandoned,
    LoadInProgress,
    load_job_id,
    receipt_name,
    table_fingerprint,
)
from metaspike.warehouse.objects import (
    AccessDenied,
    GcsObjectStore,
    MemoryObjectStore,
    ObjectNotFound,
    PreconditionFailed,
)
from metaspike.warehouse.tables import (
    BigQueryWarehouse,
    JobAlreadyExists,
    JobState,
    LoadFailed,
    LoadJob,
    TableRef,
    WarehouseError,
)
from tests.test_sampling import SOURCE, T0, attempt, minutes
from tests.warehouse_support import (
    cleared_policy,
    real_policy,
    table_schemas,
    world,
)

ATTEMPTS = TableRef("ms_raw", "sample_attempts")
RUN = "20261006T120000Z-aaaa0001"
DAY = "2026-10-06"
SECRET_SHAPED = "ya29.NOT-A-REAL-TOKEN-0123456789"


def now() -> datetime:
    return T0 + minutes(20)


def rows(count: int = 3, start: int = 0) -> list[dict[str, object]]:
    return [
        sample_attempt_row(
            attempt(T0 + minutes(1), value=100 + i, app=str(700 + i), attempt_id=f"a{i}")
        )
        for i in range(start, start + count)
    ]


def landed(w: object, run: str = RUN, part: int = 0, start: int = 0) -> BatchManifest:
    return w.writer.land(Table.SAMPLE_ATTEMPTS, SOURCE, run, now(), rows(start=start), part=part)  # type: ignore[attr-defined,no-any-return]


# --- the format ----------------------------------------------------------------------------


def test_every_landed_row_shape_is_exactly_the_declared_table() -> None:
    schemas = table_schemas()
    for spec in TABLES.values():
        declared = [field["name"] for field in schemas[spec.qualified]]
        # The same columns exactly. Order may differ only where a column had to be
        # appended to a table that already existed.
        assert sorted([*spec.columns, *LOAD_COLUMNS]) == sorted(declared), spec.qualified
        assert len(declared) == len(set(declared))
        partition = next(f for f in schemas[spec.qualified] if f["name"] == spec.partition)
        assert partition["type"] == "TIMESTAMP"
        assert spec.source_column in spec.columns or spec.purpose is None


def test_a_batch_is_immutable_ndjson_with_a_manifest_that_names_its_provenance() -> None:
    w = world(now)
    manifest = landed(w)
    assert manifest.data_object == f"{SOURCE}/sample_attempts/dt={DAY}/{RUN}/part-0000.ndjson"
    data = w.landing.get(manifest.data_object).data
    assert manifest.content_sha256 == hashlib.sha256(data).hexdigest()
    assert (manifest.format, manifest.run_id, manifest.source) == (LANDING_FORMAT, RUN, SOURCE)
    assert manifest.retrieved_at == now()
    assert manifest.schema_version == "sample_attempts_v1"
    assert manifest.policy_version == policy_version(cleared_policy(SOURCE))
    assert (manifest.rows, manifest.bytes) == (3, len(data))
    assert read_manifest(w.landing, manifest_name(manifest.data_object)) == manifest
    lines = data.decode().splitlines()
    assert len(lines) == 3 and all(json.loads(line) for line in lines)
    assert all(
        line == json.dumps(json.loads(line), sort_keys=True, separators=(",", ":"))
        for line in lines
    )
    assert [sample_attempt_from_row(row).attempt_id for row in decode_rows(data)] == [
        "a0",
        "a1",
        "a2",
    ]


def test_the_policy_version_changes_when_the_policy_does() -> None:
    real, test_only = real_policy(SOURCE), cleared_policy(SOURCE)
    assert policy_version(real) != policy_version(test_only)
    assert policy_version(real).startswith(real.reviewed_at.isoformat())


def test_landed_data_is_never_overwritten() -> None:
    w = world(now)
    manifest = landed(w)
    before = w.landing.get(manifest.data_object)
    with pytest.raises(BatchAlreadyLanded):
        landed(w, start=50)
    assert w.landing.get(manifest.data_object) == before


def test_a_collector_can_land_without_reading_or_listing_the_bucket() -> None:
    w = world(now)
    collector = w.landing.view(
        writable_prefixes=(f"{SOURCE}/",), readable_prefixes=(), can_list=False, can_overwrite=False
    )
    writer = LandingWriter(collector, cleared_policy)
    manifest = writer.land(Table.SAMPLE_ATTEMPTS, SOURCE, RUN, now(), rows())
    assert w.landing.get(manifest.data_object).data
    with pytest.raises(AccessDenied):
        collector.list(f"{SOURCE}/")
    with pytest.raises(AccessDenied):
        collector.get(manifest.data_object)
    elsewhere = w.landing.view(writable_prefixes=("igdb/",), can_list=False)
    with pytest.raises(AccessDenied):
        LandingWriter(elsewhere, cleared_policy).land(
            Table.SAMPLE_ATTEMPTS, SOURCE, "other-run", now(), rows()
        )


def test_malformed_batches_are_refused_before_anything_is_written() -> None:
    w = world(now)
    with pytest.raises(LandingError, match="empty"):
        w.writer.land(Table.SAMPLE_ATTEMPTS, SOURCE, RUN, now(), [])
    with pytest.raises(LandingError, match="another source"):
        w.writer.land(Table.SAMPLE_ATTEMPTS, "igdb", RUN, now(), rows())
    extra = [{**rows(1)[0], "payload": "{}"}]
    with pytest.raises(LandingError, match="exactly the table's columns"):
        w.writer.land(Table.SAMPLE_ATTEMPTS, SOURCE, RUN, now(), extra)
    for unsafe in ("../x", "a/b", ""):
        with pytest.raises(ValueError):
            w.writer.land(Table.SAMPLE_ATTEMPTS, SOURCE, unsafe, now(), rows())
    assert w.landing.list("") == ()


# --- source policy -------------------------------------------------------------------------


def test_the_real_steam_policies_refuse_private_collection_landing() -> None:
    w = world(now, policy_for=real_policy)
    with pytest.raises(StoragePolicyError, match="not cleared for private_collection"):
        w.writer.land(Table.SAMPLE_ATTEMPTS, SOURCE, RUN, now(), rows())
    record = SourceRecord(
        key=SourceRecordKey(source="steam_catalogue", source_id="730"),
        title="Counter-Strike 2",
        product_type=ProductType.MAIN_GAME,
        fetched_at=now(),
    )
    with pytest.raises(StoragePolicyError, match="not cleared for private_collection"):
        w.writer.land(
            Table.SOURCE_RECORDS, "steam_catalogue", RUN, now(), [source_record_row(record, RUN)]
        )
    assert w.landing.list("") == ()


def test_no_steam_payload_can_be_landed_even_if_collection_were_cleared() -> None:
    raw = MemoryObjectStore()
    for source in (SOURCE, "steam_catalogue"):
        policy = cleared_policy(source)  # test-only clearance; raw storage is untouched
        assert policy.raw_storage_allowed is Permission.RESTRICTED
        with pytest.raises(StoragePolicyError, match="no payload is kept"):
            land_raw_payload(raw, policy, RUN, "response.json", now(), b'{"response":{}}')
    assert raw.list("") == ()
    # And the normalised sample row has nowhere to put one.
    assert not {"payload", "raw", "body", "response"} & set(TABLES[Table.SAMPLE_ATTEMPTS].columns)


def test_a_payload_is_kept_only_for_a_source_that_allows_raw_storage() -> None:
    raw = MemoryObjectStore()
    manifest = land_raw_payload(raw, real_policy("igdb"), RUN, "games-0001.json", now(), b"[{}]")
    assert manifest.data_object == f"igdb/dt={DAY}/{RUN}/games-0001.json"
    assert raw.get(manifest.data_object).data == b"[{}]"
    assert manifest.content_sha256 == hashlib.sha256(b"[{}]").hexdigest()
    assert manifest.policy_version == policy_version(real_policy("igdb"))
    with pytest.raises(BatchAlreadyLanded):
        land_raw_payload(raw, real_policy("igdb"), RUN, "games-0001.json", now(), b"[]")
    with pytest.raises(ValueError):
        land_raw_payload(raw, real_policy("igdb"), RUN, "../escape", now(), b"[]")


# --- the loader ----------------------------------------------------------------------------


def test_a_batch_is_loaded_once_however_often_the_loader_runs() -> None:
    w = world(now)
    manifest = landed(w)
    first = w.loader("load-1").load_day(SOURCE, Table.SAMPLE_ATTEMPTS, DAY)
    assert first.count(BatchOutcome.LOADED) == 1 and first.rows_loaded == 3
    loaded = w.warehouse.all_rows(ATTEMPTS)
    assert len(loaded) == 3
    assert {row["landed_object"] for row in loaded} == {manifest.data_object}
    assert {row["content_sha256"] for row in loaded} == {manifest.content_sha256}
    assert {row["loaded_at"] for row in loaded} == {now()}

    for run in ("load-2", "load-3"):
        again = w.loader(run).load_day(SOURCE, Table.SAMPLE_ATTEMPTS, DAY)
        assert again.count(BatchOutcome.ALREADY_LOADED) == 1 and again.rows_loaded == 0
    assert w.warehouse.all_rows(ATTEMPTS) == loaded


def test_the_job_id_is_a_function_of_the_batch_and_the_target() -> None:
    w = world(now)
    manifest = landed(w)
    assert load_job_id(manifest, ATTEMPTS) == load_job_id(manifest, ATTEMPTS)
    assert load_job_id(manifest, ATTEMPTS).startswith("ms_load_sample_attempts_")
    assert load_job_id(manifest, ATTEMPTS, 1) != load_job_id(manifest, ATTEMPTS, 0)
    assert load_job_id(manifest, TableRef("ms_raw", "replayed")) != load_job_id(manifest, ATTEMPTS)
    other = landed(w, run="another-run")
    assert load_job_id(other, ATTEMPTS) != load_job_id(manifest, ATTEMPTS)


def test_a_lost_receipt_does_not_load_the_batch_twice() -> None:
    w = world(now)
    manifest = landed(w)
    w.loader().load_batch(manifest)
    # The run died after the job finished and before the receipt was written.
    w.operations = MemoryObjectStore()
    recovered = w.loader("load-2").load_batch(manifest)
    assert recovered.outcome is BatchOutcome.RECOVERED
    assert len(w.warehouse.all_rows(ATTEMPTS)) == 3
    assert w.operations.get(receipt_name(manifest, ATTEMPTS)).data
    assert w.loader("load-3").load_batch(manifest).outcome is BatchOutcome.ALREADY_LOADED


def test_two_loaders_racing_for_one_batch_append_it_once() -> None:
    w = world(now)
    manifest = landed(w)
    loader = w.loader()
    real_job, calls = w.warehouse.job, []

    def job_unseen_at_first(job_id: str) -> LoadJob | None:
        calls.append(job_id)
        if len(calls) == 1:
            # Between this check and our submission, the other loader gets there.
            w.loader("rival").load_batch(manifest)
            w.operations = MemoryObjectStore()
            return None
        return real_job(job_id)

    w.warehouse.job = job_unseen_at_first  # type: ignore[method-assign]
    outcome = loader.load_batch(manifest)
    assert outcome.outcome is BatchOutcome.LOADED  # it believes it loaded; the id says once
    assert len(w.warehouse.all_rows(ATTEMPTS)) == 3


def test_the_load_manifest_is_read_by_name_and_never_listed() -> None:
    w = world(now)
    manifest = landed(w)
    w.loader().load_batch(manifest)  # the loader's view of the bucket refuses listing
    name = receipt_name(manifest, ATTEMPTS)
    assert name.startswith(f"{RECEIPT_PREFIX}ms_raw.sample_attempts/")
    receipt = json.loads(w.operations.get(name).data)
    assert receipt["data_object"] == manifest.data_object
    assert receipt["content_sha256"] == manifest.content_sha256
    assert receipt["job_id"] == load_job_id(manifest, ATTEMPTS)
    assert (receipt["rows"], receipt["load_run_id"]) == (3, "load-1")


def test_a_batch_that_does_not_match_its_manifest_is_not_loaded() -> None:
    w = world(now)
    manifest = landed(w)
    tampered = w.landing.get(manifest.data_object)
    w.landing.replace(manifest.data_object, tampered.data + b"{}\n", tampered.generation)
    with pytest.raises(StorageCorruptionError, match="does not match its manifest"):
        w.loader().load_batch(manifest)
    assert w.warehouse.all_rows(ATTEMPTS) == []
    with pytest.raises(ObjectNotFound):
        w.operations.get(receipt_name(manifest, ATTEMPTS))


def test_an_interrupted_write_is_reported_and_not_loaded() -> None:
    w = world(now)
    landed(w)
    w.landing.create(f"{SOURCE}/sample_attempts/dt={DAY}/torn-run/part-0000.ndjson", b"{}\n")
    report = w.loader().load_day(SOURCE, Table.SAMPLE_ATTEMPTS, DAY)
    assert report.count(BatchOutcome.LOADED) == 1
    assert report.incomplete == (f"{SOURCE}/sample_attempts/dt={DAY}/torn-run/part-0000.ndjson",)
    assert len(w.warehouse.all_rows(ATTEMPTS)) == 3


def test_a_failed_load_appends_nothing_and_is_retried_a_bounded_number_of_times() -> None:
    w = world(now)
    bad = [{**rows(1)[0], "value": "not a number"}]
    manifest = w.writer.land(Table.SAMPLE_ATTEMPTS, SOURCE, RUN, now(), [*rows(2), *bad])
    for attempt_number in range(MAX_LOAD_ATTEMPTS):
        with pytest.raises(LoadFailed, match="value"):
            w.loader(f"load-{attempt_number}").load_batch(manifest)
        job = w.warehouse.job(load_job_id(manifest, ATTEMPTS, attempt_number))
        assert job is not None and job.state is JobState.FAILED
    assert w.warehouse.all_rows(ATTEMPTS) == []  # all or nothing
    with pytest.raises(LoadAbandoned, match="needs a person"):
        w.loader("load-last").load_batch(manifest)
    assert w.warehouse.job(load_job_id(manifest, ATTEMPTS, MAX_LOAD_ATTEMPTS)) is None


def test_a_job_that_is_still_running_is_left_alone() -> None:
    w = world(now)
    manifest = landed(w)

    def still_running(job_id: str) -> LoadJob | None:
        return LoadJob(job_id=job_id, state=JobState.RUNNING)

    w.warehouse.job = still_running  # type: ignore[method-assign]
    with pytest.raises(LoadInProgress):
        w.loader().load_batch(manifest)
    assert w.warehouse.all_rows(ATTEMPTS) == []


def test_a_job_id_is_single_use_in_the_warehouse() -> None:
    w = world(now)
    w.warehouse.load("job-1", ATTEMPTS, b"")
    with pytest.raises(JobAlreadyExists):
        w.warehouse.load("job-1", ATTEMPTS, b"")
    with pytest.raises(WarehouseError, match="timestamp"):
        w.warehouse.rows(ATTEMPTS, "status", T0, T0)


def test_replay_rebuilds_a_table_from_landing_identically() -> None:
    w = world(now)
    landed(w, run="run-a")
    landed(w, run="run-b", start=10)
    landed(w, run="run-b", part=1, start=20)
    w.loader().load_day(SOURCE, Table.SAMPLE_ATTEMPTS, DAY)
    span = (T0 - timedelta(days=1), T0 + timedelta(days=1))
    original = table_fingerprint(w.warehouse, ATTEMPTS, "slot_start", *span)
    assert original[0] == 9

    later = world(lambda: now() + timedelta(days=30))
    later.landing, later.operations, later.warehouse = w.landing, w.operations, w.warehouse
    rebuilt = TableRef("ms_raw", "sample_attempts__replay_1")
    report = later.loader("replay-1").replay(Table.SAMPLE_ATTEMPTS, [SOURCE], [DAY], rebuilt)
    assert report.count(BatchOutcome.LOADED) == 3 and report.rows_loaded == 9
    assert table_fingerprint(w.warehouse, rebuilt, "slot_start", *span) == original
    assert table_fingerprint(w.warehouse, ATTEMPTS, "slot_start", *span) == original  # untouched
    assert {row["loaded_at"] for row in w.warehouse.all_rows(rebuilt)} == {later.now()}

    with pytest.raises(ValueError, match="never loads into the original"):
        later.loader().replay(Table.SAMPLE_ATTEMPTS, [SOURCE], [DAY], ATTEMPTS)
    with pytest.raises(WarehouseError, match="already exists"):
        later.loader().replay(Table.SAMPLE_ATTEMPTS, [SOURCE], [DAY], rebuilt)


# --- the cloud adapters, against a transport that is not a network -------------------------


def test_the_metadata_token_is_cached_refreshed_and_never_shown() -> None:
    calls: list[httpx.Request] = []
    clock = [T0]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"access_token": SECRET_SHAPED, "expires_in": 3600})

    token = MetadataToken(httpx.Client(transport=httpx.MockTransport(handler)), lambda: clock[0])
    assert token() == SECRET_SHAPED and token() == SECRET_SHAPED
    assert len(calls) == 1 and calls[0].headers["Metadata-Flavor"] == "Google"
    assert calls[0].url.host == "metadata.google.internal"
    clock[0] = T0 + timedelta(minutes=59)
    token()
    assert len(calls) == 2
    assert SECRET_SHAPED not in repr(token) and SECRET_SHAPED not in str(vars(token).keys())

    refused = MetadataToken(
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(500, text=SECRET_SHAPED))
        )
    )
    with pytest.raises(TokenUnavailable) as failure:
        refused()
    assert SECRET_SHAPED not in str(failure.value)


def test_the_gcs_adapter_creates_conditionally_and_reports_refusals_without_the_token() -> None:
    seen: list[httpx.Request] = []
    answers = iter(
        [
            httpx.Response(200, json={"generation": "17"}),
            httpx.Response(412, text=SECRET_SHAPED),
            httpx.Response(200, json={"generation": "18"}),
            httpx.Response(200, content=b"data", headers={"x-goog-generation": "18"}),
            httpx.Response(404),
            httpx.Response(403, text=SECRET_SHAPED),
            httpx.Response(200, json={"items": [{"name": "p/b"}], "nextPageToken": "t"}),
            httpx.Response(200, json={"items": [{"name": "p/a"}]}),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return next(answers)

    store = GcsObjectStore(
        httpx.Client(transport=httpx.MockTransport(handler)), "bucket", lambda: SECRET_SHAPED
    )
    assert store.create("p/x y.json", b"{}") == 17
    assert seen[0].url.params["ifGenerationMatch"] == "0"
    assert seen[0].url.params["name"] == "p/x y.json"
    assert seen[0].url.host == "storage.googleapis.com"
    with pytest.raises(PreconditionFailed) as conflict:
        store.create("p/x y.json", b"{}")
    assert store.replace("p/CURRENT", b"{}", 17) == 18
    assert seen[2].url.params["ifGenerationMatch"] == "17"
    found = store.get("p/x y.json")
    assert (found.data, found.generation) == (b"data", 18)
    assert "/o/p%2Fx%20y.json" in str(seen[3].url)
    with pytest.raises(ObjectNotFound):
        store.get("p/none")
    with pytest.raises(AccessDenied) as denied:
        store.list("p/")
    assert store.list("p/") == ("p/a", "p/b")
    assert seen[-1].url.params["pageToken"] == "t"
    assert all(request.headers["Authorization"] == f"Bearer {SECRET_SHAPED}" for request in seen)
    for failure in (conflict, denied):
        assert SECRET_SHAPED not in str(failure.value)


def _bigquery(handler: object) -> BigQueryWarehouse:
    return BigQueryWarehouse(
        httpx.Client(transport=httpx.MockTransport(handler)),  # type: ignore[arg-type]
        "project",
        "us-central1",
        lambda: SECRET_SHAPED,
    )


def test_the_bigquery_adapter_submits_one_append_only_batch_job_under_the_given_id() -> None:
    seen: list[httpx.Request] = []
    answers = iter(
        [
            httpx.Response(200, json={"status": {"state": "RUNNING"}}),
            httpx.Response(
                200, json={"status": {"state": "DONE"}, "statistics": {"load": {"outputRows": "2"}}}
            ),
            httpx.Response(409, json={"error": {"errors": [{"reason": "duplicate"}]}}),
            httpx.Response(404),
            httpx.Response(
                200, json={"status": {"state": "DONE", "errorResult": {"reason": "invalid"}}}
            ),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return next(answers)

    warehouse = _bigquery(handler)
    job = warehouse.load("ms_load_x_a0", ATTEMPTS, b'{"a":1}\n{"a":2}\n')
    assert (job.state, job.rows) == (JobState.DONE, 2)
    body = seen[0].content.decode()
    configuration = json.loads(body.split("\r\n\r\n")[1].split("\r\n--")[0])
    assert configuration["jobReference"] == {
        "projectId": "project",
        "jobId": "ms_load_x_a0",
        "location": "us-central1",
    }
    load = configuration["configuration"]["load"]
    assert load["writeDisposition"] == "WRITE_APPEND"
    assert load["createDisposition"] == "CREATE_NEVER"
    assert (load["maxBadRecords"], load["ignoreUnknownValues"]) == (0, False)
    assert load["sourceFormat"] == "NEWLINE_DELIMITED_JSON"
    assert '{"a":1}\n{"a":2}\n' in body
    assert seen[0].url.path == "/upload/bigquery/v2/projects/project/jobs"
    assert seen[1].url.params["location"] == "us-central1"
    with pytest.raises(JobAlreadyExists):
        warehouse.load("ms_load_x_a0", ATTEMPTS, b"{}\n")
    assert warehouse.job("never-submitted") is None
    failed = warehouse.job("failed")
    assert failed is not None and (failed.state, failed.error) == (JobState.FAILED, "invalid")
    with pytest.raises(ValueError):
        warehouse.load("bad id!", ATTEMPTS, b"")


def test_every_bigquery_read_is_bounded_parameterised_and_byte_limited() -> None:
    seen: list[httpx.Request] = []
    micros = int((T0 - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds() * 1_000_000)
    fields = [
        {"name": "slot_start", "type": "TIMESTAMP"},
        {"name": "value", "type": "INTEGER"},
        {"name": "quality_flags", "type": "STRING", "mode": "REPEATED"},
        {"name": "detail", "type": "JSON"},
        {"name": "error_code", "type": "STRING"},
    ]

    def page(value: int, token: str | None) -> dict[str, Any]:
        cells = [
            {"v": str(micros)},
            {"v": str(value)},
            {"v": [{"v": "late"}]},
            {"v": '{"a":1}'},
            {"v": None},
        ]
        return {
            "jobComplete": True,
            "jobReference": {"jobId": "q1"},
            "schema": {"fields": fields},
            "rows": [{"f": cells}],
            **({"pageToken": token} if token else {}),
        }

    answers = iter(
        [httpx.Response(200, json=page(5, "next")), httpx.Response(200, json=page(6, None))]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return next(answers)

    found = _bigquery(handler).rows(
        ATTEMPTS,
        "slot_start",
        T0,
        T0 + minutes(15),
        {"source": SOURCE, "source_native_id": ["730", "570"]},
    )
    assert found == [
        {
            "slot_start": T0,
            "value": v,
            "quality_flags": ["late"],
            "detail": {"a": 1},
            "error_code": None,
        }
        for v in (5, 6)
    ]
    request = json.loads(seen[0].content)
    assert request["maximumBytesBilled"] == str(1024**3)
    assert request["useLegacySql"] is False and request["location"] == "us-central1"
    assert request["query"] == (
        "SELECT * FROM `project.ms_raw.sample_attempts` WHERE `slot_start` BETWEEN "
        "@range_start AND @range_end AND `source` = @p0 AND `source_native_id` IN UNNEST(@p1)"
    )
    assert SOURCE not in request["query"]  # values travel as parameters, never as SQL text
    assert [p["name"] for p in request["queryParameters"]] == [
        "range_start",
        "range_end",
        "p0",
        "p1",
    ]
    assert seen[1].url.params["pageToken"] == "next"
    with pytest.raises(ValueError, match="unsafe"):
        _bigquery(handler).rows(ATTEMPTS, "slot_start; DROP", T0, T0)
    with pytest.raises(ValueError):
        TableRef("ms_raw", "x`; DROP")
    with pytest.raises(ValueError, match="byte limit"):
        BigQueryWarehouse(httpx.Client(), "p", "l", lambda: "", max_bytes_billed=0)


def test_a_bigquery_error_carries_a_status_and_a_reason_and_nothing_else() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error": {"message": SECRET_SHAPED, "errors": [{"reason": "accessDenied"}]}},
        )

    with pytest.raises(WarehouseError) as failure:
        _bigquery(handler).rows(ATTEMPTS, "slot_start", T0, T0)
    assert str(failure.value) == "BigQuery answered 403 accessDenied"
