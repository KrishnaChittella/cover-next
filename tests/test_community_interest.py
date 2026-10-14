"""Offline Reddit community interest (U6.2). Nothing here has ever been a Reddit response.

MetaSpike has never called Reddit. Every body in this file is SYNTHETIC, written in the
shape of a Reddit Listing with invented ids, titles, scores and counts. No test opens
a socket, reads a credential or touches a cloud resource.
"""

import json
import socket
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from metaspike import cli
from metaspike.catalogue.generate import generate_catalogue
from metaspike.community_sources.association import (
    ASSOCIATION_RULES_VERSION,
    Reason,
    associate,
)
from metaspike.community_sources.collect import MAX_PAGES_PER_QUERY, collect_community_interest
from metaspike.community_sources.reading import (
    CommunityInterestReading,
    QueryCompleteness,
    read_community_interest,
)
from metaspike.community_sources.reddit import (
    API_HOST,
    DEFAULT_REQUEST_BUDGET,
    MAX_LIMIT,
    REDDIT_DATA_POLICY,
    RETENTION_HOURS,
    CommunityQuery,
    CommunityScope,
    FetchStatus,
    InvalidListing,
    RedditAdapter,
    RedditPost,
    RedditReplay,
    RequestBudget,
    collection_refusal,
    parse_listing,
)
from metaspike.community_sources.store import CommunityStore
from metaspike.community_sources.synthetic import (
    SyntheticCommunity,
    _listing,
    _Post,
    build_synthetic_community,
)
from metaspike.community_sources.themes import (
    THEME_RULES_VERSION,
    Theme,
    candidate_angles,
    themes_of,
)
from metaspike.creator_sources.association import AssociationState, GameNames, usable_name
from metaspike.creator_sources.reading import SupplyState
from metaspike.domain.signals import SourceErrorCode
from metaspike.domain.source_policy import (
    ApprovalStatus,
    DeploymentStage,
    Permission,
    SourcePurpose,
)
from metaspike.evidence.assemble import COMMUNITY_INTEREST, assess_evidence, publication_refusal
from metaspike.evidence.model import (
    EvidenceKind,
    EvidenceReport,
    EvidenceState,
    InputProvenance,
    Origin,
    Standing,
)
from metaspike.evidence.provenance import local_store_provenance
from metaspike.evidence.render import render
from metaspike.publishing.artifacts import SCHEMA_VERSION
from metaspike.sources.credentials import Secret
from metaspike.sources.registry import SourcePolicyError
from tests.test_evidence import SYNTHETIC_AS_OF, World

REPO = Path(__file__).parent.parent
AS_OF = SYNTHETIC_AS_OF
HOUR = timedelta(hours=1)
REDDIT = "reddit_data"
GAME = "2128bf4d-7afc-4d2b-9803-3afae37bb7f3"  # Rust
OTHER = "0ace3a7a-a923-4f5b-9e9a-fc8a88e8c1c9"  # Dota 2
DECLARED = InputProvenance(origin=Origin.SYNTHETIC, reference="synthetic-community-interest-v1")
RUST = GameNames.of(GAME, "Rust")
DOTA = GameNames.of(OTHER, "Dota 2")
CONSOLE = GameNames.of("bdea9df5-8fdd-4bcb-8acb-9e9fe2a2d8d2", "Rust Console Edition")
CATALOGUE = [RUST, DOTA, CONSOLE]


def child(post_id: str = "abc123", **data: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": post_id,
        "subreddit": "playrust",
        "title": "A title",
        "permalink": f"/r/playrust/comments/{post_id}/x/",
        "created_utc": (AS_OF - HOUR).timestamp(),
    }
    return {"kind": "t3", "data": {**base, **data}}


def listing(children: list[object], after: object = None) -> dict[str, object]:
    return {"kind": "Listing", "data": {"after": after, "before": None, "children": children}}


def query(
    text: str = "Rust",
    scope: CommunityScope = CommunityScope.DEDICATED,
    subreddit: str | None = "playrust",
    game: str = GAME,
    **extra: object,
) -> CommunityQuery:
    return CommunityQuery(
        canonical_game_id=game,
        text=text,
        scope=scope,
        subreddit=subreddit,
        limit=5,
        **extra,  # type: ignore[arg-type,unused-ignore]
    )


class Replay:
    """An offline replay directory, which records every request it was asked."""

    def __init__(self, directory: Path) -> None:
        self.replay = RedditReplay(directory)

    @property
    def requests(self) -> list[httpx.Request]:
        return self.replay.requests

    def adapter(
        self, budget: RequestBudget, moment: datetime = AS_OF, run: str = "run", **extra: object
    ) -> RedditAdapter:
        return RedditAdapter(self.replay, budget, run_id=run, now=lambda: moment, **extra)  # type: ignore[arg-type]


# --- the listing ----------------------------------------------------------------------------


def test_a_listing_is_read_field_by_field_and_nothing_is_invented() -> None:
    retrieved = AS_OF
    body = listing(
        [
            child(
                "k1",
                title="Kept",
                score=12,
                num_comments=0,
                link_flair_text="Question",
                url="https://example.invalid/x",
                author="never-read",
                selftext="never read",
            ),
            child("k2"),  # no score, no comments, no flair
            {"kind": "t1", "data": {}},  # a comment, not a post
            child("bad1", created_utc="yesterday"),
            child("bad2", created_utc=(AS_OF + 2 * HOUR).timestamp()),  # after its retrieval
            child("bad3", title="  "),
            child("bad4", score="12"),  # a counter that is not a number
            child("bad5", num_comments=-1),
            {"kind": "t3"},
            "not an object",
        ],
        after="t3_k2",
    )
    parsed = parse_listing(body, retrieved)
    kept, bare = parsed.posts
    assert (kept.post_id, kept.subreddit, kept.title, kept.score, kept.num_comments) == (
        "k1",
        "playrust",
        "Kept",
        12,
        0,
    )
    assert kept.flair == "Question" and kept.reserved_link and kept.permalink.startswith("/r/")
    assert kept.created_at == AS_OF - HOUR
    # Absent is None, a reported zero is 0, and they are not the same.
    assert (bare.score, bare.num_comments, bare.flair, bare.reserved_link) == (
        None,
        None,
        None,
        False,
    )
    assert kept.num_comments == 0 and kept.num_comments is not None
    assert parsed.rejected == 8 and parsed.after == "t3_k2"
    # No author, body or link is kept: only what the reading needs.
    assert set(RedditPost.model_fields) == {
        "post_id",
        "subreddit",
        "title",
        "permalink",
        "created_at",
        "score",
        "num_comments",
        "flair",
        "reserved_link",
    }
    assert "never" not in kept.model_dump_json()
    wrongs: list[object] = [
        None,
        [],
        {"kind": "Listing"},
        {"kind": "Listing", "data": {"children": {}}},
        {"kind": "t3", "data": {}},
    ]
    for wrong in wrongs:
        with pytest.raises(InvalidListing):
            parse_listing(wrong, retrieved)
    assert parse_listing(listing([], after=""), retrieved).after is None


def test_a_query_states_its_scope_and_sends_only_what_it_should() -> None:
    dedicated = query()
    assert dedicated.path() == "/r/playrust/search"
    assert dedicated.parameters(None) == {
        "q": "Rust",
        "sort": "new",
        "t": "week",
        "limit": 5,
        "type": "link",
        "raw_json": 1,
        "restrict_sr": 1,
    }
    assert dedicated.parameters("t3_x")["after"] == "t3_x"
    everywhere = query(scope=CommunityScope.SITE_WIDE, subreddit=None)
    assert everywhere.path() == "/search" and "restrict_sr" not in everywhere.parameters(None)
    # A site-wide query names no subreddit, and only it.
    with pytest.raises(ValueError, match="site-wide"):
        query(scope=CommunityScope.SITE_WIDE, subreddit="playrust")
    with pytest.raises(ValueError, match="site-wide"):
        query(scope=CommunityScope.DEDICATED, subreddit=None)
    for size in (0, MAX_LIMIT + 1):
        with pytest.raises(ValueError):
            CommunityQuery(
                canonical_game_id=GAME, text="Rust", scope=CommunityScope.SITE_WIDE, limit=size
            )
    # Why a query was asked is part of it; its identity ignores the page.
    assert query(reason="why").reason == "why"
    ids = {
        query().query_id,
        query(subreddit="gaming").query_id,
        query(scope=CommunityScope.GENERAL_GAMING).query_id,
        query(text="rust").query_id,
        query(unrelated_subreddits=("rust",)).query_id,
    }
    assert len(ids) == 5


# --- requests, budget, rate limits ----------------------------------------------------------


def test_pages_failures_and_rate_limits_are_recorded_as_what_they_were(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_socket(*_: object, **__: object) -> None:
        raise AssertionError("a Reddit test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", no_socket)
    one = _Post("p1", "playrust", "Rust one", AS_OF - HOUR)
    two = _Post("p2", "playrust", "Rust two", AS_OF - 2 * HOUR)
    _listing(
        tmp_path,
        "a",
        "/r/playrust/search",
        "Rust",
        [one],
        next_after="t3_p1",
        headers={"x-ratelimit-remaining": "57", "x-ratelimit-reset": "300"},
    )
    _listing(tmp_path, "b", "/r/playrust/search", "Rust", [two], after="t3_p1", next_after="t3_p2")
    _listing(tmp_path, "c", "/r/DotA2/search", "Dota 2", [])
    _listing(
        tmp_path,
        "d",
        "/r/GlobalOffensive/search",
        "CS2",
        [],
        status=429,
        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "412"},
    )
    replay = Replay(tmp_path)
    budget = RequestBudget(10)
    store = CommunityStore(tmp_path / "store")
    summary = collect_community_interest(
        replay.adapter(budget),
        [
            query(),
            query("Dota 2", subreddit="DotA2", game=OTHER),
            query("CS2", subreddit="GlobalOffensive"),
            query("Elden", subreddit="Eldenring"),
        ],
        store,
        max_pages=2,
    )
    # Two pages, then a complete empty search, then a 429 that halts the budget, so the
    # last query is never sent.
    assert len(replay.requests) == 4
    assert (summary.requests_sent, summary.requests_rate_limited, summary.requests_not_sent) == (
        4,
        1,
        1,
    )
    # Fetches made at the same moment have no meaningful order; find each by what it is.
    fetched = store.listings()
    first, second = sorted(
        (f for f in fetched if f.query.subreddit == "playrust"), key=lambda f: f.page_index
    )
    (empty,) = [f for f in fetched if f.query.subreddit == "DotA2"]
    (limited,) = [f for f in fetched if f.query.subreddit == "GlobalOffensive"]
    (unsent,) = [f for f in fetched if f.query.subreddit == "Eldenring"]
    assert (first.ratelimit_remaining, first.ratelimit_reset_seconds) == (57.0, 300.0)
    assert second.stopped_by == "page_limit" and second.next_after == "t3_p2"
    assert empty.status is FetchStatus.OK and empty.posts == () and empty.next_after is None
    assert (limited.status, limited.error_code, limited.http_status) == (
        FetchStatus.RATE_LIMITED,
        SourceErrorCode.RATE_LIMITED,
        429,
    )
    assert limited.ratelimit_remaining == 0.0 and budget.halted_by_source
    assert unsent.status is FetchStatus.NOT_SENT and unsent.requests_spent == 0
    # Each request carried an identifying User-Agent and, with no token, no credential.
    for request in replay.requests:
        assert request.url.host == API_HOST
        assert request.headers["user-agent"].startswith("metaspike-offline")
        assert "authorization" not in request.headers
    # An unrecorded request fails; it is never answered with an invented empty listing.
    failed = Replay(tmp_path).adapter(RequestBudget(1)).search(query("Tetris"), 0, None)
    assert (failed.status, failed.error_code) == (FetchStatus.FAILED, SourceErrorCode.NETWORK_ERROR)
    _listing(tmp_path / "e", "server", "/r/playrust/search", "Rust", [], status=500)
    server = Replay(tmp_path / "e").adapter(RequestBudget(1)).search(query(), 0, None)
    assert (server.status, server.error_code) == (FetchStatus.FAILED, SourceErrorCode.HTTP_ERROR)
    # A token, if one is ever held, travels as a header and is never stored.
    keyed = Replay(tmp_path)
    page = keyed.adapter(RequestBudget(1), token=Secret("tok-not-real")).search(query(), 0, None)
    assert keyed.requests[0].headers["authorization"] == "Bearer tok-not-real"
    assert "tok-not-real" not in page.model_dump_json()
    # The budget is local, small and cannot be raised above its default.
    assert DEFAULT_REQUEST_BUDGET == 20
    with pytest.raises(ValueError):
        RequestBudget(DEFAULT_REQUEST_BUDGET + 1)
    for pages in (0, MAX_PAGES_PER_QUERY + 1):
        with pytest.raises(ValueError):
            collect_community_interest(
                replay.adapter(RequestBudget(1)), [query()], store, max_pages=pages
            )
    # Nothing in the package reads the environment or builds a live client.
    source = "\n".join(
        p.read_text() for p in sorted((REPO / "src/metaspike/community_sources").glob("*.py"))
    )
    for forbidden in ("os.environ", "getenv", "build_client", "praw", "client_secret"):
        assert forbidden not in source, forbidden


def test_the_store_is_append_only_and_keeps_48_hours(tmp_path: Path) -> None:
    _listing(
        tmp_path, "a", "/r/playrust/search", "Rust", [_Post("p1", "playrust", "Rust", AS_OF - HOUR)]
    )
    store = CommunityStore(tmp_path / "store")
    collect_community_interest(Replay(tmp_path).adapter(RequestBudget(1)), [query()], store)
    files = {p: p.read_bytes() for p in (tmp_path / "store").rglob("*.ndjson")}
    assert store.add_listings(store.listings()) == 0
    assert {p: p.read_bytes() for p in (tmp_path / "store").rglob("*.ndjson")} == files
    assert store.listings(AS_OF - timedelta(seconds=1)) == ()
    assert RETENTION_HOURS == 48 and REDDIT_DATA_POLICY.retention_days == 2
    assert store.expired(AS_OF + 48 * HOUR) == ()
    assert len(store.expired(AS_OF + 48 * HOUR + timedelta(seconds=1))) == 1
    names = [RUST]
    assert (
        read_community_interest(store, RUST, names, [], AS_OF + 48 * HOUR).state
        is SupplyState.OBSERVED
    )
    gone = read_community_interest(store, RUST, names, [], AS_OF + 49 * HOUR)
    assert gone.state is SupplyState.EXPIRED and gone.discussions == () and gone.expired_pages == 1


# --- which game is a discussion about -------------------------------------------------------


def post(title: str, subreddit: str = "playrust") -> RedditPost:
    return RedditPost(
        post_id="x", subreddit=subreddit, title=title, permalink="/r/x/", created_at=AS_OF - HOUR
    )


def standing(
    title: str,
    subreddit: str,
    *queries: CommunityQuery,
    game: GameNames = RUST,
    related: tuple[str, ...] = (),
) -> tuple[AssociationState, tuple[Reason, ...]]:
    result = associate(game, CATALOGUE, related, post(title, subreddit), list(queries))
    return result.state, result.reasons


def test_a_title_match_alone_never_supports_a_discussion() -> None:
    assert ASSOCIATION_RULES_VERSION == "community_association_rules_v1"
    own = query()
    general = query(scope=CommunityScope.GENERAL_GAMING, subreddit="gaming")
    everywhere = query(
        scope=CommunityScope.SITE_WIDE, subreddit=None, unrelated_subreddits=("rust",)
    )
    community = (AssociationState.SUPPORTED, (Reason.GAME_COMMUNITY,))
    # In the game's own community a post need not repeat the name, and may ask or compare.
    assert standing("Base designs this week", "playrust", own) == community
    assert standing("Rust or Dota 2 for a new player?", "playrust", own) == community
    assert standing("Dota 2 is better", "playrust", own) == (
        AssociationState.UNCERTAIN,
        (Reason.ONLY_OTHER_GAMES_NAMED,),
    )
    # In a general gaming community the name must be there, alone.
    assert standing("Rust wipe day highlights", "gaming", general) == (
        AssociationState.SUPPORTED,
        (Reason.NAMED_IN_GAMING_COMMUNITY,),
    )
    assert standing("Rust and Dota 2 both got patches", "gaming", general)[1] == (
        Reason.SEVERAL_GAMES_NAMED,
    )
    assert standing("Best survival games", "gaming", general)[1] == (Reason.NAME_NOT_IN_TITLE,)
    assert standing(
        "Rust Voice Props Pack is out", "gaming", general, related=("Rust: Voice Props Pack",)
    )[1] == (Reason.RELATED_PRODUCT_NAMED,)
    # Anywhere else a title alone establishes nothing.
    assert standing("Rust (2024) film review", "movies", everywhere) == (
        AssociationState.UNCERTAIN,
        (Reason.NO_DECLARED_COMMUNITY,),
    )
    # Declared unrelated communities, and a longer catalogue name, exclude.
    assert standing("How do I satisfy the borrow checker?", "rust", everywhere) == (
        AssociationState.EXCLUDED,
        (Reason.DECLARED_UNRELATED_COMMUNITY,),
    )
    assert standing("Rust Console Edition wipe day", "gaming", general) == (
        AssociationState.EXCLUDED,
        (Reason.PART_OF_A_LONGER_GAME_NAME,),
    )
    assert (
        standing("Rust Console Edition wipe day", "gaming", general, game=CONSOLE)[0]
        is AssociationState.SUPPORTED
    )
    # The strongest declared scope among the queries that returned it decides; a
    # declared-unrelated community overrides everything.
    assert standing("Base designs", "playrust", everywhere, own) == community
    assert standing("Rust is great", "rust", own, everywhere)[0] is AssociationState.EXCLUDED
    # Whole words only; and community names compare without case.
    assert standing("Trust issues", "gaming", general)[1] == (Reason.NAME_NOT_IN_TITLE,)
    assert standing("Base designs", "PlayRust", own) == community


def test_catalogue_names_that_cannot_be_matched_as_words_are_not_used(world: World) -> None:
    """A defect found here, shared with U6.1: an alias in another script became a bare
    number, and matched every title containing it."""
    assert not usable_name("데드 스페이스 3") and not usable_name("포털 2")
    assert not usable_name("portal2.exe") and not usable_name("BG") and not usable_name("D2")
    assert usable_name("CS2") and usable_name("Dota 2") and usable_name("Rust")
    names = [
        GameNames.of(g.canonical_game_id, g.name, g.aliases) for g in world.state.catalogue.games
    ]
    assert not any(len(name) == 1 and name[0].isdigit() for entry in names for name in entry.names)
    general = query(scope=CommunityScope.GENERAL_GAMING, subreddit="gaming")
    rust = next(entry for entry in names if entry.canonical_game_id == world.ids["rust"])
    patched = associate(rust, names, [], post("Rust patch 2.3 notes", "gaming"), [general])
    assert patched.state is AssociationState.SUPPORTED  # the number names no other game


# --- themes and angles ----------------------------------------------------------------------


def test_themes_are_narrow_rules_and_angles_need_two_posts_that_share_something() -> None:
    assert THEME_RULES_VERSION == "community_theme_rules_v1"
    assert themes_of("How do I stop getting offlined?") == (Theme.QUESTION, Theme.HOW_TO)
    assert themes_of("Patch 2.3 left base decay broken") == (Theme.UPDATE, Theme.ISSUE)
    assert themes_of("Patch 2.3 broke base decay") == (Theme.UPDATE,)  # "broke" is not listed
    assert themes_of("Rust vs DayZ in 2026") == (Theme.COMPARISON,)
    assert themes_of("Is it worth it now") == (Theme.QUESTION, Theme.COMPARISON)
    assert themes_of("Best base designs this week") == ()  # unclassified, not guessed
    assert themes_of("Updated my base") == (Theme.UPDATE,)
    t = AS_OF - HOUR
    posts = [
        ("a", "playrust", "Patch 2.3 broke base decay", t, AS_OF),
        ("b", "playrust", "Thoughts on patch 2.3", t, AS_OF),
        ("c", "PlayRust", "Update 2.3 is brutal", t, AS_OF),
        ("d", "playrust", "How do I stop getting offlined?", t, AS_OF),
        ("e", "playrust", "How to stop getting offlined as a solo", t, AS_OF),
        ("f", "playrust", "Rust or DayZ for a new player?", t, AS_OF),
        ("g", "playrust", "Best base designs", t, AS_OF),
    ]
    angles, counts, once = candidate_angles(posts, frozenset({"rust"}))
    patch, offline = angles
    assert patch.themes == (Theme.UPDATE,) and patch.about == ("version 2.3",)
    assert [p.post_id for p in patch.supporting_posts] == ["a", "b", "c"]
    assert patch.distinct_subreddits == 1  # case does not make another community
    assert offline.themes == (Theme.QUESTION, Theme.HOW_TO)
    assert set(offline.about) >= {"offlined"} and "rust" not in offline.about
    assert [p.post_id for p in offline.supporting_posts] == ["d", "e"]
    assert all(angle.status == "candidate_not_validated" for angle in angles)
    assert all(len(angle.supporting_posts) >= 2 for angle in angles)
    # One post with a theme is observed once: never an angle on its own.
    assert "f" in once and "g" not in once
    # "X or Y?" is a question to these rules, not a comparison: "or" alone is too common.
    assert {c.theme: c.supported_posts for c in counts}[Theme.COMPARISON] == 0
    # Nothing shared, nothing grouped: two questions about different things are no angle.
    lone, _, alone = candidate_angles(
        [
            ("x", "s", "How do I craft armour?", t, AS_OF),
            ("y", "s", "Why is decay so fast?", t, AS_OF),
        ],
        frozenset(),
    )
    assert lone == () and set(alone) == {"x", "y"}


# --- the scenario, at several moments -------------------------------------------------------


@pytest.fixture(scope="module")
def world() -> World:
    return World()


@pytest.fixture(scope="module")
def community(world: World, tmp_path_factory: pytest.TempPathFactory) -> SyntheticCommunity:
    games = {g.slug: (g.canonical_game_id, g.name) for g in world.state.catalogue.games}
    return build_synthetic_community(tmp_path_factory.mktemp("community"), games, AS_OF)


def reading(
    world: World, community: SyntheticCommunity, slug: str, moment: datetime = AS_OF
) -> CommunityInterestReading:
    names = [
        GameNames.of(g.canonical_game_id, g.name, g.aliases) for g in world.state.catalogue.games
    ]
    game = next(entry for entry in names if entry.canonical_game_id == world.ids[slug])
    return read_community_interest(community.store, game, names, [], moment)


def test_the_scenario_shows_what_was_discussed_and_never_how_much(
    world: World, community: SyntheticCommunity
) -> None:
    rust = reading(world, community, "rust")
    # F. The dedicated query stopped at its page limit, so the sample is incomplete.
    assert rust.state is SupplyState.INCOMPLETE and not rust.sample_complete
    by_scope = {q.scope: q for q in rust.queries}
    assert by_scope[CommunityScope.DEDICATED].completeness is QueryCompleteness.PAGE_LIMITED
    assert by_scope[CommunityScope.GENERAL_GAMING].completeness is QueryCompleteness.COMPLETE
    assert by_scope[CommunityScope.SITE_WIDE].completeness is QueryCompleteness.COMPLETE
    assert by_scope[CommunityScope.DEDICATED].reason == "what Rust's own community is discussing"
    states = {d.post_id: (d.association.value, d.reasons) for d in rust.discussions}
    assert states == {
        "synr001": ("supported", ("in_the_game_community_declared_by_the_plan",)),
        "synr002": ("supported", ("in_the_game_community_declared_by_the_plan",)),
        "synr003": ("supported", ("in_the_game_community_declared_by_the_plan",)),
        "synr004": ("supported", ("in_the_game_community_declared_by_the_plan",)),
        "synr005": ("supported", ("in_the_game_community_declared_by_the_plan",)),
        "synr006": ("supported", ("in_the_game_community_declared_by_the_plan",)),
        "synr008": ("uncertain", ("several_games_named",)),
        "synr009": ("excluded", ("in_a_community_declared_unrelated",)),  # M: the language
        "synr010": ("uncertain", ("no_declared_community",)),  # B: the film
    }
    # H. The post returned by two queries is one post.
    assert rust.duplicate_results == 1
    assert next(d for d in rust.discussions if d.post_id == "synr003").returned_by_queries == 2
    # A, C, D, L. Two distinct supported topics, each a candidate angle with its posts.
    patch, offline = rust.angles
    assert patch.about == ("version 2.3",) and len(patch.supporting_posts) == 3  # C
    assert offline.themes == (Theme.QUESTION, Theme.HOW_TO) and "offlined" in offline.about  # A, L
    assert all(
        p.retrieved_at <= AS_OF and p.created_at <= AS_OF
        for a in rust.angles
        for p in a.supporting_posts
    )
    assert rust.observed_once == ("synr006",)
    # Counts keep their meanings: a missing score or comment count is absent, not zero.
    assert (rust.supported_with_score, rust.supported_without_score) == (5, 1)
    assert (rust.supported_with_comment_count, rust.supported_without_comment_count) == (5, 1)
    assert next(d for d in rust.discussions if d.post_id == "synr004").score is None
    assert rust.total_discussion_known is False and rust.supported_subreddits == 1
    document = rust.model_dump_json()
    assert "offlined every wipe" not in document and "borrow checker" not in document  # no titles
    # E. A bounded search that returned nothing; G. a 429; and a query never sent.
    dota = reading(world, community, "dota-2")
    assert dota.state is SupplyState.NO_RESULTS and "not evidence of low interest" in dota.summary
    cs = reading(world, community, "counter-strike-2")
    assert cs.state is SupplyState.INCOMPLETE
    assert cs.queries[0].completeness is QueryCompleteness.RATE_LIMITED
    elden = reading(world, community, "elden-ring")
    assert elden.queries[0].completeness is QueryCompleteness.BUDGET_LIMITED
    untouched = reading(world, community, "minecraft")
    assert (
        untouched.state is SupplyState.NOT_QUERIED
        and "That is not low interest" in untouched.summary
    )
    # Deterministic.
    assert reading(world, community, "rust") == rust


def test_a_reading_uses_only_what_existed_and_was_retrieved_by_its_moment(
    world: World, community: SyntheticCommunity, tmp_path: Path
) -> None:
    now = reading(world, community, "rust")
    # I, J. A post created after the moment, in a search made after it: absent.
    assert "synr011" not in now.model_dump_json()
    assert all(d.created_at <= AS_OF and d.retrieved_at <= AS_OF for d in now.discussions)
    # K. The score and comments of the latest retrieval by the moment, never later ones.
    earlier = reading(world, community, "rust", AS_OF - 2 * HOUR)
    then = next(d for d in earlier.discussions if d.post_id == "synr002")
    later = next(d for d in now.discussions if d.post_id == "synr002")
    assert (then.score, then.num_comments, then.retrieved_at) == (12, 9, AS_OF - 3 * HOUR)
    assert (later.score, later.num_comments, later.retrieved_at) == (340, 77, AS_OF - HOUR)
    assert "340" not in earlier.model_dump_json()
    # The earlier reading has the first run's second page; the later one, the later run.
    assert "synr007" in {d.post_id for d in earlier.discussions}
    assert "synr007" not in {d.post_id for d in now.discussions}
    # A current search cannot reconstruct an older one: before any search, nothing.
    assert reading(world, community, "rust", AS_OF - 4 * HOUR).state is SupplyState.NOT_QUERIED
    after = reading(world, community, "rust", AS_OF + 2 * HOUR)
    assert "synr011" in {d.post_id for d in after.discussions}
    # A post claiming creation after the moment, inside the parser's tolerance of its
    # own retrieval, is left out of the earlier reading.
    _listing(
        tmp_path,
        "a",
        "/r/playrust/search",
        "Rust",
        [
            _Post("early", "playrust", "Rust early", AS_OF - HOUR),
            _Post("future", "playrust", "Rust future", AS_OF + 20 * timedelta(minutes=1)),
        ],
    )
    store = CommunityStore(tmp_path / "store")
    collect_community_interest(
        Replay(tmp_path).adapter(RequestBudget(1), AS_OF - 10 * timedelta(minutes=1)),
        [query()],
        store,
    )
    assert {p.post_id for f in store.listings() for p in f.posts} == {"early", "future"}
    at = read_community_interest(store, RUST, [RUST], [], AS_OF)
    assert [d.post_id for d in at.discussions] == ["early"]
    edge = read_community_interest(store, RUST, [RUST], [], AS_OF + 20 * timedelta(minutes=1))
    assert {d.post_id for d in edge.discussions} == {"early", "future"}
    with pytest.raises(ValueError, match="timezone-aware"):
        read_community_interest(store, RUST, [RUST], [], datetime(2026, 10, 20, 12))


# --- inside the game evidence ---------------------------------------------------------------


@pytest.fixture(scope="module")
def report(world: World, community: SyntheticCommunity) -> EvidenceReport:
    inputs = world.inputs(community=community.store, community_provenance=DECLARED)
    return assess_evidence(inputs, AS_OF, world.scope)


def test_the_game_evidence_carries_community_interest_when_it_is_given(
    world: World, community: SyntheticCommunity, report: EvidenceReport
) -> None:
    assert report.report_version == "game_evidence_v3"
    assert report.inputs[REDDIT].standing is Standing.SYNTHETIC
    rust = report.game(world.ids["rust"])
    row = rust.source(REDDIT)
    assert row.kind is EvidenceKind.COMMUNITY_INTEREST and row.state is EvidenceState.INCOMPLETE
    assert rust.community_interest == reading(world, community, "rust")
    assert row.provenance.standing is Standing.SYNTHETIC and not row.usable
    assert REDDIT in rust.synthetic_sources and REDDIT not in rust.usable_sources
    assert (
        not [s for s in rust.sources if s.source == COMMUNITY_INTEREST] and len(rust.sources) == 7
    )
    codes = [gap.code for gap in rust.gaps]
    assert (
        codes[1] == "community_interest_is_a_sample"
        and "community_interest_not_integrated" not in codes
    )
    assert report.game(world.ids["minecraft"]).gaps[1].code == "community_interest_not_sampled"
    # Discussion is not demand: no family, no count, no score moved.
    plain = assess_evidence(world.inputs(), AS_OF, world.scope)
    for game in report.games:
        other = plain.game(game.canonical_game_id)
        assert (game.score_v0, game.confidence_v0) == (other.score_v0, other.confidence_v0)
        assert game.demand == other.demand and game.events == other.events
        assert game.independent_demand_families == other.independent_demand_families
        assert game.sources[:5] == other.sources[:5] and game.creator_supply is None
    view = next(p for p in report.policies if p.source == REDDIT)
    assert view.purpose is SourcePurpose.COMMUNITY_INTEREST and not view.publishable
    text = render(report)
    assert "reddit_data             SYNTHETIC" in text
    assert (
        "Community interest: a SAMPLE of discussion, not the community (incomplete)  "
        "[SYNTHETIC]. Total discussion: UNKNOWN." in text
    )
    assert (
        "CANDIDATE ANGLE (not validated): update or patch breakdown — about version 2.3; "
        "3 supporting posts" in text
    )
    assert "rate_limited (rate_limited)" in text and "budget_limited (quota_exhausted)" in text
    for hidden in ("offlined every wipe", "borrow checker", "340", "synthetic-author"):
        assert hidden not in text, hidden
    assert "it holds synthetic evidence" in publication_refusal(report)
    # Without the input, the reading says exactly what it said before.
    assert REDDIT not in plain.inputs and all(g.community_interest is None for g in plain.games)
    absent = plain.game(world.ids["rust"]).source(COMMUNITY_INTEREST)
    assert absent.state is EvidenceState.NOT_INTEGRATED and absent.policy is None
    assert (
        "community interest    -                   not_integrated: no source, no policy"
        in render(plain)
    )


def test_reddit_data_is_refused_until_access_is_approved_and_synthetic_never_counts(
    world: World, community: SyntheticCommunity, tmp_path: Path
) -> None:
    policy = REDDIT_DATA_POLICY
    assert (
        policy.approval_required.value == "required"
        and policy.approval_status is ApprovalStatus.PENDING
    )
    assert policy.derived_metrics_allowed is Permission.UNKNOWN
    assert {c.stage: c.cleared for c in policy.stage_clearance} == {
        DeploymentStage.PRIVATE_DEVELOPMENT: True,
        DeploymentStage.PRIVATE_COLLECTION: False,
        DeploymentStage.PUBLIC_LAUNCH: False,
        DeploymentStage.COMMERCIAL_PRODUCTION: False,
    }
    # Access is not approved, so the policy refuses Reddit data at every stage.
    for stage in DeploymentStage:
        assert "required approval has not been granted" in (
            policy.registration_refusal(SourcePurpose.COMMUNITY_INTEREST, stage) or ""
        )
    # The scenario's store, handed over as a local store: every post in it links to the
    # reserved domain, so it holds no Reddit data and is read, as synthetic.
    reimported = assess_evidence(
        world.inputs(community=community.store, community_provenance=local_store_provenance("x")),
        AS_OF,
        [world.ids["rust"]],
    )
    row = reimported.game(world.ids["rust"]).source(REDDIT)
    assert (
        row.provenance.standing is Standing.SYNTHETIC and "reserved .invalid" in row.provenance.note
    )
    assert not row.usable and reimported.inputs[REDDIT].standing is Standing.UNVERIFIED
    # A local store with nothing synthetic about it may hold Reddit data: refused.
    plain = _Post("q1w2e3", "playrust", "Rust base help", AS_OF - HOUR)
    _listing(tmp_path, "a", "/r/playrust/search", "Rust", [plain])
    recording = tmp_path / "a.json"
    recording.write_text(
        recording.read_text().replace(
            "https://example.invalid/synthetic/q1w2e3", "https://i.redd.it/q1w2e3.png"
        )
    )
    store = CommunityStore(tmp_path / "store")
    collect_community_interest(Replay(tmp_path).adapter(RequestBudget(1)), [query()], store)
    assert not store.listings()[0].posts[0].reserved_link
    unknown = world.inputs(community=store, community_provenance=local_store_provenance("y"))
    with pytest.raises(SourcePolicyError, match="reddit_data: policy does not permit collection"):
        assess_evidence(unknown, AS_OF, [world.ids["rust"]])
    # Even synthetic data is read at private development only.
    for stage in (DeploymentStage.PRIVATE_COLLECTION, DeploymentStage.PUBLIC_LAUNCH):
        inputs = world.inputs(
            state=generate_catalogue(),
            records=[],
            wikimedia=None,
            community=community.store,
            community_provenance=DECLARED,
            stage=stage,
        )
        with pytest.raises(SourcePolicyError, match="reddit_data"):
            assess_evidence(inputs, AS_OF)
    # Nothing can be declared verified; there is no Reddit recording to pin.
    claim = InputProvenance(origin=Origin.RECORDED_RESPONSE, reference="trust me")
    with pytest.raises(ValueError, match="verified only with the digest of a pinned"):
        assess_evidence(
            world.inputs(community=community.store, community_provenance=claim),
            AS_OF,
            [world.ids["rust"]],
        )
    without = {k: v for k, v in cli.EVIDENCE_POLICIES.items() if k != REDDIT}
    with pytest.raises(SourcePolicyError, match="reddit_data: no source policy"):
        assess_evidence(
            world.inputs(
                community=community.store, community_provenance=DECLARED, policies=without
            ),
            AS_OF,
            [world.ids["rust"]],
        )


def test_reddit_data_cannot_reach_a_published_artifact() -> None:
    assert SCHEMA_VERSION == "1.5.0"
    for path in (REPO / "src/metaspike/publishing").glob("*.py"):
        assert (
            "community_sources" not in path.read_text() and "reddit" not in path.read_text().lower()
        )
    assert not any("reddit" in p.read_text().lower() for p in (REPO / "schemas").glob("*.json"))
    users = sorted(
        p.relative_to(REPO / "src/metaspike").as_posix()
        for p in (REPO / "src/metaspike").rglob("*.py")
        if "community_sources" in p.read_text() and "community_sources" not in p.parts
    )
    # U6.3's opportunity rubric reads community readings.
    assert users == ["cli.py", "evidence/assemble.py", "evidence/model.py", "opportunity/rules.py"]
    for area in ("jobs", "scheduling", "warehouse", "pipeline", "sources"):
        for path in (REPO / "src/metaspike" / area).glob("*.py"):
            assert "reddit" not in path.read_text().lower(), path


def test_the_command_demonstrates_community_interest_offline(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    community: SyntheticCommunity,
) -> None:
    def no_socket(*_: object, **__: object) -> None:
        raise AssertionError("the evidence command tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", no_socket)

    def run(*arguments: str) -> tuple[int, str, str]:
        code = cli.main(["evidence", "--as-of", AS_OF.isoformat(), *arguments])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    code, text, _ = run("--synthetic-scenario", "--game", "rust", "--game", "dota-2")
    assert (
        code == cli.EXIT_OK
        and run("--synthetic-scenario", "--game", "rust", "--game", "dota-2")[1] == text
    )
    assert "reddit_data             SYNTHETIC" in text and "synr011" not in text
    assert '"Counter-Strike 2": a synthetic 429 from the source' in text
    code, printed, _ = run("--synthetic-scenario", "--game", "rust", "--json")
    document = json.loads(printed)
    assert document["report_version"] == "game_evidence_v3"
    interest = document["games"][0]["community_interest"]
    assert interest["state"] == "incomplete" and interest["total_discussion_known"] is False
    assert len(interest["angles"]) == 2
    code, printed, _ = run("--community-dir", str(community.store.root), "--game", "rust", "--json")
    stored = json.loads(printed)
    assert code == cli.EXIT_OK and stored["inputs"][REDDIT]["standing"] == "unverified"
    assert stored["games"][0]["community_interest"] == interest
    code, out, err = run("--synthetic-scenario", "--community-dir", str(community.store.root))
    assert (code, out) == (cli.EXIT_INVALID_CATALOGUE, "") and "never mixed" in err
    with pytest.raises(SystemExit):
        cli.main(["evidence", "--help"])
    options = capsys.readouterr().out
    assert "--community-dir" in options
    for word in ("--trust", "--verified", "--live", "--reddit-token", "--client-secret"):
        assert word not in options


# --- U6.3: no request reaches Reddit while access is pending -------------------------------


def test_a_network_capable_client_cannot_send_reddit_requests_while_access_is_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[httpx.Request] = []

    def anything(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=listing([child("real01")]))

    def no_socket(*_: object, **__: object) -> None:
        raise AssertionError("a Reddit request tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", no_socket)
    # An ordinary client, and a client whose transport would answer anything: both are
    # refused before a single request, at every stage, because approval is pending.
    capable = [httpx.Client(), httpx.Client(transport=httpx.MockTransport(anything))]
    for client in capable:
        for stage in DeploymentStage:
            with pytest.raises(SourcePolicyError, match="required approval has not been granted"):
                RedditAdapter(
                    client,
                    RequestBudget(5),
                    run_id="r",
                    policy=REDDIT_DATA_POLICY,
                    stage=stage,
                )
        # Without a policy and a stage it is not even considered.
        with pytest.raises(SourcePolicyError, match="needs Reddit's source policy"):
            RedditAdapter(client, RequestBudget(5), run_id="r")
        # Another source's policy is not Reddit's.
        with pytest.raises(SourcePolicyError, match="not reddit_data"):
            RedditAdapter(
                client,
                RequestBudget(5),
                run_id="r",
                policy=cli.EVIDENCE_POLICIES["wikimedia_pageviews"],
                stage=DeploymentStage.PRIVATE_DEVELOPMENT,
            )
    assert sent == []
    assert collection_refusal(REDDIT_DATA_POLICY, DeploymentStage.PRIVATE_DEVELOPMENT) == (
        "policy does not permit collection (required approval has not been granted)"
    )
    # Calling the label "synthetic" changes nothing: the check is on the policy, and the
    # collector cannot be reached with a network client at all.
    synthetic_label = InputProvenance(origin=Origin.SYNTHETIC, reference="real data, honestly")
    assert synthetic_label.synthetic  # a label the adapter never looks at
    # A policy that cleared collection would let a network client be built. The check is
    # the policy, not the client.
    approved = REDDIT_DATA_POLICY.model_copy(update={"approval_status": ApprovalStatus.APPROVED})
    built = RedditAdapter(
        httpx.Client(transport=httpx.MockTransport(anything)),
        RequestBudget(1),
        run_id="r",
        policy=approved,
        stage=DeploymentStage.PRIVATE_DEVELOPMENT,
    )
    assert (
        not built.offline
        and collection_refusal(approved, DeploymentStage.PRIVATE_DEVELOPMENT) is None
    )
    # And it would refuse again, before sending, at a stage the approved policy does not clear.
    later = RedditAdapter(
        httpx.Client(transport=httpx.MockTransport(anything)),
        RequestBudget(1),
        run_id="r",
        policy=approved,
        stage=DeploymentStage.PRIVATE_DEVELOPMENT,
    )
    later._stage = DeploymentStage.PUBLIC_LAUNCH  # as if the stage changed underneath it
    with pytest.raises(SourcePolicyError):
        later.search(query(), 0, None)
    assert sent == []
    # The approved copy above exists only in this test; the real policy is unchanged.
    assert REDDIT_DATA_POLICY.approval_status is ApprovalStatus.PENDING

    # The offline replay still works, deterministically, with no network at all.
    _listing(
        tmp_path, "a", "/r/playrust/search", "Rust", [_Post("p1", "playrust", "Rust", AS_OF - HOUR)]
    )
    first, second = CommunityStore(tmp_path / "one"), CommunityStore(tmp_path / "two")
    for store in (first, second):
        collect_community_interest(Replay(tmp_path).adapter(RequestBudget(1)), [query()], store)
    assert [f.model_dump_json() for f in first.listings()] == [
        f.model_dump_json() for f in second.listings()
    ]
    assert Replay(tmp_path).adapter(RequestBudget(1)).offline
    # The replay holds no client or transport: it cannot reach anything.
    assert not any(
        isinstance(v, httpx.Client | httpx.BaseTransport)
        for v in vars(RedditReplay(tmp_path)).values()
    )
