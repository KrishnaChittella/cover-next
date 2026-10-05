"""What the resolver is given and what it decides. No logic lives here.

`ResolverInput` is the resolver's entire view of the world for one record: the
incoming record, the entities that exist, and any unresolved records that bear on
it. It contains no labels, no expected outcome and no evaluation evidence, so code
that receives a `ResolverInput` cannot see ground truth even by accident.
"""

from enum import StrEnum
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from metaspike.domain.games import NonEmptyStr, RelationshipType
from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.model import FrozenModel
from metaspike.domain.source_records import SourceRecord


class EntityView(FrozenModel):
    """An existing canonical entity as the resolver sees it: a reference and its records."""

    ref: NonEmptyStr
    records: tuple[SourceRecord, ...] = Field(min_length=1)


class ResolverInput(FrozenModel):
    incoming: SourceRecord
    entities: tuple[EntityView, ...] = ()
    # Records that are not bound to any entity (quarantined or non-entity products).
    context: tuple[SourceRecord, ...] = ()
    # The entity the incoming record's source id is currently bound to, if any.
    bound_to: str | None = None

    @model_validator(mode="after")
    def _references_are_unique(self) -> Self:
        refs = [entity.ref for entity in self.entities]
        if len(refs) != len(set(refs)):
            raise ValueError("two entities share a reference")
        if self.bound_to is not None and self.bound_to not in refs:
            raise ValueError("bound_to names an entity that is not present")
        return self


class DecisionKind(StrEnum):
    AUTO_MATCH = "auto_match"
    NEW_ENTITY = "new_entity"
    QUARANTINE = "quarantine"
    MANUAL_OVERRIDE = "manual_override"
    # Not a resolution at all: the record is a related product (DLC, bundle, pack...)
    # and never receives a canonical id.
    NOT_AN_ENTITY = "not_an_entity"


class CandidateReason(StrEnum):
    """Why an entity was considered at all. Being a candidate is not evidence of a match."""

    ID_LINK = "id_link"
    EXACT_TITLE = "exact_title"
    ALTERNATE_TITLE = "alternate_title"
    PACKAGING_VARIANT = "packaging_variant"
    SUBTITLE_VARIANT = "subtitle_variant"
    SERIES_SIBLING = "series_sibling"
    UPSTREAM_LINK = "upstream_link"


class TitleRelation(StrEnum):
    EXACT = "exact"
    ALTERNATE = "alternate"
    PACKAGING = "packaging"
    SUBTITLE = "subtitle"
    SERIES_SIBLING = "series_sibling"
    NONE = "none"


class Tri(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class Veto(StrEnum):
    DIFFERENT_SEQUEL_NUMERAL = "different_sequel_numeral"
    REWORK_MARKER_MISMATCH = "rework_marker_mismatch"
    INCOMPATIBLE_RELEASE_TIMING = "incompatible_release_timing"
    UPSTREAM_DISTINCT_WORK = "upstream_distinct_work"
    INCOMPATIBLE_PRODUCT_TYPE = "incompatible_product_type"
    BOUND_ELSEWHERE = "bound_elsewhere"


class MatchEvidence(FrozenModel):
    """Everything observed about one candidate. Recorded whether or not it mattered."""

    id_link: bool = False
    # A recorded store URL that addresses the other record by its id. Observed and
    # logged, but NOT accepted as an identity link: it is not on the approved list of
    # strong evidence, so no gate reads it.
    store_url_link: bool = False
    title: TitleRelation = TitleRelation.NONE
    release_timing: Tri = Tri.UNKNOWN
    developer_overlap: Tri = Tri.UNKNOWN
    publisher_overlap: Tri = Tri.UNKNOWN
    platform_overlap: Tri = Tri.UNKNOWN
    franchise_overlap: Tri = Tri.UNKNOWN
    # The incoming record names one of the candidate's records as its version parent.
    version_parent_link: bool = False
    # The incoming record names one of the candidate's records as its parent.
    parent_link: bool = False

    @property
    def strong_link(self) -> bool:
        return self.id_link

    @property
    def company_corroboration(self) -> bool:
        return Tri.YES in (self.developer_overlap, self.publisher_overlap)


class CandidateVerdict(StrEnum):
    MATCH_STRONG = "match_strong"  # path A
    MATCH_CORROBORATED = "match_corroborated"  # path B
    REJECTED = "rejected"  # provably not the same game
    AMBIGUOUS = "ambiguous"  # plausible, unproven


class CandidateAssessment(FrozenModel):
    entity_ref: NonEmptyStr
    reasons: tuple[CandidateReason, ...]
    evidence: MatchEvidence
    vetoes: tuple[Veto, ...] = ()
    verdict: CandidateVerdict
    # Used only to order candidates in reports. It never decides a merge.
    score: int
    explanation: str


class QuarantineReason(StrEnum):
    TITLE_ONLY = "title_only"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    ID_LINK_TITLE_MISMATCH = "id_link_title_mismatch"
    MULTIPLE_CANDIDATES = "multiple_candidates"
    UNDETERMINED_PRODUCT_TYPE = "undetermined_product_type"
    VARIANT_WITHOUT_PARENT = "variant_without_parent"


class ProposedRelationship(FrozenModel):
    relationship_type: RelationshipType
    entity_ref: NonEmptyStr


class ResolutionDecision(FrozenModel):
    kind: DecisionKind
    target_ref: str | None = None
    path: Literal["A", "B"] | None = None
    quarantine_reason: QuarantineReason | None = None
    candidates: tuple[CandidateAssessment, ...] = ()
    relationships: tuple[ProposedRelationship, ...] = ()
    override_ref: str | None = None
    explanation: NonEmptyStr
    resolver_version: NonEmptyStr

    @model_validator(mode="after")
    def _shape_matches_kind(self) -> Self:
        if (self.kind is DecisionKind.AUTO_MATCH) != (self.path is not None):
            raise ValueError("a path is recorded exactly for AUTO_MATCH")
        if self.kind is DecisionKind.AUTO_MATCH and self.target_ref is None:
            raise ValueError("AUTO_MATCH needs a target")
        if (self.kind is DecisionKind.QUARANTINE) != (self.quarantine_reason is not None):
            raise ValueError("a quarantine reason is recorded exactly for QUARANTINE")
        if (self.kind is DecisionKind.MANUAL_OVERRIDE) != (self.override_ref is not None):
            raise ValueError("an override reference is recorded exactly for MANUAL_OVERRIDE")
        return self


class LoggedDecision(FrozenModel):
    """One decision as it was made: about which version of which record, and when."""

    sequence: int = Field(ge=1)
    record: SourceRecordKey
    record_content_hash: NonEmptyStr
    decided_at: AwareDatetime
    decision: ResolutionDecision
    # The canonical id that resulted (matched, created or admitted), if any.
    canonical_game_id: str | None = None

    @property
    def ref(self) -> str:
        return f"decision:{self.sequence}"


class DecisionLog(FrozenModel):
    """Append-only history of resolution decisions. Nothing is edited or removed."""

    decisions: tuple[LoggedDecision, ...] = ()

    def append(
        self,
        record: SourceRecord,
        decision: ResolutionDecision,
        decided_at: AwareDatetime,
        canonical_game_id: str | None = None,
    ) -> tuple[Self, LoggedDecision]:
        logged = LoggedDecision(
            sequence=len(self.decisions) + 1,
            record=record.key,
            record_content_hash=record.content_hash,
            decided_at=decided_at,
            decision=decision,
            canonical_game_id=canonical_game_id,
        )
        return type(self)(decisions=(*self.decisions, logged)), logged

    def for_record(self, key: SourceRecordKey) -> tuple[LoggedDecision, ...]:
        return tuple(decision for decision in self.decisions if decision.record == key)

    def latest(self, key: SourceRecordKey) -> LoggedDecision | None:
        history = self.for_record(key)
        return history[-1] if history else None
