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

History: `discovery.daily`, keyed by Steam app id.
"""

from collections.abc import Iterable
from datetime import date
from typing import Final

from metaspike.discovery.daily import DailyHistory
from metaspike.domain.samples import SampleStatus
from metaspike.sources.steam_players import SteamCurrentPlayersAdapter

ATTRIBUTION: Final = (
    "Steam data provided by Valve via the Steam Web API, as is and without warranty. "
    "Cover Next is not endorsed by or affiliated with Valve or Steam."
)


def collect(
    adapter: SteamCurrentPlayersAdapter, apps: Iterable[str], history: DailyHistory, day: date
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
