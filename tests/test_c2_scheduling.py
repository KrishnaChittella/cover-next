"""Part C2-A: unattended scheduling, offline.

Everything here runs against in-memory stores. No cloud service, no source and no
scheduler is involved, and nothing is switched on.
"""

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from metaspike.domain.observations import Freshness, Observation, ObservationStatus
from metaspike.domain.signals import SourceErrorCode
from metaspike.domain.timeseries import Granularity
from metaspike.jobs import runlog
from metaspike.jobs.collect import (
    WORK_ORDERS_ENV,
    bootstrap_marker_name,
    bootstrapped,
    collect_wikimedia,
    collect_wikimedia_job,
    seed_bootstrap_markers,
)
from metaspike.jobs.dispatch import dispatch
from metaspike.jobs.targets import Target, TargetsDocument, bootstrap_targets, publish_targets
from metaspike.scheduling import simulation
from metaspike.scheduling.config import PRODUCTION, DispatchConfig, SourceCaps
from metaspike.scheduling.control import (
    CONTROL_OBJECT,
    ControlRecord,
    Pin,
    SourceSwitch,
    load_control,
)
from metaspike.scheduling.health import (
    Breaker,
    Gate,
    HealthState,
    Outcome,
    SourceHealth,
    apply_outcome,
    gate,
    state_of,
)
from metaspike.scheduling.planner import Disposition, DueItem, plan_source
from metaspike.scheduling.projection import REVIEW_USD, Workload, project
from metaspike.scheduling.schedule import (
    POINTER,
    ScheduleStore,
    build_schedule,
    due_partitions,
    jump_hash,
    long_tail_cohort,
    period_cohort,
)
from metaspike.scheduling.simulation import SIM_SAMPLED, SIM_UNCLEARED, Scenario, Shape
from metaspike.scheduling.tick import tick_of
from metaspike.scheduling.tiers import (
    DEFAULT_CAPS,
    INTERVAL_MINUTES,
    RULES_VERSION,
    Attention,
    Proposal,
    Tier,
    fit_to_capacity,
    propose,
)
from metaspike.scheduling.workorder import (
    ClaimRefused,
    OrderPointer,
    OrderReceipt,
    OrderTarget,
    WorkOrder,
    claim,
    complete,
    issue,
    order_id,
    order_object,
    pointer_object,
    read_receipt,
    receipt_object,
)
from metaspike.sources.base import SourceError, SourceMode
from metaspike.sources.wikimedia import SOURCE_ID as WIKIMEDIA
from metaspike.storage.base import StorageCorruptionError
from metaspike.warehouse.landing import policy_version
from metaspike.warehouse.objects import MemoryObjectStore, PreconditionFailed
from metaspike.warehouse.tables import TableRef
from tests.warehouse_support import World, real_policy, runtime_for, world

STEAM = "steam_current_players"
MONDAY = datetime(2026, 10, 5, tzinfo=UTC)
TUESDAY = datetime(2026, 10, 6, tzinfo=UTC)
THREE = MONDAY.replace(hour=3)
LEDGER = TableRef("ms_ops", "expected_targets")
WIKI_CAPS = PRODUCTION.sources[WIKIMEDIA]


def arm(store: MemoryObjectStore, until: datetime, **changes: Any) -> ControlRecord:
    record = ControlRecord(
        version=1,
        enabled=True,
        armed_until=until,
        sources={WIKIMEDIA: SourceSwitch(enabled=True)},
    ).model_copy(update=changes)
    body = record.model_dump_json().encode("utf-8")
    try:
        store.create(CONTROL_OBJECT, body)
    except PreconditionFailed:
        store.replace(CONTROL_OBJECT, body, store.get(CONTROL_OBJECT).generation)
    return record


class Starter:
    def __init__(self, failing: str | None = None) -> None:
        self.started: list[str] = []
        self.failing = failing

    def start(self, job: str) -> str:
        if job == self.failing:
            raise RuntimeError("HTTP 500")
        self.started.append(job)
        return f"executions/{job}-1"


class Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


def sampled(native_id: str, source: str = SIM_SAMPLED) -> Target:
    return Target(
        source=source,
        metric="concurrent_players",
        granularity=Granularity.FIFTEEN_MINUTES,
        mode=SourceMode.SAMPLE_NOW,
        source_native_id=native_id,
        canonical_game_id=f"game-{native_id}",
    )


def article(index: int) -> Target:
    return Target(
        source=WIKIMEDIA,
        metric="pageviews",
        granularity=Granularity.DAILY,
        mode=SourceMode.FETCH_HISTORY,
        source_native_id=f"en.wikipedia.org/Article_{index:05d}",
        canonical_game_id=f"game-{index:05d}",
    )


SIM = simulation.simulation_config()


# --- tiers: a shadow rule ------------------------------------------------------------------


def test_the_rule_is_deterministic_and_never_escalates_on_missing_data() -> None:
    assert RULES_VERSION == "tiering_rules_v0"
    assert propose(None).tier is Tier.LONG_TAIL and propose(None).reason == "no_attention_data"
    unknown = propose(Attention())
    assert unknown.tier is Tier.LONG_TAIL and unknown.reason == "insufficient_history"
    # No median, or a median of zero, is unknown. It is not "infinitely above normal".
    assert propose(Attention(latest=50_000.0)).tier is Tier.LONG_TAIL
    assert propose(Attention(latest=50_000.0, trailing_median=0.0)).tier is Tier.LONG_TAIL
    cases = {
        Attention(latest=10_000.0, trailing_median=1_000.0): Tier.SURGE,
        Attention(latest=3_300.0, trailing_median=1_000.0): Tier.HOT,
        Attention(latest=2_100.0, trailing_median=1_000.0): Tier.ACTIVE,
        Attention(latest=1_400.0, trailing_median=1_000.0): Tier.WARM,
        Attention(latest=900.0, trailing_median=1_000.0): Tier.LONG_TAIL,
        # A large ratio on a tiny series is not a surge.
        Attention(latest=60.0, trailing_median=10.0): Tier.WARM,
        Attention(latest=1.0, trailing_median=1.0, days_since_event=0): Tier.SURGE,
        Attention(latest=1.0, trailing_median=1.0, days_since_event=2): Tier.HOT,
    }
    for attention, tier in cases.items():
        assert propose(attention).tier is tier, attention
        assert propose(attention) == propose(attention)


def test_a_full_tier_moves_its_weakest_down_one_tier_and_never_exceeds_its_cap() -> None:
    caps = {Tier.SURGE: 2, Tier.HOT: 2, Tier.ACTIVE: 1, Tier.WARM: 1}
    proposals = {
        f"g{i}": Proposal(tier=Tier.SURGE, score=float(i), reason="event_today") for i in range(8)
    }
    proposals["quiet"] = Proposal(tier=Tier.LONG_TAIL, score=0.0, reason="quiet")
    fitted = fit_to_capacity(proposals, caps)
    by_tier: dict[Tier, list[str]] = {}
    for key, tier in fitted.items():
        by_tier.setdefault(tier, []).append(key)
    assert sorted(by_tier[Tier.SURGE]) == ["g6", "g7"]  # the strongest keep their place
    assert sorted(by_tier[Tier.HOT]) == ["g4", "g5"]
    assert by_tier[Tier.ACTIVE] == ["g3"] and by_tier[Tier.WARM] == ["g2"]
    assert sorted(by_tier[Tier.LONG_TAIL]) == ["g0", "g1", "quiet"]
    # The same result whatever order the proposals arrive in.
    assert fit_to_capacity(dict(reversed(list(proposals.items()))), caps) == fitted
    for tier, cap in caps.items():
        assert len(by_tier[tier]) <= cap
    assert DEFAULT_CAPS == {Tier.SURGE: 25, Tier.HOT: 75, Tier.ACTIVE: 300, Tier.WARM: 1000}
    assert INTERVAL_MINUTES[Tier.SURGE] == 15 and INTERVAL_MINUTES[Tier.LONG_TAIL] == 1440


def test_production_records_shadow_proposals_and_acts_on_none_of_them() -> None:
    assert PRODUCTION.apply_shadow_tiers is False
    assert set(PRODUCTION.sources) == {WIKIMEDIA}  # the only source with any work
    assert PRODUCTION.tick_minutes == 60
    targets = [sampled("1"), sampled("2")]
    proposals = {(SIM_SAMPLED, "1"): Proposal(tier=Tier.SURGE, score=9.0, reason="event_today")}
    manifest, partitions = build_schedule(
        targets, targets_version="v", catalogued=2, cleared=lambda s: True,
        config=SIM, at=MONDAY, proposals=proposals,
    )  # fmt: skip
    entries = [e for part in partitions.values() for e in part]
    assert {e.tier for e in entries} == {Tier.LONG_TAIL}  # nobody moved
    assert {e.source_native_id: e.shadow_tier for e in entries} == {"1": Tier.SURGE, "2": None}
    assert manifest.high_frequency == 0


# --- the schedule --------------------------------------------------------------------------


def test_the_five_sets_are_counted_apart_and_uncleared_sources_are_never_scheduled() -> None:
    shape = Shape(catalogued=1000, monitored=300, wikimedia=200, sampled=150, uncleared=120)
    document = simulation.synthetic_targets(shape)
    tiering = simulation.tiering_for(0, DEFAULT_CAPS)
    tiers, proposals = tiering(document)
    manifest, partitions = build_schedule(
        document.targets, targets_version=document.version, catalogued=shape.catalogued,
        cleared=lambda source: source != SIM_UNCLEARED, config=SIM, at=MONDAY,
        tiers=tiers, proposals=proposals,
    )  # fmt: skip
    assert manifest.catalogued == 1000 and manifest.monitored == 300
    assert manifest.eligible == {SIM_SAMPLED: 150, WIKIMEDIA: 200}
    assert sum(manifest.partitions.values()) == 350
    assert not [key for key in partitions if key.startswith(SIM_UNCLEARED)]
    assert manifest.high_frequency < 20 < manifest.eligible[SIM_SAMPLED]
    # A source with no configured limits is not scheduled either, however cleared it is.
    stray = [sampled("9", source="unconfigured")]
    empty, _ = build_schedule(
        stray, targets_version="v", catalogued=1, cleared=lambda s: True, config=SIM, at=MONDAY
    )
    assert empty.partitions == {} and empty.monitored == 1


def test_the_schedule_version_is_its_content_and_publishing_it_twice_is_one_schedule() -> None:
    document = simulation.synthetic_targets(Shape.typical(1000))

    def build(at: datetime) -> Any:
        return build_schedule(
            document.targets,
            targets_version=document.version,
            catalogued=1000,
            cleared=lambda s: s != SIM_UNCLEARED,
            config=SIM,
            at=at,
        )

    first, partitions = build(MONDAY)
    assert build(MONDAY)[0] == first
    assert build(TUESDAY)[0].version == first.version  # when it was built is not content
    store = MemoryObjectStore()
    schedules = ScheduleStore(store)
    assert schedules.current() is None
    schedules.publish(first, partitions)
    written = store.writes
    schedules.publish(first, partitions)
    assert schedules.current() == first and store.writes == written  # nothing rewritten
    key = next(iter(partitions))
    assert schedules.partition(first.version, key) == partitions[key]
    store.replace(POINTER, b"{not json", store.get(POINTER).generation)
    with pytest.raises(StorageCorruptionError):
        schedules.current()


def test_long_tail_cohorts_depend_on_the_id_alone_and_are_evenly_spread() -> None:
    ids = [str(100_000 + i) for i in range(4800)]
    cohorts = [long_tail_cohort(i) for i in ids]
    assert cohorts == [long_tail_cohort(i) for i in ids]
    sizes = [cohorts.count(c) for c in range(24)]
    assert len(sizes) == 24 and min(sizes) > 140 and max(sizes) < 260  # 200 expected each
    # Growing the catalogue moves nobody: the first 1,000 targets keep their partition.
    small = simulation.synthetic_targets(Shape(catalogued=2000, monitored=1000, wikimedia=0,
                                               sampled=1000, uncleared=0))  # fmt: skip
    large = simulation.synthetic_targets(Shape(catalogued=50_000, monitored=9000, wikimedia=0,
                                               sampled=9000, uncleared=0))  # fmt: skip

    def where(document: TargetsDocument) -> dict[str, str]:
        _, partitions = build_schedule(
            document.targets, targets_version="v", catalogued=document.catalogued or 0,
            cleared=lambda s: True, config=SIM, at=MONDAY,
        )  # fmt: skip
        return {e.source_native_id: key for key, part in partitions.items() for e in part}

    before, after = where(small), where(large)
    kept = set(before) & set(after)
    assert kept and all(before[i] == after[i] for i in kept)


def test_a_period_source_grows_cohorts_with_minimal_reshuffling() -> None:
    assert [jump_hash(k, 1) for k in range(50)] == [0] * 50
    keys = [f"en.wikipedia.org/Article_{i:05d}" for i in range(6000)]
    for fewer, more in ((1, 2), (4, 5), (9, 10), (23, 24)):
        moved = [k for k in keys if period_cohort(k, fewer) != period_cohort(k, more)]
        # Only what must move to fill the new cohort does, and it all moves there.
        assert all(period_cohort(k, more) == more - 1 for k in moved)
        assert len(moved) < len(keys) / more * 1.25
    few = [article(i) for i in range(14)]
    manifest, partitions = build_schedule(
        few, targets_version="v", catalogued=14, cleared=lambda s: True, config=SIM, at=MONDAY
    )
    assert manifest.period_cohorts == {WIKIMEDIA: 1}  # fourteen articles: one run a day
    assert list(partitions) == [f"{WIKIMEDIA}/period/c00"]
    many = [article(i) for i in range(5000)]
    manifest, partitions = build_schedule(
        many, targets_version="v", catalogued=5000, cleared=lambda s: True, config=SIM, at=MONDAY
    )
    assert manifest.period_cohorts == {WIKIMEDIA: 9}
    assert max(len(p) for p in partitions.values()) < 700


def test_a_tick_reads_only_the_partitions_that_can_be_due_on_it() -> None:
    document = simulation.synthetic_targets(Shape.typical(10_000))
    tiers, proposals = simulation.tiering_for(0, DEFAULT_CAPS)(document)
    manifest, _ = build_schedule(
        document.targets, targets_version="v", catalogued=10_000,
        cleared=lambda s: s != SIM_UNCLEARED, config=SIM, at=MONDAY, tiers=tiers,
        proposals=proposals,
    )  # fmt: skip

    def at(hour: int, minute: int = 0) -> datetime:
        return MONDAY.replace(hour=hour, minute=minute)

    def due(source: str, moment: datetime) -> list[str]:
        return due_partitions(manifest, source, moment, SIM)

    assert due(WIKIMEDIA, at(3)) == [f"{WIKIMEDIA}/period/c00"]
    assert due(WIKIMEDIA, at(4)) == [f"{WIKIMEDIA}/period/c01"]
    assert due(WIKIMEDIA, at(2)) == [] and due(WIKIMEDIA, at(3, 15)) == []
    assert due(WIKIMEDIA, at(9)) == []  # only as many cohorts as the source needs
    s = SIM_SAMPLED
    assert due(s, at(9, 15)) == [f"{s}/t0"]
    assert due(s, at(9, 30)) == [f"{s}/t0", f"{s}/t1"]
    assert due(s, at(10)) == [f"{s}/t0", f"{s}/t1", f"{s}/t2", f"{s}/t4/c10"]
    assert due(s, at(9)) == [f"{s}/t0", f"{s}/t1", f"{s}/t2", f"{s}/t3", f"{s}/t4/c09"]
    assert due(SIM_UNCLEARED, at(9)) == []
    # Over a day every long-tail cohort is read exactly once.
    tail = [k for h in range(24) for k in due(s, at(h)) if "/t4/" in k]
    assert len(tail) == len(set(tail)) == 24


# --- the control record --------------------------------------------------------------------


def test_the_control_record_fails_closed() -> None:
    now = MONDAY.replace(hour=3)
    store = MemoryObjectStore()
    assert load_control(store, now).refusal == "control_record_absent"
    arm(store, now + timedelta(hours=2))
    record = load_control(store, now).record
    assert record is not None and record.source_enabled(WIKIMEDIA)
    assert not record.source_enabled(STEAM)  # a source it does not name is off
    assert load_control(store, now + timedelta(hours=2)).refusal == "control_expired"
    arm(store, now + timedelta(hours=2), enabled=False)
    assert load_control(store, now).refusal == "control_disabled"
    for body in (b"", b"{", b'{"version": 2, "enabled": true}', b'{"enabled": true}'):
        store.replace(CONTROL_OBJECT, body, store.get(CONTROL_OBJECT).generation)
        assert load_control(store, now).refusal == "control_record_malformed"
    # An identity that may not read the record gets a refusal, not an exception.
    blind = store.view(readable_prefixes=("elsewhere/",))
    assert load_control(blind, now).refusal == "control_record_unreadable"
    assert SourceSwitch().enabled is False  # naming a source is not enabling it


def test_a_pin_is_explicit_expires_and_then_does_nothing() -> None:
    now = MONDAY.replace(hour=9)
    pin = Pin(
        source=SIM_SAMPLED, metric="concurrent_players", granularity=Granularity.FIFTEEN_MINUTES,
        mode=SourceMode.SAMPLE_NOW, source_native_id="730", tier=Tier.SURGE,
        expires_at=now + timedelta(hours=1), reason="supervised C2 check", created_by="owner",
    )  # fmt: skip
    record = ControlRecord(
        version=1, enabled=True, armed_until=now + timedelta(days=1), pins=(pin,)
    )
    assert record.active_pins(now, 50) == (pin,)
    assert record.active_pins(now + timedelta(hours=1), 50) == ()  # at its expiry it is gone
    assert record.active_pins(now, 0) == ()
    for missing in ("reason", "created_by", "expires_at"):
        with pytest.raises(ValueError):
            Pin.model_validate(pin.model_dump(exclude={missing}))
    with pytest.raises(ValueError):
        Pin.model_validate({**pin.model_dump(), "reason": ""})


# --- health and the circuit breaker --------------------------------------------------------


def test_the_breaker_opens_on_three_failures_and_at_once_on_a_rate_limit() -> None:
    caps = SourceCaps(collector_job="j", max_requests_per_day=100)
    t = MONDAY
    health = SourceHealth(source="any_source")
    for failures in (1, 2):
        health = apply_outcome(health, Outcome.FAILED, t, caps)
        assert health.breaker is Breaker.CLOSED and health.consecutive_failures == failures
        assert state_of(health, t, caps) is HealthState.DEGRADED
    health = apply_outcome(health, Outcome.MISSING, t, caps)  # a lost run counts as a failure
    assert health.breaker is Breaker.OPEN and health.cooldown_minutes == 60
    assert health.reopen_at == t + timedelta(hours=1)
    assert state_of(health, t, caps) is HealthState.UNAVAILABLE
    assert gate(health, t + timedelta(minutes=59))[0] is Gate.BLOCKED
    way, probing = gate(health, t + timedelta(hours=1))
    assert way is Gate.PROBE and probing.breaker is Breaker.HALF_OPEN
    # A failed probe opens it again for twice as long, up to the configured maximum.
    cooldowns = []
    for _ in range(7):
        probing = apply_outcome(probing, Outcome.FAILED, t, caps)
        cooldowns.append(probing.cooldown_minutes)
        probing = gate(probing, t + timedelta(days=2))[1]
    assert cooldowns == [120, 240, 480, 960, 1440, 1440, 1440]
    recovered = apply_outcome(probing, Outcome.SUCCEEDED, t, caps)
    assert recovered.breaker is Breaker.CLOSED and recovered.consecutive_failures == 0
    assert recovered.cooldown_minutes == 0 and state_of(recovered, t, caps) is HealthState.HEALTHY

    limited = apply_outcome(SourceHealth(source="any_source"), Outcome.RATE_LIMITED, t, caps)
    assert limited.breaker is Breaker.OPEN and limited.last_rate_limited_at == t
    assert state_of(limited, t, caps) is HealthState.RATE_LIMITED
    partial = apply_outcome(SourceHealth(source="x"), Outcome.PARTIAL, t, caps)
    assert partial.breaker is Breaker.CLOSED and partial.last_success == t


def test_health_states_are_states_and_disabled_outranks_what_it_explains() -> None:
    caps = SourceCaps(collector_job="j", max_requests_per_day=100)
    now = MONDAY
    fine = SourceHealth(source="s", last_success=now)
    assert state_of(fine, now, caps, policy_cleared=False) is HealthState.DISABLED_BY_POLICY
    assert state_of(fine, now, caps, credential_present=False) is HealthState.DISABLED_NO_CREDENTIAL
    assert state_of(fine, now, caps, control_enabled=False) is HealthState.DISABLED_BY_CONTROL
    old = SourceHealth(source="s", last_success=now - timedelta(hours=37))
    assert state_of(old, now, caps) is HealthState.STALE
    assert state_of(SourceHealth(source="s"), now, caps) is HealthState.HEALTHY
    waiting = SourceHealth(source="s", pending_order_id="abc")
    assert gate(waiting, now)[0] is Gate.BUSY
    # No field of a health record is a measurement that could be read as a value of zero.
    numeric = {"consecutive_failures", "cooldown_minutes"}
    assert {
        name for name, field in SourceHealth.model_fields.items() if field.annotation is int
    } == numeric


# --- bounded planning ----------------------------------------------------------------------


def due(
    native_id: str, tier: Tier = Tier.LONG_TAIL, at: datetime = MONDAY, life: int = 24
) -> DueItem:
    return DueItem(
        source=SIM_SAMPLED, metric="m", granularity=Granularity.FIFTEEN_MINUTES,
        mode=SourceMode.SAMPLE_NOW, source_native_id=native_id, tier=tier, slot=at, due_at=at,
        expires_at=at + timedelta(hours=life), reason="r",
    )  # fmt: skip


def plan(fresh: Sequence[DueItem], backlog: Sequence[DueItem] = (), **options: Any) -> Any:
    settings: dict[str, Any] = {
        "caps": SourceCaps(collector_job="j", max_requests_per_day=10_000),
        "now": MONDAY,
        "gate": Gate.OPEN_FOR_WORK,
        "enabled": True,
        "requests_left_today": 10_000,
    }
    settings.update(options)
    return plan_source(SIM_SAMPLED, fresh=fresh, backlog=backlog, **settings)


def test_an_execution_is_given_no_more_than_its_source_allows() -> None:
    caps = SourceCaps(collector_job="j", max_requests_per_day=10_000)
    assert caps.targets_per_execution == 600  # 600 targets, 1,000 requests with retries
    tight = SourceCaps(collector_job="j", max_requests_per_day=10_000,
                       max_requests_per_execution=300)  # fmt: skip
    assert tight.targets_per_execution == 200  # the stricter limit wins
    items = [due(f"{i:04d}") for i in range(900)]
    result = plan(items)
    assert len(result.planned) == 600 and len(result.deferred) == 300 and not result.expired
    assert [i.source_native_id for i in result.planned] == [f"{i:04d}" for i in range(600)]
    assert len(plan(items, caps=tight).planned) == 200
    assert len(plan(items, targets_limit=50).planned) == 50  # the control record may lower it
    assert len(plan(items, targets_limit=5000).planned) == 600  # and can never raise it
    assert len(plan(items, requests_left_today=10).planned) == 10  # the day's budget
    spent = plan(items, requests_left_today=0)
    assert not spent.planned and len(spent.deferred) == 900
    with pytest.raises(ValueError):
        SourceCaps(collector_job="j", max_requests_per_day=1, max_executions_per_tick=2)


def test_selection_is_by_tier_then_age_then_id_whatever_order_work_arrives_in() -> None:
    earlier = MONDAY - timedelta(hours=1)
    items = [
        due("d", Tier.LONG_TAIL), due("b", Tier.SURGE), due("c", Tier.LONG_TAIL, earlier),
        due("a", Tier.HOT), due("e", Tier.SURGE),
    ]  # fmt: skip
    order = [i.source_native_id for i in plan(items).planned]
    assert order == ["b", "e", "a", "c", "d"]
    assert [i.source_native_id for i in plan(list(reversed(items))).planned] == order
    two = plan(items, targets_limit=2)
    assert [i.source_native_id for i in two.planned] == ["b", "e"]
    assert [i.source_native_id for i in two.deferred] == ["a", "c", "d"]


def test_a_backlog_is_bounded_drains_at_the_capped_rate_and_gives_up_what_is_stale() -> None:
    caps = SourceCaps(collector_job="j", max_requests_per_day=100_000, max_backlog=1000)
    items = [due(f"{i:05d}") for i in range(2500)]
    first = plan(items, caps=caps)
    assert len(first.planned) == 600 and len(first.deferred) == 1000
    assert len(first.overflowed) == 900  # beyond the bound it is given up, and says so
    assert {d for _, d in first.dispositions()} == {
        Disposition.PLANNED, Disposition.DEFERRED_CAP, Disposition.BACKLOG_OVERFLOW
    }  # fmt: skip
    # Later ticks with nothing new: the backlog drains one execution's worth at a time.
    second = plan([], first.deferred, caps=caps, now=MONDAY + timedelta(hours=1))
    assert len(second.planned) == 600 and len(second.deferred) == 400
    assert second.carried == 400  # still waiting: nothing new to record about them
    assert [d for _, d in second.dispositions()].count(Disposition.DEFERRED_CAP) == 0
    third = plan([], second.deferred, caps=caps, now=MONDAY + timedelta(hours=2))
    assert len(third.planned) == 400 and not third.deferred
    # Work past its expiry is not retried for ever: it becomes a recorded expiry.
    late = plan([], first.deferred, caps=caps, now=MONDAY + timedelta(hours=24))
    assert not late.planned and len(late.expired) == 1000
    assert {d for _, d in late.dispositions()} == {Disposition.EXPIRED}
    # The same slot arriving again while it waits is one piece of work, not two.
    again = plan(items[:5], items[:5], caps=caps)
    assert len(again.planned) == 5


def test_an_open_breaker_or_a_disabled_source_builds_up_no_backlog() -> None:
    items = [due(f"{i:03d}") for i in range(40)]
    blocked = plan(items, items[:10], gate=Gate.BLOCKED)
    assert not blocked.planned and not blocked.deferred and len(blocked.skipped) == 40
    assert blocked.skipped_as is Disposition.SKIPPED_BREAKER_OPEN
    probe = plan(items, gate=Gate.PROBE)
    assert probe.probe and len(probe.planned) == 5 and len(probe.skipped) == 35
    off = plan(items, enabled=False)
    assert not off.planned and off.skipped_as is Disposition.SKIPPED_DISABLED
    busy = plan(items, gate=Gate.BUSY)
    assert not busy.planned and len(busy.deferred) == 40
    assert busy.deferred_as is Disposition.DEFERRED_BUSY


# --- work orders ---------------------------------------------------------------------------


def an_order(tick: datetime = THREE, n: int = 3, **changes: Any) -> WorkOrder:
    fields: dict[str, Any] = {
        "order_id": order_id(WIKIMEDIA, tick, "s1"),
        "source": WIKIMEDIA,
        "metric": "pageviews",
        "granularity": Granularity.DAILY,
        "mode": SourceMode.FETCH_HISTORY,
        "slot": tick.replace(hour=0) - timedelta(days=2),
        "window_days": 4,
        "tick": tick,
        "created_at": tick,
        "expires_at": tick + timedelta(minutes=50),
        "schedule_version": "s1",
        "policy_version": "p1",
        "targets": tuple(
            OrderTarget(source_native_id=f"en.wikipedia.org/A{i}", tier=Tier.LONG_TAIL, reason="r")
            for i in range(n)
        ),
        "max_targets": n,
        "max_requests": n + 2,
    }
    fields.update(changes)
    return WorkOrder(**fields)


def test_an_order_is_immutable_and_issuing_it_twice_is_one_order() -> None:
    store = MemoryObjectStore()
    order = an_order()
    pointer = issue(store, order)
    assert pointer.object == order_object(WIKIMEDIA, order.order_id)
    assert order.order_id == order_id(WIKIMEDIA, order.tick, "s1") and len(order.order_id) == 32
    writes = store.writes
    assert issue(store, order) == pointer and store.writes == writes  # nothing rewritten
    # The same identity with other content is not quietly replaced.
    with pytest.raises(StorageCorruptionError):
        issue(store, order.model_copy(update={"max_requests": 99}))
    # An order may not claim more than its own limits, or an id that is not its own.
    with pytest.raises(ValueError):
        issue(store, an_order(max_targets=2))
    with pytest.raises(ValueError):
        issue(store, an_order(order_id="0" * 32))
    with pytest.raises(ValueError):
        an_order(n=0)
    assert order_id(WIKIMEDIA, order.tick, "s2") != order.order_id
    assert order_id(STEAM, order.tick, "s1") != order.order_id


def test_the_pointer_moves_forward_on_its_generation_and_never_back() -> None:
    store = MemoryObjectStore()
    three, four = MONDAY.replace(hour=3), MONDAY.replace(hour=4)
    issue(store, an_order(three))
    first = store.get(pointer_object(WIKIMEDIA)).generation
    later = issue(store, an_order(four))
    assert later.tick == four and store.get(pointer_object(WIKIMEDIA)).generation > first
    # A slow, repeated run of the earlier tick cannot move the pointer back.
    assert issue(store, an_order(three)).tick == four
    held = OrderPointer.model_validate_json(store.get(pointer_object(WIKIMEDIA)).data)
    assert held == later
    # A stale generation is refused by the store itself.
    with pytest.raises(PreconditionFailed):
        store.replace(pointer_object(WIKIMEDIA), b"{}", first)


def test_a_collector_acts_only_on_a_valid_live_order_addressed_to_it() -> None:
    store = MemoryObjectStore()
    tick = MONDAY.replace(hour=3)
    during = tick + timedelta(minutes=5)

    def refused(now: datetime = during, version: str = "p1", source: str = WIKIMEDIA) -> str:
        with pytest.raises(ClaimRefused) as caught:
            claim(store, source, now, version)
        return caught.value.reason

    assert refused() == "no_order_pointer"
    order = an_order(tick)
    issue(store, order)
    assert claim(store, WIKIMEDIA, during, "p1") == order
    assert refused(now=tick + timedelta(minutes=50)) == "order_expired"  # never run stale
    assert refused(now=tick + timedelta(days=3)) == "order_expired"
    assert refused(version="p2") == "order_policy_version_changed"
    assert refused(source=STEAM) == "no_order_pointer"  # another source's prefix is empty
    name = pointer_object(WIKIMEDIA)
    good = store.get(name)

    def corrupt(**changes: Any) -> None:
        pointer = OrderPointer.model_validate_json(good.data).model_copy(update=changes)
        store.replace(name, pointer.model_dump_json().encode(), store.get(name).generation)

    corrupt(content_sha256="0" * 64)
    assert refused() == "order_hash_mismatch"
    corrupt(object=order_object(STEAM, order.order_id))
    assert refused() == "order_pointer_names_another_prefix"
    corrupt(order_id="f" * 32, object=order_object(WIKIMEDIA, "f" * 32))
    assert refused() == "order_missing"
    store.replace(name, b"not a pointer", store.get(name).generation)
    assert refused() == "order_pointer_malformed"
    store.replace(name, good.data, store.get(name).generation)

    receipt = OrderReceipt(
        order_id=order.order_id, source=WIKIMEDIA, outcome=Outcome.SUCCEEDED, requests_sent=3,
        targets_collected=3, targets_failed=0, completed_at=during, run_id="run-1",
    )  # fmt: skip
    complete(store, receipt)
    assert refused() == "order_already_completed"
    complete(store, receipt.model_copy(update={"outcome": Outcome.FAILED}))  # ignored
    assert read_receipt(store, WIKIMEDIA, order.order_id) == receipt


def test_a_collector_needs_only_exact_name_reads_on_its_own_orders_prefix() -> None:
    bucket = MemoryObjectStore()
    order = an_order()
    issue(bucket, order)
    # The collector's grant as it would be: its own prefix read-write, the orders of
    # its own source read-only, no listing and nothing else.
    collector = bucket.view(
        writable_prefixes=(f"{WIKIMEDIA}/",),
        readable_prefixes=(f"{WIKIMEDIA}/", f"dispatch/orders/{WIKIMEDIA}/"),
        can_list=False,
    )
    now = order.tick + timedelta(minutes=1)
    assert claim(collector, WIKIMEDIA, now, "p1") == order
    complete(
        collector,
        OrderReceipt(
            order_id=order.order_id,
            source=WIKIMEDIA,
            outcome=Outcome.SUCCEEDED,
            requests_sent=3,
            targets_collected=3,
            targets_failed=0,
            completed_at=now,
            run_id="r",
        ),
    )
    assert bucket.get(receipt_object(WIKIMEDIA, order.order_id)).data
    # Without that one read grant it is refused, and it can never see another source's.
    without = bucket.view(
        writable_prefixes=(f"{WIKIMEDIA}/",), readable_prefixes=(f"{WIKIMEDIA}/",), can_list=False
    )
    with pytest.raises(ClaimRefused) as caught:
        claim(without, WIKIMEDIA, now, "p1")
    assert caught.value.reason == "order_pointer_unreadable"
    with pytest.raises(ClaimRefused):
        claim(collector, STEAM, now, "p1")


# --- the dispatcher, through the job -------------------------------------------------------


def dispatcher(w: World, clock: Clock, **options: Any) -> tuple[Any, list[dict[str, Any]], Starter]:
    w.now = clock
    runtime, log = runtime_for(w, "ms-dispatch", **options)
    return runtime, log, Starter()


def test_without_a_live_control_record_a_tick_does_nothing_at_all() -> None:
    clock = Clock(MONDAY.replace(hour=3, second=20))
    w = world(clock, policy_for=real_policy)
    runtime, log, starter = dispatcher(w, clock)
    assert dispatch(runtime, starter) == {
        "outcome": "refused",
        "reason": "control_record_absent",
        "tick": "2026-10-05T03:00:00+00:00",
    }
    assert starter.started == [] and w.landing.list("") == ()
    assert w.operations.list("") == ()  # no schedule, no lease, no order, no counter
    assert log[-1]["event"] == "tick_refused" and log[-1]["severity"] == "WARNING"
    arm(w.operations, MONDAY.replace(hour=3))  # armed until exactly now: already expired
    assert dispatch(runtime, starter)["reason"] == "control_expired"
    assert starter.started == []


def test_one_tick_records_intent_writes_one_order_and_starts_one_collector() -> None:
    clock = Clock(MONDAY.replace(hour=3, second=20))
    w = world(clock, policy_for=real_policy)  # the real policies: Steam is not cleared
    publish_targets(
        w.operations,
        TargetsDocument(
            version="g7",
            generated_at=MONDAY,
            targets=(*bootstrap_targets(MONDAY).targets, sampled("730", STEAM)),
            catalogued=40,
        ),
    )
    arm(w.operations, MONDAY + timedelta(days=1))
    runtime, _, starter = dispatcher(w, clock)
    summary = dispatch(runtime, starter)
    assert starter.started == ["ms-wikimedia"]
    assert summary["expected_targets"] == 14 and summary["dispositions"] == {"planned": 14}
    assert summary["partitions_read"] == 1 and summary["schedule_regenerated"] is True
    assert summary["source_states"] == {WIKIMEDIA: "healthy"}
    manifest = ScheduleStore(w.operations).current()
    assert manifest is not None and manifest.catalogued == 40 and manifest.monitored == 15
    assert manifest.eligible == {WIKIMEDIA: 14}  # Steam is counted nowhere it could be due

    w.load_everything()
    ledger = w.warehouse.all_rows(LEDGER)
    assert len(ledger) == 14 and {r["source"] for r in ledger} == {WIKIMEDIA}
    assert {r["slot_start"] for r in ledger} == {datetime(2026, 10, 3, tzinfo=UTC)}
    assert {r["dispatch_run_id"] for r in ledger} == {"tick-20261005T0300Z"}
    assert {r["tier"] for r in ledger} == {4} and {r["disposition"] for r in ledger} == {"planned"}
    assert {r["due_at"] for r in ledger} == {MONDAY.replace(hour=3)}
    assert {r["due_reason"] for r in ledger} == {"default_long_tail"}
    assert {r["schedule_version"] for r in ledger} == {manifest.version}
    version = policy_version(real_policy(WIKIMEDIA))
    assert {r["policy_status"] for r in ledger} == {f"cleared:{version}"}
    assert {r["work_order_id"] for r in ledger} == set(summary["work_orders"])
    assert "not_sampled" not in json.dumps(ledger, default=str).lower()

    order = claim(w.operations, WIKIMEDIA, clock(), version)
    assert len(order.targets) == 14 and order.max_targets == 14 and order.max_requests == 22
    assert order.window_days == 14  # a Monday: the weekly reconciliation window
    assert order.expires_at == MONDAY.replace(hour=3, minute=50)
    assert order.schedule_version == manifest.version and not order.probe

    # The same tick again: the lease stops it. Nothing is recorded or started twice.
    again = Starter()
    assert dispatch(runtime, again)["outcome"] == "already_dispatched"
    assert again.started == []
    assert len([n for n in w.landing.list("dispatch/expected_targets/") if "manifest" in n]) == 1


def test_a_crash_after_planning_resumes_the_same_plan() -> None:
    clock = Clock(MONDAY.replace(hour=3, second=20))
    w = world(clock, policy_for=real_policy)
    arm(w.operations, MONDAY + timedelta(days=1))
    runtime, _, _ = dispatcher(w, clock)
    with pytest.raises(RuntimeError, match="could not start: ms-wikimedia"):
        dispatch(runtime, Starter(failing="ms-wikimedia"))
    stored = w.operations.get("dispatch/plans/tick-20261005T0300Z.json").data
    # The lease was taken: this tick is spent, and a rerun starts nothing a second time.
    rerun = Starter()
    assert dispatch(runtime, rerun)["outcome"] == "already_dispatched" and rerun.started == []
    assert w.operations.get("dispatch/plans/tick-20261005T0300Z.json").data == stored
    # Before the lease, a rerun reads the stored plan instead of making another.
    w.operations.delete("dispatch/ticks/tick-20261005T0300Z.json")
    clock.moment += timedelta(minutes=9)
    resumed = Starter()
    summary = dispatch(runtime, resumed)
    assert resumed.started == ["ms-wikimedia"] and summary["expected_targets"] == 14
    assert w.operations.get("dispatch/plans/tick-20261005T0300Z.json").data == stored
    assert json.loads(w.operations.get(f"dispatch/counters/{MONDAY:%Y-%m-%d}.json").data) == {
        "day": "2026-10-05", "requests": {WIKIMEDIA: 14}, "executions": 1
    }  # fmt: skip


def run_day(
    w: World, clock: Clock, hours: Sequence[int], *, collector: Any = None, day: datetime = TUESDAY
) -> dict[int, list[str]]:
    started: dict[int, list[str]] = {}
    for hour in hours:
        clock.moment = day.replace(hour=hour, second=9)
        runtime, _, starter = dispatcher(w, clock)
        dispatch(runtime, starter)
        started[hour] = starter.started
        if collector is not None and "ms-wikimedia" in starter.started:
            collector(clock.moment + timedelta(minutes=1))
    return started


def receipt_for(w: World, outcome: Outcome) -> Any:
    def file(at: datetime) -> None:
        order = claim(w.operations, WIKIMEDIA, at, policy_version(real_policy(WIKIMEDIA)))
        complete(
            w.operations,
            OrderReceipt(
                order_id=order.order_id,
                source=WIKIMEDIA,
                outcome=outcome,
                requests_sent=1,
                targets_collected=0,
                targets_failed=len(order.targets),
                completed_at=at,
                run_id="run",
            ),
        )

    return file


def test_a_day_starts_one_collection_and_one_warehouse_run_and_nothing_else() -> None:
    clock = Clock(TUESDAY)
    w = world(clock, policy_for=real_policy)
    arm(w.operations, TUESDAY + timedelta(days=2))
    started = run_day(w, clock, range(24), collector=receipt_for(w, Outcome.SUCCEEDED))
    assert {h: j for h, j in started.items() if j} == {3: ["ms-wikimedia"], 4: ["ms-warehouse"]}
    w.load_everything()
    assert len(w.warehouse.all_rows(LEDGER)) == 14
    assert w.operations.list("") != () and not w.operations.list("dispatch/orders/steam")
    # Features, parity and the catalogue job are not started by the C2 dispatcher at all.
    assert PRODUCTION.warehouse_job == "ms-warehouse"
    assert {c.collector_job for c in PRODUCTION.sources.values()} == {"ms-wikimedia"}


def test_an_outage_then_a_rate_limit_open_the_breaker_and_no_backlog_builds() -> None:
    clock = Clock(TUESDAY)
    w = world(clock, policy_for=real_policy)
    arm(w.operations, TUESDAY + timedelta(days=30))
    failing = receipt_for(w, Outcome.FAILED)
    days = [TUESDAY + timedelta(days=d) for d in range(5)]
    started = [run_day(w, clock, [3, 4], collector=failing, day=d) for d in days[:3]]
    assert [s[3] for s in started] == [["ms-wikimedia"]] * 3
    health = SourceHealth.model_validate_json(
        w.operations.get(f"dispatch/health/{WIKIMEDIA}.json").data
    )
    assert health.breaker is Breaker.OPEN and health.consecutive_failures == 3
    # Open: the next day's work is recorded as skipped and nothing is started for it.
    clock.moment = days[3].replace(hour=3, second=9)
    health = health.model_copy(update={"reopen_at": days[3] + timedelta(hours=12)})
    w.operations.replace(
        f"dispatch/health/{WIKIMEDIA}.json",
        health.model_dump_json().encode(),
        w.operations.get(f"dispatch/health/{WIKIMEDIA}.json").generation,
    )
    runtime, _, starter = dispatcher(w, clock)
    summary = dispatch(runtime, starter)
    assert starter.started == [] and summary["dispositions"] == {"skipped_breaker_open": 14}
    assert summary["source_states"] == {WIKIMEDIA: "unavailable"}
    assert json.loads(w.operations.get(f"dispatch/backlog/{WIKIMEDIA}.json").data) == {"items": []}
    # Half open: one small probe. Its success closes the breaker.
    probe = run_day(w, clock, [3], collector=receipt_for(w, Outcome.SUCCEEDED), day=days[4])
    assert probe[3] == ["ms-wikimedia"]
    order_ids = w.operations.list(f"dispatch/orders/{WIKIMEDIA}/")
    latest = OrderPointer.model_validate_json(w.operations.get(pointer_object(WIKIMEDIA)).data)
    order = WorkOrder.model_validate_json(w.operations.get(latest.object).data)
    assert order.probe and len(order.targets) == 5 and len(order_ids) == 5
    clock.moment = days[4].replace(hour=4, second=9)
    runtime, _, starter = dispatcher(w, clock)
    assert dispatch(runtime, starter)["source_states"] == {WIKIMEDIA: "healthy"}


def test_a_lost_collector_counts_as_a_failure_once_its_order_has_expired() -> None:
    clock = Clock(TUESDAY)
    w = world(clock, policy_for=real_policy)
    arm(w.operations, TUESDAY + timedelta(days=2))
    run_day(w, clock, [3])  # the collector never files a receipt
    name = f"dispatch/health/{WIKIMEDIA}.json"
    pending = SourceHealth.model_validate_json(w.operations.get(name).data)
    assert pending.pending_order_id is not None and pending.consecutive_failures == 0
    run_day(w, clock, [4])
    health = SourceHealth.model_validate_json(w.operations.get(name).data)
    assert health.pending_order_id is None and health.last_outcome is Outcome.MISSING
    assert health.consecutive_failures == 1


def test_hours_of_downtime_are_followed_by_one_ordinary_tick() -> None:
    clock = Clock(TUESDAY)
    w = world(clock, policy_for=real_policy)
    arm(w.operations, TUESDAY + timedelta(days=3))
    # Down from before the collection hour until the evening. One tick runs afterwards.
    started = run_day(w, clock, [19])
    assert started[19] == []  # nothing was due at 19:00, and nothing is made up for
    assert w.landing.list("dispatch/expected_targets/") == ()
    assert len(w.operations.list("dispatch/ticks/")) == 1  # no tick was invented for the gap
    # The missed day is inside the next day's window: no extra request, no storm.
    next_day = run_day(w, clock, [3, 4], day=TUESDAY + timedelta(days=1),
                       collector=receipt_for(w, Outcome.SUCCEEDED))  # fmt: skip
    assert next_day == {3: ["ms-wikimedia"], 4: ["ms-warehouse"]}
    version = policy_version(real_policy(WIKIMEDIA))
    during = (TUESDAY + timedelta(days=1)).replace(hour=3, minute=10)
    with pytest.raises(ClaimRefused) as caught:
        claim(w.operations, WIKIMEDIA, during, version)
    assert caught.value.reason == "order_already_completed"


def test_the_control_record_can_lower_a_limit_and_stops_everything_at_its_expiry() -> None:
    clock = Clock(TUESDAY)
    w = world(clock, policy_for=real_policy)
    arm(
        w.operations,
        TUESDAY.replace(hour=4),
        sources={WIKIMEDIA: SourceSwitch(enabled=True, max_targets_per_execution=5)},
    )
    assert run_day(w, clock, [3])[3] == ["ms-wikimedia"]
    order = claim(w.operations, WIKIMEDIA, clock.moment, policy_version(real_policy(WIKIMEDIA)))
    assert len(order.targets) == 5  # nine wait; the limit was lowered, not raised
    w.load_everything()
    rows = w.warehouse.all_rows(LEDGER)
    assert sorted(r["disposition"] for r in rows) == ["deferred_cap"] * 9 + ["planned"] * 5
    assert {r["work_order_id"] is None for r in rows if r["disposition"] != "planned"} == {True}
    # 04:00 is the expiry: collection stops though the scheduler is still firing.
    clock.moment = TUESDAY.replace(hour=4, second=9)
    runtime, _, starter = dispatcher(w, clock)
    assert dispatch(runtime, starter)["reason"] == "control_expired" and starter.started == []
    # A source the record does not enable is recorded as skipped and gets no order.
    arm(w.operations, TUESDAY + timedelta(days=3), sources={})
    other = world(clock, policy_for=real_policy)
    arm(other.operations, TUESDAY + timedelta(days=3), sources={})
    clock.moment = TUESDAY.replace(hour=3, second=9)
    runtime, _, starter = dispatcher(other, clock)
    summary = dispatch(runtime, starter)
    assert starter.started == [] and summary["work_orders"] == []
    assert summary["dispositions"] == {"skipped_disabled_by_control": 14}
    assert summary["source_states"] == {WIKIMEDIA: "disabled_by_control"}


def test_job_starts_and_daily_executions_are_capped() -> None:
    clock = Clock(TUESDAY)
    w = world(clock, policy_for=real_policy)
    arm(w.operations, TUESDAY + timedelta(days=3), max_job_starts_per_tick=0)
    assert run_day(w, clock, [3])[3] == []
    backlog = json.loads(w.operations.get(f"dispatch/backlog/{WIKIMEDIA}.json").data)
    assert len(backlog["items"]) == 14  # it waits; nothing is lost and nothing is started
    arm(w.operations, TUESDAY + timedelta(days=3), max_executions_per_day=1)
    started = run_day(w, clock, [4, 5, 6], collector=receipt_for(w, Outcome.SUCCEEDED))
    assert started == {4: ["ms-wikimedia"], 5: [], 6: []}  # one execution today, then none
    assert PRODUCTION.max_job_starts_per_tick == 6 and PRODUCTION.max_executions_per_day == 120
    with pytest.raises(ValueError):
        DispatchConfig(tick_minutes=5, sources={})  # a tick is never shorter than 15 minutes


def test_a_tick_is_configurable_for_a_faster_source_later() -> None:
    hourly = PRODUCTION
    quarter = DispatchConfig(tick_minutes=15, sources=PRODUCTION.sources)
    moment = MONDAY.replace(hour=9, minute=44, second=30)
    assert tick_of(moment, hourly) == MONDAY.replace(hour=9)
    assert tick_of(moment, quarter) == MONDAY.replace(hour=9, minute=30)


def test_a_job_that_cannot_be_started_fails_the_dispatch() -> None:
    clock = Clock(TUESDAY.replace(hour=3))
    w = world(clock, policy_for=real_policy)
    arm(w.operations, TUESDAY + timedelta(days=1))
    runtime, log, _ = dispatcher(w, clock)
    code = runlog.run_job(
        runtime, runlog.landed(runtime, "dispatch"),
        lambda: dispatch(runtime, Starter("ms-wikimedia")),
    )  # fmt: skip
    assert code == 1
    assert [e["target_job"] for e in log if e["event"] == "job_start_failed"] == ["ms-wikimedia"]


# --- the Wikimedia collector in steady state -----------------------------------------------


class Pageviews:
    """A stand-in source that answers every article with one value per requested day."""

    source = WIKIMEDIA
    mode = SourceMode.FETCH_HISTORY

    def __init__(self, fail: SourceErrorCode | None = None) -> None:
        self.windows: dict[str, int] = {}
        self.fail = fail

    def fetch(self, game: Any, identifier: Any, signal: Any, window: Any, now: datetime) -> Any:
        if self.fail is not None:
            raise SourceError(self.fail, "refused")
        days = (window.end - window.start).days + 1
        self.windows[identifier.value] = days
        return [
            Observation(
                canonical_game_id=game.canonical_game_id,
                source=WIKIMEDIA,
                metric=signal.metric,
                granularity=signal.granularity,
                period_start=window.start + timedelta(days=offset),
                value=100.0 + offset,
                status=ObservationStatus.OBSERVED,
                observed_at=now,
                source_timestamp=window.start + timedelta(days=offset),
                freshness=Freshness(period_end=window.end + timedelta(days=1), lag_hours=30.0),
            )
            for offset in range(days)
        ]


def collected(
    w: World, clock: Clock, run: str, **options: Any
) -> tuple[Mapping[str, Any], Pageviews]:
    w.now = clock
    runtime, _ = runtime_for(w, "ms-wikimedia", run_id=run)
    adapter = Pageviews()
    return collect_wikimedia(runtime, adapter, **options), adapter  # type: ignore[arg-type]


def test_history_is_filled_once_then_a_routine_run_asks_for_four_days() -> None:
    clock = Clock(TUESDAY.replace(hour=3, minute=5))
    w = world(clock, policy_for=real_policy)
    first, adapter = collected(w, clock, "run-1")
    assert set(adapter.windows.values()) == {90} and first["requests_sent"] == 14
    assert first["history_filled"] == 14 and first["rows_landed"] == 14 * 90
    assert first["window_days"] == 90 and first["routine_window_days"] == 4
    assert first["outcome"] == "succeeded"
    assert len(w.operations.list(f"{WIKIMEDIA}/bootstrap/")) == 14
    assert bootstrapped(w.operations, WIKIMEDIA, "en.wikipedia.org/Minecraft")

    clock.moment += timedelta(days=1)  # Wednesday
    second, adapter = collected(w, clock, "run-2")
    assert set(adapter.windows.values()) == {4} and second["requests_sent"] == 14
    assert second["rows_landed"] == 14 * 4 and second["history_filled"] == 0
    assert second["window_days"] == 4
    # The same day again: every article is marked done, so the source is asked nothing.
    third, adapter = collected(w, clock, "run-3")
    assert adapter.windows == {} and third["requests_sent"] == 0
    assert third["targets_skipped_already_done"] == 14

    clock.moment = MONDAY.replace(hour=3, minute=5) + timedelta(days=7)
    weekly, adapter = collected(w, clock, "run-4")
    assert set(adapter.windows.values()) == {14} and weekly["routine_window_days"] == 14
    assert WIKI_CAPS.steady_window_days == 4 and WIKI_CAPS.reconciliation_window_days == 14
    assert WIKI_CAPS.bootstrap_window_days == 90 and WIKI_CAPS.max_bootstraps_per_execution == 200


def test_a_run_fills_only_so_many_histories_and_the_rest_wait_for_a_later_run() -> None:
    clock = Clock(TUESDAY.replace(hour=3, minute=5))
    w = world(clock, policy_for=real_policy)
    caps = WIKI_CAPS.model_copy(update={"max_bootstraps_per_execution": 5})
    first, adapter = collected(w, clock, "run-1", caps=caps)
    assert first["history_filled"] == 5 and first["history_fill_deferred"] == 9
    assert first["requests_sent"] == 5 and first["outcome"] == "partial"
    assert set(adapter.windows.values()) == {90}
    second, _ = collected(w, clock, "run-2", caps=caps)
    assert second["history_filled"] == 5 and second["targets_skipped_already_done"] == 5
    third, _ = collected(w, clock, "run-3", caps=caps)
    assert third["history_filled"] == 4 and third["history_fill_deferred"] == 0
    assert len(w.operations.list(f"{WIKIMEDIA}/bootstrap/")) == 14


def test_a_fill_marker_is_written_only_after_the_batch_and_a_damaged_one_refills() -> None:
    clock = Clock(TUESDAY.replace(hour=3, minute=5))
    w = world(clock, policy_for=real_policy)
    w.now = clock
    runtime, _ = runtime_for(w, "ms-wikimedia")
    refusing = Pageviews(fail=SourceErrorCode.RATE_LIMITED)
    summary = collect_wikimedia(runtime, refusing)  # type: ignore[arg-type]
    assert summary["halted_by"] == "rate_limited" and summary["requests_sent"] == 1
    assert w.operations.list(f"{WIKIMEDIA}/bootstrap/") == ()  # nothing landed, nothing marked
    name = bootstrap_marker_name(WIKIMEDIA, "en.wikipedia.org/Minecraft")
    w.operations.create(name, b"{broken")
    assert not bootstrapped(w.operations, WIKIMEDIA, "en.wikipedia.org/Minecraft")
    other = bootstrap_marker_name(WIKIMEDIA, "en.wikipedia.org/Dota_2")
    assert other != name and name.startswith(f"{WIKIMEDIA}/bootstrap/")


def test_targets_filled_before_markers_existed_can_be_recorded_as_filled() -> None:
    clock = Clock(TUESDAY.replace(hour=3, minute=5))
    w = world(clock, policy_for=real_policy)
    ids = [t.source_native_id for t in bootstrap_targets(TUESDAY).targets]
    done = seed_bootstrap_markers(
        w.operations, WIKIMEDIA, ids, days=90, data_object="wikimedia_pageviews/x/part-0000.ndjson",
        content_sha256="a" * 64, collection_run_id="the-first-supervised-run",
        completed_at=TUESDAY,
    )  # fmt: skip
    assert done == 14
    summary, adapter = collected(w, clock, "run-1")
    assert set(adapter.windows.values()) == {4}  # no second 90-day fetch
    assert summary["history_filled"] == 0 and summary["rows_landed"] == 56
    seed_bootstrap_markers(
        w.operations, WIKIMEDIA, ids, days=90, data_object="other", content_sha256="b" * 64,
        collection_run_id="again", completed_at=TUESDAY,
    )  # fmt: skip
    marker = json.loads(w.operations.get(bootstrap_marker_name(WIKIMEDIA, ids[0])).data)
    assert marker["collection_run_id"] == "the-first-supervised-run"  # the first one stands


def ordered_runtime(w: World, clock: Clock, run: str) -> tuple[Any, list[dict[str, Any]]]:
    w.now = clock
    return runtime_for(w, "ms-wikimedia", run_id=run, environ={WORK_ORDERS_ENV: "required"})


def test_under_work_orders_a_collector_asks_nothing_without_one() -> None:
    clock = Clock(TUESDAY.replace(hour=3, minute=5))
    w = world(clock, policy_for=real_policy)
    runtime, log = ordered_runtime(w, clock, "run-1")
    built: list[str] = []

    def build() -> Any:
        built.append("client")
        return Pageviews()

    summary = collect_wikimedia_job(runtime, build)
    assert summary == {
        "outcome": "no_work_order", "reason": "no_order_pointer", "requests_sent": 0,
        "halted_by": None,
    }  # fmt: skip
    assert built == [] and w.landing.list("") == ()  # no source client was even built
    assert log[-1]["event"] == "no_work_order" and log[-1]["severity"] == "WARNING"
    # Without the setting it is the supervised collector it always was.
    plain, _ = runtime_for(w, "ms-wikimedia", run_id="run-2")
    assert collect_wikimedia_job(plain, build)["requests_sent"] == 14 and built == ["client"]


def test_a_collector_collects_exactly_its_order_once_and_files_a_receipt() -> None:
    clock = Clock(TUESDAY.replace(hour=3, second=9))
    w = world(clock, policy_for=real_policy)
    ids = [t.source_native_id for t in bootstrap_targets(TUESDAY).targets]
    seed_bootstrap_markers(
        w.operations, WIKIMEDIA, ids, days=90, data_object="x", content_sha256="a" * 64,
        collection_run_id="first", completed_at=TUESDAY,
    )  # fmt: skip
    arm(
        w.operations,
        TUESDAY + timedelta(days=2),
        sources={WIKIMEDIA: SourceSwitch(enabled=True, max_targets_per_execution=6)},
    )
    run_day(w, clock, [3])
    clock.moment += timedelta(minutes=2)
    runtime, _ = ordered_runtime(w, clock, "run-1")
    adapter = Pageviews()
    summary = collect_wikimedia_job(runtime, lambda: adapter)  # type: ignore[arg-type, return-value]
    assert summary["requests_sent"] == 6 == summary["targets_collected"]
    assert set(adapter.windows.values()) == {4} and summary["rows_landed"] == 24
    assert summary["slot"] == "2026-10-04T00:00:00+00:00" and summary["work_order"]
    receipt = read_receipt(w.operations, WIKIMEDIA, summary["work_order"])
    assert receipt is not None and receipt.outcome is Outcome.SUCCEEDED
    assert receipt.requests_sent == 6 and receipt.run_id == "run-1"
    # A platform retry of the same execution finds the receipt and asks nothing.
    retry, _ = ordered_runtime(w, clock, "run-2")
    again = Pageviews()
    assert collect_wikimedia_job(retry, lambda: again)["reason"] == "order_already_completed"  # type: ignore[arg-type, return-value]
    assert again.windows == {}
    # A crash before the receipt: the order is claimed again, and the markers stop it
    # asking for what it already landed. At-least-once, and safe.
    w.operations.delete(receipt_object(WIKIMEDIA, summary["work_order"]))
    crashed, _ = ordered_runtime(w, clock, "run-3")
    repeat = Pageviews()
    result = collect_wikimedia_job(crashed, lambda: repeat)  # type: ignore[arg-type, return-value]
    assert result["requests_sent"] == 0 and result["targets_skipped_already_done"] == 6
    # After its expiry the order is dead, whatever state it was left in.
    w.operations.delete(receipt_object(WIKIMEDIA, summary["work_order"]))
    clock.moment = TUESDAY.replace(hour=3, minute=50)
    stale, _ = ordered_runtime(w, clock, "run-4")
    assert collect_wikimedia_job(stale, Pageviews)["reason"] == "order_expired"  # type: ignore[arg-type]


def test_an_order_bounds_requests_and_a_rate_limit_is_reported_as_one() -> None:
    clock = Clock(TUESDAY.replace(hour=3, second=9))
    w = world(clock, policy_for=real_policy)
    arm(w.operations, TUESDAY + timedelta(days=2))
    run_day(w, clock, [3])
    version = policy_version(real_policy(WIKIMEDIA))
    order = claim(w.operations, WIKIMEDIA, clock.moment, version)
    tight = order.model_copy(update={"max_requests": 3})
    w.now = clock
    runtime, _ = runtime_for(w, "ms-wikimedia", run_id="run-1")
    adapter = Pageviews()
    summary = collect_wikimedia(runtime, adapter, tight)  # type: ignore[arg-type]
    assert summary["requests_sent"] == 3 and summary["halted_by"] == "request_limit"
    assert summary["targets_failed"] == 11 and summary["outcome"] == "partial"
    # Unknown targets are counted as failed, never invented.
    ghost = order.model_copy(
        update={
            "targets": (
                OrderTarget(
                    source_native_id="en.wikipedia.org/Nope", tier=Tier.LONG_TAIL, reason="r"
                ),
            )
        }
    )
    nothing = collect_wikimedia(runtime, Pageviews(), ghost)  # type: ignore[arg-type]
    assert nothing["requests_sent"] == 0 and nothing["targets_failed"] == 1

    clock.moment += timedelta(minutes=2)
    limited, _ = ordered_runtime(w, clock, "run-2")
    refusing = Pageviews(fail=SourceErrorCode.RATE_LIMITED)
    result = collect_wikimedia_job(limited, lambda: refusing)  # type: ignore[arg-type, return-value]
    assert result["requests_sent"] == 1 and result["halted_by"] == "rate_limited"
    receipt = read_receipt(w.operations, WIKIMEDIA, order.order_id)
    assert receipt is not None and receipt.outcome is Outcome.RATE_LIMITED
    # The next tick folds it in: the breaker opens at once.
    run_day(w, clock, [4])
    health = SourceHealth.model_validate_json(
        w.operations.get(f"dispatch/health/{WIKIMEDIA}.json").data
    )
    assert health.breaker is Breaker.OPEN and health.last_outcome is Outcome.RATE_LIMITED


# --- scale ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def quiet() -> dict[int, simulation.Result]:
    return {size: simulation.run(Scenario.QUIET, size) for size in (1_000, 5_000, 10_000, 50_000)}


def test_a_tick_does_work_in_proportion_to_what_is_due_not_to_the_catalogue(
    quiet: dict[int, simulation.Result],
) -> None:
    for size, result in quiet.items():
        assert result.catalogued == size and result.monitored == round(size * 0.3)
        assert result.scheduled == sum(result.eligible.values()) < size
        assert result.listings == 0  # nothing is ever listed
        assert result.uncleared_rows == 0 and SIM_UNCLEARED not in result.eligible
        assert result.job_starts_per_tick_max <= 6
        assert result.high_frequency <= 400
        # The objects a tick reads do not grow with the catalogue.
        assert result.object_reads_per_tick_max <= 32
        # Nor do the schedule entries it reads grow with it once the limits bind.
        assert result.entries_read_per_tick_max < result.scheduled
    small, large = quiet[1_000], quiet[50_000]
    assert large.catalogued == 50 * small.catalogued
    assert large.entries_read_per_tick_max < 10 * small.entries_read_per_tick_max
    assert large.object_reads_per_tick_max <= small.object_reads_per_tick_max + 4
    # Requests are bounded by the sources' limits, whatever the breadth.
    assert large.requests[WIKIMEDIA] == 10_000  # the day's ceiling, exactly
    assert large.requests[SIM_SAMPLED] <= 24 * 600
    assert large.executions["ms-wikimedia"] <= 24 and large.backlog_max <= 6000
    assert large.slowest_tick_seconds < 10 and large.peak_memory_mb < 400


def test_the_same_simulation_gives_the_same_counts() -> None:
    first = simulation.run(Scenario.EVENTS, 5_000)
    second = simulation.run(Scenario.EVENTS, 5_000)
    volatile = {"runtime_seconds", "slowest_tick_seconds", "peak_memory_mb"}
    assert first.model_dump(exclude=volatile) == second.model_dump(exclude=volatile)


def test_many_events_at_once_fill_the_tiers_and_cannot_launch_more_jobs() -> None:
    calm = simulation.run(Scenario.QUIET, 10_000)
    busy = simulation.run(Scenario.CAPACITY, 10_000)  # an event for every sampled game
    assert busy.tiers["T0"] == 25 and busy.tiers["T1"] == 75 and busy.tiers["T2"] == 300
    assert busy.tiers["T3"] == 1000 and busy.high_frequency == 400
    assert sum(busy.executions.values()) == sum(calm.executions.values())  # not one more job
    assert busy.job_starts_per_tick_max == calm.job_starts_per_tick_max <= 6
    assert busy.requests[SIM_SAMPLED] <= 24 * 600  # the execution limit, not the event count
    assert busy.deferred > 0 and busy.backlog_max <= 3000 and busy.expired > 0
    events = simulation.run(Scenario.EVENTS, 10_000)
    assert events.tiers["T0"] == 25 and events.high_frequency <= 400


def test_an_outage_and_a_rate_limit_skip_work_and_recover_without_a_storm() -> None:
    calm = simulation.run(Scenario.QUIET, 5_000)
    outage = simulation.run(Scenario.OUTAGE, 5_000)
    limited = simulation.run(Scenario.RATE_LIMIT, 5_000)
    for trouble in (outage, limited):
        assert trouble.skipped > 0 and trouble.requests[SIM_SAMPLED] < calm.requests[SIM_SAMPLED]
        assert (
            trouble.executions[simulation.SIM_COLLECTOR] < calm.executions[simulation.SIM_COLLECTOR]
        )
        assert trouble.backlog_max <= calm.backlog_max + 600
        assert (
            trouble.requests[WIKIMEDIA] == calm.requests[WIKIMEDIA]
        )  # the other source is untouched
    assert limited.skipped < outage.skipped  # one refusal, a short cool-down, then a probe


def test_after_six_hours_offline_the_next_tick_is_an_ordinary_tick() -> None:
    calm = simulation.run(Scenario.QUIET, 10_000, keep_records=True)
    gap = simulation.run(Scenario.AFTER_DOWNTIME, 10_000, keep_records=True)
    assert gap.ticks == calm.ticks - 6
    assert gap.first_tick_after_gap_jobs is not None and gap.first_tick_after_gap_jobs <= 6
    # It plans no more than two collectors' worth: nothing is made up for the gap.
    assert gap.first_tick_after_gap_planned is not None
    assert gap.first_tick_after_gap_planned <= 2 * 600
    assert gap.requests[SIM_SAMPLED] < calm.requests[SIM_SAMPLED]
    assert max(len(r.jobs) for r in gap.records) <= 6
    noon = next(r for r in gap.records if r.tick.startswith("2026-11-03T12:00"))
    ordinary = next(r for r in calm.records if r.tick.startswith("2026-11-03T12:00"))
    assert noon.partitions_read == ordinary.partitions_read
    assert noon.object_reads <= ordinary.object_reads + 2


def test_a_huge_catalogue_with_a_small_eligible_subset_costs_like_the_subset() -> None:
    wide = simulation.run(Scenario.SMALL_ELIGIBLE, 50_000)
    narrow = simulation.run(Scenario.SMALL_ELIGIBLE, 1_000)
    assert wide.catalogued == 50_000 and wide.monitored == 500 and wide.scheduled == 700
    same = {"requests", "executions", "ledger_rows", "work_orders", "due_per_tick_max",
            "entries_read_per_tick_max", "object_reads_per_tick_max", "tiers"}  # fmt: skip
    assert wide.model_dump(include=same) == narrow.model_dump(include=same)
    assert wide.projection.usd_without_allowances == narrow.projection.usd_without_allowances


# --- cost ----------------------------------------------------------------------------------


def test_the_planned_configuration_projects_far_inside_the_budget() -> None:
    planned = project(simulation.current_production())
    assert planned.usd_without_allowances < 2 and planned.usd_with_allowances < 0.5
    assert planned.verdict == "within the preferred range"
    assert planned.query_gb < 5  # one daily build: about 4 GB billed a month
    assert planned.vcpu_seconds < 240_000 / 4


def test_a_configuration_above_the_review_line_is_not_activation_ready() -> None:
    heavy = Workload(
        label="every tier full at one request a second, four ticks an hour",
        dispatcher_executions=96,
        collector_executions={"collector": 96},
        collector_requests={"collector": 24_200 * 4},
        warehouse_daily_builds=1,
        warehouse_hourly_builds=95,
        sample_rows=24_200 * 4,
    )
    result = project(heavy)
    assert result.usd_without_allowances > REVIEW_USD
    assert result.verdict.startswith("NOT activation-ready")
    # Allowances are reported apart, so a decision never rests on them silently.
    assert result.usd_with_allowances < result.usd_without_allowances
    assert set(result.breakdown) == {"compute", "queries", "storage", "registry"}


def test_no_simulated_scale_at_an_hourly_tick_is_above_the_review_line(
    quiet: dict[int, simulation.Result],
) -> None:
    for size, result in quiet.items():
        assert result.projection.usd_without_allowances < REVIEW_USD, size


# --- image hardening -----------------------------------------------------------------------


def test_the_image_carries_no_setuid_or_setgid_program() -> None:
    from pathlib import Path

    dockerfile = (Path(__file__).parent.parent / "Dockerfile").read_text()
    strip = "RUN find / -xdev -type f -perm /6000 -exec chmod a-s {} +"
    assert dockerfile.count(strip) == 1
    # In the final stage, as root, after the last package change and before the user drops.
    final = dockerfile[dockerfile.rindex("\nFROM ") :]
    assert final.index("apt-get install") < final.index(strip) < final.index("USER 10001:10001")
    assert "chmod u+s" not in dockerfile and "chmod +s" not in dockerfile
    assert "python:3.12-slim-bookworm@sha256:" in final  # the base is unchanged in C2-A
