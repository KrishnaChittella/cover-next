"""Wikimedia pageviews as the production warehouse holds them, read point in time.

Production collects daily pageviews (`ms-wikimedia`) and loads them into BigQuery
(`ms_staging.stg_period_observations`). This module reads a local EXPORT of those rows,
made by a bounded read-only query (`export_query`), and serves them to the existing
collection pipeline through the same adapter interface as the live source. Nothing here
calls Wikimedia or Google Cloud.

Point in time: a reading at `observed_at` sees only rows the warehouse had observed by
then, and for a period observed more than once, the latest such observation. A period with
no row is absent, never zero; `build_time_series` marks it missing.

Provenance: each row keeps the collection run, landed object and content digest it came
from. The export file is UNVERIFIED like any other local file until its digest is pinned.
"""

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, ConfigDict, Field

from metaspike.domain.games import CanonicalGame, SourceIdentifier
from metaspike.domain.model import FrozenModel
from metaspike.domain.observations import Freshness, Observation, ObservationStatus
from metaspike.domain.signals import SignalSpec, SourceErrorCode
from metaspike.domain.timeseries import Granularity, TimeWindow
from metaspike.sources.base import SourceError, SourceMode
from metaspike.sources.registry import SourceRegistry
from metaspike.sources.wikimedia import DAILY_PAGEVIEWS, SOURCE_ID, WIKIMEDIA_PAGEVIEWS_POLICY

EXPORT_FORMAT = "wikimedia_warehouse_export_v1"
EXPORT_FILE = "wikimedia_warehouse_export.json"
TABLE = "ms_staging.stg_period_observations"
# A bounded read: one source, one metric, a partition filter, and a bytes cap.
MAX_BYTES_BILLED = 50_000_000
# An export holds at most this many rows. The query asks for one more: if it arrives, the
# answer was cut short somewhere and the export is refused rather than written incomplete.
EXPORT_MAX_ROWS = 100_000
COLUMNS = (
    "source_identifier",
    "canonical_game_id",
    "period_start",
    "value",
    "status",
    "observed_at",
    "collection_run_id",
    "landed_object",
    "content_sha256",
)


def export_query(first_day: str, last_day: str) -> str:
    """The read-only query an export is made from: one source, one metric, a day range."""
    return (
        f"SELECT {', '.join(COLUMNS)} FROM {TABLE} "
        f"WHERE source = '{SOURCE_ID}' AND metric = '{DAILY_PAGEVIEWS.metric}' "
        f"AND granularity = '{Granularity.DAILY.value}' "
        f"AND period_start >= TIMESTAMP('{first_day}') "
        f"AND period_start < TIMESTAMP_ADD(TIMESTAMP('{last_day}'), INTERVAL 1 DAY) "
        "ORDER BY source_identifier, period_start, observed_at "
        f"LIMIT {EXPORT_MAX_ROWS + 1}"
    )


class ExportRow(FrozenModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_identifier: str = Field(min_length=1)
    canonical_game_id: str | None = None
    period_start: AwareDatetime
    value: float | None
    status: str
    observed_at: AwareDatetime
    collection_run_id: str | None = None
    landed_object: str | None = None
    content_sha256: str | None = None


class WarehouseExport(FrozenModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    format: Literal["wikimedia_warehouse_export_v1"]
    project: str
    table: str
    query: str
    first_day: str
    last_day: str
    exported_at: AwareDatetime
    bytes_billed_cap: int = Field(ge=0)
    rows: tuple[ExportRow, ...]

    def write(self, directory: Path) -> Path:
        """Into a new directory, so the export's digest covers exactly this file."""
        directory.mkdir(parents=True, exist_ok=False)
        path = directory / EXPORT_FILE
        path.write_text(json.dumps(self.model_dump(mode="json"), indent=1, sort_keys=True))
        return path


def _utc(text: str) -> datetime:
    """BigQuery's JSON output writes a TIMESTAMP as 'YYYY-MM-DD HH:MM:SS[.ffffff]', in UTC."""
    moment = datetime.fromisoformat(text.removesuffix(" UTC"))
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def export_from_bq_json(
    output: str,
    *,
    project: str,
    first_day: str,
    last_day: str,
    exported_at: datetime,
) -> WarehouseExport:
    """An export from the JSON `bq query --format=json` printed for `export_query`."""
    answer = json.loads(output or "[]")
    if not isinstance(answer, list):
        raise ValueError("bq did not print a list of rows")
    if len(answer) > EXPORT_MAX_ROWS:
        raise ValueError(
            f"more than {EXPORT_MAX_ROWS} rows: the answer may be incomplete; export fewer days"
        )
    rows = []
    for raw in answer:
        value = raw.get("value")
        rows.append(
            ExportRow(
                source_identifier=raw["source_identifier"],
                canonical_game_id=raw.get("canonical_game_id"),
                period_start=_utc(raw["period_start"]),
                value=None if value is None else float(value),
                status=raw["status"],
                observed_at=_utc(raw["observed_at"]),
                collection_run_id=raw.get("collection_run_id"),
                landed_object=raw.get("landed_object"),
                content_sha256=raw.get("content_sha256"),
            )
        )
    return WarehouseExport(
        format=EXPORT_FORMAT,
        project=project,
        table=TABLE,
        query=export_query(first_day, last_day),
        first_day=first_day,
        last_day=last_day,
        exported_at=exported_at,
        bytes_billed_cap=MAX_BYTES_BILLED,
        rows=tuple(rows),
    )


def load_export(directory: Path) -> WarehouseExport:
    return WarehouseExport.model_validate_json((directory / EXPORT_FILE).read_text())


class WarehousePageviewsAdapter:
    """The Wikimedia daily-pageviews signal, answered from a warehouse export."""

    def __init__(self, export: WarehouseExport) -> None:
        self._rows: dict[str, list[ExportRow]] = defaultdict(list)
        for row in export.rows:
            self._rows[row.source_identifier].append(row)

    @property
    def source(self) -> str:
        return SOURCE_ID

    @property
    def mode(self) -> SourceMode:
        return SourceMode.FETCH_HISTORY

    @property
    def signals(self) -> tuple[SignalSpec, ...]:
        return (DAILY_PAGEVIEWS,)

    @property
    def description(self) -> str:
        return (
            "Daily human pageviews of the game's English Wikipedia article, as collected in "
            "production and held by the warehouse; read from a local export, point in time."
        )

    def fetch(
        self,
        game: CanonicalGame,
        identifier: SourceIdentifier,
        signal: SignalSpec,
        window: TimeWindow,
        observed_at: datetime,
    ) -> list[Observation]:
        if signal != DAILY_PAGEVIEWS:
            raise ValueError(f"{SOURCE_ID} does not provide {signal.metric}/{signal.granularity}")
        known: dict[datetime, ExportRow] = {}
        for row in self._rows.get(f"{identifier.namespace}/{identifier.value}", ()):
            if row.observed_at > observed_at or not window.contains(row.period_start):
                continue
            if row.status != ObservationStatus.OBSERVED.value or row.value is None:
                continue  # a non-observation is never a value
            earlier = known.get(row.period_start)
            if earlier is None or row.observed_at > earlier.observed_at:
                known[row.period_start] = row
        if not known:
            raise SourceError(
                SourceErrorCode.NOT_FOUND,
                f"{identifier.value}: the warehouse holds no observation in this window "
                f"known by {observed_at.isoformat()}",
            )
        return [_observation(game, identifier, known[period]) for period in sorted(known)]


def _observation(game: CanonicalGame, identifier: SourceIdentifier, row: ExportRow) -> Observation:
    period_end = row.period_start + Granularity.DAILY.step
    return Observation(
        canonical_game_id=game.canonical_game_id,
        source=SOURCE_ID,
        metric=DAILY_PAGEVIEWS.metric,
        granularity=DAILY_PAGEVIEWS.granularity,
        period_start=row.period_start,
        value=row.value,
        status=ObservationStatus.OBSERVED,
        observed_at=row.observed_at,
        source_timestamp=row.period_start,
        freshness=Freshness(
            period_end=period_end,
            lag_hours=round((row.observed_at - period_end).total_seconds() / 3600, 2),
        ),
        reference=f"{identifier.namespace}/{identifier.value}",
    )


def warehouse_registry(export: WarehouseExport) -> SourceRegistry:
    registry = SourceRegistry()
    registry.register(WarehousePageviewsAdapter(export), WIKIMEDIA_PAGEVIEWS_POLICY)
    return registry
