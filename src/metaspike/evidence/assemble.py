"""Assemble a game's evidence from what is already held. Offline and read-only.

Nothing here fetches, stores, scores or resolves. Every number comes from a component
that already exists and is called as it is:

    identity          the catalogue state's mapping history, read at `as_of`
    Steam eligibility `steam_eligibility` and `sampling_targets`, at `as_of`
    pageviews         a recorded collection, cut back to what was published by `as_of`
    players           `steam_family_v2` over the sample store, as stored by `as_of`
    features          the one feature engine, through `usable_features`
    score v0          `assess_entity`, on the pageviews visible at `as_of`
    events            `assess_events` and `upcoming_radar`, on the event store at `as_of`
    announcements     the source store's candidates at `as_of`, counted

Policy is checked before anything is read, and fails closed: each input's source must
have a policy that clears the stated stage for the purpose it is used for. An input
with no policy, or one that does not clear the stage, stops the whole assessment.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from metaspike.catalogue.eligibility import (
    STEAM_RECORD_SOURCE,
    SamplingTarget,
    SourceEligibility,
    SteamEligibility,
    active_binding,
    sampling_targets,
    steam_eligibility,
)
from metaspike.catalogue.evaluation import require_catalogue_sources, retained_sources
from metaspike.catalogue.generate import CatalogueState
from metaspike.catalogue.legacy_ids import LEGACY_ID_TO_CANONICAL
from metaspike.domain.families import SourceFamily
from metaspike.domain.games import CanonicalGame
from metaspike.domain.identity import MappingHistory, SourceRecordKey
from metaspike.domain.observations import TimeSeries
from metaspike.domain.signals import EntitySignals, Signal
from metaspike.domain.source_policy import DeploymentStage, SourcePolicy, SourcePurpose
from metaspike.domain.source_records import SourceRecord
from metaspike.domain.timeseries import Granularity, TimeWindow
from metaspike.event_sources.candidates import EVENT_CLASSIFICATION_RULES_V1
from metaspike.event_sources.gate import require_event_source
from metaspike.event_sources.steam_news import SOURCE_ID as STEAM_NEWS_SOURCE
from metaspike.event_sources.store import EventSourceStore
from metaspike.events.heat import Availability as EventAvailability
from metaspike.events.heat import EventIntelligence, coverage_of
from metaspike.events.intelligence import assess_events
from metaspike.events.model import CoverageCheck, EventRecord
from metaspike.events.radar import upcoming_radar
from metaspike.events.rules import EVENT_HEAT_RULES_V1
from metaspike.events.store import EventStore
from metaspike.evidence.model import (
    NO_INPUT,
    BoundRecord,
    CandidateCounts,
    EvidenceKind,
    EvidenceReport,
    EvidenceState,
    GameEvidence,
    Gap,
    IdentityEvidence,
    InputProvenance,
    Movement,
    NativeBinding,
    PolicyView,
    SampleCompleteness,
    SourceEvidence,
    UnresolvedRecord,
)
from metaspike.features.access import READABLE_FEATURES, Consumer, usable_features
from metaspike.features.aggregate import current_level, daily_aggregates, hourly_aggregates
from metaspike.features.engine import FeatureValue
from metaspike.features.semantics import (
    Applicability,
    Availability,
    FamilyReadingV2,
    entity_evidence_v2,
    family_reading_v2,
)
from metaspike.pipeline.assess import assess_entity
from metaspike.pipeline.collection import CollectionReport, window_ending
from metaspike.pipeline.history import read_entity_history
from metaspike.pipeline.normalize import build_time_series, latest_due_period
from metaspike.pipeline.signal_features import (
    STEAM_SLOTS,
    STEAM_SOURCE,
    WIKIMEDIA_SOURCE,
    last_complete_hour,
    steam_family_v2,
    wikimedia_family_v2,
)
from metaspike.scoring.v0 import V0_INPUT_SIGNAL
from metaspike.sources.registry import SourcePolicyError
from metaspike.storage.samples import SampleStore
from metaspike.validation.history import evaluation_day_at

IGDB_SOURCE = "igdb"
CREATOR_SUPPLY = "creator_supply"
COMMUNITY_INTEREST = "community_interest"

# What each source is used for here, which decides the permissions it must hold.
PURPOSES: Mapping[str, SourcePurpose] = {
    IGDB_SOURCE: SourcePurpose.CATALOGUE_METADATA,
    STEAM_RECORD_SOURCE: SourcePurpose.CATALOGUE_METADATA,
    WIKIMEDIA_SOURCE: SourcePurpose.DEMAND_SIGNALS,
    STEAM_SOURCE: SourcePurpose.DEMAND_SIGNALS,
    STEAM_NEWS_SOURCE: SourcePurpose.EVENT_INTELLIGENCE,
}

_USE = "a game evidence assessment"
_HOUR = timedelta(hours=1)
_DAY = timedelta(days=1)

_STATE_OF = {
    Availability.APPLICABLE_AND_AVAILABLE: EvidenceState.AVAILABLE,
    Availability.APPLICABLE_BUT_STALE: EvidenceState.STALE,
    Availability.APPLICABLE_BUT_MISSING: EvidenceState.MISSING,
    Availability.INAPPLICABLE: EvidenceState.INAPPLICABLE,
    Availability.UNKNOWN: EvidenceState.UNKNOWN,
    Availability.UNRESOLVED: EvidenceState.UNRESOLVED,
}

# States that say something about what an input held. The others (inapplicable, unknown,
# not collected) come from identity or from the input's absence, never from its content.
_READ_FROM_INPUT = frozenset(
    {
        EvidenceState.AVAILABLE,
        EvidenceState.STALE,
        EvidenceState.MISSING,
        EvidenceState.UNRESOLVED,
    }
)

NO_RECORDING_REASON = "the pageview input holds no response for this game's article"
NEVER_SAMPLED_REASON = "eligible, and no sample of it had been stored by this moment"
NO_SAMPLE_STORE_REASON = "no sample store was given to this assessment"
NO_PAGEVIEWS_REASON = "no pageview input was given to this assessment"
NO_EVENT_STORE_REASON = "no event store was given to this assessment"
NOT_INTEGRATED_REASON = "MetaSpike has no source of this kind yet; this is not a zero"


@dataclass(frozen=True)
class EvidenceInputs:
    """Everything an assessment may read. Each input says where it came from."""

    state: CatalogueState
    records: Sequence[SourceRecord]
    catalogue_provenance: InputProvenance
    # "local_state" when the state was read from disk, else "evaluation_only".
    state_persisted: bool
    stage: DeploymentStage
    # One policy per source in use. A source with no policy here is refused.
    policies: Mapping[str, SourcePolicy]
    wikimedia: CollectionReport | None = None
    wikimedia_provenance: InputProvenance = NO_INPUT
    samples: SampleStore | None = None
    samples_provenance: InputProvenance = NO_INPUT
    events: EventStore | None = None
    event_sources: EventSourceStore | None = None
    events_provenance: InputProvenance = NO_INPUT


def _policy(inputs: EvidenceInputs, source: str) -> SourcePolicy:
    policy = inputs.policies.get(source)
    if policy is None or policy.source != source:
        raise SourcePolicyError(
            f"{source}: no source policy for this source, so it cannot be used for {_USE}"
        )
    return policy


def _require(inputs: EvidenceInputs, source: str) -> None:
    refusal = _policy(inputs, source).registration_refusal(PURPOSES[source], inputs.stage)
    if refusal is not None:
        raise SourcePolicyError(f"{source}: {refusal}")


def require_sources(inputs: EvidenceInputs) -> None:
    """Refuse the assessment unless every supplied input clears the stage. Fails closed."""
    require_catalogue_sources(
        inputs.records, inputs.policies, inputs.stage, retained=retained_sources(inputs.state)
    )
    if inputs.wikimedia is not None:
        _require(inputs, WIKIMEDIA_SOURCE)
    if inputs.samples is not None:
        _require(inputs, STEAM_SOURCE)
    seen: set[str] = set()
    if inputs.events is not None:
        seen |= {check.source for check in inputs.events.checks()}
        seen |= {observation.source for observation in inputs.events.observations()}
    if inputs.event_sources is not None:
        seen |= {record.source for record in inputs.event_sources.fetches()}
        seen |= {candidate.source for candidate in inputs.event_sources.candidates()}
    for source in sorted(seen):
        # Every source that left anything in the stores, whenever it did.
        require_event_source(source, inputs.policies, inputs.stage, _USE)


def policy_view(policy: SourcePolicy, purpose: SourcePurpose, stage: DeploymentStage) -> PolicyView:
    return PolicyView(
        source=policy.source,
        purpose=purpose,
        stage=stage,
        cleared_stages=tuple(c.stage for c in policy.stage_clearance if c.cleared),
        publishable=policy.publication_refusal() is None,
        publication_refusal=policy.publication_refusal(),
        public_launch_refusal=policy.registration_refusal(purpose, DeploymentStage.PUBLIC_LAUNCH),
    )


def pageviews_at(signal: Signal, as_of: datetime, lag: timedelta) -> Signal:
    """A recorded daily series cut back to what the source had published by `as_of`.

    The same cut the backtest makes: a period is visible only once the source's
    publication lag has passed, and the window ends on the last whole day before
    `as_of`. Periods that were due and are not held stay missing.
    """
    series = signal.series
    if series is None:
        return signal
    window = window_ending(evaluation_day_at(as_of))
    due = latest_due_period(signal.key.granularity, as_of, lag)
    kept = [
        point
        for point in series.points
        if point.value is not None
        and window.contains(point.period_start)
        and point.period_start <= due
    ]
    rebuilt = build_time_series(
        signal.canonical_game_id,
        signal.key,
        window,
        kept,
        observed_at=as_of,
        latest_due=min(due, window.end),
    )
    return signal.model_copy(update={"series": rebuilt})


def pageviews_read_at(series: TimeSeries) -> datetime:
    """The moment the engine reads a daily series at: the end of its latest due period.

    The engine's daily freshness counts whole days between the last bucket and the
    moment it is read, and knows nothing of publication lag. Read at the wall clock, a
    source that publishes a day late would be stale on every day of its life. Read at
    the end of the latest period the source was DUE to have published, it is stale
    exactly when a due day is missing, which is the rule as written ("stale as soon as a
    due day is missing") and the one score v0 applies through `latest_due`. No engine
    rule is changed by this; only the moment handed to it is.
    """
    return series.latest_due + series.granularity.step


class _Assessment:
    """One assessment's shared lookups, built once and thrown away."""

    def __init__(self, inputs: EvidenceInputs, as_of: datetime) -> None:
        self.inputs = inputs
        self.as_of = as_of
        state = inputs.state
        # A record fetched after `as_of` was not known then. Only its latest version is
        # held, so an earlier one cannot be shown in its place: it is left out, and what
        # leaned on it reads as unknown instead of borrowing from later.
        known = [record for record in inputs.records if record.fetched_at <= as_of]
        self.by_key = {record.key: record for record in known}
        eligibility = steam_eligibility(state, known, as_of)
        self.eligibility = {entry.canonical_game_id: entry for entry in eligibility}
        self.targets: dict[str, list[SamplingTarget]] = {}
        for target in sampling_targets(state, eligibility, as_of=as_of):
            if target.canonical_game_id is not None:
                self.targets.setdefault(target.canonical_game_id, []).append(target)
        self.pageviews: dict[str, Signal] = {}
        if inputs.wikimedia is not None:
            for run in inputs.wikimedia.runs:
                for outcome in run.outcomes:
                    if outcome.key == V0_INPUT_SIGNAL:
                        self.pageviews[outcome.canonical_game_id] = outcome
        self.event_records: tuple[EventRecord, ...] = ()
        self.event_checks: tuple[CoverageCheck, ...] = ()
        if inputs.events is not None:
            self.event_records = inputs.events.events(as_of)
            self.event_checks = inputs.events.checks(as_of)
        self.candidates = (
            ()
            if inputs.event_sources is None
            else inputs.event_sources.latest_candidates(
                as_of, EVENT_CLASSIFICATION_RULES_V1.version
            )
        )

    def view(self, source: str) -> PolicyView:
        return policy_view(_policy(self.inputs, source), PURPOSES[source], self.inputs.stage)

    # --- identity -------------------------------------------------------------------------

    def identity(self, game: CanonicalGame) -> IdentityEvidence:
        state, as_of = self.inputs.state, self.as_of
        bound = []
        for binding in state.mapping.bindings:
            if binding.canonical_game_id != game.canonical_game_id or not binding.active_at(as_of):
                continue
            if binding.key.source not in PURPOSES:
                continue  # the bootstrap entity's own row, not an external source
            record = self.by_key.get(binding.key)  # None if it was fetched after `as_of`
            bound.append(
                BoundRecord(
                    source=binding.key.source,
                    source_id=binding.key.source_id,
                    mapping_version=binding.mapping_version,
                    valid_from=binding.valid_from,
                    product_type=record.product_type.value if record else None,
                    release_dates=tuple(
                        dict.fromkeys(
                            f"{date.starts_on or 'undated'} ({date.precision.value})"
                            for date in (record.release_dates if record else ())
                        )
                    ),
                    parent=_ref(record.parent) if record else None,
                    version_parent=_ref(record.version_parent) if record else None,
                )
            )
        unresolved = tuple(
            UnresolvedRecord(
                source=entry.record.key.source,
                source_id=entry.record.key.source_id,
                reason=entry.reason.value,
            )
            for entry in state.quarantine.entries
            if entry.first_seen <= as_of
            and any(c.entity_ref == game.canonical_game_id for c in entry.candidates)
        )
        if game.canonical_game_id in LEGACY_ID_TO_CANONICAL.values():
            persistence = "frozen_bootstrap"
        else:
            persistence = "local_state" if self.inputs.state_persisted else "evaluation_only"
        return IdentityEvidence(
            canonical_game_id=game.canonical_game_id,
            slug=game.slug,
            name=game.name,
            lifecycle_state=game.lifecycle_state,
            id_persistence=persistence,
            bound_records=tuple(sorted(bound, key=lambda r: (r.source, r.source_id))),
            relationships=tuple(
                f"{link.relationship_type.value} of {link.parent_game_id}"
                for link in game.relationships
            ),
            unresolved_records=unresolved,
        )

    def catalogue_source(self, source: str, identity: IdentityEvidence) -> SourceEvidence:
        bound = [record for record in identity.bound_records if record.source == source]
        waiting = [record for record in identity.unresolved_records if record.source == source]
        fetched = [
            self.by_key[key].fetched_at
            for key in (SourceRecordKey(source=source, source_id=r.source_id) for r in bound)
            if key in self.by_key
        ]
        if bound:
            state, reason = EvidenceState.AVAILABLE, None
        elif waiting:
            state = EvidenceState.UNRESOLVED
            reason = (
                f"{len(waiting)} record(s) of this source name the game and are in "
                "quarantine; none is bound"
            )
        else:
            state = EvidenceState.UNKNOWN
            reason = "no record of this source was bound to the game at this moment"
        return SourceEvidence(
            source=source,
            kind=EvidenceKind.CATALOGUE_METADATA,
            state=state,
            reason=reason,
            bindings=tuple(
                NativeBinding(
                    source=source,
                    source_native_id=record.source_id,
                    basis="mapping_history",
                    mapping_version=record.mapping_version,
                    valid_from=record.valid_from,
                )
                for record in bound
            ),
            latest_observed_at=max(fetched, default=None),
            provenance=self.inputs.catalogue_provenance,
            policy=self.view(source),
        )

    # --- demand ---------------------------------------------------------------------------

    def pageviews_reading(
        self, game: CanonicalGame
    ) -> tuple[FamilyReadingV2, Signal | None, EvidenceState | None, str | None]:
        """The readership family, the signal it was read from, and any override of state."""
        family = SourceFamily.WIKIMEDIA_READERSHIP
        if game.identifier_for(WIKIMEDIA_SOURCE) is None:
            return wikimedia_family_v2(None, self.as_of), None, None, None
        if self.inputs.wikimedia is None:
            reading = family_reading_v2(
                family, Applicability.APPLICABLE, reason=NO_PAGEVIEWS_REASON
            )
            return reading, None, EvidenceState.NOT_COLLECTED, NO_PAGEVIEWS_REASON
        recorded = self.pageviews.get(game.canonical_game_id)
        if recorded is None or recorded.series is None:
            code = None if recorded is None or recorded.error_code is None else recorded.error_code
            reason = NO_RECORDING_REASON + ("" if code is None else f" ({code.value})")
            return (
                family_reading_v2(family, Applicability.APPLICABLE, reason=reason),
                None,
                None,
                reason,
            )
        lag = _policy(self.inputs, WIKIMEDIA_SOURCE).operations.expected_publication_lag
        visible = pageviews_at(recorded, self.as_of, lag)
        assert visible.series is not None
        return (
            wikimedia_family_v2(visible.series, pageviews_read_at(visible.series)),
            visible,
            None,
            None,
        )

    def players_reading(
        self, eligibility: SteamEligibility
    ) -> tuple[FamilyReadingV2, EvidenceState | None, str | None]:
        family = SourceFamily.STEAM_PLAYERS
        if eligibility.state is SourceEligibility.INELIGIBLE:
            reading = family_reading_v2(
                family, Applicability.INAPPLICABLE, reason=eligibility.reason
            )
            return reading, None, None
        if eligibility.state is SourceEligibility.UNKNOWN:
            reading = family_reading_v2(family, Applicability.UNKNOWN, reason=eligibility.reason)
            return reading, None, None
        if self.inputs.samples is None:
            reading = family_reading_v2(
                family, Applicability.APPLICABLE, reason=NO_SAMPLE_STORE_REASON
            )
            return reading, EvidenceState.NOT_COLLECTED, NO_SAMPLE_STORE_REASON
        reading = steam_family_v2(
            self.inputs.samples,
            self.mapping_of(eligibility.canonical_game_id),
            eligibility,
            self.as_of,
        )
        if reading.availability is Availability.APPLICABLE_BUT_MISSING:
            return reading, None, reading.reason or NEVER_SAMPLED_REASON
        return reading, None, None

    def mapping_of(self, canonical_game_id: str) -> MappingHistory:
        """The whole binding history of every Steam app this game has ever held.

        Reading a game's sample history asks, slot by slot, which entity an app belonged
        to then. Only the histories of the game's own apps can answer that, so only they
        are handed over. The answer is the one the full mapping gives; the search is not
        made through every other record's bindings for every slot.
        """
        bindings = self.inputs.state.mapping.bindings
        apps = {
            binding.key
            for binding in bindings
            if binding.canonical_game_id == canonical_game_id
            and binding.key.source == STEAM_RECORD_SOURCE
        }
        return MappingHistory(bindings=tuple(b for b in bindings if b.key in apps))

    def completeness(self, canonical_game_id: str) -> tuple[SampleCompleteness, ...]:
        """The last whole hour and whole day of each app, for slots bound to the game then."""
        store = self.inputs.samples
        if store is None or canonical_game_id not in self.targets:
            return ()
        hour = last_complete_hour(self.as_of)
        day = Granularity.DAILY.floor(self.as_of) - _DAY
        window = TimeWindow(start=min(day, hour), end=Granularity.FIFTEEN_MINUTES.floor(self.as_of))
        history = read_entity_history(
            store,
            self.mapping_of(canonical_game_id),
            canonical_game_id,
            STEAM_SLOTS,
            STEAM_RECORD_SOURCE,
            window,
            self.as_of,
        )
        rows = []
        for native in history.by_native_id:
            slots = [s for s in native.slots if s.key.slot_start + _HOUR / 4 <= self.as_of]
            now = current_level(slots)
            in_day = [s for s in slots if day <= s.key.slot_start < day + _DAY]
            in_hour = [s for s in slots if hour <= s.key.slot_start < hour + _HOUR]
            rows.append(
                SampleCompleteness(
                    source_native_id=native.source_native_id,
                    last_hour=hourly_aggregates(in_hour, hour, hour)[0],
                    last_day=daily_aggregates(hourly_aggregates(in_day, day, day + 23 * _HOUR))[0],
                    zero_reported=now is not None and now.value == 0,
                    current_level=None if now is None else now.value,
                    current_level_observed_at=None if now is None else now.observed_at,
                )
            )
        return tuple(rows)

    def demand_source(
        self,
        source: str,
        reading: FamilyReadingV2,
        bindings: tuple[NativeBinding, ...],
        provenance: InputProvenance,
        override: EvidenceState | None,
        reason: str | None,
    ) -> SourceEvidence:
        freshness = reading.signals[0].raw_freshness if reading.signals else None
        state = override or _STATE_OF[reading.availability]
        return SourceEvidence(
            source=source,
            kind=EvidenceKind.DEMAND_SIGNAL,
            family=reading.family,
            state=state,
            reason=reason or reading.reason,
            bindings=bindings,
            latest_observed_at=None if freshness is None else freshness.latest_observed_at,
            freshness=None if freshness is None else freshness.status.value,
            provenance=provenance if state in _READ_FROM_INPUT else NO_INPUT,
            policy=self.view(source),
        )

    def movements(
        self, readings: Sequence[tuple[FamilyReadingV2, InputProvenance]]
    ) -> tuple[Movement, ...]:
        rows = []
        for reading, provenance in readings:
            if reading.availability not in (
                Availability.APPLICABLE_AND_AVAILABLE,
                Availability.APPLICABLE_BUT_STALE,
            ):
                continue
            for item in reading.signals:
                # What an opportunity score would be allowed to read, and nothing more.
                usable = usable_features(item, Consumer.OPPORTUNITY_SCORE)
                rows.append(
                    Movement(
                        family=reading.family,
                        source=item.signal.source,
                        metric=item.signal.metric,
                        synthetic=provenance.synthetic,
                        levels_showable=self.view(item.signal.source).publishable,
                        features={
                            name: _feature(value)
                            for name, value in usable.items()
                            if isinstance(value, FeatureValue)
                        },
                        withheld=tuple(n for n in READABLE_FEATURES if n not in usable),
                        maturity=item.signal.maturity.value,
                        coverage=item.signal.coverage,
                    )
                )
        return tuple(rows)

    # --- events ---------------------------------------------------------------------------

    def event_source(
        self,
        game: CanonicalGame,
        eligibility: SteamEligibility,
        bindings: tuple[NativeBinding, ...],
    ) -> SourceEvidence:
        source = STEAM_NEWS_SOURCE
        latest: datetime | None = None
        if self.inputs.events is None:
            state, reason = EvidenceState.NOT_COLLECTED, NO_EVENT_STORE_REASON
        elif eligibility.state is SourceEligibility.INELIGIBLE:
            state, reason = EvidenceState.INAPPLICABLE, eligibility.reason
        elif eligibility.state is SourceEligibility.UNKNOWN:
            state, reason = EvidenceState.UNKNOWN, eligibility.reason
        else:
            latest, _ = coverage_of(
                game.canonical_game_id,
                [r for r in self.event_records if source in r.sources],
                [c for c in self.event_checks if c.source == source],
                self.as_of,
            )
            limit = timedelta(days=EVENT_HEAT_RULES_V1.coverage_max_age_days)
            if latest is None:
                state = EvidenceState.MISSING
                reason = "this source had not looked at the game's announcements by this moment"
            elif self.as_of - latest > limit:
                state = EvidenceState.STALE
                reason = "its last look at the game's announcements is too old to rely on"
            else:
                state, reason = EvidenceState.AVAILABLE, None
        return SourceEvidence(
            source=source,
            kind=EvidenceKind.EVENT_EVIDENCE,
            family=SourceFamily.OFFICIAL_EVENTS,
            state=state,
            reason=reason,
            bindings=bindings,
            latest_observed_at=latest,
            provenance=self.inputs.events_provenance if state in _READ_FROM_INPUT else NO_INPUT,
            policy=self.view(source),
        )

    def candidate_counts(self, canonical_game_id: str) -> CandidateCounts:
        own = [c for c in self.candidates if c.canonical_game_id == canonical_game_id]
        promoted = sum(c.observation_id is not None for c in own)
        return CandidateCounts(
            total=len(own),
            classified=sum(c.classified for c in own),
            promoted=promoted,
            not_promoted=len(own) - promoted,
            timing_not_established=sum(c.classified and c.proposed_starts_at is None for c in own),
            refusals=dict(sorted(Counter(r.value for c in own for r in c.refusals).items())),
        )

    # --- one game -------------------------------------------------------------------------

    def game(self, game: CanonicalGame) -> GameEvidence:
        inputs, as_of, game_id = self.inputs, self.as_of, game.canonical_game_id
        eligibility = self.eligibility[game_id]
        identity = self.identity(game)
        apps = tuple(
            NativeBinding(
                source=STEAM_RECORD_SOURCE,
                source_native_id=target.source_native_id,
                basis="mapping_history",
                mapping_version=target.mapping_version,
                valid_from=_valid_from(inputs.state, target, as_of),
                binding_ref=target.binding_ref,
            )
            for target in self.targets.get(game_id, ())
        )
        article = game.identifier_for(WIKIMEDIA_SOURCE)
        pages = (
            ()
            if article is None
            else (
                NativeBinding(
                    source=WIKIMEDIA_SOURCE,
                    source_native_id=article.value,
                    basis="catalogue_identifier",
                ),
            )
        )
        readers, pageviews, readers_state, readers_reason = self.pageviews_reading(game)
        players, players_state, players_reason = self.players_reading(eligibility)
        demand = entity_evidence_v2(game_id, as_of, (readers, players))
        events = assess_events(game_id, self.event_records, self.event_checks, as_of)
        scored = assess_entity(
            EntitySignals(
                canonical_game_id=game_id, signals=() if pageviews is None else (pageviews,)
            )
        )
        sources = (
            self.catalogue_source(IGDB_SOURCE, identity),
            self.catalogue_source(STEAM_RECORD_SOURCE, identity),
            self.demand_source(
                WIKIMEDIA_SOURCE,
                readers,
                pages,
                inputs.wikimedia_provenance,
                readers_state,
                readers_reason,
            ),
            self.demand_source(
                STEAM_SOURCE,
                players,
                apps,
                inputs.samples_provenance,
                players_state,
                players_reason,
            ),
            self.event_source(game, eligibility, apps),
            _not_integrated(
                CREATOR_SUPPLY, EvidenceKind.CREATOR_SUPPLY, SourceFamily.CREATOR_SUPPLY
            ),
            _not_integrated(COMMUNITY_INTEREST, EvidenceKind.COMMUNITY_INTEREST, None),
        )
        independent = sum(
            1 for item in sources if item.kind is EvidenceKind.DEMAND_SIGNAL and item.usable
        )
        return GameEvidence(
            canonical_game_id=game_id,
            as_of=as_of,
            identity=identity,
            sources=sources,
            demand=demand,
            movements=self.movements(
                ((readers, inputs.wikimedia_provenance), (players, inputs.samples_provenance))
            ),
            sample_completeness=self.completeness(game_id),
            score_v0=scored.score,
            confidence_v0=scored.confidence,
            events=events,
            upcoming=(
                upcoming_radar([events], as_of).items
                if events.availability is EventAvailability.AVAILABLE
                else ()
            ),
            candidates=self.candidate_counts(game_id),
            independent_demand_families=independent,
            usable_sources=tuple(item.source for item in sources if item.usable),
            restricted_sources=tuple(item.source for item in sources if item.restricted),
            synthetic_sources=tuple(item.source for item in sources if item.provenance.synthetic),
            gaps=_gaps(sources, identity, events, independent),
        )


def _ref(key: SourceRecordKey | None) -> str | None:
    return None if key is None else f"{key.source}:{key.source_id}"


def _feature(value: FeatureValue) -> str:
    return f"{value.value:.4g}" if value.value is not None else f"none ({value.reason})"


def _valid_from(state: CatalogueState, target: SamplingTarget, as_of: datetime) -> datetime | None:
    key = SourceRecordKey(source=STEAM_RECORD_SOURCE, source_id=target.source_native_id)
    binding = active_binding(state, key, as_of)
    return None if binding is None else binding.valid_from


def _standing(item: SourceEvidence) -> str:
    synthetic = " but SYNTHETIC, so it counts for nothing" if item.provenance.synthetic else ""
    return item.state.value + synthetic


def _not_integrated(source: str, kind: EvidenceKind, family: SourceFamily | None) -> SourceEvidence:
    return SourceEvidence(
        source=source,
        kind=kind,
        family=family,
        state=EvidenceState.NOT_INTEGRATED,
        reason=NOT_INTEGRATED_REASON,
    )


def _gaps(
    sources: Sequence[SourceEvidence],
    identity: IdentityEvidence,
    events: EventIntelligence,
    independent: int,
) -> tuple[Gap, ...]:
    """What a creator-opportunity recommendation would still need. Stated, never scored."""
    gaps = [
        Gap(
            code="creator_supply_not_integrated",
            text=(
                "No creator-supply source exists, so how many videos already cover this "
                "game is unknown. Unknown is not low competition."
            ),
        ),
        Gap(
            code="community_interest_not_integrated",
            text="No community-interest source exists, so discussion volume is unknown.",
        ),
        Gap(
            code="opportunity_score_not_defined",
            text=(
                "No opportunity score exists. metaspike_score_v0 reads pageviews alone and "
                "is reported unchanged; it is not a recommendation."
            ),
        ),
    ]
    demand = [item for item in sources if item.kind is EvidenceKind.DEMAND_SIGNAL]
    if independent == 0:
        gaps.append(
            Gap(
                code="no_current_demand_evidence",
                text="No demand family has a current, real observation of this game: "
                + "; ".join(f"{item.source} is {_standing(item)}" for item in demand)
                + ".",
            )
        )
    elif independent == 1:
        other = next(item for item in demand if not item.usable)
        gaps.append(
            Gap(
                code="single_demand_family",
                text=(
                    "One demand family observes this game, so nothing corroborates it: "
                    f"{other.source} is {_standing(other)}."
                ),
            )
        )
    if events.availability is not EventAvailability.AVAILABLE:
        gaps.append(
            Gap(
                code="event_coverage_unavailable",
                text=events.summary,
            )
        )
    if identity.unresolved_records:
        gaps.append(
            Gap(
                code="unresolved_source_records",
                text=(
                    f"{len(identity.unresolved_records)} source record(s) name this game and "
                    "are quarantined. They contribute nothing until they are resolved."
                ),
            )
        )
    restricted = [item.source for item in sources if item.restricted]
    if restricted:
        gaps.append(
            Gap(
                code="evidence_restricted_to_private_development",
                text=(
                    "Not publishable under the current source policies: "
                    + ", ".join(restricted)
                    + "."
                ),
            )
        )
    synthetic = [item.source for item in sources if item.provenance.synthetic]
    if synthetic:
        gaps.append(
            Gap(
                code="synthetic_evidence",
                text=(
                    "Invented for a demonstration, and no observation of this game: "
                    + ", ".join(synthetic)
                    + "."
                ),
            )
        )
    return tuple(gaps)


def assess_evidence(
    inputs: EvidenceInputs, as_of: datetime, game_ids: Sequence[str] | None = None
) -> EvidenceReport:
    """The evidence held for each game as known at `as_of`. Reads; changes nothing."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    require_sources(inputs)
    if as_of < inputs.state.generated_at:
        raise ValueError(
            f"as_of ({as_of.isoformat()}) is earlier than the catalogue state "
            f"({inputs.state.generated_at.isoformat()}); the catalogue it describes did "
            "not exist then"
        )
    catalogue = inputs.state.catalogue
    wanted = [game.canonical_game_id for game in catalogue.games] if game_ids is None else game_ids
    assessment = _Assessment(inputs, as_of)
    provenance = {
        "catalogue": inputs.catalogue_provenance,
        WIKIMEDIA_SOURCE: inputs.wikimedia_provenance,
        STEAM_SOURCE: inputs.samples_provenance,
        STEAM_NEWS_SOURCE: inputs.events_provenance,
    }
    return EvidenceReport(
        as_of=as_of,
        stage=inputs.stage,
        contains_synthetic=any(item.synthetic for item in provenance.values()),
        inputs=provenance,
        policies=tuple(
            policy_view(_policy(inputs, source), purpose, inputs.stage)
            for source, purpose in PURPOSES.items()
        ),
        games=tuple(assessment.game(catalogue.get(game_id)) for game_id in wanted),
    )


def publication_refusal(report: EvidenceReport) -> str:
    """Why this report may not be published. There is always a reason.

    The report is a private reading. It has no schema in the artifact contract and no
    writer in the publishing path; this states why, from the report's own content.
    """
    restricted = sorted({name for game in report.games for name in game.restricted_sources})
    reasons = [f"its distribution is {report.distribution}"]
    if restricted:
        reasons.append(
            "it holds evidence from sources that are not publishable: " + ", ".join(restricted)
        )
    if report.contains_synthetic:
        reasons.append("it holds synthetic evidence")
    return "a game evidence report is never published: " + "; ".join(reasons)
