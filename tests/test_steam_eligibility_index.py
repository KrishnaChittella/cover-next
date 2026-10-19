"""Indexed Steam eligibility gives exactly what the scanning implementation gave.

`tests/eligibility_reference.py` is the implementation as it was before it was indexed,
verbatim. Every comparison here is output for output: classification, app ids, asserted
and quarantined apps, the reason sentence, the order, and point-in-time behaviour.

RECORDED inputs are the catalogue fixtures. Anything built by hand or by
`catalogue_samples` is SYNTHETIC and says so.
"""

import inspect
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from metaspike.catalogue import eligibility as indexed
from metaspike.catalogue.coverage import summarise_coverage
from metaspike.catalogue.eligibility import SourceEligibility, bootstrap_scope
from metaspike.catalogue.generate import CatalogueState, generate_catalogue
from metaspike.catalogue.incremental import generate_catalogue_blocked
from metaspike.catalogue.legacy_ids import LEGACY_ID_TO_CANONICAL
from metaspike.domain.identity import MappingHistory, SourceRecordKey
from metaspike.domain.source_records import (
    DatePrecision,
    ExternalId,
    ProductType,
    ReleaseDate,
    SourceRecord,
)
from metaspike.resolution.quarantine import Quarantine
from metaspike.resolution.recorded import load_recorded_catalogue
from tests import eligibility_reference as reference
from tests.catalogue_samples import stratified_sample
from tests.catalogue_scale import counting_ids

FIXTURES = Path(__file__).parent / "fixtures"
AS_OF = datetime(2026, 10, 6, tzinfo=UTC)
FETCHED = datetime(2026, 10, 5, 2, 0, tzinfo=UTC)
DAY = timedelta(days=1)


def recorded(*names: str) -> list[SourceRecord]:
    return [
        record
        for name in names or ("catalogue",)
        for record in load_recorded_catalogue(FIXTURES / name).values()
    ]


def built(records: list[SourceRecord], **options: object) -> CatalogueState:
    return generate_catalogue_blocked(
        records,
        as_of=AS_OF,
        id_factory=counting_ids(),
        **options,  # type: ignore[arg-type]
    )


def same(
    state: CatalogueState,
    records: list[SourceRecord],
    as_of: datetime | None = None,
    scope: frozenset[str] | None = None,
) -> tuple[indexed.SteamEligibility, ...]:
    """Assert the two implementations agree on everything, and return the result."""
    new = indexed.steam_eligibility(state, records, as_of)
    old = reference.steam_eligibility(state, records, as_of)
    # Field for field and in the same order: state, app ids, asserted, quarantined, reason.
    assert [item.model_dump(mode="json") for item in new] == [
        item.model_dump(mode="json") for item in old
    ]
    assert [item.canonical_game_id for item in new] == [
        game.canonical_game_id for game in state.catalogue.games
    ]
    # N. And the sampling targets derived from them, with their bindings.
    for chosen in (scope, None, bootstrap_scope()):
        new_targets = indexed.sampling_targets(state, new, chosen, as_of)
        old_targets = reference.sampling_targets(state, old, chosen, as_of)
        assert [target.model_dump(mode="json") for target in new_targets] == [
            target.model_dump(mode="json") for target in old_targets
        ]
    return new


@pytest.fixture(scope="module")
def recorded_state() -> CatalogueState:
    return built(recorded())


# SYNTHETIC records for situations the fixtures do not contain.


def game(identifier: str, title: str, *steam: str, year: int = 2020) -> SourceRecord:
    return SourceRecord(
        key=SourceRecordKey(source="igdb", source_id=identifier),
        title=title,
        product_type=ProductType.MAIN_GAME,
        release_dates=(ReleaseDate(starts_on=date(year, 5, 1), precision=DatePrecision.DAY),),
        developers=("Synthetic Studio",),
        external_ids=tuple(ExternalId(source="steam", value=app) for app in steam),
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


def by_name(result: tuple[indexed.SteamEligibility, ...], name: str) -> indexed.SteamEligibility:
    return next(item for item in result if item.name == name)


# --- recorded data ---------------------------------------------------------------------


def test_the_recorded_catalogue_classifies_exactly_as_before(
    recorded_state: CatalogueState,
) -> None:
    records = recorded()
    assert len(records) == 394 and len(recorded_state.catalogue.games) == 234
    result = same(recorded_state, records)
    # A. The totals are what they were, and are not what is being relied on: the
    # comparison above is entity by entity.
    counts = {state: sum(item.state is state for item in result) for state in SourceEligibility}
    assert counts == {
        SourceEligibility.ELIGIBLE: 90,
        SourceEligibility.INELIGIBLE: 69,
        SourceEligibility.UNKNOWN: 75,
    }
    assert sum(len(item.app_ids) for item in result) == 91
    coverage = summarise_coverage(recorded_state, records)
    assert coverage.entities_with_confirmed_steam_app == 90
    assert coverage.entities_without_an_eligible_source == 135
    # B. Every bootstrap entity, by name, with its exact sentence.
    slug = {item.slug: item for item in result}
    assert set(LEGACY_ID_TO_CANONICAL.values()) <= {item.canonical_game_id for item in result}
    # C. A confirmed app.
    cs2 = slug["counter-strike-2"]
    assert (cs2.state, cs2.app_ids) == (SourceEligibility.ELIGIBLE, ("730",))
    assert cs2.reason == (
        "Steam app 730 is in Steam's list, bound to this entity, and listed as a "
        "structured external id by a record bound to it."
    )
    # F. An assertion Steam's recorded list does not confirm: never eligible.
    gta = slug["gta-v"]
    assert (gta.state, gta.app_ids, gta.asserted_unconfirmed) == (
        SourceEligibility.UNKNOWN,
        (),
        ("271590",),
    )
    # H. Bound metadata that asserts no Steam app: inapplicable.
    online = slug["gta-online"]
    assert online.state is SourceEligibility.INELIGIBLE
    assert online.reason == "The bound catalogue metadata asserts no Steam app."
    # G. Nothing bound at all.
    minecraft = slug["minecraft"]
    assert minecraft.state is SourceEligibility.UNKNOWN
    assert minecraft.reason == "No source record is bound to this entity."
    # D. An entity with more than one confirmed app keeps them separate.
    several = [item for item in result if len(item.app_ids) > 1]
    assert len(several) == 1 and len(several[0].app_ids) == 2
    targets = indexed.sampling_targets(recorded_state, result)
    own = [t for t in targets if t.canonical_game_id == several[0].canonical_game_id]
    assert [t.source_native_id for t in own] == sorted(several[0].app_ids, key=int)
    # I. Quarantined candidates are reported, and are not bindings.
    waiting = [item for item in result if item.quarantined]
    assert waiting
    for item in waiting:
        assert not set(item.quarantined) & set(item.app_ids)
        if item.state is not SourceEligibility.ELIGIBLE:
            assert "are quarantined." in item.reason
    # The two fixture sets together, and each alone.
    for names in (("catalogue", "bootstrap_catalogue"), ("bootstrap_catalogue",)):
        same(built(recorded(*names)), recorded(*names))
    # The reference engine's state too, not only the blocked one's.
    same(generate_catalogue(records, as_of=AS_OF, id_factory=counting_ids()), records)


def test_eligibility_is_point_in_time_for_bindings_and_current_for_quarantine(
    recorded_state: CatalogueState,
) -> None:
    records = recorded()
    # J. Before any binding existed nothing is confirmed, in both implementations.
    for moment in (AS_OF - 30 * DAY, AS_OF - timedelta(seconds=1)):
        earlier = same(recorded_state, records, moment)
        assert not any(item.state is SourceEligibility.ELIGIBLE for item in earlier)
        assert not any(item.app_ids for item in earlier)
        assert indexed.sampling_targets(recorded_state, earlier, None, moment) == ()
        # Quarantine is the CURRENT quarantine even then: it is not reconstructed.
        assert [item.quarantined for item in earlier] == [
            item.quarantined for item in same(recorded_state, records)
        ]
    # At and after the moment of binding, and with no moment given: identical.
    now = same(recorded_state, records)
    for moment in (AS_OF, AS_OF + DAY, AS_OF + 400 * DAY):
        assert same(recorded_state, records, moment) == now

    # K. A binding closed and replaced. SYNTHETIC history laid over the recorded state:
    # Counter-Strike 2's app is rebound to another entity two days later.
    key = SourceRecordKey(source="steam_catalogue", source_id="730")
    counter_strike = recorded_state.mapping.canonical_for(key)
    assert counter_strike is not None
    other = next(
        game.canonical_game_id for game in recorded_state.catalogue.games if game.slug == "rust"
    )
    moved_at = AS_OF + 2 * DAY
    moved = recorded_state.model_copy(
        update={"mapping": recorded_state.mapping.bind(key, other, moved_at, "synthetic:move")}
    )
    assert [b.valid_to for b in moved.mapping.history_of(key)] == [moved_at, None]
    before = same(moved, records, AS_OF + DAY)
    after = same(moved, records, moved_at)
    latest = same(moved, records)
    assert after == latest
    ids = {item.canonical_game_id: item for item in before}
    assert ids[counter_strike].app_ids == ("730",)  # as it stood before the move
    ids = {item.canonical_game_id: item for item in after}
    assert ids[counter_strike].app_ids == ()  # the closed binding no longer counts
    # The app is bound to the other entity now, but nothing bound to THAT entity
    # asserts it, so it is not confirmed there either.
    assert "730" not in ids[other].app_ids
    # A later binding never appears at an earlier moment.
    assert same(moved, records, AS_OF) == same(recorded_state, records, AS_OF)
    # Then closed without a replacement.
    closed = moved.model_copy(update={"mapping": moved.mapping.unbind(key, moved_at + DAY)})
    same(closed, records, moved_at)
    same(closed, records, moved_at + DAY)
    same(closed, records)
    assert not any(
        target.source_native_id == "730"
        for target in indexed.sampling_targets(closed, same(closed, records))
    )


def test_every_branch_of_the_rule_on_small_synthetic_catalogues() -> None:
    """SYNTHETIC. One entity per situation, each compared and each pinned."""
    confirmed = [game("9500001", "Amberfall", "9500101"), app("9500101", "Amberfall")]
    two_apps = [
        game("9500002", "Brackenmoor", "9500102", "9500103"),
        app("9500102", "Brackenmoor"),
        app("9500103", "Brackenmoor"),
    ]
    unasserted = [game("9500004", "Cinderholt"), app("9500104", "Cinderholt")]
    unconfirmed = [game("9500005", "Dunmere", "9500105")]  # the app is not in Steam's list
    no_app = [game("9500006", "Eldermoss")]
    half = [game("9500007", "Fallowmere", "9500107", "9500108"), app("9500107", "Fallowmere")]
    records = [*confirmed, *two_apps, *unasserted, *unconfirmed, *no_app, *half]
    state = built(records)
    result = same(state, records)

    amberfall = by_name(result, "Amberfall")
    assert (amberfall.state, amberfall.app_ids) == (SourceEligibility.ELIGIBLE, ("9500101",))
    # D. Two confirmed apps: two ids, in order, and two separate sampling targets.
    brackenmoor = by_name(result, "Brackenmoor")
    assert brackenmoor.app_ids == ("9500102", "9500103")
    assert brackenmoor.reason.startswith("Steam app 9500102, 9500103 is in Steam's list")
    targets = indexed.sampling_targets(state, result)
    assert [t.source_native_id for t in targets] == ["9500101", "9500102", "9500103"]
    assert len({t.canonical_game_id for t in targets}) == 2
    # E. An app with the same title and no structured assertion is quarantined by the
    # resolver, never bound, so the entity's metadata simply asserts no app.
    cinderholt = by_name(result, "Cinderholt")
    assert cinderholt.app_ids == () and cinderholt.quarantined == ("9500104",)
    assert cinderholt.state is SourceEligibility.INELIGIBLE
    assert cinderholt.reason == (
        "The bound catalogue metadata asserts no Steam app. Steam app(s) 9500104 are quarantined."
    )
    # F. Asserted, and absent from Steam's list.
    dunmere = by_name(result, "Dunmere")
    assert (dunmere.state, dunmere.asserted_unconfirmed) == (
        SourceEligibility.UNKNOWN,
        ("9500105",),
    )
    assert "does not contain it" in dunmere.reason
    # H. Metadata that asserts nothing.
    assert by_name(result, "Eldermoss").state is SourceEligibility.INELIGIBLE
    # One of two asserted apps confirmed: not eligible, and the confirmed one is not sampled.
    fallowmere = by_name(result, "Fallowmere")
    assert (fallowmere.state, fallowmere.app_ids, fallowmere.asserted_unconfirmed) == (
        SourceEligibility.UNKNOWN,
        (),
        ("9500108",),
    )
    assert "9500107" not in {t.source_native_id for t in targets}

    # E, exactly: a Steam app BOUND to an entity that no bound record asserts.
    cinder_app = SourceRecordKey(source="steam_catalogue", source_id="9500104")
    forced = state.model_copy(
        update={
            "mapping": state.mapping.bind(
                cinder_app, cinderholt.canonical_game_id, AS_OF + DAY, "synthetic:bind"
            ),
            "quarantine": Quarantine(
                entries=tuple(e for e in state.quarantine.entries if e.record.key != cinder_app)
            ),
        }
    )
    bound_only = by_name(same(forced, records), "Cinderholt")
    assert (bound_only.state, bound_only.app_ids) == (SourceEligibility.UNKNOWN, ())
    assert bound_only.reason == (
        "Steam app 9500104 is bound, but no bound record lists it as a structured external id."
    )
    # G. Records missing from the call: bindings stand, assertions cannot be read.
    sparse = same(state, [record for record in records if record.key.source != "igdb"])
    assert by_name(sparse, "Amberfall").state is SourceEligibility.UNKNOWN
    assert by_name(sparse, "Amberfall").app_ids == ()
    same(state, [])
    # L. The empty catalogue: bootstrap entities only, nothing bound.
    empty = built([])
    nothing = same(empty, [])
    assert len(nothing) == 15
    assert {item.state for item in nothing} == {SourceEligibility.UNKNOWN}
    assert {item.reason for item in nothing} == {"No source record is bound to this entity."}
    assert indexed.sampling_targets(empty, nothing) == ()
    # A state with no bindings and no quarantine at all.
    bare = empty.model_copy(update={"mapping": MappingHistory(), "quarantine": Quarantine()})
    same(bare, [])


def test_thousands_of_synthetic_entities_classify_identically() -> None:
    """M. SYNTHETIC, 3,000 records with the recorded rows: every entity, every field."""
    records = [*recorded(), *stratified_sample(3_000).records]
    state = built(records)
    assert len(state.catalogue.games) > 1_400
    result = same(state, records)
    assert {item.state for item in result} == set(SourceEligibility)
    assert any(item.quarantined for item in result)
    assert any(item.asserted_unconfirmed for item in result)
    same(state, records, AS_OF - DAY)
    same(state, records[::2])  # with half the records withheld
    targets = indexed.sampling_targets(state, result)
    assert len(targets) > 100
    assert [t.source_native_id for t in targets] == sorted(
        (t.source_native_id for t in targets), key=int
    )


def test_the_index_is_per_call_and_changes_no_rule() -> None:
    signature = inspect.signature(indexed.steam_eligibility)
    assert list(signature.parameters) == ["state", "records", "as_of"]
    assert signature.parameters["as_of"].default is None
    for name in ("steam_eligibility", "sampling_targets", "active_binding"):
        new_parameters = inspect.signature(getattr(indexed, name)).parameters
        old_parameters = inspect.signature(getattr(reference, name)).parameters
        assert list(new_parameters) == list(old_parameters), name
        assert [p.default for p in new_parameters.values()] == [
            p.default for p in old_parameters.values()
        ], name
    new = Path(str(indexed.__file__)).read_text()
    old = Path(str(reference.__file__)).read_text()

    def rule(source: str) -> str:
        start = source.index("    confirmed = [app for app in steam if app in asserted]")
        return source[start : source.index("def bootstrap_scope()")]

    # From the first line that decides anything to the end of the function, the code
    # is character for character what it was.
    assert rule(new) == rule(old)
    assert new.count("class SourceEligibility") == 1
    # Nothing is stored between calls, and nothing is written.
    lookup = new[new.index("class _Lookup") : new.index("def steam_eligibility(")]
    assert "@dataclass(frozen=True)" in new and "global " not in new and "lru_cache" not in new
    assert "open(" not in new and "write" not in lookup
    # Quarantine is read as it is now, with no moment applied to it.
    assert (
        "state.quarantine.entries" in lookup and "as_of" not in lookup.split("quarantined: dict")[2]
    )
