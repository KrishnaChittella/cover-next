"""Ask each bounded query once, page by page, within a local request budget. Offline."""

from collections.abc import Sequence

from metaspike.community_sources.reddit import (
    CommunityQuery,
    FetchStatus,
    ListingFetch,
    RedditAdapter,
)
from metaspike.community_sources.store import CommunityStore
from metaspike.domain.model import FrozenModel

MAX_PAGES_PER_QUERY = 3  # self-imposed


class CommunityCollection(FrozenModel):
    queries: int
    requests_sent: int
    requests_not_sent: int
    requests_failed: int
    requests_rate_limited: int
    posts_returned: int
    budget_left: int


def collect_community_interest(
    adapter: RedditAdapter,
    queries: Sequence[CommunityQuery],
    store: CommunityStore,
    *,
    max_pages: int = 1,
) -> CommunityCollection:
    """Run each query once. Policy is the caller's: see `require_community_input`."""
    if not 1 <= max_pages <= MAX_PAGES_PER_QUERY:
        raise ValueError(f"max_pages must be between 1 and {MAX_PAGES_PER_QUERY}")
    ids = [query.query_id for query in queries]
    if len(ids) != len(set(ids)):
        raise ValueError("the same query was given more than once")
    pages: list[ListingFetch] = []
    for query in queries:
        after: str | None = None
        for page in range(max_pages):
            fetch = adapter.search(query, page, after)
            after = fetch.next_after
            if fetch.status is FetchStatus.OK and after is not None and page == max_pages - 1:
                fetch = fetch.model_copy(update={"stopped_by": "page_limit"})
            pages.append(fetch)
            if fetch.status is not FetchStatus.OK or after is None:
                break
    store.add_listings(pages)
    return CommunityCollection(
        queries=len(queries),
        requests_sent=sum(f.requests_spent for f in pages),
        requests_not_sent=sum(f.status is FetchStatus.NOT_SENT for f in pages),
        requests_failed=sum(f.status is FetchStatus.FAILED for f in pages),
        requests_rate_limited=sum(f.status is FetchStatus.RATE_LIMITED for f in pages),
        posts_returned=sum(len(f.posts) for f in pages),
        budget_left=adapter.budget.left,
    )
