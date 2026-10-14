"""SYNTHETIC Reddit-shaped responses for exercising the community-interest path.

Nothing here is an observation. MetaSpike has never called Reddit. Every post id,
community name used for an unrelated topic, title, score and count below is invented,
and every post's link points at the reserved `.invalid` domain, which the evidence
reading recognises wherever the data is later read from.

Three runs, through the real adapter, budget and collector, relative to the moment:

    run 1  three hours before
           Rust, r/playrust (declared its community): two pages, the page limit hit
           Rust, r/gaming (declared general): one page
           Rust, all of Reddit, with r/rust declared unrelated: one page
           Dota 2, r/DotA2: complete, nothing returned
           Counter-Strike 2, r/GlobalOffensive: 429, rate limited
           Elden Ring, r/Eldenring: not sent, the budget halted by the 429
    run 2  one hour before: r/playrust again, one page of several (the page limit), a
           post's score and comments now higher
    run 3  one hour AFTER the moment: r/playrust again, with a post created after it
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from metaspike.community_sources.collect import collect_community_interest
from metaspike.community_sources.reddit import (
    CommunityQuery,
    CommunityScope,
    RedditAdapter,
    RequestBudget,
    replay_reddit_client,
)
from metaspike.community_sources.store import CommunityStore

SCENARIO_NAME = "synthetic-community-interest-v1"
_HOUR = timedelta(hours=1)
_NOTE = (
    "SYNTHETIC. Written by MetaSpike's synthetic community-interest scenario in the "
    "shape of a Reddit Listing. Not a response of Reddit; every post here is invented."
)


@dataclass(frozen=True)
class _Post:
    post_id: str
    subreddit: str
    title: str
    created: datetime
    score: int | None = 10
    comments: int | None = 4
    flair: str | None = None

    def child(self) -> dict[str, object]:
        data: dict[str, object] = {
            "id": self.post_id,
            "name": f"t3_{self.post_id}",
            "subreddit": self.subreddit,
            "title": self.title,
            "permalink": f"/r/{self.subreddit}/comments/{self.post_id}/synthetic/",
            "created_utc": self.created.timestamp(),
            "url": f"https://example.invalid/synthetic/{self.post_id}",
            "author": "synthetic-author-never-read",
            "selftext": "synthetic body that is never read",
        }
        if self.score is not None:
            data["score"] = self.score
        if self.comments is not None:
            data["num_comments"] = self.comments
        if self.flair is not None:
            data["link_flair_text"] = self.flair
        return {"kind": "t3", "data": data}


def _listing(
    directory: Path,
    name: str,
    path: str,
    text: str,
    posts: list[_Post],
    *,
    after: str = "",
    next_after: str | None = None,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    request = {"path": path, "q": text}
    if after:
        request["after"] = after
    body: object = {
        "kind": "Listing",
        "data": {
            "after": next_after,
            "before": None,
            "dist": len(posts),
            "children": [post.child() for post in posts],
        },
    }
    if status != 200:
        body = {"message": "Too Many Requests", "error": status}
    document = {"note": _NOTE, "request": request, "status": status, "body": body}
    if headers:
        document["headers"] = headers
    (directory / f"{name}.json").write_text(json.dumps(document, indent=1), encoding="utf-8")


@dataclass(frozen=True)
class SyntheticCommunity:
    store: CommunityStore
    description: tuple[str, ...]


def build_synthetic_community(
    root: Path, games: dict[str, tuple[str, str]], as_of: datetime
) -> SyntheticCommunity:
    """Write the scenario under `root` and return its store. Deterministic."""
    rust_id, rust = games["rust"]
    dota_id, _ = games["dota-2"]
    cs_id, _ = games["counter-strike-2"]
    elden_id, _ = games["elden-ring"]
    store = CommunityStore(root / "community")

    def query(
        game: str, text: str, scope: CommunityScope, sub: str | None, **extra: object
    ) -> CommunityQuery:
        return CommunityQuery(
            canonical_game_id=game,
            text=text,
            scope=scope,
            subreddit=sub,
            limit=5,
            **extra,
        )

    playrust = query(
        rust_id,
        rust,
        CommunityScope.DEDICATED,
        "playrust",
        reason="what Rust's own community is discussing",
    )
    gaming = query(
        rust_id,
        rust,
        CommunityScope.GENERAL_GAMING,
        "gaming",
        reason="Rust in a general gaming community",
    )
    everywhere = query(
        rust_id,
        rust,
        CommunityScope.SITE_WIDE,
        None,
        unrelated_subreddits=("rust",),
        reason="Rust anywhere; r/rust is the programming language",
    )
    dota = query(
        dota_id,
        "patch",
        CommunityScope.DEDICATED,
        "DotA2",
        reason="patch discussion in Dota 2's own community",
    )
    cs = query(
        cs_id,
        "update",
        CommunityScope.DEDICATED,
        "GlobalOffensive",
        reason="update discussion in Counter-Strike 2's own community",
    )
    elden = query(
        elden_id,
        "dlc",
        CommunityScope.DEDICATED,
        "Eldenring",
        reason="DLC discussion in Elden Ring's own community",
    )

    t = as_of
    offline = _Post(
        "synr001", "playrust", "How do I stop getting offlined every wipe?", t - 20 * _HOUR, 15, 22
    )
    solo = _Post(
        "synr002", "playrust", "How to stop getting offlined as a solo?", t - 10 * _HOUR, 12, 9
    )
    broke = _Post(
        "synr003",
        "playrust",
        "Patch 2.3 broke base decay, anyone else?",
        t - 6 * _HOUR,
        40,
        31,
        "Discussion",
    )
    thoughts = _Post(
        "synr004", "playrust", "Thoughts on patch 2.3 decay changes", t - 5 * _HOUR, None, None
    )
    brutal = _Post(
        "synr005", "playrust", "Update 2.3 decay is brutal for small groups", t - 4 * _HOUR, 25, 18
    )
    versus = _Post("synr006", "playrust", "Rust or DayZ for a new player?", t - 8 * _HOUR, 8, 14)
    designs = _Post("synr007", "playrust", "Best base designs this week", t - 30 * _HOUR, 5, 2)
    both = _Post(
        "synr008", "gaming", "Rust and Dota 2 both got patches today", t - 7 * _HOUR, 90, 40
    )
    language = _Post(
        "synr009",
        "rust",
        "How to stop the borrow checker from complaining?",
        t - 9 * _HOUR,
        300,
        60,
    )
    film = _Post("synr010", "movies", "Rust (2024) film review", t - 11 * _HOUR, 70, 33)
    newer = _Post("synr011", "playrust", "Hotfix 2.3.1 is out, decay fixed?", t + _HOUR / 2, 3, 1)
    grown = _Post(
        "synr002", "playrust", "How to stop getting offlined as a solo?", t - 10 * _HOUR, 340, 77
    )

    first = root / "community-responses" / "run-1"
    _listing(
        first,
        "playrust-1",
        "/r/playrust/search",
        rust,
        [offline, solo, broke, thoughts, brutal, versus],
        next_after="t3_synr006",
    )
    _listing(
        first,
        "playrust-2",
        "/r/playrust/search",
        rust,
        [designs],
        after="t3_synr006",
        next_after="t3_synr007",
    )
    _listing(first, "gaming", "/r/gaming/search", rust, [both])
    _listing(first, "everywhere", "/search", rust, [language, film, broke])
    _listing(first, "dota", "/r/DotA2/search", "patch", [])
    _listing(
        first,
        "cs",
        "/r/GlobalOffensive/search",
        "update",
        [],
        status=429,
        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "412"},
    )

    def collect(
        run: str, directory: Path, moment: datetime, queries: list[CommunityQuery], pages: int
    ) -> None:
        with replay_reddit_client(directory) as client:
            adapter = RedditAdapter(client, RequestBudget(10), run_id=run, now=lambda: moment)
            collect_community_interest(adapter, queries, store, max_pages=pages)

    collect(
        "synthetic-run-1",
        first,
        t - 3 * _HOUR,
        [playrust, gaming, everywhere, dota, cs, elden],
        pages=2,
    )
    second = root / "community-responses" / "run-2"
    _listing(
        second,
        "playrust",
        "/r/playrust/search",
        rust,
        [offline, grown, broke, thoughts, brutal, versus],
        next_after="t3_synr006",
    )
    collect("synthetic-run-2", second, t - _HOUR, [playrust], pages=1)
    third = root / "community-responses" / "run-3"
    _listing(third, "playrust", "/r/playrust/search", rust, [newer, grown, broke])
    collect("synthetic-run-3", third, t + _HOUR, [playrust], pages=1)
    return SyntheticCommunity(
        store=store,
        description=(
            f'"{rust}": synthetic posts from its declared community, a general gaming '
            "community and a site-wide search with an unrelated community declared",
            '"Dota 2": a synthetic search that returned nothing',
            '"Counter-Strike 2": a synthetic 429 from the source',
            '"Elden Ring": not sent, the run budget halted by the 429',
            f'"{rust}": a synthetic search dated AFTER the moment asked about, never read',
        ),
    )
