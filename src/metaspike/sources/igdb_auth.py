"""IGDB authentication: Twitch OAuth2 client-credentials with a cached access token.

The long-lived Client ID and Client Secret come from the environment. The access
token is generated here, never configured by hand. It lasts about 60 days and an
application may hold only 25 at once, so it is cached and reused across runs rather
than minted per run. The cache is a local, owner-only file; a cloud token cache is a
later phase's concern.
"""

import contextlib
import json
import logging
import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from metaspike.domain.signals import SourceErrorCode
from metaspike.domain.source_policy import (
    LimitBasis,
    QuotaBudget,
    QuotaPeriod,
    RateLimit,
    SourceOperations,
)
from metaspike.sources.base import SourceError
from metaspike.sources.credentials import Secret
from metaspike.sources.http import LimitedHttpClient

logger = logging.getLogger(__name__)

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
DEFAULT_TOKEN_CACHE = Path(".metaspike/tokens/igdb.json")
# Refresh a little early so a long run never starts with a token about to lapse.
REFRESH_MARGIN = timedelta(days=1)

# Twitch documents no limit for the token endpoint. Minting is rare by design, so the
# budget is deliberately tiny: a bug that loops on refresh is stopped, not amplified.
TWITCH_OAUTH_OPERATIONS = SourceOperations(
    budget_group="twitch_oauth",
    rate_limit=RateLimit(
        max_requests=2,
        per_seconds=60,
        max_concurrency=1,
        basis=LimitBasis.SELF_IMPOSED,
        note="Token minting should happen roughly once every two months.",
    ),
    quota=QuotaBudget(
        limit=20,
        unit="token requests",
        period=QuotaPeriod.DAY,
        basis=LimitBasis.SELF_IMPOSED,
        note="An application may hold at most 25 active tokens.",
    ),
    expected_publication_lag_seconds=0,
    publication_lag_basis=LimitBasis.SELF_IMPOSED,
)


class _TokenResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    access_token: str = Field(min_length=1)
    expires_in: int = Field(gt=0)


class _CachedToken(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str = Field(min_length=1)
    expires_at: datetime


def _utc_now() -> datetime:
    return datetime.now(UTC)


class TokenCache:
    """A token kept in a file only its owner can read."""

    def __init__(self, path: Path = DEFAULT_TOKEN_CACHE) -> None:
        self._path = path

    def load(self) -> tuple[Secret, datetime] | None:
        try:
            cached = _CachedToken.model_validate_json(self._path.read_bytes())
        except (FileNotFoundError, ValidationError, ValueError):
            return None
        return Secret(cached.access_token), cached.expires_at

    def save(self, token: Secret, expires_at: datetime) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            self._path.parent.chmod(0o700)
        temporary = self._path.with_name(f".{self._path.name}.tmp")
        payload = json.dumps({"access_token": token.reveal(), "expires_at": expires_at.isoformat()})
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
        temporary.replace(self._path)

    def clear(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self._path.unlink()


class IgdbAuth:
    def __init__(
        self,
        http: LimitedHttpClient,
        client_id: Secret,
        client_secret: Secret,
        cache: TokenCache | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._http = http
        self._client_id = client_id
        self._client_secret = client_secret
        self._cache = cache or TokenCache()
        self._now = now
        self._lock = threading.Lock()
        self._token: Secret | None = None
        self._expires_at: datetime | None = None

    def headers(self) -> tuple[dict[str, str], Secret]:
        """Request headers, plus the token they carry so a rejection can name it."""
        token = self.token()
        return (
            {
                "Client-ID": self._client_id.reveal(),
                "Authorization": f"Bearer {token.reveal()}",
                "Accept": "application/json",
            },
            token,
        )

    def token(self) -> Secret:
        """A valid token: from memory, else the cache, else freshly minted.

        Single-flight: concurrent callers wait on one lock, and whoever enters second
        finds the token the first one obtained.
        """
        with self._lock:
            if self._usable(self._token, self._expires_at):
                assert self._token is not None
                return self._token
            cached = self._cache.load()
            if cached is not None and self._usable(*cached):
                self._token, self._expires_at = cached
                return self._token
            return self._mint()

    def invalidate(self, rejected: Secret) -> None:
        """Forget a token the API refused. A token another caller already replaced is kept."""
        with self._lock:
            if self._token is None or self._token == rejected:
                self._token = None
                self._expires_at = None
                cached = self._cache.load()
                if cached is None or cached[0] == rejected:
                    self._cache.clear()

    def _usable(self, token: Secret | None, expires_at: datetime | None) -> bool:
        return (
            token is not None
            and expires_at is not None
            and expires_at - REFRESH_MARGIN > self._now()
        )

    def _mint(self) -> Secret:
        # Credentials go in the request body, never the URL, so they cannot reach a log.
        response = self._http.request(
            "POST",
            TOKEN_URL,
            label="twitch oauth",
            data={
                "client_id": self._client_id.reveal(),
                "client_secret": self._client_secret.reveal(),
                "grant_type": "client_credentials",
            },
        )
        if response.status_code != httpx.codes.OK:
            raise SourceError(
                SourceErrorCode.UNAUTHORIZED,
                f"twitch oauth: token request rejected (HTTP {response.status_code})",
            )
        try:
            granted = _TokenResponse.model_validate_json(response.content)
        except ValidationError:
            raise SourceError(
                SourceErrorCode.INVALID_RESPONSE, "twitch oauth: unexpected token response"
            ) from None
        self._token = Secret(granted.access_token)
        self._expires_at = self._now() + timedelta(seconds=granted.expires_in)
        self._cache.save(self._token, self._expires_at)
        logger.info("twitch oauth: obtained a token valid until %s", self._expires_at.date())
        return self._token
