"""From observations to events: identity, deduplication and revisions.

`reconcile` is a pure fold. Observations are taken in the order they were observed and
each one either continues an event already known or starts a new one. Nothing is
overwritten: an event's current fields are those of its latest observation, and every
observation that touched it is kept as a revision saying what it matched on and what it
changed.

An observation is taken to be about a known event of the same game by the first rule
that applies, strongest first:

1. `same_source_native_id`: the same source reports the same source-native event id.
   The date, the title and the end may all have changed; it is still that event.
2. `same_source_type_and_day`: the same source reports the same type of event starting
   on the same UTC day, and the two statements do not carry different native ids.
   Covers a source that repeats itself, rewords a title, adds an end date later or
   starts supplying an id. Two different native ids on one day are two events.
3. `cross_source_same_reference`: a different source cites exactly the same reference
   for the same type of event.
4. `cross_source_same_type_day_and_title`: a different source reports the same type of
   event starting on the same UTC day under the same title, compared after folding case
   and punctuation. Equality, not similarity.

Anything else is a new event. That is deliberate: two real events close together must
not be merged, so the rules prefer keeping two records of one event over making one
record of two. The cost of an unmerged duplicate is bounded where it would matter, in
the heat aggregation, which counts one event per type and start day.

Consequences worth knowing:

- A source without native ids that moves an event's date is seen as reporting a new
  event. The old one stays until that source withdraws or cancels it.
- Events of different types are never merged, whatever else they share.
- Events of different games are never merged.

An event's id is fixed by its first observation and never changes, whatever is revised
afterwards.
"""

import re
import unicodedata
from collections.abc import Iterable
from datetime import datetime

from metaspike.events.model import (
    EventObservation,
    EventRecord,
    EventRevision,
    MatchRule,
    digest,
)

_REVISABLE = ("event_type", "title", "starts_at", "ends_at", "time_precision", "status")


def normalise_title(title: str) -> str:
    """A title folded for equality: case, accents, punctuation and spacing removed."""
    folded = unicodedata.normalize("NFKD", title).casefold()
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", folded).strip()


def normalise_reference(reference: str) -> str:
    """A reference folded for equality: scheme, `www.`, case of the host, trailing slash."""
    value = reference.strip()
    match = re.match(r"^(?:https?://)?(?:www\.)?([^/?#]+)(.*)$", value, flags=re.IGNORECASE)
    if match is None:
        return value
    return f"{match.group(1).lower()}{match.group(2).rstrip('/')}"


def event_id(first: EventObservation) -> str:
    """Stable identity, taken from the first observation and never recomputed."""
    if first.source_event_id is not None:
        basis: tuple[object, ...] = (
            "native",
            first.canonical_game_id,
            first.source,
            first.source_event_id,
        )
    else:
        basis = (
            "dated",
            first.canonical_game_id,
            first.source,
            first.event_type.value,
            first.start_day.isoformat(),
            normalise_title(first.title),
        )
    return f"evt_{digest(basis)[:24]}"


class _Draft:
    """An event being folded. Private to this module; `EventRecord` is what leaves it."""

    def __init__(self, first: EventObservation) -> None:
        self.event_id = event_id(first)
        self.members: list[EventObservation] = []
        self.revisions: list[EventRevision] = []
        self.add(first, MatchRule.NEW_EVENT)

    @property
    def current(self) -> EventObservation:
        return self.members[-1]

    def current_of(self, source: str) -> EventObservation | None:
        for member in reversed(self.members):
            if member.source == source:
                return member
        return None

    def native_ids(self) -> set[tuple[str, str]]:
        return {
            (member.source, member.source_event_id)
            for member in self.members
            if member.source_event_id is not None
        }

    def add(self, observation: EventObservation, rule: MatchRule) -> None:
        before = self.record() if self.members else None
        self.members.append(observation)
        after = self.record(pending=True)
        changed = (
            ()
            if before is None
            else tuple(name for name in _REVISABLE if getattr(before, name) != getattr(after, name))
        )
        self.revisions.append(
            EventRevision(
                observation_id=observation.observation_id,
                source=observation.source,
                observed_at=observation.observed_at,
                matched_by=rule,
                changed=changed,
            )
        )

    def _ends_at(self) -> datetime | None:
        """The latest statement's end; failing that, another source's current end.

        A source that has not mentioned an end does not erase one a different source
        still stands by. The same source dropping its end does.
        """
        latest = self.current
        if latest.ends_at is not None:
            return latest.ends_at
        for source in sorted({member.source for member in self.members}):
            if source == latest.source:
                continue
            claim = self.current_of(source)
            if (
                claim is not None
                and claim.ends_at is not None
                and claim.ends_at >= latest.starts_at
            ):
                return claim.ends_at
        return None

    def record(self, *, pending: bool = False) -> EventRecord:
        latest = self.current
        revisions = tuple(self.revisions)
        if pending:
            # Called from `add` before the new revision exists; only fields are compared.
            revisions = (
                *revisions,
                EventRevision(
                    observation_id=latest.observation_id,
                    source=latest.source,
                    observed_at=latest.observed_at,
                    matched_by=MatchRule.NEW_EVENT,
                    changed=(),
                ),
            )
        return EventRecord(
            event_id=self.event_id,
            canonical_game_id=latest.canonical_game_id,
            event_type=latest.event_type,
            title=latest.title,
            starts_at=latest.starts_at,
            ends_at=self._ends_at(),
            time_precision=latest.time_precision,
            status=latest.status,
            first_observed_at=self.members[0].observed_at,
            last_observed_at=latest.observed_at,
            sources=tuple(sorted({member.source for member in self.members})),
            source_event_ids=tuple(sorted({native for _, native in self.native_ids()})),
            references=tuple(
                sorted({member.reference for member in self.members if member.reference})
            ),
            revisions=revisions,
        )


def _match(observation: EventObservation, drafts: list[_Draft]) -> tuple[_Draft, MatchRule] | None:
    """The known event this observation is about, and the rule that says so."""
    same_game = [
        draft
        for draft in drafts
        if draft.current.canonical_game_id == observation.canonical_game_id
    ]
    if observation.source_event_id is not None:
        key = (observation.source, observation.source_event_id)
        for draft in same_game:
            if key in draft.native_ids():
                return draft, MatchRule.SAME_SOURCE_NATIVE_ID
    for draft in same_game:
        claim = draft.current_of(observation.source)
        if (
            claim is not None
            and (claim.source_event_id is None or observation.source_event_id is None)
            and claim.event_type is observation.event_type
            and claim.start_day == observation.start_day
        ):
            return draft, MatchRule.SAME_SOURCE_TYPE_AND_DAY
    others = [draft for draft in same_game if draft.current_of(observation.source) is None]
    if observation.reference is not None:
        wanted = normalise_reference(observation.reference)
        for draft in others:
            current = draft.current
            if current.event_type is observation.event_type and any(
                member.reference is not None and normalise_reference(member.reference) == wanted
                for member in draft.members
            ):
                return draft, MatchRule.CROSS_SOURCE_SAME_REFERENCE
    title = normalise_title(observation.title)
    for draft in others:
        current = draft.current
        if (
            title != ""
            and current.event_type is observation.event_type
            and current.start_day == observation.start_day
            and normalise_title(current.title) == title
        ):
            return draft, MatchRule.CROSS_SOURCE_SAME_TYPE_DAY_AND_TITLE
    return None


def ordered(observations: Iterable[EventObservation]) -> list[EventObservation]:
    """Observations in the one order everything here uses, without exact repeats."""
    unique = {observation.observation_id: observation for observation in observations}
    return sorted(unique.values(), key=lambda item: (item.observed_at, item.observation_id))


def reconcile(
    observations: Iterable[EventObservation], as_of: datetime | None = None
) -> tuple[EventRecord, ...]:
    """Every event the observations describe, as known at `as_of` (or with all of them).

    Deterministic: the same observations give the same records in the same order,
    whatever order they are passed in.
    """
    drafts: list[_Draft] = []
    for observation in ordered(observations):
        if as_of is not None and observation.observed_at > as_of:
            continue
        found = _match(observation, drafts)
        if found is None:
            drafts.append(_Draft(observation))
            continue
        draft, rule = found
        draft.add(observation, rule)
    records = [draft.record() for draft in drafts]
    return tuple(sorted(records, key=lambda r: (r.canonical_game_id, r.starts_at, r.event_id)))
