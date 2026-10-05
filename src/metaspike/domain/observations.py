"""Source observations and the gap-free time series built from them."""

from datetime import date, datetime
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from metaspike.domain.games import GameId, NonEmptyStr
from metaspike.domain.metrics import MetricId
from metaspike.domain.model import FrozenModel
from metaspike.domain.timeseries import Granularity


class ObservationStatus(StrEnum):
    OBSERVED = "observed"
    MISSING = "missing"


class QualityFlag(StrEnum):
    # The source returned no row for a period between two periods it did report.
    SOURCE_GAP = "source_gap"
    # The period is after the last period the source has published so far.
    PENDING_PUBLICATION = "pending_publication"
    # The period is before the first period the source reported in the window.
    BEFORE_FIRST_OBSERVATION = "before_first_observation"


class Freshness(FrozenModel):
    """How old the measured period was when we collected it."""

    period_end: AwareDatetime
    lag_hours: float


class Quality(FrozenModel):
    flags: tuple[QualityFlag, ...] = ()


class Observation(FrozenModel):
    """One metric value for one game for one period, or an explicit absence of one."""

    canonical_game_id: GameId
    source: NonEmptyStr
    metric: MetricId
    granularity: Granularity
    period_start: AwareDatetime
    value: float | None = Field(default=None, ge=0)
    status: ObservationStatus
    observed_at: AwareDatetime
    source_timestamp: AwareDatetime | None = None
    freshness: Freshness
    quality: Quality = Quality()
    reference: str | None = None

    @model_validator(mode="after")
    def _check_observation(self) -> Self:
        if (self.value is None) != (self.status is ObservationStatus.MISSING):
            raise ValueError("value must be present exactly when status is 'observed'")
        if not self.granularity.is_aligned(self.period_start):
            raise ValueError(f"period_start is not aligned to a {self.granularity.value} period")
        return self

    @property
    def period_day(self) -> date:
        """UTC date the period starts on."""
        return self.period_start.date()


class TimeSeries(FrozenModel):
    """Exactly one point per period across the window; missing periods are explicit."""

    canonical_game_id: GameId
    source: NonEmptyStr
    metric: MetricId
    granularity: Granularity
    window_start: AwareDatetime
    window_end: AwareDatetime
    # Start of the latest period the source was due to have published when the series
    # was collected. Periods after it are not late, merely not expected yet.
    latest_due: AwareDatetime
    points: tuple[Observation, ...]

    @model_validator(mode="after")
    def _points_cover_window(self) -> Self:
        step = self.granularity.step
        expected = self.granularity.periods_between(self.window_start, self.window_end) + 1
        if expected < 1 or len(self.points) != expected:
            raise ValueError("series must hold exactly one point per period of its window")
        if not self.granularity.is_aligned(self.window_start):
            raise ValueError("series window is not aligned to its granularity")
        if self.latest_due > self.window_end:
            raise ValueError("latest_due cannot be after the end of the window")
        for offset, point in enumerate(self.points):
            if point.period_start != self.window_start + offset * step:
                raise ValueError("series points must be contiguous and ordered by period")
            if (
                point.canonical_game_id != self.canonical_game_id
                or point.source != self.source
                or point.metric != self.metric
                or point.granularity is not self.granularity
            ):
                raise ValueError("series points must share the series' game, source and metric")
        return self

    def values(self) -> list[float | None]:
        return [point.value for point in self.points]

    def period_start_at(self, index: int) -> datetime:
        return self.points[index].period_start
