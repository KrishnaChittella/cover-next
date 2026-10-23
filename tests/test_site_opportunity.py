"""The site's opportunity reading: ranks of curiosity and supply gap, never a zero."""

from typing import Any

from metaspike.publishing.opportunity import readings, spotlight


def _game(
    slug: str, ratio: float | None, viewers: int | None, streams: int | None
) -> dict[str, Any]:
    twitch = (
        {"state": "missing"}
        if viewers is None
        else {"state": "ok", "now": viewers, "streams_now": streams}
    )
    rise = None if ratio is None else {"ratio": ratio}
    return {"slug": slug, "rise": rise, "twitch": twitch, "story": None, "pinned": False}


def test_score_is_the_mean_of_two_ranks_and_missing_halves_get_none() -> None:
    games = [
        _game("hot-empty", 10.0, 1000, 5),  # curious, 200 viewers per stream
        _game("hot-crowded", 9.0, 1000, 100),  # curious, crowded
        _game("calm", 1.0, 300, 3),  # 100 per stream
        _game("no-twitch", 20.0, None, None),
        _game("one-stream", 30.0, 5000, 1),  # one channel's luck
    ]
    out = readings(games)
    assert set(out) == {"hot-empty", "hot-crowded", "calm"}
    # Curiosity ranks among every game with a ratio (10.0 is above 2 of the other 4 = 0.5);
    # supply-gap ranks among games with 3+ streams (200 per stream is the top = 1.0).
    assert out["hot-empty"]["score"] == 75 and out["hot-empty"]["saturation"] == "Low"
    assert out["hot-crowded"]["score"] == 12 and out["hot-crowded"]["saturation"] == "High"
    assert out["calm"]["viewers_per_stream"] == 100.0


def test_spotlight_skips_folded_story_members() -> None:
    games = [_game("lead", 5.0, 100, 10), _game("member", 9.0, 1000, 4)]
    games[1]["story"] = {"lead": False}
    scores = readings(games)
    assert spotlight(games, scores) == "lead"
