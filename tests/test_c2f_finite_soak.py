"""A finite soak's schedule: the ticks it allows, and the ones it must not.

The cron expression is produced twice, by Terraform (`infra/prod/jobs.tf`) and by
`metaspike.scheduling.soak`. The Terraform tests pin the first to literal strings; the
tests here pin the second to the same strings and then walk a calendar with it.
"""

import re
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest
from pydantic import ValidationError

from metaspike.scheduling.soak import (
    UNBOUNDED,
    SoakWindow,
    is_finite,
    load_days_to_reach,
    matches,
    ticks_between,
)

REPO = Path(__file__).parent.parent
PROD = REPO / "infra" / "prod"
WEEK = SoakWindow(year=2026, month=10, start_day=12, end_day=18)


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def year_of_ticks(window: SoakWindow) -> list[datetime]:
    start = utc(window.year, 1, 1)
    return list(ticks_between(window.cron, start, utc(window.year + 1, 1, 1)))


def test_the_cron_is_the_one_terraform_produces() -> None:
    assert WEEK.cron == "0 * 12-18 10 *"
    assert SoakWindow(year=2027, month=2, start_day=28, end_day=28).cron == "0 * 28 2 *"
    # The same two windows and strings are asserted in the Terraform tests.
    tests = (PROD / "tests" / "c2_automation.tftest.hcl").read_text()
    assert "{ month = 10, start_day = 12, end_day = 18 }" in tests
    assert 'schedule == "0 * 12-18 10 *"' in tests and 'schedule == "0 * 28 2 *"' in tests
    # And Terraform builds it from the same parts in the same order.
    jobs = (PROD / "jobs.tf").read_text()
    assert '"0 * ${local.soak_days} ${var.soak_window.month} *"' in jobs
    assert '"${var.soak_window.start_day}-${var.soak_window.end_day}"' in jobs
    assert "schedule    = local.dispatch_schedule" in jobs


def test_every_hour_inside_the_window_is_a_tick() -> None:
    ticks = year_of_ticks(WEEK)
    assert len(ticks) == WEEK.tick_count == 168  # seven days of 24
    assert ticks[0] == WEEK.first_tick == utc(2026, 10, 12, 0)
    assert ticks[-1] == WEEK.last_tick == utc(2026, 10, 18, 23)
    # Consecutive hours with nothing missing and nothing doubled.
    assert all(later - earlier == timedelta(hours=1) for earlier, later in pairwise(ticks))
    assert len(set(ticks)) == 168
    # On the hour only.
    assert not matches(WEEK.cron, utc(2026, 10, 14, 9, 30))
    assert matches(WEEK.cron, utc(2026, 10, 14, 9, 0))


def test_nothing_fires_just_before_the_window() -> None:
    assert not matches(WEEK.cron, utc(2026, 10, 11, 23))
    assert not list(ticks_between(WEEK.cron, utc(2026, 1, 1), WEEK.first_tick))


def test_nothing_fires_after_the_window_in_the_same_year() -> None:
    assert not matches(WEEK.cron, utc(2026, 10, 19, 0))
    after = WEEK.last_tick + timedelta(hours=1)
    assert not list(ticks_between(WEEK.cron, after, utc(2027, 1, 1)))
    # The immediate post-soak period, hour by hour for thirty days.
    assert not list(ticks_between(WEEK.cron, after, after + timedelta(days=30)))


def test_the_window_is_not_one_shot_it_recurs_a_year_later() -> None:
    # Cron has no year. This is the property the safeguards exist for, stated as a test
    # so that nobody later describes the schedule as year-bounded.
    assert WEEK.recurs_at == utc(2027, 10, 12, 0)
    after = WEEK.last_tick + timedelta(hours=1)
    assert not list(ticks_between(WEEK.cron, after, WEEK.recurs_at))  # 358 silent days
    assert (WEEK.recurs_at - after).days == 358
    assert matches(WEEK.cron, WEEK.recurs_at)
    again = list(ticks_between(WEEK.cron, utc(2027, 1, 1), utc(2028, 1, 1)))
    assert len(again) == 168 and again[0] == utc(2027, 10, 12, 0)


@pytest.mark.parametrize(
    ("window", "count"),
    [
        (SoakWindow(year=2026, month=10, start_day=12, end_day=18), 168),
        (SoakWindow(year=2026, month=11, start_day=1, end_day=7), 168),
        (SoakWindow(year=2026, month=12, start_day=25, end_day=31), 168),
        (SoakWindow(year=2027, month=2, start_day=22, end_day=28), 168),
        (SoakWindow(year=2028, month=2, start_day=22, end_day=28), 168),  # a leap year
        (SoakWindow(year=2026, month=10, start_day=30, end_day=30), 24),
        (SoakWindow(year=2026, month=4, start_day=1, end_day=30), 720),
    ],
)
def test_the_tick_count_is_exact_for_any_window(window: SoakWindow, count: int) -> None:
    ticks = year_of_ticks(window)
    assert len(ticks) == window.tick_count == count
    assert (ticks[0], ticks[-1]) == (window.first_tick, window.last_tick)
    assert is_finite(window.cron)
    after = window.last_tick + timedelta(hours=1)
    assert not list(ticks_between(window.cron, after, after + timedelta(days=30)))
    assert not list(ticks_between(window.cron, after, window.recurs_at))


def test_a_window_that_ends_before_it_starts_is_refused() -> None:
    with pytest.raises(ValidationError, match="ends before it starts"):
        SoakWindow(year=2026, month=10, start_day=18, end_day=12)


@pytest.mark.parametrize(
    ("month", "start", "end"),
    [(11, 28, 31), (2, 23, 29), (4, 25, 31), (10, 28, 34)],
)
def test_a_window_that_leaves_its_month_is_refused(month: int, start: int, end: int) -> None:
    with pytest.raises(ValidationError):
        SoakWindow(year=2026, month=month, start_day=start, end_day=end)
    # There is no way to say "28 October to 3 November": one month, and end after start.
    with pytest.raises(ValidationError):
        SoakWindow(year=2026, month=10, start_day=28, end_day=3)


def test_an_unbounded_schedule_is_not_a_finite_one() -> None:
    assert not is_finite(UNBOUNDED)
    assert not is_finite("0 * 12-18 * *")  # days of every month
    assert not is_finite("0 * * 10 *")  # every day of one month is still named by month only
    assert is_finite(WEEK.cron)
    # Unbounded means what it says: 8,760 ticks in a year.
    assert len(list(ticks_between(UNBOUNDED, utc(2026, 1, 1), utc(2027, 1, 1)))) == 8760


def test_terraform_refuses_to_unpause_without_a_window_in_finite_soak_mode() -> None:
    jobs = (PROD / "jobs.tf").read_text()
    variables = (PROD / "variables.tf").read_text()
    tick = re.search(r'resource "google_cloud_scheduler_job" "tick" \{(.*?)\n\}', jobs, re.S)
    assert tick and tick.group(1).count("precondition {") == 4
    assert "!(local.finite_soak && var.soak_window == null)" in tick.group(1)
    assert "!(var.scheduler_paused && (var.soak_window != null" in tick.group(1)
    assert '!(var.schedule_mode == "steady_state" && (var.soak_window != null' in tick.group(1)
    mode = re.search(r'variable "schedule_mode" \{(.*?)\n\}', variables, re.S)
    assert mode and 'default     = "finite_soak"' in mode.group(1)
    window = re.search(r'variable "soak_window" \{(.*?)\n\}\n', variables, re.S)
    assert window and "default = null" in window.group(1)
    tests = (PROD / "tests" / "c2_automation.tftest.hcl").read_text()
    for run in (
        "unpausing_is_refused_without_a_finite_window",
        "a_window_that_ends_before_it_starts_is_refused",
        "a_window_that_runs_past_the_month_is_refused",
        "a_paused_scheduler_carries_no_window",
        "steady_state_is_separate_and_takes_no_window",
        "steady_state_is_the_unbounded_schedule_with_the_silence_alert",
    ):
        assert f'run "{run}"' in tests, run


def test_the_deploy_workflow_cannot_choose_steady_state() -> None:
    deploy = (REPO / ".github" / "workflows" / "deploy.yml").read_text()
    assert "schedule_mode" not in deploy and "steady_state" not in deploy
    assert "scheduler_paused=false" not in deploy.replace(" ", "")
    # The window comes from repository variables, all of them or none.
    for name in ("SOAK_MONTH", "SOAK_START_DAY", "SOAK_END_DAY", "SOAK_DAILY_EXECUTION_CEILING"):
        assert f"vars.{name}" in deploy, name
    assert "soak_window={month=" in deploy and "soak_daily_execution_ceiling=" in deploy


def test_a_day_of_week_is_refused_because_it_would_widen_the_window() -> None:
    with pytest.raises(ValueError, match="day-of-week"):
        matches("0 * 12-18 10 1", utc(2026, 10, 12))
    with pytest.raises(ValueError, match="five-field"):
        matches("0 * 12-18 10", utc(2026, 10, 12))


def test_the_execution_ceiling_follows_from_the_ticks_and_the_daily_child_cap() -> None:
    # 168 dispatchers, and at most three children a day for seven days.
    assert WEEK.execution_ceiling(child_executions_per_day=3) == 168 + 21
    assert WEEK.execution_ceiling(child_executions_per_day=0) == 168
    with pytest.raises(ValueError):
        WEEK.execution_ceiling(child_executions_per_day=-1)
    # The daily cap is enforced by the tick, not assumed.
    tick = (REPO / "src" / "metaspike" / "scheduling" / "tick.py").read_text()
    assert "record.max_executions_per_day" in tick and "per_day - counters.executions" in tick


def test_the_load_window_needed_to_reach_the_pending_run_logs() -> None:
    pending = date(2026, 10, 9)  # the folder holding the 32 unloaded C2-D run-log rows
    assert load_days_to_reach(date(2026, 10, 9), pending) == 1
    assert load_days_to_reach(date(2026, 10, 11), pending) == 3  # the ordinary window, just
    assert load_days_to_reach(date(2026, 10, 12), pending) == 4  # from here it must be set
    assert load_days_to_reach(date(2026, 11, 1), pending) == 24
    with pytest.raises(ValueError):
        load_days_to_reach(date(2026, 10, 8), pending)
    # The job reads the setting and defaults to three.
    job = (REPO / "src" / "metaspike" / "jobs" / "warehouse_job.py").read_text()
    assert 'LOAD_DAYS_ENV = "METASPIKE_LOAD_DAYS"' in job
    assert 'runtime.environ.get(LOAD_DAYS_ENV, "3")' in job
