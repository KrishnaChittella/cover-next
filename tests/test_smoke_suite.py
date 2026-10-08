"""The per-identity storage smoke suites, offline.

Each runtime identity is modelled from the Terraform grant table itself, so the
suite is checked against the permissions that are actually declared, not against a
hand-written copy of them.
"""

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from metaspike import cli
from metaspike.jobs import commands
from metaspike.jobs import runtime as runtime_module
from metaspike.jobs.smoke import (
    ALLOWED,
    EXPECTED_DENIAL,
    OUTSIDE,
    PROFILES,
    SHAPES,
    UNEXPECTED_ALLOW,
    UNEXPECTED_FAILURE,
    ProbeSet,
    checked_run_id,
    fixture_object,
    fixtures_for,
    outside_object,
    probe_object,
    smoke_suite,
)
from metaspike.warehouse.objects import (
    AccessDenied,
    MemoryObjectStore,
    ObjectNotFound,
    PreconditionFailed,
)
from tests.test_cloud_foundation import PROD, grants, read
from tests.warehouse_support import real_policy, runtime_for, world

RUN = "s1-20261007-a1"
BUCKETS = ("landing", "raw", "state", "ops", "tokens")
NOW = datetime(2026, 10, 7, 4, 0, tzinfo=UTC)
JOBS = [
    "ms-dispatch", "ms-steam-players", "ms-wikimedia", "ms-catalogue",
    "ms-warehouse", "ms-features", "ms-parity",
]  # fmt: skip
_SHAPE_OF = {
    ("roles/storage.objectUser", True): "user",
    ("roles/storage.objectCreator", True): "creator",
    ("roles/storage.objectViewer", True): "viewer",
    ("roles/storage.objectViewer", False): "bucket_viewer",
    ("roles/storage.objectUser", False): "bucket_user",
}


def declared_grants() -> list[tuple[str, str, str, str]]:
    table = grants(read(PROD / "storage.tf"), "object_grants")
    return [(i, b, r, p) for i, b, r, p in (row for row in table if len(row) == 4)]


class Cloud:
    """The five buckets, and each identity's view of them as Terraform declares it."""

    def __init__(self) -> None:
        self.buckets = {name: MemoryObjectStore() for name in BUCKETS}

    def as_identity(self, identity: str) -> dict[str, MemoryObjectStore]:
        views = {}
        for bucket in BUCKETS:
            mine = [(r, p) for i, b, r, p in declared_grants() if i == identity and b == bucket]
            writable = tuple(p for r, p in mine if r.endswith(("objectUser", "objectCreator")))
            readable = tuple(p for r, p in mine if r.endswith(("objectUser", "objectViewer")))
            views[bucket] = self.buckets[bucket].view(
                writable_prefixes=writable,
                readable_prefixes=readable,
                # Listing needs a grant on the bucket itself, not on a prefix of it.
                can_list=any(p == "" for _, p in mine),
                can_overwrite=any(r.endswith("objectUser") for r, _ in mine),
            )
        return views

    def place_fixtures(self, profile: str, run_id: str = RUN) -> list[tuple[str, str]]:
        placed = list(fixtures_for(run_id, profile))
        for bucket, name in placed:
            self.buckets[bucket].create(name, b"metaspike-storage-smoke\n", "text/plain")
        return placed

    def objects(self) -> dict[str, tuple[str, ...]]:
        return {name: store.list("") for name, store in self.buckets.items() if store.list("")}


def want(allowed: bool) -> str:
    return ALLOWED if allowed else EXPECTED_DENIAL


def run_suite(cloud: Cloud, profile: str, run_id: str = RUN, identity: str | None = None) -> Any:
    runtime, log = runtime_for(world(lambda: NOW, policy_for=real_policy), "smoke")
    runtime.buckets = cloud.as_identity(identity or profile)
    runtime.environ = {"CLOUD_RUN_JOB": profile, "CLOUD_RUN_EXECUTION": f"{profile}-abc12"}
    return smoke_suite(runtime, profile, run_id), log


# --- the profiles are the accepted matrix ----------------------------------------------------


def test_there_are_exactly_seven_profiles_one_per_runtime_job() -> None:
    assert sorted(PROFILES) == sorted(JOBS)
    assert sum(len(sets) for sets in PROFILES.values()) == 26
    assert {len(PROFILES[job]) for job in JOBS} == {2, 3, 4, 5, 8}
    assert {commands.JOBS[c][0] for c in commands.JOBS} == set(PROFILES)


def test_every_declared_storage_grant_is_probed_by_its_own_identity_and_nothing_else_is() -> None:
    declared = declared_grants()
    assert len(declared) == 27
    covered: set[tuple[str, str, str, str]] = set()
    for identity, bucket, role, prefix in declared:
        shape = _SHAPE_OF[(role, prefix != "")]
        match = [s for s in PROFILES[identity] if (s.bucket, s.prefix) == (bucket, prefix)]
        if (identity, bucket, role, prefix) == (
            "ms-dispatch", "ops", "roles/storage.objectViewer", ""
        ):  # fmt: skip
            # Its whole-bucket read is exercised through the dispatch/ probe set, which
            # expects read and list to be allowed for exactly this reason.
            (through,) = [
                s for s in PROFILES[identity] if (s.bucket, s.prefix) == ("ops", "dispatch/")
            ]
            assert (through.expect_read, through.expect_list) == (True, True)
            covered.add((identity, bucket, role, prefix))
            continue
        assert len(match) == 1 and match[0].mode == shape, (identity, bucket, role, prefix)
        covered.add((identity, bucket, role, prefix))
    assert covered == set(declared)
    # And no profile probes a grant that is not declared.
    for identity, sets in PROFILES.items():
        for probe_set in sets:
            role = next(r for (r, bounded), m in _SHAPE_OF.items()
                        if m == probe_set.mode and bounded == (probe_set.prefix != ""))  # fmt: skip
            assert (identity, probe_set.bucket, role, probe_set.prefix) in declared
    # Only the dispatcher is told to expect more than its prefix shape gives.
    overrides = {i for i, sets in PROFILES.items() for s in sets if s.expect_read or s.expect_list}
    assert overrides == {"ms-dispatch"}
    assert not any(p.startswith(OUTSIDE) for _, _, _, p in declared)  # nobody holds the outside


# --- one invocation covers the whole identity -----------------------------------------------


@pytest.mark.parametrize("profile", JOBS)
def test_one_invocation_passes_the_whole_matrix_of_its_identity(profile: str) -> None:
    cloud = Cloud()
    fixtures = cloud.place_fixtures(profile)
    document, log = run_suite(cloud, profile)
    assert document["overall"] == "PASS", document["first_unexpected"]
    assert document["first_unexpected"] is None
    assert (
        document["probe_sets_completed"] == document["probe_sets_planned"] == len(PROFILES[profile])
    )
    assert (document["run_id"], document["profile"], document["job"]) == (RUN, profile, profile)
    assert document["execution"] == f"{profile}-abc12"
    assert document["counts"][UNEXPECTED_FAILURE] == document["counts"][UNEXPECTED_ALLOW] == 0
    assert {(row["bucket"], row["prefix"], row["shape"]) for row in document["probes"]} == {
        (s.bucket, s.prefix, s.mode) for s in PROFILES[profile]
    }
    for probe_set in PROFILES[profile]:
        shape = SHAPES[probe_set.mode]
        rows = {
            r["operation"]: r
            for r in document["probes"]
            if (r["bucket"], r["prefix"]) == (probe_set.bucket, probe_set.prefix)
        }
        assert rows["create_inside"]["verdict"] == want(shape.create)
        if shape.bounded:
            assert rows["create_outside"]["verdict"] == EXPECTED_DENIAL
        else:
            assert "create_outside" not in rows
        read_ok = shape.read if probe_set.expect_read is None else probe_set.expect_read
        list_ok = shape.list if probe_set.expect_list is None else probe_set.expect_list
        assert rows["read"]["verdict"] == want(read_ok)
        assert rows["list"]["verdict"] == want(list_ok)
        if shape.create:
            assert rows["overwrite"]["verdict"] == want(bool(shape.overwrite))
            assert rows["delete"]["verdict"] == want(bool(shape.delete))
        else:
            assert "overwrite" not in rows and "delete" not in rows
    # What is left is exactly what an administrator must remove: create-only objects
    # and the fixtures. Everything else the identity removed itself.
    creators = [
        (s.bucket, probe_object(RUN, profile, s)) for s in PROFILES[profile] if s.mode == "creator"
    ]
    left = {(b, n) for b, names in cloud.objects().items() for n in names}
    assert left == set(creators) | set(fixtures)
    listed = {(c["bucket"], c["object"]) for c in document["cleanup"]}
    assert listed == left
    assert all(
        c["cleanup_required"] and c["cleanup_actor"] == "administrator" for c in document["cleanup"]
    )
    assert log[-1]["event"] == "smoke_suite_result" and log[-1]["overall"] == "PASS"


def test_the_document_holds_no_credential_and_only_the_fields_it_should() -> None:
    cloud = Cloud()
    cloud.place_fixtures("ms-wikimedia")
    document, _ = run_suite(cloud, "ms-wikimedia")
    assert set(document) == {
        "run_id", "profile", "job", "execution", "overall", "probe_sets_planned",
        "probe_sets_completed", "first_unexpected", "counts", "probes", "cleanup",
    }  # fmt: skip
    for row in document["probes"]:
        assert {"bucket", "prefix", "shape", "operation", "expected", "actual", "verdict",
                "status", "object"} <= set(row)  # fmt: skip
    text = json.dumps(document).lower()
    for forbidden in ("bearer", "authorization", "access_token", "ya29", "secret", "password"):
        assert forbidden not in text
    denied = [r for r in document["probes"] if r["verdict"] == EXPECTED_DENIAL]
    assert denied and all(r["actual"] == "denied" for r in denied)


# --- the run identifier ----------------------------------------------------------------------


def test_the_run_id_is_in_every_object_name_and_never_where_a_loader_reads() -> None:
    located = []
    for profile, sets in PROFILES.items():
        for probe_set in sets:
            located += [
                (probe_set.bucket, probe_object(RUN, profile, probe_set)),
                (probe_set.bucket, outside_object(RUN, profile, probe_set)),
                (probe_set.bucket, fixture_object(RUN, profile, probe_set)),
            ]
    assert len(located) == len(set(located)) == 78  # no two probes share an object
    names = [name for _, name in located]
    for name in names:
        assert f"/_smoke/{RUN}/" in f"/{name}" and name.endswith(".txt")
        # Loaders read {area}/{table}/dt=...; markers live under {source}/slots/.
        assert "/dt=" not in name and "/slots/" not in name and "/loads/" not in name
        assert not name.startswith("catalogue/CURRENT")
    cloud = Cloud()
    cloud.place_fixtures("ms-catalogue")
    run_suite(cloud, "ms-catalogue")
    for objects in cloud.objects().values():
        assert all(f"_smoke/{RUN}/" in name for name in objects)
    # Deterministic: the same inputs name the same fixture, on any machine.
    parity = PROFILES["ms-parity"][0]
    assert (
        fixture_object(RUN, "ms-parity", parity) == f"baseline/_smoke/{RUN}/ms-parity-fixture.txt"
    )
    assert fixtures_for(RUN, "ms-warehouse") == (
        ("landing", f"_smoke/{RUN}/ms-warehouse-fixture.txt"),
        ("raw", f"_smoke/{RUN}/ms-warehouse-fixture.txt"),
    )
    assert fixtures_for(RUN, "ms-features") == ()


@pytest.mark.parametrize(
    "bad", ["", "a", "../x", "a/b", "a b", "-lead", ".lead", "x" * 64, "s1..x", "s1/../x", "s1\n"]
)
def test_an_unsafe_run_id_is_refused_before_anything_is_touched(bad: str) -> None:
    with pytest.raises(ValueError):
        checked_run_id(bad)
    cloud = Cloud()
    with pytest.raises(ValueError):
        run_suite(cloud, "ms-features", run_id=bad)
    assert cloud.objects() == {}
    assert checked_run_id("s1-2026.10.07_a") == "s1-2026.10.07_a"


# --- stopping at the first surprise -----------------------------------------------------------


def test_the_first_unexpected_verdict_stops_every_later_probe() -> None:
    cloud = Cloud()
    cloud.place_fixtures("ms-steam-players")
    # The Steam collector is run with the catalogue job's permissions: wrong identity.
    document, log = run_suite(cloud, "ms-steam-players", identity="ms-catalogue")
    assert document["overall"] == "FAIL" and log[-1]["severity"] == "ERROR"
    first = document["first_unexpected"]
    assert (first["bucket"], first["prefix"], first["operation"]) == (
        "ops", "steam_current_players/", "create_inside"
    )  # fmt: skip
    assert first["verdict"] == UNEXPECTED_FAILURE and first["observed"] == "denied"
    assert document["probe_sets_completed"] == 0 and document["probe_sets_planned"] == 4
    assert [r["operation"] for r in document["probes"]] == ["create_inside"]  # nothing after it
    assert cloud.objects() == {"state": cloud.objects()["state"]}  # only the fixture exists


def test_an_identity_that_can_do_too_much_fails_and_what_it_created_is_reported() -> None:
    cloud = Cloud()
    # ms-dispatch's create-only landing prefix, but with an identity that can read there.
    runtime, _ = runtime_for(world(lambda: NOW, policy_for=real_policy), "smoke")
    views = cloud.as_identity("ms-dispatch")
    views["landing"] = cloud.buckets["landing"].view(
        writable_prefixes=("dispatch/",), readable_prefixes=("dispatch/",),
        can_list=False, can_overwrite=False,
    )  # fmt: skip
    runtime.buckets = views
    document = smoke_suite(runtime, "ms-dispatch", RUN)
    first = document["first_unexpected"]
    assert (first["bucket"], first["operation"], first["verdict"]) == (
        "landing", "read", UNEXPECTED_ALLOW
    )  # fmt: skip
    operations = [r["operation"] for r in document["probes"] if r["bucket"] == "landing"]
    assert operations == ["create_inside", "create_outside", "read"]  # no list, overwrite, delete
    # The object it had already created is still there and is handed to an administrator.
    created = probe_object(RUN, "ms-dispatch", PROFILES["ms-dispatch"][1])
    assert cloud.buckets["landing"].list("") == (created,)
    assert {"bucket": "landing", "object": created, "cleanup_required": True,
            "cleanup_actor": "administrator"} in document["cleanup"]  # fmt: skip


def test_an_unexpected_listing_fails_a_suite() -> None:
    cloud = Cloud()
    runtime, _ = runtime_for(world(lambda: NOW, policy_for=real_policy), "smoke")
    views = cloud.as_identity("ms-features")
    views["state"] = cloud.buckets["state"].view(
        writable_prefixes=("baseline/",), readable_prefixes=("baseline/",), can_list=True
    )
    runtime.buckets = views
    document = smoke_suite(runtime, "ms-features", RUN)
    assert document["overall"] == "FAIL"
    assert document["first_unexpected"]["operation"] == "list"
    assert document["first_unexpected"]["verdict"] == UNEXPECTED_ALLOW


# --- create-only and fixtures -----------------------------------------------------------------


def test_a_create_only_denied_delete_passes_and_returns_administrative_cleanup() -> None:
    cloud = Cloud()
    document, _ = run_suite(cloud, "ms-dispatch")
    assert document["overall"] == "PASS"
    landing = {r["operation"]: r for r in document["probes"] if r["bucket"] == "landing"}
    assert {op: landing[op]["verdict"] for op in landing} == {
        "create_inside": ALLOWED,
        "create_outside": EXPECTED_DENIAL,
        "read": EXPECTED_DENIAL,
        "list": EXPECTED_DENIAL,
        "overwrite": EXPECTED_DENIAL,
        "delete": EXPECTED_DENIAL,
    }
    created = f"dispatch/_smoke/{RUN}/ms-dispatch-probe.txt"
    assert document["cleanup"] == [
        {"bucket": "landing", "object": created, "cleanup_required": True,
         "cleanup_actor": "administrator"}
    ]  # fmt: skip
    assert cloud.buckets["landing"].get(created).data == b"metaspike-storage-smoke\n"
    # On the operations bucket it also reads everything: read and list are allowed there.
    ops = {r["operation"]: r["verdict"] for r in document["probes"] if r["bucket"] == "ops"}
    assert ops["read"] == ops["list"] == ALLOWED and ops["create_outside"] == EXPECTED_DENIAL
    assert cloud.buckets["ops"].list("") == ()


def test_a_missing_fixture_is_named_as_such_and_not_passed_off_as_a_denial() -> None:
    cloud = Cloud()  # no fixtures placed
    document, _ = run_suite(cloud, "ms-warehouse")  # this identity can list, so it is told 404
    first = document["first_unexpected"]
    assert document["overall"] == "FAIL" and first["operation"] == "read"
    assert first["observed"] == "fixture absent" and first["status"] == "HTTP 404"
    assert first["object"] == f"_smoke/{RUN}/ms-warehouse-fixture.txt"

    # An identity that cannot list is told "forbidden" for a missing object by the real
    # service. The suite cannot tell that from a denial, and says so instead of guessing.
    class Forbidding(MemoryObjectStore):
        def get(self, name: str) -> Any:
            try:
                return super().get(name)
            except ObjectNotFound:
                raise AccessDenied(f"state/{name}: HTTP 403") from None

    runtime, _ = runtime_for(world(lambda: NOW, policy_for=real_policy), "smoke")
    views = cloud.as_identity("ms-parity")
    state = Forbidding(writable_prefixes=(), readable_prefixes=("baseline/",), can_list=False)
    views["state"] = state
    runtime.buckets = views
    document = smoke_suite(runtime, "ms-parity", RUN)
    first = document["first_unexpected"]
    assert first["operation"] == "read" and first["status"] == "HTTP 403"
    assert "cannot distinguish" in first["note"] and "administrator" in first["note"]
    assert not hasattr(smoke_suite, "create_fixture")  # the suite has no way to place one


def test_the_suite_never_creates_a_fixture_itself() -> None:
    cloud = Cloud()
    run_suite(cloud, "ms-parity")
    assert not [n for names in cloud.objects().values() for n in names if "fixture" in n]


# --- source-free, secret-free, warehouse-free -------------------------------------------------


class FakeGoogle:
    """The metadata server and Cloud Storage over HTTP, enforcing one identity's grants."""

    def __init__(self, views: Mapping[str, MemoryObjectStore]) -> None:
        self.views = views
        self.hosts: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.hosts.append(request.url.host)
        if request.url.host == "metadata.google.internal":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        assert request.url.host == "storage.googleapis.com", request.url
        bucket = re.search(r"/b/project-(\w+)/o", request.url.path).group(1)  # type: ignore[union-attr]
        store = self.views[bucket]
        try:
            if request.method == "POST":
                name, generation = (
                    request.url.params["name"],
                    int(request.url.params["ifGenerationMatch"]),
                )
                made = (
                    store.create(name, request.content)
                    if generation == 0
                    else store.replace(name, request.content, generation)
                )
                return httpx.Response(200, json={"generation": str(made)})
            name = request.url.path.split("/o/", 1)[1] if "/o/" in request.url.path else ""
            name = httpx.URL(f"http://x/{name}").path[1:] if name else ""
            if request.method == "DELETE":
                store.delete(name)
                return httpx.Response(204)
            if not name:
                found = store.list(request.url.params["prefix"])
                return httpx.Response(200, json={"items": [{"name": n} for n in found]})
            found_object = store.get(name)
            return httpx.Response(
                200,
                content=found_object.data,
                headers={"x-goog-generation": str(found_object.generation)},
            )
        except AccessDenied:
            return httpx.Response(403)
        except ObjectNotFound:
            return httpx.Response(404)
        except PreconditionFailed:
            return httpx.Response(412)


def forbid(*_: Any, **__: Any) -> Any:
    raise AssertionError("a source client was constructed on the smoke path")


def test_suite_mode_reaches_only_google_auth_and_cloud_storage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cloud = Cloud()
    cloud.place_fixtures("ms-wikimedia")
    google = FakeGoogle(cloud.as_identity("ms-wikimedia"))
    real_client = httpx.Client
    built: list[str] = []

    def client(*args: Any, **options: Any) -> httpx.Client:
        built.append("client")
        return real_client(transport=httpx.MockTransport(google))

    monkeypatch.setattr("metaspike.jobs.runtime.httpx.Client", client)
    # Every way a source could be reached from the command module.
    for name in ("build_client", "build_igdb", "build_steam_players", "WikimediaPageviewsAdapter",
                 "collect_wikimedia_job", "collect_samples", "collect_catalogue", "warehouse",
                 "features", "parity", "dispatch"):  # fmt: skip
        monkeypatch.setattr(commands, name, forbid)
    monkeypatch.setattr(runtime_module, "BigQueryWarehouse", forbid)
    for key, value in {
        "METASPIKE_STAGE": "private_collection",
        "METASPIKE_PROJECT": "project",
        "METASPIKE_REGION": "us-central1",
        "METASPIKE_IMAGE": "image@sha256:" + "0" * 64,
        "CLOUD_RUN_JOB": "ms-wikimedia",
        "CLOUD_RUN_EXECUTION": "ms-wikimedia-x1",
    }.items():
        monkeypatch.setenv(key, value)
    for secret in ("METASPIKE_STEAM_WEB_API_KEY", "METASPIKE_IGDB_CLIENT_ID",
                   "METASPIKE_IGDB_CLIENT_SECRET"):  # fmt: skip
        monkeypatch.delenv(secret, raising=False)

    assert cli.main(["smoke", "--suite", "ms-wikimedia", "--run-id", RUN]) == 0
    document = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert document["overall"] == "PASS" and document["probe_sets_completed"] == 5
    assert set(google.hosts) == {"metadata.google.internal", "storage.googleapis.com"}
    assert google.hosts.count("metadata.google.internal") == 1  # one token, then storage only
    assert built == ["client"]  # one HTTP client: no second one for any source
    statuses = {row["status"] for row in document["probes"]}
    assert statuses <= {None, "HTTP 403"}  # denials are the service's own 403


def test_the_smoke_runtime_has_no_warehouse_and_touches_no_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_module, "BigQueryWarehouse", forbid)
    env = {
        "METASPIKE_STAGE": "private_collection", "METASPIKE_PROJECT": "p",
        "METASPIKE_REGION": "r", "METASPIKE_IMAGE": "i",
    }  # fmt: skip
    runtime = runtime_module.runtime_from_environment("smoke", env, storage_only=True)
    with pytest.raises(RuntimeError, match="no warehouse access"):
        runtime.warehouse.load("job", None, b"")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="no warehouse access"):
        runtime.warehouse.rows  # noqa: B018
    # The suite's own code names no secret, no source and no table.
    source = (Path(commands.__file__).parent / "smoke.py").read_text()
    for forbidden in ("secret", "igdb_auth", "steam_players", "wikimedia import", "warehouse.load",
                      "warehouse.rows", "Secret(", "credentials"):  # fmt: skip
        assert forbidden not in source, forbidden
    # In an offline run the warehouse double is never queried either.
    cloud = Cloud()
    w = world(lambda: NOW, policy_for=real_policy)
    job, _ = runtime_for(w, "smoke")
    job.buckets = cloud.as_identity("ms-features")
    assert smoke_suite(job, "ms-features", RUN)["overall"] == "PASS"
    assert w.warehouse.queries == 0


def test_a_suite_refuses_to_run_as_another_job_or_without_a_run_id(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key, value in {
        "METASPIKE_STAGE": "private_collection", "METASPIKE_PROJECT": "p",
        "METASPIKE_REGION": "r", "METASPIKE_IMAGE": "i", "CLOUD_RUN_JOB": "ms-features",
    }.items():  # fmt: skip
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(commands, "smoke_suite", forbid)
    assert cli.main(["smoke", "--suite", "ms-parity", "--run-id", RUN]) == 2
    assert "must run as its own job" in capsys.readouterr().err
    assert cli.main(["smoke", "--suite", "ms-features"]) == 2
    assert "--run-id" in capsys.readouterr().err
    assert cli.main(["smoke", "--suite", "ms-features", "--run-id", "../x"]) == 2
    assert cli.main(["smoke"]) == 2
    with pytest.raises(SystemExit):
        cli.main(["smoke", "--suite", "ms-scheduler", "--run-id", RUN])


def test_probe_sets_know_which_of_them_need_a_fixture() -> None:
    assert ProbeSet("state", "catalogue/", "viewer").needs_fixture
    assert ProbeSet("landing", "", "bucket_viewer").needs_fixture
    assert not ProbeSet("landing", "dispatch/", "creator").needs_fixture
    needing = {p: len(fixtures_for(RUN, p)) for p in PROFILES}
    assert needing == {
        "ms-dispatch": 0, "ms-steam-players": 1, "ms-wikimedia": 2, "ms-catalogue": 0,
        "ms-warehouse": 2, "ms-features": 0, "ms-parity": 1,
    }  # fmt: skip
