"""Scale simulation: a day of ticks over a synthetic catalogue, through the real tick.

Nothing here is a model of the dispatcher. It runs `run_tick` itself against an
in-memory store, with a synthetic targets document and a stand-in collector that
claims the work orders the tick wrote and files a receipt. What is counted is what
the real code did: objects read, schedule entries read, ledger rows, work orders,
job starts, requests, backlog.

Two sources exist only here. `sim_sampled` stands for some future sampled source so
that the tiers can be exercised; nothing about it is an approved request budget.
`sim_uncleared` is never cleared, and the simulation proves it gets no work at all.
"""

import time
import tracemalloc
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from metaspike.domain.model import FrozenModel
from metaspike.domain.timeseries import Granularity
from metaspike.jobs.targets import Target, TargetsDocument
from metaspike.scheduling.config import DispatchConfig, SourceCaps
from metaspike.scheduling.control import CONTROL_OBJECT, ControlRecord, SourceSwitch
from metaspike.scheduling.health import Outcome
from metaspike.scheduling.projection import Projection, Workload, project
from metaspike.scheduling.schedule import ScheduleStore, _digest
from metaspike.scheduling.tick import Backlog, TickEnvironment, Tiering, run_tick
from metaspike.scheduling.tiers import Attention, Proposal, Tier, fit_to_capacity, propose
from metaspike.scheduling.workorder import ClaimRefused, OrderReceipt, claim, complete
from metaspike.sources.base import SourceMode
from metaspike.sources.wikimedia import SOURCE_ID as WIKIMEDIA
from metaspike.warehouse.objects import (
    MemoryObjectStore,
    ObjectNotFound,
    ObjectStore,
    StoredObject,
)

SIM_SAMPLED = "sim_sampled"
SIM_UNCLEARED = "sim_uncleared"
SIM_COLLECTOR = "sim-sampled-collector"
START = datetime(2026, 11, 3, 0, 0, tzinfo=UTC)  # a Tuesday: no weekly reconciliation


class Scenario(StrEnum):
    QUIET = "A quiet day"
    EVENTS = "B many events at once"
    OUTAGE = "C source outage"
    RATE_LIMIT = "D rate limiting"
    OFFLINE = "E dispatcher offline for six hours"
    AFTER_DOWNTIME = "F first tick after downtime"
    CAPACITY = "G tier capacity exceeded"
    SMALL_ELIGIBLE = "H small eligible subset"


class Shape(FrozenModel):
    """Who is in the synthetic catalogue.

    The first `monitored` entities are monitored. Of those, the first `wikimedia` have
    an article, the last `sampled` have a sampled-source id, and the first `uncleared`
    have an id at a source that is never cleared. Everything else is catalogued only.
    """

    catalogued: int
    monitored: int
    wikimedia: int
    sampled: int
    uncleared: int

    @classmethod
    def typical(cls, catalogued: int) -> "Shape":
        monitored = max(14, round(catalogued * 0.3))
        return cls(
            catalogued=catalogued,
            monitored=monitored,
            wikimedia=round(monitored * 0.8),
            sampled=round(monitored * 0.6),
            uncleared=round(monitored * 0.5),
        )

    @classmethod
    def small_eligible(cls, catalogued: int) -> "Shape":
        return cls(catalogued=catalogued, monitored=500, wikimedia=400, sampled=300, uncleared=250)


def synthetic_targets(shape: Shape, at: datetime = START) -> TargetsDocument:
    targets: list[Target] = []
    for index in range(shape.monitored):
        game = f"game-{index:06d}"
        if index < shape.wikimedia:
            targets.append(
                Target(
                    source=WIKIMEDIA,
                    metric="pageviews",
                    granularity=Granularity.DAILY,
                    mode=SourceMode.FETCH_HISTORY,
                    source_native_id=f"en.wikipedia.org/Synthetic_{index:06d}",
                    canonical_game_id=game,
                )
            )
        has = {
            SIM_SAMPLED: index >= shape.monitored - shape.sampled,
            SIM_UNCLEARED: index < shape.uncleared,
        }
        for source, present in has.items():
            if present:
                targets.append(
                    Target(
                        source=source,
                        metric="concurrent_players",
                        granularity=Granularity.FIFTEEN_MINUTES,
                        mode=SourceMode.SAMPLE_NOW,
                        source_native_id=str(100_000 + index),
                        canonical_game_id=game,
                    )
                )
    return TargetsDocument(
        version=f"sim-{shape.catalogued}-{shape.monitored}",
        generated_at=at,
        targets=tuple(targets),
        catalogued=shape.catalogued,
    )


def synthetic_attention(native_id: str, events: int, index: int) -> Attention:
    """A deterministic spread: mostly quiet, a few elevated, `events` with an event today."""
    if index < events:
        return Attention(latest=9000.0, trailing_median=1500.0, days_since_event=0)
    roll = _digest("attention:" + native_id) % 1000
    if roll < 2:
        return Attention(latest=12_000.0, trailing_median=2_000.0)
    if roll < 8:
        return Attention(latest=4_000.0, trailing_median=1_200.0)
    if roll < 40:
        return Attention(latest=1_100.0, trailing_median=500.0)
    if roll < 160:
        return Attention(latest=700.0, trailing_median=500.0)
    if roll < 200:
        return Attention()  # no history: unknown, which is not zero and never escalates
    return Attention(latest=400.0, trailing_median=420.0)


def tiering_for(events: int, caps: Mapping[Tier, int]) -> Any:
    def tiering(document: TargetsDocument) -> Tiering:
        proposals: dict[str, Proposal] = {}
        sampled = [t for t in document.targets if t.source == SIM_SAMPLED]
        for index, target in enumerate(sampled):
            proposals[target.source_native_id] = propose(
                synthetic_attention(target.source_native_id, events, index)
            )
        fitted = fit_to_capacity(proposals, caps)
        return (
            {(SIM_SAMPLED, key): tier for key, tier in fitted.items()},
            {(SIM_SAMPLED, key): proposal for key, proposal in proposals.items()},
        )

    return tiering


class CountingStore:
    """An object store that counts what is asked of it."""

    def __init__(self, inner: ObjectStore) -> None:
        self._inner = inner
        self.reads = 0
        self.writes = 0
        self.bytes_read = 0
        self.lists = 0

    def create(self, name: str, data: bytes, content_type: str = "application/json") -> int:
        self.writes += 1
        return self._inner.create(name, data, content_type)

    def replace(
        self, name: str, data: bytes, if_generation: int, content_type: str = "application/json"
    ) -> int:
        self.writes += 1
        return self._inner.replace(name, data, if_generation, content_type)

    def get(self, name: str) -> StoredObject:
        self.reads += 1
        found = self._inner.get(name)
        self.bytes_read += len(found.data)
        return found

    def list(self, prefix: str) -> tuple[str, ...]:
        self.lists += 1
        return self._inner.list(prefix)

    def delete(self, name: str) -> None:
        self._inner.delete(name)


@dataclass
class Starter:
    started: list[str] = field(default_factory=list)

    def start(self, job: str) -> str:
        self.started.append(job)
        return f"executions/{job}-{len(self.started)}"


def simulation_config(tick_minutes: int = 60) -> DispatchConfig:
    return DispatchConfig(
        tick_minutes=tick_minutes,
        apply_shadow_tiers=True,  # simulation only: production records proposals and ignores them
        sources={
            WIKIMEDIA: SourceCaps(collector_job="ms-wikimedia", max_requests_per_day=10_000),
            # A stand-in. Its ceiling is a simulation value, not an approved budget.
            SIM_SAMPLED: SourceCaps(
                collector_job=SIM_COLLECTOR,
                max_requests_per_day=25_000,
                order_ttl_minutes=max(5, tick_minutes - 5),
            ),
            SIM_UNCLEARED: SourceCaps(collector_job="sim-uncleared", max_requests_per_day=25_000),
        },
    )


class TickRecord(FrozenModel):
    tick: str
    outcome: str
    due: int = 0
    planned: int = 0
    deferred: int = 0
    skipped: int = 0
    expired: int = 0
    partitions_read: int = 0
    object_reads: int = 0
    jobs: tuple[str, ...] = ()
    seconds: float = 0.0


class Result(FrozenModel):
    scenario: str
    tick_minutes: int
    catalogued: int
    monitored: int
    eligible: Mapping[str, int]
    scheduled: int
    high_frequency: int
    tiers: Mapping[str, int]
    ticks: int
    due_per_tick_max: int
    due_per_tick_mean: float
    entries_read_per_tick_max: int
    object_reads_per_tick_max: int
    listings: int
    ledger_rows: int
    work_orders: int
    executions: Mapping[str, int]
    job_starts_per_tick_max: int
    requests: Mapping[str, int]
    planned: int
    deferred: int
    skipped: int
    expired: int
    backlog_max: int
    backlog_end: int
    uncleared_rows: int
    first_tick_after_gap_jobs: int | None = None
    first_tick_after_gap_planned: int | None = None
    runtime_seconds: float
    slowest_tick_seconds: float
    peak_memory_mb: float
    projection: Projection
    records: tuple[TickRecord, ...] = ()


def run(
    scenario: Scenario,
    catalogued: int,
    *,
    tick_minutes: int = 60,
    hours: int = 24,
    keep_records: bool = False,
) -> Result:
    """One simulated day. Deterministic: the same arguments give the same counts."""
    shape = (
        Shape.small_eligible(catalogued)
        if scenario is Scenario.SMALL_ELIGIBLE
        else Shape.typical(catalogued)
    )
    config = simulation_config(tick_minutes)
    events = {
        Scenario.EVENTS: max(50, shape.sampled // 10),
        Scenario.CAPACITY: shape.sampled,
    }.get(scenario, 0)
    document = synthetic_targets(shape)
    inner = MemoryObjectStore()
    store = CountingStore(inner)

    def waiting() -> int:
        total = 0
        for source in config.sources:
            try:
                data = inner.get(f"dispatch/backlog/{source}.json").data
            except ObjectNotFound:
                continue
            total += len(Backlog.model_validate_json(data).items)
        return total

    starter = Starter()
    clock = {"now": START}
    ledger: Counter[str] = Counter()
    uncleared_rows = 0

    def land(name: str, at: datetime, rows: Sequence[Mapping[str, Any]]) -> None:
        nonlocal uncleared_rows
        for row in rows:
            ledger[str(row["disposition"])] += 1
            uncleared_rows += row["source"] == SIM_UNCLEARED

    control = ControlRecord(
        version=1,
        enabled=True,
        armed_until=START + timedelta(days=2),
        sources={WIKIMEDIA: SourceSwitch(enabled=True), SIM_SAMPLED: SourceSwitch(enabled=True)},
    )
    store.create(CONTROL_OBJECT, control.model_dump_json().encode("utf-8"))
    env = TickEnvironment(
        store=store,
        land=land,
        starter=starter,
        now=lambda: clock["now"],
        config=config,
        cleared=lambda source: source != SIM_UNCLEARED,
        policy_version=lambda source: "sim-policy-1",
        publication_lag=lambda source: timedelta(days=1),
        load_targets=lambda: document,
        log=lambda *args, **fields: None,
        tiering=tiering_for(events, config.tier_caps),
    )

    def outcome_for(source: str, hour: float) -> Outcome:
        failing = 6 <= hour < 12
        if scenario is Scenario.OUTAGE and source == SIM_SAMPLED and failing:
            return Outcome.FAILED
        if scenario is Scenario.RATE_LIMIT and source == SIM_SAMPLED and hour == 6:
            return Outcome.RATE_LIMITED
        return Outcome.SUCCEEDED

    job_source = {caps.collector_job: source for source, caps in config.sources.items()}
    requests: Counter[str] = Counter()
    executions: Counter[str] = Counter()
    records: list[TickRecord] = []
    orders = backlog_max = 0
    after_gap: tuple[int, int] | None = None
    offline = scenario in (Scenario.OFFLINE, Scenario.AFTER_DOWNTIME)
    tracemalloc.start()
    began = time.perf_counter()
    for step in range(hours * 60 // tick_minutes):
        now = START + timedelta(minutes=step * tick_minutes, seconds=7)
        hour = step * tick_minutes / 60
        clock["now"] = now
        if offline and 6 <= hour < 12:
            continue  # the scheduler or the dispatcher is down: these ticks never happen
        reads_before, started_before = store.reads, len(starter.started)
        tick_began = time.perf_counter()
        summary = run_tick(env)
        elapsed = time.perf_counter() - tick_began
        jobs = tuple(starter.started[started_before:])
        counts: Mapping[str, int] = summary.get("dispositions", {})
        planned = counts.get("planned", 0)
        deferred = sum(v for k, v in counts.items() if k.startswith("deferred"))
        skipped = sum(v for k, v in counts.items() if k.startswith("skipped"))
        expired = sum(v for k, v in counts.items() if k.startswith("expired"))
        orders += len(summary.get("work_orders", ()))
        held = waiting()
        backlog_max = max(backlog_max, held)
        if offline and hour == 12:
            after_gap = (len(jobs), planned)
        records.append(
            TickRecord(
                tick=str(summary.get("tick")),
                outcome=str(summary.get("outcome", "dispatched")),
                due=sum(counts.values()),
                planned=planned,
                deferred=deferred,
                skipped=skipped,
                expired=expired,
                partitions_read=int(summary.get("partitions_read", 0)),
                object_reads=store.reads - reads_before,
                jobs=jobs,
                seconds=elapsed,
            )
        )
        # The stand-in collectors: each claims its order by exact name and files a receipt.
        for job in jobs:
            executions[job] += 1
            source = job_source.get(job)
            if source is None:
                continue
            finished = now + timedelta(minutes=2)
            try:
                order = claim(store, source, finished, "sim-policy-1")
            except ClaimRefused:
                continue
            result = outcome_for(source, hour)
            sent = len(order.targets) if result is Outcome.SUCCEEDED else 1
            requests[source] += sent
            complete(
                store,
                OrderReceipt(
                    order_id=order.order_id,
                    source=source,
                    outcome=result,
                    requests_sent=sent,
                    targets_collected=len(order.targets) if result is Outcome.SUCCEEDED else 0,
                    targets_failed=0 if result is Outcome.SUCCEEDED else len(order.targets),
                    completed_at=finished,
                    run_id=f"sim-{step}",
                ),
            )
    runtime = time.perf_counter() - began
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    manifest = ScheduleStore(store).current()
    assert manifest is not None
    tiers: Counter[str] = Counter()
    for key, count in manifest.partitions.items():
        parts = key.split("/")
        tiers["period" if parts[1] == "period" else parts[1].upper()] += count
    entries_read = max(
        (
            sum(
                manifest.partitions.get(key, 0)
                for key in manifest.partitions
                if key in _keys_read(record, manifest.partitions)
            )
            for record in records
        ),
        default=0,
    )
    dispatched = [r for r in records if r.outcome == "dispatched"]
    wikimedia_runs = executions.get("ms-wikimedia", 0)
    workload = Workload(
        label=f"{scenario.value}, {catalogued:,} catalogued",
        dispatcher_executions=len(records),
        collector_executions={
            "ms-wikimedia": wikimedia_runs,
            SIM_COLLECTOR: executions.get(SIM_COLLECTOR, 0),
        },
        collector_requests={
            "ms-wikimedia": requests.get(WIKIMEDIA, 0),
            SIM_COLLECTOR: requests.get(SIM_SAMPLED, 0),
        },
        warehouse_daily_builds=1,
        # A sampled source lands work every tick, so the other builds are sample builds.
        warehouse_hourly_builds=max(0, executions.get(config.warehouse_job, 0) - 1),
        period_rows=requests.get(WIKIMEDIA, 0) * 4,
        sample_rows=requests.get(SIM_SAMPLED, 0),
        ledger_rows=sum(ledger.values()),
    )
    return Result(
        scenario=scenario.value,
        tick_minutes=tick_minutes,
        catalogued=manifest.catalogued,
        monitored=manifest.monitored,
        eligible=manifest.eligible,
        scheduled=sum(manifest.partitions.values()),
        high_frequency=manifest.high_frequency,
        tiers=dict(sorted(tiers.items())),
        ticks=len(records),
        due_per_tick_max=max((r.due for r in dispatched), default=0),
        due_per_tick_mean=round(sum(r.due for r in dispatched) / max(1, len(dispatched)), 1),
        entries_read_per_tick_max=entries_read,
        object_reads_per_tick_max=max((r.object_reads for r in records), default=0),
        listings=store.lists,
        ledger_rows=sum(ledger.values()),
        work_orders=orders,
        executions=dict(sorted(executions.items())),
        job_starts_per_tick_max=max((len(r.jobs) for r in records), default=0),
        requests=dict(sorted(requests.items())),
        planned=sum(r.planned for r in records),
        deferred=sum(r.deferred for r in records),
        skipped=sum(r.skipped for r in records),
        expired=sum(r.expired for r in records),
        backlog_max=backlog_max,
        backlog_end=waiting(),
        uncleared_rows=uncleared_rows,
        first_tick_after_gap_jobs=None if after_gap is None else after_gap[0],
        first_tick_after_gap_planned=None if after_gap is None else after_gap[1],
        runtime_seconds=round(runtime, 3),
        slowest_tick_seconds=round(max((r.seconds for r in records), default=0.0), 4),
        peak_memory_mb=round(peak / 1e6, 1),
        projection=project(workload),
        records=tuple(records) if keep_records else (),
    )


def _keys_read(record: TickRecord, partitions: Mapping[str, int]) -> set[str]:
    """The partitions a tick could have read: those due at its time of day."""
    moment = datetime.fromisoformat(record.tick)
    config = simulation_config()
    from metaspike.scheduling.schedule import ScheduleManifest, due_partitions

    manifest = ScheduleManifest(
        version="-",
        generated_at=moment,
        targets_version="-",
        rules_version="-",
        catalogued=0,
        monitored=0,
        eligible={},
        high_frequency=0,
        partitions=partitions,
        period_cohorts={WIKIMEDIA: sum(1 for k in partitions if k.startswith(WIKIMEDIA))},
        modes={WIKIMEDIA: SourceMode.FETCH_HISTORY, SIM_SAMPLED: SourceMode.SAMPLE_NOW},
    )
    keys: set[str] = set()
    for source in (WIKIMEDIA, SIM_SAMPLED):
        keys.update(due_partitions(manifest, source, moment, config))
    return keys


def matrix(sizes: Sequence[int] = (1_000, 5_000, 10_000, 50_000)) -> list[Result]:
    results = []
    for size in sizes:
        for scenario in Scenario:
            if scenario is Scenario.SMALL_ELIGIBLE and size != max(sizes):
                continue
            results.append(run(scenario, size))
    return results


def current_production(hours: int = 24) -> Workload:
    """The Wikimedia-only C2 configuration: 14 articles, hourly tick, one build a day."""
    return Workload(
        label="C2 as planned: 14 Wikimedia articles, hourly tick",
        dispatcher_executions=hours,
        collector_executions={"ms-wikimedia": 1},
        collector_requests={"ms-wikimedia": 14},
        warehouse_daily_builds=1,
        period_rows=14 * 4,
        ledger_rows=14,
    )


if __name__ == "__main__":
    import json

    out = [r.model_dump(mode="json", exclude={"records"}) for r in matrix()]
    out.append({"projection": project(current_production()).model_dump(mode="json")})
    print(json.dumps(out, indent=1))
