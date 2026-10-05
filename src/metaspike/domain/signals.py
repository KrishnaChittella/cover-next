"""Signals: every (source, metric, granularity) an entity could have, present or not.

An entity rarely has every signal. Absence is a first-class state with a reason, so
downstream code can never mistake "we have no data" for "nothing happened".
"""

from enum import StrEnum
from typing import Self

from pydantic import model_validator

from metaspike.domain.games import GameId, NonEmptyStr
from metaspike.domain.metrics import MetricId
from metaspike.domain.model import FrozenModel
from metaspike.domain.observations import TimeSeries
from metaspike.domain.timeseries import Granularity


class SignalSpec(FrozenModel):
    """One thing a source provides: a metric at a granularity."""

    metric: MetricId
    granularity: Granularity


class SignalKey(FrozenModel):
    source: NonEmptyStr
    metric: MetricId
    granularity: Granularity


class CollectionStatus(StrEnum):
    COLLECTED = "collected"
    FAILED = "failed"
    NOT_MAPPED = "not_mapped"


class SourceErrorCode(StrEnum):
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    HTTP_ERROR = "http_error"
    INVALID_RESPONSE = "invalid_response"
    QUOTA_EXHAUSTED = "quota_exhausted"
    UNAUTHORIZED = "unauthorized"
    MISSING_CREDENTIALS = "missing_credentials"


class Signal(FrozenModel):
    """The outcome of trying to obtain one signal for one entity."""

    canonical_game_id: GameId
    key: SignalKey
    status: CollectionStatus
    series: TimeSeries | None = None
    error_code: SourceErrorCode | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def _series_matches_status(self) -> Self:
        if (self.series is not None) != (self.status is CollectionStatus.COLLECTED):
            raise ValueError("a signal has a series exactly when it was collected")
        if self.series is not None and (
            self.series.canonical_game_id != self.canonical_game_id
            or self.series.source != self.key.source
            or self.series.metric != self.key.metric
            or self.series.granularity is not self.key.granularity
        ):
            raise ValueError("series does not belong to this signal")
        return self

    @property
    def available(self) -> bool:
        return self.series is not None


class EntitySignals(FrozenModel):
    """All signals for one entity across every source."""

    canonical_game_id: GameId
    signals: tuple[Signal, ...] = ()

    @model_validator(mode="after")
    def _signals_are_consistent(self) -> Self:
        keys = [signal.key for signal in self.signals]
        if len(keys) != len(set(keys)):
            raise ValueError("an entity cannot hold the same signal twice")
        if any(s.canonical_game_id != self.canonical_game_id for s in self.signals):
            raise ValueError("every signal must belong to the entity")
        return self

    def get(self, key: SignalKey) -> Signal | None:
        for signal in self.signals:
            if signal.key == key:
                return signal
        return None

    def available(self) -> tuple[Signal, ...]:
        return tuple(signal for signal in self.signals if signal.available)

    def missing(self) -> tuple[Signal, ...]:
        return tuple(signal for signal in self.signals if not signal.available)

    def for_metric(self, metric: str) -> tuple[Signal, ...]:
        return tuple(signal for signal in self.signals if signal.key.metric == metric)

    def metrics_available(self) -> frozenset[str]:
        return frozenset(signal.key.metric for signal in self.available())

    def sources_available(self) -> frozenset[str]:
        return frozenset(signal.key.source for signal in self.available())
