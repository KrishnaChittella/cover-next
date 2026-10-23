"""One reading a day per key, kept as a history: what Steam and Twitch figures share.

    {root}/<key>.json    {"YYYY-MM-DD": value, ...}, one value per UTC day

A day with no reading is unknown, never zero: a failed request stores nothing.
"""

import json
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Final

WINDOW_DAYS: Final = 31
RECENT_DAYS: Final = 3
# Fewer known days than this before the recent ones and there is no normal level yet.
MIN_BASELINE_DAYS: Final = 14


class DailyHistory:
    def __init__(self, root: Path) -> None:
        self.root = root

    def get(self, key: str) -> dict[date, int]:
        path = self.root / f"{key}.json"
        if not path.is_file():
            return {}
        return {date.fromisoformat(d): v for d, v in json.loads(path.read_text("utf-8")).items()}

    def put(self, key: str, day: date, value: int) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        days = self.get(key)
        days[day] = value
        payload = {d.isoformat(): v for d, v in sorted(days.items())}
        (self.root / f"{key}.json").write_text(json.dumps(payload), encoding="utf-8")


def view(days: Mapping[date, int], day: date) -> dict[str, Any]:
    """Today's value, the last 31 days, and the last 3 days against the median before them."""
    series: list[int | None] = [
        days.get(day - timedelta(days=back)) for back in range(WINDOW_DAYS - 1, -1, -1)
    ]
    if series[-1] is None:
        return {"state": "missing", "daily": []}
    recent = [v for v in series[-RECENT_DAYS:] if v is not None]
    before = [v for v in series[:-RECENT_DAYS] if v is not None]
    normal = float(median(before)) if len(before) >= MIN_BASELINE_DAYS else None
    start = day - timedelta(days=WINDOW_DAYS - 1)
    mean = sum(recent) / len(recent)
    return {
        "state": "ok",
        "now": series[-1],
        "daily": [
            {"day": (start + timedelta(days=n)).isoformat(), "value": v}
            for n, v in enumerate(series)
        ],
        "recent": round(mean),
        "normal": None if normal is None else round(normal),
        # No ratio against a normal of zero: "x infinity" says nothing.
        "ratio": None if not normal else round(mean / normal, 2),
    }
