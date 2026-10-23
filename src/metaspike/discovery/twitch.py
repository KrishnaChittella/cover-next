"""Twitch live viewers and live streams per game for the public site: one reading a day.

OWNER DECISION, 2026-10-23. The Developer Services Agreement (XIII Additional Terms,
Schedule 1-C) allows a copy of Program Materials only as a 24-hour cache unless Twitch
authorises more. A Twitch developer-forum moderator answered the owner's question the
same day: two sums per game, with no user-specific data, are "derived results of analysis"
like SullyGnome or TwitchTracker keep, and "additional permission isn't needed" (stated as
an opinion, not legal advice): FORUM_ANSWER. The owner chose to keep and show those sums,
and to change or remove them if Twitch asks. `TWITCH_HELIX_POLICY` is unchanged: the
private pipeline still keeps raw Twitch readings 24 hours only.

What is kept: per game per UTC day, the live viewer sum and the live stream count over the
top pages of streams, and whether pages were left unread (then both are lower bounds).
No stream, channel or user field is ever kept. Identity: the game's IGDB slug (Wikidata)
-> IGDB id (IGDB) -> the Twitch category Twitch itself links to that IGDB id. Never a title.

    {root}/viewers/<igdb slug>.json   {root}/streams/...   {root}/partial/... (1 = lower bound)
    {root}/categories.json            {igdb slug: twitch category id or null}
"""

import json
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Final

from metaspike.discovery.daily import DailyHistory
from metaspike.sources.igdb import IgdbCatalogueAdapter
from metaspike.sources.twitch import TwitchHelixAdapter

FORUM_ANSWER: Final = (
    "https://discuss.dev.twitch.com/t/"
    "can-i-store-aggregated-twitch-game-stats-for-longer-than-24-hours/65174"
)
ATTRIBUTION: Final = (
    "Live figures from Twitch via the Twitch API. Cover Next is not endorsed by Twitch."
)


class TwitchDaily:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.viewers = DailyHistory(root / "viewers")
        self.streams = DailyHistory(root / "streams")
        self.partial = DailyHistory(root / "partial")

    def categories(self) -> dict[str, str | None]:
        path = self.root / "categories.json"
        return json.loads(path.read_text("utf-8")) if path.is_file() else {}

    def save_categories(self, found: dict[str, str | None]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        merged = self.categories() | found
        (self.root / "categories.json").write_text(json.dumps(merged, sort_keys=True), "utf-8")


def collect(
    igdb: IgdbCatalogueAdapter,
    twitch: TwitchHelixAdapter,
    slugs: Iterable[str],
    store: TwitchDaily,
    day: date,
) -> tuple[int, int, int]:
    """Map each slug to its Twitch category by id, then read it. (read, failed, unmapped)."""
    wanted = list(dict.fromkeys(slugs))
    ids = igdb.ids_for_slugs(wanted)
    by_igdb = twitch.categories(str(i) for i in ids.values())
    found = {s: by_igdb.get(str(ids[s])) if s in ids else None for s in wanted}
    store.save_categories(found)
    read = failed = 0
    for slug, category in found.items():
        if category is None:
            continue
        live = twitch.live(slug, category)
        if live.error_code is not None or live.viewers is None or live.streams is None:
            failed += 1
            continue
        store.viewers.put(slug, day, live.viewers)
        store.streams.put(slug, day, live.streams)
        store.partial.put(slug, day, 0 if live.complete else 1)
        read += 1
    return read, failed, sum(c is None for c in found.values())
