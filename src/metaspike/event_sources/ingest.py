"""One ingestion pass: fetch, keep, read, and promote only what passes the gate.

    source --fetch--> items --classify--> candidates --promote--> EventObservation
                        |                     |                        |
                 EventSourceStore      EventSourceStore            EventStore

Three rules hold the layers apart:

1. Every item and every candidate goes to the source store. Only a promoted candidate's
   observation goes to the event store, which is all Event Heat reads.
2. A fetch that succeeded records a coverage check for the game that was asked about,
   whatever it returned: no posts, posts that classify as nothing, or candidates that
   were refused. Coverage means "the source was checked", not "an event was found". A
   fetch that failed, or was never sent, records none.
3. A game the catalogue does not bind gets no observation and no coverage.
"""

from collections import Counter
from collections.abc import Sequence
from datetime import datetime

from pydantic import Field

from metaspike.domain.model import FrozenModel
from metaspike.domain.source_policy import DeploymentStage, SourcePolicy, SourcePurpose
from metaspike.event_sources.base import (
    EventSourceAdapter,
    EventSourceFetch,
    SourceGame,
    SourceItem,
)
from metaspike.event_sources.candidates import (
    EVENT_CLASSIFICATION_RULES_V1,
    EventCandidate,
    EventClassificationRules,
    GameBinding,
    PromotionStatus,
    classify,
    promoted_observation,
    withdrawal,
)
from metaspike.event_sources.resolve import Resolver
from metaspike.event_sources.store import EventSourceStore, fetch_record
from metaspike.events.model import CoverageCheck, EventObservation
from metaspike.events.store import EventStore
from metaspike.sources.registry import SourcePolicyError


class TargetOutcome(FrozenModel):
    source_game_id: str
    canonical_game_id: str | None
    requests: int
    fetches_ok: int
    fetches_failed: int
    fetches_not_sent: int
    error_codes: tuple[str, ...] = ()
    posts: int
    coverage_recorded: bool


class IngestReport(FrozenModel):
    """What one pass did, in counts. No title and no payload: safe to print."""

    source: str
    rules_version: str
    stage: DeploymentStage
    targets: tuple[TargetOutcome, ...]
    requests: int = Field(ge=0)
    fetches_ok: int = Field(ge=0)
    fetches_failed: int = Field(ge=0)
    fetches_not_sent: int = Field(ge=0)
    posts_observed: int = Field(ge=0)
    posts_rejected: int = Field(ge=0)
    new_post_versions: int = Field(ge=0)
    candidates_created: int = Field(ge=0)
    candidates_classified: int = Field(ge=0)
    candidates_promoted: int = Field(ge=0)
    candidates_not_promoted: int = Field(ge=0)
    refusals: dict[str, int]
    promoted_event_types: dict[str, int]
    observations_appended: int = Field(ge=0)
    withdrawals_appended: int = Field(ge=0)
    coverage_checks_recorded: int = Field(ge=0)
    # The shape of what came back: names and counts, never values of a post.
    observed_envelope_fields: tuple[str, ...]
    observed_item_fields: tuple[str, ...]
    observed_feed_names: dict[str, int]
    observed_tags: dict[str, int]


def ingest(
    adapter: EventSourceAdapter,
    policy: SourcePolicy,
    source_game_ids: Sequence[str],
    resolver: Resolver,
    source_store: EventSourceStore,
    event_store: EventStore,
    *,
    stage: DeploymentStage = DeploymentStage.PRIVATE_DEVELOPMENT,
    rules: EventClassificationRules = EVENT_CLASSIFICATION_RULES_V1,
    backfill_requests: int = 0,
    before: datetime | None = None,
) -> IngestReport:
    """Ask the source about each game once (or walk back `backfill_requests` pages).

    Raises `SourcePolicyError` before anything is sent if the policy does not clear
    `stage` for event intelligence.
    """
    if policy.source != adapter.source:
        raise SourcePolicyError(
            f"policy is for '{policy.source}' but the adapter is '{adapter.source}'"
        )
    refusal = policy.registration_refusal(SourcePurpose.EVENT_INTELLIGENCE, stage)
    if refusal is not None:
        raise SourcePolicyError(f"{adapter.source}: {refusal}")

    outcomes: list[TargetOutcome] = []
    fetches: list[EventSourceFetch] = []
    created: list[EventCandidate] = []
    new_versions = appended = withdrawn = covered = 0
    for source_game_id in source_game_ids:
        requested = SourceGame(
            source=adapter.source, namespace=adapter.namespace, value=source_game_id
        )
        binding = resolver(requested)
        pages = (
            list(adapter.backfill(source_game_id, max_requests=backfill_requests, before=before))
            if backfill_requests
            else [adapter.fetch(source_game_id, before=before)]
        )
        fetches.extend(pages)
        game_id = None if binding is None else binding.canonical_game_id
        source_store.record_fetches(fetch_record(page, game_id) for page in pages)
        checks: list[CoverageCheck] = []
        for page in pages:
            if not page.succeeded:
                continue
            fresh = source_store.add_items(page.items, policy)
            new_versions += len(fresh)
            made, observations, taken_back = _read(
                fresh, resolver, source_store, event_store, rules
            )
            created.extend(made)
            appended += event_store.append(observations)
            withdrawn += event_store.append(taken_back)
            if binding is not None:
                checks.append(
                    CoverageCheck(
                        canonical_game_id=binding.canonical_game_id,
                        source=adapter.source,
                        checked_at=page.observed_at,
                    )
                )
        recorded = event_store.record_checks(checks)
        covered += recorded
        outcomes.append(
            TargetOutcome(
                source_game_id=source_game_id,
                canonical_game_id=game_id,
                requests=sum(page.attempts for page in pages),
                fetches_ok=sum(page.succeeded for page in pages),
                fetches_failed=sum(page.error_code is not None for page in pages),
                fetches_not_sent=sum(
                    not page.succeeded and page.error_code is None for page in pages
                ),
                error_codes=tuple(
                    sorted({page.error_code.value for page in pages if page.error_code})
                ),
                posts=sum(len(page.items) for page in pages),
                coverage_recorded=bool(checks),
            )
        )
    items = [item for page in fetches for item in page.items]
    promoted = [c for c in created if c.status is PromotionStatus.PROMOTED]
    return IngestReport(
        source=adapter.source,
        rules_version=rules.version,
        stage=stage,
        targets=tuple(outcomes),
        requests=sum(outcome.requests for outcome in outcomes),
        fetches_ok=sum(outcome.fetches_ok for outcome in outcomes),
        fetches_failed=sum(outcome.fetches_failed for outcome in outcomes),
        fetches_not_sent=sum(outcome.fetches_not_sent for outcome in outcomes),
        posts_observed=len(items),
        posts_rejected=sum(len(page.rejected) for page in fetches),
        new_post_versions=new_versions,
        candidates_created=len(created),
        candidates_classified=sum(candidate.classified for candidate in created),
        candidates_promoted=len(promoted),
        candidates_not_promoted=len(created) - len(promoted),
        refusals=dict(sorted(Counter(r.value for c in created for r in c.refusals).items())),
        promoted_event_types=dict(
            sorted(
                Counter(
                    c.proposed_event_type.value for c in promoted if c.proposed_event_type
                ).items()
            )
        ),
        observations_appended=appended,
        withdrawals_appended=withdrawn,
        coverage_checks_recorded=covered,
        observed_envelope_fields=tuple(
            sorted({name for page in fetches for name in page.observed_envelope_fields})
        ),
        observed_item_fields=tuple(
            sorted({name for page in fetches for name in page.observed_item_fields})
        ),
        observed_feed_names=dict(
            sorted(Counter(item.feed_name or "(none)" for item in items).items())
        ),
        observed_tags=dict(sorted(Counter(tag for item in items for tag in item.tags).items())),
    )


def _read(
    fresh: Sequence[SourceItem],
    resolver: Resolver,
    source_store: EventSourceStore,
    event_store: EventStore,
    rules: EventClassificationRules,
) -> tuple[list[EventCandidate], list[EventObservation], list[EventObservation]]:
    """Classify new versions; return candidates, promoted observations and withdrawals."""
    previous = {c.key: c for c in source_store.latest_candidates(rules_version=rules.version)}
    candidates: list[EventCandidate] = []
    observations: list[EventObservation] = []
    taken_back: list[EventObservation] = []
    stored: dict[str, EventObservation] | None = None
    for item in fresh:
        binding: GameBinding | None = resolver(item.game)
        candidate = classify(item, binding, source_cleared=True, rules=rules)
        candidates.append(candidate)
        before = previous.get(candidate.key)
        if candidate.status is PromotionStatus.PROMOTED:
            observations.append(promoted_observation(candidate, item, rules))
        elif before is not None and before.status is PromotionStatus.PROMOTED:
            # The post changed and no longer supports the event it was promoted as.
            if stored is None:
                stored = {o.observation_id: o for o in event_store.observations()}
            earlier = stored.get(before.observation_id or "")
            if earlier is not None:
                taken_back.append(withdrawal(earlier, item.observed_at))
        previous[candidate.key] = candidate
    source_store.add_candidates(candidates)
    return candidates, observations, taken_back
