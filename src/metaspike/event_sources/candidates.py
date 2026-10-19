"""From an announcement to an event candidate, and the gate before it becomes an event.

An announcement is evidence. It is not an event. A `SourceItem` becomes an
`EventCandidate`, which says what the item might be and why, and whether that is
established well enough to be handed to event intelligence. Two things must BOTH be
established for promotion:

    A. the type of event     by exactly one rule, with nothing in the title against it
    B. when it takes place   by an explicit, unambiguous date in the title, or by the
                             post's own publication time under a rule that allows that

If either is missing the candidate stays a candidate and says why. A publication
timestamp alone is never an event time: it becomes one only under a rule whose evidence
says the thing announced is happening as the post goes out.

`event_classification_rules_v1` is deterministic and deliberately narrow. It reads a
title's words and a post's structured tags, nothing else: no body, no model, no
similarity. A missed event costs nothing that cannot be recovered later; an invented
one would put a false cause into Event Heat. So wherever a title could be read two
ways, the answer is no.
"""

import re
import unicodedata
from datetime import UTC, datetime, timedelta
from datetime import date as Date
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from metaspike.domain.events import EventType
from metaspike.domain.games import CanonicalGame, Catalogue, GameId, LifecycleState, NonEmptyStr
from metaspike.domain.games import SourceIdentifier as GameIdentifier
from metaspike.domain.model import FrozenModel
from metaspike.event_sources.base import SourceGame, SourceItem, digest
from metaspike.events.model import (
    EventObservation,
    EventReport,
    EventStatus,
    TimePrecision,
    day_start,
    observe,
)

RULES_VERSION = "event_classification_rules_v1"


class TimingBasis(StrEnum):
    # A full calendar date written in the title. Explicit.
    EXPLICIT_DATE_IN_TITLE = "explicit_date_in_title"
    # The post's publication time, under a rule that allows it. Inferred.
    PUBLICATION_TIME = "publication_time"
    NONE = "none"


class EvidenceCategory(StrEnum):
    STRUCTURED_TAG_AND_TITLE_PHRASE = "structured_tag_and_title_phrase"
    STRUCTURED_TAG_ONLY = "structured_tag_only"
    EXACT_TITLE_PHRASE = "exact_title_phrase"
    NONE = "none"


class PromotionStatus(StrEnum):
    PROMOTED = "promoted"
    NOT_PROMOTED = "not_promoted"


class Refusal(StrEnum):
    """Why a candidate was not promoted. Several can hold at once."""

    UNRESOLVED_GAME = "unresolved_game"
    SOURCE_NOT_CLEARED = "source_not_cleared"
    NOT_AN_OFFICIAL_FEED = "not_an_official_feed"
    NO_TYPE_EVIDENCE = "no_type_evidence"
    AMBIGUOUS_TYPE = "ambiguous_type"
    PATCH_SIZE_NOT_ESTABLISHED = "patch_size_not_established"
    TITLE_NOT_ABOUT_AN_OCCURRENCE = "title_not_about_an_occurrence"
    NO_TIMING_EVIDENCE = "no_timing_evidence"
    TITLE_SAYS_NOT_YET = "title_says_not_yet"
    AMBIGUOUS_DATE = "ambiguous_date"
    DATE_ROLE_UNCLEAR = "date_role_unclear"
    DATE_BEFORE_PUBLICATION = "date_before_publication"
    DATE_TOO_FAR_AHEAD = "date_too_far_ahead"


_TYPE_REFUSALS = frozenset(
    {
        Refusal.NOT_AN_OFFICIAL_FEED,
        Refusal.NO_TYPE_EVIDENCE,
        Refusal.AMBIGUOUS_TYPE,
        Refusal.PATCH_SIZE_NOT_ESTABLISHED,
        Refusal.TITLE_NOT_ABOUT_AN_OCCURRENCE,
    }
)

# A token that stands for any run of digits in a phrase, as in "season 5".
NUMBER = "#"


class ClassificationRule(FrozenModel):
    """One way a title, with or without a tag, establishes a type of event."""

    rule_id: NonEmptyStr
    event_type: EventType
    # Structured tags the post must carry, exactly. Empty: none needed.
    required_tags: tuple[str, ...] = ()
    # The title must contain at least one of these, as whole consecutive words.
    title_phrases: tuple[tuple[str, ...], ...] = Field(min_length=1)
    # Whether the post's publication time may stand as the event's start when the
    # title gives no date. False: only an explicit date can time the event.
    publication_time_anchoring: bool
    # With anchoring allowed, whether the title must also say the thing is live now.
    anchoring_needs_live_phrase: bool
    evidence: NonEmptyStr
    rationale: NonEmptyStr


class EventClassificationRules(FrozenModel):
    version: NonEmptyStr
    status: NonEmptyStr
    # Feeds whose posts are the game's own publisher speaking. Others are never read
    # as evidence of an event, whatever their titles say.
    official_feed_names: tuple[str, ...]
    # The structured tag a publisher puts on a post it files as patch notes.
    patch_tag: NonEmptyStr
    rules: tuple[ClassificationRule, ...]
    # Says the thing is happening as the post goes out.
    live_phrases: tuple[tuple[str, ...], ...]
    # Any of these words and the title is about something not yet happening.
    not_yet_words: tuple[str, ...]
    # Any of these and the title is not the announcement of an occurrence at all:
    # commentary, promotion, an ending, a delay.
    not_an_occurrence_words: tuple[str, ...]
    # With an explicit date, one of these must say the date is a start...
    date_start_words: tuple[str, ...]
    # ...and none of these may suggest it is an end or a deadline.
    date_end_words: tuple[str, ...]
    max_days_ahead: int = Field(ge=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids = [rule.rule_id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("two rules share an id")
        return self

    def rule(self, rule_id: str) -> ClassificationRule:
        return next(rule for rule in self.rules if rule.rule_id == rule_id)


_LIVE_ANCHOR = (
    "Publication time is accepted as the start only because the title itself says the "
    "thing is live as the post goes out."
)

EVENT_CLASSIFICATION_RULES_V1 = EventClassificationRules(
    version=RULES_VERSION,
    status="internal_precision_first_unvalidated",
    official_feed_names=("steam_community_announcements",),
    patch_tag="patchnotes",
    rules=(
        ClassificationRule(
            rule_id="patch_notes_hotfix",
            event_type=EventType.MINOR_PATCH,
            required_tags=("patchnotes",),
            title_phrases=(("hotfix",), ("hotfixes",), ("hot", "fix")),
            publication_time_anchoring=True,
            anchoring_needs_live_phrase=False,
            evidence="publisher's own patch-notes tag, and the word 'hotfix' in the title",
            rationale=(
                "The publisher filed the post as patch notes and calls it a hotfix. Patch "
                "notes describe a build that has shipped, so the post's own time stands "
                "as the start unless the title says otherwise."
            ),
        ),
        ClassificationRule(
            rule_id="patch_notes_major_update",
            event_type=EventType.MAJOR_PATCH,
            required_tags=("patchnotes",),
            title_phrases=(("major", "update"),),
            publication_time_anchoring=True,
            anchoring_needs_live_phrase=False,
            evidence="publisher's own patch-notes tag, and 'major update' in the title",
            rationale=(
                "The publisher filed the post as patch notes and itself calls the update "
                "major. Size is taken from the publisher's words, never guessed."
            ),
        ),
        ClassificationRule(
            rule_id="season_start",
            event_type=EventType.SEASON,
            title_phrases=(("season", NUMBER),),
            publication_time_anchoring=True,
            anchoring_needs_live_phrase=True,
            evidence="'season' followed by a number in the title",
            rationale=_LIVE_ANCHOR,
        ),
        ClassificationRule(
            rule_id="free_weekend",
            event_type=EventType.FREE_WEEKEND,
            title_phrases=(("free", "weekend"),),
            publication_time_anchoring=True,
            anchoring_needs_live_phrase=True,
            evidence="'free weekend' in the title",
            rationale=_LIVE_ANCHOR,
        ),
        ClassificationRule(
            rule_id="expansion_release",
            event_type=EventType.EXPANSION,
            title_phrases=(("expansion",),),
            publication_time_anchoring=True,
            anchoring_needs_live_phrase=True,
            evidence="'expansion' in the title",
            rationale=_LIVE_ANCHOR,
        ),
        ClassificationRule(
            rule_id="dlc_release",
            event_type=EventType.DLC,
            title_phrases=(("dlc",),),
            publication_time_anchoring=True,
            anchoring_needs_live_phrase=True,
            evidence="'DLC' in the title",
            rationale=_LIVE_ANCHOR,
        ),
    ),
    live_phrases=(
        ("now", "live"),
        ("live", "now"),
        ("is", "live"),
        ("out", "now"),
        ("available", "now"),
        ("now", "available"),
        ("has", "begun"),
        ("has", "started"),
        ("starts", "now"),
        ("begins", "now"),
    ),
    not_yet_words=(
        "announced",
        "announcement",
        "announcing",
        "arriving",
        "coming",
        "countdown",
        "incoming",
        "later",
        "next",
        "planned",
        "preview",
        "reveal",
        "revealed",
        "schedule",
        "scheduled",
        "soon",
        "tomorrow",
        "tonight",
        "upcoming",
        "when",
        "will",
    ),
    not_an_occurrence_words=(
        "award",
        "awards",
        "blog",
        "bundle",
        "canceled",
        "cancelled",
        "concluded",
        "contest",
        "delay",
        "delayed",
        "devlog",
        "diary",
        "discount",
        "drops",
        "ended",
        "ending",
        "ends",
        "feedback",
        "final",
        "giveaway",
        "interview",
        "last",
        "livestream",
        "merch",
        "nominee",
        "over",
        "postponed",
        "recap",
        "retrospective",
        "review",
        "reviews",
        "roadmap",
        "sale",
        "soundtrack",
        "stream",
        "survey",
        "teaser",
        "trailer",
        "wishlist",
    ),
    date_start_words=(
        "arrives",
        "available",
        "begins",
        "launches",
        "live",
        "releases",
        "starts",
    ),
    date_end_words=("by", "deadline", "through", "thru", "till", "until"),
    max_days_ahead=180,
)


class GameBinding(FrozenModel):
    """The catalogue binding a source game id resolves through. Never a title match."""

    canonical_game_id: GameId
    binding_ref: str | None = None
    mapping_version: int | None = None


class EventCandidate(FrozenModel):
    """What an announcement might be, why, and whether it was promoted. Immutable."""

    candidate_id: str = Field(pattern=r"^cand_[0-9a-f]{32}$")
    rules_version: NonEmptyStr
    source: NonEmptyStr
    source_game: SourceGame
    source_item_id: NonEmptyStr
    item_content_id: str
    item_version_id: str
    canonical_game_id: GameId | None = None
    binding_ref: str | None = None
    mapping_version: int | None = None
    # When the source says the post was published, and when we saw it.
    announced_at: AwareDatetime
    observed_at: AwareDatetime
    title: NonEmptyStr
    reference: str | None = None
    proposed_event_type: EventType | None = None
    proposed_starts_at: AwareDatetime | None = None
    proposed_ends_at: AwareDatetime | None = None
    time_precision: TimePrecision | None = None
    classification_rule: str | None = None
    matched_rules: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    evidence_category: EvidenceCategory
    timing_basis: TimingBasis
    # True: stated in the title. False: inferred from publication. None: no timing.
    timing_is_explicit: bool | None = None
    status: PromotionStatus
    refusals: tuple[Refusal, ...] = ()
    explanation: NonEmptyStr
    # The EventObservation this candidate became, when it was promoted.
    observation_id: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        promoted = self.status is PromotionStatus.PROMOTED
        if promoted != (not self.refusals):
            raise ValueError("a candidate is promoted exactly when nothing refuses it")
        if promoted != (self.observation_id is not None):
            raise ValueError("a candidate names an observation exactly when promoted")
        if promoted and (
            self.canonical_game_id is None
            or self.proposed_event_type is None
            or self.proposed_starts_at is None
            or self.classification_rule is None
            or self.timing_basis is TimingBasis.NONE
        ):
            raise ValueError("a promoted candidate needs a game, a type, a rule and a time")
        if (self.proposed_starts_at is None) != (self.timing_basis is TimingBasis.NONE):
            raise ValueError("a start time and a timing basis go together")
        if self.candidate_id != candidate_id(self):
            raise ValueError("candidate_id does not match the candidate's content")
        return self

    @property
    def classified(self) -> bool:
        """Whether a type was established, whatever became of the timing."""
        return self.proposed_event_type is not None

    @property
    def key(self) -> tuple[str, str]:
        return self.source, self.source_item_id


def candidate_id(candidate: "EventCandidate | dict[str, object]") -> str:
    data = (
        candidate.model_dump(mode="json", exclude={"candidate_id"})
        if isinstance(candidate, EventCandidate)
        else {key: value for key, value in candidate.items() if key != "candidate_id"}
    )
    return f"cand_{digest(data)[:32]}"


def words(title: str) -> tuple[str, ...]:
    """A title as lowercase words: accents folded, punctuation dropped."""
    folded = unicodedata.normalize("NFKD", title).casefold()
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    return tuple(re.findall(r"[a-z0-9]+", folded))


def has_phrase(tokens: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
    """Whether `phrase` appears as whole consecutive words. `#` matches any number."""
    span = len(phrase)
    return any(
        all(
            (want == NUMBER and got.isdigit()) or want == got
            for want, got in zip(phrase, tokens[start : start + span], strict=True)
        )
        for start in range(len(tokens) - span + 1)
    )


_MONTHS = {
    name: index
    for index, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}
_MONTH = "|".join(sorted(_MONTHS, key=len, reverse=True))
_ORDINAL = r"(?:st|nd|rd|th)?"
_ISO_DATE = re.compile(r"(?<![\d-])(\d{4})-(\d{2})-(\d{2})(?![\d-])")
_MONTH_FIRST = re.compile(
    rf"\b({_MONTH})\.?\s+(\d{{1,2}}){_ORDINAL},?\s+(\d{{4}})\b", flags=re.IGNORECASE
)
_DAY_FIRST = re.compile(
    rf"\b(\d{{1,2}}){_ORDINAL}\s+({_MONTH})\.?,?\s+(\d{{4}})\b", flags=re.IGNORECASE
)
# Things that look like a date and cannot be read as one with certainty: a month and a
# day with no year, or digits around a slash (day/month or month/day, nobody can say).
_MONTH_AND_DAY = re.compile(
    rf"\b(?:({_MONTH})\.?\s+\d{{1,2}}{_ORDINAL}|\d{{1,2}}{_ORDINAL}\s+(?:of\s+)?({_MONTH}))\b",
    flags=re.IGNORECASE,
)
_SLASHED = re.compile(r"(?<!\d)\d{1,4}/\d{1,2}(?:/\d{1,4})?(?!\d)")


class TitleDates(FrozenModel):
    """The calendar dates a title states in full, and whether anything else looks like one."""

    dates: tuple[Date, ...]
    unreadable: bool


def dates_in(title: str) -> TitleDates:
    """Full dates in a title: `2026-10-20`, `October 20, 2026`, `20 October 2026`.

    A year is required. Anything date-like that is not one of those three forms, or
    is not a real calendar date, makes the title unreadable for dates rather than being
    guessed at.
    """
    found: list[tuple[int, int, Date | None]] = []

    def take(match: re.Match[str], year: str, month: int, day: str) -> None:
        try:
            value: Date | None = Date(int(year), month, int(day))
        except ValueError:
            value = None
        found.append((match.start(), match.end(), value))

    for match in _ISO_DATE.finditer(title):
        take(
            match,
            match.group(1),
            int(match.group(2)) if match.group(2).isdigit() else 0,
            match.group(3),
        )
    for match in _MONTH_FIRST.finditer(title):
        take(match, match.group(3), _MONTHS[match.group(1).lower()], match.group(2))
    for match in _DAY_FIRST.finditer(title):
        take(match, match.group(3), _MONTHS[match.group(2).lower()], match.group(1))
    covered = [(start, end) for start, end, _ in found]

    def outside(match: re.Match[str]) -> bool:
        return not any(start <= match.start() and match.end() <= end for start, end in covered)

    loose = any(outside(m) for m in _MONTH_AND_DAY.finditer(title)) or any(
        outside(m) for m in _SLASHED.finditer(title)
    )
    invalid = any(value is None for _, _, value in found)
    return TitleDates(
        dates=tuple(sorted({value for _, _, value in found if value is not None})),
        unreadable=loose or invalid,
    )


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def classify(
    item: SourceItem,
    binding: GameBinding | None,
    *,
    source_cleared: bool,
    rules: EventClassificationRules = EVENT_CLASSIFICATION_RULES_V1,
) -> EventCandidate:
    """Read one item under the rules. Pure: the same inputs give the same candidate.

    `binding` is what the catalogue says the item's game is, or None when it says
    nothing. `source_cleared` is whether the source's policy clears the stage this is
    running at; without it nothing is promoted.
    """
    tokens = words(item.title)
    present = set(tokens)
    refusals: list[Refusal] = []
    evidence: list[str] = []
    if binding is None:
        refusals.append(Refusal.UNRESOLVED_GAME)
    if not source_cleared:
        refusals.append(Refusal.SOURCE_NOT_CLEARED)

    # A. The type of event.
    official = item.feed_name in rules.official_feed_names
    tagged = rules.patch_tag in item.tags
    matched = [
        rule
        for rule in rules.rules
        if all(tag in item.tags for tag in rule.required_tags)
        and any(has_phrase(tokens, phrase) for phrase in rule.title_phrases)
    ]
    against = sorted(present.intersection(rules.not_an_occurrence_words))
    types = {rule.event_type for rule in matched}
    rule: ClassificationRule | None = None
    if not official:
        refusals.append(Refusal.NOT_AN_OFFICIAL_FEED)
    elif against:
        refusals.append(Refusal.TITLE_NOT_ABOUT_AN_OCCURRENCE)
        evidence.append(f"title word against: {', '.join(against)}")
    elif len(types) > 1:
        refusals.append(Refusal.AMBIGUOUS_TYPE)
    elif len(types) == 1:
        rule = matched[0]
    elif tagged:
        refusals.append(Refusal.PATCH_SIZE_NOT_ESTABLISHED)
    else:
        refusals.append(Refusal.NO_TYPE_EVIDENCE)
    if official:
        evidence.append(f"feed: {item.feed_name}")
    if tagged:
        evidence.append(f"tag: {rules.patch_tag}")
    if rule is not None:
        evidence.append(f"rule {rule.rule_id}: {rule.evidence}")

    # B. When it takes place. Only asked once the type is settled.
    starts_at: datetime | None = None
    precision: TimePrecision | None = None
    basis = TimingBasis.NONE
    if rule is not None:
        stated = dates_in(item.title)
        published_day = item.published_at.astimezone(UTC).date()
        if stated.unreadable or len(stated.dates) > 1:
            refusals.append(Refusal.AMBIGUOUS_DATE)
        elif stated.dates:
            day = stated.dates[0]
            if present.intersection(rules.date_end_words) or not present.intersection(
                rules.date_start_words
            ):
                refusals.append(Refusal.DATE_ROLE_UNCLEAR)
            elif day < published_day:
                refusals.append(Refusal.DATE_BEFORE_PUBLICATION)
            elif day > published_day + timedelta(days=rules.max_days_ahead):
                refusals.append(Refusal.DATE_TOO_FAR_AHEAD)
            else:
                starts_at, precision = day_start(day), TimePrecision.DAY
                basis = TimingBasis.EXPLICIT_DATE_IN_TITLE
                evidence.append(f"date in title: {day.isoformat()}")
        else:
            not_yet = sorted(present.intersection(rules.not_yet_words))
            live = any(has_phrase(tokens, phrase) for phrase in rules.live_phrases)
            if not_yet:
                refusals.append(Refusal.TITLE_SAYS_NOT_YET)
                evidence.append(f"title word against: {', '.join(not_yet)}")
            elif not rule.publication_time_anchoring or (
                rule.anchoring_needs_live_phrase and not live
            ):
                refusals.append(Refusal.NO_TIMING_EVIDENCE)
            else:
                starts_at, precision = item.published_at.astimezone(UTC), TimePrecision.EXACT
                basis = TimingBasis.PUBLICATION_TIME
                evidence.append(
                    "publication time, title says it is live"
                    if live
                    else "publication time, allowed by the rule"
                )

    type_settled = rule is not None
    category = EvidenceCategory.NONE
    if type_settled and rule is not None:
        category = (
            EvidenceCategory.STRUCTURED_TAG_AND_TITLE_PHRASE
            if rule.required_tags
            else EvidenceCategory.EXACT_TITLE_PHRASE
        )
    elif official and tagged:
        category = EvidenceCategory.STRUCTURED_TAG_ONLY
    promoted = not refusals
    observation = _observation(item, binding, rule, starts_at, precision) if promoted else None
    data: dict[str, object] = {
        "rules_version": rules.version,
        "source": item.source,
        "source_game": item.game.model_dump(),
        "source_item_id": item.source_item_id,
        "item_content_id": item.content_id,
        "item_version_id": item.item_version_id,
        "canonical_game_id": None if binding is None else binding.canonical_game_id,
        "binding_ref": None if binding is None else binding.binding_ref,
        "mapping_version": None if binding is None else binding.mapping_version,
        "announced_at": _iso(item.published_at),
        "observed_at": _iso(item.observed_at),
        "title": item.title,
        "reference": item.reference,
        "proposed_event_type": None if rule is None else rule.event_type.value,
        "proposed_starts_at": None if starts_at is None else _iso(starts_at),
        "proposed_ends_at": None,
        "time_precision": None if precision is None else precision.value,
        "classification_rule": None if rule is None else rule.rule_id,
        "matched_rules": [item.rule_id for item in matched],
        "evidence": evidence,
        "evidence_category": category.value,
        "timing_basis": basis.value,
        "timing_is_explicit": None
        if basis is TimingBasis.NONE
        else basis is TimingBasis.EXPLICIT_DATE_IN_TITLE,
        "status": (PromotionStatus.PROMOTED if promoted else PromotionStatus.NOT_PROMOTED).value,
        "refusals": [refusal.value for refusal in refusals],
        "explanation": _explain(rule, basis, refusals),
        "observation_id": None if observation is None else observation.observation_id,
    }
    return EventCandidate.model_validate({**data, "candidate_id": candidate_id(data)})


_REFUSAL_TEXT = {
    Refusal.UNRESOLVED_GAME: "the source's game id is not bound to a canonical game",
    Refusal.SOURCE_NOT_CLEARED: "the source's policy does not clear this stage",
    Refusal.NOT_AN_OFFICIAL_FEED: "the post is not from the game's own announcement feed",
    Refusal.NO_TYPE_EVIDENCE: "nothing in the title or tags establishes a type of event",
    Refusal.AMBIGUOUS_TYPE: "the title fits more than one type of event",
    Refusal.PATCH_SIZE_NOT_ESTABLISHED: (
        "filed as patch notes, but the title does not say whether the patch is major or minor"
    ),
    Refusal.TITLE_NOT_ABOUT_AN_OCCURRENCE: (
        "the title reads as commentary, promotion, a delay or an ending"
    ),
    Refusal.NO_TIMING_EVIDENCE: "the title gives no date and does not say it is live now",
    Refusal.TITLE_SAYS_NOT_YET: "the title says the thing has not happened yet, with no date",
    Refusal.AMBIGUOUS_DATE: "the title's date cannot be read with certainty",
    Refusal.DATE_ROLE_UNCLEAR: "the title's date is not clearly the start",
    Refusal.DATE_BEFORE_PUBLICATION: "the title's date is before the post was published",
    Refusal.DATE_TOO_FAR_AHEAD: "the title's date is further ahead than is believed",
}


def _explain(rule: ClassificationRule | None, basis: TimingBasis, refusals: list[Refusal]) -> str:
    if not refusals and rule is not None:
        timing = (
            "an explicit date in the title"
            if basis is TimingBasis.EXPLICIT_DATE_IN_TITLE
            else "the post's publication time"
        )
        return f"Promoted as {rule.event_type.value} by {rule.rule_id}, timed by {timing}."
    reasons = "; ".join(_REFUSAL_TEXT[refusal] for refusal in refusals)
    if rule is None:
        return f"Not promoted: {reasons}."
    return f"Not promoted, though {rule.rule_id} reads it as {rule.event_type.value}: {reasons}."


def binding_view(game: SourceGame, binding: GameBinding) -> Catalogue:
    """One catalogue binding, in the form the event resolver reads.

    `events.model.observe` resolves a source identifier against a catalogue and refuses
    anything not bound to exactly one game. The binding was established by the
    catalogue's own resolution; this only presents it to that one gate, so that an
    observation can be made no other way.
    """
    return Catalogue(
        games=(
            CanonicalGame(
                canonical_game_id=binding.canonical_game_id,
                slug="bound",
                name="bound",
                source_identifiers=(
                    GameIdentifier(source=game.source, namespace=game.namespace, value=game.value),
                ),
                lifecycle_state=LifecycleState.RELEASED,
            ),
        )
    )


def _observation(
    item: SourceItem,
    binding: GameBinding | None,
    rule: ClassificationRule | None,
    starts_at: datetime | None,
    precision: TimePrecision | None,
    status: EventStatus = EventStatus.SCHEDULED,
) -> EventObservation:
    assert binding is not None and rule is not None
    assert starts_at is not None and precision is not None
    report = EventReport(
        game=GameIdentifier(
            source=item.game.source, namespace=item.game.namespace, value=item.game.value
        ),
        event_type=rule.event_type,
        title=item.title,
        starts_at=starts_at,
        time_precision=precision,
        status=status,
        source_event_id=item.source_item_id,
        reference=item.reference,
    )
    return observe(report, binding_view(item.game, binding), item.observed_at)


def promoted_observation(
    candidate: EventCandidate, item: SourceItem, rules: EventClassificationRules
) -> EventObservation:
    """The observation a promoted candidate stands for. Refuses anything else."""
    if candidate.status is not PromotionStatus.PROMOTED:
        raise ValueError("only a promoted candidate becomes an observation")
    if candidate.item_version_id != item.item_version_id:
        raise ValueError("the candidate was not made from this item")
    assert candidate.canonical_game_id is not None and candidate.classification_rule is not None
    observation = _observation(
        item,
        GameBinding(canonical_game_id=candidate.canonical_game_id),
        rules.rule(candidate.classification_rule),
        candidate.proposed_starts_at,
        candidate.time_precision,
    )
    if observation.observation_id != candidate.observation_id:
        raise ValueError("the candidate does not describe this observation")
    return observation


def withdrawal(previous: EventObservation, observed_at: datetime) -> EventObservation:
    """Take back an event a later reading of the same post no longer supports.

    The earlier observation stays in the store. This is a new one saying the source's
    statement is no longer read as that event, so the event stops counting from here
    on and never before.
    """
    report = EventReport(
        game=GameIdentifier(source=previous.source, namespace="withdrawn", value="withdrawn"),
        event_type=previous.event_type,
        title=previous.title,
        starts_at=previous.starts_at,
        ends_at=previous.ends_at,
        time_precision=previous.time_precision,
        status=EventStatus.WITHDRAWN,
        source_event_id=previous.source_event_id,
        reference=previous.reference,
    )
    view = binding_view(
        SourceGame(source=previous.source, namespace="withdrawn", value="withdrawn"),
        GameBinding(canonical_game_id=previous.canonical_game_id),
    )
    return observe(report, view, observed_at)
