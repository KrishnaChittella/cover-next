"""Candidate blocking: find the entities worth assessing without assessing all of them.

The resolver assesses an entity only if something makes it a candidate for the
incoming record (see `resolver._assess`): a structured id link, a recognised source
URL, an upstream parent link, or an exact, alternative or packaging-variant title.
Everything else it ignores. So a record can be resolved against only its candidates
and the decision is the same as if every entity had been offered.

`CandidateIndex` finds those candidates by lookup. Its one obligation is to never
miss one: `candidates()` returns a SUPERSET of the entities `_assess` would accept,
and the resolver then applies its own unchanged rules to that set. Blocking decides
nothing. It adds no rule, relaxes none, and has no notion of similarity: every key
below is an exact key the resolver's own candidate rules already compare.

    rule in the resolver                          key indexed here
    --------------------------------------------  ------------------------------------
    same source record                            the record's own (source, id)
    external id, asserted by either side          (external name, value), both ways
    recognised source URL, on either side         the id the URL addresses, both ways
    parent / version parent held by the entity    the entity's records' (source, id)
    exact title                                   normalised title key
    alternative title, on either side             normalised alternative-title keys
    packaging variant                             title core without packaging words,
                                                  rework words and, on the incoming
                                                  side, any year-like number

The last row is the only one that is not a plain equality. The resolver drops a
number from the incoming title when it equals one of the other record's release
years, which the index cannot know in advance, so the lookup tries every way of
dropping the incoming title's numbers. That can only add candidates.
"""

from itertools import combinations

from metaspike.catalogue.overrides import NO_OVERRIDES, OverrideSet
from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.source_records import SourceRecord
from metaspike.resolution.models import ResolverInput
from metaspike.resolution.normalize import REWORK_WORDS, normalize_title
from metaspike.resolution.resolver import _EXTERNAL_NAME, _without
from metaspike.resolution.source_urls import url_asserted_ids

# A title with more distinct numbers than this is not blocked on its packaging key:
# the record is assessed against every entity instead. Correct, merely slower, and no
# real title is expected to reach it.
MAX_TITLE_NUMBERS = 10

_Identity = tuple[str, str]


class CandidateIndex:
    """Entities by every exact key the resolver's candidate rules compare."""

    def __init__(self) -> None:
        self._by_key: dict[SourceRecordKey, set[str]] = {}
        # (external name, id) -> entities holding the record that IS that identity.
        self._is: dict[_Identity, set[str]] = {}
        # (external name, id) -> entities holding a record that ASSERTS that identity.
        self._asserts: dict[_Identity, set[str]] = {}
        self._url_asserts: dict[_Identity, set[str]] = {}
        self._title: dict[str, set[str]] = {}
        self._alternative: dict[str, set[str]] = {}
        self._core: dict[tuple[str, ...], set[str]] = {}

    def add(self, ref: str, record: SourceRecord) -> None:
        """Index one record as belonging to an entity. A record is never removed."""
        self._by_key.setdefault(record.key, set()).add(ref)
        name = _EXTERNAL_NAME.get(record.key.source)
        if name is not None:
            self._is.setdefault((name, record.key.source_id), set()).add(ref)
        for link in record.external_ids:
            self._asserts.setdefault((link.source, link.value), set()).add(ref)
        for link in url_asserted_ids(record.websites):
            self._url_asserts.setdefault((link.source, link.value), set()).add(ref)
        title = normalize_title(record.title)
        self._title.setdefault(title.key, set()).add(ref)
        for alternative in record.alternative_titles:
            self._alternative.setdefault(normalize_title(alternative).key, set()).add(ref)
        core = _without(title.core_tokens, REWORK_WORDS, set())
        if core:
            self._core.setdefault(core, set()).add(ref)

    def holding(self, key: SourceRecordKey | None) -> set[str]:
        return set() if key is None else set(self._by_key.get(key, ()))

    def candidates(self, record: SourceRecord) -> set[str] | None:
        """Every entity that could be a candidate, or None if all must be assessed."""
        found: set[str] = set()
        found |= self._by_key.get(record.key, set())
        # Upstream links: the entity holds the record's parent or version parent.
        found |= self.holding(record.parent) | self.holding(record.version_parent)
        # Id and URL links asserted by the entity's records about this record.
        name = _EXTERNAL_NAME.get(record.key.source)
        if name is not None:
            mine = (name, record.key.source_id)
            found |= self._asserts.get(mine, set()) | self._url_asserts.get(mine, set())
        # Id and URL links this record asserts about an entity's record.
        for link in (*record.external_ids, *url_asserted_ids(record.websites)):
            found |= self._is.get((link.source, link.value), set())
        # Exact and alternative titles.
        title = normalize_title(record.title)
        found |= self._title.get(title.key, set()) | self._alternative.get(title.key, set())
        for alternative in record.alternative_titles:
            found |= self._title.get(normalize_title(alternative).key, set())
        # Packaging variants: the same words once packaging, rework words and a year
        # qualifier are set aside.
        tokens = tuple(token for token in title.core_tokens if token not in REWORK_WORDS)
        numbers = sorted({int(token) for token in tokens if token.isdigit()})
        if len(numbers) > MAX_TITLE_NUMBERS:
            return None
        for size in range(len(numbers) + 1):
            for dropped in combinations(numbers, size):
                core = _without(tokens, frozenset(), set(dropped))
                if core:
                    found |= self._core.get(core, set())
        return found


class ContextIndex:
    """Unresolved records by the identity they assert, for URL-claim corroboration.

    The resolver consults context for exactly one thing: whether an unresolved record
    carries the structured id of the incoming record. Records that do not cannot
    affect a decision, so only those are offered.
    """

    def __init__(self) -> None:
        self._claims: dict[_Identity, dict[SourceRecordKey, SourceRecord]] = {}
        self._held: dict[SourceRecordKey, SourceRecord] = {}

    def add(self, record: SourceRecord) -> None:
        self.remove(record.key)
        self._held[record.key] = record
        for link in record.external_ids:
            self._claims.setdefault((link.source, link.value), {})[record.key] = record

    def remove(self, key: SourceRecordKey) -> None:
        record = self._held.pop(key, None)
        if record is not None:
            for link in record.external_ids:
                self._claims.get((link.source, link.value), {}).pop(key, None)

    def claimants(self, record: SourceRecord) -> tuple[SourceRecord, ...]:
        name = _EXTERNAL_NAME.get(record.key.source)
        if name is None:
            return ()
        claims = self._claims.get((name, record.key.source_id), {})
        return tuple(other for key, other in claims.items() if key != record.key)


def blocked_view(view: ResolverInput, overrides: OverrideSet = NO_OVERRIDES) -> ResolverInput:
    """The same question with only the entities and context that can bear on it.

    `resolve(blocked_view(view, overrides), overrides)` must equal
    `resolve(view, overrides)` for every view. Tests hold it to that.
    """
    index = CandidateIndex()
    for entity in view.entities:
        for record in entity.records:
            index.add(entity.ref, record)
    context = ContextIndex()
    for record in view.context:
        context.add(record)
    wanted = wanted_refs(view.incoming, index, overrides, view.bound_to)
    if wanted is None:
        return view
    return ResolverInput(
        incoming=view.incoming,
        entities=tuple(entity for entity in view.entities if entity.ref in wanted),
        context=context.claimants(view.incoming),
        bound_to=view.bound_to,
    )


def wanted_refs(
    record: SourceRecord,
    index: CandidateIndex,
    overrides: OverrideSet,
    bound_to: str | None = None,
) -> set[str] | None:
    """The candidates, plus the entities an override or an existing binding names."""
    wanted = index.candidates(record)
    if wanted is None:
        return None
    override = overrides.for_record(record.key)
    if override is not None:
        # An override is applied by reference, not by evidence, so the entities it
        # names must be on offer even though nothing makes them candidates.
        wanted.add(override.canonical_game_id)
        if override.related_game_id is not None:
            wanted.add(override.related_game_id)
    if bound_to is not None:
        wanted.add(bound_to)
    return wanted
