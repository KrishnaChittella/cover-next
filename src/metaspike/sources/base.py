"""The contract every source adapter implements."""

from datetime import datetime
from enum import StrEnum
from typing import Protocol

from metaspike.domain.games import CanonicalGame, SourceIdentifier
from metaspike.domain.observations import Observation
from metaspike.domain.samples import SampleReading
from metaspike.domain.signals import SignalSpec, SourceErrorCode
from metaspike.domain.timeseries import TimeWindow


class SourceError(Exception):
    """A source could not provide data. The message is safe to log, never a traceback."""

    def __init__(self, code: SourceErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SourceMode(StrEnum):
    """How a source answers, which decides where its history comes from.

    FETCH_HISTORY  the source can be asked about past periods, so a window can be
                   fetched, re-fetched and backfilled from the source itself.
    SAMPLE_NOW     the source answers only for the instant it is asked. A moment that
                   was not sampled is gone: history exists only in our own store.
    """

    FETCH_HISTORY = "fetch_history"
    SAMPLE_NOW = "sample_now"


class SourceAdapter(Protocol):
    @property
    def source(self) -> str: ...

    @property
    def mode(self) -> SourceMode:
        """Always FETCH_HISTORY for this contract."""
        ...

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


class SamplingAdapter(Protocol):
    """A SAMPLE_NOW source: one reading of one source-native target, taken now.

    `sample` takes no window, period or date, so an implementation has no way to be
    asked for the past and no way to claim it filled a gap. It never raises for a
    source failure and never reports a failure as a value: every outcome, including
    a refusal to send, comes back as a reading with a status.
    """

    @property
    def source(self) -> str: ...

    @property
    def mode(self) -> SourceMode:
        """Always SAMPLE_NOW for this contract."""
        ...

    @property
    def signal(self) -> SignalSpec:
        """The metric this source samples, at the slot length it is sampled at."""
        ...

    @property
    def description(self) -> str:
        """One public sentence saying exactly what this source measures."""
        ...

    def sample(self, source_native_id: str) -> SampleReading: ...


class CatalogueAdapter(Protocol):
    """A source of catalogue metadata (identity, dates, relationships), not of signals."""

    @property
    def source(self) -> str: ...

    @property
    def description(self) -> str:
        """One public sentence saying exactly what this source provides."""
        ...
