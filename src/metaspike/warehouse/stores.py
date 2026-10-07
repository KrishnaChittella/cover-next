"""The storage contracts, kept in the warehouse.

The same three contracts the local stores satisfy, with the warehouse as the durable
copy: a write lands an immutable batch, the loader appends it to a table, and a read
is a bounded query. The meaning of a read is decided here, in Python, by the same
functions the local stores use (`resolve_slot`, `build_time_series`), so the two
implementations cannot drift apart on semantics; the warehouse only returns rows.

    SampleStore        ms_raw.sample_attempts      keyed by source-native id;
                                                   point-in-time by received_at;
                                                   ids from ms_raw.sample_native_ids
    ObservationStore   ms_raw.period_observations  one row per observed period version;
                                                   point-in-time by observed_at
    source records     ms_raw.source_records       one row per content version;
                                                   point-in-time by fetched_at

A write is visible to reads once its batch has been loaded, not before. Every write
is checked against the source's policy at the private-collection stage first.
"""

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from metaspike.domain.identity import IdentityLedger, SourceRecordKey
from metaspike.domain.observations import (
    Freshness,
    Observation,
    ObservationStatus,
    TimeSeries,
)
from metaspike.domain.samples import (
    LogicalSlotKey,
    SampleAttempt,
    SlotResolution,
    attempt_order,
    resolve_slot,
)
from metaspike.domain.signals import SignalKey
from metaspike.domain.source_policy import DeploymentStage, SourcePolicy, SourcePurpose
from metaspike.domain.source_records import SourceRecord
from metaspike.domain.timeseries import Granularity, TimeWindow
from metaspike.pipeline.normalize import build_time_series
from metaspike.storage.base import StorageCorruptionError, StoragePolicyError
from metaspike.warehouse.landing import (
    TABLES,
    LandingWriter,
    Table,
    period_observation_rows,
    sample_attempt_from_row,
    sample_attempt_row,
    source_record_from_row,
    source_record_row,
)
from metaspike.warehouse.loader import INDEX_HISTORY_START
from metaspike.warehouse.tables import TableRef, Warehouse

# Records are read by key across their whole history, which has no natural window.
_ALL_TIME_START = INDEX_HISTORY_START


def _ref(table: Table) -> TableRef:
    return TableRef(TABLES[table].dataset, table.value)


class WarehouseSampleStore:
    """`SampleStore` over landed batches and `ms_raw.sample_attempts`."""

    def __init__(
        self,
        landing: LandingWriter,
        warehouse: Warehouse,
        policy_for: Callable[[str], SourcePolicy],
        *,
        now: Callable[[], datetime],
        stage: DeploymentStage = DeploymentStage.PRIVATE_COLLECTION,
    ) -> None:
        self._landing = landing
        self._warehouse = warehouse
        self._policy_for = policy_for
        self._now = now
        self._stage = stage
        self._parts: dict[tuple[str, str], int] = {}
        self._open: list[SampleAttempt] | None = None

    def append(self, attempt: SampleAttempt) -> None:
        self._refuse_unless_permitted(attempt.source)
        if self._open is not None:
            self._open.append(attempt)
            return
        self.append_many((attempt,))

    def append_many(self, attempts: Sequence[SampleAttempt]) -> None:
        """Land the attempts durably: one immutable batch per source and run."""
        groups: dict[tuple[str, str], list[SampleAttempt]] = {}
        for attempt in attempts:
            self._refuse_unless_permitted(attempt.source)
            groups.setdefault((attempt.source, attempt.collection_run_id), []).append(attempt)
        for (source, run_id), group in groups.items():
            part = self._parts.get((source, run_id), 0)
            self._landing.land(
                Table.SAMPLE_ATTEMPTS,
                source,
                run_id,
                self._now(),
                [sample_attempt_row(attempt) for attempt in group],
                part=part,
            )
            self._parts[(source, run_id)] = part + 1

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Collect every append made inside the block into one batch per source.

        Nothing is durable until the block ends. If it ends in an error nothing is
        landed, and the slots stay unsampled, which is what happened.
        """
        if self._open is not None:
            raise RuntimeError("a batch is already open")
        self._open = []
        try:
            yield
            pending, self._open = self._open, None
            if pending:
                self.append_many(pending)
        finally:
            self._open = None

    def attempts(
        self, key: SignalKey, source_native_id: str, window: TimeWindow, as_of: datetime
    ) -> tuple[SampleAttempt, ...]:
        rows = self._warehouse.rows(
            _ref(Table.SAMPLE_ATTEMPTS),
            "slot_start",
            window.start,
            window.end,
            {
                "source": key.source,
                "metric": key.metric,
                "source_native_id": source_native_id,
                "granularity": key.granularity.value,
            },
        )
        stored = self._distinct(rows, as_of)
        # Attempts made inside an open batch are not durable yet, but the run that made
        # them must still see them, or it would sample the same slot twice.
        pending = tuple(
            a
            for a in self._open or ()
            if (a.source, a.metric, a.source_native_id, a.granularity)
            == (key.source, key.metric, source_native_id, key.granularity)
            and window.contains(a.slot_start)
            and a.received_at <= as_of
            and all(a.attempt_id != s.attempt_id for s in stored)
        )
        return tuple(sorted((*stored, *pending), key=attempt_order))

    def resolve(
        self, key: SignalKey, source_native_id: str, slot_start: datetime, as_of: datetime
    ) -> SlotResolution:
        (resolution,) = self.read(
            key, source_native_id, TimeWindow(start=slot_start, end=slot_start), as_of
        )
        return resolution

    def read(
        self, key: SignalKey, source_native_id: str, window: TimeWindow, as_of: datetime
    ) -> tuple[SlotResolution, ...]:
        by_slot: dict[datetime, list[SampleAttempt]] = {}
        for attempt in self.attempts(key, source_native_id, window, as_of):
            by_slot.setdefault(attempt.slot_start, []).append(attempt)
        return tuple(
            resolve_slot(
                LogicalSlotKey(
                    source=key.source,
                    metric=key.metric,
                    source_native_id=source_native_id,
                    slot_start=slot_start,
                ),
                key.granularity,
                tuple(by_slot.get(slot_start, ())),
            )
            for slot_start in window.period_starts(key.granularity)
        )

    def native_ids(self, key: SignalKey) -> tuple[str, ...]:
        """Every id that has ever had a stored attempt, across all retained history.

        Answered from the loader's native-id index, which holds one row per target,
        so the answer covers all of history at a cost bounded by the target count.
        """
        index = TABLES[Table.SAMPLE_NATIVE_IDS]
        rows = self._warehouse.rows(
            _ref(Table.SAMPLE_NATIVE_IDS),
            index.partition,
            INDEX_HISTORY_START,
            self._now(),
            {"source": key.source, "metric": key.metric, "granularity": key.granularity.value},
        )
        names = {str(row["source_native_id"]) for row in rows}
        return tuple(sorted(names, key=lambda name: (len(name), name)))

    def _refuse_unless_permitted(self, source: str) -> None:
        refusal = self._policy_for(source).registration_refusal(
            SourcePurpose.DEMAND_SIGNALS, self._stage
        )
        if refusal is not None:
            raise StoragePolicyError(f"{source}: {refusal}")

    def _distinct(
        self, rows: Sequence[dict[str, Any]], as_of: datetime
    ) -> tuple[SampleAttempt, ...]:
        """One attempt per attempt id. A batch loaded twice is the same attempt twice."""
        found: dict[str, SampleAttempt] = {}
        for row in rows:
            attempt = sample_attempt_from_row(row)
            earlier = found.setdefault(attempt.attempt_id, attempt)
            if earlier != attempt:
                raise StorageCorruptionError(
                    f"attempt {attempt.attempt_id} is stored with two different contents"
                )
        return tuple(
            sorted((a for a in found.values() if a.received_at <= as_of), key=attempt_order)
        )


class WarehouseObservationStore:
    """`ObservationStore` over landed batches and `ms_raw.period_observations`."""

    def __init__(
        self,
        landing: LandingWriter,
        warehouse: Warehouse,
        policy_for: Callable[[str], SourcePolicy],
        *,
        now: Callable[[], datetime],
        run_id: str,
        identity: IdentityLedger | None = None,
        identifier_for: Callable[[str, str], str] | None = None,
        stage: DeploymentStage = DeploymentStage.PRIVATE_COLLECTION,
    ) -> None:
        self._landing = landing
        self._warehouse = warehouse
        self._policy_for = policy_for
        self._now = now
        self._run_id = run_id
        self._identity = identity or IdentityLedger()
        # The source's own name for an entity, for a series whose points do not carry it.
        self._identifier_for = identifier_for
        self._stage = stage
        self._part = 0

    def write(self, series: TimeSeries) -> int:
        policy = self._policy_for(series.source)
        refusal = policy.registration_refusal(SourcePurpose.DEMAND_SIGNALS, self._stage)
        if refusal is not None:
            raise StoragePolicyError(f"{series.source}: {refusal}")
        if not policy.field_storage_permitted(series.metric):
            raise StoragePolicyError(
                f"{series.source}: policy does not permit storing '{series.metric}' history"
            )
        references = {point.reference for point in series.points if point.reference}
        if len(references) == 1:
            identifier = references.pop()
        elif not references and self._identifier_for is not None:
            identifier = self._identifier_for(series.canonical_game_id, series.source)
        else:
            raise ValueError("a stored series names exactly one source identifier")
        key = SignalKey(source=series.source, metric=series.metric, granularity=series.granularity)
        window = TimeWindow(start=series.window_start, end=series.window_end)
        latest = self._latest(series.canonical_game_id, key, window, self._now())
        fresh = series.model_copy(
            update={
                "points": tuple(
                    point
                    for point in series.points
                    if point.value is not None
                    and (
                        point.period_start not in latest
                        or latest[point.period_start]["value"] != point.value
                    )
                )
            }
        )
        rows = period_observation_rows(fresh, identifier, self._run_id)
        if rows:
            self._landing.land(
                Table.PERIOD_OBSERVATIONS,
                series.source,
                self._run_id,
                self._now(),
                rows,
                part=self._part,
            )
            self._part += 1
        return len(rows)

    def read(
        self, canonical_game_id: str, key: SignalKey, window: TimeWindow, as_of: datetime
    ) -> TimeSeries:
        canonical = self._identity.resolve(canonical_game_id, as_of)
        known = self._latest(canonical, key, window, as_of)
        observations = [_observation(canonical, key, row) for _, row in sorted(known.items())]
        return build_time_series(canonical, key, window, observations, as_of)

    def signals(self, canonical_game_id: str) -> tuple[SignalKey, ...]:
        end = self._now()
        stored_under = (*self._identity.aliases_of(canonical_game_id), canonical_game_id)
        rows = self._warehouse.rows(
            _ref(Table.PERIOD_OBSERVATIONS),
            "period_start",
            end - timedelta(days=400),
            end,
            {"canonical_game_id": stored_under},
        )
        keys = {
            SignalKey(source=row["source"], metric=row["metric"], granularity=row["granularity"])
            for row in rows
        }
        return tuple(sorted(keys, key=lambda k: (k.source, k.metric, k.granularity.value)))

    def _latest(
        self, canonical: str, key: SignalKey, window: TimeWindow, as_of: datetime
    ) -> dict[datetime, dict[str, Any]]:
        """The newest version of each period known at `as_of`, under any id the entity had."""
        stored_under = (*self._identity.aliases_of(canonical, as_of), canonical)
        rows = self._warehouse.rows(
            _ref(Table.PERIOD_OBSERVATIONS),
            "period_start",
            window.start,
            window.end,
            {
                "source": key.source,
                "metric": key.metric,
                "granularity": key.granularity.value,
                "canonical_game_id": stored_under,
            },
        )
        latest: dict[datetime, dict[str, Any]] = {}
        # A later observation replaces an earlier one. Equal times are ordered by where
        # the row was landed, so the result does not depend on the order rows come back.
        for row in sorted(rows, key=lambda r: (r["observed_at"], r["landed_object"])):
            if row["observed_at"] <= as_of:
                latest[row["period_start"]] = row
        return latest


def _observation(canonical: str, key: SignalKey, row: dict[str, Any]) -> Observation:
    granularity = Granularity(row["granularity"])
    period_end = row["period_start"] + granularity.step
    return Observation(
        canonical_game_id=canonical,
        source=key.source,
        metric=key.metric,
        granularity=granularity,
        period_start=row["period_start"],
        value=row["value"],
        status=ObservationStatus(row["status"]),
        observed_at=row["observed_at"],
        source_timestamp=row.get("source_timestamp"),
        freshness=Freshness(
            period_end=period_end,
            lag_hours=round((row["observed_at"] - period_end).total_seconds() / 3600, 2),
        ),
        reference=row["source_identifier"],
    )


class WarehouseSourceRecordStore:
    """Source-native catalogue records over `ms_raw.source_records`."""

    def __init__(
        self,
        landing: LandingWriter,
        warehouse: Warehouse,
        policy_for: Callable[[str], SourcePolicy],
        *,
        now: Callable[[], datetime],
        run_id: str,
        stage: DeploymentStage = DeploymentStage.PRIVATE_COLLECTION,
    ) -> None:
        self._landing = landing
        self._warehouse = warehouse
        self._policy_for = policy_for
        self._now = now
        self._run_id = run_id
        self._stage = stage
        self._parts: dict[str, int] = {}

    def write(self, records: list[SourceRecord]) -> int:
        """Land new and changed records. Returns how many versions were kept."""
        kept = 0
        for source in sorted({record.key.source for record in records}):
            refusal = self._policy_for(source).registration_refusal(
                SourcePurpose.CATALOGUE_METADATA, self._stage
            )
            if refusal is not None:
                raise StoragePolicyError(f"{source}: {refusal}")
            latest = {key: record.content_hash for key, record in self._latest(source).items()}
            fresh = []
            for record in records:
                if record.key.source == source and latest.get(record.key) != record.content_hash:
                    latest[record.key] = record.content_hash
                    fresh.append(record)
            if fresh:
                part = self._parts.get(source, 0)
                self._landing.land(
                    Table.SOURCE_RECORDS,
                    source,
                    self._run_id,
                    self._now(),
                    [source_record_row(record, self._run_id) for record in fresh],
                    part=part,
                )
                self._parts[source] = part + 1
            kept += len(fresh)
        return kept

    def get(self, key: SourceRecordKey, as_of: datetime | None = None) -> SourceRecord | None:
        return self._latest(key.source, as_of, key.source_id).get(key)

    def history(self, key: SourceRecordKey) -> tuple[SourceRecord, ...]:
        return tuple(self._versions(key.source, key.source_id))

    def all(self, source: str, as_of: datetime | None = None) -> tuple[SourceRecord, ...]:
        return tuple(self._latest(source, as_of).values())

    def _latest(
        self, source: str, as_of: datetime | None = None, source_id: str | None = None
    ) -> dict[SourceRecordKey, SourceRecord]:
        latest: dict[SourceRecordKey, SourceRecord] = {}
        for record in self._versions(source, source_id):
            if as_of is None or record.fetched_at <= as_of:
                latest[record.key] = record
        return latest

    def _versions(self, source: str, source_id: str | None) -> list[SourceRecord]:
        """Every stored version, oldest first, with a repeated load counted once."""
        equals = (
            {"source": source} if source_id is None else {"source": source, "source_id": source_id}
        )
        rows = self._warehouse.rows(
            _ref(Table.SOURCE_RECORDS), "fetched_at", _ALL_TIME_START, self._now(), equals
        )
        seen: set[tuple[str, str, datetime]] = set()
        versions = []
        for row in sorted(rows, key=lambda r: (r["fetched_at"], r["landed_object"])):
            identity = (row["source_id"], row["content_hash"], row["fetched_at"])
            if identity in seen:
                continue
            seen.add(identity)
            record = source_record_from_row(row)
            if record.content_hash != row["content_hash"]:
                raise StorageCorruptionError(
                    f"{source}:{row['source_id']} does not match its stored content hash"
                )
            versions.append(record)
        return versions
