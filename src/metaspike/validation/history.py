"""Recorded history, split into what a past moment could see and what came after it.

One recording, two views that never meet:

- `inputs_at(as_of)` is the PREDICTION side: a collection report rebuilt as it would
  have been at the simulated moment `as_of`. It is the only thing the scoring pipeline
  is ever given.
- `future()` is the OUTCOME side: values on given days, for labelling. It is a
  different object of a different type, and nothing on the prediction side accepts it.

**What a past moment could see.** A period having ended is not the same as a source
having published it. A source publishes a period some time after it ends: its
publication lag, taken from its policy. At a simulated moment P the newest period a
source could have supplied is

    latest_due_period(granularity, P, lag) = floor(P - one period - lag)

which is the same rule the live pipeline uses. Every later period is withheld from the
prediction, even though the recording, made afterwards, holds it. Withholding by the
calendar alone is not enough: with a one-day lag, yesterday's value exists in the
recording and did not exist for the source's readers at midnight.

So three things are kept apart, and a snapshot records all three:

    as_of             the simulated moment the recommendation is made
    evaluation_day    the last whole UTC day that had ended by then
    latest_due_day    the newest day the scored signal's source had published by then

A report is rebuilt for each moment from the raw observations. Features are never
computed on the whole history and sliced, which is how future information leaks into a
past baseline or percentile.

A recording made after the fact cannot show a value the source later revised, so a
revision is not reconstructable from it. That is a limit of a recording, stated here
and in the report, and not of the method: a store that keeps what was observed when
gives the same interface with revisions respected.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from metaspike.domain.signals import Signal, SignalKey
from metaspike.domain.timeseries import Granularity, start_of_day
from metaspike.pipeline.collection import CollectionReport, SourceRun, window_ending
from metaspike.pipeline.normalize import build_time_series, latest_due_period

_DAY = timedelta(days=1)


class LeakageError(Exception):
    """A prediction was about to see something it could not have seen at its moment."""


@dataclass(frozen=True)
class PredictionInputs:
    """Everything a prediction at `as_of` may use, and nothing else."""

    # The simulated moment the recommendation is made.
    as_of: datetime
    # The last whole UTC day that had ended by `as_of`. A calendar fact, not a data one.
    evaluation_day: date
    # Per source: how long after a period ends the source publishes it.
    publication_lag: Mapping[str, timedelta]
    report: CollectionReport


def evaluation_day_at(as_of: datetime) -> date:
    """The last whole UTC day that had ended by `as_of`."""
    return as_of.astimezone(UTC).date() - _DAY


def latest_due_at(as_of: datetime, granularity: Granularity, lag: timedelta) -> datetime:
    """The start of the newest period a source with this lag had published by `as_of`."""
    return latest_due_period(granularity, as_of, lag)


def first_forward_day(as_of: datetime) -> date:
    """The first UTC day that lies wholly after `as_of`.

    A moment exactly on a day boundary has that day wholly ahead of it. A moment inside
    a day does not: part of that day has already happened, so the first wholly-forward
    day is the next one.
    """
    moment = as_of.astimezone(UTC)
    day = moment.date()
    return day if moment == start_of_day(day) else day + _DAY


def ensure_available(inputs: PredictionInputs) -> int:
    """Refuse inputs holding anything the simulated moment could not have seen.

    Called before scoring. Returns how many series were checked. A point is refused if
    its source had not yet published its period at `as_of`, whether or not the period
    had ended.
    """
    if inputs.as_of.tzinfo is None:
        raise LeakageError("the simulated moment has no time zone")
    if inputs.evaluation_day != evaluation_day_at(inputs.as_of):
        raise LeakageError("the evaluation day is not the last whole day before the moment")
    report = inputs.report
    if report.window.end.astimezone(UTC).date() > inputs.evaluation_day:
        raise LeakageError("the report's window runs past the evaluation day")
    if report.observed_at > inputs.as_of:
        raise LeakageError("the report claims to have been observed after the simulated moment")
    checked = 0
    for run in report.runs:
        if run.source not in inputs.publication_lag:
            raise LeakageError(f"no publication lag is known for {run.source}")
        lag = inputs.publication_lag[run.source]
        for signal in run.outcomes:
            series = signal.series
            if series is None:
                continue
            checked += 1
            due = latest_due_at(inputs.as_of, signal.key.granularity, lag)
            if series.latest_due > due:
                raise LeakageError(f"{signal.canonical_game_id}: the series claims a later due")
            for point in series.points:
                if point.value is not None and point.period_start > due:
                    raise LeakageError(
                        f"{signal.canonical_game_id}: {run.source} had not published "
                        f"{point.period_day} at {inputs.as_of.isoformat()} "
                        f"(latest due {due.date()})"
                    )
    return checked


class FutureValues:
    """The OUTCOME side: what a signal did on given days. Labelling only."""

    def __init__(
        self, values: Mapping[tuple[str, SignalKey], Mapping[date, float]], data_through: date
    ) -> None:
        self._values = values
        self.data_through = data_through

    def on(self, canonical_game_id: str, key: SignalKey, day: date) -> float | None:
        """The value on one day, or None if that day was not observed."""
        return self._values.get((canonical_game_id, key), {}).get(day)

    def from_day(
        self, canonical_game_id: str, key: SignalKey, first_day: date, days: int
    ) -> tuple[tuple[date, float | None], ...]:
        """`days` consecutive days starting on `first_day`, each with its value or None."""
        return tuple(
            (first_day + offset * _DAY, self.on(canonical_game_id, key, first_day + offset * _DAY))
            for offset in range(days)
        )


class RecordedHistory:
    """A recorded collection, readable as of any moment it can honestly reconstruct."""

    def __init__(self, report: CollectionReport, publication_lag: Mapping[str, timedelta]) -> None:
        missing = sorted({run.source for run in report.runs} - set(publication_lag))
        if missing:
            raise ValueError(f"no publication lag given for: {', '.join(missing)}")
        if any(lag < timedelta(0) for lag in publication_lag.values()):
            raise ValueError("a publication lag cannot be negative")
        self._report = report
        self.publication_lag = dict(publication_lag)
        observed = [
            point.period_day
            for run in report.runs
            for signal in run.outcomes
            if signal.series is not None
            for point in signal.series.points
            if point.value is not None
        ]
        if not observed:
            raise ValueError("the recording holds no observation")
        self.first_day = min(observed)
        self.last_day = max(observed)

    def can_reconstruct(self, as_of: datetime) -> bool:
        """Whether the recording holds everything that was visible at `as_of`.

        The evaluation day must not precede the recording, and no source may have had a
        period due that the recording does not reach.
        """
        if evaluation_day_at(as_of) < self.first_day:
            return False
        return all(
            latest_due_at(as_of, Granularity.DAILY, lag).date() <= self.last_day
            for lag in self.publication_lag.values()
        )

    def _visible(self, signal: Signal, source: str, as_of: datetime) -> Signal:
        series = signal.series
        if series is None:
            return signal
        window = window_ending(evaluation_day_at(as_of))
        due = latest_due_at(as_of, signal.key.granularity, self.publication_lag[source])
        kept = [
            point
            for point in series.points
            if point.value is not None
            and window.contains(point.period_start)
            and point.period_start <= due
        ]
        rebuilt = build_time_series(
            signal.canonical_game_id,
            signal.key,
            window,
            kept,
            observed_at=as_of,
            latest_due=min(due, window.end),
        )
        return signal.model_copy(update={"series": rebuilt})

    def inputs_at(self, as_of: datetime) -> PredictionInputs:
        """The report as it would have been at the simulated moment `as_of`."""
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        if not self.can_reconstruct(as_of):
            raise ValueError(f"{as_of.isoformat()} cannot be reconstructed from the recording")
        day = evaluation_day_at(as_of)
        runs = tuple(
            SourceRun(
                source=run.source,
                started_at=as_of,
                finished_at=as_of,
                outcomes=tuple(self._visible(signal, run.source, as_of) for signal in run.outcomes),
            )
            for run in self._report.runs
        )
        inputs = PredictionInputs(
            as_of=as_of,
            evaluation_day=day,
            publication_lag=dict(self.publication_lag),
            report=CollectionReport(window=window_ending(day), observed_at=as_of, runs=runs),
        )
        ensure_available(inputs)
        return inputs

    def future(self) -> FutureValues:
        """The outcome side. Never passed to anything that predicts."""
        values: dict[tuple[str, SignalKey], dict[date, float]] = {}
        for run in self._report.runs:
            for signal in run.outcomes:
                if signal.series is None:
                    continue
                values[(signal.canonical_game_id, signal.key)] = {
                    point.period_day: point.value
                    for point in signal.series.points
                    if point.value is not None
                }
        return FutureValues(values, self.last_day)
