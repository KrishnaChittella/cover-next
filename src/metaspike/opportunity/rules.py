"""`creator_opportunity_rules_v1`: a transparent rubric over one game's evidence.

This turns a game evidence reading (U6.0 to U6.2) into an opportunity ASSESSMENT. It
weighs nothing numerically and computes no score. It asks a fixed set of yes-or-no
questions of evidence that is already there, says which were answered by real
observations, and puts games in an order that it explains.

WHAT COUNTS. Only VERIFIED evidence counts toward a real assessment: a recording whose
content matches a pinned digest (U6.0.1). Synthetic and unverified evidence is shown,
labelled, and kept out. Beside every real assessment is a SIMULATED one that counts
synthetic and unverified evidence as if it were real, so the rubric can be seen working
on invented data. A simulated readiness is never a recommendation.

THE QUESTIONS, each answered from counted evidence only:

    moving up       a demand family is available and current, and moved up against its
                    own history: robust z >= 3.5 and a positive log lift. (3.5 is score
                    v0's own outlier threshold, reused, and UNVALIDATED for this use.)
    moving down     the same, downward.
    disagreement    one family up and another down.
    timely event    event intelligence is available, and an event is upcoming within
                    the radar's horizon or active now.
    angle           a specific video angle exists: a community candidate angle (two or
                    more supported posts sharing a theme and something specific), or a
                    timely event.
    coverage known  a creator-supply sample ran to its end (observed or no results).
                    Even then total competition is unknown: a sample is a lower bound.

READINESS:

    evidence_supported_candidate   moving up, AND a confirmed angle, AND coverage known,
                                   AND no disagreement.
    provisional_watchlist          moving up OR a timely event, but not all of the above.
    insufficient_evidence          neither: nothing counted says "now".

A watchlist entry is a reason to look, not a recommendation. A single source moving
never claims low competition. Missing creator supply is never low competition.

ORDER, lexicographic, each step explained, no numbers mixed across sources:
readiness; number of families moving up; no disagreement before disagreement; a timely
event; a confirmed angle; coverage known; how many sources answered at all; and then
the canonical id, which carries no meaning and says so.
"""

from collections.abc import Callable, Sequence
from datetime import timedelta
from enum import StrEnum
from typing import Final, Literal

from pydantic import AwareDatetime, Field

from metaspike.community_sources.reading import CommunityInterestReading
from metaspike.creator_sources.reading import CreatorSupplyReading, SupplyState
from metaspike.domain.families import SourceFamily
from metaspike.domain.model import FrozenModel
from metaspike.events.heat import Availability as EventAvailability
from metaspike.events.heat import Phase
from metaspike.evidence.model import (
    EvidenceKind,
    EvidenceReport,
    EvidenceState,
    GameEvidence,
    Standing,
)
from metaspike.features.semantics import Availability
from metaspike.scoring.v0 import ROBUST_Z_OUTLIER_THRESHOLD

OPPORTUNITY_RULES_VERSION: Final = "creator_opportunity_rules_v1"
# Borrowed from score v0, where it is the conventional outlier threshold. Here it is
# UNVALIDATED: no historical evaluation has shown it marks useful opportunities.
NOTEWORTHY_ROBUST_Z: Final = ROBUST_Z_OUTLIER_THRESHOLD
# A community angle is timely if a supporting post was created within this. UNVALIDATED.
TIMELY_DISCUSSION: Final = timedelta(days=7)
_EVENT_PHASES = frozenset({Phase.UPCOMING, Phase.ACTIVE})


class Readiness(StrEnum):
    CANDIDATE = "evidence_supported_candidate"
    WATCHLIST = "provisional_watchlist"
    INSUFFICIENT = "insufficient_evidence"


_RANK = {Readiness.CANDIDATE: 0, Readiness.WATCHLIST: 1, Readiness.INSUFFICIENT: 2}


class Direction(StrEnum):
    UP = "up"
    DOWN = "down"
    FLAT = "flat"  # measured, and not noteworthy either way
    UNKNOWN = "unknown"  # no measurement to read


class SourceNote(FrozenModel):
    """One source's standing for the game, as the evidence reading gave it."""

    source: str
    kind: EvidenceKind
    state: EvidenceState
    standing: Standing
    latest_observed_at: AwareDatetime | None = None
    freshness: str | None = None


class MovementNote(FrozenModel):
    family: SourceFamily
    source: str
    standing: Standing
    direction: Direction
    log_lift: float | None = None
    robust_z: float | None = None
    observed_through: AwareDatetime | None = None


class EventNote(FrozenModel):
    """An event bearing on timing. Its title, written by its publisher, is not copied."""

    event_id: str
    event_type: str
    starts_at: AwareDatetime
    time_precision: str
    phase: str
    standing: Standing


class VideoAngle(FrozenModel):
    """A specific thing a video could address, with what supports it."""

    origin: Literal["community", "event"]
    addresses: str
    about: tuple[str, ...] = ()
    references: tuple[str, ...]
    timely: bool
    timely_reason: str
    standing: Standing
    # True only when what supports it is a verified observation.
    confirmed: bool
    assumptions: tuple[str, ...]


class Statement(FrozenModel):
    text: str
    standing: Standing
    references: tuple[str, ...] = ()


class Facts(FrozenModel):
    """The rubric's answers, from the evidence it was allowed to count."""

    moving_up: tuple[SourceFamily, ...] = ()
    moving_down: tuple[SourceFamily, ...] = ()
    disagreement: bool = False
    timely_event: bool = False
    angle: bool = False
    coverage_known: bool = False
    sources_answering: int = Field(default=0, ge=0)


class OpportunityAssessment(FrozenModel):
    rules_version: Literal["creator_opportunity_rules_v1"] = OPPORTUNITY_RULES_VERSION
    canonical_game_id: str
    slug: str
    name: str
    as_of: AwareDatetime
    # From verified evidence only. The only readiness that can ever be acted on.
    readiness: Readiness
    facts: Facts
    # The same rubric with synthetic and unverified evidence counted as if real.
    # Shows the rubric working on invented data. Never a recommendation.
    simulated_readiness: Readiness
    simulated_facts: Facts
    sources: tuple[SourceNote, ...]
    movements: tuple[MovementNote, ...] = ()
    events: tuple[EventNote, ...] = ()
    angles: tuple[VideoAngle, ...] = ()
    angle_status: Literal["established", "not_yet_established"]
    creator_coverage: str
    community: str
    # Backed by counted evidence only. Empty means there is no real "why now".
    why_now: tuple[Statement, ...] = ()
    # Signals that point somewhere but are synthetic or unverified, labelled as such.
    unconfirmed: tuple[Statement, ...] = ()
    caveats: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    uncertainty: str


def _counts(standing: Standing) -> bool:
    return standing is Standing.VERIFIED


def _simulated(standing: Standing) -> bool:
    return standing is not Standing.NONE


def movement_direction(lift: float | None, z: float | None) -> Direction:
    """Which way one demand signal moved against its own history, by the rubric's rule."""
    if lift is None or z is None:
        return Direction.UNKNOWN
    if z >= NOTEWORTHY_ROBUST_Z and lift > 0:
        return Direction.UP
    if z <= -NOTEWORTHY_ROBUST_Z and lift < 0:
        return Direction.DOWN
    return Direction.FLAT


def _movements(game: GameEvidence) -> tuple[MovementNote, ...]:
    notes = []
    for reading in game.demand.families:
        if reading.availability is not Availability.APPLICABLE_AND_AVAILABLE or not reading.signals:
            continue
        source = next(s for s in game.sources if s.family is reading.family)
        signal = reading.signals[0].signal
        lift, z = signal.log_lift.value, signal.robust_z.value
        direction = movement_direction(lift, z)
        notes.append(
            MovementNote(
                family=reading.family,
                source=source.source,
                standing=source.provenance.standing,
                direction=direction,
                log_lift=lift,
                robust_z=z,
                observed_through=source.latest_observed_at,
            )
        )
    return tuple(notes)


def _events(game: GameEvidence) -> tuple[EventNote, ...]:
    if game.events.availability is not EventAvailability.AVAILABLE:
        return ()
    standing = game.source("steam_news").provenance.standing
    return tuple(
        EventNote(
            event_id=c.evaluation.event_id,
            event_type=c.evaluation.event_type.value,
            starts_at=c.evaluation.starts_at,
            time_precision=c.evaluation.time_precision.value,
            phase=c.evaluation.phase.value,
            standing=standing,
        )
        for c in game.events.contributions
        if c.evaluation.phase in _EVENT_PHASES
    )


def _source(game: GameEvidence, kind: EvidenceKind) -> Standing:
    found = [s for s in game.sources if s.kind is kind and s.policy is not None]
    return found[0].provenance.standing if found else Standing.NONE


def _angles(game: GameEvidence, events: Sequence[EventNote]) -> tuple[VideoAngle, ...]:
    angles: list[VideoAngle] = []
    community: CommunityInterestReading | None = game.community_interest
    standing = _source(game, EvidenceKind.COMMUNITY_INTEREST)
    if community is not None:
        for angle in community.angles:
            newest = max(p.created_at for p in angle.supporting_posts)
            timely = game.as_of - newest <= TIMELY_DISCUSSION
            angles.append(
                VideoAngle(
                    origin="community",
                    addresses=" / ".join(angle.kinds),
                    about=angle.about,
                    references=tuple(p.post_id for p in angle.supporting_posts),
                    timely=timely,
                    timely_reason=(
                        f"newest supporting post {newest:%Y-%m-%d %H:%MZ}, "
                        + ("within" if timely else "older than")
                        + " 7 days"
                    ),
                    standing=standing,
                    confirmed=_counts(standing),
                    assumptions=(
                        "that posts in the sampled communities reflect wider interest",
                        "that a video on it would be watched; nothing here measures that",
                        "that existing videos do not already cover it; total competition "
                        "is unknown",
                    ),
                )
            )
    for event in events:
        angles.append(
            VideoAngle(
                origin="event",
                addresses=f"the {event.event_type.replace('_', ' ')} ({event.phase})",
                about=(f"starts {event.starts_at:%Y-%m-%d} ({event.time_precision} precision)",),
                references=(event.event_id,),
                timely=True,
                timely_reason=f"{event.phase} at the moment assessed",
                standing=event.standing,
                confirmed=_counts(event.standing),
                assumptions=(
                    "that the event happens as announced",
                    "that viewers look for coverage of it",
                ),
            )
        )
    return tuple(angles)


def _facts(
    game: GameEvidence,
    movements: Sequence[MovementNote],
    events: Sequence[EventNote],
    angles: Sequence[VideoAngle],
    counted: Callable[[Standing], bool],
) -> Facts:
    up = tuple(m.family for m in movements if m.direction is Direction.UP and counted(m.standing))
    down = tuple(
        m.family for m in movements if m.direction is Direction.DOWN and counted(m.standing)
    )
    supply: CreatorSupplyReading | None = game.creator_supply
    coverage = (
        supply is not None
        and counted(_source(game, EvidenceKind.CREATOR_SUPPLY))
        and supply.state in (SupplyState.OBSERVED, SupplyState.NO_RESULTS)
        and supply.sample_complete
    )
    return Facts(
        moving_up=up,
        moving_down=down,
        disagreement=bool(up and down),
        timely_event=any(counted(e.standing) for e in events),
        angle=any(counted(a.standing) and a.timely for a in angles),
        coverage_known=coverage,
        sources_answering=sum(
            1
            for s in game.sources
            if counted(s.provenance.standing)
            and s.state in (EvidenceState.AVAILABLE, EvidenceState.INCOMPLETE)
        ),
    )


def readiness(facts: Facts) -> Readiness:
    if facts.moving_up and facts.angle and facts.coverage_known and not facts.disagreement:
        return Readiness.CANDIDATE
    if facts.moving_up or facts.timely_event:
        return Readiness.WATCHLIST
    return Readiness.INSUFFICIENT


def _statements(
    movements: Sequence[MovementNote],
    events: Sequence[EventNote],
    angles: Sequence[VideoAngle],
) -> list[Statement]:
    found: list[Statement] = []
    for m in movements:
        if m.direction is Direction.UP:
            found.append(
                Statement(
                    text=(
                        f"{m.family.value} is up against its own history (log lift "
                        f"{m.log_lift:.3g}, robust z {m.robust_z:.3g})"
                    ),
                    standing=m.standing,
                    references=(m.source,),
                )
            )
    for e in events:
        found.append(
            Statement(
                text=f"a {e.event_type.replace('_', ' ')} is {e.phase}, starting "
                f"{e.starts_at:%Y-%m-%d} ({e.time_precision} precision)",
                standing=e.standing,
                references=(e.event_id,),
            )
        )
    for a in angles:
        if a.origin == "community" and a.timely:
            found.append(
                Statement(
                    text=f"{len(a.references)} posts discuss {', '.join(a.about)} ({a.addresses})",
                    standing=a.standing,
                    references=a.references,
                )
            )
    return found


def _coverage_text(game: GameEvidence) -> str:
    supply = game.creator_supply
    if supply is None:
        return "No creator-supply input: how many videos cover this game is unknown."
    standing = _source(game, EvidenceKind.CREATOR_SUPPLY).value.upper()
    return f"[{standing}] {supply.summary}"


def _community_text(game: GameEvidence) -> str:
    community = game.community_interest
    if community is None:
        return "No community input: what players discuss about this game is unknown."
    standing = _source(game, EvidenceKind.COMMUNITY_INTEREST).value.upper()
    return f"[{standing}] {community.summary}"


def assess_opportunity(game: GameEvidence) -> OpportunityAssessment:
    """The rubric applied to one game's evidence. Reads; adds no data of its own."""
    movements = _movements(game)
    events = _events(game)
    angles = _angles(game, events)
    facts = _facts(game, movements, events, angles, _counts)
    simulated = _facts(game, movements, events, angles, _simulated)
    statements = _statements(movements, events, angles)
    caveats: list[str] = []
    for m in movements:
        if m.direction is Direction.DOWN:
            caveats.append(f"{m.family.value} is DOWN against its own history ({m.standing.value})")
    if facts.disagreement or simulated.disagreement:
        caveats.append("demand families disagree: one is up while another is down")
    for s in game.sources:
        if s.state is EvidenceState.STALE:
            caveats.append(f"{s.source} is stale: its latest observation is not current")
        if s.state is EvidenceState.INCOMPLETE:
            caveats.append(f"{s.source}'s sample is incomplete")
    if game.creator_supply is not None:
        caveats.append("total creator competition is unknown: a sample is a lower bound")
    if game.synthetic_sources:
        caveats.append(f"SYNTHETIC, counted for nothing: {', '.join(game.synthetic_sources)}")
    if game.unverified_sources:
        caveats.append(f"UNVERIFIED, counted for nothing: {', '.join(game.unverified_sources)}")
    uncertainty = (
        f"{facts.sources_answering} verified source(s) answered for this game; "
        f"{len(game.synthetic_sources)} synthetic and {len(game.unverified_sources)} "
        "unverified source(s) are shown and not counted. No thresholds in this rubric have "
        "been validated against outcomes, so no readiness here is a calibrated likelihood."
    )
    return OpportunityAssessment(
        canonical_game_id=game.canonical_game_id,
        slug=game.identity.slug,
        name=game.identity.name,
        as_of=game.as_of,
        readiness=readiness(facts),
        facts=facts,
        simulated_readiness=readiness(simulated),
        simulated_facts=simulated,
        sources=tuple(
            SourceNote(
                source=s.source,
                kind=s.kind,
                state=s.state,
                standing=s.provenance.standing,
                latest_observed_at=s.latest_observed_at,
                freshness=s.freshness,
            )
            for s in game.sources
        ),
        movements=movements,
        events=events,
        angles=angles,
        angle_status="established" if facts.angle else "not_yet_established",
        creator_coverage=_coverage_text(game),
        community=_community_text(game),
        why_now=tuple(s for s in statements if _counts(s.standing)),
        unconfirmed=tuple(s for s in statements if not _counts(s.standing)),
        caveats=tuple(dict.fromkeys(caveats)),
        missing=tuple(gap.text for gap in game.gaps),
        uncertainty=uncertainty,
    )


# --- ordering -------------------------------------------------------------------------------

_STEPS: Final = (
    ("readiness", "readier on the rubric"),
    ("moving_up", "more demand families moving up"),
    ("disagreement", "no disagreement between demand families"),
    ("timely_event", "a timely event"),
    ("angle", "a specific, timely video angle"),
    ("coverage_known", "a complete creator-coverage sample"),
    ("sources", "more sources answered"),
)


def ahead_of_next(mine: tuple[object, ...], theirs: tuple[object, ...]) -> str:
    """Why an entry with ordering key `mine` is above one with `theirs`, in words."""
    step = next(i for i in range(len(mine)) if mine[i] != theirs[i])
    if step < len(_STEPS):
        return _STEPS[step][1]
    return "indistinguishable on the evidence; ordered by canonical id only, which means nothing"


def ordering_key(readiness_: Readiness, facts: Facts, canonical_game_id: str) -> tuple[object, ...]:
    return (
        _RANK[readiness_],
        -len(facts.moving_up),
        int(facts.disagreement),
        -int(facts.timely_event),
        -int(facts.angle),
        -int(facts.coverage_known),
        -facts.sources_answering,
        canonical_game_id,
    )


class ShortlistEntry(FrozenModel):
    position: int = Field(ge=1)
    canonical_game_id: str
    slug: str
    readiness: Readiness
    # Why this entry is above the next one, in words.
    ahead_of_next: str


class OpportunityShortlist(FrozenModel):
    rules_version: Literal["creator_opportunity_rules_v1"] = OPPORTUNITY_RULES_VERSION
    as_of: AwareDatetime
    distribution: Literal["private_development_only"] = "private_development_only"
    contains_synthetic: bool
    contains_unverified: bool
    # Real: from verified evidence only.
    entries: tuple[ShortlistEntry, ...]
    # The same rubric counting synthetic and unverified evidence. NEVER a recommendation.
    simulated_entries: tuple[ShortlistEntry, ...]
    assessments: tuple[OpportunityAssessment, ...]
    summary: str


def _order(
    assessments: Sequence[OpportunityAssessment], simulated: bool
) -> tuple[ShortlistEntry, ...]:
    def key(a: OpportunityAssessment) -> tuple[object, ...]:
        if simulated:
            return ordering_key(a.simulated_readiness, a.simulated_facts, a.canonical_game_id)
        return ordering_key(a.readiness, a.facts, a.canonical_game_id)

    ordered = sorted(assessments, key=key)
    entries = []
    for index, item in enumerate(ordered):
        if index + 1 == len(ordered):
            reason = "last in the list"
        else:
            reason = ahead_of_next(key(item), key(ordered[index + 1]))
        entries.append(
            ShortlistEntry(
                position=index + 1,
                canonical_game_id=item.canonical_game_id,
                slug=item.slug,
                readiness=item.simulated_readiness if simulated else item.readiness,
                ahead_of_next=reason,
            )
        )
    return tuple(entries)


def shortlist(report: EvidenceReport) -> OpportunityShortlist:
    """Every game's assessment, in an explained order. Deterministic; private."""
    assessments = tuple(assess_opportunity(game) for game in report.games)
    entries = _order(assessments, simulated=False)
    real = [e for e in entries if e.readiness is not Readiness.INSUFFICIENT]
    summary = (
        f"{len(real)} game(s) reach a real readiness above insufficient on verified evidence."
        if real
        else "No real recommendation can be justified: no game has verified evidence that "
        "says 'now'."
    )
    return OpportunityShortlist(
        as_of=report.as_of,
        contains_synthetic=report.contains_synthetic,
        contains_unverified=report.contains_unverified,
        entries=entries,
        simulated_entries=_order(assessments, simulated=True),
        assessments=assessments,
        summary=summary,
    )
