"""Source health and the circuit breaker. Pure state transitions, any source.

The dispatcher is the only writer of a source's health. It folds in the outcome of
the last work order it issued (the collector's receipt, or the absence of one after
the order expired) at the start of the next tick.

    closed     work is planned normally
    open       nothing is planned until `reopen_at`
    half open  one small probe is planned; its outcome closes or re-opens

A health state is a state. It is never a number, and a source that is not healthy
contributes no value: missing is not zero.
"""

from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import AwareDatetime, Field

from metaspike.domain.model import FrozenModel
from metaspike.scheduling.config import SourceCaps


class Breaker(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class Outcome(StrEnum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"
    # The order expired and no receipt was ever written.
    MISSING = "missing"


class HealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    DISABLED_BY_POLICY = "disabled_by_policy"
    DISABLED_NO_CREDENTIAL = "disabled_no_credential"
    DISABLED_BY_CONTROL = "disabled_by_control"


class SourceHealth(FrozenModel):
    source: str
    last_attempt: AwareDatetime | None = None
    last_success: AwareDatetime | None = None
    consecutive_failures: int = Field(default=0, ge=0)
    breaker: Breaker = Breaker.CLOSED
    reopen_at: AwareDatetime | None = None
    cooldown_minutes: int = Field(default=0, ge=0)
    last_rate_limited_at: AwareDatetime | None = None
    # The work order whose outcome has not been folded in yet.
    pending_order_id: str | None = None
    pending_expires_at: AwareDatetime | None = None
    last_outcome: Outcome | None = None


def _open(health: SourceHealth, at: datetime, caps: SourceCaps) -> dict[str, object]:
    cooldown = min(
        caps.cooldown_max_minutes,
        max(caps.cooldown_initial_minutes, health.cooldown_minutes * 2),
    )
    return {
        "breaker": Breaker.OPEN,
        "cooldown_minutes": cooldown,
        "reopen_at": at + timedelta(minutes=cooldown),
    }


def apply_outcome(
    health: SourceHealth, outcome: Outcome, at: datetime, caps: SourceCaps
) -> SourceHealth:
    """The health after one execution's outcome is known."""
    update: dict[str, object] = {
        "last_attempt": at,
        "last_outcome": outcome,
        "pending_order_id": None,
        "pending_expires_at": None,
    }
    if outcome in (Outcome.SUCCEEDED, Outcome.PARTIAL):
        update |= {
            "last_success": at,
            "consecutive_failures": 0,
            "breaker": Breaker.CLOSED,
            "reopen_at": None,
            "cooldown_minutes": 0,
        }
    elif outcome is Outcome.RATE_LIMITED:
        # A rate limit opens the breaker at once: asking again is what it forbids.
        update |= {
            "consecutive_failures": health.consecutive_failures + 1,
            "last_rate_limited_at": at,
            **_open(health, at, caps),
        }
    else:
        failures = health.consecutive_failures + 1
        update["consecutive_failures"] = failures
        if health.breaker is Breaker.HALF_OPEN or failures >= caps.breaker_failures:
            update |= _open(health, at, caps)
    return health.model_copy(update=update)


class Gate(StrEnum):
    OPEN_FOR_WORK = "open_for_work"
    PROBE = "probe"
    BLOCKED = "blocked"
    # The last order has neither a receipt nor an expiry yet: its collector may run.
    BUSY = "busy"


def gate(health: SourceHealth, now: datetime) -> tuple[Gate, SourceHealth]:
    """Whether work may be planned for this source now, and the health that follows."""
    if health.pending_order_id is not None:
        return Gate.BUSY, health
    if health.breaker is Breaker.CLOSED:
        return Gate.OPEN_FOR_WORK, health
    if health.breaker is Breaker.HALF_OPEN:
        return Gate.PROBE, health
    if health.reopen_at is not None and now >= health.reopen_at:
        return Gate.PROBE, health.model_copy(update={"breaker": Breaker.HALF_OPEN})
    return Gate.BLOCKED, health


def state_of(
    health: SourceHealth,
    now: datetime,
    caps: SourceCaps,
    *,
    policy_cleared: bool = True,
    credential_present: bool = True,
    control_enabled: bool = True,
) -> HealthState:
    """One word for a source's condition. Disabled outranks everything it explains."""
    if not policy_cleared:
        return HealthState.DISABLED_BY_POLICY
    if not credential_present:
        return HealthState.DISABLED_NO_CREDENTIAL
    if not control_enabled:
        return HealthState.DISABLED_BY_CONTROL
    if health.breaker is not Breaker.CLOSED:
        limited = health.last_outcome is Outcome.RATE_LIMITED
        return HealthState.RATE_LIMITED if limited else HealthState.UNAVAILABLE
    stale_before = now - timedelta(hours=caps.stale_after_hours)
    if health.last_success is not None and health.last_success < stale_before:
        return HealthState.STALE
    if health.consecutive_failures:
        return HealthState.DEGRADED
    return HealthState.HEALTHY
