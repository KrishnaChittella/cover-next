"""Shared rate limiting, driven entirely by `SourcePolicy.operations`.

One limiter exists per budget group. It enforces, for every request that is actually
transmitted (retries included):

- requests per interval   a token bucket of `max_requests` refilled over `per_seconds`
- concurrency             at most `max_concurrency` requests in flight
- quota                   a per-period allowance; an exhausted quota refuses the request
                          before it is sent and never waits for the period to reset
- back-off                a pause shared by the whole group (Retry-After, 429, 5xx)

State is process-local. The quota ledger is an interface so that a later phase can
swap the local file for shared state, and a dispatcher can hand each job a budget
lease, without any adapter changing.
"""

import contextlib
import json
import math
import os
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from metaspike.domain.source_policy import QuotaPeriod, SourceOperations, SourcePolicy

# Period boundaries are not always documented, so never plan to spend the last tenth.
QUOTA_SAFETY_FRACTION = 0.9


class QuotaExhaustedError(Exception):
    """The budget group's quota for the current period has been spent."""

    def __init__(self, budget_group: str, period: str) -> None:
        super().__init__(f"{budget_group}: quota exhausted for {period}")
        self.budget_group = budget_group
        self.period = period


class Clock:
    """Monotonic time that can also wait.

    With a real sleeper, time is real. With an injected sleeper (tests), time advances
    by exactly what was slept, so limits can be exercised without waiting.
    """

    def __init__(
        self,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sleeper = sleeper
        self._monotonic = monotonic
        self._virtual = sleeper is not time.sleep
        self._offset = 0.0
        self._lock = threading.Lock()

    def now(self) -> float:
        with self._lock:
            return self._monotonic() + self._offset

    def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        self._sleeper(seconds)
        if self._virtual:
            with self._lock:
                self._offset += seconds


class QuotaLedger(Protocol):
    def try_consume(self, budget_group: str, period: str, units: int, limit: int) -> bool:
        """Record `units` against the period if that keeps usage within `limit`."""
        ...

    def used(self, budget_group: str, period: str) -> int: ...


class InMemoryQuotaLedger:
    def __init__(self) -> None:
        self._used: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def try_consume(self, budget_group: str, period: str, units: int, limit: int) -> bool:
        with self._lock:
            used = self._used.get((budget_group, period), 0)
            if used + units > limit:
                return False
            self._used[(budget_group, period)] = used + units
            return True

    def used(self, budget_group: str, period: str) -> int:
        with self._lock:
            return self._used.get((budget_group, period), 0)


class FileQuotaLedger:
    """Quota usage kept in one small JSON file per budget group, so runs share a budget."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._lock = threading.Lock()

    def try_consume(self, budget_group: str, period: str, units: int, limit: int) -> bool:
        with self._lock:
            usage = self._read(budget_group)
            used = usage.get(period, 0)
            if used + units > limit:
                return False
            self._write(budget_group, {period: used + units})  # older periods are dropped
            return True

    def used(self, budget_group: str, period: str) -> int:
        with self._lock:
            return self._read(budget_group).get(period, 0)

    def _path(self, budget_group: str) -> Path:
        if not budget_group.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"unsafe budget group name: {budget_group!r}")
        return self._directory / f"{budget_group}.json"

    def _read(self, budget_group: str) -> dict[str, int]:
        path = self._path(budget_group)
        if not path.is_file():
            return {}
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return {str(period): int(used) for period, used in loaded.items()}

    def _write(self, budget_group: str, usage: dict[str, int]) -> None:
        path = self._path(budget_group)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(usage, stream)
        Path(temporary).replace(path)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RateLimiter:
    def __init__(
        self,
        operations: SourceOperations,
        ledger: QuotaLedger | None = None,
        clock: Clock | None = None,
        wall_clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.budget_group = operations.budget_group
        self._operations = operations
        self._ledger = ledger or InMemoryQuotaLedger()
        self._clock = clock or Clock()
        self._wall_clock = wall_clock
        limit = operations.rate_limit
        self._capacity = float(limit.max_requests)
        self._refill_per_second = limit.max_requests / limit.per_seconds
        self._tokens = self._capacity
        self._refilled_at = self._clock.now()
        self._not_before = 0.0
        self._state = threading.Lock()
        self._slots = threading.BoundedSemaphore(limit.max_concurrency)
        self.transmitted = 0

    @property
    def operations(self) -> SourceOperations:
        return self._operations

    @contextlib.contextmanager
    def slot(self) -> Iterator[None]:
        """Hold one concurrency slot for one request that is about to be transmitted.

        Raises QuotaExhaustedError, without waiting, if the quota is spent.
        """
        with self._slots:
            self._wait_for_turn()
            self._spend_quota()
            with self._state:
                self.transmitted += 1
            yield

    def back_off(self, seconds: float) -> None:
        """Pause the whole budget group, then wait out the pause."""
        with self._state:
            self._not_before = max(self._not_before, self._clock.now() + seconds)
        self._clock.sleep(seconds)

    def quota_remaining(self) -> int | None:
        quota = self._operations.quota
        if quota is None:
            return None
        return max(0, self._quota_limit() - self._ledger.used(self.budget_group, self._period()))

    def _wait_for_turn(self) -> None:
        while True:
            with self._state:
                now = self._clock.now()
                self._tokens = min(
                    self._capacity,
                    self._tokens + (now - self._refilled_at) * self._refill_per_second,
                )
                self._refilled_at = now
                wait = max(0.0, self._not_before - now)
                if wait == 0 and self._tokens >= 1:
                    self._tokens -= 1
                    return
                if wait == 0:
                    wait = (1 - self._tokens) / self._refill_per_second
            self._clock.sleep(wait)

    def _spend_quota(self) -> None:
        if self._operations.quota is None:
            return
        period = self._period()
        if not self._ledger.try_consume(self.budget_group, period, 1, self._quota_limit()):
            raise QuotaExhaustedError(self.budget_group, period)

    def _quota_limit(self) -> int:
        quota = self._operations.quota
        assert quota is not None
        return math.floor(quota.limit * QUOTA_SAFETY_FRACTION)

    def _period(self) -> str:
        quota = self._operations.quota
        assert quota is not None
        today = self._wall_clock().astimezone(UTC).date()
        return f"{today:%Y-%m}" if quota.period is QuotaPeriod.MONTH else today.isoformat()


class LimiterPool:
    """One limiter per budget group, shared by every source in that group."""

    def __init__(self, ledger: QuotaLedger | None = None, clock: Clock | None = None) -> None:
        self._ledger = ledger or InMemoryQuotaLedger()
        self._clock = clock or Clock()
        self._limiters: dict[str, RateLimiter] = {}
        self._lock = threading.Lock()

    def for_policy(self, policy: SourcePolicy) -> RateLimiter:
        return self.for_operations(policy.operations)

    def for_operations(self, operations: SourceOperations) -> RateLimiter:
        with self._lock:
            existing = self._limiters.get(operations.budget_group)
            if existing is None:
                existing = RateLimiter(operations, self._ledger, self._clock)
                self._limiters[operations.budget_group] = existing
            elif (
                existing.operations.rate_limit != operations.rate_limit
                or existing.operations.quota != operations.quota
            ):
                raise ValueError(
                    f"budget group '{operations.budget_group}' is declared with two "
                    "different limits"
                )
            return existing
