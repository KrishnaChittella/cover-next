"""Aggregation of sampled concurrent players: slots to hours, hours to days.

Concurrent players are a LEVEL sampled over time. They are averaged, their peak is
taken and their median is taken. They are never summed across time: adding the
players seen at 12:00 to the players seen at 12:15 counts the same people twice and
means nothing. Nothing here is a count of daily players, daily active users or total
players, and nothing here may be labelled as one.

Only resolved logical slots are aggregated: one selected observation per slot, or
none. An hour or a day that does not have enough of them has no value, and says how
many it had.
"""

import statistics
from collections.abc import Iterable
from datetime import datetime, timedelta

from pydantic import AwareDatetime

from metaspike.domain import baseline_rules as rules
from metaspike.domain.model import FrozenModel
from metaspike.domain.samples import SlotResolution
from metaspike.domain.timeseries import Granularity
from metaspike.features.engine import Bucket

WORDING = "concurrent players sampled over time"
_HOUR = timedelta(hours=1)
_DAY = timedelta(days=1)


class HourlyAggregate(FrozenModel):
    start: AwareDatetime
    end: AwareDatetime
    # Mean and maximum of the hour's valid samples; None unless the hour is valid.
    mean: float | None = None
    maximum: float | None = None
    valid_count: int
    expected_count: int
    coverage: float
    valid: bool
    rule_version: str


class DailyAggregate(FrozenModel):
    start: AwareDatetime
    end: AwareDatetime
    # All three are taken over the day's VALID HOURLY MEANS; None unless the day is valid.
    peak: float | None = None
    typical: float | None = None
    mean: float | None = None
    valid_hours: int
    expected_hours: int
    hour_coverage: float
    valid_samples: int
    expected_samples: int
    coverage: float
    valid: bool
    rule_version: str


class CurrentLevel(FrozenModel):
    """The latest selected valid observation: the truthful 'now'."""

    value: int
    observed_at: AwareDatetime
    slot_start: AwareDatetime


def current_level(slots: Iterable[SlotResolution]) -> CurrentLevel | None:
    observed = [slot.selected for slot in slots if slot.selected is not None]
    if not observed:
        return None
    latest = max(observed, key=lambda attempt: (attempt.observed_at, attempt.attempt_id))
    assert latest.value is not None
    return CurrentLevel(
        value=latest.value, observed_at=latest.observed_at, slot_start=latest.slot_start
    )


def hourly_aggregates(
    slots: Iterable[SlotResolution], first_hour: datetime, last_hour: datetime
) -> tuple[HourlyAggregate, ...]:
    """One aggregate per hour from `first_hour` to `last_hour` inclusive, in order."""
    if not (Granularity.HOURLY.is_aligned(first_hour) and Granularity.HOURLY.is_aligned(last_hour)):
        raise ValueError("hours must start on the hour")
    by_hour: dict[datetime, list[int]] = {}
    for slot in slots:
        if slot.granularity is not Granularity.FIFTEEN_MINUTES:
            raise ValueError("hourly aggregation is defined for 15-minute slots only")
        if slot.value is not None:
            by_hour.setdefault(Granularity.HOURLY.floor(slot.key.slot_start), []).append(slot.value)
    hours = []
    moment = first_hour
    while moment <= last_hour:
        values = by_hour.get(moment, [])
        valid = len(values) >= rules.MIN_SAMPLES_PER_VALID_HOUR
        hours.append(
            HourlyAggregate(
                start=moment,
                end=moment + _HOUR,
                mean=statistics.fmean(values) if valid else None,
                maximum=float(max(values)) if valid else None,
                valid_count=len(values),
                expected_count=rules.EXPECTED_SAMPLES_PER_HOUR,
                coverage=round(len(values) / rules.EXPECTED_SAMPLES_PER_HOUR, 4),
                valid=valid,
                rule_version=rules.BASELINE_RULES_VERSION,
            )
        )
        moment += _HOUR
    return tuple(hours)


def daily_aggregates(hours: Iterable[HourlyAggregate]) -> tuple[DailyAggregate, ...]:
    """One aggregate per UTC day that the hours touch, in order."""
    by_day: dict[datetime, list[HourlyAggregate]] = {}
    for hour in hours:
        by_day.setdefault(Granularity.DAILY.floor(hour.start), []).append(hour)
    days = []
    for start in sorted(by_day):
        members = by_day[start]
        means = [hour.mean for hour in members if hour.valid and hour.mean is not None]
        samples = sum(hour.valid_count for hour in members)
        valid = (
            len(means) >= rules.MIN_VALID_HOURS_PER_VALID_DAY
            and samples >= rules.MIN_SAMPLES_PER_VALID_DAY
        )
        days.append(
            DailyAggregate(
                start=start,
                end=start + _DAY,
                peak=max(means) if valid else None,
                typical=statistics.median(means) if valid else None,
                mean=statistics.fmean(means) if valid else None,
                valid_hours=len(means),
                expected_hours=24,
                hour_coverage=round(len(means) / 24, 4),
                valid_samples=samples,
                expected_samples=rules.EXPECTED_SAMPLES_PER_DAY,
                coverage=round(samples / rules.EXPECTED_SAMPLES_PER_DAY, 4),
                valid=valid,
                rule_version=rules.BASELINE_RULES_VERSION,
            )
        )
    return tuple(days)


def hourly_buckets(hours: Iterable[HourlyAggregate]) -> list[Bucket]:
    """The hourly means as engine buckets. An invalid hour is a missing bucket."""
    return [
        Bucket(
            start=hour.start,
            value=hour.mean,
            samples=hour.valid_count,
            expected_samples=hour.expected_count,
        )
        for hour in hours
    ]
