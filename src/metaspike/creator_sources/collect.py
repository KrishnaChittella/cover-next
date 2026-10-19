"""Ask a bounded set of questions and keep the answers. Offline today, by construction.

One run asks each query for at most `max_pages` pages, then asks for the details of the
videos those pages returned. Every request is paid for from the quota account before it
is sent; a request that cannot be paid for is recorded as not sent, so a reading can
tell a quota-limited sample from a complete one. Nothing is retried: a second attempt
would be a second charge.
"""

from collections.abc import Sequence

from metaspike.creator_sources.store import CreatorSupplyStore
from metaspike.creator_sources.youtube import (
    MAX_IDS_PER_VIDEOS_CALL,
    FetchStatus,
    SearchFetch,
    SearchQuery,
    VideosFetch,
    YouTubeAdapter,
)
from metaspike.domain.model import FrozenModel
from metaspike.domain.source_policy import DeploymentStage, SourcePolicy, SourcePurpose
from metaspike.sources.registry import SourcePolicyError

# Self-imposed: a query is never paged further than this in one run.
MAX_PAGES_PER_QUERY = 5


class CollectionSummary(FrozenModel):
    """What one run asked and spent. Counts only."""

    queries: int
    search_requests_sent: int
    search_requests_not_sent: int
    search_requests_failed: int
    videos_requests_sent: int
    videos_requests_not_sent: int
    candidate_videos: int
    video_details_retrieved: int
    search_calls_spent: int
    other_units_spent: int
    search_calls_left: int
    other_units_left: int


def collect_creator_supply(
    adapter: YouTubeAdapter,
    policy: SourcePolicy,
    queries: Sequence[SearchQuery],
    store: CreatorSupplyStore,
    *,
    stage: DeploymentStage,
    max_pages: int = 1,
) -> CollectionSummary:
    """Run each query once. Raises `SourcePolicyError` before anything is sent."""
    if policy.source != adapter.source:
        raise SourcePolicyError(
            f"policy is for '{policy.source}' but the adapter is '{adapter.source}'"
        )
    refusal = policy.registration_refusal(SourcePurpose.CREATOR_SUPPLY, stage)
    if refusal is not None:
        raise SourcePolicyError(f"{adapter.source}: {refusal}")
    if not 1 <= max_pages <= MAX_PAGES_PER_QUERY:
        raise ValueError(f"max_pages must be between 1 and {MAX_PAGES_PER_QUERY}")
    ids = [query.query_id for query in queries]
    if len(ids) != len(set(ids)):
        raise ValueError("the same query was given more than once")

    searches: list[SearchFetch] = []
    for query in queries:
        token: str | None = None
        for page in range(max_pages):
            fetch = adapter.search(query, page, token)
            token = fetch.next_page_token
            last_allowed = page == max_pages - 1
            if fetch.status is FetchStatus.OK and token is not None and last_allowed:
                fetch = fetch.model_copy(update={"stopped_by": "page_limit"})
            searches.append(fetch)
            if fetch.status is not FetchStatus.OK or token is None:
                break
    store.add_searches(searches)

    wanted = list(dict.fromkeys(result.video_id for fetch in searches for result in fetch.results))
    details: list[VideosFetch] = [
        adapter.videos(wanted[start : start + MAX_IDS_PER_VIDEOS_CALL])
        for start in range(0, len(wanted), MAX_IDS_PER_VIDEOS_CALL)
    ]
    store.add_videos(details)

    quota = adapter.quota
    return CollectionSummary(
        queries=len(queries),
        search_requests_sent=sum(f.search_calls_spent > 0 for f in searches),
        search_requests_not_sent=sum(f.status is FetchStatus.NOT_SENT for f in searches),
        search_requests_failed=sum(f.status is FetchStatus.FAILED for f in searches),
        videos_requests_sent=sum(f.units_spent > 0 for f in details),
        videos_requests_not_sent=sum(f.status is FetchStatus.NOT_SENT for f in details),
        candidate_videos=len(wanted),
        video_details_retrieved=len({d.video_id for f in details for d in f.details}),
        search_calls_spent=sum(f.search_calls_spent for f in searches),
        other_units_spent=sum(f.units_spent for f in details),
        search_calls_left=quota.search_calls_left,
        other_units_left=quota.other_units_left,
    )
