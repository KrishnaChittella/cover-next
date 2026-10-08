"""The only way a source adapter sends an HTTP request.

`LimitedHttpClient` wraps a transport client with the budget group's rate limiter and
one retry policy. Adapters are given a `LimitedHttpClient`, never a bare client, so
no request can leave without passing the limiter.

It is also where a request budget is enforced. A budget counts OUTBOUND ATTEMPTS: one
unit for every physical HTTP request handed to the transport, a retry included. It is
taken immediately before the send, below the retry loop, so no retry policy can send
an attempt the budget does not have. Four things are kept apart:

    target            one game or article to collect
    logical fetch     one adapter operation for one target
    outbound attempt  one physical HTTP request
    retry             a further outbound attempt for the same logical fetch

Running out of budget is MetaSpike declining to send. It is not the source refusing:
it is raised as `RequestBudgetExhausted`, which is not a `SourceError` and carries no
source error code, so it can never be mistaken for a rate limit.

This module also owns secret hygiene for outbound requests: error messages are built
from a caller-supplied label, never from a URL (a URL can carry an API key), and
HTTP client logging is filtered for credentials.
"""

import logging
import random
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from metaspike.domain.signals import SourceErrorCode
from metaspike.sources.base import SourceError
from metaspike.sources.limiter import QuotaExhaustedError, RateLimiter

logger = logging.getLogger(__name__)

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b((?:key|client_secret|client_id|access_token|token)=)[^&\s\"']+"),
    re.compile(r"(?i)\b(authorization\W{1,4}bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)(\"(?:access_token|client_secret)\"\s*:\s*\")[^\"]+"),
)
REDACTED = "[redacted]"


def redact(text: str) -> str:
    """Remove credentials from text that is about to be logged or raised."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(rf"\g<1>{REDACTED}", text)
    return text


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True


def install_log_redaction() -> None:
    """Keep credentials out of HTTP client logs, which include full request URLs."""
    for name in ("httpx", "httpcore"):
        http_logger = logging.getLogger(name)
        if not any(isinstance(f, _RedactingFilter) for f in http_logger.filters):
            http_logger.addFilter(_RedactingFilter())
        http_logger.setLevel(max(http_logger.level, logging.WARNING))


install_log_redaction()


def build_http_client(user_agent: str) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": user_agent, "Accept": "application/json"},
        timeout=httpx.Timeout(10.0, connect=5.0),
        follow_redirects=False,
    )


class RequestBudgetExhausted(Exception):
    """MetaSpike's own attempt budget is spent, so the next request was not sent.

    Local backpressure, not a source failure. `after` is the source error from the last
    attempt that WAS sent for this logical request, when the stop came between an
    attempt and its retry; it is None when nothing was sent for the request at all.
    """

    def __init__(self, label: str, limit: int, after: SourceError | None = None) -> None:
        super().__init__(f"{label}: request budget of {limit} outbound attempts is spent")
        self.limit = limit
        self.after = after


class AttemptBudget:
    """How many outbound attempts may still be sent, and how many have been.

    One is taken for every physical request, immediately before it is handed to the
    transport. With no limit it only counts. A limit can be lowered and never raised.
    """

    def __init__(self, limit: int | None = None) -> None:
        if limit is not None and limit < 0:
            raise ValueError("a request budget cannot be negative")
        self._limit = limit
        self._used = 0

    @property
    def limit(self) -> int | None:
        return self._limit

    @property
    def used(self) -> int:
        """Outbound attempts sent so far."""
        return self._used

    @property
    def remaining(self) -> int | None:
        return None if self._limit is None else max(0, self._limit - self._used)

    @property
    def exhausted(self) -> bool:
        return self._limit is not None and self._used >= self._limit

    def restrict(self, limit: int) -> None:
        """Lower the limit to `limit`. A stricter limit already in force stays."""
        if limit < 0:
            raise ValueError("a request budget cannot be negative")
        self._limit = limit if self._limit is None else min(self._limit, limit)

    def take(self, label: str = "request") -> None:
        """Spend one attempt, or refuse. Refusing spends nothing and sends nothing."""
        if self._limit is not None and self._used >= self._limit:
            raise RequestBudgetExhausted(label, self._limit)
        self._used += 1


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 4
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0
    # Fraction of each delay that may be randomly removed, to avoid synchronised retries.
    jitter: float = 0.0

    def delay(
        self, attempt: int, retry_after: float | None = None, rng: random.Random | None = None
    ) -> float:
        """Exponential backoff for the given 1-based attempt, never above the cap."""
        backoff = self.base_delay_seconds * 2.0 ** (attempt - 1)
        if self.jitter and rng is not None:
            backoff *= 1 - self.jitter * rng.random()
        return min(self.max_delay_seconds, max(backoff, retry_after or 0.0))


class LimitedHttpClient:
    def __init__(
        self,
        client: httpx.Client,
        limiter: RateLimiter,
        retry: RetryConfig | None = None,
        rng: random.Random | None = None,
        budget: AttemptBudget | None = None,
    ) -> None:
        self._client = client
        self._limiter = limiter
        self._retry = retry or RetryConfig()
        self._rng = rng or random.Random()
        # Always present, so attempts are always counted. Unlimited unless restricted.
        self._budget = budget or AttemptBudget()

    @property
    def limiter(self) -> RateLimiter:
        return self._limiter

    @property
    def budget(self) -> AttemptBudget:
        return self._budget

    def request(
        self,
        method: str,
        url: str,
        *,
        label: str,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str | int] | None = None,
        content: str | None = None,
        data: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """Send one logical request, retrying transient failures within the limits.

        Returns the response for any status that is not transient; the adapter decides
        what a 200, 401 or 404 means. Raises SourceError when attempts are exhausted
        or the quota is spent. `label` names the request in errors and logs.

        Every attempt, the first and each retry, takes one unit of the request budget
        just before it is sent. With none left nothing is sent and
        RequestBudgetExhausted is raised: retries stop at the retry policy's limit or
        at the budget, whichever comes first.
        """
        error: SourceError | None = None
        for attempt in range(1, self._retry.max_attempts + 1):
            retry_after: float | None = None
            try:
                with self._limiter.slot():
                    try:
                        self._budget.take(label)
                    except RequestBudgetExhausted as spent:
                        raise RequestBudgetExhausted(label, spent.limit, after=error) from None
                    response = self._send(method, url, headers, params, content, data)
            except QuotaExhaustedError as exhausted:
                raise SourceError(
                    SourceErrorCode.QUOTA_EXHAUSTED,
                    f"{label}: quota exhausted for {exhausted.period}",
                ) from None
            except httpx.TimeoutException:
                error = SourceError(SourceErrorCode.TIMEOUT, f"{label}: request timed out")
            except httpx.TransportError as exc:
                error = SourceError(SourceErrorCode.NETWORK_ERROR, f"{label}: {type(exc).__name__}")
            else:
                status = response.status_code
                if status == httpx.codes.TOO_MANY_REQUESTS:
                    error = SourceError(SourceErrorCode.RATE_LIMITED, f"{label}: rate limited")
                    retry_after = _retry_after_seconds(response)
                elif status >= httpx.codes.INTERNAL_SERVER_ERROR:
                    error = SourceError(SourceErrorCode.HTTP_ERROR, f"{label}: HTTP {status}")
                else:
                    return response
            if attempt < self._retry.max_attempts:
                if self._budget.exhausted:
                    # A retry would be the next thing sent, and there is nothing to
                    # send it with. Stop here, without waiting out a backoff first.
                    limit = self._budget.limit or 0
                    raise RequestBudgetExhausted(label, limit, after=error)
                delay = self._retry.delay(attempt, retry_after, self._rng)
                logger.info("%s; retrying in %.1fs (attempt %d)", error.message, delay, attempt)
                self._limiter.back_off(delay)
        if error is None:
            raise SourceError(SourceErrorCode.NETWORK_ERROR, f"{label}: no attempt was made")
        raise error

    def _send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str] | None,
        params: Mapping[str, str | int] | None,
        content: str | None,
        data: Mapping[str, str] | None,
    ) -> httpx.Response:
        arguments: dict[str, Any] = {}
        if headers is not None:
            arguments["headers"] = dict(headers)
        if params is not None:
            arguments["params"] = dict(params)
        if content is not None:
            arguments["content"] = content
        if data is not None:
            arguments["data"] = dict(data)
        return self._client.request(method, url, **arguments)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    header = response.headers.get("Retry-After", "")
    return float(header) if header.isdigit() else None
