"""Credentials come from the environment and never appear in text.

A `Secret` cannot be printed, logged, formatted or serialised by accident: every
string conversion yields a placeholder. The value leaves the object only through
`reveal()`, at the one place a request is built.
"""

import os
from collections.abc import Mapping

from metaspike.domain.signals import SourceErrorCode
from metaspike.sources.base import SourceError

IGDB_CLIENT_ID_ENV = "METASPIKE_IGDB_CLIENT_ID"
IGDB_CLIENT_SECRET_ENV = "METASPIKE_IGDB_CLIENT_SECRET"
STEAM_WEB_API_KEY_ENV = "METASPIKE_STEAM_WEB_API_KEY"

_PLACEHOLDER = "[redacted]"


class Secret:
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not value:
            raise ValueError("a secret cannot be empty")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"Secret({_PLACEHOLDER})"

    def __str__(self) -> str:
        return _PLACEHOLDER

    def __format__(self, format_spec: str) -> str:
        return _PLACEHOLDER

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Secret) and other._value == self._value

    def __hash__(self) -> int:
        return hash(self._value)

    def __reduce__(self) -> str:
        raise TypeError("a Secret cannot be pickled or copied into another process")


def secret_from_env(name: str, environ: Mapping[str, str] | None = None) -> Secret:
    """Read a credential by variable name. The error names the variable, never a value."""
    value = (os.environ if environ is None else environ).get(name, "").strip()
    if not value:
        raise SourceError(
            SourceErrorCode.MISSING_CREDENTIALS, f"environment variable {name} is not set"
        )
    return Secret(value)
