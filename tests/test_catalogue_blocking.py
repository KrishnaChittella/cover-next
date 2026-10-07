"""Blocked, incremental resolution must decide exactly what all-pairs resolution decides.

Three levels of proof, each stricter about a different thing:

    the index     never misses an entity the resolver would treat as a candidate
    one decision  resolving a blocked view gives the identical decision, on every
                  golden case and at every decision point of a catalogue build
    a whole build the blocked engine returns a CatalogueState equal to the one the
                  untouched `generate_catalogue` returns: every binding, every logged
                  decision, every quarantine entry and fingerprint, every id
"""

import uuid
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from metaspike.catalogue.generate import CatalogueState, generate_catalogue
from metaspike.catalogue.incremental import (
    BlockingMismatch,
    GenerationStats,
    generate_catalogue_blocked,
)
from metaspike.catalogue.overrides import NO_OVERRIDES, PRODUCTION_OVERRIDES
from metaspike.domain.source_records import ExternalId, SourceRecord
from metaspike.resolution import blocking
from metaspike.resolution.blocking import MAX_TITLE_NUMBERS, CandidateIndex, blocked_view
from metaspike.resolution.evaluate import evaluate
from metaspike.resolution.golden import GoldenSet, load_golden_set
from metaspike.resolution.models import DecisionKind, EntityView, ResolverInput
from metaspike.resolution.quarantine import evaluation_fingerprint
from metaspike.resolution.recorded import load_recorded_catalogue
from metaspike.resolution.resolver import RESOLVER_VERSION, candidate_refs, resolve
from tests.catalogue_samples import SHAPES, stratified_sample

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
        if offered is None:
            continue  # the record is assessed against everything; nothing can be missed
        assert truth <= offered, record.key
        # And it is selective: a superset, not everything.
        assert len(offered) <= len(truth) + 8, record.key
    assert checked > 300 and with_candidates > 200


def test_a_title_with_too_many_numbers_falls_back_to_assessing_everything() -> None:
    numbers = " ".join(str(n) for n in range(MAX_TITLE_NUMBERS + 1))
    base = recorded("catalogue")[0]
    crowded = base.model_copy(update={"title": f"Odd Title {numbers}"})
    assert CandidateIndex().candidates(crowded) is None
    fine = base.model_copy(update={"title": "Odd Title 1 2 3"})
    assert CandidateIndex().candidates(fine) == set()
    view = ResolverInput(incoming=crowded, entities=(EntityView(ref="e1", records=(base,)),))
    assert blocked_view(view) is view


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
    assert "from metaspike.resolution.resolver import _EXTERNAL_NAME, _without" in source


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
    assert stats.full_scans > 0  # the fallback path was exercised and still agreed
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
