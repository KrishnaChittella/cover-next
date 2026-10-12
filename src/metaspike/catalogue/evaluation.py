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

TWO CATALOGUES, TWO HISTORIES
An evaluation holds two catalogues and never lets one stand in for the other:

    narrow   what production's scope would build: the bootstrap entities and the
             records an explicit link ties to them
    broad    what a broad pass would build from every supplied record

The first evaluation builds the narrow catalogue and then the broad one on top of it.
A later evaluation continues EACH from its own predecessor: the narrow catalogue from
the previous narrow one, the broad from the previous broad one. A broad catalogue is
never given to the narrow pass as its history, because it holds entities the narrow
scope never admitted.

Two kinds of number follow, and they are named apart:

    scope difference   how the two catalogues differ NOW. A property of the current
                       state, so repeating an evaluation does not change it.
    since previous     what this evaluation added to each catalogue. Absent on a first
                       evaluation; zero on an identical repeat.

The scope difference is counted by records, not by comparing ids: an entity of the
broad catalogue is within the narrow scope if it is a bootstrap entity or holds a
record the narrow scope reaches. An entity both catalogues came to create after the
first evaluation has a different id in each, since each history issues its own; neither
is the production catalogue.

TIME
`as_of` is the moment of the evaluation. It stamps the decisions and bindings made in
it. It is NOT a historical cutoff that hides later records: this module cannot
reconstruct what was known at an earlier time, and does not pretend to. To keep a
binding from being dated before its evidence existed, a record fetched after `as_of`
is refused, and so is an `as_of` earlier than the evaluation being continued.
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
from metaspike.domain.identity import SourceRecordKey
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
    # --- the scope difference: the two catalogues as they stand now -----------------
    # What production's scope would resolve from the same records, for comparison.
    records_in_production_scope: int = Field(ge=0)
    records_only_broad_sees: int = Field(ge=0)
    bootstrap_entities: int = Field(ge=0)
    # Entities in the narrow catalogue, and in the broad one.
    entities_in_production_scope: int = Field(ge=0)
    canonical_entities: int = Field(ge=0)
    # Entities of the broad catalogue that the narrow scope does not reach: not a
    # bootstrap entity, and holding no record the narrow scope sees. A property of the
    # current state; an identical repeat leaves it unchanged.
    entities_only_in_broad_scope: int = Field(ge=0)
    # --- since the previous evaluation ----------------------------------------------
    # None on a first evaluation: there is no previous one to count from. Zero is a
    # count, and means nothing was added.
    continues_previous: bool
    narrow_entities_admitted_since_previous: int | None = Field(default=None, ge=0)
    broad_entities_admitted_since_previous: int | None = Field(default=None, ge=0)
    records_new_since_previous: int | None = Field(default=None, ge=0)
    quarantines_released_since_previous: int | None = Field(default=None, ge=0)
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
    """Both evaluated catalogues, in memory only, and their summary.

    This is what a later evaluation continues from. It carries both catalogues because
    each has its own history; see the module docstring.
    """

    summary: EvaluationSummary
    # The narrow catalogue: what production's scope gives from these records.
    scoped: CatalogueState
    # The broad catalogue. Never stored by this module.
    state: CatalogueState
    # Every source record this evaluation has been given, by key.
    record_keys: frozenset[SourceRecordKey]


def require_visible(records: Iterable[SourceRecord], as_of: datetime) -> None:
    """Refuse a record fetched after the moment the evaluation is stamped with.

    `as_of` dates the decisions and bindings an evaluation makes. A binding dated
    before its evidence was fetched would read, afterwards, as something known earlier
    than it was.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    late = [record for record in records if record.fetched_at > as_of]
    if late:
        newest = max(record.fetched_at for record in late)
        raise ValueError(
            f"{len(late)} record(s) were fetched after as_of ({as_of.isoformat()}); the "
            f"latest at {newest.isoformat()}. as_of is the time of the evaluation and "
            "cannot be earlier than the records it uses"
        )


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
    previous: BroadEvaluation | None = None,
    cross_check: bool = False,
) -> BroadEvaluation:
    """Resolve every given record, offline, and say what a broad catalogue would hold.

    `previous` is the earlier EVALUATION to continue, with both of its catalogues. A
    bare `CatalogueState` is refused: a broad catalogue is not a valid history for the
    narrow one. `cross_check` re-asks every question the all-pairs way and raises on a
    difference.
    """
    if previous is not None and not isinstance(previous, BroadEvaluation):
        raise TypeError(
            "previous must be the earlier BroadEvaluation, which carries the narrow and "
            "the broad catalogue separately; a single CatalogueState cannot be continued"
        )
    everything = list(records)
    keys = [record.key for record in everything]
    if len(keys) != len(set(keys)):
        raise ValueError("the same source record was supplied more than once")
    used = require_catalogue_sources(everything, policies, stage)
    assert stage is not None
    require_visible(everything, as_of)
    if previous is not None and as_of < previous.summary.as_of:
        raise ValueError(
            "as_of is earlier than the evaluation being continued; an evaluation cannot "
            "be dated before the one it builds on"
        )

    # The narrow catalogue, continued from the previous NARROW catalogue only.
    reachable = directly_reachable(everything)
    scoped_stats = GenerationStats()
    started = perf_counter()
    scoped = generate_catalogue_blocked(
        reachable,
        as_of=as_of,
        overrides=overrides,
        id_factory=id_factory,
        previous=None if previous is None else previous.scoped,
        stats=scoped_stats,
        cross_check=cross_check,
    )
    scoped_seconds = perf_counter() - started

    # The broad catalogue: on top of the narrow one the first time, and from the
    # previous BROAD catalogue ever after.
    broad_stats = GenerationStats()
    started = perf_counter()
    state = generate_catalogue_blocked(
        everything,
        as_of=as_of,
        overrides=overrides,
        id_factory=id_factory,
        previous=scoped if previous is None else previous.state,
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

    # The scope difference, by records: which broad entities the narrow scope reaches.
    bootstrap_ids = {game.canonical_game_id for game in bootstrap_catalogue().games}
    reachable_keys = {record.key for record in reachable}
    within_narrow = bootstrap_ids | {
        binding.canonical_game_id for key, binding in active.items() if key in reachable_keys
    }
    only_broad = sum(
        1 for game in state.catalogue.games if game.canonical_game_id not in within_narrow
    )

    # What this evaluation added, counted against each catalogue's own predecessor.
    since: dict[str, int | None] = {
        "narrow_entities_admitted_since_previous": None,
        "broad_entities_admitted_since_previous": None,
        "records_new_since_previous": None,
        "quarantines_released_since_previous": None,
    }
    if previous is not None:
        narrow_before = {game.canonical_game_id for game in previous.scoped.catalogue.games}
        broad_before = {game.canonical_game_id for game in previous.state.catalogue.games}
        held_before = {entry.record.key for entry in previous.state.quarantine.entries}
        held_now = {entry.record.key for entry in state.quarantine.entries}
        since = {
            "narrow_entities_admitted_since_previous": sum(
                1 for g in scoped.catalogue.games if g.canonical_game_id not in narrow_before
            ),
            "broad_entities_admitted_since_previous": sum(
                1 for g in state.catalogue.games if g.canonical_game_id not in broad_before
            ),
            "records_new_since_previous": len(supplied - previous.record_keys),
            "quarantines_released_since_previous": len(held_before - held_now),
        }

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
        bootstrap_entities=len(bootstrap_ids),
        entities_in_production_scope=len(scoped.catalogue.games),
        canonical_entities=len(state.catalogue.games),
        entities_only_in_broad_scope=only_broad,
        continues_previous=previous is not None,
        **since,
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
    known = supplied if previous is None else supplied | previous.record_keys
    return BroadEvaluation(
        summary=summary, scoped=scoped, state=state, record_keys=frozenset(known)
    )
