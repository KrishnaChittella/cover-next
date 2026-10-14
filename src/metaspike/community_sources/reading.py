"""What a bounded sample of Reddit says about one game's community at a moment.

A reading describes a SAMPLE: the posts a few bounded searches returned when they were
asked. It is not Reddit, and not the players:

    - a search returns what matched, up to the pages fetched;
    - a post's score is not a number of interested people, and a comment count is not
      independent demand; a post returned twice is one post;
    - an empty or partial sample is not evidence of low interest;
    - the communities searched are the ones the plan declared, and nothing else.

Time. A reading at `as_of` uses only pages RETRIEVED by `as_of`, and only posts CREATED
by `as_of`. A search made later is not a picture of earlier, whatever the creation
times of its posts. Scores and comment counts are those of the retrieval used, shown
with its time; a later count is never shown for an earlier moment. Pages retrieved
more than 48 hours before `as_of` are past the retention limit and are not read.
"""

from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, Literal

from pydantic import AwareDatetime, Field

from metaspike.community_sources.association import (
    ASSOCIATION_RULES_VERSION,
    associate,
)
from metaspike.community_sources.reddit import (
    RETENTION_HOURS,
    CommunityQuery,
    CommunityScope,
    FetchStatus,
    ListingFetch,
    RedditPost,
)
from metaspike.community_sources.store import CommunityStore
from metaspike.community_sources.themes import (
    THEME_RULES_VERSION,
    CandidateAngle,
    Theme,
    ThemeCount,
    candidate_angles,
    themes_of,
)
from metaspike.creator_sources.association import AssociationState, GameNames
from metaspike.creator_sources.reading import SupplyState
from metaspike.domain.model import FrozenModel
from metaspike.domain.signals import SourceErrorCode

READING_VERSION: Final = "community_interest_reading_v1"
_RETENTION = timedelta(hours=RETENTION_HOURS)
_DAY = timedelta(days=1)

# The same six states creator supply uses, for the same reasons.
InterestState = SupplyState


class QueryCompleteness(StrEnum):
    COMPLETE = "complete"
    PAGE_LIMITED = "page_limited"
    RATE_LIMITED = "rate_limited"  # the source said not now
    BUDGET_LIMITED = "budget_limited"  # the local budget refused to send
    FAILED = "failed"


class QuerySample(FrozenModel):
    query_id: str
    text: str
    scope: CommunityScope
    subreddit: str | None
    reason: str
    run_id: str
    retrieved_at: AwareDatetime
    completeness: QueryCompleteness
    pages_fetched: int = Field(ge=0)
    posts_returned: int = Field(ge=0)
    entries_rejected: int = Field(ge=0)
    error_code: SourceErrorCode | None = None


class Discussion(FrozenModel):
    """One post in the sample. Its title stays in the store."""

    post_id: str
    subreddit: str
    created_at: AwareDatetime
    # The retrieval whose score and comment count are shown.
    retrieved_at: AwareDatetime
    association: AssociationState
    reasons: tuple[str, ...]
    themes: tuple[Theme, ...]
    returned_by_queries: int = Field(ge=1)
    score: int | None = None
    num_comments: int | None = None
    flair: str | None = None


class CommunityInterestReading(FrozenModel):
    reading_version: Literal["community_interest_reading_v1"] = READING_VERSION
    association_rules_version: str = ASSOCIATION_RULES_VERSION
    theme_rules_version: str = THEME_RULES_VERSION
    canonical_game_id: str
    as_of: AwareDatetime
    state: SupplyState
    # Always False. A sample of posts is never the whole of Reddit's discussion.
    total_discussion_known: Literal[False] = False
    queries: tuple[QuerySample, ...] = ()
    sample_complete: bool = False
    earliest_retrieved_at: AwareDatetime | None = None
    latest_retrieved_at: AwareDatetime | None = None
    candidate_posts: int = Field(default=0, ge=0)
    supported_posts: int = Field(default=0, ge=0)
    uncertain_posts: int = Field(default=0, ge=0)
    excluded_posts: int = Field(default=0, ge=0)
    duplicate_results: int = Field(default=0, ge=0)
    # Among SUPPORTED posts only.
    supported_subreddits: int = Field(default=0, ge=0)
    supported_created_last_24h: int = Field(default=0, ge=0)
    supported_created_last_7d: int = Field(default=0, ge=0)
    newest_supported_created_at: AwareDatetime | None = None
    supported_with_score: int = Field(default=0, ge=0)
    supported_without_score: int = Field(default=0, ge=0)
    supported_with_comment_count: int = Field(default=0, ge=0)
    supported_without_comment_count: int = Field(default=0, ge=0)
    themes: tuple[ThemeCount, ...] = ()
    # Ids of supported posts whose theme no other supported post shares.
    observed_once: tuple[str, ...] = ()
    angles: tuple[CandidateAngle, ...] = ()
    expired_pages: int = Field(default=0, ge=0)
    discussions: tuple[Discussion, ...] = ()
    summary: str


def _completeness(pages: Sequence[ListingFetch]) -> QueryCompleteness:
    last = pages[-1]
    if last.status is FetchStatus.NOT_SENT:
        return QueryCompleteness.BUDGET_LIMITED
    if last.status is FetchStatus.RATE_LIMITED:
        return QueryCompleteness.RATE_LIMITED
    if last.status is FetchStatus.FAILED:
        return QueryCompleteness.FAILED
    if last.next_after is not None:
        return QueryCompleteness.PAGE_LIMITED
    return QueryCompleteness.COMPLETE


_FIXED = {
    SupplyState.NOT_QUERIED: (
        "Not queried: no community search for this game had been made by this moment. "
        "Nothing is known about what is discussed. That is not low interest."
    ),
    SupplyState.EXPIRED: (
        "Expired: the last community search for this game is older than the 48-hour "
        "retention limit. Nothing current is held."
    ),
    SupplyState.NO_RESULTS: (
        "The bounded sample ran to its end and returned no post. That is what these "
        "searches held, not evidence of low interest."
    ),
}


def read_community_interest(
    store: CommunityStore,
    game: GameNames,
    catalogue: Sequence[GameNames],
    related_titles: Sequence[str],
    as_of: datetime,
) -> CommunityInterestReading:
    """The community sample for one game as known at `as_of`. Reads; changes nothing."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    game_id = game.canonical_game_id
    retrieved = [f for f in store.listings(as_of) if f.query.canonical_game_id == game_id]
    current = [f for f in retrieved if as_of - f.retrieved_at <= _RETENTION]
    expired = len(retrieved) - len(current)
    if not current:
        state = SupplyState.EXPIRED if retrieved else SupplyState.NOT_QUERIED
        return CommunityInterestReading(
            canonical_game_id=game_id,
            as_of=as_of,
            state=state,
            expired_pages=expired,
            summary=_FIXED[state],
        )

    latest_run: dict[str, str] = {}
    for fetch in current:
        latest_run[fetch.query.query_id] = fetch.run_id
    samples: list[QuerySample] = []
    seen: dict[str, tuple[RedditPost, datetime]] = {}
    queries_of: dict[str, dict[str, CommunityQuery]] = {}
    for query_id, run_id in latest_run.items():
        pages = [f for f in current if f.query.query_id == query_id and f.run_id == run_id]
        query = pages[0].query
        samples.append(
            QuerySample(
                query_id=query_id,
                text=query.text,
                scope=query.scope,
                subreddit=query.subreddit,
                reason=query.reason,
                run_id=run_id,
                retrieved_at=pages[-1].retrieved_at,
                completeness=_completeness(pages),
                pages_fetched=sum(page.status is FetchStatus.OK for page in pages),
                posts_returned=sum(len(page.posts) for page in pages),
                entries_rejected=sum(page.rejected for page in pages),
                error_code=pages[-1].error_code,
            )
        )
        for page in pages:
            for post in page.posts:
                if post.created_at > as_of:
                    continue  # created after the moment: not part of what was known
                queries_of.setdefault(post.post_id, {})[query_id] = query
                held = seen.get(post.post_id)
                # The latest observation of the post among the pages used.
                if held is None or page.retrieved_at >= held[1]:
                    seen[post.post_id] = (post, page.retrieved_at)

    game_words = frozenset(token for name in game.names for token in name)
    discussions: list[Discussion] = []
    for post_id, (post, at) in seen.items():
        verdict = associate(
            game, catalogue, related_titles, post, list(queries_of[post_id].values())
        )
        discussions.append(
            Discussion(
                post_id=post_id,
                subreddit=post.subreddit,
                created_at=post.created_at,
                retrieved_at=at,
                association=verdict.state,
                reasons=tuple(reason.value for reason in verdict.reasons),
                themes=themes_of(post.title),
                returned_by_queries=len(queries_of[post_id]),
                score=post.score,
                num_comments=post.num_comments,
                flair=post.flair,
            )
        )
    discussions.sort(key=lambda d: (d.association.value, d.created_at, d.post_id))
    supported = [d for d in discussions if d.association is AssociationState.SUPPORTED]
    angles, counts, once = candidate_angles(
        [
            (d.post_id, d.subreddit, seen[d.post_id][0].title, d.created_at, d.retrieved_at)
            for d in supported
        ],
        game_words,
    )
    uncertain = sum(d.association is AssociationState.UNCERTAIN for d in discussions)
    excluded = sum(d.association is AssociationState.EXCLUDED for d in discussions)
    communities = Counter(d.subreddit.casefold() for d in supported)
    complete = all(sample.completeness is QueryCompleteness.COMPLETE for sample in samples)
    if not complete:
        state = SupplyState.INCOMPLETE
    elif not discussions:
        state = SupplyState.NO_RESULTS
    elif not supported:
        state = SupplyState.AMBIGUOUS
    else:
        state = SupplyState.OBSERVED
    return CommunityInterestReading(
        canonical_game_id=game_id,
        as_of=as_of,
        state=state,
        queries=tuple(sorted(samples, key=lambda s: (s.retrieved_at, s.query_id))),
        sample_complete=complete,
        earliest_retrieved_at=min(s.retrieved_at for s in samples),
        latest_retrieved_at=max(s.retrieved_at for s in samples),
        candidate_posts=len(discussions),
        supported_posts=len(supported),
        uncertain_posts=uncertain,
        excluded_posts=excluded,
        duplicate_results=sum(len(q) - 1 for q in queries_of.values() if q),
        supported_subreddits=len(communities),
        supported_created_last_24h=sum(as_of - d.created_at <= _DAY for d in supported),
        supported_created_last_7d=sum(as_of - d.created_at <= 7 * _DAY for d in supported),
        newest_supported_created_at=max((d.created_at for d in supported), default=None),
        supported_with_score=sum(d.score is not None for d in supported),
        supported_without_score=sum(d.score is None for d in supported),
        supported_with_comment_count=sum(d.num_comments is not None for d in supported),
        supported_without_comment_count=sum(d.num_comments is None for d in supported),
        themes=counts,
        observed_once=once,
        angles=angles,
        expired_pages=expired,
        discussions=tuple(discussions),
        summary=_summary(
            state,
            len(discussions),
            len(supported),
            uncertain,
            excluded,
            len(communities),
            len(angles),
        ),
    )


def _summary(
    state: SupplyState,
    candidates: int,
    supported: int,
    uncertain: int,
    excluded: int,
    communities: int,
    angles: int,
) -> str:
    if state is SupplyState.NO_RESULTS:
        return _FIXED[state]
    held = (
        f"The sample held {candidates} post(s): {supported} supported as being about this "
        f"game (in {communities} communit(ies)), {uncertain} uncertain, {excluded} excluded; "
        f"{angles} candidate angle(s)."
    )
    if state is SupplyState.INCOMPLETE:
        return f"Incomplete sample. {held} The searches did not run to their end."
    if state is SupplyState.AMBIGUOUS:
        return f"Ambiguous. {held} Nothing in it is established as being about this game."
    return f"{held} This is what the sample shows being discussed, not how much interest exists."
