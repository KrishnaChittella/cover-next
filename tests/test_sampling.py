"""Sample-now sources: the contract, the attempt model, the store and one sampling run.

Everything here is offline. Readings are synthetic unless a test says it uses the one
recorded Steam response.
"""

import inspect
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from metaspike.catalogue.eligibility import SamplingTarget
from metaspike.domain import baseline_rules
from metaspike.domain.identity import MappingHistory, SourceRecordKey
from metaspike.domain.metrics import CONCURRENT_PLAYERS, default_metrics, sampled_metrics
from metaspike.domain.samples import (
    LogicalSlotKey,
    SampleAttempt,
    SampleFlag,
    SampleReading,
    SampleStatus,
    SlotState,
    resolve_slot,
)
from metaspike.domain.signals import SignalKey, SignalSpec, SourceErrorCode
from metaspike.domain.source_policy import DeploymentStage, Permission, SourcePurpose
from metaspike.domain.timeseries import Granularity, TimeWindow
from metaspike.pipeline.history import (
    NO_BINDING_REASON,
    mapping_as_known,
    read_entity_history,
)
from metaspike.pipeline.run import run_pipeline
from metaspike.pipeline.sampling import (
    MAX_TRANSMISSIONS_PER_SLOT,
    TargetAction,
    run_sampling_slot,
    target_health,
)
from metaspike.sources.base import SamplingAdapter, SourceAdapter, SourceMode
from metaspike.sources.registry import (
    SourcePolicyError,
    SourceRegistrationError,
    SourceRegistry,
    default_registry,
)
from metaspike.sources.replay import replay_client
from metaspike.sources.steam_players import STEAM_CURRENT_PLAYERS_POLICY
from metaspike.sources.wikimedia import WIKIMEDIA_PAGEVIEWS_POLICY, WikimediaPageviewsAdapter
from metaspike.storage.base import StorageCorruptionError, StoragePolicyError
from metaspike.storage.samples import LocalSampleStore
from tests.conftest import RECORDED_END, RECORDINGS, make_id
from tests.conftest import catalogue as _catalogue  # noqa: F401

SOURCE = "steam_current_players"
BINDING_SOURCE = "steam_catalogue"
SLOT = Granularity.FIFTEEN_MINUTES
KEY = SignalKey(source=SOURCE, metric="concurrent_players", granularity=SLOT)
T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)  # the start of a slot
ENTITY_A, ENTITY_B = make_id(1), make_id(2)
APP = "730"


def minutes(count: float) -> timedelta:
    return timedelta(minutes=count)


def attempt(
    at: datetime,
    status: SampleStatus = SampleStatus.OBSERVED,
    value: int | None = 100,
    *,
    app: str = APP,
    attempt_id: str = "a1",
    received_at: datetime | None = None,
    scheduled_for: datetime | None = None,
    entity: str | None = ENTITY_A,
    error: SourceErrorCode | None = None,
    flags: tuple[SampleFlag, ...] = (),
) -> SampleAttempt:
    observed = status is SampleStatus.OBSERVED
    if not observed and error is None and status is not SampleStatus.UNAVAILABLE:
        error = SourceErrorCode.TIMEOUT
    marks = list(flags)
    if observed and value == 0:
        marks.append(SampleFlag.ZERO_REPORTED)
    if entity is None:
        marks.append(SampleFlag.BINDING_UNKNOWN)
    return SampleAttempt(
        source=SOURCE,
        metric="concurrent_players",
        source_native_id=app,
        value=value if observed else None,
        granularity=SLOT,
        scheduled_for=SLOT.floor(at) if scheduled_for is None else scheduled_for,
        slot_start=SLOT.floor(at),
        observed_at=at,
        received_at=at if received_at is None else received_at,
        canonical_game_id=entity,
        mapping_version=None if entity is None else 1,
        binding_ref=None if entity is None else "decision:1",
        collection_run_id="run-1",
        attempt_id=attempt_id,
        status=status,
        quality_flags=tuple(marks),
        error_code=error,
    )


def store_in(tmp_path: Path) -> LocalSampleStore:
    return LocalSampleStore(tmp_path / "samples", lambda _: STEAM_CURRENT_PLAYERS_POLICY)


def window(start: datetime, slots: int) -> TimeWindow:
    return TimeWindow(start=start, end=start + (slots - 1) * SLOT.step)


class Ticker:
    """A clock the test moves by hand."""

    def __init__(self, at: datetime = T0) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


class FakeSampler:
    """A sample-now source that answers from a script. Each answer takes `latency`."""

    source = SOURCE
    mode = SourceMode.SAMPLE_NOW
    signal = SignalSpec(metric="concurrent_players", granularity=SLOT)
    description = "Synthetic sampler."

    def __init__(
        self, clock: Ticker, *script: SampleStatus | int, latency: timedelta = timedelta(seconds=2)
    ) -> None:
        self._clock = clock
        self._script: Iterator[SampleStatus | int] = iter(script)
        self._latency = latency
        self.asked: list[str] = []
        self.halt_on: SourceErrorCode | None = None

    def sample(self, source_native_id: str) -> SampleReading:
        self.asked.append(source_native_id)
        self._clock.at += self._latency
        answer = next(self._script)
        if isinstance(answer, int) and not isinstance(answer, bool):
            return SampleReading(
                status=SampleStatus.OBSERVED, value=answer, observed_at=self._clock.at
            )
        if answer is SampleStatus.UNAVAILABLE:
            return SampleReading(status=answer, observed_at=self._clock.at)
        code = self.halt_on or SourceErrorCode.TIMEOUT
        return SampleReading(
            status=answer,
            observed_at=self._clock.at,
            error_code=code,
            halt_run=self.halt_on is not None,
        )


def target(app: str = APP, entity: str | None = ENTITY_A) -> SamplingTarget:
    if entity is None:
        return SamplingTarget(source_native_id=app)
    return SamplingTarget(
        source_native_id=app, canonical_game_id=entity, mapping_version=1, binding_ref="decision:1"
    )


def ids() -> Any:
    counter = iter(range(1, 10_000))
    return lambda: f"{next(counter):032x}"


# --- source modes and the sampling contract ----------------------------------------------


def test_each_source_declares_how_it_answers() -> None:
    wikimedia = WikimediaPageviewsAdapter(httpx.Client())
    assert wikimedia.mode is SourceMode.FETCH_HISTORY
    assert FakeSampler(Ticker()).mode is SourceMode.SAMPLE_NOW
    assert {mode.value for mode in SourceMode} == {"fetch_history", "sample_now"}


def test_a_sampling_adapter_has_no_way_to_be_asked_for_the_past() -> None:
    assert list(inspect.signature(SamplingAdapter.sample).parameters) == [
        "self",
        "source_native_id",
    ]
    assert not hasattr(SamplingAdapter, "fetch")
    assert "window" in inspect.signature(SourceAdapter.fetch).parameters  # the contrast
    sampler = FakeSampler(Ticker(), 5)
    with pytest.raises(TypeError):
        sampler.sample(APP, window(T0, 4))  # type: ignore[call-arg]


def test_the_registry_keeps_the_two_modes_apart() -> None:
    wikimedia = WikimediaPageviewsAdapter(httpx.Client())
    sampler = FakeSampler(Ticker())
    registry = SourceRegistry(metrics=sampled_metrics())
    with pytest.raises(SourceRegistrationError, match="cannot be registered as a history source"):
        registry.register(sampler, STEAM_CURRENT_PLAYERS_POLICY)  # type: ignore[arg-type]
    with pytest.raises(SourceRegistrationError, match="only a sample-now source"):
        registry.register_sampler(wikimedia, WIKIMEDIA_PAGEVIEWS_POLICY)  # type: ignore[arg-type]
    registry.register_sampler(sampler, STEAM_CURRENT_PLAYERS_POLICY)
    assert [entry.policy.source for entry in registry.samplers()] == [SOURCE]
    assert registry.entries() == ()  # a sampler is never a history source
    assert registry.policy(SOURCE) is STEAM_CURRENT_PLAYERS_POLICY
    with pytest.raises(SourcePolicyError, match="already registered"):
        registry.register_sampler(sampler, STEAM_CURRENT_PLAYERS_POLICY)


def test_a_sampler_that_could_also_fetch_history_is_refused() -> None:
    class Both(FakeSampler):
        def fetch(self) -> None: ...

    with pytest.raises(SourceRegistrationError, match="cannot also fetch history"):
        SourceRegistry(metrics=sampled_metrics()).register_sampler(
            Both(Ticker()), STEAM_CURRENT_PLAYERS_POLICY
        )


def test_sampled_metrics_are_outside_the_published_vocabulary() -> None:
    assert CONCURRENT_PLAYERS.metric_id not in default_metrics()
    assert CONCURRENT_PLAYERS.metric_id in sampled_metrics()
    assert "never a daily total" in CONCURRENT_PLAYERS.description
    with pytest.raises(SourceRegistrationError, match="not in the metric registry"):
        SourceRegistry().register_sampler(FakeSampler(Ticker()), STEAM_CURRENT_PLAYERS_POLICY)


# --- the stage-aware policy gate ---------------------------------------------------------


def test_the_steam_player_policy_is_recorded_exactly() -> None:
    policy = STEAM_CURRENT_PLAYERS_POLICY
    assert policy.source == SOURCE
    assert policy.derived_metrics_allowed is Permission.UNKNOWN
    assert policy.commercial_use_allowed is Permission.UNKNOWN
    assert policy.raw_storage_allowed is Permission.RESTRICTED
    assert policy.historical_storage_allowed is Permission.RESTRICTED
    assert policy.redistribution_allowed is Permission.RESTRICTED
    assert {c.stage.value: c.cleared for c in policy.stage_clearance} == {
        "private_development": True,
        "private_collection": False,
        "public_launch": False,
        "commercial_production": False,
    }
    operations = policy.operations
    assert operations.budget_group == "steam_web_api"
    assert (operations.rate_limit.max_requests, operations.rate_limit.per_seconds) == (1, 1)
    assert operations.rate_limit.max_concurrency == 1
    assert operations.quota is not None and operations.quota.limit == 100_000
    assert operations.cache_max_age_seconds == 0
    text = policy.notes + policy.display.note
    for requirement in ("100,000", "confidential", "disclosed", "country", "as is", "logo"):
        assert requirement in text
    assert "affiliated with Valve" in policy.display.note
    # The Subscriber Agreement is not recorded as banning licensed Web API access.
    assert "not recorded here as a restriction on licensed Web API access" in policy.notes
    assert all(reference.startswith("https://") for reference in policy.references)


def test_an_unanswered_derived_metrics_permission_is_allowed_privately_and_nowhere_else() -> None:
    policy = STEAM_CURRENT_PLAYERS_POLICY
    demand = SourcePurpose.DEMAND_SIGNALS
    assert policy.registration_refusal(demand, DeploymentStage.PRIVATE_DEVELOPMENT) is None
    collection = policy.registration_refusal(demand, DeploymentStage.PRIVATE_COLLECTION)
    assert collection is not None and collection.startswith("not cleared for private_collection")
    for stage in (DeploymentStage.PUBLIC_LAUNCH, DeploymentStage.COMMERCIAL_PRODUCTION):
        refusal = policy.registration_refusal(demand, stage)
        assert refusal is not None and "derived metrics are not explicitly allowed" in refusal
        with pytest.raises(SourcePolicyError, match="derived metrics are not explicitly"):
            SourceRegistry(metrics=sampled_metrics(), stage=stage).register_sampler(
                FakeSampler(Ticker()), policy
            )


def test_marking_a_stage_cleared_does_not_turn_unknown_into_allowed() -> None:
    cleared = STEAM_CURRENT_PLAYERS_POLICY.model_copy(
        update={
            "stage_clearance": tuple(
                c.model_copy(
                    update={"cleared": c.stage is not DeploymentStage.COMMERCIAL_PRODUCTION}
                )
                for c in STEAM_CURRENT_PLAYERS_POLICY.stage_clearance
            )
        }
    )
    refusal = cleared.registration_refusal(
        SourcePurpose.DEMAND_SIGNALS, DeploymentStage.PUBLIC_LAUNCH
    )
    assert refusal is not None and "unknown" in refusal
    assert not cleared.derived_output_permitted
    assert cleared.publication_refusal() is not None


def test_prohibited_or_unstorable_sources_are_refused_even_privately() -> None:
    development = DeploymentStage.PRIVATE_DEVELOPMENT
    prohibited = STEAM_CURRENT_PLAYERS_POLICY.model_copy(
        update={"derived_metrics_allowed": Permission.PROHIBITED}
    )
    assert "prohibited" in str(
        prohibited.registration_refusal(SourcePurpose.DEMAND_SIGNALS, development)
    )
    unstorable = STEAM_CURRENT_PLAYERS_POLICY.model_copy(
        update={"historical_storage_allowed": Permission.UNKNOWN}
    )
    assert "storing the source's history" in str(
        unstorable.registration_refusal(SourcePurpose.DEMAND_SIGNALS, development)
    )


def test_nothing_derived_from_an_unanswered_source_can_be_published(tmp_path: Path) -> None:
    """A source may be studied privately and still be unable to reach an artifact."""
    unanswered = WIKIMEDIA_PAGEVIEWS_POLICY.model_copy(
        update={"derived_metrics_allowed": Permission.UNKNOWN}
    )
    from metaspike.catalogue.seed import load_seed_catalogue
    from metaspike.pipeline.collection import window_ending

    with replay_client(RECORDINGS) as client:
        registry = SourceRegistry()
        registry.register(WikimediaPageviewsAdapter(client), unanswered)  # private: accepted
        assert set(registry.publication_refusals()) == {"wikimedia_pageviews"}
        with pytest.raises(SourcePolicyError, match="may enter a published artifact"):
            run_pipeline(
                load_seed_catalogue(),
                registry,
                window_ending(RECORDED_END),
                tmp_path / "artifacts",
                clock=lambda: T0,
            )
        assert not (tmp_path / "artifacts").exists()  # nothing was written
        assert default_registry(client).publication_refusals() == {}

    sampling = SourceRegistry(metrics=sampled_metrics())
    sampling.register_sampler(FakeSampler(Ticker()), STEAM_CURRENT_PLAYERS_POLICY)
    assert set(sampling.publication_refusals()) == {SOURCE}


def test_the_sample_store_enforces_the_stage(tmp_path: Path) -> None:
    public = LocalSampleStore(
        tmp_path, lambda _: STEAM_CURRENT_PLAYERS_POLICY, DeploymentStage.PUBLIC_LAUNCH
    )
    with pytest.raises(StoragePolicyError, match="derived metrics are not explicitly"):
        public.append(attempt(T0))
    assert not any(tmp_path.iterdir())


# --- the attempt model -------------------------------------------------------------------


def test_a_value_exists_exactly_when_something_was_observed() -> None:
    assert attempt(T0, value=0).value == 0
    for status in (SampleStatus.FAILED, SampleStatus.REFUSED, SampleStatus.UNAVAILABLE):
        assert attempt(T0, status).value is None
    valid = attempt(T0).model_dump()
    for broken in (
        {"value": None},  # observed without a value
        {"status": "failed", "error_code": "timeout"},  # a failure carrying a value
        {"status": "failed", "value": None},  # a failure with no reason
        {"value": 0},  # a zero that is not flagged
        {"quality_flags": ("zero_reported",)},  # a flag for a zero that was not reported
        {"slot_start": T0 - SLOT.step},  # moved backward out of its real slot
        {"scheduled_for": T0 + minutes(1)},  # not a slot
        {"received_at": T0 - minutes(1)},  # stored before it was observed
        {"mapping_version": None},  # half a binding stamp
        {"quality_flags": ("binding_unknown",)},  # bound, yet flagged unknown
        {"value": -1},
    ):
        with pytest.raises(ValidationError):
            SampleAttempt.model_validate(valid | broken)


def test_the_four_times_are_kept_apart() -> None:
    scheduled = T0
    observed = T0 + minutes(16)  # the request ran long: the answer arrived in the next slot
    stored = observed + timedelta(seconds=1)
    crossed = attempt(observed, scheduled_for=scheduled, received_at=stored)
    assert crossed.scheduled_for == scheduled
    assert crossed.slot_start == T0 + SLOT.step  # where it was really observed
    assert crossed.observed_at == observed and crossed.received_at == stored
    assert crossed.crossed_slot and crossed.delay() == minutes(16)
    assert crossed.slot_key == LogicalSlotKey(
        source=SOURCE, metric="concurrent_players", source_native_id=APP, slot_start=T0 + SLOT.step
    )


# --- logical slot versus attempt ---------------------------------------------------------


def slot_key(start: datetime = T0, app: str = APP) -> LogicalSlotKey:
    return LogicalSlotKey(
        source=SOURCE, metric="concurrent_players", source_native_id=app, slot_start=start
    )


def test_a_failed_attempt_and_the_retry_that_succeeded_are_both_kept() -> None:
    failed = attempt(T0 + minutes(1), SampleStatus.FAILED, attempt_id="first")
    retried = attempt(T0 + minutes(3), value=250, attempt_id="second")
    assert failed.slot_key == retried.slot_key and failed.attempt_id != retried.attempt_id
    slot = resolve_slot(slot_key(), SLOT, (failed, retried))
    assert slot.state is SlotState.OBSERVED and slot.value == 250
    assert slot.selected == retried
    assert [a.attempt_id for a in slot.attempts] == ["first", "second"]  # nothing discarded
    assert slot.duplicates == ()


def test_the_earliest_success_speaks_for_the_slot_whatever_order_it_was_stored_in() -> None:
    early = attempt(T0 + minutes(2), value=100, attempt_id="b")
    later = attempt(T0 + minutes(9), value=999, attempt_id="a")
    latest = attempt(T0 + minutes(12), value=5, attempt_id="c")
    failed = attempt(T0 + minutes(1), SampleStatus.FAILED, attempt_id="z")
    orders = [
        (early, later, latest, failed),
        (latest, failed, later, early),
        (later, early, failed, latest),
    ]
    resolutions = [resolve_slot(slot_key(), SLOT, order) for order in orders]
    assert all(r == resolutions[0] for r in resolutions)
    chosen = resolutions[0]
    assert chosen.selected == early and chosen.value == 100
    assert [d.attempt_id for d in chosen.duplicates] == ["a", "c"]  # recorded, not selected
    # Two successes at the same instant: the attempt id breaks the tie.
    twin_a, twin_b = attempt(T0, value=1, attempt_id="a"), attempt(T0, value=2, attempt_id="b")
    assert resolve_slot(slot_key(), SLOT, (twin_b, twin_a)).value == 1


@pytest.mark.parametrize(
    ("statuses", "state"),
    [
        ((), SlotState.NOT_SAMPLED),
        ((SampleStatus.REFUSED,), SlotState.REFUSED),
        ((SampleStatus.REFUSED, SampleStatus.FAILED), SlotState.FAILED),
        (
            (SampleStatus.FAILED, SampleStatus.UNAVAILABLE, SampleStatus.REFUSED),
            SlotState.UNAVAILABLE,
        ),
    ],
)
def test_a_slot_without_a_success_resolves_to_its_most_informative_outcome(
    statuses: tuple[SampleStatus, ...], state: SlotState
) -> None:
    attempts = tuple(
        attempt(T0 + minutes(index), status, attempt_id=str(index))
        for index, status in enumerate(statuses)
    )
    slot = resolve_slot(slot_key(), SLOT, attempts)
    assert slot.state is state and slot.value is None and slot.selected is None


def test_an_attempt_from_another_slot_is_rejected() -> None:
    with pytest.raises(ValueError, match="does not belong"):
        resolve_slot(slot_key(), SLOT, (attempt(T0 + SLOT.step),))


# --- the local store ---------------------------------------------------------------------


def test_missing_is_never_zero(tmp_path: Path) -> None:
    store = store_in(tmp_path)
    store.append(attempt(T0, value=0, attempt_id="zero"))
    store.append(attempt(T0 + SLOT.step, SampleStatus.FAILED, attempt_id="failed"))
    store.append(attempt(T0 + 3 * SLOT.step, value=40, attempt_id="forty"))
    slots = store.read(KEY, APP, window(T0, 4), T0 + minutes(120))
    assert [slot.state for slot in slots] == [
        SlotState.OBSERVED,
        SlotState.FAILED,
        SlotState.NOT_SAMPLED,  # reconstructed: nothing was ever stored for it
        SlotState.OBSERVED,
    ]
    assert [slot.value for slot in slots] == [0, None, None, 40]
    assert slots[0].selected is not None
    assert SampleFlag.ZERO_REPORTED in slots[0].selected.quality_flags
    assert slots[2].attempts == ()


def test_files_are_one_per_target_per_utc_month_and_reads_are_bounded(tmp_path: Path) -> None:
    store = store_in(tmp_path)
    september = datetime(2026, 9, 30, 23, 45, tzinfo=UTC)
    october = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    store.append(attempt(september, attempt_id="s"))
    store.append(attempt(october, attempt_id="o"))
    store.append(attempt(october, app="570", attempt_id="d"))
    files = sorted(str(p.relative_to(tmp_path)) for p in (tmp_path / "samples").rglob("*.jsonl"))
    assert files == [
        f"samples/{SOURCE}/concurrent_players/570/2026-10.jsonl",
        f"samples/{SOURCE}/concurrent_players/730/2026-09.jsonl",
        f"samples/{SOURCE}/concurrent_players/730/2026-10.jsonl",
    ]
    assert store.native_ids(KEY) == ("570", "730")
    across = store.read(KEY, APP, window(september, 2), october + minutes(30))
    assert [slot.state for slot in across] == [SlotState.OBSERVED, SlotState.OBSERVED]
    # A window inside October never opens September's file.
    (tmp_path / "samples" / SOURCE / "concurrent_players" / APP / "2026-09.jsonl").write_text("{")
    assert len(store.read(KEY, APP, window(october, 4), october + minutes(90))) == 4
    with pytest.raises(ValueError, match="unsafe path segment"):
        store.append(attempt(T0, app="../escape"))


def test_a_read_as_of_a_moment_ignores_what_was_stored_later(tmp_path: Path) -> None:
    store = store_in(tmp_path)
    store.append(attempt(T0 + minutes(1), SampleStatus.FAILED, attempt_id="failed"))
    # Observed in the slot, but only durably stored twenty minutes later.
    store.append(
        attempt(T0 + minutes(4), value=77, attempt_id="slow", received_at=T0 + minutes(24))
    )
    before = store.resolve(KEY, APP, T0, T0 + minutes(10))
    after = store.resolve(KEY, APP, T0, T0 + minutes(30))
    assert before.state is SlotState.FAILED and before.value is None
    assert after.state is SlotState.OBSERVED and after.value == 77
    assert store.resolve(KEY, APP, T0, T0).state is SlotState.NOT_SAMPLED


def test_a_torn_final_line_is_skipped_and_the_next_append_starts_clean(tmp_path: Path) -> None:
    store = store_in(tmp_path)
    store.append(attempt(T0, value=10, attempt_id="whole"))
    path = tmp_path / "samples" / SOURCE / "concurrent_players" / APP / "2026-10.jsonl"
    whole = path.read_text()
    torn = attempt(T0 + SLOT.step, value=20, attempt_id="torn").model_dump_json()[:60]
    path.write_text(whole + torn)  # the process died mid-write

    restarted = store_in(tmp_path)
    assert [s.value for s in restarted.read(KEY, APP, window(T0, 2), T0 + minutes(60))] == [
        10,
        None,
    ]
    assert restarted.torn_lines_skipped == 1
    restarted.append(attempt(T0 + SLOT.step, value=30, attempt_id="after"))
    lines = path.read_text().splitlines()
    assert lines[0] == whole.strip() and lines[1] == torn  # nothing rewritten
    assert json.loads(lines[2])["attempt_id"] == "after"
    assert [
        s.value for s in store_in(tmp_path).read(KEY, APP, window(T0, 2), T0 + minutes(60))
    ] == [
        10,
        30,
    ]


def test_a_whole_line_that_is_not_a_valid_attempt_is_corruption_not_a_tear(tmp_path: Path) -> None:
    store = store_in(tmp_path)
    store.append(attempt(T0))
    path = next((tmp_path / "samples").rglob("*.jsonl"))
    path.write_text(path.read_text() + '{"value": 5}\n')
    with pytest.raises(StorageCorruptionError, match="line 2"):
        store.read(KEY, APP, window(T0, 1), T0 + minutes(5))


def test_the_store_never_rewrites_what_it_holds(tmp_path: Path) -> None:
    store = store_in(tmp_path)
    store.append(attempt(T0, value=1, attempt_id="first"))
    path = next((tmp_path / "samples").rglob("*.jsonl"))
    before = path.read_bytes()
    store.append(attempt(T0 + minutes(5), value=2, attempt_id="second"))
    store.append(attempt(T0 + minutes(6), SampleStatus.FAILED, attempt_id="third"))
    assert path.read_bytes().startswith(before)
    assert len(path.read_text().splitlines()) == 3


# --- one sampling run --------------------------------------------------------------------


def test_a_run_stores_every_outcome_and_returns_no_values(tmp_path: Path) -> None:
    clock, store = Ticker(), store_in(tmp_path)
    sampler = FakeSampler(clock, 120, 0, SampleStatus.FAILED, SampleStatus.UNAVAILABLE)
    targets = tuple(target(app) for app in ("570", "730", "252490", "1245620"))
    report = run_sampling_slot(targets, sampler, store, now=clock, new_id=ids())

    assert sampler.asked == ["570", "730", "252490", "1245620"]  # stable order
    assert report.requests_sent == 4 and report.halted_by is None
    assert [o.status for o in report.outcomes] == [
        SampleStatus.OBSERVED,
        SampleStatus.OBSERVED,
        SampleStatus.FAILED,
        SampleStatus.UNAVAILABLE,
    ]
    assert "value" not in report.model_dump_json()  # the report is not a data path
    assert "120" not in report.model_dump_json().replace(report.collection_run_id, "")

    as_of = clock.at + minutes(1)
    assert store.resolve(KEY, "570", T0, as_of).value == 120
    zero = store.resolve(KEY, "730", T0, as_of)
    assert zero.state is SlotState.OBSERVED and zero.value == 0
    assert zero.selected is not None and SampleFlag.ZERO_REPORTED in zero.selected.quality_flags
    assert store.resolve(KEY, "252490", T0, as_of).state is SlotState.FAILED
    assert store.resolve(KEY, "1245620", T0, as_of).state is SlotState.UNAVAILABLE
    stamped = store.resolve(KEY, "570", T0, as_of).selected
    assert stamped is not None
    assert (stamped.canonical_game_id, stamped.mapping_version, stamped.binding_ref) == (
        ENTITY_A,
        1,
        "decision:1",
    )
    assert stamped.collection_run_id == report.collection_run_id
    assert stamped.scheduled_for == T0 and stamped.received_at >= stamped.observed_at


def test_rerunning_a_slot_sends_nothing_for_what_is_already_observed(tmp_path: Path) -> None:
    clock, store = Ticker(), store_in(tmp_path)
    targets = (target("570"), target("730"))
    first = FakeSampler(clock, 100, SampleStatus.FAILED)
    run_sampling_slot(targets, first, store, now=clock, new_id=ids())

    again = FakeSampler(clock, 200)  # a fresh process: nothing is remembered but the store
    second = run_sampling_slot(targets, again, store_in(tmp_path), now=clock, new_id=ids())
    assert again.asked == ["730"]  # only the failed slot is retried
    assert [o.action for o in second.outcomes] == [
        TargetAction.ALREADY_OBSERVED,
        TargetAction.SAMPLED,
    ]
    assert second.requests_sent == 1

    third = FakeSampler(clock)
    final = run_sampling_slot(targets, third, store, now=clock, new_id=ids())
    assert third.asked == [] and final.requests_sent == 0
    slot = store.resolve(KEY, "730", T0, clock.at + minutes(1))
    assert [a.status for a in slot.attempts] == [SampleStatus.FAILED, SampleStatus.OBSERVED]
    assert slot.value == 200 and len({a.attempt_id for a in slot.attempts}) == 2


def test_retries_within_a_slot_are_bounded(tmp_path: Path) -> None:
    clock, store = Ticker(), store_in(tmp_path)
    for _ in range(MAX_TRANSMISSIONS_PER_SLOT):
        sampler = FakeSampler(clock, SampleStatus.FAILED)
        run_sampling_slot((target(),), sampler, store, now=clock, new_id=ids())
        assert sampler.asked == [APP]
    spent = FakeSampler(clock)
    report = run_sampling_slot((target(),), spent, store, now=clock, new_id=ids())
    assert spent.asked == [] and report.outcomes[0].action is TargetAction.RETRY_BUDGET_SPENT
    assert len(store.resolve(KEY, APP, T0, clock.at + minutes(1)).attempts) == 3
    # An app the source has already said it has nothing for is not asked again either.
    unavailable = FakeSampler(clock, SampleStatus.UNAVAILABLE)
    run_sampling_slot((target("999"),), unavailable, store, now=clock, new_id=ids())
    quiet = FakeSampler(clock)
    outcome = run_sampling_slot((target("999"),), quiet, store, now=clock, new_id=ids())
    assert quiet.asked == [] and outcome.outcomes[0].action is TargetAction.SOURCE_UNAVAILABLE
    # The next slot starts with a fresh budget.
    clock.at = T0 + SLOT.step
    fresh = FakeSampler(clock, 9)
    run_sampling_slot((target(),), fresh, store, now=clock, new_id=ids())
    assert fresh.asked == [APP]


def test_a_refused_key_or_rate_stops_the_run_and_the_rest_are_recorded_as_refused(
    tmp_path: Path,
) -> None:
    clock, store = Ticker(), store_in(tmp_path)
    sampler = FakeSampler(clock, 50, SampleStatus.FAILED)
    sampler.halt_on = SourceErrorCode.UNAUTHORIZED
    targets = tuple(target(app) for app in ("570", "730", "252490", "1245620"))
    report = run_sampling_slot(targets, sampler, store, now=clock, new_id=ids())
    assert sampler.asked == ["570", "730"]  # nothing sent after the refusal
    assert report.requests_sent == 2 and report.halted_by is SourceErrorCode.UNAUTHORIZED
    assert [o.action for o in report.outcomes] == [
        TargetAction.SAMPLED,
        TargetAction.SAMPLED,
        TargetAction.HALTED,
        TargetAction.HALTED,
    ]
    halted = store.resolve(KEY, "1245620", T0, clock.at + minutes(1))
    assert halted.state is SlotState.REFUSED and halted.value is None
    assert halted.attempts[0].error_code is SourceErrorCode.UNAUTHORIZED
    # A refusal sent nothing, so it does not use up the slot's retry budget.
    retry = FakeSampler(clock, 7, 8, 9)
    run_sampling_slot(targets, retry, store, now=clock, new_id=ids())
    assert retry.asked == ["730", "252490", "1245620"]


def test_late_inside_the_intended_slot_is_flagged_and_stays_in_that_slot(tmp_path: Path) -> None:
    clock, store = Ticker(T0 + minutes(9)), store_in(tmp_path)  # the run started 9 minutes late
    sampler = FakeSampler(clock, 300)
    report = run_sampling_slot(
        (target(),), sampler, store, now=clock, scheduled_for=T0, new_id=ids()
    )
    (outcome,) = report.outcomes
    assert outcome.late and not outcome.crossed_slot and outcome.slot_start == T0
    slot = store.resolve(KEY, APP, T0, clock.at + minutes(1))
    assert slot.selected is not None and SampleFlag.LATE in slot.selected.quality_flags
    assert slot.selected.scheduled_for == T0

    on_time = FakeSampler(Ticker(T0 + SLOT.step + minutes(4)), 1)
    punctual = run_sampling_slot((target(),), on_time, store, now=on_time._clock, new_id=ids())
    assert not punctual.outcomes[0].late  # within tolerance


def test_an_observation_that_crosses_into_the_next_slot_belongs_to_the_next_slot(
    tmp_path: Path,
) -> None:
    clock, store = Ticker(T0 + minutes(14.5)), store_in(tmp_path)
    slow = FakeSampler(clock, 800, latency=minutes(1))  # answers at 12:15:30
    report = run_sampling_slot((target(),), slow, store, now=clock, scheduled_for=T0, new_id=ids())
    (outcome,) = report.outcomes
    assert outcome.crossed_slot and outcome.late and outcome.slot_start == T0 + SLOT.step
    as_of = clock.at + minutes(1)
    intended, actual = store.read(KEY, APP, window(T0, 2), as_of)
    assert intended.state is SlotState.NOT_SAMPLED and intended.value is None
    assert actual.state is SlotState.OBSERVED and actual.value == 800
    assert actual.selected is not None and actual.selected.scheduled_for == T0  # intent kept


def test_a_sample_with_no_bound_entity_is_kept_under_its_own_id(tmp_path: Path) -> None:
    clock, store = Ticker(), store_in(tmp_path)
    run_sampling_slot(
        (target("424242", entity=None),), FakeSampler(clock, 61), store, now=clock, new_id=ids()
    )
    slot = store.resolve(KEY, "424242", T0, clock.at + minutes(1))
    assert slot.value == 61 and slot.selected is not None
    assert slot.selected.canonical_game_id is None and slot.selected.binding_ref is None
    assert SampleFlag.BINDING_UNKNOWN in slot.selected.quality_flags
    # It belongs to no entity: an entity read finds nothing bound, not a zero.
    history = read_entity_history(
        store, MappingHistory(), ENTITY_A, KEY, BINDING_SOURCE, window(T0, 1), clock.at + minutes(1)
    )
    assert history.combined is None and history.unavailable_reason == NO_BINDING_REASON


def test_a_run_refuses_a_history_source_and_an_unaligned_slot(tmp_path: Path) -> None:
    clock, store = Ticker(), store_in(tmp_path)
    wrong = FakeSampler(clock)
    wrong.mode = SourceMode.FETCH_HISTORY
    with pytest.raises(ValueError, match="sample-now source"):
        run_sampling_slot((target(),), wrong, store, now=clock)
    with pytest.raises(ValueError, match="start of a slot"):
        run_sampling_slot(
            (target(),), FakeSampler(clock), store, now=clock, scheduled_for=T0 + minutes(3)
        )


def test_health_is_counts_and_times_only(tmp_path: Path) -> None:
    store = store_in(tmp_path)
    store.append(attempt(T0, value=11, attempt_id="a"))
    store.append(attempt(T0 + minutes(2), value=12, attempt_id="b"))  # a duplicate success
    store.append(attempt(T0 + SLOT.step, SampleStatus.FAILED, attempt_id="c"))
    store.append(
        attempt(T0 + 2 * SLOT.step + minutes(8), value=13, attempt_id="d", flags=(SampleFlag.LATE,))
    )
    health = target_health(store, KEY, APP, T0 + 4 * SLOT.step, 5, T0 + minutes(120))
    assert (health.slots, health.observed, health.failed, health.not_sampled) == (5, 2, 1, 2)
    assert health.coverage == 0.4 and health.duplicates == 1 and health.late == 1
    assert health.last_observed_at == T0 + 2 * SLOT.step + minutes(8)
    assert health.slots_since_last_observation == 2
    assert "value" not in health.model_dump()
    empty = target_health(store, KEY, "570", T0, 4, T0)
    assert empty.observed == 0 and empty.last_observed_at is None


# --- rebinding: history is resolved at observation time ----------------------------------

REBOUND_AT = T0 + 4 * SLOT.step  # T: app X moves from entity A to entity B
APP_X = SourceRecordKey(source=BINDING_SOURCE, source_id=APP)


def rebound_mapping() -> MappingHistory:
    before = MappingHistory().bind(APP_X, ENTITY_A, T0 - timedelta(days=30), "decision:1")
    return before.bind(APP_X, ENTITY_B, REBOUND_AT, "decision:2")


def rebinding_store(tmp_path: Path) -> LocalSampleStore:
    store = store_in(tmp_path)
    for index in range(8):  # four slots before T, four after
        at = T0 + index * SLOT.step + minutes(1)
        store.append(
            attempt(
                at,
                value=1000 + index,
                attempt_id=f"s{index}",
                entity=ENTITY_A if at < REBOUND_AT else ENTITY_B,
            )
        )
    return store


def test_each_entity_receives_only_the_samples_observed_while_it_owned_the_app(
    tmp_path: Path,
) -> None:
    store, mapping = rebinding_store(tmp_path), rebound_mapping()
    whole, today = window(T0, 8), T0 + timedelta(days=1)
    before_bytes = {p: p.read_bytes() for p in (tmp_path / "samples").rglob("*.jsonl")}

    a = read_entity_history(store, mapping, ENTITY_A, KEY, BINDING_SOURCE, whole, today)
    b = read_entity_history(store, mapping, ENTITY_B, KEY, BINDING_SOURCE, whole, today)
    assert a.combined is not None and b.combined is not None
    assert [slot.value for slot in a.combined] == [1000, 1001, 1002, 1003]
    assert [slot.value for slot in b.combined] == [1004, 1005, 1006, 1007]
    assert all(slot.key.slot_start < REBOUND_AT for slot in a.combined)
    assert all(slot.key.slot_start >= REBOUND_AT for slot in b.combined)
    # Reading changed nothing on disk, and the two histories share one source file.
    assert before_bytes == {p: p.read_bytes() for p in (tmp_path / "samples").rglob("*.jsonl")}
    assert len(before_bytes) == 1


def test_a_replay_from_before_the_rebinding_does_not_know_about_it(tmp_path: Path) -> None:
    store, mapping = rebinding_store(tmp_path), rebound_mapping()
    before_t = REBOUND_AT - minutes(5)
    known = mapping_as_known(mapping, before_t)
    assert [(b.canonical_game_id, b.valid_to) for b in known.bindings] == [(ENTITY_A, None)]

    whole = window(T0, 8)
    a = read_entity_history(store, mapping, ENTITY_A, KEY, BINDING_SOURCE, whole, before_t)
    b = read_entity_history(store, mapping, ENTITY_B, KEY, BINDING_SOURCE, whole, before_t)
    assert a.combined is not None
    # Only what had been stored by then; the rest of the window is not sampled yet.
    assert [slot.value for slot in a.combined] == [1000, 1001, 1002, 1003, None, None, None, None]
    assert [slot.state for slot in a.combined[4:]] == [SlotState.NOT_SAMPLED] * 4
    assert b.combined is None and b.unavailable_reason == NO_BINDING_REASON

    # The same past question asked later gives the same answer: history is reproducible.
    again = read_entity_history(store, mapping, ENTITY_A, KEY, BINDING_SOURCE, whole, before_t)
    assert again == a
    after_t = read_entity_history(
        store, mapping, ENTITY_A, KEY, BINDING_SOURCE, whole, T0 + timedelta(days=1)
    )
    assert after_t.combined is not None
    assert [slot.value for slot in after_t.combined] == [1000, 1001, 1002, 1003]


def test_the_collection_time_stamp_is_an_audit_record_not_the_authority(tmp_path: Path) -> None:
    store = store_in(tmp_path)
    # Stamped with entity B, although the mapping says the app belonged to A then.
    store.append(attempt(T0 + minutes(1), value=5, entity=ENTITY_B))
    mapping = MappingHistory().bind(APP_X, ENTITY_A, T0 - timedelta(days=1), "decision:1")
    as_of = T0 + minutes(30)
    a = read_entity_history(store, mapping, ENTITY_A, KEY, BINDING_SOURCE, window(T0, 1), as_of)
    assert a.combined is not None and a.combined[0].value == 5
    assert a.combined[0].selected is not None
    assert a.combined[0].selected.canonical_game_id == ENTITY_B  # the stamp is preserved as written


def test_several_apps_for_one_entity_are_kept_apart_and_never_combined(tmp_path: Path) -> None:
    store = store_in(tmp_path)
    other = SourceRecordKey(source=BINDING_SOURCE, source_id="570")
    mapping = (
        MappingHistory()
        .bind(APP_X, ENTITY_A, T0 - timedelta(days=2), "decision:1")
        .bind(other, ENTITY_A, T0 - timedelta(days=1), "decision:2")
    )
    store.append(attempt(T0 + minutes(1), value=300, attempt_id="x"))
    store.append(attempt(T0 + minutes(1), value=40, app="570", attempt_id="y"))
    history = read_entity_history(
        store, mapping, ENTITY_A, KEY, BINDING_SOURCE, window(T0, 1), T0 + minutes(30)
    )
    assert history.combined is None  # not a sum, not an average, not a chosen app
    assert history.unavailable_reason == "multi_app_combination_unresolved"
    assert history.unavailable_reason == baseline_rules.MULTI_APP_UNRESOLVED_REASON
    assert {h.source_native_id: h.slots[0].value for h in history.by_native_id} == {
        "570": 40,
        "730": 300,
    }


# --- recorded design constants -----------------------------------------------------------


def test_baseline_rules_v1_records_the_approved_coverage_constants() -> None:
    assert baseline_rules.BASELINE_RULES_VERSION == "baseline_rules_v1"
    assert baseline_rules.EXPECTED_SAMPLES_PER_HOUR == 4
    assert baseline_rules.MIN_SAMPLES_PER_VALID_HOUR == 3  # not 2 of 4
    assert baseline_rules.MIN_VALID_HOURS_PER_VALID_DAY == 18
    assert (baseline_rules.MIN_SAMPLES_PER_VALID_DAY, baseline_rules.EXPECTED_SAMPLES_PER_DAY) == (
        72,
        96,
    )
    assert baseline_rules.LOW_BASE_FLOOR_PLAYERS == 50
    assert SLOT.step * baseline_rules.EXPECTED_SAMPLES_PER_HOUR == timedelta(hours=1)
