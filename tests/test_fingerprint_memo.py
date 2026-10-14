"""Keeping content hashes for a run changes how often they are computed, and nothing else.

Three levels, each against something that was not changed:

    a hash          `ContentHashMemo.of(record)` is `record.content_hash`, always
    a fingerprint   the current `evaluation_fingerprint`, with and without a memo, gives
                    the same string as the original function kept verbatim in
                    `tests/fingerprint_reference.py`
    a build         the blocked engine, which keeps hashes, returns the same state as
                    `generate_catalogue`, which does not, on a first build and on every
                    continuation

RECORDED inputs are the catalogue fixtures. Records built by hand or by
`catalogue_samples` are SYNTHETIC and say so.
"""

import gc
import inspect
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from metaspike.catalogue import incremental
from metaspike.catalogue.generate import CatalogueState, generate_catalogue
from metaspike.catalogue.incremental import GenerationStats, generate_catalogue_blocked
from metaspike.catalogue.overrides import (
    NO_OVERRIDES,
    PRODUCTION_OVERRIDES,
    IdentityOverride,
    OverrideAction,
    OverrideSet,
)
from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.source_policy import DeploymentStage
from metaspike.domain.source_records import (
    ContentHashMemo,
    DatePrecision,
    ExternalId,
    ProductType,
    ReleaseDate,
    SourceRecord,
)
from metaspike.resolution import quarantine
from metaspike.resolution.models import DecisionKind, EntityView, ResolverInput
from metaspike.resolution.quarantine import evaluation_fingerprint
from metaspike.resolution.recorded import load_recorded_catalogue
from metaspike.sources.registry import SourcePolicyError
from tests import fingerprint_reference as reference
from tests.catalogue_samples import stratified_sample
from tests.catalogue_scale import (
    CATALOGUE_POLICIES,
    counting_content_hashes,
    counting_ids,
    evaluate_synthetic,
)
from tests.conftest import make_id

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
AS_OF = datetime(2026, 10, 6, tzinfo=UTC)
FETCHED = datetime(2026, 10, 5, 2, 0, tzinfo=UTC)
DAY = timedelta(days=1)


def recorded(name: str = "catalogue") -> list[SourceRecord]:
    return list(load_recorded_catalogue(FIXTURES / name).values())


# SYNTHETIC records.


def game(
    identifier: str,
    title: str,
    *,
    steam: tuple[str, ...] = (),
    year: int = 2019,
    kind: ProductType = ProductType.MAIN_GAME,
    parent: SourceRecord | None = None,
    version_parent: SourceRecord | None = None,
    websites: tuple[str, ...] = (),
    studio: str = "Synthetic Studio",
) -> SourceRecord:
    return SourceRecord(
        key=SourceRecordKey(source="igdb", source_id=identifier),
        title=title,
        product_type=kind,
        release_dates=(ReleaseDate(starts_on=date(year, 3, 1), precision=DatePrecision.DAY),),
        developers=(studio,),
        external_ids=tuple(ExternalId(source="steam", value=value) for value in steam),
        websites=websites,
        parent=None if parent is None else parent.key,
        version_parent=None if version_parent is None else version_parent.key,
        fetched_at=FETCHED,
    )


def app(identifier: str, title: str) -> SourceRecord:
    return SourceRecord(
        key=SourceRecordKey(source="steam_catalogue", source_id=identifier),
        title=title,
        product_type=ProductType.UNKNOWN,
        source_category="games",
        fetched_at=FETCHED,
    )


class Engines:
    """Run the blocked engine and the reference engine side by side, step after step."""

    def __init__(self, overrides: OverrideSet = PRODUCTION_OVERRIDES) -> None:
        self.overrides = overrides
        self.fast: CatalogueState | None = None
        self.slow: CatalogueState | None = None
        self.stats = GenerationStats()
        self._step = 0

    def run(
        self,
        records: list[SourceRecord],
        at: datetime = AS_OF,
        overrides: OverrideSet | None = None,
    ) -> CatalogueState:
        if overrides is not None:
            self.overrides = overrides
        self._step += 1
        start = 10_000 * self._step
        self.stats = GenerationStats()
        self.fast = generate_catalogue_blocked(
            records,
            as_of=at,
            overrides=self.overrides,
            id_factory=counting_ids(start),
            previous=self.fast,
            stats=self.stats,
            cross_check=True,  # every question and fingerprint re-asked without a memo
        )
        self.slow = generate_catalogue(
            records,
            as_of=at,
            overrides=self.overrides,
            id_factory=counting_ids(start),
            previous=self.slow,
        )
        # The whole state: ids, bindings, decision log in order, quarantine entries with
        # their fingerprints, evaluation counts and dates, related products, lifecycle.
        assert self.fast == self.slow
        assert self.fast.model_dump_json() == self.slow.model_dump_json()
        return self.fast

    def held(self, record: SourceRecord) -> quarantine.QuarantineEntry | None:
        assert self.fast is not None
        return self.fast.quarantine.get(record.key)

    def bound(self, record: SourceRecord) -> str | None:
        assert self.fast is not None
        return self.fast.mapping.canonical_for(record.key)


# --- a hash ----------------------------------------------------------------------------


def test_a_kept_hash_is_the_record_s_own_hash_computed_once() -> None:
    record = game("9600001", "Amberwick")
    memo = ContentHashMemo()
    # A. The same record, asked for again and again.
    with counting_content_hashes() as computed:
        answers = {memo.of(record) for _ in range(500)}
    assert answers == {record.content_hash}
    assert computed[0] == 1  # five hundred requests, one computation
    assert (memo.requested, memo.computed, len(memo)) == (500, 1, 1)
    assert len(memo.of(record)) == 64 and int(memo.of(record), 16) >= 0  # a SHA-256 hex digest

    # B. Re-fetched, unchanged: a new object, computed afresh, and the same hash,
    # because the fetch time is not part of what a source said.
    refetched = record.model_copy(update={"fetched_at": FETCHED + 5 * DAY})
    assert refetched is not record and refetched.fetched_at != record.fetched_at
    assert memo.of(refetched) == memo.of(record) == refetched.content_hash
    assert memo.computed == 2

    # C, D, E. The same source id saying something different. Never the older answer.
    changed = {
        "an external id": record.model_copy(
            update={"external_ids": (ExternalId(source="steam", value="42"),)}
        ),
        "the title": record.model_copy(update={"title": "Amberwick II"}),
        "a release date": record.model_copy(
            update={
                "release_dates": (
                    ReleaseDate(starts_on=date(2020, 3, 1), precision=DatePrecision.DAY),
                )
            }
        ),
        "a parent link": record.model_copy(
            update={"parent": SourceRecordKey(source="igdb", source_id="1")}
        ),
        "a version parent": record.model_copy(
            update={"version_parent": SourceRecordKey(source="igdb", source_id="2")}
        ),
        "a developer": record.model_copy(update={"developers": ("Someone Else",)}),
    }
    seen = {memo.of(record)}
    for what, other in changed.items():
        assert other.key == record.key, what  # the same source-native id
        assert memo.of(other) == other.content_hash, what
        assert memo.of(other) not in seen, what
        seen.add(memo.of(other))
    # The original is still answered as itself afterwards.
    assert memo.of(record) == record.content_hash
    # Two equal records that are different objects each get their own, equal, answer.
    twin = game("9600001", "Amberwick")
    assert twin == record and twin is not record
    assert memo.of(twin) == memo.of(record)

    # L. The same title under different source ids is different content.
    assert memo.of(game("9600002", "Amberwick")) != memo.of(record)
    assert memo.of(app("9600001", "Amberwick")) != memo.of(record)


def test_the_memo_can_never_answer_for_a_different_record() -> None:
    """Keyed by object, and holding the object, so an address is never reused under it."""
    memo = ContentHashMemo()
    base = game("9600010", "Brindlecombe")
    # Thousands of short-lived records. If an entry could outlive its record and be read
    # for a later one at the same address, some answer here would be another's.
    for number in range(3_000):
        temporary = base.model_copy(update={"title": f"Brindlecombe {number}"})
        assert memo.of(temporary) == temporary.content_hash
        del temporary
        if number % 500 == 0:
            gc.collect()
    assert memo.computed == 3_000 == len(memo)
    # Every record it has seen is still alive inside it.
    assert all(held[0].content_hash == held[1] for held in memo._held.values())
    assert all(key == id(held[0]) for key, held in memo._held.items())
    # Nothing about a record is the key: no source id, no title, no field.
    code = inspect.getsource(ContentHashMemo)
    assert "id(record)" in code and "held[0] is record" in code
    for forbidden in (".key", "source_id", ".title", "lru_cache", "global ", "weakref"):
        assert forbidden not in code.split('"""')[2], forbidden
    # K, O. It is one run's, not the process's: nothing module-level holds one.
    for module in (incremental, quarantine):
        assert not [
            name for name, value in vars(module).items() if isinstance(value, ContentHashMemo)
        ]
    engine = inspect.getsource(generate_catalogue_blocked)
    assert engine.count("ContentHashMemo()") == 1  # made inside the run, once per run
    # The content hash itself is untouched: same fields, fetch time excluded, SHA-256.
    hashed = inspect.getsource(SourceRecord.content_hash.fget)  # type: ignore[attr-defined]
    assert 'exclude={"fetched_at"}' in hashed and "sha256" in hashed
    assert 'sort_keys=True, separators=(",", ":")' in hashed


# --- a fingerprint ---------------------------------------------------------------------


def three_ways(view: ResolverInput, overrides: OverrideSet = NO_OVERRIDES) -> str:
    """The fingerprint from the original function, and from the current one twice."""
    original = reference.evaluation_fingerprint(view, overrides)
    plain = evaluation_fingerprint(view, overrides)
    memo = ContentHashMemo()
    kept = evaluation_fingerprint(view, overrides, memo)
    again = evaluation_fingerprint(view, overrides, memo)  # answered from the memo
    assert original == plain == kept == again
    assert len(original) == 64
    return original


def test_the_fingerprint_is_the_same_string_and_moves_with_every_kind_of_evidence() -> None:
    base = game("9601000", "Cinderwake", steam=("9601900",))
    edition = game("9601001", "Cinderwake Gold", version_parent=base)
    other = game("9601002", "Dunholm")
    incoming = app("9601900", "Cinderwake")
    entities = (
        EntityView(ref=make_id(1), records=(base,)),
        EntityView(ref=make_id(2), records=(other,)),
    )
    view = ResolverInput(incoming=incoming, entities=entities)
    start = three_ways(view)
    seen = {start}

    def moved(changed: ResolverInput, overrides: OverrideSet = NO_OVERRIDES) -> None:
        value = three_ways(changed, overrides)
        assert value not in seen
        seen.add(value)

    def same(unchanged: ResolverInput) -> None:
        assert three_ways(unchanged) == start

    # B. Unchanged evidence, re-fetched: the same fingerprint.
    later = FETCHED + 9 * DAY
    same(view.model_copy(update={"incoming": incoming.model_copy(update={"fetched_at": later})}))
    same(
        view.model_copy(
            update={
                "entities": (
                    EntityView(
                        ref=make_id(1), records=(base.model_copy(update={"fetched_at": later}),)
                    ),
                    entities[1],
                )
            }
        )
    )
    # An entity that is not a candidate changing does not move it.
    same(
        view.model_copy(
            update={
                "entities": (
                    entities[0],
                    EntityView(
                        ref=make_id(2),
                        records=(other.model_copy(update={"title": "Dunholm Remade"}),),
                    ),
                )
            }
        )
    )
    # The order records and entities arrive in does not move it.
    same(view.model_copy(update={"entities": entities[::-1]}))

    # D. The incoming record's own content.
    moved(view.model_copy(update={"incoming": incoming.model_copy(update={"title": "Cinderwak"})}))

    def with_candidate(*records: SourceRecord) -> ResolverInput:
        return view.model_copy(
            update={"entities": (EntityView(ref=make_id(1), records=records), entities[1])}
        )

    # C, E, G. One field of a candidate's record: an external id, a release date, a link.
    more_ids = (*base.external_ids, ExternalId(source="steam", value="7"))
    moved(with_candidate(base.model_copy(update={"external_ids": more_ids})))
    moved(
        with_candidate(
            base.model_copy(
                update={
                    "release_dates": (
                        ReleaseDate(starts_on=date(2021, 1, 1), precision=DatePrecision.DAY),
                    )
                }
            )
        )
    )
    moved(with_candidate(base.model_copy(update={"parent": other.key})))
    moved(with_candidate(base.model_copy(update={"version_parent": other.key})))
    # F. A candidate entity gains a record, and loses one.
    moved(with_candidate(base, edition))
    moved(with_candidate(edition.model_copy(update={"external_ids": base.external_ids})))
    # The set of candidates itself: the same record held by a second entity.
    moved(
        view.model_copy(
            update={
                "entities": (*entities, EntityView(ref=make_id(3), records=(base,))),
            }
        )
    )
    # The candidate under a different entity id.
    moved(
        view.model_copy(
            update={"entities": (EntityView(ref=make_id(4), records=(base,)), entities[1])}
        )
    )
    # G, K. Linking context: an unresolved record that names the incoming one.
    claimant = game("9601003", "Cinderwake Deluxe", steam=("9601900",), version_parent=base)
    moved(view.model_copy(update={"context": (claimant,)}))
    moved(
        view.model_copy(
            update={"context": (claimant.model_copy(update={"title": "Cinderwake Deluxe II"}),)}
        )
    )
    # Context that does not link to the incoming record is not evidence.
    same(view.model_copy(update={"context": (other,)}))
    # E. An existing binding target.
    moved(view.model_copy(update={"bound_to": make_id(1)}))
    moved(view.model_copy(update={"bound_to": make_id(2)}))

    # M. An override: applied, a different one, a newer version of it, and removed.
    def override(ref: str, version: int) -> OverrideSet:
        return OverrideSet(
            version=version,
            overrides=(
                IdentityOverride(
                    ref=ref,
                    record=incoming.key,
                    action=OverrideAction.BIND_TO_ENTITY,
                    canonical_game_id=make_id(1),
                    reason="synthetic",
                    added_in_version=version,
                ),
            ),
        )

    moved(view, override("synthetic:a", 1))
    moved(view, override("synthetic:b", 1))
    moved(view, override("synthetic:a", 2))
    assert three_ways(view, OverrideSet(version=7)) == start  # an override for nobody
    assert len(seen) == 17  # the starting point and sixteen distinct changes


def test_every_fingerprint_a_real_build_computes_equals_the_original() -> None:
    """Each fingerprint the engine actually asks for, re-derived by the original function."""
    compared: list[int] = []
    current = evaluation_fingerprint
    assert incremental.__dict__["evaluation_fingerprint"] is current

    def checked(
        view: ResolverInput, overrides: OverrideSet, hashes: ContentHashMemo | None = None
    ) -> str:
        value: str = current(view, overrides, hashes)
        assert value == reference.evaluation_fingerprint(view, overrides)
        assert value == current(view, overrides)  # and without a memo
        compared.append(1)
        return value

    patch = pytest.MonkeyPatch()
    patch.setattr(incremental, "evaluation_fingerprint", checked)
    try:
        # P. The recorded fixtures: a first build, then a continuation with nothing new.
        records = recorded()
        first = generate_catalogue_blocked(records, as_of=AS_OF, id_factory=counting_ids())
        on_first = len(compared)
        generate_catalogue_blocked(
            records, as_of=AS_OF + DAY, id_factory=counting_ids(90_000), previous=first
        )
        assert on_first > 40 and len(compared) > on_first
        # Q. SYNTHETIC: a sample whose entities hold many records each.
        sample = [*records, *stratified_sample(1_500).records]
        state = generate_catalogue_blocked(sample, as_of=AS_OF, id_factory=counting_ids())
        many = max(
            sum(1 for b in state.mapping.bindings if b.canonical_game_id == game_id)
            for game_id in {b.canonical_game_id for b in state.mapping.bindings}
        )
        assert many >= 3
        # A continuation in which half the sample is new.
        half = generate_catalogue_blocked(sample[::2], as_of=AS_OF, id_factory=counting_ids())
        generate_catalogue_blocked(
            sample, as_of=AS_OF + DAY, id_factory=counting_ids(90_000), previous=half
        )
    finally:
        patch.undo()
    assert len(compared) > 2_500


# --- a build ---------------------------------------------------------------------------


def test_recorded_and_synthetic_builds_are_identical_and_hash_each_record_once() -> None:
    records = recorded()
    engines = Engines()
    with counting_content_hashes() as computed:
        engines.run(records)
    stats = engines.stats
    # The engine asked for far more hashes than it had to compute...
    assert stats.content_hashes_requested > stats.content_hashes_computed > 0
    # ...and computed at most one per record: those it was given, and the bootstrap
    # entities' own records where one was a candidate.
    assert stats.content_hashes_computed <= len(records) + 15
    assert computed[0] > stats.content_hashes_computed  # the reference engine kept none
    assert stats.cross_checked == stats.resolver_calls
    # P. The frozen recorded result.
    assert engines.fast is not None
    assert len(engines.fast.catalogue.games) == 234
    assert len(engines.fast.quarantine.entries) == 44
    assert len(engines.fast.related_products) == 23
    assert sum(1 for b in engines.fast.mapping.bindings if b.valid_to is None) == 327
    # H, N. Continued with the same records: nothing is decided again, by either engine.
    before = engines.fast.quarantine
    engines.run(records, AS_OF + DAY)
    assert engines.stats.resolver_calls == 0
    assert engines.stats.skipped_unchanged_quarantine == engines.stats.fingerprint_checks > 40
    assert engines.fast.quarantine == before  # evaluations, dates and fingerprints as they were
    # Re-fetched, with nothing a source said changed: still nothing decided again.
    refetched = [r.model_copy(update={"fetched_at": FETCHED + 3 * DAY}) for r in records]
    engines.run(refetched, AS_OF + 4 * DAY)
    assert engines.stats.resolver_calls == 0
    assert [e.evaluations for e in engines.fast.quarantine.entries] == [
        e.evaluations for e in before.entries
    ]

    # Q. SYNTHETIC multi-record entities: a first build, then a continuation.
    sample = [*records, *stratified_sample(900).records]
    synthetic = Engines()
    synthetic.run(sample[::2])
    synthetic.run(sample, AS_OF + DAY)
    assert synthetic.stats.content_hashes_requested > 3 * synthetic.stats.content_hashes_computed
    assert synthetic.fast is not None
    kinds = {d.decision.kind for d in synthetic.fast.decisions.decisions}
    assert kinds == set(DecisionKind)


def test_quarantine_is_re_examined_exactly_when_its_evidence_changes() -> None:
    """SYNTHETIC. Each step is run through both engines and must give the same state."""
    engines = Engines(NO_OVERRIDES)
    owner = game("9602000", "Eldergrove")
    title_only = app("9602900", "Eldergrove")  # shares a title, nothing else
    # J. An id link to a record whose title disagrees: conflicting evidence.
    claimant = game("9602001", "Fallowmere", steam=("9602901",))
    mismatched = app("9602901", "Fallowmere Racing Simulator 3")
    records = [owner, title_only, claimant, mismatched]
    engines.run(records)
    for record in (title_only, mismatched):
        held = engines.held(record)
        assert held is not None and held.evaluations == 1 and held.first_seen == AS_OF
        assert engines.bound(record) is None  # a title is never an identity
    assert engines.held(title_only).reason.value == "title_only"  # type: ignore[union-attr]
    assert engines.held(mismatched).reason.value == "id_link_title_mismatch"  # type: ignore[union-attr]

    # H. Nothing changed: skipped. The entry is exactly what it was.
    untouched = engines.held(title_only)
    engines.run(records, AS_OF + DAY)
    assert engines.stats.resolver_calls == 0
    assert engines.held(title_only) == untouched

    # L. A bound record that is a candidate changes: the quarantine it bears on reopens,
    # is examined again, and, the evidence still being ambiguous, stays.
    retitled_year = owner.model_copy(
        update={
            "release_dates": (ReleaseDate(starts_on=date(2020, 3, 1), precision=DatePrecision.DAY),)
        }
    )
    records = [retitled_year, title_only, claimant, mismatched]
    engines.run(records, AS_OF + 2 * DAY)
    again = engines.held(title_only)
    assert again is not None and again.evaluations == 2
    assert again.first_seen == AS_OF and again.last_evaluated == AS_OF + 2 * DAY
    assert again.fingerprint != untouched.fingerprint  # type: ignore[union-attr]
    assert len(again.decision_history) == 2
    assert engines.bound(title_only) is None
    # The other quarantine did not lean on that record and was not touched.
    assert engines.held(mismatched).evaluations == 1  # type: ignore[union-attr]

    # J. The conflicting pair: its candidate changes, it is examined again, and it is
    # still a conflict. Nothing lowered the bar.
    records = [
        retitled_year,
        title_only,
        claimant.model_copy(update={"developers": ("X",)}),
        mismatched,
    ]
    engines.run(records, AS_OF + 3 * DAY)
    conflict = engines.held(mismatched)
    assert conflict is not None and conflict.evaluations == 2
    assert conflict.reason.value == "id_link_title_mismatch"
    assert engines.bound(mismatched) is None

    # I, F. Corroborating id evidence arrives for the title-only record: released,
    # bound, and the history appended.
    linked = retitled_year.model_copy(
        update={"external_ids": (ExternalId(source="steam", value="9602900"),)}
    )
    records = [linked, title_only, records[2], mismatched]
    before = engines.fast
    engines.run(records, AS_OF + 4 * DAY)
    assert engines.held(title_only) is None
    assert engines.bound(title_only) == engines.bound(linked)
    assert before is not None and engines.fast is not None
    earlier = before.decisions.decisions
    assert engines.fast.decisions.decisions[: len(earlier)] == earlier
    released = engines.fast.decisions.decisions[-1]
    assert released.record == title_only.key
    assert released.decision.kind is DecisionKind.AUTO_MATCH and released.decision.path == "A"
    assert released.record_content_hash == title_only.content_hash
    assert engines.fast.mapping.bindings[: len(before.mapping.bindings)] == before.mapping.bindings
    assert engines.fast.mapping.canonical_for(title_only.key, AS_OF + 3 * DAY) is None
    # The conflict is untouched by that, and still there. No record was dropped.
    assert engines.held(mismatched) == conflict
    quarantined = {e.record.key for e in engines.fast.quarantine.entries}
    bound = {b.key for b in engines.fast.mapping.bindings if b.valid_to is None}
    assert {r.key for r in records} == quarantined | bound


def test_linking_evidence_and_overrides_reopen_a_quarantine() -> None:
    """SYNTHETIC. A store URL names an app; a later edition corroborates it."""
    engines = Engines(NO_OVERRIDES)
    base = game(
        "9603000",
        "Glenmoor",
        websites=("https://store.steampowered.com/app/9603900/Glenmoor",),
    )
    target = app("9603900", "Unrelated Listing Name")
    engines.run([base, target])
    held = engines.held(target)
    assert held is not None and held.reason.value == "unconfirmed_url_claim"
    engines.run([base, target], AS_OF + DAY)
    assert engines.stats.resolver_calls == 0  # H

    # K. An unresolved record arrives that carries the structured id for the target and
    # names the base game as its version parent. That is linking evidence: the target is
    # examined again.
    edition = game(
        "9603001",
        "Glenmoor Gold Edition",
        steam=("9603900",),
        version_parent=base,
        kind=ProductType.EXPANDED_GAME,
    )
    engines.run([base, target, edition], AS_OF + 2 * DAY)
    assert engines.fast is not None
    decided = [d for d in engines.fast.decisions.decisions if d.record == target.key]
    assert len(decided) >= 2  # looked at again because the evidence changed
    assert decided[-1].decided_at == AS_OF + 2 * DAY

    # M. An override: added for a quarantined record, it resolves it; the fingerprint
    # had to move for it to be looked at again at all.
    lonely = app("9603901", "Hartfell")
    other = Engines(NO_OVERRIDES)
    other.run([base, lonely])
    assert other.held(lonely) is not None and other.fast is not None
    entity = other.bound(base)
    assert entity is not None
    bind = OverrideSet(
        version=1,
        overrides=(
            IdentityOverride(
                ref="synthetic:bind-hartfell",
                record=lonely.key,
                action=OverrideAction.BIND_TO_ENTITY,
                canonical_game_id=entity,
                reason="synthetic",
                added_in_version=1,
            ),
        ),
    )
    other.run([base, lonely], AS_OF + DAY, bind)
    assert other.held(lonely) is None and other.bound(lonely) == entity
    assert other.fast.decisions.decisions[-1].decision.kind is DecisionKind.MANUAL_OVERRIDE
    # Unchanged afterwards: nothing is decided again.
    other.run([base, lonely], AS_OF + 2 * DAY)
    assert other.stats.resolver_calls == 0


def test_separate_runs_in_one_process_share_nothing() -> None:
    """O, J, K. Two evaluations, one after the other, with the same id saying different things."""
    first_version = game("9604000", "Ironmere")
    second_version = first_version.model_copy(update={"title": "Ironmere Reforged"})
    assert first_version.key == second_version.key
    assert first_version.content_hash != second_version.content_hash

    def logged_hash(records: list[SourceRecord]) -> str:
        state = generate_catalogue_blocked(
            records, as_of=AS_OF, overrides=NO_OVERRIDES, id_factory=counting_ids()
        )
        (decision,) = [d for d in state.decisions.decisions if d.record == first_version.key]
        return decision.record_content_hash

    # Interleaved, repeatedly: each run logs the hash of the record IT was given.
    for _ in range(3):
        assert logged_hash([first_version]) == first_version.content_hash
        assert logged_hash([second_version]) == second_version.content_hash
    # A continuation given the changed record sees the change, not the earlier answer.
    engines = Engines(NO_OVERRIDES)
    engines.run([first_version, app("9604900", "Ironmere")])
    engines.run([second_version, app("9604900", "Ironmere")], AS_OF + DAY)
    assert engines.stats.fingerprint_checks > 0
    # Two different record sets through the whole evaluation, back to back, and again:
    # the same result each time, with one hash computed per record each time.
    small, large = evaluate_synthetic(300), evaluate_synthetic(600)
    assert evaluate_synthetic(300).state == small.state
    assert evaluate_synthetic(600).state == large.state
    assert small.summary.broad_work.content_hashes_computed == 300
    assert large.summary.broad_work.content_hashes_computed == 600
    assert large.summary.broad_work.content_hashes_requested > 600


def test_nothing_else_moved() -> None:
    # R. Source policy still fails closed, for supplied and for retained sources.
    from metaspike.catalogue.evaluation import evaluate_broad_catalogue, retained_sources

    records = recorded()
    private = evaluate_broad_catalogue(
        records,
        policies=CATALOGUE_POLICIES,
        stage=DeploymentStage.PRIVATE_DEVELOPMENT,
        as_of=AS_OF,
        id_factory=counting_ids(),
    )
    assert retained_sources(private.state) == {"igdb", "steam_catalogue"}
    summary = private.summary
    assert (summary.canonical_entities, summary.entities_only_in_broad_scope) == (234, 219)
    assert (summary.bound_records, summary.quarantined_records, summary.related_products) == (
        327,
        44,
        23,
    )
    assert summary.coverage.by_source["steam_current_players"] == {
        "eligible": 90,
        "ineligible": 69,
        "unknown": 75,
    }
    assert summary.coverage.entities_with_wikimedia_mapping == 14
    assert summary.coverage.entities_without_an_eligible_source == 135
    for stage in (DeploymentStage.PRIVATE_COLLECTION, DeploymentStage.PUBLIC_LAUNCH):
        with pytest.raises(SourcePolicyError):
            evaluate_broad_catalogue(records, policies=CATALOGUE_POLICIES, stage=stage, as_of=AS_OF)
        with pytest.raises(SourcePolicyError, match="retained in the state"):
            evaluate_broad_catalogue(
                [], policies=CATALOGUE_POLICIES, stage=stage, as_of=AS_OF + DAY, previous=private
            )
    # The reference engine was not given a memo, and the cross-check re-derives every
    # fingerprint without one.
    reference_engine = inspect.getsource(generate_catalogue)
    assert "ContentHashMemo" not in reference_engine and "hashes" not in reference_engine
    slow_side = inspect.getsource(incremental._same_fingerprint)
    assert "hashes" not in slow_side
    # The fingerprint's payload is what it was: same keys, same order of construction.
    new = inspect.getsource(evaluation_fingerprint)
    old = inspect.getsource(reference.evaluation_fingerprint)
    for key in ('"record"', '"candidates"', '"linking"', '"bound_to"', '"override"'):
        assert new.count(key) == old.count(key) == 1
    tail = 'canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))'
    assert new[new.index(tail) :] == old[old.index(tail) :]
