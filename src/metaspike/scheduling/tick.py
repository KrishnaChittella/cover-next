"""One dispatcher tick: read the switch, decide what is due, bound it, record it, start it.

The order is fixed and every step is safe to repeat:

    1. the control record. Absent, malformed, disabled or expired: nothing happens.
    2. the tick's lease. If it exists this tick was already dispatched.
    3. the tick's plan. Computed once and stored under the tick's name; a second run
       of the same tick reads the stored plan instead of computing another.
    4. apply the plan: the expected-target ledger, the work orders, health, backlog,
       counters. Each is written to the value the plan holds, so applying it twice
       changes nothing.
    5. the lease, then the job starts.

A tick plans the current tick only. There is no loop over ticks that were missed:
after any outage the next tick is an ordinary tick with the same limits.

Only what can be due is read. The catalogue is never opened here; the targets
document is read once a day to regenerate the schedule.
"""

import contextlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from pydantic import AwareDatetime, Field, ValidationError

from metaspike.domain.model import FrozenModel
from metaspike.jobs.targets import TargetsDocument
from metaspike.pipeline.normalize import latest_due_period
from metaspike.scheduling.config import RETRY_ALLOWANCE, DispatchConfig, SourceCaps
from metaspike.scheduling.control import ControlRecord, Pin, load_control
from metaspike.scheduling.health import (
    HealthState,
    Outcome,
    SourceHealth,
    apply_outcome,
    gate,
    state_of,
)
from metaspike.scheduling.planner import Disposition, DueItem, SourcePlan, plan_source
from metaspike.scheduling.schedule import (
    ScheduleEntry,
    ScheduleManifest,
    ScheduleStore,
    build_schedule,
    due_partitions,
)
from metaspike.scheduling.tiers import INTERVAL_MINUTES, Proposal, Tier
from metaspike.scheduling.workorder import OrderTarget, WorkOrder, issue, order_id, read_receipt
from metaspike.sources.base import SourceMode
from metaspike.storage.base import StorageCorruptionError
from metaspike.warehouse.landing import timestamp
from metaspike.warehouse.objects import ObjectNotFound, ObjectStore, PreconditionFailed

Tiering = tuple[Mapping[tuple[str, str], Tier], Mapping[tuple[str, str], Proposal]]


class JobStarter(Protocol):
    def start(self, job: str) -> str: ...


class WarehouseState(FrozenModel):
    # The tick on which a collector was started and nothing has loaded its work yet.
    due_since: AwareDatetime | None = None
    last_started: AwareDatetime | None = None


class Counters(FrozenModel):
    day: str
    requests: Mapping[str, int] = Field(default_factory=dict)
    executions: int = 0


class Backlog(FrozenModel):
    items: tuple[DueItem, ...] = ()


class TickPlan(FrozenModel):
    tick: AwareDatetime
    name: str
    created_at: AwareDatetime
    schedule_version: str
    sources: tuple[SourcePlan, ...]
    states: Mapping[str, HealthState]
    orders: tuple[WorkOrder, ...]
    health: tuple[SourceHealth, ...]
    jobs: tuple[str, ...]
    warehouse: WarehouseState
    counters: Counters
    partitions_read: int
    schedule_regenerated: bool


@dataclass(frozen=True)
class TickEnvironment:
    store: ObjectStore
    land: Callable[[str, datetime, Sequence[Mapping[str, Any]]], None]
    starter: JobStarter
    now: Callable[[], datetime]
    config: DispatchConfig
    cleared: Callable[[str], bool]
    policy_version: Callable[[str], str]
    publication_lag: Callable[[str], timedelta]
    load_targets: Callable[[], TargetsDocument]
    log: Callable[..., None]
    credential_present: Callable[[str], bool] = lambda source: True
    # The shadow rule's output for a targets document: tiers and proposals. Production
    # supplies none; the proposals are recorded and the tiers are used only when the
    # configuration says so, which production's does not.
    tiering: Callable[[TargetsDocument], Tiering] | None = None


def tick_of(now: datetime, config: DispatchConfig) -> datetime:
    moment = now.astimezone(UTC)
    minutes = (moment.hour * 60 + moment.minute) // config.tick_minutes * config.tick_minutes
    return moment.replace(hour=minutes // 60, minute=minutes % 60, second=0, microsecond=0)


def tick_name(tick: datetime) -> str:
    return f"tick-{tick:%Y%m%dT%H%MZ}"


def _read[T: FrozenModel](store: ObjectStore, name: str, model: type[T]) -> T | None:
    try:
        data = store.get(name).data
    except ObjectNotFound:
        return None
    try:
        return model.model_validate_json(data)
    except ValidationError:
        raise StorageCorruptionError(f"{name} is unreadable") from None


def _put(store: ObjectStore, name: str, value: FrozenModel) -> None:
    """Set an object to a value. Repeating it, or losing a race to the same value, is fine."""
    body = value.model_dump_json().encode("utf-8")
    for _ in range(5):
        try:
            current = store.get(name)
        except ObjectNotFound:
            try:
                store.create(name, body)
                return
            except PreconditionFailed:
                continue
        if current.data == body:
            return
        try:
            store.replace(name, body, current.generation)
            return
        except PreconditionFailed:
            continue
    raise RuntimeError(f"{name} could not be written")


def _schedule(
    env: TickEnvironment, tick: datetime, record: ControlRecord
) -> tuple[ScheduleManifest, bool]:
    """The schedule in force, regenerated once a day or when there is none."""
    store = ScheduleStore(env.store)
    manifest = store.current()
    daily = tick.minute == 0 and tick.hour == env.config.schedule_hour
    if manifest is not None and not (daily and manifest.generated_at < tick):
        return manifest, False
    document = env.load_targets()
    monitored = {t.canonical_game_id or t.source_native_id for t in document.targets}
    catalogued = document.catalogued if document.catalogued is not None else len(monitored)
    tiers, proposals = env.tiering(document) if env.tiering is not None else ({}, {})
    built, partitions = build_schedule(
        document.targets,
        targets_version=document.version,
        catalogued=catalogued,
        cleared=env.cleared,
        config=env.config,
        at=tick,
        tiers=tiers if env.config.apply_shadow_tiers else None,
        proposals=proposals,
    )
    if manifest is not None and manifest.version == built.version:
        return manifest, False
    store.publish(built, partitions)
    return built, True


def _item(entry: ScheduleEntry, tick: datetime, env: TickEnvironment, reason: str) -> DueItem:
    if entry.mode is SourceMode.FETCH_HISTORY:
        slot = latest_due_period(entry.granularity, tick, env.publication_lag(entry.source))
        life = timedelta(days=1)
    else:
        slot = entry.granularity.floor(tick)
        life = timedelta(minutes=INTERVAL_MINUTES[entry.tier])
    return DueItem(
        source=entry.source,
        metric=entry.metric,
        granularity=entry.granularity,
        mode=entry.mode,
        source_native_id=entry.source_native_id,
        canonical_game_id=entry.canonical_game_id,
        tier=entry.tier,
        slot=slot,
        due_at=tick,
        expires_at=tick + life,
        reason=reason,
    )


def _pinned(pin: Pin, tick: datetime, env: TickEnvironment) -> DueItem | None:
    """A pin's work on this tick, if its tier is due now. Sampled sources only."""
    if pin.mode is not SourceMode.SAMPLE_NOW:
        return None
    if (tick.hour * 60 + tick.minute) % INTERVAL_MINUTES[pin.tier] != 0:
        return None
    entry = ScheduleEntry(
        source=pin.source,
        metric=pin.metric,
        granularity=pin.granularity,
        mode=pin.mode,
        source_native_id=pin.source_native_id,
        tier=pin.tier,
        reason="pin",
    )
    return _item(entry, tick, env, f"pin:{pin.reason}")


def _fold(env: TickEnvironment, source: str, caps: SourceCaps, now: datetime) -> SourceHealth:
    """A source's health, with the outcome of its last order folded in if it is known."""
    health = _read(env.store, f"dispatch/health/{source}.json", SourceHealth)
    if health is None:
        return SourceHealth(source=source)
    if health.pending_order_id is None:
        return health
    receipt = read_receipt(env.store, source, health.pending_order_id)
    if receipt is not None:
        return apply_outcome(health, receipt.outcome, receipt.completed_at, caps)
    if health.pending_expires_at is not None and health.pending_expires_at <= now:
        return apply_outcome(health, Outcome.MISSING, now, caps)
    return health


def _plan(env: TickEnvironment, tick: datetime, record: ControlRecord, now: datetime) -> TickPlan:
    config = env.config
    name = tick_name(tick)
    manifest, regenerated = _schedule(env, tick, record)
    schedules = ScheduleStore(env.store)
    day = f"{tick:%Y-%m-%d}"
    counters = _read(env.store, f"dispatch/counters/{day}.json", Counters) or Counters(day=day)
    warehouse = _read(env.store, "dispatch/warehouse.json", WarehouseState) or WarehouseState()

    starts_left = config.max_job_starts_per_tick
    if record.max_job_starts_per_tick is not None:
        starts_left = min(starts_left, record.max_job_starts_per_tick)
    per_day = config.max_executions_per_day
    if record.max_executions_per_day is not None:
        per_day = min(per_day, record.max_executions_per_day)
    starts_left = max(0, min(starts_left, per_day - counters.executions))

    pins = record.active_pins(now, config.max_pins)
    plans: list[SourcePlan] = []
    orders: list[WorkOrder] = []
    healths: list[SourceHealth] = []
    states: dict[str, HealthState] = {}
    jobs: list[str] = []
    requests = dict(counters.requests)
    read = 0
    for source in sorted(config.sources):
        caps = config.sources[source]
        cleared = env.cleared(source)
        credential = env.credential_present(source)
        enabled = record.source_enabled(source)
        health = _fold(env, source, caps, now)
        if not cleared or not credential:
            # Never planned and never recorded as intent: not even a ledger row.
            states[source] = state_of(
                health, now, caps, policy_cleared=cleared, credential_present=credential
            )
            healths.append(health)
            continue
        fresh: dict[str, DueItem] = {}
        for key in due_partitions(manifest, source, tick, config):
            read += 1
            for entry in schedules.partition(manifest.version, key):
                fresh[entry.source_native_id] = _item(entry, tick, env, entry.reason)
        for pin in pins:
            if pin.source == source and (pinned := _pinned(pin, tick, env)) is not None:
                fresh[pin.source_native_id] = pinned
        backlog = _read(env.store, f"dispatch/backlog/{source}.json", Backlog) or Backlog()
        way, health = gate(health, now)
        switch = record.sources.get(source)
        per_day_requests = caps.max_requests_per_day
        limit = None
        if switch is not None:
            if switch.max_requests_per_day is not None:
                per_day_requests = min(per_day_requests, switch.max_requests_per_day)
            limit = switch.max_targets_per_execution
        left = max(0, per_day_requests - requests.get(source, 0))
        plan = plan_source(
            source,
            fresh=tuple(fresh.values()),
            backlog=backlog.items,
            caps=caps,
            now=now,
            gate=way,
            enabled=enabled,
            requests_left_today=left,
            targets_limit=limit,
        )
        if plan.planned and starts_left <= 0:
            # No job may be started for it on this tick, so it waits like any other.
            # Those already waiting stay at the front, where `carried` counts them.
            held = (*plan.deferred[: plan.carried], *plan.planned, *plan.deferred[plan.carried :])
            kept, over = held[: caps.max_backlog], held[caps.max_backlog :]
            plan = plan.model_copy(
                update={
                    "planned": (),
                    "deferred": kept,
                    "overflowed": (*plan.overflowed, *over),
                    "deferred_as": Disposition.DEFERRED_CAP,
                }
            )
        if plan.planned:
            first = plan.planned[0]
            window = None
            if first.mode is SourceMode.FETCH_HISTORY:
                weekly = tick.weekday() == config.reconciliation_weekday
                window = caps.reconciliation_window_days if weekly else caps.steady_window_days
            identifier = order_id(source, tick, manifest.version)
            budget = min(
                caps.max_requests_per_execution, int(len(plan.planned) * RETRY_ALLOWANCE) + 1
            )
            expires = tick + timedelta(minutes=caps.order_ttl_minutes)
            orders.append(
                WorkOrder(
                    order_id=identifier,
                    source=source,
                    metric=first.metric,
                    granularity=first.granularity,
                    mode=first.mode,
                    slot=first.slot,
                    window_days=window,
                    tick=tick,
                    created_at=now,
                    expires_at=expires,
                    schedule_version=manifest.version,
                    policy_version=env.policy_version(source),
                    targets=tuple(
                        OrderTarget(
                            source_native_id=item.source_native_id,
                            canonical_game_id=item.canonical_game_id,
                            tier=item.tier,
                            reason=item.reason,
                        )
                        for item in plan.planned
                    ),
                    max_targets=len(plan.planned),
                    max_requests=budget,
                    probe=plan.probe,
                )
            )
            requests[source] = requests.get(source, 0) + len(plan.planned)
            jobs.append(caps.collector_job)
            starts_left -= 1
            health = health.model_copy(
                update={"pending_order_id": identifier, "pending_expires_at": expires}
            )
        plans.append(plan)
        healths.append(health)
        states[source] = state_of(health, now, caps, control_enabled=enabled)

    daily = tick.minute == 0 and tick.hour == config.warehouse_daily_hour
    already_today = warehouse.last_started is not None and warehouse.last_started >= tick.replace(
        hour=0, minute=0
    )
    wanted = (warehouse.due_since is not None and warehouse.due_since < tick) or (
        daily and not already_today
    )
    if wanted and starts_left > 0:
        jobs.append(config.warehouse_job)
        warehouse = WarehouseState(due_since=None, last_started=tick)
    if orders and warehouse.due_since is None:
        warehouse = warehouse.model_copy(update={"due_since": tick})

    return TickPlan(
        tick=tick,
        name=name,
        created_at=now,
        schedule_version=manifest.version,
        sources=tuple(plans),
        states=states,
        orders=tuple(orders),
        health=tuple(healths),
        jobs=tuple(jobs),
        warehouse=warehouse,
        counters=Counters(
            day=day,
            requests=dict(sorted(requests.items())),
            executions=counters.executions + len(jobs),
        ),
        partitions_read=read,
        schedule_regenerated=regenerated,
    )


def ledger_rows(plan: TickPlan, policy_version: Callable[[str], str]) -> list[dict[str, Any]]:
    """One row per target that was due on this tick, with what was done about it.

    A slot that is later found without a sample is `NOT_SAMPLED` in the timeline view,
    derived from these rows. That state is never written here or anywhere else.
    """
    order_of = {order.source: order.order_id for order in plan.orders}
    rows: list[dict[str, Any]] = []
    for source_plan in plan.sources:
        status = f"cleared:{policy_version(source_plan.source)}"
        for item, disposition in source_plan.dispositions():
            planned = disposition is Disposition.PLANNED
            rows.append(
                {
                    "source": item.source,
                    "metric": item.metric,
                    "granularity": item.granularity.value,
                    "mode": item.mode.value,
                    "source_native_id": item.source_native_id,
                    "slot_start": timestamp(item.slot),
                    "schedule_version": plan.schedule_version,
                    "dispatch_run_id": plan.name,
                    "recorded_at": timestamp(plan.created_at),
                    "tier": int(item.tier),
                    "due_at": timestamp(item.due_at),
                    "due_reason": item.reason,
                    "policy_status": status,
                    "disposition": disposition.value,
                    "work_order_id": order_of.get(item.source) if planned else None,
                }
            )
    return rows


def _apply(env: TickEnvironment, plan: TickPlan) -> None:
    rows = ledger_rows(plan, env.policy_version)
    if rows:
        env.land(plan.name, plan.created_at, rows)
    for order in plan.orders:
        issue(env.store, order)
    for health in plan.health:
        _put(env.store, f"dispatch/health/{health.source}.json", health)
    for source_plan in plan.sources:
        backlog = Backlog(items=source_plan.deferred)
        _put(env.store, f"dispatch/backlog/{source_plan.source}.json", backlog)
    _put(env.store, f"dispatch/counters/{plan.counters.day}.json", plan.counters)
    _put(env.store, "dispatch/warehouse.json", plan.warehouse)


def run_tick(env: TickEnvironment) -> Mapping[str, Any]:
    now = env.now()
    tick = tick_of(now, env.config)
    name = tick_name(tick)
    control = load_control(env.store, now)
    if control.record is None:
        env.log("tick_refused", severity="WARNING", tick=tick.isoformat(), reason=control.refusal)
        return {"outcome": "refused", "reason": control.refusal, "tick": tick.isoformat()}
    lease = f"dispatch/ticks/{name}.json"
    with contextlib.suppress(ObjectNotFound):
        env.store.get(lease)
        env.log("tick_already_dispatched", tick=tick.isoformat())
        return {"outcome": "already_dispatched", "tick": tick.isoformat()}

    stored = f"dispatch/plans/{name}.json"
    plan = _read(env.store, stored, TickPlan)
    if plan is None:
        plan = _plan(env, tick, control.record, now)
        try:
            env.store.create(stored, plan.model_dump_json().encode("utf-8"))
        except PreconditionFailed:
            plan = _read(env.store, stored, TickPlan) or plan
    _apply(env, plan)
    try:
        env.store.create(lease, b"{}")
    except PreconditionFailed:
        env.log("tick_already_dispatched", tick=tick.isoformat())
        return {"outcome": "already_dispatched", "tick": tick.isoformat()}

    started: dict[str, str] = {}
    failed: dict[str, str] = {}
    for job in plan.jobs:
        try:
            started[job] = env.starter.start(job)
        except Exception as error:
            failed[job] = str(error)
            env.log("job_start_failed", severity="ERROR", target_job=job, reason=str(error))
    counts: dict[str, int] = {}
    for source_plan in plan.sources:
        for _, disposition in source_plan.dispositions():
            counts[disposition.value] = counts.get(disposition.value, 0) + 1
    if failed:
        raise RuntimeError(f"could not start: {', '.join(sorted(failed))}")
    return {
        "tick": tick.isoformat(),
        "schedule_version": plan.schedule_version,
        "schedule_regenerated": plan.schedule_regenerated,
        "partitions_read": plan.partitions_read,
        "expected_targets": sum(counts.values()),
        "dispositions": dict(sorted(counts.items())),
        "work_orders": [order.order_id for order in plan.orders],
        "jobs_started": list(plan.jobs),
        "source_states": {k: v.value for k, v in sorted(plan.states.items())},
        "executions_today": plan.counters.executions,
    }
