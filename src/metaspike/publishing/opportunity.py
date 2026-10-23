"""The site's opportunity reading: audience curiosity against creator supply, per game.

Rule `site_opportunity_v1` (a new rule, not the frozen `metaspike_score_v0`). Both halves are
ranks among today's tracked games, so the reading says where a game stands today, never a
promise that a video will do well:

    curiosity   Wikipedia views lately against the game's own normal (the x figure)
    supply gap  Twitch live viewers per live stream: many viewers per streamer means
                creators have not crowded in yet

    score = round(100 * mean(curiosity rank, supply-gap rank))

Video-platform data is deliberately absent: its policies forbid figures derived from it. A game
without both halves (or with fewer than MIN_STREAMS live streams) gets no score, never a
zero. Saturation names the supply-gap third:
Low (top third), Medium, High.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Final

RULE: Final = "site_opportunity_v1"
# Fewer live streams than this and viewers per stream is one channel's luck, not supply.
MIN_STREAMS: Final = 3


def _ranks(values: Mapping[str, float]) -> dict[str, float]:
    """Each key's share of the other keys it is strictly above: 0.0 lowest, 1.0 highest."""
    if len(values) < 2:
        return dict.fromkeys(values, 0.5)
    ordered = sorted(values.values())
    return {k: sum(o < v for o in ordered) / (len(ordered) - 1) for k, v in values.items()}


def _viewers_per_stream(game: Mapping[str, Any]) -> float | None:
    twitch = game["twitch"]
    if twitch.get("state") != "ok" or (twitch.get("streams_now") or 0) < MIN_STREAMS:
        return None
    return float(twitch["now"]) / float(twitch["streams_now"])


def readings(games: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """slug -> the opportunity reading, for every game with both halves."""
    curiosity = {
        g["slug"]: float(g["rise"]["ratio"])
        for g in games
        if g["rise"] is not None and g["rise"]["ratio"] is not None
    }
    supply = {g["slug"]: v for g in games if (v := _viewers_per_stream(g)) is not None}
    c_rank, s_rank = _ranks(curiosity), _ranks(supply)
    out: dict[str, dict[str, Any]] = {}
    for slug in curiosity.keys() & supply.keys():
        gap = s_rank[slug]
        out[slug] = {
            "rule": RULE,
            "score": round(100 * (c_rank[slug] + gap) / 2),
            "curiosity_rank": round(c_rank[slug], 2),
            "supply_gap_rank": round(gap, 2),
            "viewers_per_stream": round(supply[slug], 1),
            "saturation": "Low" if gap >= 2 / 3 else "Medium" if gap >= 1 / 3 else "High",
        }
    return out


def spotlight(
    games: Sequence[Mapping[str, Any]], scores: Mapping[str, Mapping[str, Any]]
) -> str | None:
    """The listed game with the best reading; the strongest riser when none has one."""
    listed = [
        g
        for g in games
        if not (g["story"] is not None and not g["story"]["lead"] and not g["pinned"])
    ]
    scored = [g for g in listed if g["slug"] in scores]
    if scored:
        best = max(scored, key=lambda g: (scores[g["slug"]]["score"], g["rise"]["ratio"]))
        return str(best["slug"])
    rising = [g for g in listed if g["rise"] is not None and g["rise"]["ratio"] is not None]
    return str(max(rising, key=lambda g: g["rise"]["ratio"])["slug"]) if rising else None
