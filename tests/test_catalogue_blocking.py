"""Blocked, incremental resolution must decide exactly what all-pairs resolution decides.

Three levels of proof, each stricter about a different thing:

    the index     never misses an entity the resolver would treat as a candidate
    one decision  resolving a blocked view gives the identical decision, on every
                  golden case and at every decision point of a catalogue build
    a whole build the blocked engine returns a CatalogueState equal to the one the
                  untouched `generate_catalogue` returns: every binding, every logged
                  decision, every quarantine entry and fingerprint, every id
"""

import random
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from metaspike.catalogue.generate import CatalogueState, generate_catalogue
from metaspike.catalogue.incremental import (
    BlockingMismatch,
    GenerationStats,
    generate_catalogue_blocked,
)
from metaspike.catalogue.overrides import (
    NO_OVERRIDES,
    PRODUCTION_OVERRIDES,
    IdentityOverride,
    OverrideAction,
    OverrideSet,
)
from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.source_records import (
    DatePrecision,
    ExternalId,
    ProductType,
    ReleaseDate,
    SourceRecord,
)
from metaspike.resolution import blocking
from metaspike.resolution.blocking import (
    CandidateIndex,
    blocked_view,
    number_free,
    wanted_refs,
)
from metaspike.resolution.evaluate import evaluate
from metaspike.resolution.golden import GoldenSet, load_golden_set
from metaspike.resolution.models import DecisionKind, EntityView, ResolverInput
from metaspike.resolution.normalize import REWORK_WORDS, normalize_title
from metaspike.resolution.quarantine import evaluation_fingerprint
from metaspike.resolution.recorded import load_recorded_catalogue
from metaspike.resolution.resolver import RESOLVER_VERSION, _without, candidate_refs, resolve
from tests.catalogue_samples import SHAPES, stratified_sample
from tests.conftest import make_id

FIXTURES = Path(__file__).parent / "fixtures"
AS_OF = datetime(2026, 10, 5, tzinfo=UTC)
GOLDEN_V3 = "c2b3aef7b3728828ea63a16f1a445bc24d7db53ce1150450252fc4154af189bb"


def counting_ids(start: int = 10_000) -> Callable[[], uuid.UUID]:
    """Deterministic ids. A later run takes a different range, as real ids never repeat."""
    issued = [0]

    def factory() -> uuid.UUID:
        issued[0] += 1
        return uuid.UUID(int=start + issued[0], version=4)

    return factory


def recorded(name: str) -> list[SourceRecord]:
    return list(load_recorded_catalogue(FIXTURES / name).values())


@pytest.fixture(scope="module")
def golden() -> GoldenSet:
    return load_golden_set(
        FIXTURES / "resolution_golden.json", load_recorded_catalogue(FIXTURES / "catalogue")
    )


def both(
    records: list[SourceRecord], **options: object
) -> tuple[CatalogueState, CatalogueState, GenerationStats]:
    stats = GenerationStats()
    slow = generate_catalogue(records, as_of=AS_OF, id_factory=counting_ids(), **options)  # type: ignore[arg-type]
    fast = generate_catalogue_blocked(
        records,
        as_of=AS_OF,
        id_factory=counting_ids(),
        stats=stats,
        **options,  # type: ignore[arg-type]
    )
    return slow, fast, stats


# --- golden v3 ---------------------------------------------------------------------------


def test_the_blocked_resolver_decides_every_golden_case_identically(golden: GoldenSet) -> None:
    assert golden.content_hash == GOLDEN_V3 and len(golden.cases) == 200
    kinds: Counter[str] = Counter()
    narrowed = 0
    for case in golden.cases:
        view, _ = case.resolver_view()
        for overrides in (PRODUCTION_OVERRIDES, NO_OVERRIDES):
            narrow = blocked_view(view, overrides)
            assert resolve(narrow, overrides) == resolve(view, overrides), case.case_id
            assert candidate_refs(narrow) == candidate_refs(view), case.case_id
            assert evaluation_fingerprint(narrow, overrides) == evaluation_fingerprint(
                view.model_copy(update={"context": narrow.context}), overrides
            )
        kinds[resolve(view, PRODUCTION_OVERRIDES).kind.value] += 1
        narrowed += len(narrow.entities) < len(view.entities)
    assert set(kinds) == {k.value for k in DecisionKind}
    assert narrowed > 0  # blocking really removed entities on some cases


def test_the_golden_evaluation_is_unchanged_under_blocking(golden: GoldenSet) -> None:
    plain = evaluate(golden)
    blocked = evaluate(
        golden, resolver=lambda view, overrides: resolve(blocked_view(view, overrides), overrides)
    )
    assert blocked == plain
    assert plain.resolver_version == RESOLVER_VERSION == "resolver_v2"
    assert plain.overall.false_auto_merges == 0
    assert plain.overall.precision == 1.0 and plain.overall.auto_matches == 52
    assert (
        plain.unmasked_positives.correct_auto_matches_on_positives,
        plain.unmasked_positives.positives,
    ) == (44, 44)


# --- the index never misses a candidate --------------------------------------------------


def test_the_index_offers_every_entity_the_resolver_would_assess() -> None:
    sample = stratified_sample(700)
    state = generate_catalogue_blocked(
        [*recorded("catalogue"), *sample.records], as_of=AS_OF, id_factory=counting_ids()
    )
    by_key = {r.key: r for r in [*recorded("catalogue"), *sample.records]}
    members: dict[str, list[SourceRecord]] = {}
    for binding in state.mapping.bindings:
        members.setdefault(binding.canonical_game_id, []).append(by_key[binding.key])
    entities = tuple(EntityView(ref=ref, records=tuple(rs)) for ref, rs in members.items())
    index = CandidateIndex()
    for entity in entities:
        for record in entity.records:
            index.add(entity.ref, record)

    checked = with_candidates = 0
    for record in list(by_key.values())[::3]:
        truth = set(candidate_refs(ResolverInput(incoming=record, entities=entities)))
        offered = index.candidates(record)
        checked += 1
        with_candidates += bool(truth)
        assert offered is not None  # no title is assessed against everything any more
        assert truth <= offered, record.key
        # And it is selective: a superset, not everything.
        assert len(offered) <= len(truth) + 8, record.key
    assert checked > 300 and with_candidates > 200


def test_blocking_adds_no_rule_of_its_own() -> None:
    """Every key the index uses is one the resolver's candidate rules compare exactly."""
    source = Path(blocking.__file__).read_text()
    for forbidden in (
        "difflib",
        "levenshtein",
        "fuzz",
        "soundex",
        "similar",
        "startswith",
        "ratio(",
    ):
        assert forbidden not in source.lower().replace("similarity: every", ""), forbidden
    # The one comparison that is not a plain equality is the resolver's own function,
    # called with the resolver's own inputs.
    assert (
        "from metaspike.resolution.resolver import _EXTERNAL_NAME, _release_years, _without"
        in source
    )
    assert "_without(core_tokens, REWORK_WORDS, set(their_years)) == their_core" in source
    # No shortlist, no cap, no sampling: nothing that could leave a candidate out.
    code = source.split('"""', 2)[2]
    for arbitrary in (
        "[:",
        "heapq",
        "most_common",
        "random",
        "itertools",
        "top_k",
        "limit",
        "MAX_",
    ):
        assert arbitrary not in code, arbitrary


# --- whole builds ------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", ["catalogue", "bootstrap_catalogue"])
def test_the_blocked_build_equals_the_reference_on_the_recorded_fixtures(fixture: str) -> None:
    records = recorded(fixture)
    for options in ({}, {"overrides": NO_OVERRIDES}):
        slow, fast, stats = both(records, **options)
        assert fast == slow
        assert fast.model_dump_json() == slow.model_dump_json()
        assert stats.full_scans == 0
    assert stats.entities_offered * 20 < stats.entities_all_pairs or fixture != "catalogue"


def test_the_blocked_build_equals_the_reference_on_a_stratified_sample() -> None:
    sample = stratified_sample(700)
    assert len(sample.counts()) >= 45 and len(SHAPES) == 21
    slow, fast, stats = both([*recorded("catalogue"), *sample.records])
    assert fast == slow
    kinds = Counter(d.decision.kind for d in fast.decisions.decisions)
    assert set(kinds) == set(DecisionKind)
    assert {e.reason.value for e in fast.quarantine.entries} == {
        "title_only",
        "conflicting_evidence",
        "id_link_title_mismatch",
        "multiple_candidates",
        "undetermined_product_type",
        "variant_without_parent",
        "unconfirmed_url_claim",
    }
    assert {d.decision.path for d in fast.decisions.decisions if d.decision.path} == {"A", "B"}
    # The sample still holds its titles with a dozen numbers; they are keyed now, so
    # nothing is assessed against every entity, and the two builds still agree.
    assert "igdb_title_beyond_blocking" in sample.counts()
    assert stats.full_scans == 0
    assert stats.entities_offered * 10 < stats.entities_all_pairs


def test_input_order_does_not_let_the_two_strategies_diverge() -> None:
    records = [*recorded("catalogue"), *stratified_sample(120, seed=7).records]
    for ordering in (records, records[::-1], sorted(records, key=lambda r: r.content_hash)):
        slow, fast, _ = both(ordering)
        assert fast == slow


def test_cross_checking_asks_every_question_both_ways_and_would_catch_a_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [*recorded("catalogue"), *stratified_sample(400).records]
    stats = GenerationStats()
    checked = generate_catalogue_blocked(
        records, as_of=AS_OF, id_factory=counting_ids(), stats=stats, cross_check=True
    )
    assert checked == generate_catalogue_blocked(records, as_of=AS_OF, id_factory=counting_ids())
    assert stats.cross_checked == stats.resolver_calls > 700

    # An index that forgets exact titles must be caught, not silently tolerated.
    real = CandidateIndex.candidates

    def forgetful(self: CandidateIndex, record: SourceRecord) -> set[str] | None:
        found = real(self, record)
        return None if found is None else {ref for ref in found if hash(ref) % 3}

    monkeypatch.setattr(CandidateIndex, "candidates", forgetful)
    with pytest.raises(BlockingMismatch):
        generate_catalogue_blocked(
            records, as_of=AS_OF, id_factory=counting_ids(), cross_check=True
        )


# --- incrementality ----------------------------------------------------------------------


def test_a_second_run_over_the_same_records_resolves_nothing() -> None:
    records = [*recorded("catalogue"), *stratified_sample(250).records]
    slow, fast, _ = both(records)
    later = AS_OF + timedelta(days=1)
    stats = GenerationStats()
    again = generate_catalogue_blocked(records, as_of=later, previous=fast, stats=stats)
    assert again == generate_catalogue(records, as_of=later, previous=slow)
    assert stats.resolver_calls == 0
    assert (
        stats.fingerprint_checks
        == stats.skipped_unchanged_quarantine
        == len(fast.quarantine.entries)
    )
    assert again.decisions == fast.decisions and again.mapping == fast.mapping


def test_only_new_records_and_affected_quarantine_are_resolved() -> None:
    sample = stratified_sample(520).records
    known, arriving = [*recorded("catalogue"), *sample[:440]], sample[440:]
    slow, fast, _ = both(known)
    later = AS_OF + timedelta(days=1)
    everything = [*known, *arriving]
    stats = GenerationStats()
    advanced = generate_catalogue_blocked(
        everything, as_of=later, previous=fast, id_factory=counting_ids(900_000), stats=stats,
        cross_check=True,
    )  # fmt: skip
    reference = generate_catalogue(
        everything, as_of=later, previous=slow, id_factory=counting_ids(900_000)
    )
    assert advanced == reference
    new_decisions = advanced.decisions.decisions[len(fast.decisions.decisions) :]
    decided = {d.record for d in new_decisions}
    assert {r.key for r in arriving} <= decided
    # Anything else that was decided again was in quarantine, and its evidence changed.
    held_before = {e.record.key: e.fingerprint for e in fast.quarantine.entries}
    reopened = decided - {r.key for r in arriving}
    assert reopened and reopened <= set(held_before)
    assert stats.examined == len(arriving) + len(held_before)
    assert stats.resolver_calls < len(arriving) + len(held_before)
    assert stats.resolver_calls * 5 < len(everything)
    # Nothing already bound was looked at again.
    bound_before = {b.key for b in fast.mapping.bindings}
    assert not decided & bound_before


def test_a_changed_bound_record_reopens_the_quarantine_it_bears_on() -> None:
    records = recorded("catalogue")
    slow, fast, _ = both(records)
    waiting = next(
        e
        for e in fast.quarantine.entries
        if e.record.key.source == "steam_catalogue" and e.reason.value == "title_only"
    )
    target = waiting.candidates[0].entity_ref
    holder = next(
        b.key
        for b in fast.mapping.bindings
        if b.canonical_game_id == target and b.key.source == "igdb"
    )
    link = ExternalId(source="steam", value=waiting.record.key.source_id)
    changed = [
        r.model_copy(update={"external_ids": (*r.external_ids, link)}) if r.key == holder else r
        for r in records
    ]
    later = AS_OF + timedelta(days=2)
    stats = GenerationStats()
    advanced = generate_catalogue_blocked(changed, as_of=later, previous=fast, stats=stats)
    assert advanced == generate_catalogue(changed, as_of=later, previous=slow)
    assert advanced.mapping.canonical_for(waiting.record.key) == target  # resolved itself
    assert advanced.quarantine.get(waiting.record.key) is None
    assert 1 <= stats.resolver_calls <= 3  # the one record whose evidence changed


# --- adversarial titles: the index is still a superset, and no longer falls back ---------
#
# Every record below is SYNTHETIC: invented to put the index under a specific strain.


def made(
    source_id: str,
    title: str,
    *,
    source: str = "igdb",
    years: tuple[int, ...] = (),
    kind: ProductType = ProductType.MAIN_GAME,
    alternatives: tuple[str, ...] = (),
    external: tuple[tuple[str, str], ...] = (),
    websites: tuple[str, ...] = (),
    parent: SourceRecord | None = None,
    version_parent: SourceRecord | None = None,
    studio: str | None = "Synthetic Studio",
) -> SourceRecord:
    return SourceRecord(
        key=SourceRecordKey(source=source, source_id=source_id),
        title=title,
        alternative_titles=alternatives,
        product_type=kind,
        release_dates=tuple(
            ReleaseDate(starts_on=date(year, 6, 1), precision=DatePrecision.DAY) for year in years
        ),
        developers=() if studio is None else (studio,),
        external_ids=tuple(ExternalId(source=s, value=v) for s, v in external),
        websites=websites,
        parent=None if parent is None else parent.key,
        version_parent=None if version_parent is None else version_parent.key,
        fetched_at=AS_OF,
    )


def entities_of(*groups: tuple[SourceRecord, ...]) -> tuple[EntityView, ...]:
    return tuple(
        EntityView(ref=f"e{number}", records=group) for number, group in enumerate(groups, start=1)
    )


def index_of(entities: tuple[EntityView, ...]) -> CandidateIndex:
    index = CandidateIndex()
    for entity in entities:
        for record in entity.records:
            index.add(entity.ref, record)
    return index


def check(
    incoming: SourceRecord,
    entities: tuple[EntityView, ...],
    context: tuple[SourceRecord, ...] = (),
    overrides: OverrideSet = NO_OVERRIDES,
    bound_to: str | None = None,
) -> tuple[set[str], set[str]]:
    """Hold the index to its one obligation for one question. Returns (truth, offered)."""
    view = ResolverInput(incoming=incoming, entities=entities, context=context, bound_to=bound_to)
    truth = set(candidate_refs(view))
    offered = index_of(entities).candidates(incoming)
    # Never misses a candidate.
    assert truth <= offered, (incoming.title, truth - offered)
    # And the decision is the resolver's, whole: kind, target, assessments, vetoes,
    # explanation, relationships.
    blocked = blocked_view(view, overrides)
    assert resolve(blocked, overrides) == resolve(view, overrides), incoming.title
    assert evaluation_fingerprint(blocked, overrides) == evaluation_fingerprint(view, overrides)
    return truth, offered


def test_a_title_with_any_number_of_numbers_is_keyed_and_never_assessed_against_everything() -> (
    None
):
    """The old index tried every way of dropping a title's numbers and gave up above ten."""
    numbers = " ".join(str(n) for n in range(3, 40))  # 37 distinct numbers
    base = made("1", f"Odd Title {numbers}")
    edition = made("2", f"Odd Title {numbers} Deluxe Edition", source="steam_catalogue")
    other = made("3", "Something Else Entirely")
    different_numbers = made("4", f"Odd Title {' '.join(str(n) for n in range(3, 39))}")
    entities = entities_of((base,), (other,), (different_numbers,))
    # A. The crowded title finds exactly its own entity, by lookup.
    truth, offered = check(edition, entities)
    assert truth == {"e1"} == offered
    assert index_of(entities).candidates(edition) is not None
    view = ResolverInput(incoming=edition, entities=entities)
    blocked = blocked_view(view)
    assert blocked is not view and [e.ref for e in blocked.entities] == ["e1"]
    # One number fewer is a different title: same words, not a variant.
    truth, offered = check(made("5", f"Odd Title {numbers} 40"), entities)
    assert truth == set() == offered
    # P. An index with nothing in it offers nothing, and still does not fall back.
    assert CandidateIndex().candidates(edition) == set()
    assert blocked_view(ResolverInput(incoming=edition, entities=())).entities == ()
    # In a whole build such titles are no longer scanned against every entity.
    stats = GenerationStats()
    generate_catalogue_blocked(
        [base, edition, other, different_numbers],
        as_of=AS_OF,
        id_factory=counting_ids(),
        stats=stats,
        cross_check=True,
    )
    assert stats.full_scans == 0 and stats.cross_checked == stats.resolver_calls > 0


def test_numbers_that_are_and_are_not_release_years() -> None:
    # B. Many numbers, exactly one of which is the OTHER record's release year.
    remake = made("10", "Citadel 4 8 15 16 23 42 1998 2003 2011 77 91 12", years=(2011,))
    original = made("11", "Citadel 4 8 15 16 23 42 1998 2003 77 91 12", years=(2011,))
    wrong_year = made("12", "Citadel 4 8 15 16 23 42 1998 2003 77 91 12", years=(2010,))
    no_year = made("13", "Citadel 4 8 15 16 23 42 1998 2003 77 91 12")
    truth, offered = check(remake, entities_of((original,), (wrong_year,), (no_year,)))
    # Only the record whose own release year is the extra number is a packaging variant.
    assert truth == {"e1"} == offered

    # C. Several tokens that look like years; the other record released in two of them.
    incoming = made("20", "Harbor 1999 2004 2016", source="steam_catalogue")
    both = made("21", "Harbor 2016", years=(1999, 2004))
    one = made("22", "Harbor 2004 2016", years=(1999,))
    none_dropped = made("23", "Harbor 1999 2004 2016", years=(1987,))
    too_many = made("24", "Harbor", years=(1999, 2004))  # 2016 is not one of its years
    all_three = made("25", "Harbor", years=(1999, 2004, 2016))
    entities = entities_of((both,), (one,), (none_dropped,), (too_many,), (all_three,))
    truth, offered = check(incoming, entities)
    assert truth == {"e1", "e2", "e3", "e5"} == offered

    # D. Numbers that are not years at all: a sequel numeral never drops out...
    sequel = made("30", "Lantern 2", source="steam_catalogue")
    first = made("31", "Lantern", years=(2015,))
    truth, offered = check(sequel, entities_of((first,)))
    assert truth == set() == offered
    # ...unless the other record really was released in that "year".
    ancient = made("32", "Lantern", years=(2,))
    truth, offered = check(sequel, entities_of((ancient,)))
    assert truth == {"e1"} == offered
    # A number written two ways is one number to the resolver, and to the index.
    padded = made("33", "Lantern 02016", source="steam_catalogue")
    released = made("34", "Lantern", years=(2016,))
    truth, offered = check(padded, entities_of((released,)))
    assert truth == {"e1"} == offered
    # A title that is nothing but numbers, among others like it.
    digits = made("35", "2048 1942", source="steam_catalogue")
    rivals = entities_of(
        (made("36", "2048", years=(1942,)),),
        (made("37", "1942", years=(2048,)),),
        (made("38", "2048 1942", years=(2001,)),),
        (made("39", "1942 2048", years=(2001,)),),
        (made("40", "7", years=(2048, 1942)),),
    )
    truth, offered = check(digits, rivals)
    assert truth == {"e1", "e2", "e3"} == offered
    # A year the incoming title repeats is dropped everywhere it appears.
    doubled = made("41", "Echo 2016 Returns 2016", source="steam_catalogue")
    truth, offered = check(doubled, entities_of((made("42", "Echo Returns", years=(2016,)),)))
    assert truth == {"e1"} == offered


def test_titles_shared_by_different_games_are_all_offered_and_none_invented() -> None:
    # E. Exact and alternative titles, in both directions.
    known = made("50", "Verdant Reach", alternatives=("VR: Origins", "Verdant Reach I"))
    by_alternative = made("51", "VR Origins", source="steam_catalogue")
    truth, offered = check(by_alternative, entities_of((known,)))
    assert truth == {"e1"} == offered
    lists_it = made("52", "Some Other Name", alternatives=("Verdant Reach",))
    truth, offered = check(lists_it, entities_of((known,)))
    assert truth == {"e1"} == offered
    # F. The same normalised title on distinct games: every one is a candidate. The
    # index does not choose between them; the resolver does.
    twins = entities_of(
        (made("60", "Prey", years=(2006,)),),
        (made("61", "PREY", years=(2017,)),),
        (made("62", "Prey!", years=(2030,)),),
        (made("63", "Pray", years=(2017,)),),
    )
    truth, offered = check(made("64", "prey", source="steam_catalogue"), twins)
    assert truth == {"e1", "e2", "e3"} == offered
    assert (
        resolve(
            ResolverInput(incoming=made("64", "prey", source="steam_catalogue"), entities=twins)
        ).kind
        is DecisionKind.QUARANTINE
    )
    # G. Sequel numbering and series siblings are not candidates by title, in Arabic
    # or Roman numerals, and are not offered.
    series = entities_of(
        (made("70", "Ironvale"),), (made("71", "Ironvale 2"),), (made("72", "Ironvale III"),)
    )
    truth, offered = check(made("73", "Ironvale 4", source="steam_catalogue"), series)
    assert truth == set() == offered
    truth, offered = check(made("74", "Ironvale II", source="steam_catalogue"), series)
    assert truth == {"e2"} == offered  # "II" is 2: the same title
    # H. Remakes, remasters and packaging variants.
    base = made("80", "Mossgate", years=(2009,))
    variants = [
        made("81", "Mossgate: Game of the Year Edition", source="steam_catalogue"),
        made("82", "Mossgate - The Complete Edition", source="steam_catalogue"),
        made("83", "Mossgate (2009)", source="steam_catalogue"),
        made("84", "Mossgate Remastered", kind=ProductType.REMASTER),
        made("85", "Mossgate HD Deluxe 2009", source="steam_catalogue"),
    ]
    for variant in variants:
        truth, offered = check(variant, entities_of((base,)))
        assert truth == {"e1"} == offered, variant.title
    # A rework word never makes a different title match, and a wrong year does not drop.
    for stranger in (made("86", "Mossgate Reborn"), made("87", "Mossgate (2010)")):
        truth, offered = check(stranger, entities_of((base,)))
        assert truth == set() == offered, stranger.title
    # A title that is only packaging words has an empty core, and matches nothing by it.
    empty_core = made("88", "Deluxe Edition", source="steam_catalogue")
    truth, offered = check(empty_core, entities_of((made("89", "Gold Edition"),), (base,)))
    assert truth == set() == offered


def test_links_overrides_and_bindings_are_offered_whatever_the_titles_say() -> None:
    game = made("100", "Thornfield", external=(("steam", "9001"),))
    # I. A record asserting another source's id, in each direction, with unrelated titles.
    app = made("9001", "Completely Different Name", source="steam_catalogue")
    truth, offered = check(app, entities_of((game,)))
    assert truth == {"e1"} == offered
    truth, offered = check(game, entities_of((app,)))
    assert truth == {"e1"} == offered
    # J. A recognised store URL, alone, corroborated, and claimed by two games at once.
    by_url = made("101", "Thornfield", websites=("https://store.steampowered.com/app/9002/x",))
    target = made("9002", "Unrelated Words", source="steam_catalogue")
    truth, offered = check(target, entities_of((by_url,)))
    assert truth == {"e1"} == offered
    edition = made("102", "Thornfield Gold", external=(("steam", "9002"),), version_parent=by_url)
    check(target, entities_of((by_url,)), context=(edition,))
    rival = made("103", "Another Game", websites=("https://store.steampowered.com/app/9002/y",))
    truth, offered = check(target, entities_of((by_url,), (rival,)))
    assert truth == {"e1", "e2"} == offered
    # K. Parent and version-parent links.
    dlc = made("104", "Nothing In Common", kind=ProductType.DLC, parent=game)
    truth, offered = check(dlc, entities_of((game,), (target,)))
    assert truth == {"e1"} == offered
    goty = made("105", "Also Nothing In Common", version_parent=game)
    truth, offered = check(goty, entities_of((game,), (target,)))
    assert truth == {"e1"} == offered
    # L. Conflicting evidence: an id link to a record whose title disagrees is still a
    # candidate, and the resolver quarantines it. The index does not pre-judge.
    mismatched = made("9001", "Thornfield Racing Simulator 3", source="steam_catalogue")
    view = ResolverInput(incoming=mismatched, entities=entities_of((game,)))
    truth, offered = check(mismatched, entities_of((game,)))
    assert truth == {"e1"} == offered
    assert resolve(view).kind is DecisionKind.QUARANTINE
    # M. An explicit override names its entities by reference; they are offered though
    # nothing makes them candidates.
    orphan = made("106", "No Evidence At All")
    overrides = OverrideSet(
        version=99,
        overrides=(
            IdentityOverride(
                ref="synthetic:override",
                record=orphan.key,
                action=OverrideAction.BIND_TO_ENTITY,
                canonical_game_id=make_id(1),
                reason="synthetic",
                added_in_version=99,
            ),
        ),
    )
    named = (
        EntityView(ref=make_id(1), records=(target,)),
        EntityView(ref=make_id(2), records=(game,)),
    )
    assert index_of(named).candidates(orphan) == set()
    wanted = wanted_refs(orphan, index_of(named), overrides)
    assert wanted == {make_id(1)}
    blocked = blocked_view(ResolverInput(incoming=orphan, entities=named), overrides)
    assert [e.ref for e in blocked.entities] == [make_id(1)]
    assert resolve(blocked, overrides) == resolve(
        ResolverInput(incoming=orphan, entities=named), overrides
    )
    # N. A record already bound elsewhere keeps that entity on offer.
    check(orphan, entities_of((target,), (game,)), bound_to="e2")
    assert wanted_refs(orphan, index_of(entities_of((target,), (game,))), NO_OVERRIDES, "e2") == {
        "e2"
    }
    # O. No useful key at all: nothing is offered, and nothing is a candidate.
    truth, offered = check(made("107", "Zzyzx Unrelated"), entities_of((game,), (target,)))
    assert truth == set() == offered


def test_the_index_agrees_with_the_resolver_on_generated_title_combinations() -> None:
    """Q. Systematic and seeded: thousands of questions, each asked both ways."""
    chooser = random.Random(20261013)
    words = ["ashen", "vale", "iron", "tide", "the", "of", "echo"]
    extras = ["deluxe", "edition", "gold", "remastered", "hd", "collection", "game of the year"]
    numbers = ["2", "3", "II", "7", "12", "64", "1998", "2004", "2016", "2023", "02016", "99"]

    def title() -> str:
        parts = chooser.sample(words, chooser.randint(1, 2))
        parts += chooser.sample(numbers, chooser.randint(0, 5))
        if chooser.random() < 0.15:  # numeric-heavy, far beyond the old limit of ten
            parts += [str(n) for n in chooser.sample(range(100, 140), 14)]
        chooser.shuffle(parts)
        parts += chooser.sample(extras, chooser.randint(0, 2))
        return " ".join(parts)

    def years() -> tuple[int, ...]:
        pool = (2, 3, 7, 12, 64, 99, 1998, 2004, 2016, 2023, 110, 125)
        return tuple(chooser.sample(pool, chooser.randint(0, 3)))

    records = []
    for number in range(420):
        source = "igdb" if number % 2 else "steam_catalogue"
        other = "steam" if source == "igdb" else "igdb"
        links = ((other, str(chooser.randint(0, 419))),) if chooser.random() < 0.2 else ()
        alternatives = (title(),) if chooser.random() < 0.2 else ()
        site = (
            (f"https://store.steampowered.com/app/{chooser.randint(0, 419)}/x",)
            if source == "igdb" and chooser.random() < 0.1
            else ()
        )
        records.append(
            made(
                str(number),
                title(),
                source=source,
                years=years(),
                alternatives=alternatives,
                external=links,
                websites=site,
            )
        )
    held, incoming = records[:300], records[300:]
    groups: list[list[SourceRecord]] = []
    for record in held:
        if groups and chooser.random() < 0.3:
            chooser.choice(groups).append(record)
        else:
            groups.append([record])
    entities = tuple(
        EntityView(ref=f"e{number}", records=tuple(group)) for number, group in enumerate(groups)
    )
    index = index_of(entities)
    asked = with_candidates = by_packaging = offered_total = 0
    for record in incoming:
        view = ResolverInput(incoming=record, entities=entities)
        truth = set(candidate_refs(view))
        offered = index.candidates(record)
        assert offered is not None
        assert truth <= offered, (record.title, truth - offered)
        blocked = blocked_view(view)
        assert resolve(blocked) == resolve(view), record.title
        # The packaging row is exact, not merely a superset: every entity it returns is
        # one the resolver relates by title.
        for ref in index.packaging_variants(normalize_title(record.title).core_tokens):
            assert ref in truth, (record.title, ref)
            by_packaging += 1
        asked += 1
        with_candidates += bool(truth)
        offered_total += len(offered)
    assert asked == 120 and with_candidates > 20 and by_packaging > 10
    # Selective: a small fraction of the entities, not all of them.
    assert offered_total < asked * len(entities) / 20
    # A whole build over all of it, with every question re-asked the all-pairs way.
    stats = GenerationStats()
    fast = generate_catalogue_blocked(
        records, as_of=AS_OF, id_factory=counting_ids(), stats=stats, cross_check=True
    )
    slow = generate_catalogue(records, as_of=AS_OF, id_factory=counting_ids())
    assert fast == slow and fast.model_dump_json() == slow.model_dump_json()
    assert stats.full_scans == 0 and stats.cross_checked == stats.resolver_calls > 400


def test_the_packaging_bucket_is_keyed_on_a_necessary_condition() -> None:
    """Why the lookup cannot miss: dropping numbers never changes a title's words."""
    chooser = random.Random(7)
    tokens_pool = ["ashen", "vale", "2", "1998", "iron", "02016", "7", "the", "64"]
    for _ in range(2_000):
        tokens = tuple(chooser.choices(tokens_pool, k=chooser.randint(0, 7)))
        dropped = set(chooser.sample((2, 7, 64, 1998, 2016), chooser.randint(0, 5)))
        after = _without(tokens, frozenset(), dropped)
        # Whatever is dropped, the words are the same...
        assert number_free(after) == number_free(tokens)
        # ...so two cores made equal by dropping numbers always share a bucket key.
        assert number_free(_without(tokens, REWORK_WORDS, dropped)) == number_free(
            _without(tokens, REWORK_WORDS, set())
        )
    assert number_free(("a", "2", "b", "1998")) == ("a", "b")
    assert number_free(("2048",)) == () and number_free(()) == ()


# --- the 10,000-record proof (minutes; run explicitly) ------------------------------------


@pytest.mark.slow
def test_the_blocked_build_equals_the_reference_on_ten_thousand_records(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sample = stratified_sample(10_000)
    records = [*recorded("catalogue"), *sample.records]
    slow, fast, stats = both(records)
    assert fast == slow
    assert fast.model_dump_json() == slow.model_dump_json()
    kinds = Counter(d.decision.kind.value for d in fast.decisions.decisions)
    with capsys.disabled():
        print(f"\nrecords={len(records)} strata={len(sample.counts())} decisions={dict(kinds)}")
        print(f"entities={len(fast.catalogue.games)} bindings={len(fast.mapping.bindings)}")
        print(f"quarantined={len(fast.quarantine.entries)} stats={stats}")
