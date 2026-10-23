"""Steam concurrent players for the public site: one reading a day, kept as a history.

OWNER DECISION, 2026-10-23. The Steam Web API Terms (Section 2) license distributing Steam
Data to end users through an application; they do not address figures derived from it
(history, "against normal"). Valve was asked by email the same day. Until Valve answers,
the owner chose to show both the raw count and the derived history, as SteamCharts and
SteamDB do, and to take them down if Valve asks. `STEAM_CURRENT_PLAYERS_POLICY` itself is
unchanged: the warehouse pipeline still refuses derived Steam output; this module is the
public site's own, recorded exception.

Display conditions that do hold (Sections 2, 3): "as is" with a disclaimer, Valve named
and linked without nofollow, no implied affiliation, and a privacy notice saying what is
stored and where.

    {root}/<app id>.json    {"YYYY-MM-DD": players, ...}, one reading per UTC day

A day with no reading is unknown, never zero: a failed request stores nothing.
"""

import json
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Final

from metaspike.domain.samples import SampleStatus
from metaspike.sources.steam_players import SteamCurrentPlayersAdapter

ATTRIBUTION: Final = (
    "Steam data provided by Valve via the Steam Web API, as is and without warranty. "
    "Cover Next is not endorsed by or affiliated with Valve or Steam."
)
WINDOW_DAYS: Final = 31
RECENT_DAYS: Final = 3
# Fewer known days than this before the recent ones and there is no normal level yet.
MIN_BASELINE_DAYS: Final = 14


class SteamHistory:
    def __init__(self, root: Path) -> None:
        self.root = root

    def get(self, app: str) -> dict[date, int]:
        path = self.root / f"{app}.json"
        if not path.is_file():
            return {}
        return {date.fromisoformat(d): v for d, v in json.loads(path.read_text("utf-8")).items()}

    def put(self, app: str, day: date, players: int) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        days = self.get(app)
        days[day] = players
        payload = {d.isoformat(): v for d, v in sorted(days.items())}
        (self.root / f"{app}.json").write_text(json.dumps(payload), encoding="utf-8")


def collect(
    adapter: SteamCurrentPlayersAdapter, apps: Iterable[str], history: SteamHistory, day: date
) -> tuple[int, int]:
    """Read each app once and keep the day's count. Returns (read, failed)."""
    read = failed = 0
    for app in apps:
        reading = adapter.sample(app)
        if reading.status is SampleStatus.OBSERVED and reading.value is not None:
            history.put(app, day, reading.value)
            read += 1
        else:
            failed += 1
        if reading.halt_run:  # key refused or rate limited: stop sending
            break
    return read, failed


def view(days: Mapping[date, int], day: date, app: str) -> dict[str, Any]:
    """What the site shows for one app: today's count, the last 31 days, against normal."""
    series: list[int | None] = [
        days.get(day - timedelta(days=back)) for back in range(WINDOW_DAYS - 1, -1, -1)
    ]
    if series[-1] is None:
        return {"state": "missing", "app": app, "daily": []}
    recent = [v for v in series[-RECENT_DAYS:] if v is not None]
    before = [v for v in series[:-RECENT_DAYS] if v is not None]
    normal = float(median(before)) if len(before) >= MIN_BASELINE_DAYS else None
    start = day - timedelta(days=WINDOW_DAYS - 1)
    return {
        "state": "ok",
        "app": app,
        "players_now": series[-1],
        "daily": [
            {"day": (start + timedelta(days=n)).isoformat(), "players": v}
            for n, v in enumerate(series)
        ],
        "recent": round(sum(recent) / len(recent)),
        "normal": None if normal is None else round(normal),
        # No ratio against a normal of zero: "x infinity" says nothing.
        "ratio": None if not normal else round(sum(recent) / len(recent) / normal, 2),
    }
