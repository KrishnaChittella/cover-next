"""Deterministic entity resolution. Evidence and gates; no learning, no fuzzy matching.

For one incoming source record the resolver answers: is this an existing canonical
game, a new one, a related product, or not yet knowable?

ORDER OF EVALUATION
  1. An explicit override for this exact record, if one exists.
  2. Product admissibility: DLC, bundles, packs and the like never become entities.
  3. Candidate generation: entities reached by a cross-source id link, an upstream
     parent link, an exact normalised title, an alternative title, or a title that
     differs only by packaging. Nothing looser.
  4. Evidence and vetoes for each candidate.
  5. Gates.

GATES
  AUTO_MATCH needs no hard veto, a unique best candidate, and one of:
    Path A  a cross-source id link, plus a compatible title
    Path B  no id link, but an exact or alternative title, compatible release
            timing, and a shared developer or publisher
  NEW_ENTITY needs an entity-eligible product type and no surviving candidate.
  Everything else that is plausible but unproven is QUARANTINE.

The numeric score exists only to order candidates in reports. No decision reads it.
False merges are far worse than missed merges, so every doubt resolves to QUARANTINE.
"""

import re

from metaspike.catalogue.overrides import NO_OVERRIDES, IdentityOverride, OverrideSet
from metaspike.domain.games import RelationshipType
from metaspike.domain.source_records import Admissibility, ProductType, SourceRecord
from metaspike.resolution.models import (
    CandidateAssessment,
    CandidateReason,
    CandidateVerdict,
    DecisionKind,
    EntityView,
    MatchEvidence,
    ProposedRelationship,
    QuarantineReason,
    ResolutionDecision,
    ResolverInput,
    TitleRelation,
    Tri,
    Veto,
)
from metaspike.resolution.normalize import (
    COLLECTION_WORDS,
    REWORK_WORDS,
    NormalizedTitle,
    normalize_title,
)

RESOLVER_VERSION = "resolver_v1"
# First release years this far apart or closer are the same release, allowing for
# regional and platform staggering. Further apart is a different release.
MAX_COMPATIBLE_YEAR_GAP = 1

# How each source names the others in its external-id fields.
_EXTERNAL_NAME = {"steam_catalogue": "steam", "igdb": "igdb"}
_STORE_URL = {"steam_catalogue": r"store\.steampowered\.com/app/{id}(?:\D|$)"}

_TITLES_COMPATIBLE_WITH_A_LINK = (
    TitleRelation.EXACT,
    TitleRelation.ALTERNATE,
    TitleRelation.PACKAGING,
    TitleRelation.SUBTITLE,
)
_TITLES_THAT_MAKE_A_CANDIDATE = {
    TitleRelation.EXACT: CandidateReason.EXACT_TITLE,
    TitleRelation.ALTERNATE: CandidateReason.ALTERNATE_TITLE,
    TitleRelation.PACKAGING: CandidateReason.PACKAGING_VARIANT,
}
# Vetoes that state a fact rather than a likelihood. No other evidence outweighs them.
_DECISIVE_VETOES = frozenset({Veto.UPSTREAM_DISTINCT_WORK, Veto.BOUND_ELSEWHERE})
_DISTINCT_WORK_TYPES = {
    ProductType.REMAKE: RelationshipType.REMAKE,
    ProductType.REMASTER: RelationshipType.REMASTER,
    ProductType.STANDALONE_EXPANSION: RelationshipType.STANDALONE_EXPANSION,
}
_SCORE = {
    "id_link": 100,
    TitleRelation.EXACT: 40,
    TitleRelation.ALTERNATE: 30,
    TitleRelation.PACKAGING: 20,
    TitleRelation.SUBTITLE: 15,
    "release_timing": 15,
    "developer_overlap": 10,
    "publisher_overlap": 10,
    "platform_overlap": 5,
    "franchise_overlap": 5,
}


def resolve(view: ResolverInput, overrides: OverrideSet = NO_OVERRIDES) -> ResolutionDecision:
    record = view.incoming
    override = overrides.for_record(record.key)
    if override is not None:
        return _apply_override(view, override)

    assessments = tuple(
        sorted(
            (a for a in (_assess(view, entity) for entity in view.entities) if a is not None),
            key=lambda a: (-a.score, a.entity_ref),
        )
    )
    admissibility = record.admissibility
    if admissibility in (
        Admissibility.RELATED_PRODUCT,
        Admissibility.NOT_A_GAME,
        Admissibility.EXPLICIT_MODEL_ONLY,
    ):
        return _related_product(view, assessments)
    if admissibility is Admissibility.VARIANT_OF_PARENT:
        return _variant_of_parent(record, assessments)
    return _decide(view, assessments, eligible=admissibility is Admissibility.ENTITY_ELIGIBLE)


def candidate_refs(view: ResolverInput) -> tuple[str, ...]:
    """The entities that are candidates for the incoming record, in a stable order."""
    return tuple(sorted(e.ref for e in view.entities if _assess(view, e) is not None))


# --- overrides ---------------------------------------------------------------------


def _apply_override(view: ResolverInput, override: IdentityOverride) -> ResolutionDecision:
    refs = {entity.ref for entity in view.entities}
    relationships: tuple[ProposedRelationship, ...] = ()
    if override.relationship_type is not None and override.related_game_id is not None:
        related = override.related_game_id if override.related_game_id in refs else None
        if related is None:
            # The related entity is not addressed by canonical id here; find it through
            # the record's own upstream parent link instead.
            related = _entity_holding(view, view.incoming.parent)
        if related is not None:
            relationships = (
                ProposedRelationship(
                    relationship_type=override.relationship_type, entity_ref=related
                ),
            )
    return _decision(
        DecisionKind.MANUAL_OVERRIDE,
        target_ref=override.canonical_game_id if override.canonical_game_id in refs else None,
        relationships=relationships,
        override_ref=override.ref,
        explanation=f"{override.action.value} by {override.ref}: {override.reason}",
    )


def _entity_holding(view: ResolverInput, key: object) -> str | None:
    if key is None:
        return None
    for entity in view.entities:
        if any(record.key == key for record in entity.records):
            return entity.ref
    return None


# --- candidates, evidence and vetoes ------------------------------------------------


def _assess(view: ResolverInput, entity: EntityView) -> CandidateAssessment | None:
    """Assess one entity, or None if nothing makes it a candidate."""
    record = view.incoming
    id_link = _has_id_link(record, entity)
    store_link = _has_store_link(record, entity)
    parent_link = _points_at(record.parent, entity)
    version_link = _points_at(record.version_parent, entity)
    title, rework_mismatch = _title_relation(record, entity)

    reasons: list[CandidateReason] = []
    if id_link:
        reasons.append(CandidateReason.ID_LINK)
    if parent_link or version_link:
        reasons.append(CandidateReason.UPSTREAM_LINK)
    if title in _TITLES_THAT_MAKE_A_CANDIDATE:
        reasons.append(_TITLES_THAT_MAKE_A_CANDIDATE[title])
    if not reasons:
        return None

    evidence = MatchEvidence(
        id_link=id_link,
        store_url_link=store_link,
        title=title,
        release_timing=_release_timing(record, entity),
        developer_overlap=_overlap(record.developers, _all(entity, "developers")),
        publisher_overlap=_overlap(record.publishers, _all(entity, "publishers")),
        platform_overlap=_overlap(record.platforms, _all(entity, "platforms")),
        franchise_overlap=_overlap(record.franchises, _all(entity, "franchises")),
        version_parent_link=version_link,
        parent_link=parent_link,
    )
    vetoes = _vetoes(view, entity, evidence, rework_mismatch)
    verdict, explanation = _verdict(evidence, vetoes)
    return CandidateAssessment(
        entity_ref=entity.ref,
        reasons=tuple(reasons),
        evidence=evidence,
        vetoes=vetoes,
        verdict=verdict,
        score=_score(evidence),
        explanation=explanation,
    )


def _has_id_link(record: SourceRecord, entity: EntityView) -> bool:
    """A cross-source identifier asserted by either side's source."""
    own_name = _EXTERNAL_NAME.get(record.key.source)
    for other in entity.records:
        if other.key == record.key:
            return True
        if own_name is not None and any(
            link.source == own_name and link.value == record.key.source_id
            for link in other.external_ids
        ):
            return True
        other_name = _EXTERNAL_NAME.get(other.key.source)
        if other_name is not None and any(
            link.source == other_name and link.value == other.key.source_id
            for link in record.external_ids
        ):
            return True
    return False


def _has_store_link(record: SourceRecord, entity: EntityView) -> bool:
    """A recorded store URL that addresses the other record by its id.

    Recorded as an observation only. It is not approved strong evidence, so it makes
    no entity a candidate and satisfies no gate.
    """

    def links(page_owner: SourceRecord, target: SourceRecord) -> bool:
        pattern = _STORE_URL.get(target.key.source)
        if pattern is None:
            return False
        compiled = re.compile(pattern.format(id=re.escape(target.key.source_id)))
        return any(compiled.search(url) for url in page_owner.websites)

    return any(links(other, record) or links(record, other) for other in entity.records)


def _points_at(reference: object, entity: EntityView) -> bool:
    return reference is not None and any(record.key == reference for record in entity.records)


def _title_relation(record: SourceRecord, entity: EntityView) -> tuple[TitleRelation, bool]:
    """The closest title relation to any of the entity's records.

    Also reports whether exactly one side carries a rework marker (remaster, remake,
    HD...), which makes an otherwise similar title a different work.
    """
    ranking = [
        TitleRelation.EXACT,
        TitleRelation.ALTERNATE,
        TitleRelation.PACKAGING,
        TitleRelation.SUBTITLE,
        TitleRelation.SERIES_SIBLING,
        TitleRelation.NONE,
    ]
    best, best_mismatch = TitleRelation.NONE, False
    mine = normalize_title(record.title)
    my_alternatives = {normalize_title(title).key for title in record.alternative_titles}
    for other in entity.records:
        theirs = normalize_title(other.title)
        their_alternatives = {normalize_title(title).key for title in other.alternative_titles}
        if mine.key == theirs.key:
            relation, mismatch = TitleRelation.EXACT, False
        elif mine.key in their_alternatives or theirs.key in my_alternatives:
            relation, mismatch = TitleRelation.ALTERNATE, False
        else:
            relation, mismatch = _structural_relation(mine, theirs, _release_years(other))
        if ranking.index(relation) < ranking.index(best):
            best, best_mismatch = relation, mismatch
    return best, best_mismatch


def _structural_relation(
    mine: NormalizedTitle, theirs: NormalizedTitle, their_years: set[int]
) -> tuple[TitleRelation, bool]:
    mismatch = mine.rework_markers != theirs.rework_markers
    my_core = _without(mine.core_tokens, REWORK_WORDS, their_years)
    their_core = _without(theirs.core_tokens, REWORK_WORDS, set())
    if my_core and my_core == their_core:
        # Same words once packaging (and a year qualifier naming the other record's own
        # release year) is set aside.
        return TitleRelation.PACKAGING, mismatch
    shorter, longer = sorted((mine.tokens, theirs.tokens), key=len)
    if shorter and len(shorter) < len(longer) and longer[: len(shorter)] == shorter:
        extra = longer[len(shorter) :]
        numeric = [token for token in extra if token.isdigit()]
        if len(extra) == 1 and numeric:
            return TitleRelation.SERIES_SIBLING, False
        if not numeric and not set(extra) & (REWORK_WORDS | COLLECTION_WORDS):
            return TitleRelation.SUBTITLE, False
    if (
        mine.series_key is not None
        and mine.series_key == theirs.series_key
        and mine.tokens[-1] != theirs.tokens[-1]
    ):
        return TitleRelation.SERIES_SIBLING, False
    return TitleRelation.NONE, False


def _without(tokens: tuple[str, ...], words: frozenset[str], years: set[int]) -> tuple[str, ...]:
    return tuple(
        token
        for token in tokens
        if token not in words and not (token.isdigit() and int(token) in years)
    )


def _release_years(record: SourceRecord) -> set[int]:
    return {release.starts_on.year for release in record.release_dates if release.starts_on}


def _first_year(records: tuple[SourceRecord, ...]) -> int | None:
    years = {year for record in records for year in _release_years(record)}
    return min(years) if years else None


def _release_timing(record: SourceRecord, entity: EntityView) -> Tri:
    mine, theirs = _first_year((record,)), _first_year(entity.records)
    if mine is None or theirs is None:
        return Tri.UNKNOWN
    return Tri.YES if abs(mine - theirs) <= MAX_COMPATIBLE_YEAR_GAP else Tri.NO


def _all(entity: EntityView, field: str) -> tuple[str, ...]:
    return tuple(value for record in entity.records for value in getattr(record, field))


def _overlap(mine: tuple[str, ...], theirs: tuple[str, ...]) -> Tri:
    """Whether two name lists share an entry. Unknown if either side recorded nothing."""
    if not mine or not theirs:
        return Tri.UNKNOWN
    shared = {name.casefold() for name in mine} & {name.casefold() for name in theirs}
    return Tri.YES if shared else Tri.NO


def _vetoes(
    view: ResolverInput, entity: EntityView, evidence: MatchEvidence, rework_mismatch: bool
) -> tuple[Veto, ...]:
    record = view.incoming
    vetoes: list[Veto] = []
    if view.bound_to is not None and view.bound_to != entity.ref:
        vetoes.append(Veto.BOUND_ELSEWHERE)
    if evidence.parent_link and record.product_type in (
        *_DISTINCT_WORK_TYPES,
        ProductType.MAIN_GAME,
    ):
        # The source itself says this is a separate work derived from the candidate.
        vetoes.append(Veto.UPSTREAM_DISTINCT_WORK)
    if evidence.title is TitleRelation.SERIES_SIBLING:
        vetoes.append(Veto.DIFFERENT_SEQUEL_NUMERAL)
    if rework_mismatch:
        vetoes.append(Veto.REWORK_MARKER_MISMATCH)
    if evidence.release_timing is Tri.NO:
        vetoes.append(Veto.INCOMPATIBLE_RELEASE_TIMING)
    their_types = {other.product_type for other in entity.records}
    rework = {ProductType.REMAKE, ProductType.REMASTER}
    if (
        record.product_type in rework
        and their_types & {ProductType.MAIN_GAME}
        and not their_types & rework
    ) or (record.product_type is ProductType.MAIN_GAME and their_types and their_types <= rework):
        vetoes.append(Veto.INCOMPATIBLE_PRODUCT_TYPE)
    return tuple(vetoes)


def _verdict(evidence: MatchEvidence, vetoes: tuple[Veto, ...]) -> tuple[CandidateVerdict, str]:
    if vetoes:
        names = ", ".join(veto.value for veto in vetoes)
        if _DECISIVE_VETOES & set(vetoes):
            return CandidateVerdict.REJECTED, f"rejected: {names}"
        if evidence.strong_link or evidence.company_corroboration:
            return (
                CandidateVerdict.AMBIGUOUS,
                f"conflicting: evidence of identity alongside {names}",
            )
        return CandidateVerdict.REJECTED, f"rejected: {names}, with nothing supporting identity"
    if evidence.strong_link:
        if evidence.title in _TITLES_COMPATIBLE_WITH_A_LINK:
            return CandidateVerdict.MATCH_STRONG, f"id link with {evidence.title.value} title"
        return CandidateVerdict.AMBIGUOUS, "id link, but the titles are not compatible"
    if evidence.version_parent_link:
        return CandidateVerdict.AMBIGUOUS, "named as version parent by the source"
    if (
        evidence.title in (TitleRelation.EXACT, TitleRelation.ALTERNATE)
        and evidence.release_timing is Tri.YES
        and evidence.company_corroboration
    ):
        return (
            CandidateVerdict.MATCH_CORROBORATED,
            f"{evidence.title.value} title, compatible release timing and a shared company",
        )
    if evidence.title in _TITLES_THAT_MAKE_A_CANDIDATE:
        return CandidateVerdict.AMBIGUOUS, f"{evidence.title.value} title without corroboration"
    return CandidateVerdict.REJECTED, "an upstream link with no identity evidence"


def _score(evidence: MatchEvidence) -> int:
    total = _SCORE.get(evidence.title, 0)
    total += _SCORE["id_link"] if evidence.id_link else 0
    for field in (
        "release_timing",
        "developer_overlap",
        "publisher_overlap",
        "platform_overlap",
        "franchise_overlap",
    ):
        total += _SCORE[field] if getattr(evidence, field) is Tri.YES else 0
    return total


# --- gates -------------------------------------------------------------------------


def _decide(
    view: ResolverInput, assessments: tuple[CandidateAssessment, ...], eligible: bool
) -> ResolutionDecision:
    strong = [a for a in assessments if a.verdict is CandidateVerdict.MATCH_STRONG]
    corroborated = [a for a in assessments if a.verdict is CandidateVerdict.MATCH_CORROBORATED]
    ambiguous = [a for a in assessments if a.verdict is CandidateVerdict.AMBIGUOUS]
    linked_but_unclear = [a for a in ambiguous if a.evidence.strong_link]

    if len(strong) == 1 and not linked_but_unclear:
        # Path A. Other candidates that share only a title do not compete with a link
        # the source itself asserts.
        return _decision(
            DecisionKind.AUTO_MATCH,
            target_ref=strong[0].entity_ref,
            path="A",
            candidates=assessments,
            explanation=f"path A: {strong[0].explanation}",
        )
    if len(strong) > 1 or (strong and linked_but_unclear):
        return _quarantine(
            QuarantineReason.MULTIPLE_CANDIDATES,
            "more than one entity is linked to this record by id",
            assessments,
        )
    if len(corroborated) == 1 and not ambiguous:
        return _decision(
            DecisionKind.AUTO_MATCH,
            target_ref=corroborated[0].entity_ref,
            path="B",
            candidates=assessments,
            explanation=f"path B: {corroborated[0].explanation}",
        )
    if len(corroborated) > 1:
        return _quarantine(
            QuarantineReason.MULTIPLE_CANDIDATES,
            "more than one entity is corroborated equally well",
            assessments,
        )
    if corroborated or ambiguous:
        return _quarantine(*_why_ambiguous(corroborated, ambiguous), assessments)

    if eligible:
        return _decision(
            DecisionKind.NEW_ENTITY,
            candidates=assessments,
            relationships=_derived_work_relationships(view),
            explanation=(
                "no existing entity is a candidate"
                if not assessments
                else "every candidate is contradicted by the evidence"
            ),
        )
    return _quarantine(
        QuarantineReason.UNDETERMINED_PRODUCT_TYPE,
        "no entity matches, and the source does not say what kind of product this is",
        assessments,
    )


def _why_ambiguous(
    corroborated: list[CandidateAssessment], ambiguous: list[CandidateAssessment]
) -> tuple[QuarantineReason, str]:
    if any(a.vetoes for a in ambiguous):
        return QuarantineReason.CONFLICTING_EVIDENCE, "evidence for and against the same entity"
    if any(a.evidence.strong_link for a in ambiguous):
        return (
            QuarantineReason.ID_LINK_TITLE_MISMATCH,
            "an id link to an entity whose title is not compatible",
        )
    if corroborated:
        return (
            QuarantineReason.MULTIPLE_CANDIDATES,
            "a corroborated candidate, but another plausible one exists",
        )
    return QuarantineReason.TITLE_ONLY, "a title relation with nothing to corroborate it"


def _derived_work_relationships(view: ResolverInput) -> tuple[ProposedRelationship, ...]:
    relationship = _DISTINCT_WORK_TYPES.get(view.incoming.product_type)
    parent = _entity_holding(view, view.incoming.parent)
    if relationship is None or parent is None:
        return ()
    return (ProposedRelationship(relationship_type=relationship, entity_ref=parent),)


def _variant_of_parent(
    record: SourceRecord, assessments: tuple[CandidateAssessment, ...]
) -> ResolutionDecision:
    """A port or expanded edition belongs to the game its source names as its parent."""
    parents = [a for a in assessments if a.evidence.version_parent_link or a.evidence.parent_link]
    if len(parents) == 1 and Veto.BOUND_ELSEWHERE not in parents[0].vetoes:
        return _decision(
            DecisionKind.AUTO_MATCH,
            target_ref=parents[0].entity_ref,
            path="A",
            candidates=assessments,
            explanation=f"path A: the source names this {record.product_type.value}'s parent",
        )
    return _quarantine(
        QuarantineReason.VARIANT_WITHOUT_PARENT,
        f"a {record.product_type.value} whose parent game is not a known entity",
        assessments,
    )


def _related_product(
    view: ResolverInput, assessments: tuple[CandidateAssessment, ...]
) -> ResolutionDecision:
    """DLC, bundles, packs, mods and non-games never become canonical entities."""
    record = view.incoming
    relationships: tuple[ProposedRelationship, ...] = ()
    detail = "no entity is provably related"
    if record.product_type is ProductType.BUNDLE:
        linked = [
            a.entity_ref
            for a in assessments
            if a.evidence.version_parent_link or a.evidence.parent_link or a.evidence.strong_link
        ]
        if normalize_title(record.title).is_collection:
            relationships = tuple(
                ProposedRelationship(relationship_type=RelationshipType.COLLECTION, entity_ref=ref)
                for ref in linked
            )
            detail = "a collection of several games"
        elif len(linked) == 1:
            relationships = (
                ProposedRelationship(
                    relationship_type=RelationshipType.PACKAGING_VARIANT, entity_ref=linked[0]
                ),
            )
            detail = "packaging of one game, as linked by the source"
        else:
            detail = "a bundle with no provable link to one game"
    return _decision(
        DecisionKind.NOT_AN_ENTITY,
        candidates=assessments,
        relationships=relationships,
        explanation=(
            f"{record.product_type.value} is a {record.admissibility.value}, never a "
            f"canonical game: {detail}"
        ),
    )


def _quarantine(
    reason: QuarantineReason, explanation: str, assessments: tuple[CandidateAssessment, ...]
) -> ResolutionDecision:
    return _decision(
        DecisionKind.QUARANTINE,
        quarantine_reason=reason,
        candidates=assessments,
        explanation=explanation,
    )


def _decision(kind: DecisionKind, *, explanation: str, **fields: object) -> ResolutionDecision:
    return ResolutionDecision.model_validate(
        {"kind": kind, "explanation": explanation, "resolver_version": RESOLVER_VERSION, **fields}
    )
