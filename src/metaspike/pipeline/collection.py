"""Collection: ask every registered source for every game, isolating failures per game."""

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from pydantic import AwareDatetime

from metaspike.domain.games import Catalogue, GameId
from metaspike.domain.model import FrozenModel
from metaspike.domain.observations import DailySeries
from metaspike.features.attention import HISTORY_DAYS
from metaspike.pipeline.normalize import build_daily_series
from metaspike.sources.base import FetchWindow, SourceError, SourceErrorCode
from metaspike.sources.registry import SourceRegistry

logger = logging.getLogger(__name__)


class CollectionStatus(StrEnum):
    COLLECTED = "collected"
    FAILED = "failed"
    NOT_MAPPED = "not_mapped"


class GameCollection(FrozenModel):
    canonical_game_id: GameId
    status: CollectionStatus
    series: DailySeries | None = None
    error_code: SourceErrorCode | None = None
    error_message: str | None = None


class SourceRun(FrozenModel):
    source: str
    started_at: AwareDatetime
    finished_at: AwareDatetime
    outcomes: tuple[GameCollection, ...]

    def with_status(self, status: CollectionStatus) -> tuple[GameCollection, ...]:
        return tuple(outcome for outcome in self.outcomes if outcome.status is status)


class CollectionReport(FrozenModel):
    window: FetchWindow
    observed_at: AwareDatetime
    runs: tuple[SourceRun, ...]

    @property
    def any_success(self) -> bool:
        return any(run.with_status(CollectionStatus.COLLECTED) for run in self.runs)


def latest_complete_day(now: datetime) -> date:
    """Yesterday in UTC: the most recent day a daily source can have finished counting."""
    return now.astimezone(UTC).date() - timedelta(days=1)


def window_ending(end: date) -> FetchWindow:
    return FetchWindow(start=end - timedelta(days=HISTORY_DAYS - 1), end=end)


def collect(
    catalogue: Catalogue,
    registry: SourceRegistry,
    window: FetchWindow,
    observed_at: datetime,
    clock: Callable[[], datetime],
) -> CollectionReport:
    runs: list[SourceRun] = []
    for entry in registry.entries():
        adapter = entry.adapter
        started_at = clock()
        outcomes: list[GameCollection] = []
        for game in catalogue.games:
            identifier = game.identifier_for(adapter.source)
            if identifier is None:
                outcomes.append(
                    GameCollection(
                        canonical_game_id=game.canonical_game_id,
                        status=CollectionStatus.NOT_MAPPED,
                    )
                )
                continue
            try:
                observations = adapter.fetch(game, identifier, window, observed_at)
            except SourceError as error:
                logger.warning("%s: %s failed: %s", adapter.source, game.slug, error.message)
                outcomes.append(
                    GameCollection(
                        canonical_game_id=game.canonical_game_id,
                        status=CollectionStatus.FAILED,
                        error_code=error.code,
                        error_message=error.message,
                    )
                )
                continue
            series = build_daily_series(
                game.canonical_game_id,
                adapter.source,
                adapter.metric,
                window,
                observations,
                observed_at,
            )
            logger.info("%s: %s collected %d days", adapter.source, game.slug, len(observations))
            outcomes.append(
                GameCollection(
                    canonical_game_id=game.canonical_game_id,
                    status=CollectionStatus.COLLECTED,
                    series=series,
                )
            )
        runs.append(
            SourceRun(
                source=adapter.source,
                started_at=started_at,
                finished_at=clock(),
                outcomes=tuple(outcomes),
            )
        )
    return CollectionReport(window=window, observed_at=observed_at, runs=tuple(runs))
