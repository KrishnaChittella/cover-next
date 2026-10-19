"""SYNTHETIC YouTube-shaped responses for exercising the creator-supply path.

Nothing here is an observation. MetaSpike has never called YouTube and holds no real
response. Every video id, channel id, title, tag and counter below is invented, and
every response this writes carries an etag that begins with `SYNTHETIC`, which the
evidence reading recognises wherever the data is later read from.

The scenario writes response files, then passes them through the real adapter, quota
account and collector into a store, in four runs at different moments:

    run 1  two hours before the moment asked about
           "Rust"            two pages of several, stopped by the page limit
           "Dota 2"          a complete search that returns nothing
           "Counter-Strike 2"  the API answers that the quota is exceeded
           "Elden Ring"      not sent: by then the account has refused everything
    run 2  one hour before: "Rust gameplay", which returns one video a second time
    run 3  ninety minutes before, on a fresh account: "Elden Ring", complete, and
           nothing in it is supported
    run 4  one hour AFTER the moment asked about: "Rust" again, with a newer video

Run 4 exists to show that it is not read: a later search is not a picture of earlier.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from metaspike.creator_sources.collect import collect_creator_supply
from metaspike.creator_sources.store import CreatorSupplyStore
from metaspike.creator_sources.youtube import (
    YOUTUBE_DATA_POLICY,
    SearchQuery,
    YouTubeAdapter,
    YouTubeQuota,
    replay_youtube_client,
)
from metaspike.domain.source_policy import DeploymentStage

SCENARIO_NAME = "synthetic-creator-supply-v1"
SYNTHETIC_ETAG = "SYNTHETIC-not-a-youtube-response"
_HOUR = timedelta(hours=1)
_DAY = timedelta(days=1)
_GAMING, _SCIENCE, _HOWTO, _FILM = "20", "28", "26", "1"
_GAME_TOPIC = "https://en.wikipedia.org/wiki/Action-adventure_game"
_TECH_TOPIC = "https://en.wikipedia.org/wiki/Technology"
_NOTE = (
    "SYNTHETIC. Written by MetaSpike's synthetic creator-supply scenario in the shape "
    "the YouTube Data API documents. Not a response of YouTube; every id, title, "
    "channel and counter here is invented."
)


@dataclass(frozen=True)
class _Video:
    video_id: str
    channel: str
    title: str
    published: datetime
    category: str | None = _GAMING
    tags: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    views: int | None = 1000
    in_details: bool = True

    def search_item(self) -> dict[str, object]:
        return {
            "kind": "youtube#searchResult",
            "etag": SYNTHETIC_ETAG,
            "id": {"kind": "youtube#video", "videoId": self.video_id},
            "snippet": {
                "publishedAt": _stamp(self.published),
                "channelId": self.channel,
                "title": self.title,
                "description": "synthetic description that is never read",
                "channelTitle": "synthetic channel title that is never read",
                "liveBroadcastContent": "none",
            },
        }

    def resource(self) -> dict[str, object]:
        snippet: dict[str, object] = {
            "publishedAt": _stamp(self.published),
            "channelId": self.channel,
            "title": self.title,
            "description": "synthetic description that is never read",
        }
        if self.category is not None:
            snippet["categoryId"] = self.category
        if self.tags:
            snippet["tags"] = list(self.tags)
        resource: dict[str, object] = {
            "kind": "youtube#video",
            "etag": SYNTHETIC_ETAG,
            "id": self.video_id,
            "snippet": snippet,
        }
        if self.views is not None:
            resource["statistics"] = {
                "viewCount": str(self.views),
                "likeCount": str(self.views // 20),
                "commentCount": str(self.views // 100),
            }
        if self.topics:
            resource["topicDetails"] = {"topicCategories": list(self.topics)}
        return resource


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(directory: Path, name: str, request: dict[str, str], status: int, body: object) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    document = {"note": _NOTE, "request": request, "status": status, "body": body}
    (directory / f"{name}.json").write_text(json.dumps(document, indent=1), encoding="utf-8")


def _search(
    directory: Path,
    name: str,
    text: str,
    videos: list[_Video],
    *,
    token: str = "",
    next_token: str | None = None,
    total: int | None = None,
) -> None:
    body: dict[str, object] = {
        "kind": "youtube#searchListResponse",
        "etag": SYNTHETIC_ETAG,
        "regionCode": "US",
        # The API's own approximation. Deliberately large: it is never read as a count.
        "pageInfo": {"totalResults": total or len(videos), "resultsPerPage": len(videos)},
        "items": [video.search_item() for video in videos],
    }
    if next_token is not None:
        body["nextPageToken"] = next_token
    request = {"method": "search", "q": text}
    if token:
        request["pageToken"] = token
    _write(directory, name, request, 200, body)


def _details(directory: Path, name: str, videos: list[_Video]) -> None:
    body = {
        "kind": "youtube#videoListResponse",
        "etag": SYNTHETIC_ETAG,
        "pageInfo": {"totalResults": len(videos), "resultsPerPage": len(videos)},
        "items": [video.resource() for video in videos if video.in_details],
    }
    request = {"method": "videos", "id": ",".join(video.video_id for video in videos)}
    _write(directory, name, request, 200, body)


@dataclass(frozen=True)
class SyntheticCreatorSupply:
    store: CreatorSupplyStore
    description: tuple[str, ...]


def build_synthetic_creator_supply(
    root: Path, games: dict[str, tuple[str, str]], as_of: datetime
) -> SyntheticCreatorSupply:
    """Write the scenario under `root` and return its store. Deterministic.

    `games` maps a slug to (canonical id, catalogue name) for `rust`, `dota-2`,
    `counter-strike-2` and `elden-ring`. Identity is not invented.
    """
    rust_id, rust = games["rust"]
    dota_id, dota = games["dota-2"]
    cs_id, cs = games["counter-strike-2"]
    elden_id, elden = games["elden-ring"]
    store = CreatorSupplyStore(root / "creator-supply")

    def collect(
        run: str,
        directory: Path,
        moment: datetime,
        queries: list[SearchQuery],
        quota: YouTubeQuota,
        pages: int,
    ) -> None:
        with replay_youtube_client(directory) as client:
            adapter = YouTubeAdapter(client, quota, run_id=run, now=lambda: moment)
            collect_creator_supply(
                adapter,
                YOUTUBE_DATA_POLICY,
                queries,
                store,
                stage=DeploymentStage.PRIVATE_DEVELOPMENT,
                max_pages=pages,
            )

    def ask(game_id: str, text: str) -> SearchQuery:
        return SearchQuery(canonical_game_id=game_id, text=text, max_results=5)

    a, b, c = "UCsynthetic00000000000A", "UCsynthetic00000000000B", "UCsynthetic00000000000C"
    wipe = _Video(
        "SYNrust0001",
        a,
        f"{rust} wipe day solo base",
        as_of - 5 * _HOUR,
        tags=("rust", "rust gameplay"),
    )
    raid = _Video("SYNrust0002", a, f"{rust} raid defence highlights", as_of - 20 * _HOUR)
    language = _Video(
        "SYNrust0003",
        b,
        f"{rust} programming tutorial: ownership",
        as_of - 2 * _DAY,
        category=_SCIENCE,
        topics=(_TECH_TOPIC,),
    )
    tools = _Video(
        "SYNrust0004",
        c,
        f"How to remove {rust.lower()} from old tools",
        as_of - 3 * _DAY,
        category=_HOWTO,
    )
    both = _Video("SYNrust0005", b, f"{rust} and {dota} funny moments", as_of - 4 * _DAY)
    survival = _Video("SYNrust0006", c, "Best survival games this year", as_of - 6 * _DAY)
    hidden = _Video(
        "SYNrust0007", b, f"{rust} monthly update first look", as_of - 9 * _DAY, views=None
    )
    snippet_only = _Video(
        "SYNrust0008", c, f"{rust} beginner guide", as_of - 12 * _DAY, in_details=False
    )
    extra = _Video(
        "SYNrust0009",
        c,
        f"{rust} gameplay: first night",
        as_of - 30 * _HOUR,
        category=None,
        topics=(_GAME_TOPIC,),
    )
    newer = _Video("SYNrust0010", a, f"{rust} patch notes rundown", as_of + _HOUR / 2)
    dlc = _Video("SYNelden001", a, f"{elden} and {cs} crossover ranking", as_of - 2 * _DAY)
    film = _Video(
        "SYNelden002", b, f"{elden} movie announcement reaction", as_of - 3 * _DAY, category=_FILM
    )
    bare = _Video("SYNelden003", c, f"{elden} lore explained", as_of - 5 * _DAY, category=None)

    first = root / "responses" / "run-1"
    _search(
        first,
        "rust-page-1",
        rust,
        [wipe, raid, language, tools, both],
        next_token="SYNP2",
        total=1_000_000,
    )
    _search(
        first,
        "rust-page-2",
        rust,
        [survival, hidden, snippet_only],
        token="SYNP2",
        next_token="SYNP3",
        total=1_000_000,
    )
    _details(
        first, "rust-details", [wipe, raid, language, tools, both, survival, hidden, snippet_only]
    )
    _search(first, "dota", dota, [])
    _write(
        first,
        "counter-strike-quota",
        {"method": "search", "q": cs},
        403,
        {
            "error": {
                "code": 403,
                "message": "synthetic quota error",
                "errors": [{"domain": "youtube.quota", "reason": "quotaExceeded"}],
            },
        },
    )
    collect(
        "synthetic-run-1",
        first,
        as_of - 2 * _HOUR,
        [ask(rust_id, rust), ask(dota_id, dota), ask(cs_id, cs), ask(elden_id, elden)],
        YouTubeQuota(search_calls=10, other_units=10),
        pages=2,
    )

    second = root / "responses" / "run-2"
    gameplay = f"{rust} gameplay"
    _search(second, "rust-gameplay", gameplay, [wipe, extra])
    _details(second, "rust-gameplay-details", [wipe, extra])
    collect(
        "synthetic-run-2",
        second,
        as_of - _HOUR,
        [ask(rust_id, gameplay)],
        YouTubeQuota(search_calls=5, other_units=5),
        pages=1,
    )

    third = root / "responses" / "run-3"
    _search(third, "elden", elden, [dlc, film, bare])
    _details(third, "elden-details", [dlc, film, bare])
    collect(
        "synthetic-run-3",
        third,
        as_of - 3 * _HOUR / 2,
        [ask(elden_id, elden)],
        YouTubeQuota(search_calls=5, other_units=5),
        pages=1,
    )

    fourth = root / "responses" / "run-4"
    _search(fourth, "rust-later", rust, [newer, wipe])
    _details(fourth, "rust-later-details", [newer, wipe])
    collect(
        "synthetic-run-4",
        fourth,
        as_of + _HOUR,
        [ask(rust_id, rust)],
        YouTubeQuota(search_calls=5, other_units=5),
        pages=1,
    )

    return SyntheticCreatorSupply(
        store=store,
        description=(
            f'"{rust}": synthetic search, two of several pages, then a second query',
            f'"{dota}": synthetic search that returned nothing',
            f'"{cs}": synthetic quota error from the API',
            f'"{elden}": first not sent (quota), then a synthetic search with nothing supported',
            f'"{rust}": a synthetic search dated AFTER the moment asked about, never read',
        ),
    )
