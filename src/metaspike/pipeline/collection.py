"""Collection: ask every registered source for every signal of every game.

Failures are isolated per signal, so one game or one metric failing never stops the
run and never turns into a value.
"""

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from pydantic import AwareDatetime

from metaspike.domain.games import Catalogue
from metaspike.domain.model import FrozenModel
from metaspike.domain.signals import (
    CollectionStatus,
    EntitySignals,
    Signal,
    SignalKey,
    SignalSpec,
)
from metaspike.domain.timeseries import Granularity, TimeWindow
from metaspike.features.attention import HISTORY_DAYS
from metaspike.pipeline.normalize import build_time_series, latest_due_period
from metaspike.sources.base import SourceError
from metaspike.sources.registry import RegisteredSource, SourceRegistry

logger = logging.getLogger(__name__)


class SourceRun(FrozenModel):
    source: str
    started_at: AwareDatetime
    finished_at: AwareDatetime
    outcomes: tuple[Signal, ...]

    def with_status(self, status: CollectionStatus) -> tuple[Signal, ...]:
        return tuple(outcome for outcome in self.outcomes if outcome.status is status)

    def entity_status(self) -> dict[str, CollectionStatus]:
        """One status per entity: collected if any signal arrived, else failed, else unmapped."""
        statuses: dict[str, CollectionStatus] = {}
        rank = {
            CollectionStatus.NOT_MAPPED: 0,
            CollectionStatus.FAILED: 1,
            CollectionStatus.COLLECTED: 2,
        }
        for outcome in self.outcomes:
            current = statuses.get(outcome.canonical_game_id)
            if current is None or rank[outcome.status] > rank[current]:
                statuses[outcome.canonical_game_id] = outcome.status
        return statuses


class CollectionReport(FrozenModel):
    # The days of history requested; each signal covers them at its own granularity.
    window: TimeWindow
    observed_at: AwareDatetime
    runs: tuple[SourceRun, ...]

    @property
    def any_success(self) -> bool:
        return any(run.with_status(CollectionStatus.COLLECTED) for run in self.runs)

    def signals_for(self, canonical_game_id: str) -> EntitySignals:
        return EntitySignals(
            canonical_game_id=canonical_game_id,
            signals=tuple(
                outcome
                for run in self.runs
                for outcome in run.outcomes
                if outcome.canonical_game_id == canonical_game_id
            ),
        )


def latest_complete_day(now: datetime) -> date:
    """Yesterday in UTC: the most recent day a daily source can have finished counting."""
    return now.astimezone(UTC).date() - timedelta(days=1)


def window_ending(end: date) -> TimeWindow:
    return TimeWindow.of_days(end - timedelta(days=HISTORY_DAYS - 1), end)


def signal_window(days: TimeWindow, granularity: Granularity) -> TimeWindow:
    """The periods of `granularity` that fall inside the requested days."""
    last_period = days.end + Granularity.DAILY.step - granularity.step
    return TimeWindow(start=days.start, end=last_period)


def collect(
    catalogue: Catalogue,
    registry: SourceRegistry,
    window: TimeWindow,
    observed_at: datetime,
    clock: Callable[[], datetime],
) -> CollectionReport:
    runs: list[SourceRun] = []
    for entry in registry.entries():
        started_at = clock()
        outcomes = [
            _collect_signal(entry, game_id, spec, catalogue, window, observed_at)
            for game_id in (game.canonical_game_id for game in catalogue.games)
            for spec in entry.adapter.signals
        ]
        runs.append(
            SourceRun(
                source=entry.adapter.source,
                started_at=started_at,
                finished_at=clock(),
                outcomes=tuple(outcomes),
            )
        )
    return CollectionReport(window=window, observed_at=observed_at, runs=tuple(runs))


def _collect_signal(
    entry: RegisteredSource,
    canonical_game_id: str,
    spec: SignalSpec,
    catalogue: Catalogue,
    days: TimeWindow,
    observed_at: datetime,
) -> Signal:
    adapter = entry.adapter
    game = catalogue.get(canonical_game_id)
    key = SignalKey(source=adapter.source, metric=spec.metric, granularity=spec.granularity)
    identifier = game.identifier_for(adapter.source)
    if identifier is None:
        return Signal(
            canonical_game_id=canonical_game_id, key=key, status=CollectionStatus.NOT_MAPPED
        )
    window = signal_window(days, spec.granularity)
    try:
        observations = adapter.fetch(game, identifier, spec, window, observed_at)
    except SourceError as error:
        logger.warning("%s: %s failed: %s", adapter.source, game.slug, error.message)
        return Signal(
            canonical_game_id=canonical_game_id,
            key=key,
            status=CollectionStatus.FAILED,
            error_code=error.code,
            error_message=error.message,
        )
    series = build_time_series(
        canonical_game_id,
        key,
        window,
        observations,
        observed_at,
        latest_due=latest_due_period(
            key.granularity, observed_at, entry.policy.operations.expected_publication_lag
        ),
    )
    logger.info(
        "%s: %s collected %d %s periods of %s",
        adapter.source,
        game.slug,
        len(observations),
        spec.granularity.value,
        spec.metric,
    )
    return Signal(
        canonical_game_id=canonical_game_id,
        key=key,
        status=CollectionStatus.COLLECTED,
        series=series,
    )
