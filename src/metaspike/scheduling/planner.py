"""Bounded planning for one source on one tick. A pure function: no clock, no store.

Work that is due is planned up to the source's limits. What does not fit is deferred
to a bounded backlog; what has outlived its usefulness is given up. Nothing here
loops over missed ticks, so however long the dispatcher was away, one tick plans at
most one execution's worth of work per source.

The order of selection is fixed: higher tier first, then the oldest due, then the id.
The same inputs always give the same plan.
"""

from collections.abc import Iterable, Sequence
from datetime import datetime
from enum import StrEnum

from pydantic import AwareDatetime

from metaspike.domain.model import FrozenModel
from metaspike.domain.timeseries import Granularity
from metaspike.scheduling.config import SourceCaps
from metaspike.scheduling.health import Gate
from metaspike.scheduling.tiers import Tier
from metaspike.sources.base import SourceMode


class Disposition(StrEnum):
    PLANNED = "planned"
    DEFERRED_CAP = "deferred_cap"
    DEFERRED_BUSY = "deferred_collector_busy"
    SKIPPED_BREAKER_OPEN = "skipped_breaker_open"
    SKIPPED_DISABLED = "skipped_disabled_by_control"
    EXPIRED = "expired"
    BACKLOG_OVERFLOW = "expired_backlog_overflow"


class DueItem(FrozenModel):
    source: str
    metric: str
    granularity: Granularity
    mode: SourceMode
    source_native_id: str
    canonical_game_id: str | None = None
    tier: Tier
    slot: AwareDatetime
    due_at: AwareDatetime
    expires_at: AwareDatetime
    reason: str


class SourcePlan(FrozenModel):
    source: str
    gate: Gate | None
    probe: bool = False
    planned: tuple[DueItem, ...] = ()
    # Kept for a later tick.
    deferred: tuple[DueItem, ...] = ()
    deferred_as: Disposition = Disposition.DEFERRED_CAP
    # How many of `deferred` were already waiting before this tick. They lead the
    # tuple, and the ledger already holds the row that says they were deferred.
    carried: int = 0
    # Not kept: an open breaker or a disabled source does not build up a backlog.
    skipped: tuple[DueItem, ...] = ()
    skipped_as: Disposition | None = None
    expired: tuple[DueItem, ...] = ()
    overflowed: tuple[DueItem, ...] = ()

    def dispositions(self) -> Iterable[tuple[DueItem, Disposition]]:
        """What changed for each target on this tick: one ledger row each.

        A target that was already waiting and still is has nothing new to say.
        """
        for item in self.planned:
            yield item, Disposition.PLANNED
        for item in self.deferred[self.carried :]:
            yield item, self.deferred_as
        for item in self.skipped:
            yield item, self.skipped_as or Disposition.SKIPPED_DISABLED
        for item in self.expired:
            yield item, Disposition.EXPIRED
        for item in self.overflowed:
            yield item, Disposition.BACKLOG_OVERFLOW


def _order(item: DueItem) -> tuple[int, datetime, str]:
    return (int(item.tier), item.due_at, item.source_native_id)


def plan_source(
    source: str,
    *,
    fresh: Sequence[DueItem],
    backlog: Sequence[DueItem],
    caps: SourceCaps,
    now: datetime,
    gate: Gate,
    enabled: bool,
    requests_left_today: int,
    targets_limit: int | None = None,
) -> SourcePlan:
    """What happens to everything due for one source on this tick."""
    seen: set[tuple[str, datetime]] = set()
    live: list[DueItem] = []
    expired: list[DueItem] = []
    waiting: set[tuple[str, datetime]] = set()
    for item in (*backlog, *fresh):  # the backlog first: its record of a slot wins
        key = (item.source_native_id, item.slot)
        if key in seen:
            continue
        seen.add(key)
        (expired if item.expires_at <= now else live).append(item)
    for item in backlog:
        waiting.add((item.source_native_id, item.slot))
    live.sort(key=_order)
    expired.sort(key=_order)

    def keep(items: Sequence[DueItem]) -> tuple[tuple[DueItem, ...], int]:
        """Deferred items with those already waiting first, and how many those are."""
        old = [i for i in items if (i.source_native_id, i.slot) in waiting]
        new = [i for i in items if (i.source_native_id, i.slot) not in waiting]
        return (*old, *new), len(old)

    if not enabled:
        return SourcePlan(
            source=source,
            gate=None,
            skipped=tuple(live),
            skipped_as=Disposition.SKIPPED_DISABLED,
            expired=tuple(expired),
        )
    if gate is Gate.BLOCKED:
        return SourcePlan(
            source=source,
            gate=gate,
            skipped=tuple(live),
            skipped_as=Disposition.SKIPPED_BREAKER_OPEN,
            expired=tuple(expired),
        )
    if gate is Gate.BUSY:
        kept, over = live[: caps.max_backlog], live[caps.max_backlog :]
        deferred, carried = keep(kept)
        return SourcePlan(
            source=source,
            gate=gate,
            deferred=deferred,
            carried=carried,
            deferred_as=Disposition.DEFERRED_BUSY,
            expired=tuple(expired),
            overflowed=tuple(over),
        )
    if gate is Gate.PROBE:
        room = min(caps.probe_targets, max(0, requests_left_today))
        return SourcePlan(
            source=source,
            gate=gate,
            probe=True,
            planned=tuple(live[:room]),
            skipped=tuple(live[room:]),
            skipped_as=Disposition.SKIPPED_BREAKER_OPEN,
            expired=tuple(expired),
        )
    room = caps.targets_per_execution * caps.max_executions_per_tick
    if targets_limit is not None:
        room = min(room, targets_limit)
    room = max(0, min(room, requests_left_today))
    rest = live[room:]
    deferred, carried = keep(rest[: caps.max_backlog])
    return SourcePlan(
        source=source,
        gate=gate,
        planned=tuple(live[:room]),
        deferred=deferred,
        carried=carried,
        expired=tuple(expired),
        overflowed=tuple(rest[caps.max_backlog :]),
    )
