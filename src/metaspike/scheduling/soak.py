"""A finite soak's schedule, worked out before anything is unpaused. Nothing is fetched.

A soak is a run of whole UTC days inside one calendar month on which the scheduler
fires every hour. Terraform turns the same three numbers into the scheduler's cron
expression (`infra/prod/jobs.tf`); this module is where that expression is checked
against a calendar, so the number of ticks is known when the plan is reviewed and the
execution ceiling can be derived from it.

Cloud Scheduler's cron has no year. A window of days in a month is therefore NOT a
one-shot schedule: the same expression is eligible again on the same dates a year
later. Three things bound a soak to its year, none of them the cron: the control
record's `armed_until`, which carries a year and fails closed; the silence between the
window's end and its recurrence, which `ticks_between` measures; and the reconciliation
after the soak, which pauses the scheduler and removes the window.
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

from pydantic import Field, model_validator

from metaspike.domain.model import FrozenModel

HOUR = timedelta(hours=1)
# February is 28 days in every year, as in Terraform, so a window never needs a leap year.
DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
UNBOUNDED = "0 * * * *"


class SoakWindow(FrozenModel):
    """The days of one month. `year` is the year the soak is meant for; the cron has none."""

    year: int = Field(ge=2026, le=2100)
    month: int = Field(ge=1, le=12)
    start_day: int = Field(ge=1, le=31)
    end_day: int = Field(ge=1, le=31)

    @model_validator(mode="after")
    def _inside_one_month(self) -> "SoakWindow":
        if self.end_day < self.start_day:
            raise ValueError(
                "the window ends before it starts; a window that wraps into another month "
                "is not supported"
            )
        if self.end_day > DAYS_IN_MONTH[self.month - 1]:
            raise ValueError("the window must lie inside one calendar month")
        return self

    @property
    def days(self) -> int:
        return self.end_day - self.start_day + 1

    @property
    def cron(self) -> str:
        """Exactly what `local.dispatch_schedule` produces for the same numbers."""
        days = str(self.start_day) if self.days == 1 else f"{self.start_day}-{self.end_day}"
        return f"0 * {days} {self.month} *"

    @property
    def first_tick(self) -> datetime:
        return datetime(self.year, self.month, self.start_day, 0, tzinfo=UTC)

    @property
    def last_tick(self) -> datetime:
        return datetime(self.year, self.month, self.end_day, 23, tzinfo=UTC)

    @property
    def tick_count(self) -> int:
        return 24 * self.days

    @property
    def recurs_at(self) -> datetime:
        """The first tick the same expression allows after this window: a year on."""
        return self.first_tick.replace(year=self.year + 1)

    def execution_ceiling(self, child_executions_per_day: int) -> int:
        """Every tick is one dispatcher; the dispatcher starts at most this many children
        in a UTC day (the control record's `max_executions_per_day`, enforced in the tick).
        """
        if child_executions_per_day < 0:
            raise ValueError("a number of executions cannot be negative")
        return self.tick_count + self.days * child_executions_per_day


def _field(expression: str, low: int, high: int) -> frozenset[int]:
    """One cron field: `*`, a number, a range, or a comma-separated list of those."""
    if expression == "*":
        return frozenset(range(low, high + 1))
    allowed: set[int] = set()
    for part in expression.split(","):
        first, _, last = part.partition("-")
        start, end = int(first), int(last or first)
        if not low <= start <= end <= high:
            raise ValueError(f"cron field out of range: {expression!r}")
        allowed.update(range(start, end + 1))
    return frozenset(allowed)


def matches(cron: str, moment: datetime) -> bool:
    """Whether a five-field cron fires at this UTC minute.

    Only the forms this project produces are read: no steps, no names. A day-of-week
    other than `*` is refused, because cron then ORs it with the day of the month and a
    window stops being a window.
    """
    fields = cron.split()
    if len(fields) != 5:
        raise ValueError(f"not a five-field cron expression: {cron!r}")
    minute, hour, day, month, weekday = fields
    if weekday != "*":
        raise ValueError("a day-of-week field is not supported")
    moment = moment.astimezone(UTC)
    return (
        moment.minute in _field(minute, 0, 59)
        and moment.hour in _field(hour, 0, 23)
        and moment.day in _field(day, 1, 31)
        and moment.month in _field(month, 1, 12)
    )


def is_finite(cron: str) -> bool:
    """A schedule is finite when it names both its days and its month."""
    fields = cron.split()
    return len(fields) == 5 and fields[2] != "*" and fields[3] != "*" and fields[4] == "*"


def ticks_between(cron: str, start: datetime, end: datetime) -> Iterator[datetime]:
    """Every hourly tick the expression allows from `start` up to but not including `end`."""
    moment = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    if moment < start:
        moment += HOUR
    while moment < end:
        if matches(cron, moment):
            yield moment
        moment += HOUR


def load_days_to_reach(run_day: date, oldest_folder: date) -> int:
    """The `METASPIKE_LOAD_DAYS` a warehouse run on `run_day` needs to reach a landing
    folder dated `oldest_folder`. The loader walks today and the days before it, so
    three reaches back two days.
    """
    if oldest_folder > run_day:
        raise ValueError("the folder is dated after the run")
    return (run_day - oldest_folder).days + 1
