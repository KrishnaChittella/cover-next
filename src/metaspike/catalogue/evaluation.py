"""Offline evaluation of a broader catalogue: what WOULD broad discovery admit?

Production resolves only the bootstrap entities and the records an explicit link ties
to them; resolving everything else ("broad discovery") is switched off by a constant
in `persistence`, and stays off. This module does not flip that switch and does not go
through it. It answers a question, locally, about records that are already in hand:

    given these recorded source records, resolved by the existing resolver with the
    existing rules, what would a broad catalogue look like?

What keeps it from being a back door:

- It takes records. It has no adapter, no client and no way to fetch anything.
- It has no repository, no object store and no pointer. The result is a value in
  memory; nothing here can move CURRENT or write a generation.
- It is not a second resolver. It calls `generate_catalogue_blocked` twice, once on the
  records production would see and once on all of them, and holds no identity rule.
- It fails closed on policy. Every source a record comes from must have a
  `SourcePolicy`, handed in by the caller under that source's own name, that clears the
  STATED stage for catalogue metadata. There is no default stage, no default policy and
  no registry consulted behind the caller's back.

Synthetic records are for measuring scale only. They carry a source name, so they need
a policy like any other; tests supply a test-only one.
"""

import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from time import perf_counter
from typing import Literal

from pydantic import AwareDatetime, Field

from metaspike.catalogue.coverage import CoverageSummary, summarise_coverage
from metaspike.catalogue.generate import BOOTSTRAP_SOURCE, CatalogueState
from metaspike.catalogue.incremental import GenerationStats, generate_catalogue_blocked
from metaspike.catalogue.overrides import PRODUCTION_OVERRIDES, OverrideSet
from metaspike.catalogue.persistence import directly_reachable
from metaspike.catalogue.seed import bootstrap_catalogue
from metaspike.domain.model import FrozenModel
from metaspike.domain.source_policy import DeploymentStage, SourcePolicy, SourcePurpose
from metaspike.domain.source_records import SourceRecord
from metaspike.resolution.models import DecisionKind
from metaspike.sources.registry import SourcePolicyError

EVALUATION_SCOPE: Literal["offline_broad_evaluation"] = "offline_broad_evaluation"


class WorkCount(FrozenModel):
    """How much resolving one pass did. Counts from the engine; nothing is estimated."""

    records: int = Field(ge=0)
    examined: int = Field(ge=0)
    passes: int = Field(ge=0)
    resolver_calls: int = Field(ge=0)
    fingerprint_checks: int = Field(ge=0)
    skipped_unchanged_quarantine: int = Field(ge=0)
    # Entities offered to the resolver, in total, and what all-pairs would have offered.
    entities_offered: int = Field(ge=0)
    entities_all_pairs: int = Field(ge=0)
    most_entities_offered: int = Field(ge=0)
    full_scans: int = Field(ge=0)
    cross_checked: int = Field(ge=0)
    seconds: float = Field(ge=0)

    @property
    def share_of_all_pairs(self) -> float | None:
        """Entities offered as a fraction of all-pairs. None when nothing was offered."""
        if self.entities_all_pairs == 0:
            return None
        return self.entities_offered / self.entities_all_pairs


class SourceUse(FrozenModel):
    """One source whose records were evaluated, and the policy that allowed it."""

    source: str
    records: int = Field(ge=0)
    cleared_stages: tuple[str, ...]
    reviewed_on: str


class EvaluationSummary(FrozenModel):
    """What the evaluation found, in counts. No title, id or text of any record."""

    scope: Literal["offline_broad_evaluation"]
    stage: DeploymentStage
    as_of: AwareDatetime
    resolver_version: str
    sources: tuple[SourceUse, ...]
    source_records: int = Field(ge=0)
    # What production's scope would resolve from the same records, for comparison.
    records_in_production_scope: int = Field(ge=0)
    records_only_broad_sees: int = Field(ge=0)
    bootstrap_entities: int = Field(ge=0)
    entities_in_production_scope: int = Field(ge=0)
    canonical_entities: int = Field(ge=0)
    # Entities that exist only because broad evaluation resolved a record.
    entities_admitted_by_broad: int = Field(ge=0)
    # Each source record is in exactly one of these three states.
    bound_records: int = Field(ge=0)
    quarantined_records: int = Field(ge=0)
    related_products: int = Field(ge=0)
    # Of the bound records: the one that created its entity, or joined one that existed.
    records_admitted_as_new_entities: int = Field(ge=0)
    records_resolved_to_existing_entities: int = Field(ge=0)
    quarantine_reasons: dict[str, int]
    related_product_types: dict[str, int]
    decision_kinds: dict[str, int]
    coverage: CoverageSummary
    scoped_work: WorkCount
    broad_work: WorkCount


class BroadEvaluation(FrozenModel):
    """The evaluated catalogue, in memory only, and its summary."""

    summary: EvaluationSummary
    # The catalogue production's scope gives from these records.
    scoped: CatalogueState
    # The catalogue a broad pass would give on top of it. Never stored by this module.
    state: CatalogueState


def require_catalogue_sources(
    records: Iterable[SourceRecord],
    policies: Mapping[str, SourcePolicy] | None,
    stage: DeploymentStage | None,
) -> dict[str, SourcePolicy]:
    """The policy for every source among `records`, or a refusal. Fails closed."""
    sources = sorted({record.key.source for record in records})
    if stage is None:
        raise SourcePolicyError("a catalogue evaluation must state the deployment stage it runs at")
    used: dict[str, SourcePolicy] = {}
    for source in sources:
        if source == BOOTSTRAP_SOURCE:
            raise SourcePolicyError(
                f"{source}: a bootstrap record is not source data and cannot be supplied"
            )
        policy = (policies or {}).get(source)
        if policy is None:
            raise SourcePolicyError(
                f"{source}: no source policy, so its records cannot be evaluated"
            )
        if policy.source != source:
            raise SourcePolicyError(
                f"{source}: the policy given for this source is for '{policy.source}'"
            )
        refusal = policy.registration_refusal(SourcePurpose.CATALOGUE_METADATA, stage)
        if refusal is not None:
            raise SourcePolicyError(f"{source}: {refusal}")
        used[source] = policy
    return used


def _work(stats: GenerationStats, seconds: float) -> WorkCount:
    return WorkCount(
        records=stats.records,
        examined=stats.examined,
        passes=stats.passes,
        resolver_calls=stats.resolver_calls,
        fingerprint_checks=stats.fingerprint_checks,
        skipped_unchanged_quarantine=stats.skipped_unchanged_quarantine,
        entities_offered=stats.entities_offered,
        entities_all_pairs=stats.entities_all_pairs,
        most_entities_offered=stats.most_entities_offered,
        full_scans=stats.full_scans,
        cross_checked=stats.cross_checked,
        seconds=seconds,
    )


def evaluate_broad_catalogue(
    records: Iterable[SourceRecord],
    *,
    policies: Mapping[str, SourcePolicy] | None,
    stage: DeploymentStage | None,
    as_of: datetime,
    overrides: OverrideSet = PRODUCTION_OVERRIDES,
    id_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    previous: CatalogueState | None = None,
    cross_check: bool = False,
) -> BroadEvaluation:
    """Resolve every given record, offline, and say what a broad catalogue would hold.

    `previous` continues an earlier evaluation's catalogue (its `state`), so that the
    effect of new evidence can be seen without anything already decided being redone.
    `cross_check` re-asks every question the all-pairs way and raises on a difference.
    """
    everything = list(records)
    keys = [record.key for record in everything]
    if len(keys) != len(set(keys)):
        raise ValueError("the same source record was supplied more than once")
    used = require_catalogue_sources(everything, policies, stage)
    assert stage is not None

    # What production would resolve from these records: the comparison, and the base.
    reachable = directly_reachable(everything)
    scoped_stats = GenerationStats()
    started = perf_counter()
    scoped = generate_catalogue_blocked(
        reachable,
        as_of=as_of,
        overrides=overrides,
        id_factory=id_factory,
        previous=previous,
        stats=scoped_stats,
        cross_check=cross_check,
    )
    scoped_seconds = perf_counter() - started

    broad_stats = GenerationStats()
    started = perf_counter()
    state = generate_catalogue_blocked(
        everything,
        as_of=as_of,
        overrides=overrides,
        id_factory=id_factory,
        previous=scoped,
        stats=broad_stats,
        cross_check=cross_check,
    )
    broad_seconds = perf_counter() - started

    active = {b.key: b for b in state.mapping.bindings if b.valid_to is None}
    supplied = set(keys)
    bound = [key for key in active if key in supplied]
    quarantined = [e for e in state.quarantine.entries if e.record.key in supplied]
    related = [p for p in state.related_products if p.record in supplied]
    # A record admitted as a new entity is one whose latest decision was NEW_ENTITY.
    latest = {}
    for logged in state.decisions.decisions:
        latest[logged.record] = logged.decision.kind
    created = sum(1 for key in bound if latest.get(key) is DecisionKind.NEW_ENTITY)
    scoped_ids = {game.canonical_game_id for game in scoped.catalogue.games}
    by_source = Counter(record.key.source for record in everything)
    summary = EvaluationSummary(
        scope=EVALUATION_SCOPE,
        stage=stage,
        as_of=as_of,
        resolver_version=state.resolver_version,
        sources=tuple(
            SourceUse(
                source=source,
                records=by_source[source],
                cleared_stages=tuple(c.stage.value for c in policy.stage_clearance if c.cleared),
                reviewed_on=policy.reviewed_at.isoformat(),
            )
            for source, policy in used.items()
        ),
        source_records=len(everything),
        records_in_production_scope=len(reachable),
        records_only_broad_sees=len(everything) - len(reachable),
        bootstrap_entities=len(bootstrap_catalogue().games),
        entities_in_production_scope=len(scoped_ids),
        canonical_entities=len(state.catalogue.games),
        entities_admitted_by_broad=sum(
            1 for game in state.catalogue.games if game.canonical_game_id not in scoped_ids
        ),
        bound_records=len(bound),
        quarantined_records=len(quarantined),
        related_products=len(related),
        records_admitted_as_new_entities=created,
        records_resolved_to_existing_entities=len(bound) - created,
        quarantine_reasons=dict(sorted(Counter(e.reason.value for e in quarantined).items())),
        related_product_types=dict(sorted(Counter(p.product_type.value for p in related).items())),
        decision_kinds=dict(
            sorted(Counter(d.decision.kind.value for d in state.decisions.decisions).items())
        ),
        coverage=summarise_coverage(state, everything),
        scoped_work=_work(scoped_stats, scoped_seconds),
        broad_work=_work(broad_stats, broad_seconds),
    )
    return BroadEvaluation(summary=summary, scoped=scoped, state=state)
