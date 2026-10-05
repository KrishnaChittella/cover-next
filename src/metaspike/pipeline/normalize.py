"""Turn the points a source returned into a gap-free time series."""

from datetime import datetime, timedelta

from metaspike.domain.observations import (
    Freshness,
    Observation,
    ObservationStatus,
    Quality,
    QualityFlag,
    TimeSeries,
)
from metaspike.domain.signals import SignalKey
from metaspike.domain.timeseries import Granularity, TimeWindow


def build_time_series(
    canonical_game_id: str,
    key: SignalKey,
    window: TimeWindow,
    observations: list[Observation],
    observed_at: datetime,
    latest_due: datetime | None = None,
) -> TimeSeries:
    """One point per period of the window. Periods the source omitted become MISSING.

    A missing period is never filled with zero: the source omitting a period does not
    tell us that nothing happened in it. `latest_due` is the latest period the source
    was expected to have published by now; it defaults to the end of the window.
    """
    label = f"{canonical_game_id}/{key.source}/{key.metric}"
    by_period: dict[datetime, Observation] = {}
    for observation in observations:
        period = observation.period_start
        if observation.metric != key.metric or observation.granularity is not key.granularity:
            raise ValueError(f"{label}: observation belongs to a different signal")
        if period in by_period:
            raise ValueError(f"{label}: duplicate observation for {period.isoformat()}")
        if not window.contains(period):
            raise ValueError(f"{label}: observation outside the window")
        by_period[period] = observation

    first = min(by_period, default=None)
    last = max(by_period, default=None)
    points: list[Observation] = []
    for period in window.period_starts(key.granularity):
        point = by_period.get(period)
        if point is None:
            point = _missing(
                canonical_game_id, key, period, observed_at, _gap_flag(period, first, last)
            )
        points.append(point)
    return TimeSeries(
        canonical_game_id=canonical_game_id,
        source=key.source,
        metric=key.metric,
        granularity=key.granularity,
        window_start=window.start,
        window_end=window.end,
        latest_due=window.end if latest_due is None else min(latest_due, window.end),
        points=tuple(points),
    )


def latest_due_period(
    granularity: Granularity, observed_at: datetime, publication_lag: timedelta
) -> datetime:
    """Start of the latest period whose data the source should have published by now.

    A period is due once it has ended and the source's publication lag has passed.
    """
    return granularity.floor(observed_at - granularity.step - publication_lag)


def _gap_flag(period: datetime, first: datetime | None, last: datetime | None) -> QualityFlag:
    if last is None or period > last:
        return QualityFlag.PENDING_PUBLICATION
    if first is not None and period < first:
        return QualityFlag.BEFORE_FIRST_OBSERVATION
    return QualityFlag.SOURCE_GAP


def _missing(
    canonical_game_id: str,
    key: SignalKey,
    period: datetime,
    observed_at: datetime,
    flag: QualityFlag,
) -> Observation:
    period_end = period + key.granularity.step
    return Observation(
        canonical_game_id=canonical_game_id,
        source=key.source,
        metric=key.metric,
        granularity=key.granularity,
        period_start=period,
        value=None,
        status=ObservationStatus.MISSING,
        observed_at=observed_at,
        freshness=Freshness(
            period_end=period_end,
            lag_hours=round((observed_at - period_end).total_seconds() / 3600, 2),
        ),
        quality=Quality(flags=(flag,)),
    )
