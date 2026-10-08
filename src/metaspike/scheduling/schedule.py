"""The versioned schedule: who is asked, by which source, how often.

The schedule is written once a day from the targets document. It is partitioned so
that a tick reads only what can be due on it:

    {source}/t0 .. t3            sampled, one partition per tier
    {source}/t4/c00 .. c23       sampled long tail, one cohort due each hour
    {source}/period/c00 ..       period sources, one cohort due each hour from
                                 the period hour

A tick never opens the catalogue and never reads a partition that cannot be due.

Cohorts are stable. A long-tail target's cohort is a hash of its id alone, so no
target moves when the catalogue grows. A period target's cohort uses a jump hash over
the number of cohorts in use, so when that number grows only about one target in n
moves, and none otherwise.

Five sets are kept apart and counted in the manifest: catalogued (every entity),
monitored (has a target), eligible (its source is cleared), scheduled (in a
partition), and high frequency (tiers 0 to 2). What is due is decided per tick.
"""

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime

from pydantic import AwareDatetime, ValidationError

from metaspike.domain.model import FrozenModel
from metaspike.domain.timeseries import Granularity
from metaspike.jobs.targets import Target
from metaspike.scheduling.config import DispatchConfig
from metaspike.scheduling.tiers import INTERVAL_MINUTES, RULES_VERSION, Proposal, Tier
from metaspike.sources.base import SourceMode
from metaspike.storage.base import StorageCorruptionError
from metaspike.warehouse.objects import ObjectNotFound, ObjectStore, PreconditionFailed

ROOT = "dispatch/schedule"
POINTER = f"{ROOT}/CURRENT"


class ScheduleEntry(FrozenModel):
    source: str
    metric: str
    granularity: Granularity
    mode: SourceMode
    source_native_id: str
    canonical_game_id: str | None = None
    # The tier in force. In production this is the long tail unless pinned.
    tier: Tier
    # What the shadow rule proposed, recorded and not acted on.
    shadow_tier: Tier | None = None
    reason: str


class ScheduleManifest(FrozenModel):
    version: str
    generated_at: AwareDatetime
    targets_version: str
    rules_version: str
    catalogued: int
    monitored: int
    # Per source: targets whose source is cleared. Others are never scheduled.
    eligible: Mapping[str, int]
    high_frequency: int
    # Partition key to the number of entries in it. Empty partitions are not written.
    partitions: Mapping[str, int]
    period_cohorts: Mapping[str, int]
    modes: Mapping[str, SourceMode]


class SchedulePointer(FrozenModel):
    version: str
    generated_at: AwareDatetime


def _digest(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


def long_tail_cohort(source_native_id: str, cohorts: int = 24) -> int:
    """The hour of the day a long-tail target is due. Depends on its id alone."""
    return _digest(source_native_id) % cohorts


def jump_hash(key: int, buckets: int) -> int:
    """Lamping and Veach's jump consistent hash: growing `buckets` moves few keys."""
    if buckets < 1:
        raise ValueError("at least one bucket is needed")
    b, j = -1, 0
    while j < buckets:
        b = j
        key = (key * 2862933555777941757 + 1) & 0xFFFFFFFFFFFFFFFF
        j = int((b + 1) * (1 << 31) / ((key >> 33) + 1))
    return b


def period_cohort(source_native_id: str, cohorts: int) -> int:
    return jump_hash(_digest(source_native_id), cohorts)


def partition_key(entry: ScheduleEntry, period_cohorts: int, config: DispatchConfig) -> str:
    if entry.mode is SourceMode.FETCH_HISTORY:
        return f"{entry.source}/period/c{period_cohort(entry.source_native_id, period_cohorts):02d}"
    if entry.tier is Tier.LONG_TAIL:
        cohort = long_tail_cohort(entry.source_native_id, config.long_tail_cohorts)
        return f"{entry.source}/t4/c{cohort:02d}"
    return f"{entry.source}/t{int(entry.tier)}"


def build_schedule(
    targets: Iterable[Target],
    *,
    targets_version: str,
    catalogued: int,
    cleared: Callable[[str], bool],
    config: DispatchConfig,
    at: datetime,
    tiers: Mapping[tuple[str, str], Tier] | None = None,
    proposals: Mapping[tuple[str, str], Proposal] | None = None,
) -> tuple[ScheduleManifest, dict[str, tuple[ScheduleEntry, ...]]]:
    """The schedule for one targets document.

    `tiers` is the tier in force per (source, native id); anything absent is the long
    tail. `proposals` is what the shadow rule said and is only recorded. A target whose
    source is not cleared, or has no configured caps, is counted and not scheduled.
    """
    tiers = tiers or {}
    proposals = proposals or {}
    monitored: set[str] = set()
    eligible: Counter[str] = Counter()
    by_source: dict[str, list[ScheduleEntry]] = defaultdict(list)
    modes: dict[str, SourceMode] = {}
    for target in targets:
        monitored.add(target.canonical_game_id or f"{target.source}:{target.source_native_id}")
        if target.source not in config.sources or not cleared(target.source):
            continue
        key = (target.source, target.source_native_id)
        proposal = proposals.get(key)
        tier = tiers.get(key, Tier.LONG_TAIL)
        if target.mode is SourceMode.FETCH_HISTORY or tier is Tier.CATALOGUED:
            tier = Tier.LONG_TAIL  # a period source has one cadence
        eligible[target.source] += 1
        modes[target.source] = target.mode
        by_source[target.source].append(
            ScheduleEntry(
                source=target.source,
                metric=target.metric,
                granularity=target.granularity,
                mode=target.mode,
                source_native_id=target.source_native_id,
                canonical_game_id=target.canonical_game_id,
                tier=tier,
                shadow_tier=None if proposal is None else proposal.tier,
                reason="pinned_or_assigned" if key in tiers else "default_long_tail",
            )
        )
    period_cohorts: dict[str, int] = {}
    partitions: dict[str, list[ScheduleEntry]] = defaultdict(list)
    for source, entries in by_source.items():
        cohorts = 1
        if modes[source] is SourceMode.FETCH_HISTORY:
            per_run = config.sources[source].targets_per_execution
            cohorts = min(config.max_period_cohorts, max(1, math.ceil(len(entries) / per_run)))
            period_cohorts[source] = cohorts
        for entry in entries:
            partitions[partition_key(entry, cohorts, config)].append(entry)
    ordered = {
        key: tuple(sorted(entries, key=lambda e: e.source_native_id))
        for key, entries in sorted(partitions.items())
    }
    high = sum(
        len(entries)
        for entries in ordered.values()
        for entry in entries[:1]
        if entry.mode is SourceMode.SAMPLE_NOW and entry.tier <= Tier.ACTIVE
    )
    content = hashlib.sha256(
        json.dumps(
            {k: [e.model_dump(mode="json") for e in v] for k, v in ordered.items()},
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    version = (
        "s"
        + hashlib.sha256(
            f"{targets_version}|{RULES_VERSION}|{catalogued}|{content}".encode()
        ).hexdigest()[:20]
    )
    manifest = ScheduleManifest(
        version=version,
        generated_at=at,
        targets_version=targets_version,
        rules_version=RULES_VERSION,
        catalogued=catalogued,
        monitored=len(monitored),
        eligible=dict(sorted(eligible.items())),
        high_frequency=high,
        partitions={key: len(entries) for key, entries in ordered.items()},
        period_cohorts=period_cohorts,
        modes=dict(sorted(modes.items())),
    )
    return manifest, ordered


def due_partitions(
    manifest: ScheduleManifest, source: str, tick: datetime, config: DispatchConfig
) -> list[str]:
    """The partitions of one source that can hold work due on this tick, in order."""
    keys: list[str] = []
    minute_of_day = tick.hour * 60 + tick.minute
    if manifest.modes.get(source) is SourceMode.FETCH_HISTORY:
        if tick.minute == 0:
            cohort = (tick.hour - config.period_hour) % 24
            if cohort < manifest.period_cohorts.get(source, 0):
                keys.append(f"{source}/period/c{cohort:02d}")
    else:
        for tier in (Tier.SURGE, Tier.HOT, Tier.ACTIVE, Tier.WARM):
            if minute_of_day % INTERVAL_MINUTES[tier] == 0:
                keys.append(f"{source}/t{int(tier)}")
        if tick.minute == 0:
            keys.append(f"{source}/t4/c{tick.hour % config.long_tail_cohorts:02d}")
    return [key for key in keys if key in manifest.partitions]


class ScheduleStore:
    """Schedules in the dispatcher's own operations prefix. Exact names; no listing."""

    def __init__(self, store: ObjectStore) -> None:
        self._store = store

    def publish(
        self, manifest: ScheduleManifest, partitions: Mapping[str, Sequence[ScheduleEntry]]
    ) -> None:
        """Write a version's objects, then move the pointer to it.

        A version's name is a hash of its content, so writing it again is refused and
        means it is already there. The pointer moves last, on its generation.
        """
        base = f"{ROOT}/{manifest.version}"
        for key, entries in partitions.items():
            body = json.dumps([entry.model_dump(mode="json") for entry in entries]).encode()
            self._create(f"{base}/{key}.json", body)
        self._create(f"{base}/manifest.json", manifest.model_dump_json().encode("utf-8"))
        pointer = SchedulePointer(version=manifest.version, generated_at=manifest.generated_at)
        body = pointer.model_dump_json().encode("utf-8")
        try:
            current = self._store.get(POINTER)
        except ObjectNotFound:
            try:
                self._store.create(POINTER, body)
                return
            except PreconditionFailed:
                current = self._store.get(POINTER)
        if current.data != body:
            self._store.replace(POINTER, body, current.generation)

    def _create(self, name: str, body: bytes) -> None:
        try:
            self._store.create(name, body)
        except PreconditionFailed:
            if self._store.get(name).data != body:
                raise StorageCorruptionError(f"{name} exists with other content") from None

    def current(self) -> ScheduleManifest | None:
        try:
            pointer = SchedulePointer.model_validate_json(self._store.get(POINTER).data)
            data = self._store.get(f"{ROOT}/{pointer.version}/manifest.json").data
            return ScheduleManifest.model_validate_json(data)
        except ObjectNotFound:
            return None
        except ValidationError:
            raise StorageCorruptionError("the schedule pointer or manifest is unreadable") from None

    def partition(self, version: str, key: str) -> tuple[ScheduleEntry, ...]:
        data = self._store.get(f"{ROOT}/{version}/{key}.json").data
        try:
            return tuple(ScheduleEntry.model_validate(item) for item in json.loads(data))
        except (ValidationError, ValueError):
            raise StorageCorruptionError(f"schedule partition {key} is unreadable") from None
