"""The collectors. Each asks one source, lands what it said, and marks what is done.

A collector refuses to start unless its source's reviewed policy clears the stage.
There is no override: the policy objects are the ones in the source modules, and
nothing in a job can swap them.

For every target the order is fixed (see `warehouse.markers`): read the marker, skip
if the slot is done, otherwise ask the source, land the result, and only after the
landing manifest is committed create the marker. A crash can cost a repeated request.
It cannot cause a sample to be skipped.
"""

import contextlib
import hashlib
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import AwareDatetime, ValidationError

from metaspike.catalogue.eligibility import SamplingTarget
from metaspike.catalogue.persistence import CatalogueRepository
from metaspike.catalogue.seed import bootstrap_catalogue
from metaspike.domain.games import Catalogue, SourceIdentifier
from metaspike.domain.model import FrozenModel
from metaspike.domain.observations import Observation, ObservationStatus
from metaspike.domain.samples import SampleAttempt, SampleReading, SampleStatus
from metaspike.domain.signals import SignalKey, SourceErrorCode
from metaspike.domain.source_policy import SourcePurpose
from metaspike.domain.timeseries import Granularity, TimeWindow
from metaspike.jobs.runtime import Runtime
from metaspike.pipeline.normalize import latest_due_period
from metaspike.pipeline.sampling import _attempt
from metaspike.scheduling.config import PRODUCTION, SourceCaps
from metaspike.scheduling.health import Outcome
from metaspike.scheduling.workorder import ClaimRefused, OrderReceipt, WorkOrder, claim, complete
from metaspike.sources.base import SamplingAdapter, SourceAdapter, SourceError, SourceMode
from metaspike.sources.http import AttemptBudget, RequestBudgetExhausted
from metaspike.sources.wikimedia import DAILY_PAGEVIEWS
from metaspike.sources.wikimedia import SOURCE_ID as WIKIMEDIA
from metaspike.warehouse.landing import (
    Table,
    observation_rows,
    policy_version,
    sample_attempt_row,
)
from metaspike.warehouse.markers import Completion, SlotMarkers
from metaspike.warehouse.objects import ObjectNotFound, ObjectStore, PreconditionFailed

# Supervised runs only: ask for exactly this many days for every article.
WINDOW_DAYS_ENV = "METASPIKE_WIKIMEDIA_DAYS"
# "required": act only on a work order from the dispatcher.
WORK_ORDERS_ENV = "METASPIKE_WORK_ORDERS"
BOOTSTRAP_FORMAT = "bootstrap_marker_v1"
# Why a run stopped asking when the stop was ours: the attempt budget was spent. Also
# the name of the log event. Deliberately not a source error code.
BUDGET_EXHAUSTED = "request_budget_exhausted"
# A target answered without the period that was due: what it said is landed, and the
# slot is not marked done. Also the name of the log event.
DUE_PERIOD_MISSING = "due_period_missing"
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


def bootstrap_marker_name(source: str, source_native_id: str) -> str:
    """A fixed name for one target's history fill. Read by exact name; never listed."""
    target = hashlib.sha256(source_native_id.encode("utf-8")).hexdigest()[:32]
    return f"{source}/bootstrap/{target}.json"


class BootstrapMarker(FrozenModel):
    """This target's history has been filled once, and where that fill is."""

    format: str = BOOTSTRAP_FORMAT
    source: str
    source_native_id: str
    days: int
    data_object: str
    content_sha256: str
    collection_run_id: str
    completed_at: AwareDatetime


def bootstrapped(store: ObjectStore, source: str, source_native_id: str) -> bool:
    """Whether a valid fill marker exists for exactly this target.

    Anything else (absent, unreadable, about another target) is no marker, so a damaged
    marker costs one more history fill and never a gap.
    """
    try:
        data = store.get(bootstrap_marker_name(source, source_native_id)).data
        marker = BootstrapMarker.model_validate_json(data)
    except (ObjectNotFound, ValidationError):
        return False
    return (
        marker.format == BOOTSTRAP_FORMAT
        and marker.source == source
        and marker.source_native_id == source_native_id
    )


def mark_bootstrapped(store: ObjectStore, marker: BootstrapMarker) -> None:
    with contextlib.suppress(PreconditionFailed):
        store.create(
            bootstrap_marker_name(marker.source, marker.source_native_id),
            marker.model_dump_json().encode("utf-8"),
        )


def seed_bootstrap_markers(
    store: ObjectStore,
    source: str,
    source_native_ids: Sequence[str],
    *,
    days: int,
    data_object: str,
    content_sha256: str,
    collection_run_id: str,
    completed_at: datetime,
) -> int:
    """Record that targets were filled by a run that predates fill markers.

    For the owner, once: the first supervised collection filled its targets before
    this marker existed. Each marker names the batch that holds the fill.
    """
    for native_id in source_native_ids:
        mark_bootstrapped(
            store,
            BootstrapMarker(
                source=source,
                source_native_id=native_id,
                days=days,
                data_object=data_object,
                content_sha256=content_sha256,
                collection_run_id=collection_run_id,
                completed_at=completed_at,
            ),
        )
    return len(source_native_ids)


def collect_wikimedia(
    runtime: Runtime,
    adapter: SourceAdapter,
    order: WorkOrder | None = None,
    caps: SourceCaps = PRODUCTION.sources[WIKIMEDIA],
) -> Mapping[str, Any]:
    """One pass over the articles: daily pageviews, the smallest window that will do.

    A target seen for the first time is filled once with the bootstrap window, at most
    `max_bootstraps_per_execution` of them in a run. After that a run asks for the
    steady window, or the reconciliation window once a week, ending at the day that
    is due. With a work order the run collects exactly the order's targets for the
    order's slot and window.

    The request limit is a limit on OUTBOUND ATTEMPTS: every physical HTTP request,
    each retry included. It is enforced where the request is sent, so a retry cannot
    exceed it. When it is spent the run stops asking; the targets it did not reach are
    reported as deferred by the budget. That is our own backpressure, not a refusal by
    the source: it is never logged or reported as a rate limit, and no observation is
    made up for a target that was not asked.
    """
    runtime.refuse_unless_cleared(WIKIMEDIA, SourcePurpose.DEMAND_SIGNALS)
    policy = runtime.policy_for(WIKIMEDIA)
    if adapter.mode is not SourceMode.FETCH_HISTORY:
        raise ValueError("the Wikimedia collector needs a fetch-history source")
    started = runtime.now()
    override: int | None = None
    if order is not None:
        due = order.slot
        routine = order.window_days or caps.steady_window_days
        request_limit = order.max_requests
        wanted: set[str] | None = {target.source_native_id for target in order.targets}
    else:
        lag = policy.operations.expected_publication_lag
        due = latest_due_period(Granularity.DAILY, started, lag)
        weekly = started.astimezone(UTC).weekday() == PRODUCTION.reconciliation_weekday
        routine = caps.reconciliation_window_days if weekly else caps.steady_window_days
        request_limit = caps.max_requests_per_execution
        wanted = None
        if WINDOW_DAYS_ENV in runtime.environ:
            override = int(runtime.environ[WINDOW_DAYS_ENV])
            if override < 1:
                raise ValueError(f"{WINDOW_DAYS_ENV} must be at least 1")
    catalogue, version = runtime_catalogue(runtime)
    markers = SlotMarkers(runtime.operations, runtime.now)
    metric = DAILY_PAGEVIEWS.metric
    # The adapter's own budget, where its transport sends. A stand-in with no transport
    # has none; it is charged one attempt for each fetch instead.
    shared: AttemptBudget | None = getattr(adapter, "budget", None)
    budget = shared if shared is not None else AttemptBudget()
    budget.restrict(request_limit)
    sent_before = budget.used

    rows: list[dict[str, Any]] = []
    fetched: list[str] = []
    filled: list[str] = []
    skipped = failed = attempted = fills_deferred = budget_deferred = due_missing = 0
    widest = 0
    halted_by: str | None = None
    known: set[str] = set()
    for game in catalogue.games:
        identifier = game.identifier_for(WIKIMEDIA)
        if identifier is None:
            continue
        native_id = _native(identifier)
        known.add(native_id)
        if wanted is not None and native_id not in wanted:
            continue
        if markers.completed(WIKIMEDIA, metric, native_id, due) is not None:
            skipped += 1
            continue
        if halted_by == BUDGET_EXHAUSTED:
            budget_deferred += 1  # not asked: our budget, not the source's answer
            continue
        if halted_by is not None:
            failed += 1
            continue
        fill = override is None and not bootstrapped(runtime.operations, WIKIMEDIA, native_id)
        if fill and len(filled) + fills_deferred >= caps.max_bootstraps_per_execution:
            fills_deferred += 1  # its history is filled by a later run, not skipped
            continue
        if budget.exhausted:
            halted_by = BUDGET_EXHAUSTED
            budget_deferred += 1
            runtime.log(
                BUDGET_EXHAUSTED,
                severity="WARNING",
                source=WIKIMEDIA,
                request_budget=budget.limit,
                outbound_attempts=budget.used - sent_before,
            )
            continue
        days = override or (caps.bootstrap_window_days if fill else routine)
        widest = max(widest, days)
        window = TimeWindow(start=due - timedelta(days=days - 1), end=due)
        attempted += 1
        try:
            if shared is None:
                budget.take(native_id)
            observations: Sequence[Observation] = adapter.fetch(
                game, identifier, DAILY_PAGEVIEWS, window, runtime.now()
            )
        except RequestBudgetExhausted as spent:
            # The budget ran out inside this fetch. If an attempt had been sent and
            # failed, that failure is real and is recorded as what it was; the retry
            # that was not sent is ours. If nothing was sent, the target was not asked.
            halted_by = BUDGET_EXHAUSTED
            if spent.after is None:
                attempted -= 1
                budget_deferred += 1
            else:
                failed += 1
                refused = spent.after.code in _REFUSALS
                runtime.log(
                    "source_refused" if refused else "source_failed",
                    severity="WARNING",
                    source=WIKIMEDIA,
                    error_code=spent.after.code.value,
                    target=native_id,
                    retry_not_sent=BUDGET_EXHAUSTED,
                )
                if refused:
                    halted_by = spent.after.code.value  # a real refusal outranks our cap
            runtime.log(
                BUDGET_EXHAUSTED,
                severity="WARNING",
                source=WIKIMEDIA,
                request_budget=budget.limit,
                outbound_attempts=budget.used - sent_before,
            )
            continue
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
            if fill or days >= caps.bootstrap_window_days:
                filled.append(native_id)
            if any(
                point.period_start == due and point.status is ObservationStatus.OBSERVED
                for point in observations
            ):
                fetched.append(native_id)
            else:
                # The answer is real and is landed, but it does not hold the period this
                # run was for. The slot is not done: no marker, so a later run asks again.
                due_missing += 1
                runtime.log(
                    DUE_PERIOD_MISSING,
                    severity="WARNING",
                    source=WIKIMEDIA,
                    target=native_id,
                    slot=due.isoformat(),
                    latest_period=max(str(row["period_start"]) for row in landed),
                )
        else:
            failed += 1  # the source answered with nothing: not a result, so no marker
    unknown = 0 if wanted is None else len(wanted - known)
    if unknown:
        failed += unknown
        runtime.log("order_targets_unknown", severity="WARNING", source=WIKIMEDIA, count=unknown)

    data_object = None
    if rows:
        manifest = runtime.writer.land(
            Table.PERIOD_OBSERVATIONS, WIKIMEDIA, runtime.run_id, runtime.now(), rows
        )
        data_object = manifest.data_object
        # The batch and its manifest are durable. Only now is anything marked done.
        for native_id in fetched:
            markers.complete(manifest, metric, native_id, due, Completion.COLLECTED)
        for native_id in filled:
            mark_bootstrapped(
                runtime.operations,
                BootstrapMarker(
                    source=WIKIMEDIA,
                    source_native_id=native_id,
                    days=widest,
                    data_object=manifest.data_object,
                    content_sha256=manifest.content_sha256,
                    collection_run_id=runtime.run_id,
                    completed_at=runtime.now(),
                ),
            )
    outbound = budget.used - sent_before
    complete_run = not (failed or fills_deferred or budget_deferred or due_missing)
    return {
        "outcome": "succeeded" if complete_run else "partial",
        # Outbound attempts: every physical request sent, retries included.
        "requests_sent": outbound,
        "outbound_attempts": outbound,
        "retry_attempts": max(0, outbound - attempted),
        "request_budget": budget.limit,
        "targets_intended": len(known) if wanted is None else len(wanted),
        "targets_attempted": attempted,
        "targets_budget_deferred": budget_deferred,
        "halted_by": halted_by,
        "catalogue": version,
        "slot": due.isoformat(),
        "window_days": widest,
        "routine_window_days": override or routine,
        "targets_collected": len(fetched),
        "targets_skipped_already_done": skipped,
        "targets_failed": failed,
        "targets_due_period_missing": due_missing,
        "history_filled": len(filled),
        "history_fill_deferred": fills_deferred,
        "rows_landed": len(rows),
        "data_object": data_object,
        "work_order": None if order is None else order.order_id,
    }


def collect_wikimedia_job(
    runtime: Runtime, build_adapter: Callable[[], SourceAdapter]
) -> Mapping[str, Any]:
    """The job's body: supervised by default, or only on a work order when required.

    With `METASPIKE_WORK_ORDERS=required` the collector acts on a valid, unexpired,
    uncompleted order addressed to it and on nothing else. Without one it builds no
    source client and asks nothing.
    """
    runtime.refuse_unless_cleared(WIKIMEDIA, SourcePurpose.DEMAND_SIGNALS)
    if runtime.environ.get(WORK_ORDERS_ENV) != "required":
        return collect_wikimedia(runtime, build_adapter())
    version = policy_version(runtime.policy_for(WIKIMEDIA))
    try:
        order = claim(runtime.operations, WIKIMEDIA, runtime.now(), version)
    except ClaimRefused as refusal:
        runtime.log("no_work_order", severity="WARNING", source=WIKIMEDIA, reason=refusal.reason)
        return {
            "outcome": "no_work_order",
            "reason": refusal.reason,
            "requests_sent": 0,
            "halted_by": None,
        }
    summary = collect_wikimedia(runtime, build_adapter(), order)
    # Only what the source did decides the outcome the breaker sees. Targets our own
    # budget kept us from asking make the run partial; they never make it a failure
    # or a rate limit.
    if summary["halted_by"] == SourceErrorCode.RATE_LIMITED.value:
        outcome = Outcome.RATE_LIMITED
    elif summary["targets_failed"] and not summary["targets_collected"]:
        outcome = Outcome.FAILED
    elif (
        summary["targets_failed"]
        or summary["targets_budget_deferred"]
        or summary["targets_due_period_missing"]
    ):
        outcome = Outcome.PARTIAL
    else:
        outcome = Outcome.SUCCEEDED
    complete(
        runtime.operations,
        OrderReceipt(
            order_id=order.order_id,
            source=WIKIMEDIA,
            outcome=outcome,
            requests_sent=summary["outbound_attempts"],
            targets_collected=summary["targets_collected"],
            targets_failed=summary["targets_failed"],
            completed_at=runtime.now(),
            run_id=runtime.run_id,
            request_budget=summary["request_budget"],
            targets_attempted=summary["targets_attempted"],
            retry_attempts=summary["retry_attempts"],
            targets_budget_deferred=summary["targets_budget_deferred"],
        ),
    )
    return summary


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
