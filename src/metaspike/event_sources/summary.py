"""What an event source has been asked and what came of it, in counts, at a moment.

Counts only. No title, reference or payload leaves the source store through here. It
is point-in-time like everything else: what was observed after `as_of` is not part of
the picture, and is reported separately as a bare count so that a store filled later
is not mistaken for coverage that existed then.
"""

from collections import Counter
from datetime import datetime

from pydantic import AwareDatetime, Field

from metaspike.domain.model import FrozenModel
from metaspike.domain.source_policy import SourcePolicy, StageClearance
from metaspike.event_sources.candidates import PromotionStatus
from metaspike.event_sources.store import EventSourceStore


class GameSourceSummary(FrozenModel):
    canonical_game_id: str
    source_game_ids: tuple[str, ...]
    last_checked_at: AwareDatetime | None
    fetches_ok: int = Field(ge=0)
    fetches_failed: int = Field(ge=0)
    posts: int = Field(ge=0)
    candidates_classified: int = Field(ge=0)
    candidates_promoted: int = Field(ge=0)
    candidates_not_promoted: int = Field(ge=0)


class EventSourceSummary(FrozenModel):
    source: str
    rules_version: str
    as_of: AwareDatetime
    # False: nothing had been asked of the source by `as_of`. Not the same as "asked
    # and found nothing", which is `collected` with zero posts.
    collected: bool
    stage_clearance: tuple[StageClearance, ...]
    fetches_ok: int = Field(ge=0)
    fetches_failed: int = Field(ge=0)
    fetches_not_sent: int = Field(ge=0)
    last_success_at: AwareDatetime | None
    last_failure_at: AwareDatetime | None
    games_checked: int = Field(ge=0)
    posts_observed: int = Field(ge=0)
    candidates: int = Field(ge=0)
    candidates_classified: int = Field(ge=0)
    candidates_promoted: int = Field(ge=0)
    candidates_not_promoted: int = Field(ge=0)
    candidates_unresolved: int = Field(ge=0)
    refusals: dict[str, int]
    promoted_event_types: dict[str, int]
    # Requests made after `as_of`. They are not part of this picture.
    fetches_after_as_of: int = Field(ge=0)
    games: tuple[GameSourceSummary, ...]


def summarise(
    store: EventSourceStore, policy: SourcePolicy, rules_version: str, as_of: datetime
) -> EventSourceSummary:
    source = policy.source
    every_fetch = [record for record in store.fetches() if record.source == source]
    fetches = [record for record in every_fetch if record.observed_at <= as_of]
    candidates = [
        candidate
        for candidate in store.latest_candidates(as_of, rules_version)
        if candidate.source == source
    ]
    posts = [item for item in store.latest_items(as_of) if item.source == source]
    promoted = [c for c in candidates if c.status is PromotionStatus.PROMOTED]
    ok = [record for record in fetches if record.succeeded]
    failed = [record for record in fetches if record.error_code is not None]
    game_ids = sorted({record.canonical_game_id for record in ok if record.canonical_game_id})
    games = []
    for game_id in game_ids:
        own_fetches = [record for record in fetches if record.canonical_game_id == game_id]
        own = [c for c in candidates if c.canonical_game_id == game_id]
        own_ok = [record for record in own_fetches if record.succeeded]
        games.append(
            GameSourceSummary(
                canonical_game_id=game_id,
                source_game_ids=tuple(sorted({record.game.value for record in own_fetches})),
                last_checked_at=max((record.observed_at for record in own_ok), default=None),
                fetches_ok=len(own_ok),
                fetches_failed=sum(record.error_code is not None for record in own_fetches),
                posts=len(own),
                candidates_classified=sum(c.classified for c in own),
                candidates_promoted=sum(c.status is PromotionStatus.PROMOTED for c in own),
                candidates_not_promoted=sum(c.status is PromotionStatus.NOT_PROMOTED for c in own),
            )
        )
    return EventSourceSummary(
        source=source,
        rules_version=rules_version,
        as_of=as_of,
        collected=bool(fetches),
        stage_clearance=policy.stage_clearance,
        fetches_ok=len(ok),
        fetches_failed=len(failed),
        fetches_not_sent=len(fetches) - len(ok) - len(failed),
        last_success_at=max((record.observed_at for record in ok), default=None),
        last_failure_at=max((record.observed_at for record in failed), default=None),
        games_checked=len(game_ids),
        posts_observed=len(posts),
        candidates=len(candidates),
        candidates_classified=sum(c.classified for c in candidates),
        candidates_promoted=len(promoted),
        candidates_not_promoted=len(candidates) - len(promoted),
        candidates_unresolved=sum(c.canonical_game_id is None for c in candidates),
        refusals=dict(sorted(Counter(r.value for c in candidates for r in c.refusals).items())),
        promoted_event_types=dict(
            sorted(
                Counter(
                    c.proposed_event_type.value for c in promoted if c.proposed_event_type
                ).items()
            )
        ),
        fetches_after_as_of=len(every_fetch) - len(fetches),
        games=tuple(games),
    )
