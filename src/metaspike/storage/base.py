"""The storage contract for normalised observations.

Storage is append-only and point-in-time: a period's value as first seen is never
overwritten, and a later revision is kept as a new version beside it. Reading "as
of" a moment returns exactly what was known then, which is what lets a past score
be replayed without leaking information from its future.
"""

from datetime import datetime
from typing import Protocol

from metaspike.domain.observations import TimeSeries
from metaspike.domain.signals import SignalKey
from metaspike.domain.timeseries import TimeWindow


class StoragePolicyError(Exception):
    """The source's policy does not permit storing this data."""


class StorageCorruptionError(Exception):
    """Stored data could not be read back as valid observations."""


class ObservationStore(Protocol):
    def write(self, series: TimeSeries) -> int:
        """Append the observed points of a series. Returns the number of versions kept.

        Writing the same values again keeps nothing new. Missing points are not
        stored: absence is reconstructed on read. Raises StoragePolicyError if the
        source's policy does not allow historical storage of the metric.
        """
        ...

    def read(
        self, canonical_game_id: str, key: SignalKey, window: TimeWindow, as_of: datetime
    ) -> TimeSeries:
        """The series as it was known at `as_of`, gap-free across `window`."""
        ...

    def signals(self, canonical_game_id: str) -> tuple[SignalKey, ...]:
        """Every signal that has stored observations for the entity."""
        ...
