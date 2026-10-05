"""Time primitives shared by every signal: granularity and aligned windows."""

from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, model_validator

from metaspike.domain.model import FrozenModel

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class Granularity(StrEnum):
    """Length of one period. Periods are aligned to the UTC epoch."""

    FIFTEEN_MINUTES = "15min"
    HOURLY = "hourly"
    DAILY = "daily"

    @property
    def step(self) -> timedelta:
        return _STEPS[self]

    def floor(self, moment: datetime) -> datetime:
        """Start of the period that contains `moment`, in UTC."""
        elapsed = moment.astimezone(UTC) - _EPOCH
        return _EPOCH + (elapsed // self.step) * self.step

    def is_aligned(self, moment: datetime) -> bool:
        return self.floor(moment) == moment

    def periods_between(self, start: datetime, end: datetime) -> int:
        """Whole periods from `start` to `end`; negative when `end` is earlier."""
        return (end - start) // self.step


_STEPS = {
    Granularity.FIFTEEN_MINUTES: timedelta(minutes=15),
    Granularity.HOURLY: timedelta(hours=1),
    Granularity.DAILY: timedelta(days=1),
}


def start_of_day(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


class TimeWindow(FrozenModel):
    """Inclusive range of period starts, in UTC."""

    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.start > self.end:
            raise ValueError("window start must not be after its end")
        return self

    @classmethod
    def of_days(cls, start: date, end: date) -> Self:
        """The window whose daily periods run from `start` to `end` inclusive."""
        return cls(start=start_of_day(start), end=start_of_day(end))

    def aligned(self, granularity: Granularity) -> Self:
        """The same window snapped inward to whole periods of `granularity`."""
        start = granularity.floor(self.start)
        if start < self.start:
            start += granularity.step
        return type(self)(start=start, end=granularity.floor(self.end))

    def period_starts(self, granularity: Granularity) -> list[datetime]:
        if not (granularity.is_aligned(self.start) and granularity.is_aligned(self.end)):
            raise ValueError(f"window is not aligned to {granularity.value} periods")
        count = granularity.periods_between(self.start, self.end) + 1
        return [self.start + index * granularity.step for index in range(count)]

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment <= self.end
