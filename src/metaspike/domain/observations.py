"""Source observations and the gap-free daily series built from them."""

from datetime import date, timedelta
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from metaspike.domain.games import GameId, NonEmptyStr
from metaspike.domain.model import FrozenModel


class Metric(StrEnum):
    DAILY_PAGEVIEWS = "daily_pageviews"


class ObservationStatus(StrEnum):
    OBSERVED = "observed"
    MISSING = "missing"


class QualityFlag(StrEnum):
    # The source returned no row for a day between two days it did report.
    SOURCE_GAP = "source_gap"
    # The day is after the last day the source has published so far.
    PENDING_PUBLICATION = "pending_publication"
    # The day is before the first day the source reported in the window.
    BEFORE_FIRST_OBSERVATION = "before_first_observation"


class Freshness(FrozenModel):
    """How old the measured period was when we collected it."""

    period_end: AwareDatetime
    lag_hours: float


class Quality(FrozenModel):
    flags: tuple[QualityFlag, ...] = ()


class Observation(FrozenModel):
    """One metric value for one game for one UTC day, or an explicit absence of one."""

    canonical_game_id: GameId
    source: NonEmptyStr
    metric: Metric
    period_start: date
    value: float | None = Field(default=None, ge=0)
    status: ObservationStatus
    observed_at: AwareDatetime
    source_timestamp: AwareDatetime | None = None
    freshness: Freshness
    quality: Quality = Quality()
    reference: str | None = None

    @model_validator(mode="after")
    def _value_matches_status(self) -> Self:
        if (self.value is None) != (self.status is ObservationStatus.MISSING):
            raise ValueError("value must be present exactly when status is 'observed'")
        return self


class DailySeries(FrozenModel):
    """Exactly one point per UTC day across the window; missing days are explicit."""

    canonical_game_id: GameId
    source: NonEmptyStr
    metric: Metric
    window_start: date
    window_end: date
    points: tuple[Observation, ...]

    @model_validator(mode="after")
    def _points_cover_window(self) -> Self:
        expected = (self.window_end - self.window_start).days + 1
        if expected < 1 or len(self.points) != expected:
            raise ValueError("series must hold exactly one point per day of its window")
        for offset, point in enumerate(self.points):
            if point.period_start != self.window_start + timedelta(days=offset):
                raise ValueError("series points must be contiguous and ordered by day")
            if point.canonical_game_id != self.canonical_game_id or point.source != self.source:
                raise ValueError("series points must belong to the series' game and source")
        return self

    def values(self) -> list[float | None]:
        return [point.value for point in self.points]
