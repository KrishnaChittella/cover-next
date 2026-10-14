"""What a bounded sample of YouTube says about one game's creator supply at a moment.

A reading describes a SAMPLE: the videos a few bounded searches returned when they were
asked. It is not the YouTube market and cannot become it:

    - a search returns what matched, in the order asked for, up to the pages fetched;
    - `totalResults` is the source's approximation and is never read as a count;
    - the supported videos are a lower bound on what exists, and say nothing above it;
    - an empty or partial sample is not evidence of low competition.

So the reading never says how much competition there is. It says how many videos the
sample held, how many of those are supported as being about the game, from how many
channels, how recently, and how complete the sample was. Whether that is "substantial"
or "limited" needs a threshold nobody has calibrated, and none is invented here.

Time. A reading at `as_of` uses only searches and details RETRIEVED by `as_of`. A
search answers for the moment it was asked: one made later is not a picture of earlier,
whatever dates its videos carry, and is not used. Counters are the latest retrieved by
`as_of` and are reported with that retrieval time; they are never a history. Nothing
dated after `as_of` is used, however it got into the store: a search result or a video
detail whose publication time is later than the moment read at contributes nothing to
it, even when it was retrieved before that moment. Anything
retrieved more than 30 days before `as_of` has passed YouTube's retention limit and is
not read.
"""

from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, Literal

from pydantic import AwareDatetime, Field

from metaspike.creator_sources.association import (
    ASSOCIATION_RULES_VERSION,
    AssociationState,
    GameNames,
    Reason,
    associate,
)
from metaspike.creator_sources.store import CreatorSupplyStore
from metaspike.creator_sources.youtube import (
    RETENTION_DAYS,
    FetchStatus,
    SearchFetch,
    VideoDetail,
)
from metaspike.domain.model import FrozenModel
from metaspike.domain.signals import SourceErrorCode

READING_VERSION: Final = "creator_supply_reading_v1"
# Why a video has no details in a reading although some were retrieved: they are dated
# after the moment read at, so none of them is used.
DETAILS_DATED_AFTER_MOMENT: Final = "video_details_dated_after_the_moment_read_at"
_RETENTION = timedelta(days=RETENTION_DAYS)
_DAY = timedelta(days=1)


class SupplyState(StrEnum):
    """What the sample allows to be said. Never how much competition there is."""

    # The game has not been asked about, as far as was known at the moment.
    NOT_QUERIED = "not_queried"
    # It was asked about, but too long ago: the answers are past the retention limit.
    EXPIRED = "expired"
    # Asked, and the sample is not whole: a page limit, the quota, or a failure.
    INCOMPLETE = "incomplete"
    # Asked completely, and no video came back. NOT evidence of low competition.
    NO_RESULTS = "no_results"
    # Videos came back and none is supported as being about the game.
    AMBIGUOUS = "ambiguous"
    # At least one supported video, from a sample that ran to its end.
    OBSERVED = "observed"


class QueryCompleteness(StrEnum):
    COMPLETE = "complete"  # the last page fetched said there are no more
    PAGE_LIMITED = "page_limited"  # more pages exist and were not fetched
    QUOTA_LIMITED = "quota_limited"  # the quota stopped it
    FAILED = "failed"  # a request failed


class QuerySample(FrozenModel):
    """The latest run of one query as known at the moment."""

    query_id: str
    text: str
    run_id: str
    retrieved_at: AwareDatetime
    completeness: QueryCompleteness
    pages_fetched: int = Field(ge=0)
    videos_returned: int = Field(ge=0)
    entries_rejected: int = Field(ge=0)
    error_code: SourceErrorCode | None = None
    api_error_reason: str | None = None


class SampledVideo(FrozenModel):
    """One video in the sample. Its title stays in the store."""

    video_id: str
    channel_id: str
    published_at: AwareDatetime
    association: AssociationState
    reasons: tuple[str, ...]
    # How many of the sampled queries returned it.
    returned_by_queries: int = Field(ge=1)
    details_retrieved_at: AwareDatetime | None = None
    # As retrieved at `details_retrieved_at`. None: the response did not carry it.
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None


class CreatorSupplyReading(FrozenModel):
    reading_version: Literal["creator_supply_reading_v1"] = READING_VERSION
    association_rules_version: str = ASSOCIATION_RULES_VERSION
    canonical_game_id: str
    as_of: AwareDatetime
    state: SupplyState
    # Always False. A sample is never the market, and nothing here estimates it.
    total_competition_known: Literal[False] = False
    queries: tuple[QuerySample, ...] = ()
    sample_complete: bool = False
    earliest_retrieved_at: AwareDatetime | None = None
    latest_retrieved_at: AwareDatetime | None = None
    # Distinct videos the sampled queries returned, and how they stand for the game.
    candidate_videos: int = Field(default=0, ge=0)
    supported_videos: int = Field(default=0, ge=0)
    uncertain_videos: int = Field(default=0, ge=0)
    excluded_videos: int = Field(default=0, ge=0)
    # Results returned by more than one query, counted once above.
    duplicate_results: int = Field(default=0, ge=0)
    # Among SUPPORTED videos only.
    supported_channels: int = Field(default=0, ge=0)
    most_videos_from_one_channel: int = Field(default=0, ge=0)
    supported_published_last_24h: int = Field(default=0, ge=0)
    supported_published_last_7d: int = Field(default=0, ge=0)
    newest_supported_published_at: AwareDatetime | None = None
    supported_with_view_count: int = Field(default=0, ge=0)
    supported_without_view_count: int = Field(default=0, ge=0)
    # Videos whose retrieved details were not used because they are dated after `as_of`.
    details_withheld_as_future_dated: int = Field(default=0, ge=0)
    # Searches retrieved by `as_of` that are past the retention limit and were not read.
    expired_searches: int = Field(default=0, ge=0)
    videos: tuple[SampledVideo, ...] = ()
    summary: str


def _completeness(pages: Sequence[SearchFetch]) -> QueryCompleteness:
    last = pages[-1]
    if last.status is FetchStatus.NOT_SENT or last.error_code is SourceErrorCode.QUOTA_EXHAUSTED:
        return QueryCompleteness.QUOTA_LIMITED
    if last.status is FetchStatus.FAILED:
        return QueryCompleteness.FAILED
    if last.next_page_token is not None:
        return QueryCompleteness.PAGE_LIMITED
    return QueryCompleteness.COMPLETE


_SUMMARIES = {
    SupplyState.NOT_QUERIED: (
        "Not queried: no search for this game had been made by this moment. Nothing is "
        "known about its creator supply. That is not low competition."
    ),
    SupplyState.EXPIRED: (
        "Expired: this game was last asked about more than 30 days before this moment, "
        "past YouTube's retention limit. Nothing current is held."
    ),
    SupplyState.NO_RESULTS: (
        "The bounded sample ran to its end and returned no video. That is what this "
        "sample held, not evidence of low competition."
    ),
}


def read_creator_supply(
    store: CreatorSupplyStore,
    game: GameNames,
    catalogue: Sequence[GameNames],
    related_titles: Sequence[str],
    as_of: datetime,
) -> CreatorSupplyReading:
    """The creator-supply sample for one game as known at `as_of`. Reads; changes nothing."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    game_id = game.canonical_game_id
    retrieved = [f for f in store.searches(as_of) if f.query.canonical_game_id == game_id]
    current = [f for f in retrieved if as_of - f.retrieved_at <= _RETENTION]
    expired = len(retrieved) - len(current)
    if not current:
        state = SupplyState.EXPIRED if retrieved else SupplyState.NOT_QUERIED
        return CreatorSupplyReading(
            canonical_game_id=game_id,
            as_of=as_of,
            state=state,
            expired_searches=expired,
            summary=_SUMMARIES[state],
        )

    # For each query, its latest run: one run is one sample of that question.
    latest_run: dict[str, str] = {}
    for fetch in current:  # already in retrieval order
        latest_run[fetch.query.query_id] = fetch.run_id
    samples: list[QuerySample] = []
    returned: dict[str, list[SearchFetch]] = {}
    search_titles: dict[str, tuple[str, str, datetime]] = {}
    for query_id, run_id in latest_run.items():
        pages = [f for f in current if f.query.query_id == query_id and f.run_id == run_id]
        samples.append(
            QuerySample(
                query_id=query_id,
                text=pages[0].query.text,
                run_id=run_id,
                retrieved_at=pages[-1].retrieved_at,
                completeness=_completeness(pages),
                pages_fetched=sum(page.status is FetchStatus.OK for page in pages),
                videos_returned=sum(len(page.results) for page in pages),
                entries_rejected=sum(page.rejected for page in pages),
                error_code=pages[-1].error_code,
                api_error_reason=pages[-1].api_error_reason,
            )
        )
        for page in pages:
            for result in page.results:
                # A video published after the moment read at cannot be in a search
                # retrieved by it; anything that claims to be is left out.
                if result.published_at > as_of:
                    continue
                seen = returned.setdefault(result.video_id, [])
                if page.query.query_id not in {f.query.query_id for f in seen}:
                    seen.append(page)
                search_titles.setdefault(
                    result.video_id, (result.title, result.channel_id, result.published_at)
                )

    # The latest details of each video retrieved by the moment, and when.
    details: dict[str, tuple[VideoDetail, datetime]] = {}
    for batch in store.videos(as_of):
        if as_of - batch.retrieved_at > _RETENTION:
            continue
        for found in batch.details:
            details[found.video_id] = (found, batch.retrieved_at)
    # A detail can be retrieved by the moment and still claim a publication time after
    # it: the parser tolerates a small clock difference against the RETRIEVAL time, and
    # that tolerance is not a licence to read the future. Such a detail contradicts the
    # moment it would be used at, so nothing of it is used: not its classification, its
    # channel, its date or its counters. No earlier detail is put in its place either,
    # since two details that disagree cannot be reconciled by guessing. The video falls
    # back to its search snippet and stays uncertain, and the reason says why.
    future_dated = {
        video_id for video_id, (found, _) in details.items() if found.published_at > as_of
    }
    for video_id in future_dated:
        del details[video_id]

    videos: list[SampledVideo] = []
    for video_id, pages_with_it in returned.items():
        title, channel_id, published_at = search_titles[video_id]
        held = details.get(video_id)
        detail = None if held is None else held[0]
        verdict = associate(game, catalogue, related_titles, title, detail)
        videos.append(
            SampledVideo(
                video_id=video_id,
                channel_id=channel_id if detail is None else detail.channel_id,
                published_at=published_at if detail is None else detail.published_at,
                association=verdict.state,
                reasons=tuple(
                    DETAILS_DATED_AFTER_MOMENT
                    if reason is Reason.NO_VIDEO_DETAILS and video_id in future_dated
                    else reason.value
                    for reason in verdict.reasons
                ),
                returned_by_queries=len(pages_with_it),
                details_retrieved_at=None if held is None else held[1],
                view_count=None if detail is None else detail.view_count,
                like_count=None if detail is None else detail.like_count,
                comment_count=None if detail is None else detail.comment_count,
            )
        )
    videos.sort(key=lambda video: (video.association.value, video.published_at, video.video_id))

    supported = [v for v in videos if v.association is AssociationState.SUPPORTED]
    uncertain = sum(v.association is AssociationState.UNCERTAIN for v in videos)
    excluded = sum(v.association is AssociationState.EXCLUDED for v in videos)
    channels = Counter(video.channel_id for video in supported)
    complete = all(sample.completeness is QueryCompleteness.COMPLETE for sample in samples)
    if not complete:
        state = SupplyState.INCOMPLETE
    elif not videos:
        state = SupplyState.NO_RESULTS
    elif not supported:
        state = SupplyState.AMBIGUOUS
    else:
        state = SupplyState.OBSERVED
    return CreatorSupplyReading(
        canonical_game_id=game_id,
        as_of=as_of,
        state=state,
        queries=tuple(sorted(samples, key=lambda sample: (sample.retrieved_at, sample.query_id))),
        sample_complete=complete,
        earliest_retrieved_at=min(sample.retrieved_at for sample in samples),
        latest_retrieved_at=max(sample.retrieved_at for sample in samples),
        candidate_videos=len(videos),
        supported_videos=len(supported),
        uncertain_videos=uncertain,
        excluded_videos=excluded,
        duplicate_results=sum(len(pages) - 1 for pages in returned.values()),
        supported_channels=len(channels),
        most_videos_from_one_channel=max(channels.values(), default=0),
        supported_published_last_24h=sum(as_of - v.published_at <= _DAY for v in supported),
        supported_published_last_7d=sum(as_of - v.published_at <= 7 * _DAY for v in supported),
        newest_supported_published_at=max((v.published_at for v in supported), default=None),
        supported_with_view_count=sum(v.view_count is not None for v in supported),
        supported_without_view_count=sum(v.view_count is None for v in supported),
        details_withheld_as_future_dated=len(future_dated & returned.keys()),
        expired_searches=expired,
        videos=tuple(videos),
        summary=_summary(state, len(videos), len(supported), uncertain, excluded, len(channels)),
    )


def _summary(
    state: SupplyState,
    candidates: int,
    supported: int,
    uncertain: int,
    excluded: int,
    channels: int,
) -> str:
    if state is SupplyState.NO_RESULTS:
        return _SUMMARIES[state]
    held = (
        f"The sample held {candidates} video(s): {supported} supported as being about "
        f"this game (from {channels} channel(s)), {uncertain} uncertain, {excluded} excluded."
    )
    if state is SupplyState.INCOMPLETE:
        return (
            f"Incomplete sample. {held} The sample did not run to its end, so even the "
            "supported count is partial. Total competition is unknown."
        )
    if state is SupplyState.AMBIGUOUS:
        return (
            f"Ambiguous. {held} No video in the sample is supported as being about this "
            "game. That is not low competition: it is no established evidence."
        )
    return (
        f"{held} At least {supported} video(s) from at least {channels} channel(s) exist; "
        "how many more is unknown. A sample is a lower bound, never the market."
    )
