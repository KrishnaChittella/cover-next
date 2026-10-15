"""Offline historical evaluation of `creator_opportunity_rules_v1` (U6.5).

Walks forward over fixed, declared moments and asks, at each, what the rubric would have
said with only what was visible then. Afterwards, and separately, it attaches what
happened next. It changes no rule: not the rubric, not its thresholds (robust z 3.5, a
7-day discussion window), not `validation_protocol_v1`, not `outcome_rules_v1`, not
`recommendation_snapshot_v2`.

**Two questions, kept apart.**

    (A) future audience attention   did attention to the game rise after the moment?
    (B) creator-video success       would a video made then have done well?

Only (A) can be measured from what is recorded, and only by proxy: `outcome_rules_v1`
over Wikipedia readership. Every outcome here is labelled an ATTENTION PROXY. Creator-
video success cannot yet be validated from the recorded data: no video was made, no video
performance was recorded, and readership is not viewership.

**What a past moment can honestly see.** The prediction side is `RecordedHistory`: the
pageview recording cut back to what Wikimedia had published by the moment under its
publication lag. Nothing else was recorded before the moments the recording covers. No
Steam player samples, YouTube searches, Reddit posts or event observations exist from
then, and none is invented, backdated, or granted a standing it did not have when it was
observed. Those families are MISSING at every recorded moment, with a reason. So on
recorded data the rubric can reach at most a provisional watchlist, never a candidate.

A moment the recording cannot reconstruct is UNAVAILABLE and makes no prediction.

**Four instants, never merged.** Each snapshot records the prediction moment (`as_of`),
the newest completed period (`evaluation_day`), and the newest period the demand source
had published by then (`latest_due_day`). The outcome period is attached later, by the
outcome record, and starts on the first day wholly after the moment.

**Snapshots are frozen before any outcome is read.** An `OpportunitySnapshot`
(`opportunity_snapshot_v1`, new and additive) is fingerprinted from its content. Outcomes
are separate records pointing at a fingerprint; changing the future changes an outcome
and never a snapshot. A snapshot carries real readiness only. In synthetic mode every
input is invented, the whole report says so, and real and synthetic inputs are refused
together.

**Metrics are descriptive.** Counts and shares with their denominators, and no interval:
there are too few observations for one to mean anything. A share over nothing is NOT
ESTIMABLE, never zero.
"""

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal

from pydantic import AwareDatetime, Field

from metaspike.community_sources.reddit import REDDIT_DATA_POLICY, collection_refusal
from metaspike.creator_sources.youtube import YOUTUBE_DATA_POLICY
from metaspike.domain.families import SourceFamily
from metaspike.domain.games import Catalogue
from metaspike.domain.model import FrozenModel
from metaspike.domain.signals import Signal
from metaspike.domain.source_policy import DeploymentStage, SourcePolicy, SourcePurpose
from metaspike.domain.timeseries import start_of_day
from metaspike.event_sources.steam_news import STEAM_NEWS_POLICY
from metaspike.evidence.assemble import pageviews_read_at
from metaspike.evidence.model import InputProvenance, Standing
from metaspike.evidence.provenance import recording_digest, require_honest
from metaspike.features.semantics import Availability
from metaspike.opportunity.rules import (
    NOTEWORTHY_ROBUST_Z,
    OPPORTUNITY_RULES_VERSION,
    TIMELY_DISCUSSION,
    Direction,
    Facts,
    MovementNote,
    Readiness,
    Statement,
    ahead_of_next,
    movement_direction,
    ordering_key,
    readiness,
)
from metaspike.pageview_recorder import (
    LEGACY_PAGEVIEW_RECORDINGS,
    check_recording,
    is_recorder_output,
)
from metaspike.pipeline.collection import CollectionReport, collect
from metaspike.pipeline.signal_features import wikimedia_family_v2
from metaspike.scoring.v0 import V0_INPUT_SIGNAL
from metaspike.sources.registry import default_registry
from metaspike.sources.replay import replay_client
from metaspike.sources.wikimedia import WIKIMEDIA_PAGEVIEWS_POLICY
from metaspike.validation.backtest import days_between, prediction_moment
from metaspike.validation.history import RecordedHistory, latest_due_at
from metaspike.validation.outcomes import OutcomeAvailability, OutcomeUnavailable, label
from metaspike.validation.rules import OUTCOME_RULES_V1, PROTOCOL_VERSION, OutcomeRules
from metaspike.validation.snapshot import RecommendationSnapshot, content_id, predict

OPPORTUNITY_SNAPSHOT_FORMAT: Final = "opportunity_snapshot_v1"
HISTORY_REPORT_FORMAT: Final = "opportunity_history_report_v1"
ATTENTION_PROXY: Final = (
    "ATTENTION PROXY: outcome_rules_v1 over Wikipedia readership says whether attention "
    "to the game rose after the moment. It does not say whether a video would have done "
    "well."
)
CREATOR_SUCCESS_UNVALIDATED: Final = (
    "Creator-video success cannot yet be validated from the recorded data."
)
NOT_ESTIMABLE_NO_CANDIDATES: Final = "NOT ESTIMABLE — NO POSITIVE CANDIDATES"
SENSITIVITY: Final = (
    "EXPLORATORY sensitivity, not a result: how many decisions would change if the "
    "movement threshold alone were different. No alternative is approved or claimed better, "
    "no outcome is attached to any alternative, and creator_opportunity_rules_v1 is unchanged."
)
# Alternatives to robust z 3.5, shown for exploration only. Never applied to a decision.
EXPLORATORY_ROBUST_Z: Final = (2.5, 3.0, 4.0, 5.0)
# A bounded universe: this is a study of a handful of games, not a sweep of the catalogue.
MAX_UNIVERSE: Final = 50
DEMAND_SOURCE: Final = V0_INPUT_SIGNAL.source
DEMAND_FAMILY: Final = SourceFamily.WIKIMEDIA_READERSHIP
PLAYERS_FAMILY: Final = "steam_concurrent_players"


class EvaluationMode(StrEnum):
    RECORDED = "recorded"
    SYNTHETIC = "synthetic"


class Counting(StrEnum):
    # A real readiness: only verified evidence counts.
    VERIFIED_ONLY = "verified_only"
    # Synthetic mode: the invented inputs are counted so the rubric can be seen working.
    # Nothing counted this way is evidence of anything.
    SYNTHETIC_AS_IF_VERIFIED = "synthetic_counted_as_if_verified"


class ContextFamily(StrEnum):
    EVENT = "event_intelligence"
    COMMUNITY = "community_interest"
    CREATOR_SUPPLY = "creator_supply"


_CONTEXT_POLICY: Final[Mapping[ContextFamily, tuple[SourcePolicy, SourcePurpose]]] = {
    ContextFamily.EVENT: (STEAM_NEWS_POLICY, SourcePurpose.EVENT_INTELLIGENCE),
    ContextFamily.COMMUNITY: (REDDIT_DATA_POLICY, SourcePurpose.COMMUNITY_INTEREST),
    ContextFamily.CREATOR_SUPPLY: (YOUTUBE_DATA_POLICY, SourcePurpose.CREATOR_SUPPLY),
}
FAMILIES: Final = (
    DEMAND_FAMILY.value,
    PLAYERS_FAMILY,
    ContextFamily.EVENT.value,
    ContextFamily.COMMUNITY.value,
    ContextFamily.CREATOR_SUPPLY.value,
)


class ContextObservation(FrozenModel):
    """One observation of a non-demand family, dated by when it was RETRIEVED.

    Nothing is usable before `retrieved_at`, whatever it describes: an announcement
    retrieved on Tuesday did not exist for a Monday moment. It bears on a moment between
    `timely_from` and `timely_until` (an event upcoming or active; a discussion within
    the rubric's 7 days; a creator sample still current).
    """

    canonical_game_id: str
    family: ContextFamily
    reference: str
    retrieved_at: AwareDatetime
    timely_from: AwareDatetime
    timely_until: AwareDatetime
    # A creator-supply sample that ran to its end.
    complete: bool = True
    # A community angle needs at least two supporting posts.
    supporting: int = Field(default=1, ge=0)
    provenance: InputProvenance


def community_angle(
    canonical_game_id: str,
    reference: str,
    newest_post_at: datetime,
    retrieved_at: datetime,
    supporting: int,
    provenance: InputProvenance,
) -> ContextObservation:
    """A discussion angle, timely for the rubric's 7 days after its newest post."""
    return ContextObservation(
        canonical_game_id=canonical_game_id,
        family=ContextFamily.COMMUNITY,
        reference=reference,
        retrieved_at=retrieved_at,
        timely_from=newest_post_at,
        timely_until=newest_post_at + TIMELY_DISCUSSION,
        supporting=supporting,
        provenance=provenance,
    )


class MissingReason(StrEnum):
    NOT_RECORDED = "not_recorded_at_or_before_the_moment"
    OBSERVED_ONLY_AFTER = "observed_only_after_the_moment"
    NOT_TIMELY = "observed_but_not_timely_at_the_moment"
    INCOMPLETE_SAMPLE = "creator_sample_incomplete"
    TOO_FEW_POSTS = "fewer_than_two_supporting_posts"
    NO_PLAYER_HISTORY = "no_player_sample_history_recorded"
    NOT_MAPPED = "no_recorded_series_for_the_game"
    NOT_AVAILABLE = "demand_signal_not_available_at_the_moment"
    UNBOUND = "identity_not_bound_at_the_moment"


class MissingFamily(FrozenModel):
    family: str
    reason: MissingReason
    detail: str = ""


class EvidenceOrigin(FrozenModel):
    family: str
    origin: str
    reference: str
    standing: Standing


class Reconstruction(StrEnum):
    # Demand reconstructed from the recording; other families missing, with reasons.
    DEMAND_ONLY = "demand_reconstructed_other_families_missing"
    # Demand and at least one other family were visible at the moment.
    MULTI_FAMILY = "demand_and_other_families_reconstructed"
    # No demand reading could be made at the moment.
    NO_DEMAND = "no_demand_reading_at_the_moment"
    # The game's identity binding had not begun at the moment.
    UNBOUND = "identity_not_bound_at_the_moment"


class OpportunitySnapshot(FrozenModel):
    """What the rubric said about one game at one past moment. Frozen; no outcome."""

    fingerprint: str = Field(pattern=r"^opsnap_[0-9a-f]{32}$")
    format: Literal["opportunity_snapshot_v1"] = OPPORTUNITY_SNAPSHOT_FORMAT
    rules_version: Literal["creator_opportunity_rules_v1"] = OPPORTUNITY_RULES_VERSION
    mode: EvaluationMode
    counting: Counting
    canonical_game_id: str
    slug: str
    name: str
    # The prediction moment.
    as_of: AwareDatetime
    # The newest completed period at the moment: a calendar fact.
    evaluation_day: date
    # The newest period the demand source had published by the moment.
    latest_due_day: date
    # The newest day actually observed for this game among those visible.
    latest_observed_day: date | None = None
    reconstruction: Reconstruction
    readiness: Readiness
    facts: Facts
    # Families whose counted evidence was a verified observation.
    verified_families: tuple[str, ...] = ()
    counted_families: tuple[str, ...] = ()
    movements: tuple[MovementNote, ...] = ()
    why_now: tuple[Statement, ...] = ()
    angle_available: bool
    angle_references: tuple[str, ...] = ()
    creator_coverage: Literal["complete_sample", "incomplete_sample", "not_observed_by_moment"]
    missing: tuple[MissingFamily, ...] = ()
    origins: tuple[EvidenceOrigin, ...]
    position: int = Field(ge=1)
    universe_size: int = Field(ge=1)
    ahead_of_next: str
    # The recommendation_snapshot_v2 made at the same moment, from the same inputs.
    base_snapshot_id: str


class OutcomeStatus(StrEnum):
    POSITIVE = "positive_attention"
    NEGATIVE = "negative_attention"
    NEITHER = "neither_positive_nor_negative"
    # The forward period runs past the end of the data: not yet observable.
    FORWARD_INCOMPLETE = "forward_period_incomplete"
    UNAVAILABLE = "unavailable"


class OpportunityOutcome(FrozenModel):
    """What attention did after a snapshot's moment. Attached afterwards; a proxy."""

    outcome_id: str = Field(pattern=r"^opout_[0-9a-f]{32}$")
    snapshot_fingerprint: str
    canonical_game_id: str
    measure: Literal["attention_proxy"] = "attention_proxy"
    measure_label: str = ATTENTION_PROXY
    outcome_rules_version: str
    label_id: str | None = None
    prediction_at: AwareDatetime
    outcome_window_start: date
    outcome_window_end: date
    data_through: date
    status: OutcomeStatus
    reason: str | None = None
    realized_uplift_72h: float | None = None


class MomentRecord(FrozenModel):
    as_of: AwareDatetime
    reconstructed: bool
    reason: str | None = None
    assessed: int = Field(ge=0)


class Count(FrozenModel):
    name: str
    count: int = Field(ge=0)
    of: int = Field(ge=0)


class GroupMetrics(FrozenModel):
    readiness: Readiness
    assessments: int = Field(ge=0)
    # Assessments are not independent: one game on consecutive moments is one episode,
    # and consecutive moments' forward windows overlap.
    distinct_games: int = Field(ge=0)
    episodes: int = Field(ge=0)
    outcomes: tuple[Count, ...]
    positive_share: str


class SensitivityRow(FrozenModel):
    """EXPLORATORY: the decisions under another movement threshold. Not a rule."""

    robust_z: float
    changed: int = Field(ge=0)
    candidates: int = Field(ge=0)
    watchlist: int = Field(ge=0)
    insufficient: int = Field(ge=0)


class HistoryMetrics(FrozenModel):
    moments_requested: int = Field(ge=0)
    moments_reconstructed: int = Field(ge=0)
    moments_unavailable: int = Field(ge=0)
    games: int = Field(ge=0)
    assessments: int = Field(ge=0)
    # Game-moments with no prediction at all, because the moment was unavailable.
    no_prediction: int = Field(ge=0)
    candidates: int = Field(ge=0)
    watchlist: int = Field(ge=0)
    insufficient: int = Field(ge=0)
    abstention: str
    family_availability: tuple[Count, ...]
    outcome_availability: tuple[Count, ...]
    missing_by_reason: tuple[Count, ...]
    groups: tuple[GroupMetrics, ...]
    candidate_positive_share: str
    sensitivity_label: str = SENSITIVITY
    sensitivity: tuple[SensitivityRow, ...] = ()


class OpportunityHistoryReport(FrozenModel):
    fingerprint: str = Field(pattern=r"^ophist_[0-9a-f]{32}$")
    format: Literal["opportunity_history_report_v1"] = HISTORY_REPORT_FORMAT
    rules_version: Literal["creator_opportunity_rules_v1"] = OPPORTUNITY_RULES_VERSION
    outcome_rules_version: str
    protocol_version: str = PROTOCOL_VERSION
    distribution: Literal["private_development_only"] = "private_development_only"
    mode: EvaluationMode
    counting: Counting
    first_day: date
    last_day: date
    cadence_days: int = Field(ge=1)
    universe: tuple[str, ...]
    publication_lag_hours: float = Field(ge=0)
    noteworthy_robust_z: float
    timely_discussion_days: float
    demand_origin: EvidenceOrigin
    moments: tuple[MomentRecord, ...]
    snapshots: tuple[OpportunitySnapshot, ...]
    outcomes: tuple[OpportunityOutcome, ...]
    metrics: HistoryMetrics
    statements: tuple[str, ...]
    assumptions: tuple[str, ...]


class HistoryRefused(Exception):
    """The evaluation was asked to do something it must not."""


# --- the prediction side ------------------------------------------------------------------


def _origin(family: str, provenance: InputProvenance) -> EvidenceOrigin:
    return EvidenceOrigin(
        family=family,
        origin=provenance.origin.value,
        reference=provenance.reference,
        standing=provenance.standing,
    )


def _counter(counting: Counting) -> Callable[[Standing], bool]:
    if counting is Counting.VERIFIED_ONLY:
        return lambda standing: standing is Standing.VERIFIED
    return lambda standing: standing is not Standing.NONE


class _Draft(FrozenModel):
    """A snapshot before it has a place in the order or a fingerprint."""

    canonical_game_id: str
    slug: str
    name: str
    latest_observed_day: date | None
    reconstruction: Reconstruction
    readiness: Readiness
    facts: Facts
    verified_families: tuple[str, ...]
    counted_families: tuple[str, ...]
    movements: tuple[MovementNote, ...]
    why_now: tuple[Statement, ...]
    angle_available: bool
    angle_references: tuple[str, ...]
    creator_coverage: Literal["complete_sample", "incomplete_sample", "not_observed_by_moment"]
    missing: tuple[MissingFamily, ...]
    origins: tuple[EvidenceOrigin, ...]
    base_snapshot_id: str


def _movement(
    signal: Signal | None, standing: Standing
) -> tuple[MovementNote | None, MissingFamily | None, date | None]:
    """The readership movement exactly as the evidence reading computes it."""
    if signal is None or signal.series is None:
        return (
            None,
            MissingFamily(family=DEMAND_FAMILY.value, reason=MissingReason.NOT_MAPPED),
            None,
        )
    series = signal.series
    observed = [point.period_day for point in series.points if point.value is not None]
    latest = max(observed, default=None)
    reading = wikimedia_family_v2(series, pageviews_read_at(series))
    if reading.availability is not Availability.APPLICABLE_AND_AVAILABLE or not reading.signals:
        return (
            None,
            MissingFamily(
                family=DEMAND_FAMILY.value,
                reason=MissingReason.NOT_AVAILABLE,
                detail=reading.reason or reading.availability.value,
            ),
            latest,
        )
    measured = reading.signals[0].signal
    lift, z = measured.log_lift.value, measured.robust_z.value
    note = MovementNote(
        family=DEMAND_FAMILY,
        source=DEMAND_SOURCE,
        standing=standing,
        direction=movement_direction(lift, z),
        log_lift=lift,
        robust_z=z,
        observed_through=None if latest is None else start_of_day(latest),
    )
    return note, None, latest


def _context(
    family: ContextFamily,
    observations: Sequence[ContextObservation],
    moment: datetime,
    counted: Callable[[Standing], bool],
) -> tuple[list[ContextObservation], MissingFamily | None]:
    """The observations of one family that count at `moment`, or why there are none."""
    seen = [o for o in observations if o.family is family and o.retrieved_at <= moment]
    if not seen:
        later = any(o.family is family for o in observations)
        reason = MissingReason.OBSERVED_ONLY_AFTER if later else MissingReason.NOT_RECORDED
        return [], MissingFamily(family=family.value, reason=reason)
    timely = [o for o in seen if o.timely_from <= moment <= o.timely_until]
    if not timely:
        return [], MissingFamily(family=family.value, reason=MissingReason.NOT_TIMELY)
    if family is ContextFamily.CREATOR_SUPPLY:
        complete = [o for o in timely if o.complete]
        if not complete:
            return [], MissingFamily(family=family.value, reason=MissingReason.INCOMPLETE_SAMPLE)
        timely = complete
    if family is ContextFamily.COMMUNITY:
        supported = [o for o in timely if o.supporting >= 2]
        if not supported:
            return [], MissingFamily(family=family.value, reason=MissingReason.TOO_FEW_POSTS)
        timely = supported
    return [o for o in timely if counted(o.provenance.standing)], None


def _assess(
    game_id: str,
    slug: str,
    name: str,
    moment: datetime,
    signal: Signal | None,
    bound: bool,
    demand: InputProvenance,
    observations: Sequence[ContextObservation],
    counting: Counting,
    base: RecommendationSnapshot,
) -> _Draft:
    counted = _counter(counting)
    missing: list[MissingFamily] = []
    movements: tuple[MovementNote, ...] = ()
    latest: date | None = None
    if not bound:
        missing.append(MissingFamily(family=DEMAND_FAMILY.value, reason=MissingReason.UNBOUND))
    else:
        note, absent, latest = _movement(signal, demand.standing)
        if note is not None:
            movements = (note,)
        if absent is not None:
            missing.append(absent)
    missing.append(MissingFamily(family=PLAYERS_FAMILY, reason=MissingReason.NO_PLAYER_HISTORY))

    visible: dict[ContextFamily, list[ContextObservation]] = {}
    for family in ContextFamily:
        found, absent = _context(family, observations, moment, counted)
        visible[family] = found
        if absent is not None:
            missing.append(absent)

    up = tuple(m.family for m in movements if m.direction is Direction.UP and counted(m.standing))
    down = tuple(
        m.family for m in movements if m.direction is Direction.DOWN and counted(m.standing)
    )
    events = visible[ContextFamily.EVENT]
    community = visible[ContextFamily.COMMUNITY]
    creators = visible[ContextFamily.CREATOR_SUPPLY]
    demand_counts = bool(movements) and counted(demand.standing)
    facts = Facts(
        moving_up=up,
        moving_down=down,
        disagreement=bool(up and down),
        timely_event=bool(events),
        # As in the rubric: a timely event is itself an angle.
        angle=bool(community or events),
        coverage_known=bool(creators),
        sources_answering=int(demand_counts) + sum(1 for found in visible.values() if found),
    )
    counted_families = tuple(
        [DEMAND_FAMILY.value] * demand_counts
        + [family.value for family, found in visible.items() if found]
    )
    verified = tuple(
        [DEMAND_FAMILY.value] * (demand_counts and demand.standing is Standing.VERIFIED)
        + [
            family.value
            for family, found in visible.items()
            if any(o.provenance.standing is Standing.VERIFIED for o in found)
        ]
    )
    why_now = [
        Statement(
            text=f"{m.family.value} is up against its own history (log lift "
            f"{m.log_lift:.3g}, robust z {m.robust_z:.3g})",
            standing=m.standing,
            references=(m.source,),
        )
        for m in movements
        if m.family in up
    ]
    why_now += [
        Statement(
            text="an event is upcoming or active",
            standing=o.provenance.standing,
            references=(o.reference,),
        )
        for o in events
    ]
    why_now += [
        Statement(
            text=f"{o.supporting} posts discuss it within {TIMELY_DISCUSSION.days} days",
            standing=o.provenance.standing,
            references=(o.reference,),
        )
        for o in community
    ]
    seen_creators = [
        o
        for o in observations
        if o.family is ContextFamily.CREATOR_SUPPLY
        and o.retrieved_at <= moment
        and o.timely_from <= moment <= o.timely_until
    ]
    coverage: Literal["complete_sample", "incomplete_sample", "not_observed_by_moment"] = (
        "complete_sample"
        if any(o.complete for o in seen_creators)
        else "incomplete_sample"
        if seen_creators
        else "not_observed_by_moment"
    )
    origins = [_origin(DEMAND_FAMILY.value, demand)] + [
        _origin(o.family.value, o.provenance)
        for o in sorted(
            {(o.family, o.provenance.reference): o for o in observations}.values(),
            key=lambda o: (o.family.value, o.provenance.reference),
        )
    ]
    if not bound:
        reconstruction = Reconstruction.UNBOUND
    elif not movements:
        reconstruction = Reconstruction.NO_DEMAND
    elif any(visible.values()):
        reconstruction = Reconstruction.MULTI_FAMILY
    else:
        reconstruction = Reconstruction.DEMAND_ONLY
    return _Draft(
        canonical_game_id=game_id,
        slug=slug,
        name=name,
        latest_observed_day=latest,
        reconstruction=reconstruction,
        readiness=readiness(facts),
        facts=facts,
        verified_families=verified,
        counted_families=counted_families,
        movements=movements,
        why_now=tuple(why_now),
        angle_available=facts.angle,
        angle_references=tuple(o.reference for o in (*community, *events)),
        creator_coverage=coverage,
        missing=tuple(missing),
        origins=tuple(dict.fromkeys(origins)),
        base_snapshot_id=base.snapshot_id,
    )


def _freeze(
    drafts: Sequence[_Draft],
    moment: datetime,
    evaluation_day: date,
    due_day: date,
    mode: EvaluationMode,
    counting: Counting,
) -> tuple[OpportunitySnapshot, ...]:
    """Place each draft in the rubric's order, explain it, and fingerprint it."""

    def key(d: _Draft) -> tuple[object, ...]:
        return ordering_key(d.readiness, d.facts, d.canonical_game_id)

    ordered = sorted(drafts, key=key)
    frozen = []
    for index, draft in enumerate(ordered):
        reason = (
            "last in the list"
            if index + 1 == len(ordered)
            else ahead_of_next(key(draft), key(ordered[index + 1]))
        )
        fields = {
            **draft.model_dump(),
            "mode": mode,
            "counting": counting,
            "as_of": moment,
            "evaluation_day": evaluation_day,
            "latest_due_day": due_day,
            "position": index + 1,
            "universe_size": len(ordered),
            "ahead_of_next": reason,
        }
        draft_snapshot = OpportunitySnapshot.model_validate(
            {**fields, "fingerprint": "opsnap_" + "0" * 32}
        )
        payload = draft_snapshot.model_dump(mode="json", exclude={"fingerprint"})
        frozen.append(
            draft_snapshot.model_copy(update={"fingerprint": content_id("opsnap", payload)})
        )
    return tuple(frozen)


# --- the outcome side --------------------------------------------------------------------


def _outcome(
    snapshot: OpportunitySnapshot,
    base: RecommendationSnapshot,
    history: RecordedHistory,
    rules: OutcomeRules,
) -> OpportunityOutcome:
    labelled = label(base, history.future(), rules)
    status: OutcomeStatus
    reason: str | None = None
    uplift: float | None = None
    if snapshot.reconstruction is Reconstruction.UNBOUND:
        status, reason = OutcomeStatus.UNAVAILABLE, MissingReason.UNBOUND.value
    elif labelled.availability is OutcomeAvailability.AVAILABLE:
        uplift = labelled.realized_uplift_72h
        assert uplift is not None
        if uplift >= rules.hit_threshold:
            status = OutcomeStatus.POSITIVE
        elif uplift <= rules.downside_threshold:
            status = OutcomeStatus.NEGATIVE
        else:
            status = OutcomeStatus.NEITHER
    elif (
        labelled.unavailable_reason is OutcomeUnavailable.FUTURE_INCOMPLETE
        and labelled.primary_window_end > labelled.data_through
    ):
        status = OutcomeStatus.FORWARD_INCOMPLETE
        reason = f"the forward period ends {labelled.primary_window_end}; data ends " + str(
            labelled.data_through
        )
    else:
        status = OutcomeStatus.UNAVAILABLE
        reason = None if labelled.unavailable_reason is None else labelled.unavailable_reason.value
    fields: dict[str, object] = {
        "snapshot_fingerprint": snapshot.fingerprint,
        "canonical_game_id": snapshot.canonical_game_id,
        "outcome_rules_version": rules.version,
        # An unbound game was never held to its series, so no label stands for it.
        "label_id": None
        if snapshot.reconstruction is Reconstruction.UNBOUND
        else labelled.label_id,
        "prediction_at": snapshot.as_of,
        "outcome_window_start": labelled.primary_window_start,
        "outcome_window_end": labelled.primary_window_end,
        "data_through": labelled.data_through,
        "status": status,
        "reason": reason,
        "realized_uplift_72h": uplift,
    }
    draft = OpportunityOutcome.model_validate({**fields, "outcome_id": "opout_" + "0" * 32})
    payload = draft.model_dump(mode="json", exclude={"outcome_id"})
    return draft.model_copy(update={"outcome_id": content_id("opout", payload)})


# --- metrics -----------------------------------------------------------------------------


def share(count: int, of: int, empty: str) -> str:
    """A count over its denominator. Over nothing it is not estimable, never zero."""
    if of == 0:
        return empty
    return f"{count}/{of} ({count / of:.1%}); descriptive only, no interval"


def episodes(snapshots: Sequence[OpportunitySnapshot], cadence: timedelta) -> int:
    """Runs of one game on consecutive moments, `cadence` apart."""
    runs = 0
    previous: dict[str, datetime] = {}
    for snapshot in sorted(snapshots, key=lambda s: (s.canonical_game_id, s.as_of)):
        last = previous.get(snapshot.canonical_game_id)
        if last is None or snapshot.as_of - last != cadence:
            runs += 1
        previous[snapshot.canonical_game_id] = snapshot.as_of
    return runs


def _sensitivity(snapshots: Sequence[OpportunitySnapshot]) -> tuple[SensitivityRow, ...]:
    """EXPLORATORY. Each decision recomputed with only the movement threshold changed.

    Everything else a decision rested on is held as it was. The result is a count of
    changed decisions; no outcome is looked at, so nothing here can select a threshold.
    """
    rows = []
    for threshold in EXPLORATORY_ROBUST_Z:
        tally: Counter[Readiness] = Counter()
        changed = 0
        for snapshot in snapshots:
            counted = [m for m in snapshot.movements if m.family.value in snapshot.counted_families]
            up = tuple(
                m.family
                for m in counted
                if m.robust_z is not None
                and m.log_lift is not None
                and m.robust_z >= threshold
                and m.log_lift > 0
            )
            down = tuple(
                m.family
                for m in counted
                if m.robust_z is not None
                and m.log_lift is not None
                and m.robust_z <= -threshold
                and m.log_lift < 0
            )
            facts = snapshot.facts.model_copy(
                update={"moving_up": up, "moving_down": down, "disagreement": bool(up and down)}
            )
            level = readiness(facts)
            tally[level] += 1
            changed += level is not snapshot.readiness
        rows.append(
            SensitivityRow(
                robust_z=threshold,
                changed=changed,
                candidates=tally[Readiness.CANDIDATE],
                watchlist=tally[Readiness.WATCHLIST],
                insufficient=tally[Readiness.INSUFFICIENT],
            )
        )
    return tuple(rows)


def _metrics(
    moments: Sequence[MomentRecord],
    cadence: timedelta,
    universe: Sequence[str],
    snapshots: Sequence[OpportunitySnapshot],
    outcomes: Sequence[OpportunityOutcome],
) -> HistoryMetrics:
    by_fingerprint = {o.snapshot_fingerprint: o for o in outcomes}
    total = len(snapshots)
    tally = Counter(s.readiness for s in snapshots)
    groups = []
    for level in Readiness:
        group = [s for s in snapshots if s.readiness is level]
        members = [by_fingerprint[s.fingerprint] for s in group]
        statuses = Counter(o.status for o in members)
        available = sum(
            statuses[s]
            for s in (OutcomeStatus.POSITIVE, OutcomeStatus.NEGATIVE, OutcomeStatus.NEITHER)
        )
        groups.append(
            GroupMetrics(
                readiness=level,
                assessments=len(members),
                distinct_games=len({s.canonical_game_id for s in group}),
                episodes=episodes(group, cadence),
                outcomes=tuple(
                    Count(name=status.value, count=statuses[status], of=len(members))
                    for status in OutcomeStatus
                ),
                positive_share=share(
                    statuses[OutcomeStatus.POSITIVE],
                    available,
                    f"NOT ESTIMABLE — NO {level.value.upper()} WITH AN AVAILABLE OUTCOME",
                ),
            )
        )
    candidates = next(g for g in groups if g.readiness is Readiness.CANDIDATE)
    candidate_available = sum(
        c.count
        for c in candidates.outcomes
        if c.name in (OutcomeStatus.POSITIVE, OutcomeStatus.NEGATIVE, OutcomeStatus.NEITHER)
    )
    candidate_share = (
        NOT_ESTIMABLE_NO_CANDIDATES
        if candidates.assessments == 0
        else candidates.positive_share
        if candidate_available
        else "NOT ESTIMABLE — NO CANDIDATE OUTCOME IS AVAILABLE YET"
    )
    families = Counter(
        family
        for s in snapshots
        for family in FAMILIES
        if family not in {m.family for m in s.missing}
    )
    reasons = Counter((m.family, m.reason.value) for s in snapshots for m in s.missing)
    statuses = Counter(o.status for o in outcomes)
    unavailable_moments = sum(1 for m in moments if not m.reconstructed)
    return HistoryMetrics(
        moments_requested=len(moments),
        moments_reconstructed=len(moments) - unavailable_moments,
        moments_unavailable=unavailable_moments,
        games=len(universe),
        assessments=total,
        no_prediction=unavailable_moments * len(universe),
        candidates=tally[Readiness.CANDIDATE],
        watchlist=tally[Readiness.WATCHLIST],
        insufficient=tally[Readiness.INSUFFICIENT],
        abstention=share(
            tally[Readiness.INSUFFICIENT], total, "NOT ESTIMABLE — NO ASSESSMENT WAS MADE"
        ),
        family_availability=tuple(
            Count(name=family, count=families[family], of=total) for family in FAMILIES
        ),
        outcome_availability=tuple(
            Count(name=status.value, count=statuses[status], of=total) for status in OutcomeStatus
        ),
        missing_by_reason=tuple(
            Count(name=f"{family}: {reason}", count=count, of=total)
            for (family, reason), count in sorted(reasons.items())
        ),
        groups=tuple(groups),
        candidate_positive_share=candidate_share,
        sensitivity=_sensitivity(snapshots),
    )


# --- the walk ----------------------------------------------------------------------------


def material_content(recording: CollectionReport) -> tuple[object, ...]:
    """Everything in a recording that carries historical meaning, in a canonical order.

    Kept: the requested window; which sources ran; for every signal its source, game,
    signal key, collection status, error code and message; for every series its
    identity, granularity, window, latest due period and every point's identity,
    period, status, value and source reference. A recording that lacks a failed signal,
    or holds one twice, differs.

    Left out, on purpose: when the collection ran (`observed_at`, run start and finish,
    each point's `observed_at` and the freshness lag computed from it). A replay is run
    at a moment of its choosing; those stamps describe the replay, not the history.

    Raises HistoryRefused if any signal appears twice for the same source, game and
    key: a duplicate is never silently collapsed.
    """
    seen: set[tuple[str, ...]] = set()
    entries: list[tuple[object, ...]] = []
    for run in recording.runs:
        for signal in run.outcomes:
            identity = (
                run.source,
                signal.canonical_game_id,
                signal.key.source,
                signal.key.metric,
                signal.key.granularity.value,
            )
            if identity in seen:
                raise HistoryRefused(
                    f"the recording holds more than one entry for {'/'.join(identity)}"
                )
            seen.add(identity)
            series = signal.series
            entries.append(
                (
                    *identity,
                    signal.status.value,
                    None if signal.error_code is None else signal.error_code.value,
                    signal.error_message,
                    None
                    if series is None
                    else (
                        series.canonical_game_id,
                        series.source,
                        series.metric,
                        series.granularity.value,
                        series.window_start.isoformat(),
                        series.window_end.isoformat(),
                        series.latest_due.isoformat(),
                        tuple(
                            (
                                point.canonical_game_id,
                                point.source,
                                point.metric,
                                point.granularity.value,
                                point.period_start.isoformat(),
                                point.status.value,
                                point.value,
                                point.reference,
                                None
                                if point.source_timestamp is None
                                else point.source_timestamp.isoformat(),
                            )
                            for point in series.points
                        ),
                    ),
                )
            )
    return (
        recording.window.start.isoformat(),
        recording.window.end.isoformat(),
        tuple(sorted(run.source for run in recording.runs)),
        tuple(sorted(entries, key=repr)),
    )


def _check_origin(
    catalogue: Catalogue,
    recording: CollectionReport,
    demand: InputProvenance,
    context: Iterable[ContextObservation],
) -> None:
    """Refuse any claim of a verified origin that the evaluator cannot back.

    A verified standing is a claim about where data came from, and a provenance record
    is only a claim. Three checks, all failing closed:

    1. The existing honesty guard: a verified origin must carry digests, and every one
       must be pinned (`require_honest`).
    2. A verified context observation is refused outright. No recording of any context
       family is pinned, and nothing here could tie an in-memory observation to one.
    3. Verified demand must name exactly one recording directory whose content still
       has the claimed pinned digest, and the supplied recording's material content
       (`material_content`: identities, statuses, failures, windows, due periods and
       values) must equal what replaying that directory gives. A matching digest alone proves only
       that the directory is the pinned one; the replay comparison is what ties the
       separately supplied, in-memory data to it.

    None of this is cryptographic authentication of the source. A pinned digest says
    the bytes are the ones this repository reviewed as a recording of the source;
    the review, not the hash, is what vouches for them.
    """
    for provenance in (demand, *(o.provenance for o in context)):
        try:
            require_honest(provenance)
        except ValueError as error:
            raise HistoryRefused(str(error)) from None
    for observation in context:
        if observation.provenance.standing is Standing.VERIFIED:
            raise HistoryRefused(
                f"{observation.family.value} ({observation.reference}): no recording of this "
                "family is pinned, so a verified origin cannot be checked; it is refused"
            )
    if demand.standing is not Standing.VERIFIED:
        return
    directories = [part.strip() for part in demand.reference.split(",")]
    if len(directories) != 1 or len(demand.digests) != 1:
        raise HistoryRefused(
            f"{demand.reference}: verified demand must name exactly one pinned recording"
        )
    directory = Path(directories[0])
    if not directory.is_dir() or recording_digest(directory) != demand.digests[0]:
        raise HistoryRefused(
            f"{demand.reference}: the directory no longer has the claimed pinned content"
        )
    # A recorder-made recording must hold together and be complete. Its acquisition
    # times are NOT compared with the simulated moments: a historical evaluation is a
    # retrospective reconstruction of source periods under the publication lag, not a
    # claim that a response was retrieved at each moment (U6.7.1). A point-in-time
    # reading (assess_evidence) does enforce them.
    if is_recorder_output(directory):
        check = check_recording(directory)
        if check.problems or not check.review_ready:
            raise HistoryRefused(
                f"{demand.reference}: a recording that is not intact and complete is never "
                "verified demand evidence"
            )
    elif demand.digests[0] not in LEGACY_PAGEVIEW_RECORDINGS:
        raise HistoryRefused(
            f"{demand.reference}: a pinned recording made after the legacy fixtures must "
            "carry its acquisition records"
        )
    with replay_client(directory) as client:
        replayed = collect(
            catalogue,
            default_registry(client),
            recording.window,
            observed_at=recording.observed_at,
            clock=lambda: recording.observed_at,
        )
    if material_content(replayed) != material_content(recording):
        raise HistoryRefused(
            f"{demand.reference}: the supplied pageviews are not what the pinned recording "
            "holds, so they cannot be counted as verified"
        )


def _check_policy(
    mode: EvaluationMode,
    stage: DeploymentStage,
    demand: InputProvenance,
    context: Iterable[ContextObservation],
) -> None:
    if stage is not DeploymentStage.PRIVATE_DEVELOPMENT:
        raise HistoryRefused(
            f"a historical opportunity evaluation is private development only, not {stage.value}"
        )
    refusal = WIKIMEDIA_PAGEVIEWS_POLICY.registration_refusal(SourcePurpose.DEMAND_SIGNALS, stage)
    if refusal is not None:
        raise HistoryRefused(f"{WIKIMEDIA_PAGEVIEWS_POLICY.source}: {refusal}")
    synthetic = mode is EvaluationMode.SYNTHETIC
    origins = [demand, *(o.provenance for o in context)]
    if synthetic and any(p.standing is not Standing.SYNTHETIC for p in origins):
        raise HistoryRefused("synthetic mode takes invented inputs only; nothing real is mixed in")
    if not synthetic and any(p.standing is Standing.SYNTHETIC for p in origins):
        raise HistoryRefused("recorded mode takes no synthetic input; use synthetic mode")
    for observation in context:
        if observation.provenance.standing is Standing.SYNTHETIC:
            continue
        policy, purpose = _CONTEXT_POLICY[observation.family]
        refusal = (
            collection_refusal(policy, stage)
            if observation.family is ContextFamily.COMMUNITY
            else policy.registration_refusal(purpose, stage)
        )
        if refusal is not None:
            raise HistoryRefused(f"{policy.source}: {refusal}")


def evaluate_history(
    catalogue: Catalogue,
    recording: CollectionReport,
    publication_lag: Mapping[str, timedelta],
    first_day: date,
    last_day: date,
    cadence_days: int,
    universe: Sequence[str],
    demand_origin: InputProvenance,
    mode: EvaluationMode,
    created_at: datetime,
    context: Sequence[ContextObservation] = (),
    bound_from: Mapping[str, datetime] | None = None,
    stage: DeploymentStage = DeploymentStage.PRIVATE_DEVELOPMENT,
    outcome_rules: OutcomeRules = OUTCOME_RULES_V1,
) -> OpportunityHistoryReport:
    """Walk forward over the declared moments, freeze every snapshot, then label them.

    Deterministic and offline. `universe` is a bounded list of slugs. `bound_from` gives,
    per canonical id, the moment a game's identity binding began; before it the game has
    no demand reading. Absent, every catalogue binding is taken as given, the assumption
    the validation protocol's backtests make. `created_at` reaches only the underlying
    recommendation snapshots, whose identity excludes it.
    """
    if cadence_days < 1:
        raise ValueError("the cadence is a whole number of days, at least one")
    slugs = sorted(set(universe))
    if not slugs or len(slugs) != len(universe):
        raise ValueError("the universe is a non-empty list of distinct slugs")
    if len(slugs) > MAX_UNIVERSE:
        raise ValueError(f"the universe is bounded: at most {MAX_UNIVERSE} games")
    games = {game.slug: game for game in catalogue.games}
    unknown = [slug for slug in slugs if slug not in games]
    if unknown:
        raise ValueError(f"no game with slug: {', '.join(unknown)}")
    chosen = [games[slug] for slug in slugs]
    ids = {game.canonical_game_id for game in chosen}
    if any(o.canonical_game_id not in ids for o in context):
        raise ValueError("a context observation names a game outside the universe")
    if DEMAND_SOURCE not in publication_lag:
        raise ValueError(f"no publication lag is known for {DEMAND_SOURCE}")
    _check_policy(mode, stage, demand_origin, context)
    _check_origin(catalogue, recording, demand_origin, context)
    counting = (
        Counting.VERIFIED_ONLY
        if mode is EvaluationMode.RECORDED
        else Counting.SYNTHETIC_AS_IF_VERIFIED
    )
    bindings = dict(bound_from or {})
    history = RecordedHistory(recording, publication_lag)
    lag = publication_lag[DEMAND_SOURCE]
    by_game: dict[str, list[ContextObservation]] = {}
    for observation in sorted(
        context, key=lambda o: (o.canonical_game_id, o.family.value, o.retrieved_at, o.reference)
    ):
        by_game.setdefault(observation.canonical_game_id, []).append(observation)

    # --- prediction side: frozen before anything after a moment is read -----------------
    moments: list[MomentRecord] = []
    snapshots: list[OpportunitySnapshot] = []
    bases: dict[str, RecommendationSnapshot] = {}
    for day in days_between(first_day, last_day)[::cadence_days]:
        moment = prediction_moment(day)
        if not history.can_reconstruct(moment):
            moments.append(
                MomentRecord(
                    as_of=moment,
                    reconstructed=False,
                    reason=f"the recording ({history.first_day} to {history.last_day}) does "
                    "not hold everything visible at this moment under the publication lag",
                    assessed=0,
                )
            )
            continue
        inputs = history.inputs_at(moment)
        base = {s.canonical_game_id: s for s in predict(catalogue, inputs, created_at)}
        signals = {
            signal.canonical_game_id: signal
            for run in inputs.report.runs
            if run.source == DEMAND_SOURCE
            for signal in run.outcomes
        }
        drafts = [
            _assess(
                game.canonical_game_id,
                game.slug,
                game.name,
                moment,
                signals.get(game.canonical_game_id),
                bindings.get(game.canonical_game_id, moment) <= moment,
                demand_origin,
                by_game.get(game.canonical_game_id, ()),
                counting,
                base[game.canonical_game_id],
            )
            for game in chosen
        ]
        frozen = _freeze(
            drafts,
            moment,
            inputs.evaluation_day,
            latest_due_at(moment, V0_INPUT_SIGNAL.granularity, lag).date(),
            mode,
            counting,
        )
        for snapshot in frozen:
            bases[snapshot.fingerprint] = base[snapshot.canonical_game_id]
        snapshots.extend(frozen)
        moments.append(MomentRecord(as_of=moment, reconstructed=True, assessed=len(frozen)))

    # --- outcome side: only now is the future read --------------------------------------
    outcomes = [_outcome(s, bases[s.fingerprint], history, outcome_rules) for s in snapshots]

    metrics = _metrics(moments, timedelta(days=cadence_days), slugs, snapshots, outcomes)
    statements = [ATTENTION_PROXY, CREATOR_SUCCESS_UNVALIDATED]
    if mode is EvaluationMode.SYNTHETIC:
        statements.insert(
            0,
            "SYNTHETIC: every input in this report was invented to exercise the method. "
            "Nothing here is evidence about any game.",
        )
    if metrics.candidates == 0:
        statements.append(
            f"Candidate outcome rate: {NOT_ESTIMABLE_NO_CANDIDATES}. No moment had the "
            "evidence a candidate needs."
        )
    watch = next(g for g in metrics.groups if g.readiness is Readiness.WATCHLIST)
    if watch.assessments:
        statements.append(
            f"The {watch.assessments} watchlist assessment(s) are not independent: they "
            f"form {watch.episodes} episode(s) over {watch.distinct_games} game(s), and "
            "consecutive moments' forward windows overlap. outcome_rules_v1 compares the "
            "next three days with the same 28-day baseline the movement already exceeded, "
            "so a rise under way that merely persists counts as positive. This measures "
            "whether attention stayed up, not whether it was anticipated."
        )
    statements += [
        "Readiness counts "
        + (
            "verified evidence only."
            if counting is Counting.VERIFIED_ONLY
            else "invented evidence as if it were verified, to show the rubric working."
        ),
        f"Thresholds as written and unvalidated: robust z {NOTEWORTHY_ROBUST_Z}, discussion "
        f"within {TIMELY_DISCUSSION.days} days. Nothing was fitted.",
        "No interval is reported: the observations are too few for one to mean anything.",
    ]
    assumptions = (
        f"Moments are 00:00 UTC at the start of each declared day, every {cadence_days} day(s).",
        f"{DEMAND_SOURCE} publishes a day {lag.total_seconds() / 3600:g} hours after it ends "
        "(its policy's lag); later days are withheld from the moment.",
        "A recording made after the fact cannot show a value the source later revised.",
        "Identity bindings are the catalogue's as given unless a start is declared, the "
        "assumption the validation protocol's backtests make.",
        "No Steam player samples, YouTube searches, Reddit posts or event observations were "
        "recorded at these moments; those families are missing, never filled.",
        "Outcomes reuse each moment's recommendation_snapshot_v2 reference levels and "
        "outcome_rules_v1 unchanged.",
    )
    fields: dict[str, object] = {
        "outcome_rules_version": outcome_rules.version,
        "mode": mode,
        "counting": counting,
        "first_day": first_day,
        "last_day": last_day,
        "cadence_days": cadence_days,
        "universe": tuple(slugs),
        "publication_lag_hours": lag.total_seconds() / 3600,
        "noteworthy_robust_z": NOTEWORTHY_ROBUST_Z,
        "timely_discussion_days": TIMELY_DISCUSSION.total_seconds() / 86400,
        "demand_origin": _origin(DEMAND_FAMILY.value, demand_origin),
        "moments": tuple(moments),
        "snapshots": tuple(snapshots),
        "outcomes": tuple(outcomes),
        "metrics": metrics,
        "statements": tuple(statements),
        "assumptions": assumptions,
    }
    draft = OpportunityHistoryReport.model_validate({**fields, "fingerprint": "ophist_" + "0" * 32})
    payload = draft.model_dump(mode="json", exclude={"fingerprint"})
    return draft.model_copy(update={"fingerprint": content_id("ophist", payload)})


# --- rendering ---------------------------------------------------------------------------


def render_history(report: OpportunityHistoryReport) -> str:
    m = report.metrics
    lines = [
        "CREATOR-OPPORTUNITY HISTORICAL EVALUATION (private development only)",
        f"  {report.rules_version}, outcomes by {report.outcome_rules_version} as an "
        f"ATTENTION PROXY, under {report.protocol_version}",
        f"  mode: {report.mode.value.upper()}; readiness counts: {report.counting.value}",
        f"  moments: {report.first_day} to {report.last_day}, every {report.cadence_days} "
        f"day(s), 00:00 UTC",
        f"  universe ({len(report.universe)}): {', '.join(report.universe)}",
        f"  demand: {report.demand_origin.origin} [{report.demand_origin.standing.value}] "
        f"{report.demand_origin.reference}",
        "",
    ]
    lines += [f"  {s}" for s in report.statements]
    lines += [
        "",
        "MOMENTS",
        f"  requested {m.moments_requested}, reconstructed {m.moments_reconstructed}, "
        f"unavailable {m.moments_unavailable} (no prediction for {m.no_prediction} "
        "game-moments)",
        "ASSESSMENTS",
        f"  {m.assessments} over {m.games} game(s): candidate {m.candidates}, watchlist "
        f"{m.watchlist}, insufficient {m.insufficient}",
        f"  abstention (insufficient): {m.abstention}",
        "FAMILY AVAILABILITY (assessments with the family present)",
    ]
    lines += [f"  {c.name:<26} {c.count}/{c.of}" for c in m.family_availability]
    lines += ["MISSING EVIDENCE BY REASON"]
    lines += [f"  {c.name:<70} {c.count}" for c in m.missing_by_reason]
    lines += ["OUTCOMES (ATTENTION PROXY; attached after every snapshot was frozen)"]
    lines += [f"  {c.name:<32} {c.count}/{c.of}" for c in m.outcome_availability]
    lines += ["BY READINESS"]
    for group in m.groups:
        detail = ", ".join(f"{c.name} {c.count}" for c in group.outcomes if c.count)
        lines.append(
            f"  {group.readiness.value:<30} {group.assessments:>4} "
            f"({group.episodes} episode(s), {group.distinct_games} game(s))  {detail or '-'}"
        )
        lines.append(f"    positive attention share: {group.positive_share}")
    lines += [
        f"  candidate positive share: {m.candidate_positive_share}",
        "",
        "EXPLORATORY SENSITIVITY (not a result; no outcome attached)",
        f"  {m.sensitivity_label}",
        f"  as written: robust z {NOTEWORTHY_ROBUST_Z}: candidate {m.candidates}, watchlist "
        f"{m.watchlist}, insufficient {m.insufficient}",
    ]
    lines += [
        f"  if robust z {row.robust_z}: {row.changed} decision(s) would change; candidate "
        f"{row.candidates}, watchlist {row.watchlist}, insufficient {row.insufficient}"
        for row in m.sensitivity
    ]
    lines += [
        "",
        "ASSUMPTIONS",
    ]
    lines += [f"  - {a}" for a in report.assumptions]
    lines += [f"Report fingerprint: {report.fingerprint}", ""]
    return "\n".join(lines)
