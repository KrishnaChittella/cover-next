"""The contract every source adapter implements."""

from datetime import date, datetime
from enum import StrEnum
from typing import Protocol, Self

from pydantic import model_validator

from metaspike.domain.games import CanonicalGame, SourceIdentifier
from metaspike.domain.model import FrozenModel
from metaspike.domain.observations import Metric, Observation


class SourceErrorCode(StrEnum):
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    HTTP_ERROR = "http_error"
    INVALID_RESPONSE = "invalid_response"


class SourceError(Exception):
    """A source could not provide data. The message is safe to log, never a traceback."""

    def __init__(self, code: SourceErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class FetchWindow(FrozenModel):
    """Inclusive range of UTC days."""

    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.start > self.end:
            raise ValueError("window start must not be after its end")
        return self

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


class SourceAdapter(Protocol):
    @property
    def source(self) -> str: ...

    @property
    def metric(self) -> Metric: ...

    @property
    def description(self) -> str:
        """One public sentence saying exactly what this source measures."""
        ...

    def fetch(
        self,
        game: CanonicalGame,
        identifier: SourceIdentifier,
        window: FetchWindow,
        observed_at: datetime,
    ) -> list[Observation]:
        """Return the observed points in the window, or raise SourceError.

        Adapters report only what the source returned. They never invent a value for
        a day the source omitted, and never turn a failure into zero activity.
        """
        ...
