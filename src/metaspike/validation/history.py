"""Recorded history, split into what a past moment knew and what came after it.

One recording, two views that never meet:

- `inputs_at(cutoff)` is the PREDICTION side: a collection report rebuilt as it would
  have been at the end of `cutoff`, holding no observation after that day. It is the
  only thing the scoring pipeline is ever given.
- `future()` is the OUTCOME side: forward values after a cutoff, for labelling. It is a
  different object of a different type, and nothing on the prediction side accepts it.

A report is rebuilt for each cutoff from the raw observations. Features are never
computed on the whole history and sliced, which is how future information leaks into a
past baseline or percentile.

Point in time is by period. A daily value is taken as known once its day has ended;
nothing later is. A recording made after the fact cannot show a value the source later
revised, so a revision is not reconstructable from it. That is a limit of a recording,
stated here and in the report, and not of the method: a store that keeps what was
observed when gives the same interface with revisions respected.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from metaspike.domain.signals import Signal, SignalKey
from metaspike.domain.timeseries import start_of_day
from metaspike.pipeline.collection import CollectionReport, SourceRun, window_ending
from metaspike.pipeline.normalize import build_time_series, latest_due_period

_DAY = timedelta(days=1)


class LeakageError(Exception):
    """A prediction was about to see something from after its cutoff."""


@dataclass(frozen=True)
class PredictionInputs:
    """Everything a prediction at the end of `cutoff_day` may use, and nothing else."""

    cutoff_day: date
    # The moment the snapshot speaks for: the first instant after the cutoff day.
    as_of: datetime
    report: CollectionReport


def as_of_for(cutoff_day: date) -> datetime:
    return start_of_day(cutoff_day) + _DAY


def ensure_no_future(inputs: PredictionInputs) -> None:
    """Refuse inputs that hold anything after their own cutoff. Called before scoring."""
    report = inputs.report
    if report.window.end.astimezone(UTC).date() > inputs.cutoff_day:
        raise LeakageError("the report's window runs past the cutoff day")
    if report.observed_at > inputs.as_of:
        raise LeakageError("the report claims to have been observed after the snapshot")
    for run in report.runs:
        for signal in run.outcomes:
            if signal.series is None:
                continue
            for point in signal.series.points:
                if point.period_day > inputs.cutoff_day:
                    raise LeakageError(
                        f"{signal.canonical_game_id}: a point for {point.period_day} is after "
                        f"the cutoff {inputs.cutoff_day}"
                    )


class FutureValues:
    """The OUTCOME side: what a signal did after a cutoff. Labelling only."""

    def __init__(
        self, values: Mapping[tuple[str, SignalKey], Mapping[date, float]], data_through: date
    ) -> None:
        self._values = values
        self.data_through = data_through

    def after(
        self, canonical_game_id: str, key: SignalKey, cutoff_day: date, days: int
    ) -> tuple[tuple[date, float | None], ...]:
        """The `days` days after the cutoff, each with its value or None if not observed."""
        series = self._values.get((canonical_game_id, key), {})
        return tuple(
            (cutoff_day + offset * _DAY, series.get(cutoff_day + offset * _DAY))
            for offset in range(1, days + 1)
        )


class RecordedHistory:
    """A recorded collection, readable as of any day inside it."""

    def __init__(
        self,
        report: CollectionReport,
        publication_lag: Mapping[str, timedelta] | None = None,
    ) -> None:
        self._report = report
        self._lag = dict(publication_lag or {})
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

    def _truncated(self, signal: Signal, source: str, cutoff: date, as_of: datetime) -> Signal:
        series = signal.series
        if series is None:
            return signal
        window = window_ending(cutoff)
        kept = [
            point
            for point in series.points
            if point.value is not None and window.contains(point.period_start)
        ]
        due = latest_due_period(signal.key.granularity, as_of, self._lag.get(source, _DAY))
        rebuilt = build_time_series(
            signal.canonical_game_id,
            signal.key,
            window,
            kept,
            observed_at=as_of,
            latest_due=min(due, window.end),
        )
        return signal.model_copy(update={"series": rebuilt})

    def inputs_at(self, cutoff_day: date) -> PredictionInputs:
        """The report as it would have been at the end of `cutoff_day`."""
        if not self.first_day <= cutoff_day <= self.last_day:
            raise ValueError(f"{cutoff_day} is outside the recording")
        as_of = as_of_for(cutoff_day)
        runs = tuple(
            SourceRun(
                source=run.source,
                started_at=as_of,
                finished_at=as_of,
                outcomes=tuple(
                    self._truncated(signal, run.source, cutoff_day, as_of)
                    for signal in run.outcomes
                ),
            )
            for run in self._report.runs
        )
        inputs = PredictionInputs(
            cutoff_day=cutoff_day,
            as_of=as_of,
            report=CollectionReport(window=window_ending(cutoff_day), observed_at=as_of, runs=runs),
        )
        ensure_no_future(inputs)
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
