"""The job commands, offline: markers, collectors, dispatcher, warehouse, smoke.

Every store is in memory and every source is a recording or a script. Nothing here
reaches a network, and the real source policies are the default.
"""

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from metaspike import cli
from metaspike.catalogue.seed import BOOTSTRAP_ANCHORS
from metaspike.domain.samples import SampleStatus
from metaspike.domain.signals import SourceErrorCode
from metaspike.domain.source_records import SourceRecord
from metaspike.jobs import runlog
from metaspike.jobs.baseline_jobs import features, parity
from metaspike.jobs.catalogue_job import EVIDENCE_OBJECT, collect_catalogue
from metaspike.jobs.collect import collect_samples, collect_wikimedia
from metaspike.jobs.commands import JOBS
from metaspike.jobs.dispatch import dispatch, due_jobs
from metaspike.jobs.runtime import POLICIES, Refused, production_policy, runtime_from_environment
from metaspike.jobs.smoke import (
    ADMINISTRATOR,
    ALLOWED,
    EXPECTED_DENIAL,
    NOBODY,
    NOT_APPLICABLE,
    RUNTIME,
    SHAPES,
    UNEXPECTED_ALLOW,
    UNEXPECTED_FAILURE,
    SmokeFailed,
    smoke,
)
from metaspike.jobs.targets import (
    BOOTSTRAP_VERSION,
    TARGETS_OBJECT,
    Target,
    TargetsDocument,
    bootstrap_targets,
    load_targets,
    publish_targets,
)
from metaspike.jobs.warehouse_job import (
    PENDING_OBJECT,
    PendingRebuild,
    normal_window_start,
    warehouse,
)
from metaspike.resolution.recorded import load_recorded_catalogue
from metaspike.sources.base import SourceMode
from metaspike.sources.replay import replay_client
from metaspike.sources.wikimedia import SOURCE_ID as WIKIMEDIA
from metaspike.sources.wikimedia import WikimediaPageviewsAdapter
from metaspike.warehouse.landing import MANIFEST_SUFFIX, Table, read_manifest, sample_attempt_row
from metaspike.warehouse.markers import Completion, SlotMarkers, marker_name
from metaspike.warehouse.objects import MemoryObjectStore
from metaspike.warehouse.tables import TableRef
from tests.conftest import RECORDINGS
from tests.test_sampling import SLOT, SOURCE, T0, FakeSampler, Ticker, attempt, minutes, target
from tests.warehouse_support import World, cleared_policy, real_policy, runtime_for, world

FIXTURES = Path(__file__).parent / "fixtures"
# Two days after the last recorded day: with Wikimedia's one-day publication lag the
# latest period due is the last one in the recordings.
NOW = datetime(2026, 10, 5, 6, 0, tzinfo=UTC)
DUE = datetime(2026, 10, 3, tzinfo=UTC)
RUNS = TableRef("ms_ops", "collection_runs")


def at(moment: datetime = NOW) -> World:
    return world(lambda: moment, policy_for=real_policy)


def wikimedia() -> WikimediaPageviewsAdapter:
    return WikimediaPageviewsAdapter(replay_client(RECORDINGS), sleep=lambda _: None)


def data_objects(w: World, prefix: str = "") -> list[str]:
    return [n for n in w.landing.list(prefix) if n.endswith(".ndjson")]


# --- the reviewed policies are the only policies -------------------------------------------


def test_production_jobs_use_the_reviewed_policies_and_nothing_else() -> None:
    assert set(POLICIES) == {
        "igdb", "steam_catalogue", "steam_current_players", "wikimedia_pageviews"
    }  # fmt: skip
    for source, policy in POLICIES.items():
        assert production_policy(source) is policy is real_policy(source)
    assert set(JOBS) == {
        "dispatch", "collect-steam-players", "collect-wikimedia", "collect-catalogue",
        "warehouse", "features", "parity",
    }  # fmt: skip


def test_a_job_does_not_start_without_its_configuration(capsys: pytest.CaptureFixture[str]) -> None:
    for command in JOBS:
        assert cli.main([command]) == 2
    assert "METASPIKE_STAGE" in capsys.readouterr().err
    with pytest.raises(Exception, match="not a deployment stage"):
        runtime_from_environment(
            "x",
            {
                "METASPIKE_STAGE": "anything",
                "METASPIKE_PROJECT": "p",
                "METASPIKE_REGION": "r",
                "METASPIKE_IMAGE": "i",
            },
        )


# --- D1: completion markers ----------------------------------------------------------------


def landed_manifest(w: World, run: str = "run-a") -> Any:
    row = sample_attempt_row(attempt(T0 + minutes(1), attempt_id="m1"))
    return w.writer.land(Table.SAMPLE_ATTEMPTS, SOURCE, run, T0 + minutes(2), [row])


def test_a_marker_exists_only_after_a_committed_batch_and_names_it() -> None:
    w = world(lambda: T0 + minutes(3))
    markers = SlotMarkers(w.operations, w.now)
    assert markers.completed(SOURCE, "concurrent_players", "730", T0) is None
    manifest = landed_manifest(w)
    marker = markers.complete(manifest, "concurrent_players", "730", T0, Completion.OBSERVED)
    assert markers.completed(SOURCE, "concurrent_players", "730", T0) == marker
    assert (marker.data_object, marker.content_sha256, marker.collection_run_id) == (
        manifest.data_object, manifest.content_sha256, "run-a"
    )  # fmt: skip
    # The batch it points at is really there, with its manifest.
    assert read_manifest(w.landing, manifest.data_object.replace(".ndjson", MANIFEST_SUFFIX))
    # Operational metadata only: no value, no payload.
    stored = json.loads(w.operations.get(marker_name(SOURCE, "concurrent_players", "730", T0)).data)
    assert set(stored) == {
        "format", "source", "metric", "source_native_id", "slot_start", "completion",
        "collection_run_id", "data_object", "content_sha256", "completed_at",
    }  # fmt: skip
    # Another target, another slot and another metric have no marker.
    assert markers.completed(SOURCE, "concurrent_players", "570", T0) is None
    assert markers.completed(SOURCE, "concurrent_players", "730", T0 + SLOT.step) is None


def test_a_marker_is_create_only_and_a_damaged_one_never_skips_a_sample() -> None:
    w = world(lambda: T0 + minutes(3))
    markers = SlotMarkers(w.operations, w.now)
    first = markers.complete(
        landed_manifest(w, "run-a"), "concurrent_players", "730", T0, Completion.OBSERVED
    )
    second = markers.complete(
        landed_manifest(w, "run-b"), "concurrent_players", "730", T0, Completion.OBSERVED
    )
    assert second == first and second.collection_run_id == "run-a"  # the first stands

    name = marker_name(SOURCE, "concurrent_players", "570", T0)
    w.operations.create(name, b"{not json")
    assert markers.completed(SOURCE, "concurrent_players", "570", T0) is None
    # A marker that is valid but about a different target is not this target's marker.
    other = marker_name(SOURCE, "concurrent_players", "999", T0)
    w.operations.create(other, first.model_dump_json().encode())
    assert markers.completed(SOURCE, "concurrent_players", "999", T0) is None
    assert "730" not in name and "/" not in name.rsplit("/", 1)[1].removesuffix(".json")


def test_a_collector_can_use_markers_with_only_its_own_prefix_and_no_listing() -> None:
    w = world(lambda: T0 + minutes(3))
    own = w.operations.view(
        writable_prefixes=(f"{SOURCE}/",), readable_prefixes=(f"{SOURCE}/",), can_list=False
    )
    markers = SlotMarkers(own, w.now)
    markers.complete(landed_manifest(w), "concurrent_players", "730", T0, Completion.OBSERVED)
    assert markers.completed(SOURCE, "concurrent_players", "730", T0) is not None


# --- sampling collector --------------------------------------------------------------------


def sampling_runtime(w: World, clock: Ticker, run_id: str = "run-0001") -> Any:
    runtime, log = runtime_for(w, "ms-steam-players", policy_for=cleared_policy, run_id=run_id)
    runtime.now = clock
    return runtime, log


def test_a_durable_result_stops_a_retry_from_asking_the_source_again() -> None:
    clock = Ticker(T0 + minutes(1))
    w = world(clock, policy_for=cleared_policy)
    targets = [target("730"), target("570", None)]
    runtime, log = sampling_runtime(w, clock)
    sampler = FakeSampler(clock, 100, 200)
    summary = collect_samples(runtime, sampler, targets)
    assert sampler.asked == ["570", "730"] and summary["attempts_landed"] == 2
    assert len(data_objects(w)) == 1 and summary["requests_sent"] == 2
    assert [e["state"] for e in log if e["event"] == "slot_resolved"] == ["observed", "observed"]

    retry, _ = sampling_runtime(w, clock, "run-0002")
    again = FakeSampler(clock)  # an empty script: asking would raise
    summary = collect_samples(retry, again, targets)
    assert again.asked == [] and summary["targets_skipped_already_done"] == 2
    assert summary["attempts_landed"] == 0 and len(data_objects(w)) == 1


def test_a_crash_before_the_marker_repeats_the_request_and_loses_nothing() -> None:
    clock = Ticker(T0 + minutes(1))
    w = world(clock, policy_for=cleared_policy)
    runtime, _ = sampling_runtime(w, clock)
    real = runtime.operations.create

    def die(name: str, data: bytes, content_type: str = "application/json") -> int:
        raise RuntimeError("the job was stopped before it could mark the slot")

    w.operations.create = die  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        collect_samples(runtime, FakeSampler(clock, 100), [target("730")])
    w.operations.create = real  # type: ignore[method-assign]
    assert len(data_objects(w)) == 1  # the result is durable
    assert w.operations.list(f"{SOURCE}/slots/") == ()  # and nothing says so

    retry, _ = sampling_runtime(w, clock, "run-0002")
    sampler = FakeSampler(clock, 105)
    collect_samples(retry, sampler, [target("730")])
    assert sampler.asked == ["730"]  # asked again: at-least-once, never a skipped sample
    assert len(data_objects(w)) == 2 and len(w.operations.list(f"{SOURCE}/slots/")) == 1
    w.load_everything()
    stored = w.warehouse.all_rows(TableRef("ms_raw", "sample_attempts"))
    assert sorted(row["value"] for row in stored) == [100, 105]  # both kept, neither lost


def test_a_failure_or_refusal_never_marks_a_slot_done() -> None:
    clock = Ticker(T0 + minutes(1))
    w = world(clock, policy_for=cleared_policy)
    runtime, log = sampling_runtime(w, clock)
    sampler = FakeSampler(clock, SampleStatus.FAILED, SampleStatus.UNAVAILABLE, 7)
    collect_samples(runtime, sampler, [target("1"), target("2"), target("3")])
    markers = SlotMarkers(w.operations, clock)
    done = {
        app: markers.completed(SOURCE, "concurrent_players", app, T0) for app in ("1", "2", "3")
    }
    assert done["1"] is None  # failed: may be retried
    assert done["2"] is not None and done["2"].completion is Completion.UNAVAILABLE
    assert done["3"] is not None and done["3"].completion is Completion.OBSERVED

    halting = FakeSampler(clock, SampleStatus.FAILED)
    halting.halt_on = SourceErrorCode.UNAUTHORIZED
    retry, log = sampling_runtime(w, clock, "run-0002")
    summary = collect_samples(retry, halting, [target("1"), target("4"), target("5")])
    assert halting.asked == ["1"] and summary["halted_by"] == "unauthorized"
    assert summary["attempts_landed"] == 3  # one failure, two recorded refusals
    refused = [e for e in log if e["event"] == "source_refused"]
    assert refused and refused[0]["error_code"] == "unauthorized"
    assert all(markers.completed(SOURCE, "concurrent_players", a, T0) is None for a in "145")


def test_the_real_steam_policy_refuses_the_collector_before_anything_happens() -> None:
    clock = Ticker(T0 + minutes(1))
    w = world(clock, policy_for=real_policy)
    runtime, log = runtime_for(w, "ms-steam-players")
    sampler = FakeSampler(clock, 100)
    with pytest.raises(Refused, match="not cleared for private_collection"):
        collect_samples(runtime, sampler, [target("730")])
    assert sampler.asked == [] and data_objects(w) == []

    code = runlog.run_job(
        runtime,
        runlog.landed(runtime, SOURCE),
        lambda: collect_samples(runtime, sampler, [target("730")]),
        source=SOURCE,
    )
    assert code == runlog.EXIT_REFUSED == 5 and sampler.asked == []
    assert [e["event"] for e in log] == ["job_started", "job_refused"]
    # Only the run's own operational record was written: no sample, no marker.
    assert all("/collection_runs/" in name for name in w.landing.list(""))
    assert w.operations.list("") == ()


# --- Wikimedia collector -------------------------------------------------------------------


def test_one_wikimedia_pass_lands_one_batch_marks_it_and_a_second_pass_asks_nothing() -> None:
    w = at()
    runtime, log = runtime_for(w, "ms-wikimedia")
    summary = collect_wikimedia(runtime, wikimedia())
    recorded = sorted(p.stem for p in RECORDINGS.glob("*.json"))
    assert summary["targets_collected"] == len(recorded) == 6
    assert summary["slot"] == DUE.isoformat() and summary["window_days"] == 90
    assert summary["rows_landed"] == 6 * 90 and summary["catalogue"] == "bootstrap"
    assert summary["requests_sent"] == 14  # every bootstrap entity with an article
    assert summary["targets_failed"] == 8 and summary["outcome"] == "partial"
    assert data_objects(w) == [summary["data_object"]]
    marked = w.operations.list(f"{WIKIMEDIA}/slots/pageviews/dt=2026-10-03/")
    assert len(marked) == 6
    assert {e["error_code"] for e in log if e["event"] == "source_failed"} == {"not_found"}

    assert w.load_everything() == 540
    stored = w.warehouse.all_rows(TableRef("ms_raw", "period_observations"))
    assert {row["source_identifier"] for row in stored} == {
        f"en.wikipedia.org/{a}" for a in recorded
    }
    assert all(row["source_timestamp"] == row["period_start"] for row in stored)

    again, _ = runtime_for(w, "ms-wikimedia", run_id="run-0002")
    second = collect_wikimedia(again, wikimedia())
    assert second["targets_skipped_already_done"] == 6 and second["targets_collected"] == 0
    assert second["requests_sent"] == 8  # only the ones that have no durable result
    assert len(data_objects(w)) == 1


def test_wikimedia_stops_asking_when_the_source_refuses_the_rate() -> None:
    w = at()
    runtime, log = runtime_for(w, "ms-wikimedia")

    class Refusing:
        source = WIKIMEDIA
        mode = SourceMode.FETCH_HISTORY
        asked = 0

        def fetch(self, *_: Any) -> Any:
            from metaspike.sources.base import SourceError

            self.asked += 1
            raise SourceError(SourceErrorCode.RATE_LIMITED, "429")

    adapter = Refusing()
    summary = collect_wikimedia(runtime, adapter)  # type: ignore[arg-type]
    assert adapter.asked == 1 and summary["halted_by"] == "rate_limited"
    assert summary["rows_landed"] == 0 and data_objects(w) == []
    assert [e["event"] for e in log if e["event"].startswith("source_")] == ["source_refused"]


def test_the_wikimedia_job_records_its_run_and_exits_by_outcome() -> None:
    w = at()
    runtime, log = runtime_for(w, "ms-wikimedia")
    code = runlog.run_job(
        runtime,
        runlog.landed(runtime, WIKIMEDIA),
        lambda: collect_wikimedia(runtime, wikimedia()),
        source=WIKIMEDIA,
    )
    assert code == 0 and next(e["event"] for e in log) == "job_started"
    assert log[-1]["event"] == "job_finished" and log[-1]["rows_landed"] == 540
    w.load_everything()
    runs = sorted(w.warehouse.all_rows(RUNS), key=lambda r: r["phase"], reverse=True)
    assert [(r["phase"], r["outcome"]) for r in runs] == [
        ("started", None),
        ("finished", "partial"),
    ]
    assert runs[1]["requests_sent"] == 14 and runs[1]["detail"]["rows_landed"] == 540
    assert runs[1]["job"] == "ms-wikimedia" and runs[1]["source"] == WIKIMEDIA
    assert runs[1]["policy_version"].startswith(real_policy(WIKIMEDIA).reviewed_at.isoformat())

    def explode() -> Mapping[str, Any]:
        raise RuntimeError("the bucket is on fire")

    failing, log = runtime_for(w, "ms-wikimedia", run_id="run-0009")
    assert runlog.run_job(failing, runlog.landed(failing, WIKIMEDIA), explode) == 1
    assert log[-1]["event"] == "job_failed" and log[-1]["severity"] == "ERROR"


# --- D2: the dispatcher and the expected-target ledger -------------------------------------


class Starter:
    def __init__(self, failing: str | None = None) -> None:
        self.started: list[str] = []
        self.failing = failing

    def start(self, job: str) -> str:
        if job == self.failing:
            raise RuntimeError("HTTP 500")
        self.started.append(job)
        return f"executions/{job}-1"


STEAM_TARGETS = TargetsDocument(
    version="g00000007-abc",
    generated_at=NOW,
    targets=(
        *bootstrap_targets(NOW).targets,
        *(
            Target(
                source=SOURCE,
                metric="concurrent_players",
                granularity=SLOT,
                mode=SourceMode.SAMPLE_NOW,
                source_native_id=app,
            )
            for app in ("730", "570")
        ),
    ),
)


def test_the_schedule_starts_each_job_only_when_it_is_due() -> None:
    both = {SOURCE, WIKIMEDIA}
    day = datetime(2026, 10, 5, tzinfo=UTC)
    assert due_jobs(day.replace(hour=2), both, True) == [
        "ms-steam-players",
        "ms-wikimedia",
        "ms-warehouse",
    ]
    assert due_jobs(day.replace(hour=2, minute=15), both, True) == [
        "ms-steam-players",
        "ms-features",
    ]
    assert due_jobs(day.replace(hour=1), both, True) == [
        "ms-steam-players",
        "ms-catalogue",
        "ms-warehouse",
    ]
    assert due_jobs(day.replace(hour=1), both, False) == ["ms-steam-players", "ms-warehouse"]
    assert due_jobs(day.replace(hour=4), {WIKIMEDIA}, True) == ["ms-warehouse", "ms-parity"]
    assert due_jobs(day.replace(hour=9, minute=30), {WIKIMEDIA}, True) == []


def test_the_dispatcher_records_intent_only_for_cleared_sources_then_starts_what_is_due() -> None:
    tick = datetime(2026, 10, 5, 2, 0, tzinfo=UTC)
    w = at(tick + timedelta(seconds=20))
    publish_targets(w.operations, STEAM_TARGETS)
    runtime, _ = runtime_for(w, "ms-dispatch")  # the real policies: Steam is not cleared
    starter = Starter()
    summary = dispatch(runtime, starter)
    assert starter.started == ["ms-wikimedia", "ms-warehouse"]
    assert summary["schedule_version"] == "g00000007-abc" and summary["expected_targets"] == 14
    w.load_everything()
    ledger = w.warehouse.all_rows(TableRef("ms_ops", "expected_targets"))
    assert {row["source"] for row in ledger} == {WIKIMEDIA}  # nothing about Steam at all
    assert {row["slot_start"] for row in ledger} == {datetime(2026, 10, 3, tzinfo=UTC)}
    assert {row["mode"] for row in ledger} == {"fetch_history"}
    assert {row["dispatch_run_id"] for row in ledger} == {"tick-20261005T0200Z"}
    assert {row["recorded_at"] for row in ledger} == {w.now()}
    assert not [n for n in w.landing.list("") if n.startswith(SOURCE)]

    # The same tick again: the lease stops it, and the intent is not recorded twice.
    again = Starter()
    assert dispatch(runtime, again)["outcome"] == "already_dispatched"
    assert again.started == [] and len(data_objects(w, "dispatch/")) == 1


def test_a_cleared_sampled_source_is_expected_in_every_tick() -> None:
    tick = datetime(2026, 10, 5, 9, 30, tzinfo=UTC)
    w = world(lambda: tick + timedelta(seconds=5), policy_for=cleared_policy)
    publish_targets(w.operations, STEAM_TARGETS)
    runtime, _ = runtime_for(w, "ms-dispatch")
    starter = Starter()
    summary = dispatch(runtime, starter)
    assert starter.started == ["ms-steam-players"] and summary["expected_targets"] == 2
    w.load_everything()
    ledger = w.warehouse.all_rows(TableRef("ms_ops", "expected_targets"))
    assert {(r["source_native_id"], r["slot_start"], r["mode"]) for r in ledger} == {
        ("730", tick, "sample_now"), ("570", tick, "sample_now")
    }  # fmt: skip


def test_without_a_catalogue_generation_the_plan_is_the_bootstrap_articles() -> None:
    w = at()
    document = load_targets(w.operations, NOW)
    assert document.version == BOOTSTRAP_VERSION and len(document.targets) == 14
    assert {t.source for t in document.targets} == {WIKIMEDIA}
    w.operations.create(TARGETS_OBJECT + ".x", b"")  # an unrelated object changes nothing
    assert load_targets(w.operations, NOW) == document


def test_a_job_that_cannot_be_started_fails_the_dispatch() -> None:
    tick = datetime(2026, 10, 5, 2, 0, tzinfo=UTC)
    w = at(tick)
    runtime, log = runtime_for(w, "ms-dispatch")
    code = runlog.run_job(
        runtime,
        runlog.landed(runtime, "dispatch"),
        lambda: dispatch(runtime, Starter("ms-warehouse")),
    )
    assert code == 1
    assert [e["target_job"] for e in log if e["event"] == "job_start_failed"] == ["ms-warehouse"]


# --- catalogue job -------------------------------------------------------------------------


def igdb_records() -> list[SourceRecord]:
    recorded = load_recorded_catalogue(FIXTURES / "catalogue")
    return [recorded[key] for key in BOOTSTRAP_ANCHORS.values() if key in recorded]


def never() -> Sequence[SourceRecord]:
    raise AssertionError("an uncleared source was fetched")


def test_the_catalogue_job_collects_cleared_sources_and_refuses_the_rest() -> None:
    w = at()
    state = MemoryObjectStore()
    runtime, log = runtime_for(w, "ms-catalogue", state=state)
    fetchers = {"igdb": igdb_records, "steam_catalogue": never}
    summary = collect_catalogue(runtime, fetchers)
    assert summary["sources_collected"] == ["igdb"] and summary["outcome"] == "partial"
    assert "not cleared for private_collection" in summary["sources_refused"]["steam_catalogue"]
    assert [e["source"] for e in log if e["event"] == "source_portion_refused"] == [
        "steam_catalogue"
    ]
    assert summary["records_changed"] == len(igdb_records()) > 5
    generation = summary["generation"]
    assert state.get("catalogue/CURRENT").data and state.get(EVIDENCE_OBJECT).data

    areas = {name.split("/")[0] for name in w.landing.list("")}
    assert areas == {"igdb", "catalogue"}  # nothing under any Steam prefix
    assert w.load_everything() > 0
    entities = w.warehouse.all_rows(TableRef("ms_catalogue", "entity_events"))
    assert len(entities) >= 15 and {e["event_type"] for e in entities} == {"created"}
    assert all(e["previous"] is None and e["catalogue_generation"] == generation for e in entities)
    assert {e["schema_version"] for e in entities} == {"entity_events_v1"}
    lifecycle = w.warehouse.all_rows(TableRef("ms_catalogue", "lifecycle_events"))
    assert {e["event_type"] for e in lifecycle} == {"state_recorded"}
    assert all(e["new_state"] and e["valid_from"] and e["rule_version"] for e in lifecycle)
    first = [e for e in lifecycle if e["previous_state"] is None]
    assert len(first) == len(entities)
    assert len(w.warehouse.all_rows(TableRef("ms_raw", "source_records"))) == len(igdb_records())
    targets = load_targets(w.operations, NOW)
    assert targets.version == generation and load_targets(state, NOW) == targets

    # A second run with nothing new changes nothing and lands nothing.
    before = w.landing.list("")
    again, _ = runtime_for(w, "ms-catalogue", state=state, run_id="run-0002")
    second = collect_catalogue(again, fetchers)
    assert second["records_changed"] == 0 and second["generation"] is None
    assert w.landing.list("") == before


def test_a_changed_entity_is_recorded_with_what_it_was_before() -> None:
    w = at()
    state = MemoryObjectStore()
    runtime, _ = runtime_for(w, "ms-catalogue", state=state)
    records = igdb_records()
    collect_catalogue(runtime, {"igdb": lambda: records[:6]})
    later = world(lambda: NOW + timedelta(days=400), policy_for=real_policy)
    later.landing, later.operations, later.warehouse = w.landing, w.operations, w.warehouse
    again, _ = runtime_for(later, "ms-catalogue", state=state, run_id="run-0002")
    summary = collect_catalogue(again, {"igdb": lambda: records})
    assert summary["generation"] is not None
    later.load_everything()
    lifecycle = later.warehouse.all_rows(TableRef("ms_catalogue", "lifecycle_events"))
    changed = [e for e in lifecycle if e["catalogue_generation"] == summary["generation"]]
    assert changed and all(
        e["event_type"] in ("state_recorded", "pending_changed") for e in changed
    )
    recorded = [e for e in changed if e["event_type"] == "state_recorded"]
    assert all(e["previous_state"] is not None for e in recorded)
    updated = [
        e
        for e in later.warehouse.all_rows(TableRef("ms_catalogue", "entity_events"))
        if e["event_type"] == "updated"
    ]
    assert all(e["previous"] is not None and e["previous"] != e["entity"] for e in updated)


def test_the_catalogue_job_is_refused_when_no_source_is_cleared() -> None:
    w = at()
    runtime, _ = runtime_for(w, "ms-catalogue")
    with pytest.raises(Refused, match="steam_catalogue"):
        collect_catalogue(runtime, {"steam_catalogue": never})
    assert w.landing.list("") == ()


# --- D8: the warehouse job and late data ---------------------------------------------------


class Dbt:
    def __init__(self, code: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.code = code

    def __call__(self, arguments: Sequence[str], environment: Mapping[str, str]) -> int:
        self.calls.append(list(arguments))
        assert environment["MS_GCP_PROJECT"] == "project"
        return self.code

    def variables(self, call: int = -1) -> dict[str, Any]:
        arguments = self.calls[call]
        return dict(json.loads(arguments[arguments.index("--vars") + 1]))

    def selector(self, call: int = -1) -> str:
        arguments = self.calls[call]
        return arguments[arguments.index("--selector") + 1]


def land_attempts(w: World, slot: datetime, run: str) -> None:
    rows = [
        sample_attempt_row(attempt(slot + minutes(1), attempt_id=f"{run}-1", scheduled_for=slot))
    ]
    w.writer.land(Table.SAMPLE_ATTEMPTS, SOURCE, run, w.now(), rows)


def test_a_normal_warehouse_run_loads_and_builds_the_recent_window() -> None:
    now = datetime(2026, 10, 9, 6, 0, tzinfo=UTC)
    w = world(lambda: now, policy_for=cleared_policy)
    land_attempts(w, datetime(2026, 10, 8, 12, 0, tzinfo=UTC), "run-a")
    runtime, log = runtime_for(w, "ms-warehouse")
    dbt = Dbt()
    summary = warehouse(runtime, dbt)
    assert summary["batches_loaded"] == 1 and summary["rows_loaded"] == 1
    assert summary["selector"] == "hourly" and summary["rebuilt_from"] == {}
    assert dbt.variables() == {"lookback_days": 3, "run_as_of": "2026-10-09 06:00:00"}
    assert dbt.calls[0][:4] == ["/opt/dbt/bin/dbt", "build", "--target", "prod"]
    assert normal_window_start(now) == datetime(2026, 10, 6, tzinfo=UTC)
    assert not [e for e in log if e["event"] == "late_data_loaded"]
    assert warehouse(runtime, dbt)["batches_loaded"] == 0  # and again: nothing to load


def test_late_data_is_rebuilt_from_where_it_reaches_back_to() -> None:
    now = datetime(2026, 10, 9, 6, 0, tzinfo=UTC)
    w = world(lambda: now, policy_for=cleared_policy)
    late_slot = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)  # eleven days back
    land_attempts(w, late_slot, "run-late")
    land_attempts(w, datetime(2026, 10, 8, 12, 0, tzinfo=UTC), "run-fresh")
    runtime, log = runtime_for(w, "ms-warehouse")
    dbt = Dbt()
    summary = warehouse(runtime, dbt)
    assert dbt.variables()["rebuild_from_samples"] == "2026-09-28 00:00:00"
    assert summary["rebuilt_from"] == {"samples": late_slot.isoformat()}
    late = [e for e in log if e["event"] == "late_data_loaded"]
    assert (
        len(late) == 1
        and late[0]["severity"] == "WARNING"
        and late[0]["table"] == "sample_attempts"
    )
    # Rebuilt and done: the next run is a normal one.
    assert json.loads(w.operations.get(PENDING_OBJECT).data) == {"since": {}}
    warehouse(runtime, dbt)
    assert "rebuild_from_samples" not in dbt.variables()


def test_a_failed_build_keeps_the_pending_rebuild_and_fails_the_job() -> None:
    now = datetime(2026, 10, 9, 6, 0, tzinfo=UTC)
    w = world(lambda: now, policy_for=cleared_policy)
    land_attempts(w, datetime(2026, 9, 28, 12, 0, tzinfo=UTC), "run-late")
    runtime, log = runtime_for(w, "ms-warehouse")
    code = runlog.run_job(runtime, runlog.direct(runtime), lambda: warehouse(runtime, Dbt(code=1)))
    assert code == 1
    failed = [e for e in log if e["event"] == "warehouse_failed"]
    assert failed and failed[0]["pending_rebuild"] == {"samples": "2026-09-28T12:00:00+00:00"}
    pending = PendingRebuild.model_validate_json(w.operations.get(PENDING_OBJECT).data)
    assert set(pending.since) == {"samples"}
    runs = w.warehouse.all_rows(RUNS)  # this job records its own run directly
    assert {(r["phase"], r["outcome"]) for r in runs} == {("started", None), ("finished", "failed")}
    assert {r["landed_object"].split("/")[0] for r in runs} == {"direct"}

    # The batch is loaded and has its receipt, so the next run loads nothing new, and
    # still rebuilds: the knowledge that late data arrived survived the failure.
    retry, _ = runtime_for(w, "ms-warehouse", run_id="run-0002")
    dbt = Dbt()
    summary = warehouse(retry, dbt)
    assert summary["batches_loaded"] == 0
    assert dbt.variables()["rebuild_from_samples"] == "2026-09-28 00:00:00"
    assert json.loads(w.operations.get(PENDING_OBJECT).data) == {"since": {}}


def test_a_crash_between_the_load_and_its_receipt_cannot_lose_the_late_record() -> None:
    now = datetime(2026, 10, 9, 6, 0, tzinfo=UTC)
    w = world(lambda: now, policy_for=cleared_policy)
    land_attempts(w, datetime(2026, 9, 28, 12, 0, tzinfo=UTC), "run-late")
    runtime, _ = runtime_for(w, "ms-warehouse")
    real = w.operations.create

    def die_at_receipt(name: str, data: bytes, content_type: str = "application/json") -> int:
        if name.startswith("warehouse/loads/"):
            raise RuntimeError("stopped before the receipt")
        return real(name, data, content_type)

    w.operations.create = die_at_receipt  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        warehouse(runtime, Dbt())
    w.operations.create = real  # type: ignore[method-assign]
    pending = PendingRebuild.model_validate_json(w.operations.get(PENDING_OBJECT).data)
    assert set(pending.since) == {"samples"}  # recorded before the receipt, not after
    dbt = Dbt()
    summary = warehouse(runtime, dbt)
    assert summary["batches_loaded"] == 1  # recovered, not loaded twice
    assert len(w.warehouse.all_rows(TableRef("ms_raw", "sample_attempts"))) == 1
    assert "rebuild_from_samples" in dbt.variables()


def test_late_data_for_daily_models_widens_an_hourly_run() -> None:
    w = at()  # 06:00, an hourly run
    runtime, _ = runtime_for(w, "ms-wikimedia")
    collect_wikimedia(runtime, wikimedia())  # a 90-day window is "late" by construction
    job, _ = runtime_for(w, "ms-warehouse", run_id="run-0002")
    dbt = Dbt()
    summary = warehouse(job, dbt)
    assert dbt.selector() == "daily" == summary["selector"]
    assert dbt.variables()["rebuild_from_periods"] == "2026-07-06 00:00:00"
    assert json.loads(w.operations.get(PENDING_OBJECT).data) == {"since": {}}


def test_an_unreadable_pending_record_means_rebuild_everything() -> None:
    now = datetime(2026, 10, 9, 6, 0, tzinfo=UTC)
    w = world(lambda: now, policy_for=cleared_policy)
    w.operations.create(PENDING_OBJECT, b"{corrupt")
    runtime, _ = runtime_for(w, "ms-warehouse")
    dbt = Dbt()
    warehouse(runtime, dbt)
    assert dbt.selector() == "daily"
    assert {k for k in dbt.variables() if k.startswith("rebuild_from_")} == {
        "rebuild_from_samples", "rebuild_from_periods", "rebuild_from_records", "rebuild_from_runs"
    }  # fmt: skip


# --- features and parity -------------------------------------------------------------------


def test_features_and_parity_do_nothing_while_the_sampled_source_is_uncleared() -> None:
    w = at()
    for job in (features, parity):
        runtime, log = runtime_for(w, "ms-features")
        assert job(runtime)["outcome"] == "nothing_to_do"
        assert [e["event"] for e in log] == ["source_not_cleared"]
        assert w.warehouse.queries == 0


def test_features_keeps_a_state_per_target_and_parity_agrees_with_a_rebuild() -> None:
    start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    clock = Ticker(start)
    w = world(clock, policy_for=cleared_policy)
    rows = []
    for index in range(4 * 30):
        slot = start + index * SLOT.step
        rows.append(
            sample_attempt_row(
                attempt(
                    slot + minutes(1),
                    value=1000 + index,
                    attempt_id=f"f{index}",
                    scheduled_for=slot,
                )
            )
        )
    clock.at = start + timedelta(hours=31)
    w.writer.land(Table.SAMPLE_ATTEMPTS, SOURCE, "run-a", clock.at, rows)
    w.load_everything()
    state = MemoryObjectStore()
    runtime, _ = runtime_for(w, "ms-features", state=state)
    assert features(runtime)["targets"] == 1
    assert len(state.list("baseline/baseline_rules_v1/")) == 1
    checker, log = runtime_for(w, "ms-parity", state=state)
    assert parity(checker) == {
        "checked": 1,
        "without_state": 0,
        "rule_version": "baseline_rules_v1",
    }
    clock.at += timedelta(hours=1)
    assert features(runtime)["targets"] == 1  # continued from the stored state

    # A stored state that a rebuild does not reproduce is reported and fails the job.
    name = state.list("baseline/")[0]
    stored = state.get(name)
    tampered = json.loads(stored.data)
    tampered["state"]["gap_run"] = 99
    state.replace(name, json.dumps(tampered).encode(), stored.generation)
    with pytest.raises(RuntimeError, match="differ from a rebuild"):
        parity(checker)
    assert [e["event"] for e in log if e["severity"] == "ERROR"] == ["parity_mismatch"]


# --- smoke: one test per shape of storage grant --------------------------------------------


def identity(bucket: MemoryObjectStore, prefix: str, **options: Any) -> MemoryObjectStore:
    return bucket.view(writable_prefixes=(prefix,), readable_prefixes=(prefix,), **options)


def smoke_runtime(w: World, **buckets: MemoryObjectStore) -> Any:
    runtime, log = runtime_for(w, "smoke")
    runtime.buckets = {**runtime.buckets, **buckets}
    return runtime, log


def verdicts(result: Mapping[str, Any]) -> dict[str, str]:
    return {name: outcome["verdict"] for name, outcome in result["probes"].items()}


def test_smoke_shapes_are_the_least_privilege_of_each_grant() -> None:
    assert set(SHAPES) == {"user", "creator", "viewer", "bucket_viewer", "bucket_user"}
    creator = SHAPES["creator"]
    # Storage Object Creator: create, and nothing else. Not read, overwrite, delete or list.
    assert (creator.create, creator.read, creator.overwrite, creator.delete, creator.list) == (
        True, False, False, False, False
    )  # fmt: skip
    assert SHAPES["user"].delete is True and SHAPES["viewer"].create is False
    assert all(shape.bounded for name, shape in SHAPES.items() if not name.startswith("bucket_"))


def test_an_object_user_prefix_passes_and_cleans_up_itself() -> None:
    w = at()
    runtime, log = smoke_runtime(
        w, ops=identity(w.operations, "wikimedia_pageviews/", can_list=False)
    )
    result = smoke(runtime, "ops", "wikimedia_pageviews/", "dispatch/")
    assert verdicts(result) == {
        "create_inside": ALLOWED,
        "create_outside": EXPECTED_DENIAL,
        "read": ALLOWED,
        "list": EXPECTED_DENIAL,
        "overwrite": ALLOWED,
        "delete": ALLOWED,
    }
    assert result["passed"] and result["unexpected"] == [] and result["for_review"] == []
    assert result["cleanup"] == {
        "actor": RUNTIME, "object": result["cleanup"]["object"], "done": True
    }  # fmt: skip
    assert w.operations.list("") == ()  # nothing left behind
    assert log[-1]["event"] == "smoke_result" and log[-1]["severity"] == "INFO"


def test_a_create_only_prefix_passes_with_its_denials_and_leaves_cleanup_to_an_administrator() -> (
    None
):
    w = at()
    creator = w.landing.view(
        writable_prefixes=("dispatch/",), readable_prefixes=(), can_list=False, can_overwrite=False
    )
    runtime, log = smoke_runtime(w, landing=creator)
    result = smoke(runtime, "landing", "dispatch/", "catalogue/", mode="creator")
    assert verdicts(result) == {
        "create_inside": ALLOWED,
        "create_outside": EXPECTED_DENIAL,
        "read": EXPECTED_DENIAL,
        "list": EXPECTED_DENIAL,
        "overwrite": EXPECTED_DENIAL,
        "delete": EXPECTED_DENIAL,  # the design working, not a failure
    }
    assert result["passed"] and result["unexpected"] == []
    # The object is still there, exactly as created, and the result says who removes it.
    left = result["cleanup"]["object"]
    assert result["cleanup"] == {"actor": ADMINISTRATOR, "object": left, "done": False}
    assert w.landing.list("") == (left,) and "/_smoke/" in left  # never where the loader looks
    assert w.landing.get(left).data.startswith(b"metaspike smoke")
    assert log[-1]["severity"] == "INFO"
    # The administrator removes it afterwards, with access the runtime identity never had.
    w.landing.delete(left)
    assert w.landing.list("") == ()


@pytest.mark.parametrize(
    ("grant", "broken"),
    [
        ({"can_list": False}, "delete"),  # can overwrite and delete: that is objectUser
        ({"can_list": False, "can_overwrite": False, "readable": True}, "read"),
    ],
)
def test_a_create_only_identity_that_can_do_more_fails(grant: dict[str, Any], broken: str) -> None:
    w = at()
    readable = ("dispatch/",) if grant.pop("readable", False) else ()
    wider = w.landing.view(writable_prefixes=("dispatch/",), readable_prefixes=readable, **grant)
    runtime, log = smoke_runtime(w, landing=wider)
    with pytest.raises(SmokeFailed) as failure:
        smoke(runtime, "landing", "dispatch/", "catalogue/", mode="creator")
    assert broken in failure.value.result["unexpected"]
    assert failure.value.result["probes"][broken]["verdict"] == UNEXPECTED_ALLOW
    assert not failure.value.result["passed"] and log[-1]["severity"] == "ERROR"


def test_a_write_outside_the_prefix_is_an_unexpected_allow_and_is_removed() -> None:
    w = at()
    runtime, _ = smoke_runtime(w, ops=w.operations)  # an identity with no prefix limit
    with pytest.raises(SmokeFailed, match="create_outside: expected denied, observed allowed") as f:
        smoke(runtime, "ops", "wikimedia_pageviews/", "dispatch/", expect_list=True)
    assert f.value.result["probes"]["create_outside"]["verdict"] == UNEXPECTED_ALLOW
    assert f.value.result["probes"]["create_outside"]["removed"] == "allowed"
    assert w.operations.list("") == ()  # it removed what must not exist


def test_a_refused_write_that_should_have_worked_is_an_unexpected_failure() -> None:
    w = at()
    wrong, _ = smoke_runtime(w, ops=identity(w.operations, "features/", can_list=False))
    with pytest.raises(SmokeFailed, match="create_inside: expected allowed, observed denied") as f:
        smoke(wrong, "ops", "wikimedia_pageviews/", "dispatch/")
    probes = f.value.result["probes"]
    assert probes["create_inside"]["verdict"] == UNEXPECTED_FAILURE
    assert "delete" not in probes and f.value.result["cleanup"]["actor"] == NOBODY
    assert probes["read"]["verdict"] == NOT_APPLICABLE  # nothing exists to read


def test_listing_is_reported_and_only_fails_the_check_when_asked_to() -> None:
    w = at()
    lister = identity(w.operations, "wikimedia_pageviews/", can_list=True)
    runtime, _ = smoke_runtime(w, ops=lister)
    result = smoke(runtime, "ops", "wikimedia_pageviews/", "dispatch/")
    assert result["passed"] and result["for_review"] == ["list"]
    assert result["probes"]["list"]["verdict"] == UNEXPECTED_ALLOW
    with pytest.raises(SmokeFailed, match="list: expected denied, observed allowed"):
        smoke(runtime, "ops", "wikimedia_pageviews/", "dispatch/", strict_list=True)
    # Where another reviewed grant gives listing, saying so makes it expected.
    stated = smoke(runtime, "ops", "wikimedia_pageviews/", "dispatch/", expect_list=True)
    assert stated["for_review"] == [] and stated["probes"]["list"]["verdict"] == ALLOWED


def test_a_read_only_prefix_cannot_create_and_reads_an_object_placed_for_it() -> None:
    w = at()
    state = MemoryObjectStore()
    state.create("catalogue/_smoke/fixture.txt", b"placed by an administrator")
    viewer = state.view(writable_prefixes=(), readable_prefixes=("catalogue/",), can_list=False)
    runtime, _ = smoke_runtime(w, state=viewer)
    result = smoke(
        runtime, "state", "catalogue/", "baseline/", mode="viewer",
        read_object="catalogue/_smoke/fixture.txt",
    )  # fmt: skip
    assert verdicts(result) == {
        "create_inside": EXPECTED_DENIAL,
        "create_outside": EXPECTED_DENIAL,
        "read": ALLOWED,
        "list": EXPECTED_DENIAL,
    }
    assert result["passed"] and result["cleanup"] == {"actor": NOBODY, "object": None}
    assert state.list("") == ("catalogue/_smoke/fixture.txt",)  # it wrote nothing
    # With nothing placed, read is not probed and is not counted either way.
    bare = smoke(runtime, "state", "catalogue/", "baseline/", mode="viewer")
    assert bare["passed"] and bare["probes"]["read"]["verdict"] == NOT_APPLICABLE
    # A viewer that can create is not a viewer.
    writer, _ = smoke_runtime(w, state=identity(state, "catalogue/", can_list=False))
    with pytest.raises(SmokeFailed) as failure:
        smoke(writer, "state", "catalogue/", "baseline/", mode="viewer")
    assert "create_inside" in failure.value.result["unexpected"]


def test_whole_bucket_shapes_have_no_outside_and_list_is_expected() -> None:
    w = at()
    w.landing.create("igdb/_smoke/fixture.txt", b"x")
    reader = w.landing.view(writable_prefixes=(), readable_prefixes=("",), can_list=True)
    runtime, _ = smoke_runtime(w, landing=reader)
    result = smoke(
        runtime, "landing", "", mode="bucket_viewer", read_object="igdb/_smoke/fixture.txt"
    )
    assert verdicts(result) == {"create_inside": EXPECTED_DENIAL, "read": ALLOWED, "list": ALLOWED}
    tokens = MemoryObjectStore()
    owner, _ = smoke_runtime(w, tokens=tokens)
    result = smoke(owner, "tokens", "", mode="bucket_user")
    assert set(verdicts(result).values()) == {ALLOWED} and tokens.list("") == ()
    assert result["cleanup"]["actor"] == RUNTIME


def test_smoke_refuses_a_question_that_makes_no_sense() -> None:
    w = at()
    runtime, _ = smoke_runtime(w)
    for arguments, options in (
        (("ops", "a", "b/"), {}),
        (("ops", "a/", "a/b/"), {}),
        (("nowhere", "a/", "b/"), {}),
        (("ops", "a/", None), {}),
        (("ops", "", "b/"), {"mode": "bucket_user"}),
        (("ops", "a/", "b/"), {"mode": "owner"}),
    ):
        with pytest.raises(ValueError):
            smoke(runtime, *arguments, **options)  # type: ignore[arg-type]
    assert w.operations.list("") == ()
