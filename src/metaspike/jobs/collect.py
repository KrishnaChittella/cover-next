"""The collectors. Each asks one source, lands what it said, and marks what is done.

A collector refuses to start unless its source's reviewed policy clears the stage.
There is no override: the policy objects are the ones in the source modules, and
nothing in a job can swap them.

For every target the order is fixed (see `warehouse.markers`): read the marker, skip
if the slot is done, otherwise ask the source, land the result, and only after the
landing manifest is committed create the marker. A crash can cost a repeated request.
It cannot cause a sample to be skipped.
"""

import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from typing import Any

from metaspike.catalogue.eligibility import SamplingTarget
from metaspike.catalogue.persistence import CatalogueRepository
from metaspike.catalogue.seed import bootstrap_catalogue
from metaspike.domain.games import Catalogue, SourceIdentifier
from metaspike.domain.observations import Observation
from metaspike.domain.samples import SampleAttempt, SampleReading, SampleStatus
from metaspike.domain.signals import SignalKey, SourceErrorCode
from metaspike.domain.source_policy import SourcePurpose
from metaspike.domain.timeseries import Granularity, TimeWindow
from metaspike.jobs.runtime import Runtime
from metaspike.pipeline.normalize import latest_due_period
from metaspike.pipeline.sampling import _attempt
from metaspike.sources.base import SamplingAdapter, SourceAdapter, SourceError, SourceMode
from metaspike.sources.wikimedia import DAILY_PAGEVIEWS
from metaspike.sources.wikimedia import SOURCE_ID as WIKIMEDIA
from metaspike.warehouse.landing import Table, observation_rows, sample_attempt_row
from metaspike.warehouse.markers import Completion, SlotMarkers

WINDOW_DAYS_ENV = "METASPIKE_WIKIMEDIA_DAYS"
DEFAULT_WINDOW_DAYS = 90
# Errors that mean the source has refused us, not that one request went wrong.
_REFUSALS = (
    SourceErrorCode.UNAUTHORIZED,
    SourceErrorCode.RATE_LIMITED,
    SourceErrorCode.QUOTA_EXHAUSTED,
)


def runtime_catalogue(runtime: Runtime) -> tuple[Catalogue, str]:
    """The catalogue to collect for: the stored head, or the bootstrap entities."""
    head = CatalogueRepository(runtime.state, runtime.policy_for, runtime.stage).load()
    if head is None:
        return bootstrap_catalogue(), "bootstrap"
    return head.state.catalogue, head.record.generation


def collect_wikimedia(runtime: Runtime, adapter: SourceAdapter) -> Mapping[str, Any]:
    """One supervised pass over every entity's article. Daily pageviews only."""
    runtime.refuse_unless_cleared(WIKIMEDIA, SourcePurpose.DEMAND_SIGNALS)
    policy = runtime.policy_for(WIKIMEDIA)
    if adapter.mode is not SourceMode.FETCH_HISTORY:
        raise ValueError("the Wikimedia collector needs a fetch-history source")
    started = runtime.now()
    due = latest_due_period(Granularity.DAILY, started, policy.operations.expected_publication_lag)
    days = int(runtime.environ.get(WINDOW_DAYS_ENV, DEFAULT_WINDOW_DAYS))
    if days < 1:
        raise ValueError(f"{WINDOW_DAYS_ENV} must be at least 1")
    window = TimeWindow(start=due - timedelta(days=days - 1), end=due)
    catalogue, version = runtime_catalogue(runtime)
    markers = SlotMarkers(runtime.operations, runtime.now)
    metric = DAILY_PAGEVIEWS.metric

    rows: list[dict[str, Any]] = []
    fetched: list[str] = []
    skipped = failed = requests = 0
    halted_by: str | None = None
    for game in catalogue.games:
        identifier = game.identifier_for(WIKIMEDIA)
        if identifier is None:
            continue
        native_id = _native(identifier)
        if markers.completed(WIKIMEDIA, metric, native_id, due) is not None:
            skipped += 1
            continue
        if halted_by is not None:
            failed += 1
            continue
        requests += 1
        try:
            observations: Sequence[Observation] = adapter.fetch(
                game, identifier, DAILY_PAGEVIEWS, window, runtime.now()
            )
        except SourceError as error:
            failed += 1
            runtime.log(
                "source_refused" if error.code in _REFUSALS else "source_failed",
                severity="WARNING",
                source=WIKIMEDIA,
                error_code=error.code.value,
                target=native_id,
            )
            if error.code in _REFUSALS:
                halted_by = error.code.value  # stop asking; the rest wait for the next run
            continue
        landed = observation_rows(observations, native_id, runtime.run_id)
        if landed:
            rows.extend(landed)
            fetched.append(native_id)
        else:
            failed += 1  # the source answered with nothing: not a result, so no marker

    data_object = None
    if rows:
        manifest = runtime.writer.land(
            Table.PERIOD_OBSERVATIONS, WIKIMEDIA, runtime.run_id, runtime.now(), rows
        )
        data_object = manifest.data_object
        # The batch and its manifest are durable. Only now is anything marked done.
        for native_id in fetched:
            markers.complete(manifest, metric, native_id, due, Completion.COLLECTED)
    return {
        "outcome": "succeeded" if not failed else "partial",
        "requests_sent": requests,
        "halted_by": halted_by,
        "catalogue": version,
        "slot": due.isoformat(),
        "window_days": days,
        "targets_collected": len(fetched),
        "targets_skipped_already_done": skipped,
        "targets_failed": failed,
        "rows_landed": len(rows),
        "data_object": data_object,
    }


def _native(identifier: SourceIdentifier) -> str:
    return f"{identifier.namespace}/{identifier.value}"


def collect_samples(
    runtime: Runtime,
    adapter: SamplingAdapter,
    targets: Sequence[SamplingTarget],
    new_id: Callable[[], str] = lambda: uuid.uuid4().hex,
) -> Mapping[str, Any]:
    """One sampling pass for a sample-now source: every target, one slot, one batch."""
    source = adapter.source
    runtime.refuse_unless_cleared(source, SourcePurpose.DEMAND_SIGNALS)
    if adapter.mode is not SourceMode.SAMPLE_NOW:
        raise ValueError("a sampling collector needs a sample-now source")
    signal = adapter.signal
    key = SignalKey(source=source, metric=signal.metric, granularity=signal.granularity)
    scheduled_for = signal.granularity.floor(runtime.now())
    markers = SlotMarkers(runtime.operations, runtime.now)

    attempts: list[SampleAttempt] = []
    skipped = requests = 0
    halted_by: SourceErrorCode | None = None
    for target in sorted(targets, key=lambda t: (len(t.source_native_id), t.source_native_id)):
        slot = signal.granularity.floor(runtime.now())
        if markers.completed(source, signal.metric, target.source_native_id, slot) is not None:
            skipped += 1
            continue
        if halted_by is None:
            reading = adapter.sample(target.source_native_id)
            if reading.status is not SampleStatus.REFUSED:
                requests += 1
            if reading.halt_run:
                halted_by = reading.error_code
                runtime.log(
                    "source_refused",
                    severity="ERROR",
                    source=source,
                    error_code=None if halted_by is None else halted_by.value,
                )
        else:
            # The run has stopped sending. The refusal is recorded; nothing was asked.
            reading = SampleReading(
                status=SampleStatus.REFUSED, observed_at=runtime.now(), error_code=halted_by
            )
        attempts.append(
            _attempt(reading, target, key, scheduled_for, runtime.run_id, new_id(), runtime.now)
        )

    data_object = None
    if attempts:
        manifest = runtime.writer.land(
            Table.SAMPLE_ATTEMPTS,
            source,
            runtime.run_id,
            runtime.now(),
            [sample_attempt_row(attempt) for attempt in attempts],
        )
        data_object = manifest.data_object
        for attempt in attempts:
            runtime.log(
                "slot_resolved",
                source=source,
                target=attempt.source_native_id,
                slot=attempt.slot_start.isoformat(),
                state=attempt.status.value,
            )
            # Only an answer settles a slot. A failure or a refusal may be retried.
            if attempt.status is SampleStatus.OBSERVED:
                done = Completion.OBSERVED
            elif attempt.status is SampleStatus.UNAVAILABLE:
                done = Completion.UNAVAILABLE
            else:
                continue
            markers.complete(
                manifest, signal.metric, attempt.source_native_id, attempt.slot_start, done
            )
    return {
        "requests_sent": requests,
        "halted_by": None if halted_by is None else halted_by.value,
        "slot": scheduled_for.isoformat(),
        "targets": len(targets),
        "targets_skipped_already_done": skipped,
        "attempts_landed": len(attempts),
        "observed": sum(1 for a in attempts if a.status is SampleStatus.OBSERVED),
        "data_object": data_object,
    }
