"""Short-lived access tokens from the runtime's metadata server.

A Cloud Run job obtains a token for its own service account from the metadata
server. Nothing is read from a file or an environment variable and nothing is
written anywhere; the token is held in memory until shortly before it expires and
is never logged, returned in an error or included in a report.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

_METADATA_TOKEN = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)
_REFRESH_MARGIN = timedelta(seconds=120)


class TokenUnavailable(Exception):
    """No access token could be obtained. The message never contains one."""


class MetadataToken:
    def __init__(
        self, client: httpx.Client, now: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._client = client
        self._now = now
        self._value: str | None = None
        self._expires = datetime.min.replace(tzinfo=UTC)

    def __call__(self) -> str:
        if self._value is None or self._now() >= self._expires - _REFRESH_MARGIN:
            try:
                response = self._client.get(_METADATA_TOKEN, headers={"Metadata-Flavor": "Google"})
            except httpx.HTTPError:
                raise TokenUnavailable("the metadata server could not be reached") from None
            if response.status_code != 200:
                raise TokenUnavailable(f"the metadata server answered {response.status_code}")
            try:
                body = response.json()
                value, lifetime = str(body["access_token"]), int(body["expires_in"])
            except (ValueError, KeyError, TypeError):
                raise TokenUnavailable("the metadata server's answer was not a token") from None
            self._value = value
            self._expires = self._now() + timedelta(seconds=lifetime)
        return self._value

    def __repr__(self) -> str:
        return "MetadataToken(<redacted>)"
