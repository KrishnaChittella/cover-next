"""The contract every source adapter implements."""

from datetime import datetime
from typing import Protocol

from metaspike.domain.games import CanonicalGame, SourceIdentifier
from metaspike.domain.observations import Observation
from metaspike.domain.signals import SignalSpec, SourceErrorCode
from metaspike.domain.timeseries import TimeWindow


class SourceError(Exception):
    """A source could not provide data. The message is safe to log, never a traceback."""

    def __init__(self, code: SourceErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SourceAdapter(Protocol):
    @property
    def source(self) -> str: ...

    @property
    def signals(self) -> tuple[SignalSpec, ...]:
        """Every metric this source provides, each at the granularity it is fetched at."""
        ...

    @property
    def description(self) -> str:
        """One public sentence saying exactly what this source measures."""
        ...

    def fetch(
        self,
        game: CanonicalGame,
        identifier: SourceIdentifier,
        signal: SignalSpec,
        window: TimeWindow,
        observed_at: datetime,
    ) -> list[Observation]:
        """Return the observed points of one signal in the window, or raise SourceError.

        Adapters report only what the source returned. They never invent a value for
        a period the source omitted, and never turn a failure into zero activity.
        """
        ...


class CatalogueAdapter(Protocol):
    """A source of catalogue metadata (identity, dates, relationships), not of signals."""

    @property
    def source(self) -> str: ...

    @property
    def description(self) -> str:
        """One public sentence saying exactly what this source provides."""
        ...
