"""The warehouse stores against the local stores: the same contract, the same answers."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from metaspike.domain.identity import AliasKind, IdentityAlias, IdentityLedger
from metaspike.domain.samples import SampleAttempt, SampleStatus, SlotState
from metaspike.domain.signals import SourceErrorCode
from metaspike.domain.source_policy import DeploymentStage, FieldRule, Permission
from metaspike.domain.timeseries import TimeWindow
from metaspike.features.attention import compute_attention_features
from metaspike.resolution.recorded import load_recorded_catalogue
from metaspike.scoring.v0 import V0_INPUT_SIGNAL
from metaspike.sources.wikimedia import SOURCE_ID as WIKIMEDIA
from metaspike.storage.base import ObservationStore, StorageCorruptionError, StoragePolicyError
from metaspike.storage.local import LocalObservationStore
from metaspike.storage.records import LocalSourceRecordStore
from metaspike.storage.samples import LocalSampleStore, SampleStore
from metaspike.warehouse.landing import MANIFEST_SUFFIX, encode_rows, sample_attempt_row
from metaspike.warehouse.stores import (
    WarehouseObservationStore,
    WarehouseSampleStore,
    WarehouseSourceRecordStore,
)
from metaspike.warehouse.tables import TableRef
from tests.conftest import GAME_ID, RECORDED_END, RUN_TIME, make_id, series_of, spiking
from tests.test_sampling import KEY, SLOT, SOURCE, T0, attempt, minutes, window
from tests.warehouse_support import World, cleared_policy, real_policy, world

FIXTURES = Path(__file__).parent / "fixtures"
ATTEMPTS = TableRef("ms_raw", "sample_attempts")
LATER = T0 + timedelta(days=2)


def now() -> datetime:
    return LATER


def sample_store(w: World) -> WarehouseSampleStore:
    return WarehouseSampleStore(w.writer, w.warehouse, w.policy_for, now=w.now)


# A slot that fails and is retried, a duplicate success, a late crossing, an unavailable
# answer, a refusal, a second app, and a slot nobody sampled.
SCENARIO: tuple[SampleAttempt, ...] = (
    attempt(T0 + minutes(1), SampleStatus.FAILED, attempt_id="a01"),
    attempt(T0 + minutes(3), value=120, attempt_id="a02"),
    attempt(T0 + minutes(9), value=125, attempt_id="a03"),
    attempt(T0 + minutes(16), SampleStatus.UNAVAILABLE, attempt_id="a04"),
    attempt(
        T0 + minutes(31),
        SampleStatus.REFUSED,
        attempt_id="a05",
        error=SourceErrorCode.QUOTA_EXHAUSTED,
    ),
    attempt(T0 + minutes(32), SampleStatus.FAILED, attempt_id="a06"),
    attempt(T0 + minutes(61), value=0, attempt_id="a07", scheduled_for=T0 + minutes(45)),
    attempt(T0 + minutes(2), value=9000, app="570", attempt_id="a08", entity=None),
    attempt(T0 + minutes(76), value=130, attempt_id="a09", received_at=T0 + timedelta(hours=30)),
)


# --- samples -------------------------------------------------------------------------------


def test_the_warehouse_store_satisfies_the_sample_contract() -> None:
    interface: SampleStore = sample_store(world(now))
    assert interface.native_ids(KEY) == ()


def test_the_warehouse_and_local_sample_stores_give_the_same_answers(tmp_path: Path) -> None:
    w = world(now)
    remote = sample_store(w)
    local = LocalSampleStore(tmp_path / "samples", cleared_policy)
    for one in SCENARIO:
        local.append(one)
        remote.append(one)
    assert w.load_everything() == len(SCENARIO)

    span = window(T0 - minutes(15), 8)
    for as_of in (
        T0 + minutes(2),
        T0 + minutes(10),
        T0 + minutes(40),
        T0 + timedelta(hours=2),
        LATER,
    ):
        for app in ("730", "570", "999"):
            assert remote.attempts(KEY, app, span, as_of) == local.attempts(KEY, app, span, as_of)
            assert remote.read(KEY, app, span, as_of) == local.read(KEY, app, span, as_of)
            for slot in span.period_starts(SLOT):
                assert remote.resolve(KEY, app, slot, as_of) == local.resolve(KEY, app, slot, as_of)
    assert remote.native_ids(KEY) == local.native_ids(KEY) == ("570", "730")

    states = [slot.state for slot in remote.read(KEY, "730", span, LATER)]
    assert states == [
        SlotState.NOT_SAMPLED,
        SlotState.OBSERVED,
        SlotState.UNAVAILABLE,
        SlotState.FAILED,
        SlotState.NOT_SAMPLED,
        SlotState.OBSERVED,
        SlotState.OBSERVED,
        SlotState.NOT_SAMPLED,
    ]
    observed = remote.resolve(KEY, "730", T0, LATER)
    assert observed.value == 120 and [a.attempt_id for a in observed.duplicates] == ["a03"]
    assert remote.resolve(KEY, "730", T0 + minutes(60), LATER).value == 0  # a reported zero
    # Point in time: an attempt stored later is not known earlier.
    assert remote.resolve(KEY, "730", T0 + minutes(75), T0 + timedelta(hours=2)).state is (
        SlotState.NOT_SAMPLED
    )


def test_a_landed_attempt_is_visible_only_once_its_batch_is_loaded() -> None:
    w = world(now)
    store = sample_store(w)
    store.append(SCENARIO[1])
    assert store.resolve(KEY, "730", T0, LATER).state is SlotState.NOT_SAMPLED
    w.load_everything()
    assert store.resolve(KEY, "730", T0, LATER).state is SlotState.OBSERVED


def test_not_sampled_is_never_a_stored_row() -> None:
    w = world(now)
    store = sample_store(w)
    store.append_many(SCENARIO)
    w.load_everything()
    stored = {row["status"] for row in w.warehouse.all_rows(ATTEMPTS)}
    assert stored == {"observed", "failed", "refused", "unavailable"}
    assert SlotState.NOT_SAMPLED.value not in stored
    with pytest.raises(ValueError):
        SampleStatus("not_sampled")


def test_a_batch_loaded_twice_is_still_one_attempt() -> None:
    w = world(now)
    store = sample_store(w)
    store.append(SCENARIO[1])
    w.load_everything()
    row = {
        **sample_attempt_row(SCENARIO[1]),
        "landed_object": "x",
        "content_sha256": "0" * 64,
        "loaded_at": "2026-10-09T00:00:00.000000Z",
    }
    w.warehouse.load("a-second-load-that-should-not-have-happened", ATTEMPTS, encode_rows([row]))
    assert len(w.warehouse.all_rows(ATTEMPTS)) == 2
    assert store.attempts(KEY, "730", window(T0, 1), LATER) == (SCENARIO[1],)

    w.warehouse.load("a-contradiction", ATTEMPTS, encode_rows([{**row, "value": 121}]))
    with pytest.raises(StorageCorruptionError, match="two different contents"):
        store.attempts(KEY, "730", window(T0, 1), LATER)


def test_a_run_lands_one_batch_and_sees_its_own_unlanded_attempts() -> None:
    w = world(now)
    store = sample_store(w)
    with store.batch():
        for one in SCENARIO[:4]:
            store.append(one)
        assert w.landing.list("") == ()  # nothing is durable inside the block
        assert store.resolve(KEY, "730", T0, LATER).value == 120  # but the run sees it
    data = [name for name in w.landing.list("") if not name.endswith(MANIFEST_SUFFIX)]
    assert data == [f"{SOURCE}/sample_attempts/dt=2026-10-08/run-1/part-0000.ndjson"]
    assert w.load_everything() == 4

    with pytest.raises(RuntimeError, match="boom"), store.batch():
        store.append(SCENARIO[4])
        raise RuntimeError("boom")
    assert len(w.landing.list("")) == 2  # the failed run landed nothing
    with store.batch():
        store.append(SCENARIO[5])
    assert len(w.landing.list("")) == 4  # and the next part has its own name


def test_the_real_steam_policy_refuses_to_store_samples_in_the_warehouse() -> None:
    w = world(now, policy_for=real_policy)
    store = sample_store(w)
    with pytest.raises(StoragePolicyError, match="not cleared for private_collection"):
        store.append(SCENARIO[1])
    with pytest.raises(StoragePolicyError, match="not cleared for private_collection"):
        store.append_many(SCENARIO)
    with pytest.raises(StoragePolicyError), store.batch():
        store.append(SCENARIO[1])
    assert w.landing.list("") == ()
    assert real_policy(SOURCE).cleared_for(DeploymentStage.PRIVATE_COLLECTION) is False


# --- period observations -------------------------------------------------------------------


def window_of(days: int) -> TimeWindow:
    return TimeWindow.of_days(RECORDED_END - timedelta(days=days - 1), RECORDED_END)


def observation_store(w: World, **options: object) -> WarehouseObservationStore:
    return WarehouseObservationStore(
        w.writer,
        w.warehouse,
        w.policy_for,
        now=lambda: RUN_TIME + timedelta(days=3),
        run_id="wikimedia-run-1",
        identifier_for=lambda game, source: "en.wikipedia/Grand_Theft_Auto_V",
        **options,  # type: ignore[arg-type]
    )


def test_the_warehouse_observation_store_reads_back_what_the_local_store_does(
    tmp_path: Path,
) -> None:
    w = world(lambda: RUN_TIME + timedelta(days=3), policy_for=real_policy)
    remote = observation_store(w)
    interface: ObservationStore = remote
    assert interface.signals(GAME_ID) == ()
    local = LocalObservationStore(tmp_path / "store", real_policy)
    values = spiking()
    values[40] = None
    original = series_of(values)
    assert remote.write(original) == local.write(original) == 89
    assert w.load_everything() == 89

    restored = remote.read(GAME_ID, V0_INPUT_SIGNAL, window_of(90), as_of=RUN_TIME)
    expected = local.read(GAME_ID, V0_INPUT_SIGNAL, window_of(90), as_of=RUN_TIME)
    assert restored.values() == expected.values() == values
    assert [p.status for p in restored.points] == [p.status for p in expected.points]
    assert [p.quality for p in restored.points] == [p.quality for p in expected.points]
    assert [p.observed_at for p in restored.points] == [p.observed_at for p in expected.points]
    assert compute_attention_features(restored) == compute_attention_features(expected)
    assert remote.signals(GAME_ID) == local.signals(GAME_ID) == (V0_INPUT_SIGNAL,)
    assert restored.points[0].reference == "en.wikipedia/Grand_Theft_Auto_V"

    assert remote.write(original) == 0  # the same data again keeps nothing new
    assert w.load_everything() == 0
    stored = w.warehouse.all_rows(TableRef("ms_raw", "period_observations"))
    assert {row["status"] for row in stored} == {"observed"}  # a missing period is never a row


def test_a_revision_is_kept_beside_the_original_in_the_warehouse() -> None:
    w = world(lambda: RUN_TIME + timedelta(days=3), policy_for=real_policy)
    store = observation_store(w)
    store.write(series_of([100.0, 200.0]))
    w.load_everything()
    later = RUN_TIME + timedelta(days=1)
    revised = series_of([100.0, 250.0])
    revised = revised.model_copy(
        update={
            "points": tuple(p.model_copy(update={"observed_at": later}) for p in revised.points)
        }
    )
    assert store.write(revised) == 1
    w.load_everything("load-2")

    def read(as_of: datetime) -> list[float | None]:
        return store.read(GAME_ID, V0_INPUT_SIGNAL, window_of(2), as_of=as_of).values()

    assert read(RUN_TIME) == [100.0, 200.0]
    assert read(later) == [100.0, 250.0]
    assert read(RUN_TIME - timedelta(hours=1)) == [None, None]


def test_history_written_under_a_retired_id_is_read_under_its_successor() -> None:
    w = world(lambda: RUN_TIME + timedelta(days=3), policy_for=real_policy)
    observation_store(w).write(series_of([100.0, 200.0]))
    w.load_everything()
    successor = make_id(77)
    ledger = IdentityLedger().with_aliases(
        IdentityAlias(
            alias=GAME_ID,
            canonical_game_id=successor,
            kind=AliasKind.MERGED,
            valid_from=RUN_TIME - timedelta(days=30),
            reason="test merge",
        )
    )
    merged = observation_store(w, identity=ledger)
    series = merged.read(GAME_ID, V0_INPUT_SIGNAL, window_of(2), as_of=RUN_TIME)
    assert series.canonical_game_id == successor and series.values() == [100.0, 200.0]


def test_observation_storage_is_refused_when_the_policy_does_not_allow_it() -> None:
    wikimedia = real_policy(WIKIMEDIA)
    refusing = (
        wikimedia.model_copy(update={"historical_storage_allowed": Permission.UNKNOWN}),
        wikimedia.model_copy(
            update={
                "field_rules": (
                    FieldRule(
                        field="pageviews",
                        storage_allowed=Permission.PROHIBITED,
                        display_allowed=Permission.PROHIBITED,
                    ),
                )
            }
        ),
    )
    for policy in refusing:
        w = world(now, policy_for=lambda _, policy=policy: policy)  # type: ignore[misc]
        with pytest.raises(StoragePolicyError, match="does not permit storing"):
            observation_store(w).write(series_of(spiking()))
        assert w.landing.list("") == ()


# --- source records ------------------------------------------------------------------------


def test_the_warehouse_record_store_keeps_versions_like_the_local_one(tmp_path: Path) -> None:
    recorded = [
        r
        for r in load_recorded_catalogue(FIXTURES / "bootstrap_catalogue").values()
        if r.key.source == "igdb"
    ]
    assert recorded
    w = world(now, policy_for=real_policy)
    remote = WarehouseSourceRecordStore(
        w.writer, w.warehouse, w.policy_for, now=w.now, run_id="catalogue-run-1"
    )
    local = LocalSourceRecordStore(tmp_path, real_policy, DeploymentStage.PRIVATE_COLLECTION)
    assert remote.write(recorded) == local.write(recorded) == len(recorded)
    w.load_everything()
    assert remote.write(recorded) == local.write(recorded) == 0

    first = recorded[0]
    seen_again = first.fetched_at + timedelta(days=1)
    changed = first.model_copy(
        update={"title": first.title + " (renamed)", "fetched_at": seen_again}
    )
    refetched = recorded[1].model_copy(update={"fetched_at": seen_again})
    assert remote.write([changed, refetched]) == local.write([changed, refetched]) == 1
    w.load_everything("load-2")

    assert set(remote.all("igdb")) == set(local.all("igdb"))
    assert remote.get(first.key) == local.get(first.key) == changed
    assert remote.get(first.key, as_of=first.fetched_at) == local.get(first.key, first.fetched_at)
    assert remote.get(first.key, as_of=first.fetched_at) == first
    assert remote.history(first.key) == local.history(first.key) == (first, changed)
    assert remote.all("steam_catalogue") == ()
    stored = w.warehouse.all_rows(TableRef("ms_raw", "source_records"))
    assert all(row["record"]["key"]["source"] == "igdb" for row in stored)


def test_steam_catalogue_records_are_refused_until_the_source_is_cleared() -> None:
    steam = [
        r
        for r in load_recorded_catalogue(FIXTURES / "bootstrap_catalogue").values()
        if r.key.source == "steam_catalogue"
    ]
    w = world(now, policy_for=real_policy)
    store = WarehouseSourceRecordStore(w.writer, w.warehouse, w.policy_for, now=w.now, run_id="r1")
    with pytest.raises(StoragePolicyError, match="not cleared for private_collection"):
        store.write(steam)
    assert w.landing.list("") == ()
