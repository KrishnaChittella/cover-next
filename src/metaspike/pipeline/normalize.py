"""Turn the points a source returned into a gap-free daily series."""

from datetime import UTC, date, datetime, timedelta

from metaspike.domain.observations import (
    DailySeries,
    Freshness,
    Metric,
    Observation,
    ObservationStatus,
    Quality,
    QualityFlag,
)
from metaspike.sources.base import FetchWindow


def build_daily_series(
    canonical_game_id: str,
    source: str,
    metric: Metric,
    window: FetchWindow,
    observations: list[Observation],
    observed_at: datetime,
) -> DailySeries:
    """One point per day of the window. Days the source omitted become MISSING points.

    A missing day is never filled with zero: the source omitting a day does not tell
    us that nothing happened on it.
    """
    by_day: dict[date, Observation] = {}
    for observation in observations:
        day = observation.period_start
        if day in by_day:
            raise ValueError(f"{canonical_game_id}/{source}: duplicate observation for {day}")
        if not window.start <= day <= window.end:
            raise ValueError(f"{canonical_game_id}/{source}: observation outside the window")
        by_day[day] = observation

    first = min(by_day, default=None)
    last = max(by_day, default=None)
    points: list[Observation] = []
    for offset in range(window.days):
        day = window.start + timedelta(days=offset)
        point = by_day.get(day)
        if point is None:
            point = _missing(
                canonical_game_id, source, metric, day, observed_at, _gap_flag(day, first, last)
            )
        points.append(point)
    return DailySeries(
        canonical_game_id=canonical_game_id,
        source=source,
        metric=metric,
        window_start=window.start,
        window_end=window.end,
        points=tuple(points),
    )


def _gap_flag(day: date, first: date | None, last: date | None) -> QualityFlag:
    if last is None or day > last:
        return QualityFlag.PENDING_PUBLICATION
    if first is not None and day < first:
        return QualityFlag.BEFORE_FIRST_OBSERVATION
    return QualityFlag.SOURCE_GAP


def _missing(
    canonical_game_id: str,
    source: str,
    metric: Metric,
    day: date,
    observed_at: datetime,
    flag: QualityFlag,
) -> Observation:
    period_end = datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(days=1)
    return Observation(
        canonical_game_id=canonical_game_id,
        source=source,
        metric=metric,
        period_start=day,
        value=None,
        status=ObservationStatus.MISSING,
        observed_at=observed_at,
        freshness=Freshness(
            period_end=period_end,
            lag_hours=round((observed_at - period_end).total_seconds() / 3600, 2),
        ),
        quality=Quality(flags=(flag,)),
    )
