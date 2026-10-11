"""Event intelligence: identity, revisions, heat over time, aggregation, availability,
the radar and explanations. Everything is offline and takes its time as an argument.

Every event here is synthetic and exists only in this file.
"""

import math
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest
from pydantic import ValidationError

from metaspike.domain.events import EventType
from metaspike.domain.games import (
    CanonicalGame,
    Catalogue,
    LifecycleState,
    SourceIdentifier,
)
from metaspike.events import heat as heat_module
from metaspike.events.explain import explain_event, type_label
from metaspike.events.heat import (
    Availability,
    EventIntelligence,
    Phase,
    UnavailableReason,
    aggregate,
    evaluate_event,
)
from metaspike.events.intelligence import assess_events
from metaspike.events.model import (
    CoverageCheck,
    EventObservation,
    EventRecord,
    EventReport,
    EventStatus,
    MatchRule,
    TimePrecision,
    UnresolvedGameError,
    observe,
    resolve_game,
)
from metaspike.events.radar import Radar, upcoming_radar, window_for
from metaspike.events.reconcile import normalise_reference, normalise_title, reconcile
from metaspike.events.rules import (
    EVENT_HEAT_RULES_V1,
    RULES_STATUS,
    RULES_VERSION,
    EventHeatRules,
)
from metaspike.events.store import EventStore
from metaspike.storage.base import StorageCorruptionError
from tests.conftest import make_id

RULES = EVENT_HEAT_RULES_V1
A, B = "events_a", "events_b"
GAME, OTHER, UNBOUND = make_id(1), make_id(2), make_id(3)
T0 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)
HOUR = timedelta(hours=1)
DEFAULT_START = datetime(2026, 10, 20, tzinfo=UTC)
EARLY = datetime(2026, 9, 1, tzinfo=UTC)


def at(month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


def game(game_id: str, slug: str, state: LifecycleState, *sources: str) -> CanonicalGame:
    return CanonicalGame(
        canonical_game_id=game_id,
        slug=slug,
        name=slug.title(),
        source_identifiers=tuple(
            SourceIdentifier(source=source, namespace="game", value=f"{slug}-in-{source}")
            for source in sources
        ),
        lifecycle_state=state,
    )


CATALOGUE = Catalogue(
    games=(
        game(GAME, "alpha", LifecycleState.LIVE_SERVICE, A, B),
        game(OTHER, "beta", LifecycleState.PRE_RELEASE, A, B),
        game(UNBOUND, "gamma", LifecycleState.DORMANT),
    )
)


def identifier(source: str, slug: str = "alpha") -> SourceIdentifier:
    return SourceIdentifier(source=source, namespace="game", value=f"{slug}-in-{source}")


def seen(
    source: str = A,
    *,
    slug: str = "alpha",
    kind: EventType = EventType.MAJOR_PATCH,
    title: str = "Update 2.0",
    starts: datetime = DEFAULT_START,
    ends: datetime | None = None,
    observed: datetime = T0,
    native: str | None = None,
    reference: str | None = None,
    status: EventStatus = EventStatus.SCHEDULED,
    precision: TimePrecision = TimePrecision.DAY,
) -> EventObservation:
    report = EventReport(
        game=identifier(source, slug),
        event_type=kind,
        title=title,
        starts_at=starts,
        ends_at=ends,
        time_precision=precision,
        status=status,
        source_event_id=native,
        reference=reference,
    )
    return observe(report, CATALOGUE, observed)


def one(*observations: EventObservation, as_of: datetime | None = None) -> EventRecord:
    records = reconcile(observations, as_of)
    assert len(records) == 1, [record.title for record in records]
    return records[0]


def record_of(
    kind: EventType,
    starts: datetime,
    ends: datetime | None = None,
    *,
    title: str = "An event",
    precision: TimePrecision = TimePrecision.EXACT,
    status: EventStatus = EventStatus.SCHEDULED,
    observed: datetime = EARLY,
    native: str | None = None,
) -> EventRecord:
    return one(
        seen(
            kind=kind,
            starts=starts,
            ends=ends,
            title=title,
            precision=precision,
            status=status,
            observed=observed,
            native=native,
        )
    )


def strength(record: EventRecord, as_of: datetime) -> float:
    return evaluate_event(record, RULES, as_of).strength


# --- the vocabulary and the rules -----------------------------------------------------


def test_every_event_type_has_a_rule_a_label_and_can_be_observed() -> None:
    assert set(RULES.types) == set(EventType)
    assert len(EventType) == 15
    for kind in EventType:
        rule = RULES.rule(kind)
        assert 0 < rule.importance <= 1 and rule.rationale
        assert type_label(kind)
        record = record_of(kind, at(10, 20), title=f"A {kind.value}")
        assert record.event_type is kind
        assert evaluate_event(record, RULES, at(10, 20)).phase in (Phase.ACTIVE, Phase.DECAYING)


def test_the_rules_are_versioned_internal_and_uncalibrated() -> None:
    assert RULES.version == RULES_VERSION == "event_heat_rules_v1"
    assert RULES.status == RULES_STATUS == "internal_uncalibrated"
    assert RULES.scale == 100 and RULES.max_contributing_events == 3
    assert RULES.radar_windows_hours == (24, 168, 720) and RULES.horizon_days == 30
    for name in (
        "scale",
        "max_contributing_events",
        "coverage_max_age",
        "horizon",
        "radar_windows",
    ):
        assert getattr(RULES, f"{name}_rationale")


def test_a_rule_set_missing_a_type_or_with_a_ramp_past_the_horizon_is_refused() -> None:
    data = RULES.model_dump()
    partial = {**data, "types": {k: v for k, v in data["types"].items() if k is not EventType.WIPE}}
    with pytest.raises(ValidationError, match="no rule for event type"):
        EventHeatRules.model_validate(partial)
    long_ramp = {**data, "horizon_days": 20, "radar_windows_hours": (24, 168, 480)}
    with pytest.raises(ValidationError, match="ramp may be longer than the horizon"):
        EventHeatRules.model_validate(long_ramp)
    with pytest.raises(ValidationError, match="last radar window must be the horizon"):
        EventHeatRules.model_validate({**data, "radar_windows_hours": (24, 168)})


def test_no_constant_hides_in_the_heat_arithmetic() -> None:
    # The only numerals allowed in the engine are 0, 1 and the halving base 0.5.
    source = Path(heat_module.__file__).read_text(encoding="utf-8")
    code = "\n".join(line.split("#")[0] for line in source.split('"""')[2].splitlines())
    import re

    numbers = set(re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", code))
    assert numbers <= {"0", "1", "0.0", "1.0", "0.5"}, numbers
    assert "datetime.now" not in source and "utcnow" not in source and "time.time" not in source


# --- resolving the game ---------------------------------------------------------------


def test_an_event_attaches_only_to_a_game_the_catalogue_binds_exactly() -> None:
    assert resolve_game(CATALOGUE, identifier(A)) == GAME
    assert seen(A).canonical_game_id == GAME and seen(B, slug="beta").canonical_game_id == OTHER
    for unknown in (
        SourceIdentifier(source=A, namespace="game", value="alpha"),  # the name, not the binding
        SourceIdentifier(source=A, namespace="game", value="ALPHA-IN-EVENTS_A"),  # no case folding
        SourceIdentifier(source=A, namespace="other", value="alpha-in-events_a"),
        SourceIdentifier(source="events_c", namespace="game", value="alpha-in-events_c"),
        SourceIdentifier(source=A, namespace="game", value="gamma-in-events_a"),  # unbound game
    ):
        with pytest.raises(UnresolvedGameError):
            resolve_game(CATALOGUE, unknown)
        report = EventReport(
            game=unknown, event_type=EventType.RELEASE, title="x", starts_at=at(10, 20)
        )
        with pytest.raises(UnresolvedGameError):
            observe(report, CATALOGUE, T0)


def test_an_observation_cannot_be_forged_or_end_before_it_starts() -> None:
    real = seen()
    with pytest.raises(ValidationError, match="does not match the observation's content"):
        EventObservation.model_validate({**real.model_dump(mode="json"), "title": "Another"})
    with pytest.raises(ValidationError, match="cannot end before it starts"):
        seen(starts=at(10, 20), ends=at(10, 19))
    with pytest.raises(ValueError, match="timezone-aware"):
        observe(
            EventReport(
                game=identifier(A), event_type=EventType.DLC, title="x", starts_at=at(10, 20)
            ),
            CATALOGUE,
            datetime(2026, 10, 10, 12, 0),
        )


def test_a_day_precision_event_is_normalised_to_the_start_of_its_utc_day() -> None:
    with_hour = seen(starts=at(10, 20, 17, 30), ends=at(10, 22, 9))
    assert with_hour.starts_at == at(10, 20) and with_hour.ends_at == at(10, 22)
    assert with_hour.observation_id == seen(starts=at(10, 20), ends=at(10, 22)).observation_id
    exact = seen(starts=at(10, 20, 17, 30), precision=TimePrecision.EXACT)
    assert exact.starts_at == at(10, 20, 17, 30)
    # An inclusive end day is over at the next midnight.
    assert (
        one(with_hour).effective_end == at(10, 23) and one(with_hour).end_day == at(10, 22).date()
    )


# --- identity, repeats and revisions --------------------------------------------------


def test_identity_is_stable_and_does_not_depend_on_order_or_repetition() -> None:
    first = seen(native="patch-200", observed=T0)
    again = seen(native="patch-200", observed=T0 + DAY)
    record = one(first, again)
    assert record.event_id == one(first).event_id == one(again, first, first).event_id
    assert record.event_id.startswith("evt_") and len(record.event_id) == 28
    assert [r.matched_by for r in record.revisions] == [
        MatchRule.NEW_EVENT,
        MatchRule.SAME_SOURCE_NATIVE_ID,
    ]
    assert record.revisions[1].changed == ()  # a repeat changes nothing
    assert record.first_observed_at == T0 and record.last_observed_at == T0 + DAY
    # The same statement at the same moment is one observation, not two.
    assert first.observation_id == seen(native="patch-200", observed=T0).observation_id
    assert first.observation_id != again.observation_id


def test_a_repeat_without_a_native_id_is_matched_by_source_type_and_day() -> None:
    record = one(seen(observed=T0), seen(observed=T0 + HOUR), seen(observed=T0 + DAY))
    assert [r.matched_by for r in record.revisions][1:] == [MatchRule.SAME_SOURCE_TYPE_AND_DAY] * 2
    assert record.sources == (A,)


def test_a_revised_date_keeps_the_event_and_records_the_change() -> None:
    first = seen(native="s4", kind=EventType.SEASON, title="Season 4", starts=at(10, 20))
    moved = seen(
        native="s4", kind=EventType.SEASON, title="Season 4", starts=at(10, 27), observed=T0 + DAY
    )
    record = one(first, moved)
    assert record.event_id == one(first).event_id
    assert record.starts_at == at(10, 27)
    assert record.revisions[1].matched_by is MatchRule.SAME_SOURCE_NATIVE_ID
    assert record.revisions[1].changed == ("starts_at",)
    # Nothing was overwritten: as of the first day the event still starts on the 20th.
    assert one(first, moved, as_of=T0).starts_at == at(10, 20)
    assert len(one(first, moved, as_of=T0).revisions) == 1


def test_a_changed_title_and_a_later_end_date_are_revisions_of_the_same_event() -> None:
    first = seen(kind=EventType.FREE_WEEKEND, title="Free Weekend", starts=at(10, 16))
    reworded = seen(
        kind=EventType.FREE_WEEKEND, title="Free weekend!", starts=at(10, 16), observed=T0 + HOUR
    )
    with_end = seen(
        kind=EventType.FREE_WEEKEND,
        title="Free weekend!",
        starts=at(10, 16),
        ends=at(10, 18),
        observed=T0 + DAY,
    )
    record = one(first, reworded, with_end)
    assert record.title == "Free weekend!" and record.ends_at == at(10, 18)
    assert [r.changed for r in record.revisions] == [(), ("title",), ("ends_at",)]
    assert one(first, reworded, with_end, as_of=T0 + HOUR).ends_at is None


def test_a_cancellation_is_a_revision_and_the_event_is_kept() -> None:
    first = seen(native="x", kind=EventType.SHOWCASE, title="Showcase")
    off = seen(
        native="x",
        kind=EventType.SHOWCASE,
        title="Showcase",
        status=EventStatus.CANCELLED,
        observed=T0 + DAY,
    )
    record = one(first, off)
    assert record.status is EventStatus.CANCELLED and record.revisions[1].changed == ("status",)
    evaluation = evaluate_event(record, RULES, at(10, 19))
    assert evaluation.phase is Phase.CALLED_OFF and evaluation.contribution == 0
    assert evaluate_event(one(first, off, as_of=T0), RULES, at(10, 19)).contribution > 0


def test_a_date_moved_without_a_native_id_is_safely_a_new_event() -> None:
    records = reconcile([seen(starts=at(10, 20)), seen(starts=at(10, 27), observed=T0 + DAY)])
    assert len(records) == 2 and len({record.event_id for record in records}) == 2


def test_two_sources_reporting_one_event_are_merged_only_on_exact_evidence() -> None:
    a = seen(A, kind=EventType.EXPANSION, title="The Frozen North", starts=at(11, 3))
    same_title = seen(
        B, kind=EventType.EXPANSION, title="the frozen north!", starts=at(11, 3), observed=T0 + HOUR
    )
    record = one(a, same_title)
    assert record.sources == (A, B)
    assert record.revisions[1].matched_by is MatchRule.CROSS_SOURCE_SAME_TYPE_DAY_AND_TITLE
    assert record.event_id == one(a).event_id  # the first observation fixes the id

    ref_a = seen(
        A, kind=EventType.TRAILER, title="Reveal", reference="https://www.Example.com/t/1/"
    )
    ref_b = seen(
        B,
        kind=EventType.TRAILER,
        title="Announcement trailer",
        starts=at(10, 21),
        reference="http://example.com/t/1",
        observed=T0 + HOUR,
    )
    by_reference = one(ref_a, ref_b)
    assert by_reference.revisions[1].matched_by is MatchRule.CROSS_SOURCE_SAME_REFERENCE
    assert by_reference.references == ("http://example.com/t/1", "https://www.Example.com/t/1/")


def test_an_end_date_from_one_source_survives_another_source_that_gives_none() -> None:
    a = seen(A, kind=EventType.BETA, title="Open beta", starts=at(10, 20), ends=at(10, 25))
    b = seen(B, kind=EventType.BETA, title="Open Beta", starts=at(10, 20), observed=T0 + HOUR)
    assert one(a, b).ends_at == at(10, 25)
    # The same source withdrawing its end does remove it.
    dropped = seen(A, kind=EventType.BETA, title="Open beta", starts=at(10, 20), observed=T0 + DAY)
    assert one(a, dropped).ends_at is None


@pytest.mark.parametrize(
    ("first", "second"),
    [
        # Different sources, same day and type, different titles: could be two events.
        (
            {"source": A, "title": "Winter Event"},
            {"source": B, "title": "Holiday Sale Event"},
        ),
        # Same title and day, different type.
        (
            {"source": A, "title": "Update 2.0", "kind": EventType.MAJOR_PATCH},
            {"source": B, "title": "Update 2.0", "kind": EventType.MINOR_PATCH},
        ),
        # Same title and type, a day apart.
        ({"source": A, "title": "Patch"}, {"source": B, "title": "Patch", "starts": at(10, 21)}),
        # One source, two native ids on one day.
        (
            {"source": A, "native": "dlc-1", "title": "Pack"},
            {"source": A, "native": "dlc-2", "title": "Pack"},
        ),
        # A similar title is not an equal title.
        ({"source": A, "title": "Season 4"}, {"source": B, "title": "Season 4: Reckoning"}),
        # The same event of a different game.
        (
            {"source": A, "title": "Update 2.0"},
            {"source": A, "title": "Update 2.0", "slug": "beta"},
        ),
    ],
)
def test_events_that_might_be_different_are_never_merged(
    first: dict[str, object], second: dict[str, object]
) -> None:
    records = reconcile([seen(**first), seen(**second, observed=T0 + HOUR)])  # type: ignore[arg-type]
    assert len(records) == 2
    assert all(len(record.revisions) == 1 for record in records)


def test_titles_and_references_are_compared_exactly_after_folding() -> None:
    assert normalise_title("  The Frozen North! ") == normalise_title("the  frozen—north")
    assert normalise_title("Pokémon Day") == normalise_title("POKEMON DAY")
    assert normalise_title("Season 4") != normalise_title("Season 14")
    assert normalise_reference("https://www.Example.com/a/") == normalise_reference(
        "http://example.com/a"
    )
    assert normalise_reference("https://example.com/a") != normalise_reference(
        "https://example.com/b"
    )
    assert normalise_reference("https://example.com/a?x=1") != normalise_reference(
        "https://example.com/a"
    )


def test_reconciliation_is_deterministic_whatever_order_observations_arrive_in() -> None:
    observations = [
        seen(A, title="One", starts=at(10, 20), observed=T0),
        seen(B, title="one", starts=at(10, 20), observed=T0 + HOUR),
        seen(A, title="Two", kind=EventType.DLC, starts=at(10, 25), observed=T0 + 2 * HOUR),
        seen(A, title="One", starts=at(10, 20), observed=T0 + DAY),
        seen(A, slug="beta", title="Launch", kind=EventType.RELEASE, starts=at(11, 1)),
    ]
    expected = reconcile(observations)
    assert [record.title for record in expected] == ["One", "Two", "Launch"]
    for rotation in range(len(observations)):
        shuffled = observations[rotation:] + observations[:rotation]
        assert reconcile(shuffled) == expected
        assert reconcile(list(reversed(shuffled))) == expected


# --- the store ------------------------------------------------------------------------


def test_the_store_is_append_only_idempotent_and_never_rewrites(tmp_path: Path) -> None:
    store = EventStore(tmp_path)
    assert store.observations() == () and store.events() == () and store.checks() == ()
    first = seen(native="p", observed=T0)
    moved = seen(native="p", starts=at(10, 24), observed=T0 + DAY)
    assert store.append([first]) == 1
    files = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*.ndjson"))
    before = {path: (tmp_path / path).read_bytes() for path in files}
    assert store.append([first]) == 0  # a repeat adds nothing
    assert store.append([first, moved]) == 1
    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*.ndjson"))
    assert set(files) < set(after) and len(after) == 2
    assert all((tmp_path / path).read_bytes() == body for path, body in before.items())
    assert [path.parts[:2] for path in after] == [
        ("observations", "2026-10-10"),
        ("observations", "2026-10-11"),
    ]
    assert EventStore(tmp_path).observations() == (first, moved)


def test_the_store_reconstructs_what_was_known_at_any_moment(tmp_path: Path) -> None:
    store = EventStore(tmp_path)
    first = seen(native="p", observed=T0)
    moved = seen(native="p", starts=at(10, 24), observed=T0 + DAY)
    other = seen(B, kind=EventType.DLC, title="Pack", starts=at(11, 1), observed=T0 + 2 * DAY)
    store.append([other, moved, first])
    assert store.events(T0 - HOUR) == ()
    assert [r.starts_at for r in store.events(T0)] == [at(10, 20)]
    assert [r.starts_at for r in store.events(T0 + DAY)] == [at(10, 24)]
    assert [r.title for r in store.events()] == ["Update 2.0", "Pack"]
    assert store.events(T0 + DAY) == reconcile([first, moved, other], T0 + DAY)
    # Asking again, or after more is appended, gives the same past.
    past = store.events(T0)
    store.append([seen(native="p", starts=at(10, 30), observed=T0 + 5 * DAY)])
    assert store.events(T0) == past


def test_coverage_checks_are_stored_the_same_way(tmp_path: Path) -> None:
    store = EventStore(tmp_path)
    early = CoverageCheck(canonical_game_id=GAME, source=A, checked_at=T0)
    late = CoverageCheck(canonical_game_id=GAME, source=A, checked_at=T0 + DAY)
    assert store.record_checks([late, early, early]) == 2
    assert store.record_checks([early]) == 0
    assert store.checks() == (early, late) and store.checks(T0) == (early,)


def test_a_corrupt_store_is_an_error_not_an_empty_result(tmp_path: Path) -> None:
    store = EventStore(tmp_path)
    store.append([seen()])
    path = next(tmp_path.rglob("*.ndjson"))
    path.write_text(path.read_text().replace("Update 2.0", "Update 9.9"))
    with pytest.raises(StorageCorruptionError):
        store.observations()


# --- one event over time --------------------------------------------------------------


def test_as_of_is_required_and_must_carry_a_time_zone() -> None:
    record = record_of(EventType.RELEASE, at(10, 20))
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_event(record, RULES, datetime(2026, 10, 20))
    with pytest.raises(ValueError, match="timezone-aware"):
        assess_events(GAME, [record], [], datetime(2026, 10, 20))
    # The same inputs always give the same result.
    assert evaluate_event(record, RULES, at(10, 22)) == evaluate_event(record, RULES, at(10, 22))


def test_a_future_event_ramps_from_exactly_the_ramp_boundary() -> None:
    rule = RULES.rule(EventType.MAJOR_PATCH)  # ramp 5 days, peak 0.4
    start = at(10, 20, 15)
    record = record_of(EventType.MAJOR_PATCH, start)
    ramp = timedelta(days=rule.ramp_days)
    outside = evaluate_event(record, RULES, start - ramp - timedelta(seconds=1))
    assert (outside.phase, outside.in_ramp, outside.strength) == (Phase.UPCOMING, False, 0)
    boundary = evaluate_event(record, RULES, start - ramp)
    assert (boundary.phase, boundary.in_ramp, boundary.strength) == (Phase.UPCOMING, False, 0)
    inside = evaluate_event(record, RULES, start - ramp + timedelta(seconds=1))
    assert inside.in_ramp and 0 < inside.strength < 1e-5
    halfway = evaluate_event(record, RULES, start - ramp / 2)
    assert halfway.strength == pytest.approx(rule.pre_event_peak / 2)
    assert halfway.contribution == pytest.approx(rule.importance * rule.pre_event_peak / 2)
    eve = evaluate_event(record, RULES, start - timedelta(seconds=1))
    assert eve.phase is Phase.UPCOMING and eve.strength == pytest.approx(
        rule.pre_event_peak, abs=1e-5
    )
    assert eve.strength < rule.pre_event_peak


def test_the_start_is_the_peak_to_the_second() -> None:
    start = at(10, 20, 15)
    for kind in EventType:
        record = record_of(kind, start)
        before = evaluate_event(record, RULES, start - timedelta(microseconds=1))
        on = evaluate_event(record, RULES, start)
        assert before.phase is Phase.UPCOMING
        assert on.phase in (Phase.ACTIVE, Phase.DECAYING) and on.strength == 1.0
        assert on.contribution == RULES.rule(kind).importance
        assert before.strength <= RULES.rule(kind).pre_event_peak < on.strength


def test_a_day_precision_event_starts_at_midnight_utc() -> None:
    record = record_of(EventType.SEASON, at(10, 20, 18), precision=TimePrecision.DAY)
    assert evaluate_event(record, RULES, at(10, 19, 23, 59)).phase is Phase.UPCOMING
    assert evaluate_event(record, RULES, at(10, 20)).phase is Phase.ACTIVE
    assert evaluate_event(record, RULES, at(10, 20)).strength == 1.0


def test_an_active_multi_day_event_halves_and_holds_at_its_floor_until_it_ends() -> None:
    rule = RULES.rule(EventType.SEASON)  # half-life 5, floor 0.15
    start, end = at(10, 1), at(11, 30)
    record = record_of(EventType.SEASON, start, end)
    half = evaluate_event(record, RULES, start + timedelta(days=rule.half_life_days))
    assert half.phase is Phase.ACTIVE and half.strength == pytest.approx(0.5)
    assert strength(record, start + timedelta(days=2 * rule.half_life_days)) == pytest.approx(0.25)
    late = evaluate_event(record, RULES, start + timedelta(days=40))
    assert late.phase is Phase.ACTIVE and late.strength == rule.active_floor
    assert late.end_assumed is False and late.ends_at == end


def test_the_end_boundary_is_exclusive_and_continuous() -> None:
    rule = RULES.rule(EventType.FREE_WEEKEND)  # half-life 3, floor 0.4, window 2
    start, end = at(10, 16, 17), at(10, 19, 17)
    record = record_of(EventType.FREE_WEEKEND, start, end)
    last = evaluate_event(record, RULES, end - timedelta(microseconds=1))
    over = evaluate_event(record, RULES, end)
    assert (last.phase, over.phase) == (Phase.ACTIVE, Phase.DECAYING)
    at_end = max(0.5 ** (3 / rule.half_life_days), rule.active_floor)
    assert over.strength == pytest.approx(at_end) == pytest.approx(0.5)
    assert last.strength == pytest.approx(over.strength, abs=1e-9)
    after = evaluate_event(record, RULES, end + DAY)
    assert after.strength == pytest.approx(at_end * 0.5 ** (1 / rule.half_life_days))


def test_decay_stops_exactly_at_the_window_and_an_old_event_is_expired() -> None:
    rule = RULES.rule(EventType.MAJOR_PATCH)  # instantaneous, half-life 4, window 10
    start = at(10, 1, 9)
    record = record_of(EventType.MAJOR_PATCH, start)
    window = timedelta(days=rule.decay_window_days)
    assert strength(record, start + timedelta(days=rule.half_life_days)) == pytest.approx(0.5)
    still = evaluate_event(record, RULES, start + window - timedelta(seconds=1))
    assert still.phase is Phase.DECAYING
    assert still.strength == pytest.approx(
        0.5 ** (rule.decay_window_days / rule.half_life_days), rel=1e-4
    )
    gone = evaluate_event(record, RULES, start + window)
    assert (gone.phase, gone.strength, gone.contribution) == (Phase.EXPIRED, 0, 0)
    assert evaluate_event(record, RULES, start + timedelta(days=400)).phase is Phase.EXPIRED


def test_an_unknown_end_uses_the_types_default_duration_and_says_so() -> None:
    rule = RULES.rule(EventType.WIPE)  # default duration 14 days
    start = at(10, 1)
    record = record_of(EventType.WIPE, start)
    running = evaluate_event(record, RULES, start + timedelta(days=13))
    assert running.phase is Phase.ACTIVE and running.end_assumed is True
    assert running.ends_at == start + timedelta(days=rule.default_duration_days)
    assert evaluate_event(record, RULES, running.ends_at).phase is Phase.DECAYING
    # A stated end replaces the assumption.
    stated = evaluate_event(record_of(EventType.WIPE, start, at(10, 5)), RULES, at(10, 6))
    assert stated.end_assumed is False and stated.phase is Phase.DECAYING


def test_a_revised_date_moves_the_heat_only_from_when_the_revision_was_known() -> None:
    first = seen(native="p", starts=at(10, 20), observed=at(10, 1))
    moved = seen(native="p", starts=at(10, 30), observed=at(10, 18))
    as_of = at(10, 19)
    with_revision = evaluate_event(one(first, moved, as_of=as_of), RULES, as_of)
    without = evaluate_event(one(first, moved, as_of=at(10, 17)), RULES, as_of)
    assert without.in_ramp and without.strength > 0
    assert not with_revision.in_ramp and with_revision.strength == 0  # 11 days out again


@pytest.mark.parametrize("kind", list(EventType))
def test_strength_never_falls_before_the_start_and_never_rises_after_it(kind: EventType) -> None:
    start = at(10, 20, 12)
    for ends in (None, start + 6 * DAY):
        record = record_of(kind, start, ends)
        moments = [start - 40 * DAY + step * 3 * HOUR for step in range(8 * 120)]
        values = [evaluate_event(record, RULES, moment) for moment in moments]
        for earlier, later in pairwise(values):
            assert 0 <= later.strength <= 1 and later.contribution <= RULES.rule(kind).importance
            if later.phase is Phase.UPCOMING:
                assert later.strength >= earlier.strength - 1e-12
            elif earlier.phase is not Phase.UPCOMING:
                assert later.strength <= earlier.strength + 1e-12
        assert values[0].strength == 0 and values[-1].phase is Phase.EXPIRED


# --- several events at once -----------------------------------------------------------


def evaluations(as_of: datetime, *records: EventRecord) -> list[heat_module.EventEvaluation]:
    return [evaluate_event(record, RULES, as_of) for record in records]


def test_overlapping_events_combine_and_each_contribution_stays_inspectable() -> None:
    as_of = at(10, 20)
    expansion = record_of(EventType.EXPANSION, as_of, title="Expansion")  # 0.85 at its start
    season = record_of(EventType.SEASON, as_of - 5 * DAY, as_of + 60 * DAY, title="Season")  # 0.35
    heat, parts = aggregate(evaluations(as_of, season, expansion), RULES)
    assert heat == pytest.approx(100 * (1 - (1 - 0.85) * (1 - 0.35)))
    assert [(p.evaluation.title, p.counted, p.rank) for p in parts] == [
        ("Expansion", True, 1),
        ("Season", True, 2),
    ]
    assert [p.evaluation.contribution for p in parts] == pytest.approx([0.85, 0.35])
    single, _ = aggregate(evaluations(as_of, expansion), RULES)
    assert single == pytest.approx(85) and heat > single  # two important events matter more


def test_stacking_dozens_of_minor_events_cannot_make_a_large_number() -> None:
    as_of = at(10, 20)
    minors = [
        record_of(
            EventType.MINOR_PATCH, as_of - index * HOUR, title=f"Hotfix {index}", native=str(index)
        )
        for index in range(40)
    ]
    assert len({record.event_id for record in minors}) == 40
    heat, parts = aggregate(evaluations(as_of, *minors), RULES)
    ceiling = 100 * (
        1 - (1 - RULES.rule(EventType.MINOR_PATCH).importance) ** RULES.max_contributing_events
    )
    assert heat <= ceiling + 1e-9 and ceiling == pytest.approx(38.5875)
    assert sum(part.counted for part in parts) == RULES.max_contributing_events
    major, _ = aggregate(evaluations(as_of, record_of(EventType.MAJOR_PATCH, as_of)), RULES)
    assert heat < major  # forty hotfixes are still less than one major patch
    assert len(parts) == 40 and not any(math.isnan(p.evaluation.contribution) for p in parts)


def test_heat_is_bounded_ordered_and_never_falls_when_an_event_is_added() -> None:
    as_of = at(10, 20)
    pool = [
        record_of(kind, as_of - index * DAY, title=f"{kind.value}", native=kind.value)
        for index, kind in enumerate(EventType)
    ]
    previous = 0.0
    for count in range(1, len(pool) + 1):
        heat, parts = aggregate(evaluations(as_of, *pool[:count]), RULES)
        assert 0 <= heat <= RULES.scale and heat >= previous - 1e-9
        counted = [part for part in parts if part.counted]
        values = [part.evaluation.contribution for part in counted]
        assert (
            values == sorted(values, reverse=True) and len(counted) <= RULES.max_contributing_events
        )
        assert [part.rank for part in counted] == list(range(1, len(counted) + 1))
        assert heat >= 100 * max(values) - 1e-9  # never below the largest single contribution
        previous = heat
    assert aggregate([], RULES) == (0.0, ())
    release, _ = aggregate(evaluations(as_of, record_of(EventType.RELEASE, as_of)), RULES)
    assert release == 100.0  # reaches the scale only when one contribution is 1


def test_a_possible_duplicate_counts_once_and_says_why() -> None:
    as_of = at(10, 20)
    a = seen(
        A,
        kind=EventType.EXPANSION,
        title="The Frozen North",
        starts=as_of,
        precision=TimePrecision.EXACT,
    )
    b = seen(
        B,
        kind=EventType.EXPANSION,
        title="Northern Expansion",
        starts=as_of,
        precision=TimePrecision.EXACT,
    )
    records = reconcile([a, b])
    assert len(records) == 2  # different titles: not merged
    heat, parts = aggregate(evaluations(as_of, *records), RULES)
    assert heat == pytest.approx(85)  # and not 1 - 0.15 * 0.15
    assert [part.counted for part in parts] == [True, False]
    assert parts[1].not_counted_because == "same_type_and_start_day_as_a_stronger_event"
    # Events beyond the limit say so too.
    many = [
        record_of(kind, as_of, title=kind.value)
        for kind in (EventType.RELEASE, EventType.EXPANSION, EventType.SEASON, EventType.DLC)
    ]
    _, ranked = aggregate(evaluations(as_of, *many), RULES)
    assert [part.not_counted_because for part in ranked] == [
        None,
        None,
        None,
        "beyond_max_contributing_events",
    ]
    assert ranked[3].evaluation.event_type is EventType.DLC


def test_ties_are_broken_deterministically() -> None:
    as_of = at(10, 20)
    first = record_of(EventType.DLC, as_of, title="Pack A", native="a")
    second = record_of(EventType.DLC, as_of - DAY, title="Pack B", native="b")
    third = record_of(EventType.DLC, as_of + DAY, title="Pack C", native="c")
    forward = aggregate(evaluations(as_of, first, second, third), RULES)
    backward = aggregate(evaluations(as_of, third, second, first), RULES)
    assert forward == backward


# --- availability ---------------------------------------------------------------------


def check(when: datetime, source: str = A, game_id: str = GAME) -> CoverageCheck:
    return CoverageCheck(canonical_game_id=game_id, source=source, checked_at=when)


def test_unavailable_is_not_zero_and_zero_is_not_unavailable() -> None:
    as_of = at(10, 20)
    nobody_looked = assess_events(GAME, [], [], as_of)
    assert nobody_looked.availability is Availability.UNAVAILABLE
    assert nobody_looked.event_heat is None  # not 0
    assert nobody_looked.unavailable_reasons == (UnavailableReason.NO_EVENT_COVERAGE,)
    assert nobody_looked.contributions == () and nobody_looked.top_event_id is None
    assert "not the same as no events" in nobody_looked.summary

    looked = assess_events(GAME, [], [check(as_of - DAY)], as_of)
    assert looked.availability is Availability.AVAILABLE
    assert looked.event_heat == 0.0 and looked.event_heat is not None
    assert looked.unavailable_reasons == () and looked.reasons[0].code == "no_relevant_event"
    assert looked.coverage_checked_at == as_of - DAY and looked.coverage_sources == (A,)

    with_event = assess_events(
        GAME, [record_of(EventType.RELEASE, as_of)], [check(as_of - DAY)], as_of
    )
    assert with_event.availability is Availability.AVAILABLE and with_event.event_heat == 100.0
    assert with_event.top_event_id == with_event.contributions[0].evaluation.event_id


def test_the_result_model_refuses_a_number_without_availability_and_the_reverse() -> None:
    good = assess_events(GAME, [], [], at(10, 20)).model_dump()
    with pytest.raises(ValidationError, match="event_heat is a number exactly when"):
        EventIntelligence.model_validate({**good, "event_heat": 0.0})
    available = assess_events(GAME, [], [check(at(10, 19))], at(10, 20)).model_dump()
    with pytest.raises(ValidationError, match="event_heat is a number exactly when"):
        EventIntelligence.model_validate({**available, "event_heat": None})
    with pytest.raises(ValidationError, match="unavailable_reasons are given exactly when"):
        EventIntelligence.model_validate(
            {**available, "unavailable_reasons": ["no_event_coverage"]}
        )


def test_coverage_goes_stale_exactly_after_the_configured_age() -> None:
    as_of = at(10, 20)
    limit = timedelta(days=RULES.coverage_max_age_days)
    on_the_limit = assess_events(GAME, [], [check(as_of - limit)], as_of)
    assert on_the_limit.availability is Availability.AVAILABLE and on_the_limit.event_heat == 0
    past_it = assess_events(GAME, [], [check(as_of - limit - timedelta(seconds=1))], as_of)
    assert past_it.availability is Availability.UNAVAILABLE and past_it.event_heat is None
    assert past_it.unavailable_reasons == (UnavailableReason.EVENT_COVERAGE_STALE,)
    assert past_it.coverage_checked_at == as_of - limit - timedelta(seconds=1)
    # A stale picture is not trusted even when it holds a known future event.
    known = record_of(EventType.RELEASE, as_of + DAY, observed=as_of - 20 * DAY)
    assert assess_events(GAME, [known], [], as_of).event_heat is None


def test_an_observation_counts_as_a_look_and_a_future_check_does_not() -> None:
    as_of = at(10, 20)
    record = record_of(EventType.DLC, as_of + 2 * DAY, observed=as_of - DAY)
    by_observation = assess_events(GAME, [record], [], as_of)
    assert by_observation.availability is Availability.AVAILABLE
    assert by_observation.coverage_checked_at == as_of - DAY
    not_yet = assess_events(GAME, [], [check(as_of + HOUR)], as_of)
    assert not_yet.availability is Availability.UNAVAILABLE
    # Another game's coverage says nothing about this one.
    elsewhere = assess_events(GAME, [], [check(as_of - DAY, game_id=OTHER)], as_of)
    assert elsewhere.availability is Availability.UNAVAILABLE


def test_only_relevant_events_are_listed_and_only_this_games() -> None:
    as_of = at(10, 20)
    records = [
        record_of(EventType.MAJOR_PATCH, as_of - 30 * DAY, title="Old patch", native="old"),
        record_of(EventType.RELEASE, as_of + 45 * DAY, title="Far release", native="far"),
        record_of(EventType.DLC, as_of + 20 * DAY, title="Later pack", native="later"),
        record_of(EventType.SEASON, as_of - 2 * DAY, as_of + 30 * DAY, title="Season", native="s"),
        one(seen(slug="beta", kind=EventType.RELEASE, title="Other game", starts=as_of)),
    ]
    result = assess_events(GAME, records, [check(as_of)], as_of)
    assert [c.evaluation.title for c in result.contributions] == ["Season", "Later pack"]
    assert [c.counted for c in result.contributions] == [True, False]
    later = result.contributions[1].evaluation
    assert later.phase is Phase.UPCOMING and not later.in_ramp and later.contribution == 0
    assert result.top_event_id == result.contributions[0].evaluation.event_id


def test_event_heat_does_not_depend_on_lifecycle_state() -> None:
    as_of = at(10, 20)
    results = []
    for state in LifecycleState:
        catalogue = Catalogue(games=(game(GAME, "alpha", state, A),))
        report = EventReport(
            game=identifier(A), event_type=EventType.EXPANSION, title="North", starts_at=as_of - DAY
        )
        record = one(observe(report, catalogue, as_of - 2 * DAY))
        results.append(assess_events(GAME, [record], [], as_of).model_dump())
    assert all(result == results[0] for result in results)
    assert results[0]["event_heat"] == pytest.approx(100 * 0.85 * 0.5 ** (1 / 6))


# --- explanations ---------------------------------------------------------------------


def sentence(record: EventRecord, as_of: datetime) -> tuple[str, str]:
    reason = explain_event(evaluate_event(record, RULES, as_of), as_of)
    assert reason is not None
    return reason.code, reason.text


def test_explanations_are_rendered_from_the_event_and_its_phase() -> None:
    patch = record_of(EventType.MAJOR_PATCH, at(10, 20, 15), title="Update 2.0")
    assert sentence(patch, at(10, 18, 9)) == (
        "event_upcoming",
        "Major update launches in 2 days: Update 2.0.",
    )
    assert sentence(patch, at(10, 19, 23)) == (
        "event_upcoming",
        "Major update launches tomorrow: Update 2.0.",
    )
    assert sentence(patch, at(10, 20, 3)) == (
        "event_upcoming",
        "Major update launches later today: Update 2.0.",
    )
    assert sentence(patch, at(10, 20, 16)) == (
        "event_started_today",
        "Major update launched today: Update 2.0.",
    )
    assert sentence(patch, at(10, 21, 1)) == (
        "event_recent",
        "Major update launched yesterday; event influence is still elevated: Update 2.0.",
    )
    assert sentence(patch, at(10, 23, 16)) == (
        "event_recent",
        "Major update launched 3 days ago; event influence is still elevated: Update 2.0.",
    )
    assert sentence(patch, at(10, 10)) == (
        "event_scheduled",
        "Major update launches in 10 days: Update 2.0. It is not yet adding to event heat.",
    )
    season = record_of(
        EventType.SEASON, at(10, 20), at(11, 20), title="Season 4", precision=TimePrecision.DAY
    )
    assert sentence(season, at(10, 20, 8)) == (
        "event_active",
        "New season started today: Season 4. It ends in 32 days.",
    )
    assert sentence(season, at(10, 23)) == (
        "event_active",
        "New season started 3 days ago and is still running: Season 4. It ends in 29 days.",
    )
    assert sentence(season, at(11, 22)) == (
        "event_ended_recently",
        "New season ended yesterday; event influence is fading: Season 4.",
    )
    wipe = record_of(EventType.WIPE, at(10, 20), title="Wipe")  # no stated end
    assert sentence(wipe, at(10, 22)) == (
        "event_active",
        "Wipe happened 2 days ago and is still running: Wipe.",
    )


def test_an_event_with_nothing_to_say_has_no_sentence() -> None:
    old = record_of(EventType.TRAILER, at(8, 1))
    assert explain_event(evaluate_event(old, RULES, at(10, 20)), at(10, 20)) is None
    off = record_of(EventType.TRAILER, at(10, 21), status=EventStatus.WITHDRAWN)
    assert explain_event(evaluate_event(off, RULES, at(10, 20)), at(10, 20)) is None


def test_every_sentence_in_a_result_is_backed_by_an_event_in_it() -> None:
    as_of = at(10, 20)
    records = [
        record_of(EventType.EXPANSION, as_of + 2 * DAY, title="North", native="n"),
        record_of(EventType.SEASON, as_of - DAY, as_of + 30 * DAY, title="Season 4", native="s"),
    ]
    result = assess_events(GAME, records, [check(as_of)], as_of)
    assert len(result.reasons) == len(result.contributions) == 2
    for reason, contribution in zip(result.reasons, result.contributions, strict=True):
        assert contribution.evaluation.title in reason.text
        assert reason == explain_event(contribution.evaluation, as_of)
    assert result.summary == result.reasons[0].text
    assert result.reasons[0].text.startswith("New season started yesterday")
    for kind in EventType:
        code, text = sentence(record_of(kind, as_of + DAY, title="T"), as_of)
        assert (
            text.startswith(type_label(kind)) and "tomorrow" in text and code.startswith("event_")
        )


# --- the radar ------------------------------------------------------------------------


def radar_for(
    as_of: datetime, *records: EventRecord, covered: tuple[str, ...] = (GAME, OTHER)
) -> Radar:
    checks = [check(as_of, game_id=game_id) for game_id in covered]
    results = {
        game_id: assess_events(game_id, list(records), checks, as_of)
        for game_id in (GAME, OTHER, UNBOUND)
    }
    return upcoming_radar(results, as_of)


def test_radar_windows_include_their_end_and_exclude_what_has_started() -> None:
    assert RULES.radar_windows_hours == (24, 168, 720)
    assert [window_for(hours, RULES) for hours in (0.001, 23.99, 24)] == [24, 24, 24]
    assert [window_for(hours, RULES) for hours in (24.001, 167.9, 168)] == [168, 168, 168]
    assert [window_for(hours, RULES) for hours in (168.001, 719.9, 720)] == [720, 720, 720]
    assert [window_for(hours, RULES) for hours in (720.001, 5000, 0, -1)] == [
        None,
        None,
        None,
        None,
    ]

    as_of = at(10, 20, 12)
    offsets = {
        "now": timedelta(0),
        "in a second": timedelta(seconds=1),
        "24h": 24 * HOUR,
        "24h+": 24 * HOUR + timedelta(seconds=1),
        "7d": 7 * DAY,
        "7d+": 7 * DAY + timedelta(seconds=1),
        "30d": 30 * DAY,
        "30d+": 30 * DAY + timedelta(seconds=1),
        "past": -HOUR,
    }
    records = [
        record_of(EventType.DLC, as_of + offset, title=name, native=name)
        for name, offset in offsets.items()
    ]
    radar = radar_for(as_of, *records)
    assert radar.availability is Availability.AVAILABLE
    assert {item.title: item.window_hours for item in radar.items} == {
        "in a second": 24,
        "24h": 24,
        "24h+": 168,
        "7d": 168,
        "7d+": 720,
        "30d": 720,
    }
    assert [item.title for item in radar.items] == [
        "in a second",
        "24h",
        "24h+",
        "7d",
        "7d+",
        "30d",
    ]
    assert radar.items[1].hours_until_start == 24.0


def test_the_radar_reports_timing_and_context_not_a_ranking() -> None:
    as_of = at(10, 20)
    soon = record_of(EventType.MINOR_PATCH, as_of + 6 * HOUR, title="Hotfix", native="h")
    big = record_of(EventType.RELEASE, as_of + 3 * DAY, title="Launch", native="l")
    far = record_of(EventType.TRAILER, as_of + 10 * DAY, title="Trailer", native="t")
    running = record_of(EventType.SEASON, as_of - DAY, as_of + 20 * DAY, title="Season", native="s")
    other = one(seen(slug="beta", kind=EventType.BETA, title="Open beta", starts=as_of + 2 * DAY))
    radar = radar_for(as_of, soon, big, far, running, other)
    # Soonest first: a hotfix in six hours comes before a release in three days.
    assert [item.title for item in radar.items] == ["Hotfix", "Open beta", "Launch", "Trailer"]
    assert "Season" not in [item.title for item in radar.items]  # already started
    launch = radar.items[2]
    assert launch.in_ramp and launch.hours_until_start == 72 and launch.window_hours == 168
    assert launch.contribution == pytest.approx(1.0 * 0.6 * (1 - 3 / 30))
    assert launch.text == "Release launches in 3 days: Launch."
    trailer = radar.items[3]
    assert not trailer.in_ramp and trailer.contribution == 0 and "not yet adding" in trailer.text
    # Context: the game's whole event heat, the same on each of its items.
    assert radar.items[0].game_event_heat == radar.items[2].game_event_heat > 0
    assert radar.items[1].canonical_game_id == OTHER
    assert (radar.games_with_coverage, radar.games_without_coverage) == (2, 1)
    assert "opportunit" not in radar.summary.lower()


def test_an_unavailable_radar_is_not_an_empty_radar() -> None:
    as_of = at(10, 20)
    unknown = radar_for(as_of, covered=())
    assert unknown.availability is Availability.UNAVAILABLE and unknown.items == ()
    assert unknown.unavailable_reasons == (UnavailableReason.NO_EVENT_COVERAGE,)
    assert (unknown.games_with_coverage, unknown.games_without_coverage) == (0, 3)
    assert "not the same as nothing upcoming" in unknown.summary

    empty = radar_for(as_of)
    assert empty.availability is Availability.AVAILABLE and empty.items == ()
    assert empty.unavailable_reasons == () and empty.games_with_coverage == 2
    assert empty.summary.startswith("No dated event starts within the horizon")

    # A game without coverage contributes nothing, even if an event of it is on record.
    stale = record_of(EventType.RELEASE, as_of + DAY, observed=as_of - 30 * DAY)
    partly = radar_for(as_of, stale, covered=(OTHER,))
    assert partly.availability is Availability.AVAILABLE and partly.items == ()
    assert (partly.games_with_coverage, partly.games_without_coverage) == (1, 2)


def test_the_radar_refuses_results_from_another_moment() -> None:
    as_of = at(10, 20)
    results = [assess_events(GAME, [], [check(as_of)], as_of)]
    with pytest.raises(ValueError, match="same as_of"):
        upcoming_radar(results, as_of + HOUR)
