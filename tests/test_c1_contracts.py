"""Contracts closed in Part C1: source_timestamp, full-history native ids, internal landing."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from metaspike.catalogue.seed import bootstrap_catalogue
from metaspike.domain.signals import SignalKey
from metaspike.domain.timeseries import TimeWindow
from metaspike.pipeline.normalize import build_time_series
from metaspike.sources.replay import replay_client
from metaspike.sources.wikimedia import DAILY_PAGEVIEWS, WikimediaPageviewsAdapter
from metaspike.sources.wikimedia import SOURCE_ID as WIKIMEDIA
from metaspike.storage.local import LocalObservationStore
from metaspike.storage.samples import LocalSampleStore
from metaspike.warehouse.landing import (
    INTERNAL_AREAS,
    INTERNAL_POLICY_VERSION,
    TABLES,
    LandingError,
    Table,
    sample_attempt_row,
)
from metaspike.warehouse.loader import INDEX_HISTORY_START
from metaspike.warehouse.stores import WarehouseObservationStore, WarehouseSampleStore
from metaspike.warehouse.tables import TableRef
from tests.conftest import RECORDINGS
from tests.test_sampling import KEY, SLOT, SOURCE, T0, attempt, minutes
from tests.warehouse_support import cleared_policy, real_policy, world

NOW = datetime(2026, 10, 5, 6, 0, tzinfo=UTC)
INDEX = TableRef("ms_raw", "sample_native_ids")


# --- D6: source_timestamp survives the warehouse -------------------------------------------


def test_an_observation_round_trips_through_the_warehouse_unchanged(tmp_path: Path) -> None:
    game = next(g for g in bootstrap_catalogue().games if g.slug == "gta-v")
    identifier = game.identifier_for(WIKIMEDIA)
    assert identifier is not None
    window = TimeWindow.of_days(datetime(2026, 7, 6).date(), datetime(2026, 10, 3).date())
    adapter = WikimediaPageviewsAdapter(replay_client(RECORDINGS), sleep=lambda _: None)
    observations = adapter.fetch(game, identifier, DAILY_PAGEVIEWS, window, NOW)
    assert len(observations) == 90
    assert all(o.source_timestamp == o.period_start for o in observations)
    key = SignalKey(source=WIKIMEDIA, metric="pageviews", granularity=DAILY_PAGEVIEWS.granularity)
    series = build_time_series(game.canonical_game_id, key, window, observations, NOW)

    w = world(lambda: NOW + timedelta(hours=1), policy_for=real_policy)
    remote = WarehouseObservationStore(
        w.writer, w.warehouse, w.policy_for, now=w.now, run_id="run-1"
    )
    local = LocalObservationStore(tmp_path / "store", real_policy)
    assert remote.write(series) == local.write(series) == 90
    w.load_everything()
    stored = w.warehouse.all_rows(TableRef("ms_raw", "period_observations"))
    assert all(row["source_timestamp"] == row["period_start"] for row in stored)

    restored = remote.read(game.canonical_game_id, key, window, as_of=NOW)
    expected = local.read(game.canonical_game_id, key, window, as_of=NOW)
    assert restored == expected == series  # every field, source_timestamp included
    assert all(p.source_timestamp is not None for p in restored.points)
    assert "source_timestamp" in TABLES[Table.PERIOD_OBSERVATIONS].columns


def test_an_observation_without_a_source_timestamp_keeps_none(tmp_path: Path) -> None:
    from tests.conftest import GAME_ID, series_of
    from tests.test_warehouse_stores import observation_store, window_of

    w = world(lambda: NOW, policy_for=real_policy)
    store = observation_store(w)
    store.write(series_of([100.0, 200.0]))
    w.load_everything()
    stored = w.warehouse.all_rows(TableRef("ms_raw", "period_observations"))
    assert [row["source_timestamp"] for row in stored] == [None, None]
    from metaspike.scoring.v0 import V0_INPUT_SIGNAL
    from tests.conftest import RUN_TIME

    read = store.read(GAME_ID, V0_INPUT_SIGNAL, window_of(2), as_of=RUN_TIME)
    assert [p.source_timestamp for p in read.points] == [None, None]


# --- D7: native ids across all history, at bounded cost ------------------------------------


def test_native_ids_match_the_local_store_across_more_than_35_days(tmp_path: Path) -> None:
    start = T0 - timedelta(days=120)
    now = T0 + timedelta(days=1)
    w = world(lambda: now)
    remote = WarehouseSampleStore(w.writer, w.warehouse, w.policy_for, now=w.now)
    local = LocalSampleStore(tmp_path / "samples", cleared_policy)
    # One app last sampled 120 days ago, one 60 days ago, one 36, one yesterday.
    seen = {"10": 120, "570": 60, "730": 36, "9000": 1, "11": 0}
    batch = []
    for app, days_ago in seen.items():
        slot = T0 - timedelta(days=days_ago)
        one = attempt(slot + minutes(1), app=app, attempt_id=f"n-{app}", scheduled_for=slot)
        one = one.model_copy(update={"collection_run_id": f"run-{app}"})
        local.append(one)
        batch.append(one)
    remote.append_many(batch)
    assert remote.native_ids(KEY) == ()  # landed, not yet loaded
    w.load_everything()
    assert start < T0 - timedelta(days=35)
    assert remote.native_ids(KEY) == local.native_ids(KEY) == ("10", "11", "570", "730", "9000")
    # A different metric or granularity is a different signal with no ids.
    other = KEY.model_copy(update={"metric": "pageviews"})
    assert remote.native_ids(other) == local.native_ids(other) == ()


def test_the_native_id_index_holds_one_row_per_target_however_much_is_loaded() -> None:
    now = T0 + timedelta(days=40)
    w = world(lambda: now)
    store = WarehouseSampleStore(w.writer, w.warehouse, w.policy_for, now=w.now)
    for day in range(40):
        slot = T0 + timedelta(days=day)
        store.append_many(
            [
                attempt(
                    slot + minutes(1), app=app, attempt_id=f"d{day}-{app}", scheduled_for=slot
                ).model_copy(update={"collection_run_id": f"run-{day:03d}"})
                for app in ("570", "730")
            ]
        )
    w.load_everything()
    assert len(w.warehouse.all_rows(TableRef("ms_raw", "sample_attempts"))) == 80
    index = w.warehouse.all_rows(INDEX)
    assert sorted(row["source_native_id"] for row in index) == ["570", "730"]  # not 80 rows
    assert {row["first_slot_start"] for row in index} == {T0}
    # The read is against the index, and bounded by the same floor as every other.
    before = w.warehouse.queries
    assert store.native_ids(KEY) == ("570", "730")
    assert w.warehouse.queries == before + 1
    assert INDEX_HISTORY_START.year == 2020


def test_a_batch_has_a_receipt_only_once_its_ids_are_indexed() -> None:
    w = world(lambda: T0 + timedelta(hours=1))
    row = sample_attempt_row(attempt(T0 + minutes(1), app="730", attempt_id="x1"))
    manifest = w.writer.land(Table.SAMPLE_ATTEMPTS, SOURCE, "run-a", w.now(), [row])
    real = w.warehouse.load
    calls: list[str] = []

    def fail_the_index(job_id: str, table: TableRef, ndjson: bytes) -> object:
        calls.append(job_id)
        if table == INDEX:
            raise RuntimeError("stopped before the index was written")
        return real(job_id, table, ndjson)

    w.warehouse.load = fail_the_index  # type: ignore[assignment]
    with pytest.raises(RuntimeError):
        w.loader().load_batch(manifest)
    w.warehouse.load = real  # type: ignore[method-assign]
    assert w.operations.list("warehouse/loads/") == ()  # no receipt: the batch is not done
    assert len(w.warehouse.all_rows(TableRef("ms_raw", "sample_attempts"))) == 1
    w.loader("load-2").load_batch(manifest)  # recovered, indexed, then receipted
    assert len(w.warehouse.all_rows(TableRef("ms_raw", "sample_attempts"))) == 1
    assert [r["source_native_id"] for r in w.warehouse.all_rows(INDEX)] == ["730"]
    assert len(w.operations.list("warehouse/loads/")) == 1
    assert SLOT and len([c for c in calls if c.startswith("ms_index_")]) == 1


# --- internal landing ----------------------------------------------------------------------


def test_only_metaspikes_own_records_can_be_landed_without_a_source_policy() -> None:
    w = world(lambda: NOW, policy_for=real_policy)
    assert {"dispatch", "catalogue"} == INTERNAL_AREAS
    internal = {t for t, spec in TABLES.items() if spec.purpose is None}
    assert internal == {
        Table.COLLECTION_RUNS, Table.EXPECTED_TARGETS, Table.SAMPLE_NATIVE_IDS,
        Table.ENTITY_EVENTS, Table.LIFECYCLE_EVENTS,
    }  # fmt: skip
    # A table that holds source data cannot take the internal path around its policy.
    row = sample_attempt_row(attempt(T0 + minutes(1)))
    for table in sorted(set(TABLES) - internal):
        with pytest.raises(LandingError, match="needs a source policy"):
            w.writer.land_internal(table, "dispatch", "run-1", NOW, [row])
    assert w.landing.list("") == ()
    ledger = {
        "source": WIKIMEDIA, "metric": "pageviews", "granularity": "daily",
        "mode": "fetch_history", "source_native_id": "en.wikipedia.org/Minecraft",
        "slot_start": "2026-10-03T00:00:00.000000Z", "schedule_version": "bootstrap",
        "dispatch_run_id": "tick-1", "recorded_at": "2026-10-05T06:00:00.000000Z",
        "tier": 4, "due_at": "2026-10-05T03:00:00.000000Z", "due_reason": "default_long_tail",
        "policy_status": "cleared", "disposition": "planned", "work_order_id": None,
    }  # fmt: skip
    manifest = w.writer.land_internal(Table.EXPECTED_TARGETS, "dispatch", "tick-1", NOW, [ledger])
    assert manifest.policy_version == INTERNAL_POLICY_VERSION
    assert manifest.data_object.startswith("dispatch/expected_targets/dt=2026-10-05/")
    with pytest.raises(LandingError, match="exactly the table's columns"):
        w.writer.land_internal(
            Table.EXPECTED_TARGETS, "dispatch", "tick-2", NOW, [{**ledger, "value": 1}]
        )
