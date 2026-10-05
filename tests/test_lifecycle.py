import ast
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from metaspike.domain.games import LifecycleState
from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.source_records import DatePrecision, ProductType, ReleaseDate, SourceRecord
from metaspike.lifecycle import history as history_module
from metaspike.lifecycle import rules
from metaspike.lifecycle.history import LifecycleHistory, LifecycleOverride
from metaspike.lifecycle.rules import (
    RULE_VERSION,
    ActivityEvidence,
    Availability,
    LifecycleFlag,
    derive_state,
    effective_date,
    first_public_availability,
)
from tests.conftest import make_id

GAME = make_id(1)
NO_EVIDENCE = ActivityEvidence()
LAUNCH = date(2026, 1, 10)


def release(
    day: date | None, precision: DatePrecision = DatePrecision.DAY, **fields: str
) -> ReleaseDate:
    return ReleaseDate(starts_on=day, precision=precision, **fields)


def game(*releases: ReleaseDate, status: str | None = None, source_id: str = "1") -> SourceRecord:
    return SourceRecord(
        key=SourceRecordKey(source="igdb", source_id=source_id),
        title="Synthetic Quest",
        product_type=ProductType.MAIN_GAME,
        status=status,
        release_dates=releases,
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def state(
    first: date | None, as_of: date, activity: ActivityEvidence = NO_EVIDENCE
) -> LifecycleState | None:
    return derive_state(Availability(first_available=first), activity, as_of).state


# --- release dates --------------------------------------------------------------------


def test_the_earliest_release_on_any_platform_or_region_counts() -> None:
    record = game(
        release(date(2026, 3, 1), platform="PlayStation 5", region="europe"),
        release(LAUNCH, platform="PC (Microsoft Windows)", region="north_america"),
        release(date(2027, 6, 1), platform="Nintendo Switch"),
    )
    assert first_public_availability((record,)).first_available == LAUNCH


def test_early_access_counts_as_first_public_availability() -> None:
    record = game(
        release(LAUNCH, status="Early Access"), release(date(2027, 2, 1), status="Full Release")
    )
    availability = first_public_availability((record,))
    assert availability.first_available == LAUNCH
    assert state(LAUNCH, LAUNCH + timedelta(days=5)) is LifecycleState.LAUNCH_WINDOW


@pytest.mark.parametrize(
    ("precision", "expected"),
    [
        (DatePrecision.DAY, date(2026, 4, 1)),
        (DatePrecision.MONTH, date(2026, 4, 30)),
        (DatePrecision.QUARTER, date(2026, 6, 30)),
        (DatePrecision.YEAR, date(2026, 12, 31)),
    ],
)
def test_an_imprecise_date_counts_as_the_end_of_its_period(
    precision: DatePrecision, expected: date
) -> None:
    assert effective_date(release(date(2026, 4, 1), precision)) == expected
    assert effective_date(release(None, DatePrecision.TBD)) is None


def test_a_game_announced_for_a_year_is_pre_release_until_that_year_ends() -> None:
    availability = first_public_availability((game(release(date(2026, 1, 1), DatePrecision.YEAR)),))
    assert (
        derive_state(availability, NO_EVIDENCE, date(2026, 11, 1)).state
        is LifecycleState.PRE_RELEASE
    )
    assert (
        derive_state(availability, NO_EVIDENCE, date(2027, 1, 5)).state
        is LifecycleState.LAUNCH_WINDOW
    )


def test_a_release_with_no_date_yet_is_pre_release() -> None:
    availability = first_public_availability((game(release(None, DatePrecision.TBD)),))
    assert availability.announced_without_date
    assert derive_state(availability, NO_EVIDENCE, LAUNCH).state is LifecycleState.PRE_RELEASE


# --- the launch window ----------------------------------------------------------------


def test_launch_window_is_exactly_thirty_days_and_never_starts_early() -> None:
    assert rules.LAUNCH_WINDOW_DAYS == 30
    assert state(LAUNCH, LAUNCH - timedelta(days=1)) is LifecycleState.PRE_RELEASE
    assert state(LAUNCH, LAUNCH) is LifecycleState.LAUNCH_WINDOW
    assert state(LAUNCH, LAUNCH + timedelta(days=29)) is LifecycleState.LAUNCH_WINDOW
    assert state(LAUNCH, LAUNCH + timedelta(days=30)) is LifecycleState.RELEASED


def test_a_full_release_after_early_access_does_not_restart_the_launch_window() -> None:
    full_release = LAUNCH + timedelta(days=400)
    record = game(
        release(LAUNCH, status="Early Access"), release(full_release, status="Full Release")
    )
    availability = first_public_availability((record,))
    for offset in (0, 5, 29):
        derived = derive_state(availability, NO_EVIDENCE, full_release + timedelta(days=offset))
        assert derived.state is LifecycleState.RELEASED


def test_a_later_platform_release_does_not_restart_the_launch_window() -> None:
    port = LAUNCH + timedelta(days=700)
    record = game(release(LAUNCH, platform="PlayStation 5"), release(port, platform="PC"))
    availability = first_public_availability((record,))
    assert (
        derive_state(availability, NO_EVIDENCE, port + timedelta(days=3)).state
        is LifecycleState.RELEASED
    )


# --- states that need later evidence ---------------------------------------------------

OLD = date(2020, 1, 1)
TODAY = date(2026, 10, 5)


def test_live_service_needs_delivered_content_not_promises() -> None:
    active = ActivityEvidence(content_events_last_180d=3, active_day_fraction_last_180d=0.9)
    assert state(OLD, TODAY, active) is LifecycleState.LIVE_SERVICE
    two = active.model_copy(update={"content_events_last_180d": 2})
    assert state(OLD, TODAY, two) is LifecycleState.EVERGREEN
    ended = active.model_copy(update={"service_ended": True})
    assert state(OLD, TODAY, ended) is not LifecycleState.LIVE_SERVICE


def test_evergreen_needs_both_age_and_sustained_activity() -> None:
    busy = ActivityEvidence(content_events_last_180d=0, active_day_fraction_last_180d=0.9)
    assert state(OLD, TODAY, busy) is LifecycleState.EVERGREEN
    young = TODAY - timedelta(days=400)
    assert state(young, TODAY, busy) is LifecycleState.RELEASED  # attention alone is not enough
    quiet = busy.model_copy(update={"active_day_fraction_last_180d": 0.5})
    assert state(OLD, TODAY, quiet) is LifecycleState.RELEASED  # age alone is not enough


def test_dormant_needs_no_events_for_a_year_and_almost_no_activity() -> None:
    silent = ActivityEvidence(
        content_events_last_180d=0,
        days_since_last_content_event=400,
        active_day_fraction_last_180d=0.0,
    )
    assert state(OLD, TODAY, silent) is LifecycleState.DORMANT
    recent = silent.model_copy(update={"days_since_last_content_event": 200})
    assert state(OLD, TODAY, recent) is LifecycleState.RELEASED
    still_played = silent.model_copy(update={"active_day_fraction_last_180d": 0.3})
    assert state(OLD, TODAY, still_played) is LifecycleState.RELEASED


def test_without_event_or_activity_evidence_the_rules_only_say_released() -> None:
    assert state(OLD, TODAY) is LifecycleState.RELEASED
    assert ActivityEvidence().content_events_last_180d is None


def test_thresholds_are_versioned_rules_not_timeless_truth() -> None:
    assert RULE_VERSION == "lifecycle_rules_v1"
    assert "VERSIONED INITIAL" in (rules.__doc__ or "")
    assert (rules.LIVE_SERVICE_MIN_EVENTS, rules.EVERGREEN_MIN_AGE_DAYS) == (3, 1095)
    assert (rules.DORMANT_NO_EVENT_DAYS, rules.HOLD_DAYS) == (365, 14)


def test_lifecycle_never_reads_the_opportunity_score() -> None:
    for module in (rules, history_module):
        tree = ast.parse(Path(module.__file__ or "").read_text())
        imported = {
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        }
        assert not any("scoring" in name or "features" in name for name in imported)
    assert "score" not in ActivityEvidence.model_fields
    assert "score" not in Availability.model_fields


# --- conflicts, history, hold and overrides ---------------------------------------------


def test_conflicting_evidence_keeps_the_previous_state_and_raises_a_flag() -> None:
    history = LifecycleHistory(canonical_game_id=GAME).advance(
        Availability(first_available=LAUNCH), NO_EVIDENCE, LAUNCH + timedelta(days=60)
    )
    assert history.current is not None and history.current.state is LifecycleState.RELEASED

    disagreeing = (game(release(LAUNCH)), game(release(LAUNCH + timedelta(days=90)), source_id="2"))
    conflicted = first_public_availability(disagreeing)
    assert conflicted.conflict is not None and "90 days" in conflicted.conflict
    after = history.advance(conflicted, NO_EVIDENCE, LAUNCH + timedelta(days=70))

    assert after.current is not None and after.current.state is LifecycleState.RELEASED
    assert LifecycleFlag.LIFECYCLE_CONFLICT in after.current.flags
    assert len(after.records) == 2 and after.records[0].flags == ()


def test_records_within_a_week_of_each_other_do_not_conflict() -> None:
    close = (game(release(LAUNCH)), game(release(LAUNCH + timedelta(days=6)), source_id="2"))
    assert first_public_availability(close).conflict is None


def test_a_status_that_contradicts_a_past_date_is_a_conflict() -> None:
    availability = first_public_availability((game(release(LAUNCH), status="rumored"),))
    derived = derive_state(availability, NO_EVIDENCE, LAUNCH + timedelta(days=90))
    assert derived.state is None and LifecycleFlag.LIFECYCLE_CONFLICT in derived.flags


def test_history_is_append_only_point_in_time_and_carries_the_rule_version() -> None:
    history = LifecycleHistory(canonical_game_id=GAME)
    availability = Availability(first_available=LAUNCH)
    for day in (-10, 0, 10, 45, 46):
        history = history.advance(availability, NO_EVIDENCE, LAUNCH + timedelta(days=day))

    assert [record.state for record in history.records] == [
        LifecycleState.PRE_RELEASE,
        LifecycleState.LAUNCH_WINDOW,
        LifecycleState.RELEASED,
    ]  # an unchanged state appends nothing
    assert {record.rule_version for record in history.records} == {RULE_VERSION}
    assert [r.rule_id for r in history.records] == [
        "first_release_in_future",
        "within_launch_window",
        "released_default",
    ]
    assert history.state_at(LAUNCH - timedelta(days=11)) is None
    assert history.state_at(LAUNCH - timedelta(days=5)) is LifecycleState.PRE_RELEASE
    assert history.state_at(LAUNCH + timedelta(days=20)) is LifecycleState.LAUNCH_WINDOW
    assert history.state_at(LAUNCH + timedelta(days=500)) is LifecycleState.RELEASED


def test_a_change_between_settled_states_must_hold_for_fourteen_days() -> None:
    availability = Availability(first_available=OLD)
    quiet = ActivityEvidence(content_events_last_180d=0, active_day_fraction_last_180d=0.5)
    lively = ActivityEvidence(content_events_last_180d=4, active_day_fraction_last_180d=0.9)
    history = LifecycleHistory(canonical_game_id=GAME).advance(availability, quiet, TODAY)

    for day in (1, 5, 13):
        history = history.advance(availability, lively, TODAY + timedelta(days=day))
        assert history.current is not None and history.current.state is LifecycleState.RELEASED
    assert history.pending_state is LifecycleState.LIVE_SERVICE

    flicker = history.advance(availability, quiet, TODAY + timedelta(days=14))
    assert flicker.pending_state is None and len(flicker.records) == 1  # it did not hold

    settled = history.advance(availability, lively, TODAY + timedelta(days=15))
    assert settled.current is not None and settled.current.state is LifecycleState.LIVE_SERVICE
    assert settled.current.valid_from == TODAY + timedelta(days=15)


def test_dormant_games_reactivate_automatically() -> None:
    availability = Availability(first_available=OLD)
    silent = ActivityEvidence(
        content_events_last_180d=0,
        days_since_last_content_event=500,
        active_day_fraction_last_180d=0.0,
    )
    revived = ActivityEvidence(content_events_last_180d=3, active_day_fraction_last_180d=0.6)
    history = LifecycleHistory(canonical_game_id=GAME).advance(availability, silent, TODAY)
    assert history.current is not None and history.current.state is LifecycleState.DORMANT
    for day in (1, 15):
        history = history.advance(availability, revived, TODAY + timedelta(days=day))
    assert history.current is not None and history.current.state is LifecycleState.LIVE_SERVICE


def test_a_labelled_override_applies_only_while_evidence_is_missing() -> None:
    override = LifecycleOverride(
        canonical_game_id=GAME, state=LifecycleState.LIVE_SERVICE, reason="seed classification"
    )
    availability = Availability(first_available=OLD)
    labelled = LifecycleHistory(canonical_game_id=GAME).advance(
        availability, NO_EVIDENCE, TODAY, override
    )
    assert labelled.current is not None
    assert labelled.current.state is LifecycleState.LIVE_SERVICE
    assert labelled.current.rule_id == "labelled_override"
    assert LifecycleFlag.LABELLED_OVERRIDE in labelled.current.flags

    measured = ActivityEvidence(content_events_last_180d=0, active_day_fraction_last_180d=0.9)
    by_rules = LifecycleHistory(canonical_game_id=GAME).advance(
        availability, measured, TODAY, override
    )
    assert by_rules.current is not None and by_rules.current.state is LifecycleState.EVERGREEN
    assert LifecycleFlag.LABELLED_OVERRIDE not in by_rules.current.flags


def test_an_override_cannot_assert_a_date_derived_state_or_beat_one() -> None:
    with pytest.raises(ValueError, match="only LIVE_SERVICE, EVERGREEN and DORMANT"):
        LifecycleOverride(canonical_game_id=GAME, state=LifecycleState.PRE_RELEASE, reason="no")
    override = LifecycleOverride(
        canonical_game_id=GAME, state=LifecycleState.EVERGREEN, reason="seed classification"
    )
    upcoming = LifecycleHistory(canonical_game_id=GAME).advance(
        Availability(first_available=TODAY + timedelta(days=40)), NO_EVIDENCE, TODAY, override
    )
    assert upcoming.current is not None and upcoming.current.state is LifecycleState.PRE_RELEASE
    launching = LifecycleHistory(canonical_game_id=GAME).advance(
        Availability(first_available=TODAY), NO_EVIDENCE, TODAY, override
    )
    assert launching.current is not None
    assert launching.current.state is LifecycleState.LAUNCH_WINDOW


def test_missing_release_evidence_keeps_the_previous_state() -> None:
    history = LifecycleHistory(canonical_game_id=GAME).started(
        LifecycleState.PRE_RELEASE, date(2026, 10, 4), "bootstrap"
    )
    after = history.advance(Availability(), NO_EVIDENCE, TODAY)
    assert after.current is not None and after.current.state is LifecycleState.PRE_RELEASE
    assert LifecycleFlag.EVIDENCE_MISSING in after.current.flags
    assert after.records[0].flags == (LifecycleFlag.BOOTSTRAP,)
