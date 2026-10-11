"""The walk-forward backtest.

For each day T in the range, in order:

    1. rebuild the inputs as they stood at the end of T        (prediction side)
    2. compute the prediction from those inputs alone
    3. freeze it as a snapshot
    4. read the days after T and label the snapshot            (outcome side)
    5. join snapshot and label

Steps 1 to 3 never see step 4's data: they are given a `PredictionInputs`, which holds
nothing after T, and the function that predicts has no parameter an outcome could be
passed through. The checks at the end are run, not assumed, and their results go into
the report.

Cost is linear in days times games: each day rebuilds each game's fixed-length window
and scores it once. Nothing is recomputed across the whole history per day.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from metaspike.domain.games import Catalogue
from metaspike.events.model import CoverageCheck, EventRecord
from metaspike.events.rules import EVENT_HEAT_RULES_V1, EventHeatRules
from metaspike.events.store import EventStore
from metaspike.pipeline.collection import CollectionReport
from metaspike.validation.history import LeakageError, RecordedHistory
from metaspike.validation.metrics import Pair
from metaspike.validation.outcomes import OutcomeLabel, label
from metaspike.validation.report import LeakageCheck, ValidationReport, build_report
from metaspike.validation.rules import (
    OUTCOME_RULES_V1,
    VALIDATION_PROTOCOL_V1,
    OutcomeRules,
    ValidationProtocol,
)
from metaspike.validation.snapshot import RecommendationSnapshot, predict

_DAY = timedelta(days=1)
# What future values are multiplied by when checking that they cannot matter.
_PERTURBATION = 7.0


@dataclass(frozen=True)
class BacktestResult:
    snapshots: tuple[RecommendationSnapshot, ...]
    labels: tuple[OutcomeLabel, ...]
    report: ValidationReport

    @property
    def pairs(self) -> tuple[Pair, ...]:
        by_snapshot = {item.snapshot_id: item for item in self.labels}
        return tuple((snapshot, by_snapshot[snapshot.snapshot_id]) for snapshot in self.snapshots)


def days_between(first: date, last: date) -> list[date]:
    if last < first:
        raise ValueError("the range ends before it starts")
    return [first + offset * _DAY for offset in range((last - first).days + 1)]


def perturb_after(report: CollectionReport, cutoff: date, factor: float) -> CollectionReport:
    """The same recording with every value after `cutoff` multiplied by `factor`.

    Used only to check for leakage: a prediction at `cutoff` must not notice.
    """
    runs = []
    for run in report.runs:
        outcomes = []
        for signal in run.outcomes:
            series = signal.series
            if series is None:
                outcomes.append(signal)
                continue
            points = tuple(
                point.model_copy(update={"value": point.value * factor})
                if point.value is not None and point.period_day > cutoff
                else point
                for point in series.points
            )
            outcomes.append(
                signal.model_copy(update={"series": series.model_copy(update={"points": points})})
            )
        runs.append(run.model_copy(update={"outcomes": tuple(outcomes)}))
    return report.model_copy(update={"runs": tuple(runs)})


def _events_at(
    store: EventStore | None, as_of: datetime
) -> tuple[tuple[EventRecord, ...], tuple[CoverageCheck, ...]]:
    """Events and coverage as known at `as_of`: later observations do not exist yet."""
    if store is None:
        return (), ()
    return store.events(as_of), store.checks(as_of)


def run_backtest(
    catalogue: Catalogue,
    recording: CollectionReport,
    first_day: date,
    last_day: date,
    created_at: datetime,
    publication_lag: dict[str, timedelta] | None = None,
    event_store: EventStore | None = None,
    outcome_rules: OutcomeRules = OUTCOME_RULES_V1,
    protocol: ValidationProtocol = VALIDATION_PROTOCOL_V1,
    event_rules: EventHeatRules = EVENT_HEAT_RULES_V1,
) -> BacktestResult:
    """Walk forward over `[first_day, last_day]` and report. Deterministic and offline."""
    history = RecordedHistory(recording, publication_lag)
    days = days_between(first_day, last_day)
    if days[0] < history.first_day or days[-1] > history.last_day:
        raise ValueError(
            f"the range must lie inside the recording ({history.first_day} to {history.last_day})"
        )

    # --- prediction side: nothing below this line may see beyond its own day ----------
    by_day: dict[date, tuple[RecommendationSnapshot, ...]] = {}
    for day in days:
        inputs = history.inputs_at(day)
        records, checks = _events_at(event_store, inputs.as_of)
        by_day[day] = predict(catalogue, inputs, created_at, records, checks, event_rules)
    snapshots = tuple(snapshot for day in days for snapshot in by_day[day])

    # --- outcome side: the predictions are frozen; only now is the future read --------
    future = history.future()
    labels = tuple(label(snapshot, future, outcome_rules) for snapshot in snapshots)

    # --- checks, run after the fact ---------------------------------------------------
    reproduced = 0
    for day in days:
        inputs = history.inputs_at(day)
        records, checks = _events_at(event_store, inputs.as_of)
        again = predict(catalogue, inputs, created_at, records, checks, event_rules)
        if [item.snapshot_id for item in again] != [item.snapshot_id for item in by_day[day]]:
            raise LeakageError(f"the prediction for {day} did not reproduce")
        reproduced += len(again)

    probes = sorted({days[0], days[len(days) // 2], days[-1]})
    perturbed = 0
    for day in probes:
        altered = RecordedHistory(perturb_after(recording, day, _PERTURBATION), publication_lag)
        inputs = altered.inputs_at(day)
        records, checks = _events_at(event_store, inputs.as_of)
        again = predict(catalogue, inputs, created_at, records, checks, event_rules)
        if [item.snapshot_id for item in again] != [item.snapshot_id for item in by_day[day]]:
            raise LeakageError(f"the prediction for {day} changed when its future was altered")
        perturbed += len(again)

    frozen = sum(
        1
        for snapshot, outcome in zip(snapshots, labels, strict=True)
        if outcome.reference_baseline
        == (None if snapshot.features is None else snapshot.features.baseline_median_28d)
    )
    if frozen != len(snapshots):
        raise LeakageError("a label's reference differs from the one its snapshot recorded")

    leakage_checks = (
        LeakageCheck(
            name="inputs_end_at_cutoff",
            description=(
                "Every prediction was computed from inputs rebuilt for its own day and "
                "refused if they held any observation after it."
            ),
            checked=len(days),
            passed=True,
        ),
        LeakageCheck(
            name="reproduced_after_labelling",
            description=(
                "After every outcome had been read, each prediction was computed again "
                "from scratch and had the identical snapshot id."
            ),
            checked=reproduced,
            passed=True,
        ),
        LeakageCheck(
            name="unchanged_when_future_altered",
            description=(
                "For the first, middle and last day, every value after that day was "
                f"multiplied by {_PERTURBATION:g} and the prediction recomputed: identical."
            ),
            checked=perturbed,
            passed=True,
        ),
        LeakageCheck(
            name="reference_frozen_at_snapshot",
            description=(
                "Every label's reference level is the one its snapshot recorded; none "
                "was computed from later data."
            ),
            checked=frozen,
            passed=True,
        ),
    )
    pairs: Sequence[Pair] = tuple(zip(snapshots, labels, strict=True))
    real_events = event_store is not None and bool(event_store.observations())
    report = build_report(
        pairs,
        outcome_rules,
        protocol,
        event_rules.version,
        leakage_checks,
        real_event_history=real_events,
        data_through=history.last_day,
        created_at=created_at,
    )
    return BacktestResult(snapshots=snapshots, labels=labels, report=report)
