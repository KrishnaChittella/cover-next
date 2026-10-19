"""The limits a tick works within. One place, per source, changed by a reviewed commit.

Every number here is a ceiling enforced in code. The control record can lower a
ceiling for a while; it can never raise one.
"""

from collections.abc import Mapping

from pydantic import Field

from metaspike.domain.model import FrozenModel
from metaspike.scheduling.tiers import DEFAULT_CAPS, Tier
from metaspike.sources.wikimedia import SOURCE_ID as WIKIMEDIA

# A collector may retry a request. An execution's request ceiling must leave room for
# that, so the targets it is given are the ceiling divided by this.
RETRY_ALLOWANCE = 1.5


class SourceCaps(FrozenModel):
    # The Cloud Run job that collects this source.
    collector_job: str
    max_targets_per_execution: int = Field(default=600, ge=1)
    max_requests_per_execution: int = Field(default=1000, ge=1)
    max_requests_per_day: int = Field(ge=1)
    max_executions_per_tick: int = Field(default=1, ge=0, le=1)
    # Deferred work kept for later ticks. Beyond this the oldest is given up.
    max_backlog: int = Field(default=3000, ge=0)
    # A work order is useless after this long. Shorter than a tick, so an order
    # never outlives the tick that would replace it.
    order_ttl_minutes: int = Field(default=50, ge=1)
    breaker_failures: int = Field(default=3, ge=1)
    cooldown_initial_minutes: int = Field(default=60, ge=1)
    cooldown_max_minutes: int = Field(default=1440, ge=1)
    probe_targets: int = Field(default=5, ge=1)
    stale_after_hours: int = Field(default=36, ge=1)
    # Period sources only.
    steady_window_days: int = Field(default=4, ge=1)
    reconciliation_window_days: int = Field(default=14, ge=1)
    bootstrap_window_days: int = Field(default=90, ge=1)
    max_bootstraps_per_execution: int = Field(default=200, ge=0)

    @property
    def targets_per_execution(self) -> int:
        """Targets one execution may be given, leaving room for retries."""
        return min(
            self.max_targets_per_execution,
            int(self.max_requests_per_execution / RETRY_ALLOWANCE),
        )


class DispatchConfig(FrozenModel):
    # Minutes between scheduler ticks. Hourly while the only source is daily.
    tick_minutes: int = Field(default=60, ge=15)
    max_job_starts_per_tick: int = Field(default=6, ge=0)
    max_executions_per_day: int = Field(default=120, ge=0)
    # UTC hour at which the first cohort of a period source is due.
    period_hour: int = Field(default=12, ge=0, le=23)
    # Monday: the daily run that day uses the reconciliation window.
    reconciliation_weekday: int = Field(default=0, ge=0, le=6)
    # UTC hour at which the schedule is regenerated from the targets document.
    schedule_hour: int = Field(default=2, ge=0, le=23)
    warehouse_job: str = "ms-warehouse"
    # UTC hour of the warehouse run that happens whether or not anything landed.
    warehouse_daily_hour: int = Field(default=4, ge=0, le=23)
    long_tail_cohorts: int = Field(default=24, ge=1, le=24)
    max_period_cohorts: int = Field(default=24, ge=1, le=24)
    max_pins: int = Field(default=50, ge=0)
    tier_caps: Mapping[Tier, int] = DEFAULT_CAPS
    # Shadow proposals are recorded and change nothing. Simulation may set this.
    apply_shadow_tiers: bool = False
    sources: Mapping[str, SourceCaps]


PRODUCTION = DispatchConfig(
    sources={
        WIKIMEDIA: SourceCaps(collector_job="ms-wikimedia", max_requests_per_day=10_000),
    }
)
