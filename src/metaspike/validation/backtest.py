"""The walk-forward backtest.

For each prediction day D in the range, in order, with P = 00:00 UTC on D:

    1. rebuild the inputs as each source had published them by P    (prediction side)
    2. compute the prediction from those inputs alone
    3. freeze it as a snapshot
    4. read the days from P onwards and label the snapshot          (outcome side)
    5. join snapshot and label

Steps 1 to 3 never see step 4's data: they are given a `PredictionInputs`, which holds
nothing a source had not published by P, and the function that predicts has no
parameter an outcome could be passed through. The checks at the end are run, not
assumed, and their results go into the report.

Cost is linear in days times games: each day rebuilds each game's fixed-length window
and scores it once. Nothing is recomputed across the whole history per day.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from metaspike.domain.games import Catalogue
from metaspike.domain.timeseries import start_of_day
from metaspike.events.model import CoverageCheck, EventRecord
from metaspike.events.rules import EVENT_HEAT_RULES_V1, EventHeatRules
from metaspike.events.store import EventStore
from metaspike.pipeline.collection import CollectionReport
from metaspike.validation.history import (
    LeakageError,
    RecordedHistory,
    ensure_available,
    first_forward_day,
    latest_due_at,
)
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
# What not-yet-visible values are multiplied by when checking that they cannot matter.
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


def prediction_moment(day: date) -> datetime:
    """The moment a day's recommendation is simulated: 00:00 UTC, the start of the day."""
    return start_of_day(day)


def perturb_unavailable(
    report: CollectionReport, as_of: datetime, lags: Mapping[str, timedelta], factor: float
) -> CollectionReport:
    """The same recording with every value NOT yet published at `as_of` multiplied.

    Used only to check for leakage. It reaches further back than "after `as_of`": a
    period that had ended but that its source had not yet published is altered too, and
    a prediction at `as_of` must not notice either.
    """
    runs = []
    for run in report.runs:
        outcomes = []
        for signal in run.outcomes:
            series = signal.series
            if series is None:
                outcomes.append(signal)
                continue
            due = latest_due_at(as_of, signal.key.granularity, lags[run.source])
            points = tuple(
                point.model_copy(update={"value": point.value * factor})
                if point.value is not None and point.period_start > due
                else point
                for point in series.points
            )
            updated = series.model_copy(update={"points": points})
            outcomes.append(signal.model_copy(update={"series": updated}))
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
    publication_lag: Mapping[str, timedelta],
    event_store: EventStore | None = None,
    outcome_rules: OutcomeRules = OUTCOME_RULES_V1,
    protocol: ValidationProtocol = VALIDATION_PROTOCOL_V1,
    event_rules: EventHeatRules = EVENT_HEAT_RULES_V1,
) -> BacktestResult:
    """Walk forward over prediction days `[first_day, last_day]` and report.

    Deterministic and offline. `publication_lag` is required: there is no default,
    because assuming a source publishes sooner than it does is exactly the leak this
    exists to prevent.
    """
    history = RecordedHistory(recording, publication_lag)
    days = days_between(first_day, last_day)
    for day in (days[0], days[-1]):
        if not history.can_reconstruct(prediction_moment(day)):
            raise ValueError(
                f"{day} cannot be reconstructed from the recording "
                f"({history.first_day} to {history.last_day}) under these publication lags"
            )

    # --- prediction side: nothing below may see what a source had not published -------
    by_day: dict[date, tuple[RecommendationSnapshot, ...]] = {}
    series_checked = 0
    for day in days:
        inputs = history.inputs_at(prediction_moment(day))
        series_checked += ensure_available(inputs)
        records, checks = _events_at(event_store, inputs.as_of)
        by_day[day] = predict(catalogue, inputs, created_at, records, checks, event_rules)
    snapshots = tuple(snapshot for day in days for snapshot in by_day[day])

    # --- outcome side: the predictions are frozen; only now is the future read --------
    future = history.future()
    labels = tuple(label(snapshot, future, outcome_rules) for snapshot in snapshots)

    # --- checks, run after the fact ---------------------------------------------------
    not_due = [
        snapshot
        for snapshot in snapshots
        if snapshot.latest_observed_day is not None
        and snapshot.latest_observed_day > snapshot.latest_due_day
    ]
    if not_due:
        raise LeakageError(
            f"{len(not_due)} prediction(s) rest on a day their source had not published"
        )
    early = [
        outcome
        for snapshot, outcome in zip(snapshots, labels, strict=True)
        if outcome.primary_window_start != first_forward_day(snapshot.as_of)
        or (
            snapshot.latest_observed_day is not None
            and outcome.primary_window_start <= snapshot.latest_observed_day
        )
    ]
    if early:
        raise LeakageError(f"{len(early)} outcome window(s) do not start after the recommendation")

    reproduced = 0
    for day in days:
        inputs = history.inputs_at(prediction_moment(day))
        records, checks = _events_at(event_store, inputs.as_of)
        again = predict(catalogue, inputs, created_at, records, checks, event_rules)
        if [item.snapshot_id for item in again] != [item.snapshot_id for item in by_day[day]]:
            raise LeakageError(f"the prediction for {day} did not reproduce")
        reproduced += len(again)

    probes = sorted({days[0], days[len(days) // 2], days[-1]})
    perturbed = 0
    for day in probes:
        moment = prediction_moment(day)
        altered = RecordedHistory(
            perturb_unavailable(recording, moment, publication_lag, _PERTURBATION), publication_lag
        )
        inputs = altered.inputs_at(moment)
        records, checks = _events_at(event_store, inputs.as_of)
        again = predict(catalogue, inputs, created_at, records, checks, event_rules)
        if [item.snapshot_id for item in again] != [item.snapshot_id for item in by_day[day]]:
            raise LeakageError(
                f"the prediction for {day} changed when values it could not see were altered"
            )
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
            name="source_publication_lag_respected",
            description=(
                "Every series given to a prediction held no period its source had not yet "
                "published at the simulated moment, under that source's publication lag; "
                "and no prediction rests on a day later than its source's latest due day."
            ),
            checked=series_checked,
            passed=True,
        ),
        LeakageCheck(
            name="outcome_window_after_prediction",
            description=(
                "Every outcome window starts on the first whole day after the simulated "
                "moment, and after the newest day the prediction used."
            ),
            checked=len(labels),
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
            name="unchanged_when_unseen_values_altered",
            description=(
                "For the first, middle and last day, every value not yet published at "
                f"that moment was multiplied by {_PERTURBATION:g} and the prediction "
                "recomputed: identical. This includes days that had ended but were not "
                "yet published."
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
