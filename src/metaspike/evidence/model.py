"""What MetaSpike holds about one game at one moment, source by source.

This is a reading, not a judgement. It puts side by side what each source says about a
canonical game as known at `as_of`, and says plainly where a source says nothing and
why. It computes no score of its own and weighs nothing against anything else:

    catalogue metadata   who the game is. Never evidence that demand is rising.
    demand signals       each family read against its own history by the one feature
                         engine. Pageviews and concurrent players are never added,
                         averaged or compared in raw units.
    event evidence       promoted events and their timing. An announcement that was
                         not promoted is a candidate and is only ever counted.
    creator supply       a bounded sample of videos, when one is given; otherwise not
                         integrated. A sample is never the market and never a zero.
    community interest   a bounded sample of discussions, when one is given; otherwise not
                         integrated. A sample is never the community and never a zero.

Nine states are kept apart, because each one calls for something different:

    available       the source observes the game and its latest observation is current
    stale           it has observed the game, but not recently enough
    missing         it could observe the game and, as known at `as_of`, never has
    inapplicable    it cannot observe this game; nothing is missing
    unknown         whether it can observe this game is not established
    unresolved      observations exist, and no approved rule makes one value of them
    not_collected   nothing of this source was given to the assessment
    not_integrated  MetaSpike has no source of this kind yet, or none was given
    incomplete      asked, and the answer is not whole: a page limit, a quota, a failure

A reported value of zero is `available` with a level of zero. It is none of the others.

Everything here is internal. It is not an artifact, has no place in the published
bundle, and nothing in the publishing path reads it.
"""

from enum import StrEnum
from typing import Any, Final, Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from metaspike.community_sources.reading import CommunityInterestReading
from metaspike.creator_sources.reading import CreatorSupplyReading
from metaspike.domain.families import SourceFamily
from metaspike.domain.games import GameId, LifecycleState
from metaspike.domain.model import FrozenModel
from metaspike.domain.scores import ConfidenceResult, ScoreResult
from metaspike.domain.source_policy import DeploymentStage, SourcePurpose
from metaspike.events.heat import EventIntelligence
from metaspike.events.radar import RadarItem
from metaspike.features.aggregate import DailyAggregate, HourlyAggregate
from metaspike.features.semantics import EntityEvidenceV2

EVIDENCE_REPORT_VERSION: Final = "game_evidence_v3"
PRIVATE_DEVELOPMENT_ONLY: Final = "private_development_only"


class EvidenceKind(StrEnum):
    """What a source is evidence OF. Kinds are never blended into one another."""

    CATALOGUE_METADATA = "catalogue_metadata"
    DEMAND_SIGNAL = "demand_signal"
    EVENT_EVIDENCE = "event_evidence"
    CREATOR_SUPPLY = "creator_supply"
    COMMUNITY_INTEREST = "community_interest"


class EvidenceState(StrEnum):
    AVAILABLE = "available"
    STALE = "stale"
    MISSING = "missing"
    INAPPLICABLE = "inapplicable"
    UNKNOWN = "unknown"
    UNRESOLVED = "unresolved"
    NOT_COLLECTED = "not_collected"
    NOT_INTEGRATED = "not_integrated"
    # Asked, and the answer is not whole: a page limit, a quota, a failure.
    INCOMPLETE = "incomplete"


class Origin(StrEnum):
    """Where an input came from, as far as it can be checked. See `provenance.py`."""

    # A recording whose content matches a digest pinned in the repository. VERIFIED.
    RECORDED_RESPONSE = "recorded_response"
    # Files given as a recording that match no pinned digest. Origin not established.
    UNVERIFIED_FILES = "unverified_files"
    # A local store, read as found. Origin not established.
    LOCAL_STORE = "local_store"
    # Invented for a test or a demonstration: declared, or detected from its content.
    SYNTHETIC = "synthetic"
    NONE = "none"


class Standing(StrEnum):
    """Whether an input is a real observation. A different question from availability."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    SYNTHETIC = "synthetic"
    NONE = "none"


_STANDING = {
    Origin.RECORDED_RESPONSE: Standing.VERIFIED,
    Origin.UNVERIFIED_FILES: Standing.UNVERIFIED,
    Origin.LOCAL_STORE: Standing.UNVERIFIED,
    Origin.SYNTHETIC: Standing.SYNTHETIC,
    Origin.NONE: Standing.NONE,
}


class InputProvenance(FrozenModel):
    origin: Origin
    # Where the input was read from: a fixture directory, a store, a scenario name.
    reference: str
    note: str = ""
    # For a verified recording: the pinned content digests it matched.
    digests: tuple[str, ...] = ()

    # Follows from `origin` and is written out beside it, so a reader of the JSON
    # never has to know which origins are verified.
    standing: Standing = Standing.NONE

    @model_validator(mode="before")
    @classmethod
    def _fill_standing(cls, data: Any) -> Any:
        if isinstance(data, dict) and "standing" not in data and "origin" in data:
            return {**data, "standing": _STANDING[Origin(data["origin"])]}
        return data

    @model_validator(mode="after")
    def _standing_follows_origin(self) -> Self:
        if self.standing is not _STANDING[self.origin]:
            raise ValueError("an input's standing follows from its origin and nothing else")
        if self.digests and self.origin is not Origin.RECORDED_RESPONSE:
            raise ValueError("only a verified recording carries digests")
        return self

    @property
    def synthetic(self) -> bool:
        return self.origin is Origin.SYNTHETIC

    @property
    def verified(self) -> bool:
        return self.origin is Origin.RECORDED_RESPONSE

    @property
    def unverified(self) -> bool:
        return self.standing is Standing.UNVERIFIED


NO_INPUT = InputProvenance(origin=Origin.NONE, reference="-")


class NativeBinding(FrozenModel):
    """How a source's own id for the game was tied to the canonical game at `as_of`."""

    source: str
    source_native_id: str
    # "mapping_history": a resolver binding, with its version and the moment it began.
    # "catalogue_identifier": an identifier the bootstrap entity carries.
    basis: Literal["mapping_history", "catalogue_identifier"]
    mapping_version: int | None = None
    valid_from: AwareDatetime | None = None
    binding_ref: str | None = None


class PolicyView(FrozenModel):
    """What the source's own policy says about this use. Read from it, never decided here."""

    source: str
    purpose: SourcePurpose
    stage: DeploymentStage
    cleared_stages: tuple[DeploymentStage, ...]
    # Whether anything computed from the source may enter a published artifact.
    publishable: bool
    publication_refusal: str | None = None
    # Why the source could not be part of a public-launch configuration for this use.
    public_launch_refusal: str | None = None


class SourceEvidence(FrozenModel):
    """One source's standing for one game at `as_of`."""

    source: str
    kind: EvidenceKind
    # The independent observation process the source belongs to, where it has one.
    family: SourceFamily | None = None
    state: EvidenceState
    reason: str | None = None
    bindings: tuple[NativeBinding, ...] = ()
    # The newest moment the source's evidence speaks for, as known at `as_of`.
    latest_observed_at: AwareDatetime | None = None
    freshness: str | None = None
    provenance: InputProvenance = NO_INPUT
    policy: PolicyView | None = None

    @property
    def usable(self) -> bool:
        """Current, and verified to be an observation of something real.

        Availability is not enough. Synthetic evidence is never usable, and neither is
        evidence whose origin is not established, however current it looks.
        """
        return self.state is EvidenceState.AVAILABLE and self.provenance.verified

    @property
    def contributes(self) -> bool:
        """Whether anything of this source is actually in the reading for this game."""
        return self.state in (
            EvidenceState.AVAILABLE,
            EvidenceState.STALE,
            EvidenceState.UNRESOLVED,
            EvidenceState.INCOMPLETE,
        )

    @property
    def restricted(self) -> bool:
        """In the reading, and from a source whose data may not be published."""
        return self.contributes and self.policy is not None and not self.policy.publishable


class BoundRecord(FrozenModel):
    """A source record bound to the game at `as_of`, with the metadata it asserts."""

    source: str
    source_id: str
    mapping_version: int
    valid_from: AwareDatetime
    # False when the only version of the record that is held was fetched after the
    # moment read at. The binding was known then; what the record says was not.
    content_known: bool
    product_type: str | None = None
    # Each release date exactly as the source gave it, with its own precision.
    release_dates: tuple[str, ...] = ()
    parent: str | None = None
    version_parent: str | None = None


class UnresolvedRecord(FrozenModel):
    """A record in quarantine that names this game as a candidate. It is not bound."""

    source: str
    source_id: str
    reason: str


class IdentityEvidence(FrozenModel):
    canonical_game_id: GameId
    slug: str
    name: str
    lifecycle_state: LifecycleState
    # "frozen_bootstrap": one of the original entities, whose id never changes.
    # "local_state": read from a persisted local catalogue state.
    # "evaluation_only": made for this evaluation and stored nowhere.
    id_persistence: Literal["frozen_bootstrap", "local_state", "evaluation_only"]
    bound_records: tuple[BoundRecord, ...] = ()
    relationships: tuple[str, ...] = ()
    unresolved_records: tuple[UnresolvedRecord, ...] = ()


class Movement(FrozenModel):
    """One family's recent movement against its own history. Never a cross-source figure."""

    family: SourceFamily
    source: str
    metric: str
    synthetic: bool
    standing: Standing
    # False when the source's policy does not let its values be shown: the text reading
    # then gives only the features that are relative to the source's own history.
    levels_showable: bool
    # Each feature an opportunity score would be allowed to read, with its value or the
    # reason there is none.
    features: dict[str, str]
    # Recorded but not fit to act on under the current rule version.
    withheld: tuple[str, ...] = ()
    maturity: str
    coverage: float


class SampleCompleteness(FrozenModel):
    """The latest whole hour and whole day of a sampled source, by the baseline rules."""

    source_native_id: str
    last_hour: HourlyAggregate
    last_day: DailyAggregate
    zero_reported: bool
    current_level: int | None = None
    current_level_observed_at: AwareDatetime | None = None


class CandidateCounts(FrozenModel):
    """Announcements read for the game, in counts. No title or text leaves the store."""

    total: int = Field(ge=0)
    classified: int = Field(ge=0)
    promoted: int = Field(ge=0)
    not_promoted: int = Field(ge=0)
    # Candidates whose type was established and whose timing was not.
    timing_not_established: int = Field(ge=0)
    refusals: dict[str, int]


class Gap(FrozenModel):
    """Something a creator-opportunity recommendation would need and does not have."""

    code: str
    text: str


class GameEvidence(FrozenModel):
    canonical_game_id: GameId
    as_of: AwareDatetime
    identity: IdentityEvidence
    sources: tuple[SourceEvidence, ...]
    # The demand families under signal_semantics_v2, exactly as the engine reads them.
    demand: EntityEvidenceV2
    movements: tuple[Movement, ...] = ()
    sample_completeness: tuple[SampleCompleteness, ...] = ()
    # The engine counts a family as observed when its data is current, WHATEVER its
    # origin: `demand.evidence_breadth` includes synthetic and unverified families. It
    # is kept as the engine gives it and must not be read as confirmed evidence. The
    # confirmed count is `independent_demand_families`, below.
    # metaspike_score_v0 on the game's pageviews as visible at `as_of`. Unchanged.
    score_v0: ScoreResult
    confidence_v0: ConfidenceResult | None = None
    events: EventIntelligence
    upcoming: tuple[RadarItem, ...] = ()
    candidates: CandidateCounts
    # The creator-supply sample, when a creator-supply input was given. None otherwise.
    # A description of a bounded sample; never a count of the market, and never a score.
    creator_supply: CreatorSupplyReading | None = None
    # The community-interest sample, when a community input was given. None otherwise.
    # What the sample shows being discussed; never a measure of interest or demand.
    community_interest: CommunityInterestReading | None = None
    # Independent demand families that are available now AND verified real observations.
    independent_demand_families: int = Field(ge=0)
    verified_demand_families: tuple[SourceFamily, ...] = ()
    # Families with current data that is NOT verified. Never part of the count above.
    unconfirmed_demand_families: tuple[SourceFamily, ...] = ()
    # Whether the events read for this game come only from verified inputs. False for
    # synthetic or unverified announcements: the heat below is then a diagnostic.
    events_verified: bool = False
    usable_sources: tuple[str, ...] = ()
    restricted_sources: tuple[str, ...] = ()
    synthetic_sources: tuple[str, ...] = ()
    unverified_sources: tuple[str, ...] = ()
    gaps: tuple[Gap, ...] = ()

    def source(self, name: str) -> SourceEvidence:
        return next(item for item in self.sources if item.source == name)


class EvidenceReport(FrozenModel):
    """A local, private reading. It is not an artifact and is never published."""

    # v2 (U6.1): creator supply; the `incomplete` state; and the U6.0.1 provenance fields.
    # v3 (U6.2): community interest.
    report_version: Literal["game_evidence_v3"] = EVIDENCE_REPORT_VERSION
    as_of: AwareDatetime
    stage: DeploymentStage
    distribution: Literal["private_development_only"] = PRIVATE_DEVELOPMENT_ONLY
    # True when any input was invented. Such a report describes no real game's state.
    contains_synthetic: bool
    # True when any input's origin is not established.
    contains_unverified: bool
    inputs: dict[str, InputProvenance]
    policies: tuple[PolicyView, ...]
    games: tuple[GameEvidence, ...]

    def game(self, canonical_game_id: str) -> GameEvidence:
        return next(g for g in self.games if g.canonical_game_id == canonical_game_id)
