"""SYNTHETIC inputs for the historical opportunity evaluation. Invented; labelled.

Nothing here is a recording of any source. The games are invented ("Synthetic Alpha"
and so on) so they cannot be mistaken for real ones, every input carries a synthetic
provenance, and the evaluation refuses to mix any of it with recorded data. It exists to
show the method reaching every outcome the recorded data cannot: a candidate followed by
attention rising, a candidate followed by attention falling, a watchlist entry, an
abstention, an incomplete forward period, and evidence that arrived too late to count.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

from metaspike.creator_sources.youtube import RETENTION_DAYS
from metaspike.domain.games import CanonicalGame, Catalogue, LifecycleState
from metaspike.domain.observations import Freshness, Observation, ObservationStatus
from metaspike.domain.signals import CollectionStatus, Signal
from metaspike.domain.timeseries import Granularity, TimeWindow, start_of_day
from metaspike.events.rules import EVENT_HEAT_RULES_V1
from metaspike.evidence.model import InputProvenance, Origin
from metaspike.opportunity.history import ContextFamily, ContextObservation, community_angle
from metaspike.pipeline.collection import CollectionReport, SourceRun
from metaspike.pipeline.normalize import build_time_series
from metaspike.scoring.v0 import V0_INPUT_SIGNAL

SYNTHETIC_HISTORY: Final = InputProvenance(
    origin=Origin.SYNTHETIC,
    reference="synthetic-history",
    note="invented to exercise the historical evaluation; evidence of nothing",
)
_DAY = timedelta(days=1)
# The invented recording's first day. Any date would do; this one is plainly not real data.
START: Final = date(2030, 1, 1)
# Invented games, numbered from here so their ids are far from any real one.
_ID_BASE: Final = 0x5E_0000


def day(index: int) -> date:
    return START + index * _DAY


def at(index: int, hour: int = 0) -> datetime:
    return start_of_day(day(index)) + timedelta(hours=hour)


def synthetic_game(number: int, slug: str) -> CanonicalGame:
    return CanonicalGame(
        canonical_game_id=str(uuid.UUID(int=_ID_BASE + number, version=4)),
        slug=slug,
        name=f"Synthetic {slug.replace('synthetic-', '').title()}",
        lifecycle_state=LifecycleState.LIVE_SERVICE,
    )


def steady(days: int) -> list[float | None]:
    """An invented, quiet daily series with a little regular variation."""
    return [1000.0 + 60 * (index % 3) for index in range(days)]


def pageview_recording(
    games: Sequence[CanonicalGame],
    series: Mapping[str, Sequence[float | None]],
    observed_at: datetime,
) -> CollectionReport:
    """An invented pageview recording: one daily series per slug, starting on START."""
    length = max(len(values) for values in series.values())
    window = TimeWindow.of_days(START, day(length - 1))
    signals = []
    for game in games:
        values = series.get(game.slug, ())
        observations = [
            Observation(
                canonical_game_id=game.canonical_game_id,
                source=V0_INPUT_SIGNAL.source,
                metric=V0_INPUT_SIGNAL.metric,
                granularity=Granularity.DAILY,
                period_start=start_of_day(day(index)),
                value=value,
                status=ObservationStatus.OBSERVED,
                observed_at=observed_at,
                freshness=Freshness(period_end=start_of_day(day(index)) + _DAY, lag_hours=0),
            )
            for index, value in enumerate(values)
            if value is not None
        ]
        if not observations:
            signals.append(
                Signal(
                    canonical_game_id=game.canonical_game_id,
                    key=V0_INPUT_SIGNAL,
                    status=CollectionStatus.NOT_MAPPED,
                )
            )
            continue
        signals.append(
            Signal(
                canonical_game_id=game.canonical_game_id,
                key=V0_INPUT_SIGNAL,
                status=CollectionStatus.COLLECTED,
                series=build_time_series(
                    game.canonical_game_id, V0_INPUT_SIGNAL, window, observations, observed_at
                ),
            )
        )
    run = SourceRun(
        source=V0_INPUT_SIGNAL.source,
        started_at=observed_at,
        finished_at=observed_at,
        outcomes=tuple(signals),
    )
    return CollectionReport(window=window, observed_at=observed_at, runs=(run,))


def creator_sample(
    canonical_game_id: str,
    reference: str,
    retrieved_at: datetime,
    complete: bool = True,
    provenance: InputProvenance = SYNTHETIC_HISTORY,
) -> ContextObservation:
    """A creator-supply sample, current for the YouTube data's retention period."""
    return ContextObservation(
        canonical_game_id=canonical_game_id,
        family=ContextFamily.CREATOR_SUPPLY,
        reference=reference,
        retrieved_at=retrieved_at,
        timely_from=retrieved_at,
        timely_until=retrieved_at + timedelta(days=RETENTION_DAYS),
        complete=complete,
        provenance=provenance,
    )


def event(
    canonical_game_id: str,
    reference: str,
    retrieved_at: datetime,
    starts_at: datetime,
    provenance: InputProvenance = SYNTHETIC_HISTORY,
) -> ContextObservation:
    """An announced event: upcoming within the radar's horizon, active on its first day."""
    return ContextObservation(
        canonical_game_id=canonical_game_id,
        family=ContextFamily.EVENT,
        reference=reference,
        retrieved_at=retrieved_at,
        timely_from=starts_at - timedelta(days=EVENT_HEAT_RULES_V1.horizon_days),
        timely_until=starts_at + _DAY,
        provenance=provenance,
    )


def discussion(
    canonical_game_id: str,
    reference: str,
    newest_post_at: datetime,
    retrieved_at: datetime,
    supporting: int = 3,
    provenance: InputProvenance = SYNTHETIC_HISTORY,
) -> ContextObservation:
    return community_angle(
        canonical_game_id, reference, newest_post_at, retrieved_at, supporting, provenance
    )


# The scenario: sixty quiet days, then each game does something different from day 60.
QUIET_DAYS: Final = 60
SCENARIO_DAYS: Final = 70
FIRST_MOMENT: Final = QUIET_DAYS - 2
LAST_MOMENT: Final = SCENARIO_DAYS + 2


@dataclass(frozen=True)
class SyntheticHistory:
    catalogue: Catalogue
    recording: CollectionReport
    context: tuple[ContextObservation, ...]
    publication_lag: Mapping[str, timedelta]
    first_day: date
    last_day: date
    description: tuple[str, ...]


def _after_quiet(rest: Sequence[float]) -> list[float | None]:
    tail = list(rest) + [rest[-1]] * (SCENARIO_DAYS - QUIET_DAYS - len(rest))
    return [*steady(QUIET_DAYS), *tail]


def synthetic_history() -> SyntheticHistory:
    """An invented ten-day story for six invented games. SYNTHETIC throughout."""
    slugs = (
        "synthetic-alpha",
        "synthetic-bravo",
        "synthetic-charlie",
        "synthetic-delta",
        "synthetic-echo",
        "synthetic-foxtrot",
    )
    games = tuple(synthetic_game(number, slug) for number, slug in enumerate(slugs, start=1))
    ids = {game.slug: game.canonical_game_id for game in games}
    series = {
        # Rises from day 60 and stays up.
        "synthetic-alpha": _after_quiet([4000.0]),
        # Rises on days 60 and 61, then falls well below its normal level.
        "synthetic-bravo": _after_quiet([4000.0, 4000.0, 400.0]),
        # Rises and stays up, with nothing else to say why.
        "synthetic-charlie": _after_quiet([4000.0]),
        # Quiet throughout; an event is coming.
        "synthetic-delta": _after_quiet([1000.0]),
        # Quiet throughout; nothing else.
        "synthetic-echo": _after_quiet([1000.0]),
        # Rises and stays up; its discussion was only retrieved later.
        "synthetic-foxtrot": _after_quiet([4000.0]),
    }
    recording = pageview_recording(games, series, at(SCENARIO_DAYS + 5))
    context = (
        discussion(ids["synthetic-alpha"], "synthetic-thread-a", at(59, 12), at(60, 6)),
        creator_sample(ids["synthetic-alpha"], "synthetic-sample-a", at(60, 6)),
        discussion(ids["synthetic-bravo"], "synthetic-thread-b", at(59, 12), at(60, 6)),
        creator_sample(ids["synthetic-bravo"], "synthetic-sample-b", at(60, 6)),
        event(ids["synthetic-delta"], "synthetic-event-d", at(55), at(66)),
        creator_sample(ids["synthetic-foxtrot"], "synthetic-sample-f", at(60, 6)),
        discussion(ids["synthetic-foxtrot"], "synthetic-thread-f", at(59, 12), at(69)),
    )
    return SyntheticHistory(
        catalogue=Catalogue(games=games),
        recording=recording,
        context=context,
        publication_lag={V0_INPUT_SIGNAL.source: _DAY},
        first_day=day(FIRST_MOMENT),
        last_day=day(LAST_MOMENT),
        description=(
            "SYNTHETIC: six invented games, sixty quiet invented days, then:",
            "  synthetic-alpha    rises and stays up; a discussion and a complete creator "
            "sample retrieved on day 60",
            "  synthetic-bravo    rises for two days, then falls; the same evidence",
            "  synthetic-charlie  rises and stays up; no other evidence",
            "  synthetic-delta    quiet; an event announced on day 55 starts on day 66",
            "  synthetic-echo     quiet; nothing else",
            "  synthetic-foxtrot  rises; its discussion was retrieved only on day 69",
        ),
    )
