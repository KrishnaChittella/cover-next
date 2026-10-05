"""Recorded catalogue responses, loaded through the real parsers.

The fixture files hold rows exactly as the sources returned them (identity fields
only, no authentication material). Loading them through the same parsing code the
adapters use means the golden set's evidence is tied to what the sources actually
said, not to a hand-written copy of it.
"""

import json
from datetime import datetime
from pathlib import Path

from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.source_records import SourceRecord
from metaspike.sources.igdb import parse_game_row
from metaspike.sources.steam_catalogue import SteamAppKind, parse_app_row

IGDB_FIXTURE = "igdb_games.json"
STEAM_FIXTURE = "steam_apps.json"


def load_recorded_catalogue(directory: Path) -> dict[SourceRecordKey, SourceRecord]:
    """Every recorded source record, keyed by its source-native identity."""
    records: dict[SourceRecordKey, SourceRecord] = {}

    igdb = json.loads((directory / IGDB_FIXTURE).read_text(encoding="utf-8"))
    recorded_at = datetime.fromisoformat(igdb["recorded_at"])
    for row in igdb["rows"]:
        record = parse_game_row(row, recorded_at)
        records[record.key] = record

    steam = json.loads((directory / STEAM_FIXTURE).read_text(encoding="utf-8"))
    recorded_at = datetime.fromisoformat(steam["recorded_at"])
    for entry in steam["apps"]:
        parsed = parse_app_row(entry["app"], SteamAppKind(entry["kind"]), recorded_at)
        if parsed is None:
            raise ValueError("a recorded Steam app has no name")
        if parsed.key in records:
            raise ValueError(f"Steam app {parsed.key.source_id} was recorded twice")
        records[parsed.key] = parsed
    return records
