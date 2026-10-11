"""Normalised signals across sources: families, applicability, breadth and safety.

Steam histories here are SYNTHETIC samples written to a temporary store. Wikimedia
series are synthetic or the recorded fixtures. Nothing calls a network.
"""

import functools
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from metaspike.catalogue.eligibility import SourceEligibility, steam_eligibility
from metaspike.domain.families import (
    ACTIVE_FAMILIES,
    FamilyApplicability,
    SourceFamily,
    UnknownFamilyError,
    family_of,
)
from metaspike.domain.identity import MappingHistory, SourceRecordKey
from metaspike.domain.samples import SampleAttempt, SampleStatus
from metaspike.domain.source_policy import DeploymentStage, SourcePurpose
from metaspike.domain.timeseries import Granularity
from metaspike.features.attention import compute_attention_features
from metaspike.features.config import SeasonalityBasis
from metaspike.features.engine import BaselineState, Maturity
from metaspike.features.normalise import (
    EntityEvidence,
    Freshness,
    NormalisedSignal,
    entity_evidence,
    family_reading,
    unavailable_signal,
)
from metaspike.pipeline.signal_features import (
    STEAM_SLOTS,
    features_from_slots,
    steam_app_features,
    steam_config,
    steam_family,
    wikimedia_config,
    wikimedia_family,
    wikimedia_features,
)
from metaspike.publishing import artifacts
from metaspike.publishing.artifacts import ContractModel
from metaspike.publishing.build import build_bundle
from metaspike.publishing.safety import refuse_unpublishable, refuse_unpublishable_evidence
from metaspike.publishing.schemas import ARTIFACT_MODELS, schema_text
from metaspike.scoring.v0 import score_v0
from metaspike.sources.registry import SourcePolicyError, SourceRegistry
from metaspike.sources.steam_players import STEAM_CURRENT_PLAYERS_POLICY
from metaspike.sources.wikimedia import WIKIMEDIA_PAGEVIEWS_POLICY
from metaspike.storage.samples import LocalSampleStore
from tests.conftest import RUN_TIME, make_id, series_of, spiking, steady
from tests.test_steam_players import catalogue

SOURCE = "steam_current_players"
SLOT = Granularity.FIFTEEN_MINUTES
START = datetime(2026, 9, 1, tzinfo=UTC)  # a Tuesday
HOUR, DAY = timedelta(hours=1), timedelta(days=1)
ENTITY_A, ENTITY_B = make_id(11), make_id(12)
APP, OTHER_APP = "730", "570"


def policy_for(source: str):  # type: ignore[no-untyped-def]
    return {
        "steam_current_players": STEAM_CURRENT_PLAYERS_POLICY,
        "wikimedia_pageviews": WIKIMEDIA_PAGEVIEWS_POLICY,
    }[source]


def players(slot_index: int, base: float = 8_000) -> int:
    moment = START + slot_index * SLOT.step
    level = base * (1 + 0.25 * math.sin(2 * math.pi * moment.hour / 24))
    return int(level * (1.4 if moment.weekday() >= 5 else 1.0))


def sample(
    slot_index: int,
    value: int,
    app: str = APP,
    entity: str = ENTITY_A,
    received_after: timedelta = timedelta(seconds=5),
) -> SampleAttempt:
    at = START + slot_index * SLOT.step + timedelta(minutes=1)
    return SampleAttempt(
        source=SOURCE,
        metric="concurrent_players",
        source_native_id=app,
        value=value,
        granularity=SLOT,
        scheduled_for=SLOT.floor(at),
        slot_start=SLOT.floor(at),
        observed_at=at,
        received_at=at + received_after,
        canonical_game_id=entity,
        mapping_version=1,
        binding_ref="decision:1",
        collection_run_id="synthetic",
        attempt_id=f"{app}-{slot_index}",
        status=SampleStatus.OBSERVED,
    )


def filled_store(root: Path, days: int, app: str = APP, base: float = 8_000) -> LocalSampleStore:
    """A store holding `days` of synthetic 15-minute samples for one app."""
    store = LocalSampleStore(root, lambda _: STEAM_CURRENT_PLAYERS_POLICY)
    directory = root / SOURCE / "concurrent_players" / app
    directory.mkdir(parents=True, exist_ok=True)
    by_month: dict[str, list[str]] = {}
    for index in range(days * 96):
        made = sample(index, players(index, base), app)
        by_month.setdefault(f"{made.slot_start:%Y-%m}", []).append(made.model_dump_json())
    for month, lines in by_month.items():
        with (directory / f"{month}.jsonl").open("a", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")
    return store


@pytest.fixture(scope="module")
def month_store(tmp_path_factory: pytest.TempPathFactory) -> LocalSampleStore:
    return filled_store(tmp_path_factory.mktemp("samples"), days=30)


AS_OF = START + 30 * DAY


@functools.cache
def wikimedia_signal() -> NormalisedSignal:
    return wikimedia_features(series_of(spiking()), RUN_TIME)[0]


# --- families ----------------------------------------------------------------------------


def test_the_family_vocabulary_and_which_families_are_active() -> None:
    assert {family.value for family in SourceFamily} == {
        "wikimedia_readership",
        "steam_players",
        "news_coverage",
        "search_interest",
        "official_events",
        "creator_supply",
    }
    assert ACTIVE_FAMILIES == (SourceFamily.WIKIMEDIA_READERSHIP, SourceFamily.STEAM_PLAYERS)
    assert family_of("wikimedia_pageviews", "pageviews") is SourceFamily.WIKIMEDIA_READERSHIP
    assert family_of(SOURCE, "concurrent_players") is SourceFamily.STEAM_PLAYERS
    with pytest.raises(UnknownFamilyError):
        family_of(SOURCE, "reviews")
    assert steam_config().source_family is SourceFamily.STEAM_PLAYERS
    assert wikimedia_config().source_family is SourceFamily.WIKIMEDIA_READERSHIP
    assert {state.value for state in FamilyApplicability} == {
        "applicable_and_available",
        "applicable_but_missing",
        "inapplicable",
        "unknown",
    }


# --- Steam from the store ----------------------------------------------------------------


def test_a_steam_app_is_normalised_from_its_stored_samples(month_store: LocalSampleStore) -> None:
    signal, state = steam_app_features(month_store, APP, AS_OF)
    assert (signal.source, signal.metric, signal.granularity) == (
        SOURCE,
        "concurrent_players",
        Granularity.HOURLY,
    )
    assert signal.source_family is SourceFamily.STEAM_PLAYERS
    assert signal.rule_version == "baseline_rules_v1" and signal.as_of == AS_OF
    assert signal.valid_through == AS_OF and signal.bucket_start == AS_OF - HOUR
    assert (signal.maturity, signal.freshness) == (Maturity.MATURE, Freshness.FRESH)
    assert signal.seasonality_basis is SeasonalityBasis.HOUR_OF_WEEK
    assert (signal.coverage, signal.baseline_coverage) == (1.0, 1.0)
    for name in (
        "current_level",
        "bucket_level",
        "expected_level",
        "absolute_change",
        "log_lift",
        "percentage_lift",
        "robust_z",
        "trailing_percentile",
        "anomaly_strength",
        "lift_fast",
        "lift_slow",
        "velocity",
        "acceleration",
        "short_trend",
        "long_trend",
    ):
        assert getattr(signal, name).value is not None, name
    assert signal.reason_codes == () and signal.unsupported == ()
    assert signal.robust_z.value is not None and abs(signal.robust_z.value) < 1
    assert signal.change_point.supported
    # The current level is the latest single observation; the bucket level an hourly mean.
    assert signal.current_level.value == players(30 * 96 - 1)
    assert signal.current_level_observed_at == START + (30 * 96 - 1) * SLOT.step + timedelta(
        minutes=1
    )
    assert state.valid_through == AS_OF and signal.observed


def test_an_app_with_no_samples_has_no_history_not_zero(tmp_path: Path) -> None:
    store = LocalSampleStore(tmp_path, lambda _: STEAM_CURRENT_PLAYERS_POLICY)
    signal, _ = steam_app_features(store, APP, AS_OF)
    assert (signal.maturity, signal.freshness) == (Maturity.NO_HISTORY, Freshness.NO_DATA)
    assert signal.current_level.value is None and signal.bucket_level.reason == "no_history"
    assert signal.reason_codes == ("no_history",) and not signal.observed


# --- Wikimedia through the same engine ---------------------------------------------------


def test_daily_pageviews_run_through_the_same_engine_with_daily_semantics_only() -> None:
    signal = wikimedia_signal()
    assert (signal.source, signal.metric, signal.granularity) == (
        "wikimedia_pageviews",
        "pageviews",
        Granularity.DAILY,
    )
    assert signal.source_family is SourceFamily.WIKIMEDIA_READERSHIP
    assert signal.rule_version == "baseline_rules_v1"
    assert signal.maturity is Maturity.MATURE
    assert signal.seasonality_basis is SeasonalityBasis.DAY_OF_WEEK
    assert signal.bucket_level.value == 400.0  # the last day of the synthetic spike
    assert (signal.log_lift.value or 0) > 1.0 and (signal.robust_z.value or 0) > 7
    assert signal.anomaly_strength.value == 1.0
    assert signal.percentage_lift.value is not None and signal.trailing_percentile.value == 100.0
    assert signal.short_trend.value is not None and signal.long_trend.value is not None
    # No daily definition was approved for these, so they do not exist for this signal.
    assert signal.unsupported == (
        "lift_fast",
        "lift_slow",
        "velocity",
        "acceleration",
        "change_point",
    )
    for name in ("lift_fast", "lift_slow", "velocity", "acceleration"):
        assert getattr(signal, name).reason == "unsupported_for_granularity", name
    assert not signal.change_point.supported
    assert signal.reason_codes == ("unsupported_for_granularity",)


def test_the_generic_record_is_parallel_to_v0_and_changes_nothing_in_it() -> None:
    series = series_of(spiking())
    before = compute_attention_features(series)
    score_before = score_v0(before)
    wikimedia_features(series, RUN_TIME)
    wikimedia_features(series_of(steady()), RUN_TIME)
    assert compute_attention_features(series) == before
    assert score_v0(compute_attention_features(series)) == score_before
    quiet = wikimedia_features(series_of(steady()), RUN_TIME)[0]
    assert quiet.robust_z.value is not None and abs(quiet.robust_z.value) < 2
    assert (quiet.anomaly_strength.value or 0) < 0.3


def test_days_not_yet_due_are_not_gaps_and_a_missing_due_day_is_stale() -> None:
    values = steady()
    complete = wikimedia_features(series_of(values), RUN_TIME)[0]
    assert complete.freshness is Freshness.FRESH
    values[-1] = None  # the latest due day never arrived
    late = wikimedia_features(series_of(values), RUN_TIME)[0]
    assert late.freshness is Freshness.STALE and late.log_lift.reason == "stale"
    assert late.maturity is Maturity.MATURE  # staleness says nothing about history


# --- units are never mixed ---------------------------------------------------------------


def test_each_signal_is_normalised_against_its_own_history_only(
    month_store: LocalSampleStore,
) -> None:
    steam, _ = steam_app_features(month_store, APP, AS_OF)
    wikimedia = wikimedia_signal()
    # Raw levels differ by orders of magnitude and are never put on one scale.
    assert (steam.bucket_level.value or 0) > 10 * (wikimedia.bucket_level.value or 0)
    # The normalised quantities are unit-free and comparable: a spike and a quiet hour.
    assert (wikimedia.anomaly_strength.value or 0) > (steam.anomaly_strength.value or 0)
    assert 0 <= (steam.anomaly_strength.value or 0) <= 1 >= (wikimedia.anomaly_strength.value or 0)

    # A game ten times the size, with the same shape, normalises to the same thing.
    big_store = filled_store(month_store._root.parent / "big", days=30, base=80_000)
    big, _ = steam_app_features(big_store, APP, AS_OF)
    assert big.bucket_level.value == pytest.approx(10 * (steam.bucket_level.value or 0), rel=0.001)
    for name in ("log_lift", "robust_z", "anomaly_strength", "velocity", "long_trend"):
        assert getattr(big, name).value == pytest.approx(getattr(steam, name).value, abs=0.01), name
    # And nothing about the Steam history changed the Wikimedia record, or the reverse.
    assert wikimedia_features(series_of(spiking()), RUN_TIME)[0] == wikimedia


# --- applicability and breadth -----------------------------------------------------------


def evidence_for(slug: str, store: LocalSampleStore, with_wikimedia: bool = True) -> EntityEvidence:
    state, records = catalogue()
    entity = next(e for e in steam_eligibility(state, records) if e.slug == slug)
    wikimedia = wikimedia_family(series_of(spiking()) if with_wikimedia else None, RUN_TIME)
    steam = steam_family(store, state.mapping, entity, AS_OF)
    return entity_evidence(entity.canonical_game_id, AS_OF, (wikimedia, steam))


@pytest.fixture(scope="module")
def empty_store(tmp_path_factory: pytest.TempPathFactory) -> LocalSampleStore:
    return LocalSampleStore(
        tmp_path_factory.mktemp("empty"), lambda _: STEAM_CURRENT_PLAYERS_POLICY
    )


@pytest.mark.parametrize("slug", ["star-citizen", "fortnite", "fivem", "gta-vi"])
def test_a_game_steam_cannot_observe_is_not_missing_a_source(
    slug: str, empty_store: LocalSampleStore
) -> None:
    evidence = evidence_for(slug, empty_store)
    steam = evidence.family(SourceFamily.STEAM_PLAYERS)
    assert steam.applicability is FamilyApplicability.INAPPLICABLE
    assert steam.signals == () and steam.reason
    breadth = evidence.evidence_breadth
    assert breadth.inapplicable == (SourceFamily.STEAM_PLAYERS,)
    assert breadth.missing == () and breadth.unknown == ()  # not a missing or failing source
    assert breadth.observed == (SourceFamily.WIKIMEDIA_READERSHIP,)
    assert (breadth.observed_count, breadth.applicable_count) == (1, 1)  # all it can have
    # Its one signal is exactly what it would be for any game: nothing was lowered.
    assert evidence.family(SourceFamily.WIKIMEDIA_READERSHIP).signals == (wikimedia_signal(),)
    (quality,) = evidence.signal_quality_confidence
    assert (quality.maturity, quality.freshness) == (Maturity.MATURE, Freshness.FRESH)


@pytest.mark.parametrize("slug", ["gta-v", "minecraft"])
def test_an_unproven_steam_binding_is_unknown_not_inapplicable(
    slug: str, empty_store: LocalSampleStore
) -> None:
    evidence = evidence_for(slug, empty_store)
    steam = evidence.family(SourceFamily.STEAM_PLAYERS)
    assert steam.applicability is FamilyApplicability.UNKNOWN and steam.signals == ()
    breadth = evidence.evidence_breadth
    assert breadth.unknown == (SourceFamily.STEAM_PLAYERS,)
    assert breadth.inapplicable == () and breadth.missing == ()
    assert (breadth.observed_count, breadth.applicable_count) == (1, 1)


def test_an_eligible_game_with_no_samples_is_applicable_but_missing(
    empty_store: LocalSampleStore,
) -> None:
    evidence = evidence_for("elden-ring", empty_store)
    steam = evidence.family(SourceFamily.STEAM_PLAYERS)
    assert steam.applicability is FamilyApplicability.APPLICABLE_BUT_MISSING
    (signal,) = steam.signals
    assert signal.current_level.value is None and signal.freshness is Freshness.NO_DATA
    breadth = evidence.evidence_breadth
    assert breadth.missing == (SourceFamily.STEAM_PLAYERS,)
    assert (breadth.observed_count, breadth.applicable_count) == (1, 2)
    # Still no effect on the signal that is there.
    assert evidence.family(SourceFamily.WIKIMEDIA_READERSHIP).signals == (wikimedia_signal(),)
    assert len(evidence.signal_quality_confidence) == 1  # nothing to describe for Steam yet


def test_an_eligible_game_with_samples_has_two_independent_families(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    store = filled_store(tmp_path_factory.mktemp("cs"), days=30, app="730")
    state, records = catalogue()
    entity = next(e for e in steam_eligibility(state, records) if e.slug == "counter-strike-2")
    assert entity.state is SourceEligibility.ELIGIBLE and entity.app_ids == ("730",)
    # The synthetic samples predate the recorded binding; bind the app for the whole period.
    mapping = MappingHistory().bind(
        SourceRecordKey(source="steam_catalogue", source_id="730"),
        entity.canonical_game_id,
        START - DAY,
        "synthetic",
    )
    steam = steam_family(store, mapping, entity, AS_OF)
    evidence = entity_evidence(
        entity.canonical_game_id,
        AS_OF,
        (wikimedia_family(series_of(spiking()), RUN_TIME), steam),
        quality_notes={SOURCE: ("values may be cached by the source for several minutes",)},
    )
    assert steam.applicability is FamilyApplicability.APPLICABLE_AND_AVAILABLE
    breadth = evidence.evidence_breadth
    assert breadth.observed == ACTIVE_FAMILIES and breadth.observed_count == 2
    assert breadth.applicable_count == 2 and breadth.unresolved == ()
    assert [q.source_family for q in evidence.signal_quality_confidence] == list(ACTIVE_FAMILIES)
    assert evidence.signal_quality_confidence[1].source_quality_notes != ()
    # Two families are more evidence than one, and the record says so without scoring it.
    single = evidence_for("star-citizen", store).evidence_breadth
    assert breadth.observed_count > single.observed_count
    assert not hasattr(evidence, "confidence") and "confidence" not in type(breadth).model_fields


def test_several_metrics_from_one_family_are_one_voice(month_store: LocalSampleStore) -> None:
    hourly, _ = steam_app_features(month_store, APP, AS_OF)
    another_view = hourly.model_copy(update={"metric": "concurrent_players_daily_peak"})
    third_view = hourly.model_copy(update={"metric": "concurrent_players_velocity"})
    steam = family_reading(
        SourceFamily.STEAM_PLAYERS,
        FamilyApplicability.APPLICABLE_AND_AVAILABLE,
        (hourly, another_view, third_view),
    )
    evidence = entity_evidence(
        ENTITY_A, AS_OF, (wikimedia_family(series_of(spiking()), RUN_TIME), steam)
    )
    assert len(evidence.family(SourceFamily.STEAM_PLAYERS).signals) == 3
    assert evidence.evidence_breadth.observed_count == 2  # three Steam views, one Steam family
    assert len(evidence.signal_quality_confidence) == 4  # quality is still described per signal

    with pytest.raises(ValueError, match="only once"):
        entity_evidence(ENTITY_A, AS_OF, (steam, steam))
    with pytest.raises(ValueError, match="every active family"):
        entity_evidence(ENTITY_A, AS_OF, (steam,))
    with pytest.raises(ValueError, match="another family"):
        family_reading(
            SourceFamily.WIKIMEDIA_READERSHIP,
            FamilyApplicability.APPLICABLE_AND_AVAILABLE,
            (hourly,),
        )


def test_a_stale_family_is_applicable_but_missing(tmp_path: Path) -> None:
    store = filled_store(tmp_path, days=30)
    later = AS_OF + 6 * HOUR  # nothing was sampled for six hours
    signal, _ = steam_app_features(store, APP, later)
    assert signal.freshness is Freshness.STALE and signal.maturity is Maturity.MATURE
    reading = family_reading(
        SourceFamily.STEAM_PLAYERS, FamilyApplicability.APPLICABLE_AND_AVAILABLE, (signal,)
    )
    assert reading.applicability is FamilyApplicability.APPLICABLE_BUT_MISSING


# --- multi-app entities ------------------------------------------------------------------


def test_an_entity_with_two_apps_has_no_combined_feature_but_each_app_has_its_own(
    tmp_path: Path,
) -> None:
    store = filled_store(tmp_path, days=30, app=APP)
    filled_store(tmp_path, days=30, app=OTHER_APP, base=900)
    state, records = catalogue()
    entity = next(e for e in steam_eligibility(state, records) if e.slug == "elden-ring")
    two_apps = entity.model_copy(update={"app_ids": (OTHER_APP, APP)})
    mapping = (
        MappingHistory()
        .bind(
            SourceRecordKey(source="steam_catalogue", source_id=APP),
            entity.canonical_game_id,
            START - DAY,
            "a",
        )
        .bind(
            SourceRecordKey(source="steam_catalogue", source_id=OTHER_APP),
            entity.canonical_game_id,
            START - DAY,
            "b",
        )
    )
    steam = steam_family(store, mapping, two_apps, AS_OF)
    assert steam.reason == "multi_app_combination_unresolved"
    (canonical,) = steam.signals
    assert canonical.subject == entity.canonical_game_id
    for name in ("current_level", "bucket_level", "log_lift", "robust_z", "velocity", "long_trend"):
        feature = getattr(canonical, name)
        assert feature.value is None and feature.reason == "multi_app_combination_unresolved"
    assert canonical.reason_codes == ("multi_app_combination_unresolved",)
    # Each app keeps its own normalised history: not summed, averaged or ranked.
    assert [s.subject for s in steam.per_native] == [OTHER_APP, APP]
    small, large = steam.per_native
    assert small.maturity is large.maturity is Maturity.MATURE
    assert (large.bucket_level.value or 0) > 5 * (small.bucket_level.value or 0)
    assert small == steam_app_features(store, OTHER_APP, AS_OF)[0]
    evidence = entity_evidence(
        entity.canonical_game_id, AS_OF, (wikimedia_family(series_of(spiking()), RUN_TIME), steam)
    )
    assert evidence.evidence_breadth.unresolved == (SourceFamily.STEAM_PLAYERS,)
    assert evidence.evidence_breadth.observed_count == 2  # observed, though not yet combinable
    blank = unavailable_signal(steam_config(), "x", AS_OF, "multi_app_combination_unresolved")
    assert blank.freshness is Freshness.NO_DATA and not blank.observed


# --- no future leakage, through the store and the mapping --------------------------------


def test_a_feature_as_of_a_moment_sees_no_sample_received_after_it(tmp_path: Path) -> None:
    store = filled_store(tmp_path, days=10)
    as_of = START + 10 * DAY
    before, state_before = steam_app_features(store, APP, as_of)

    # A sample observed inside the window, but only stored a day after as_of.
    late_slot = 9 * 96 + 50
    path = next((tmp_path / SOURCE / "concurrent_players" / APP).glob("2026-09.jsonl"))
    late = sample(late_slot, 999_999, received_after=2 * DAY).model_copy(
        update={"attempt_id": "arrived-late"}
    )
    # And samples observed after as_of.
    future = [sample(10 * 96 + index, 5_000_000) for index in range(8)]
    with path.open("a", encoding="utf-8") as stream:
        stream.write("".join(item.model_dump_json() + "\n" for item in (late, *future)))

    after, state_after = steam_app_features(store, APP, as_of)
    assert after == before and state_after == state_before  # the past did not move
    assert (after.bucket_level.value or 0) < 20_000

    # Later, the same store does show them: they were hidden by time, not lost.
    later, _ = steam_app_features(store, APP, as_of + 3 * DAY)
    assert later != before
    slot = store.resolve(STEAM_SLOTS, APP, START + late_slot * SLOT.step, as_of + 3 * DAY)
    assert len(slot.attempts) == 2 and slot.value == players(late_slot)  # the earliest still speaks


def test_a_later_rebinding_does_not_change_an_earlier_attribution(tmp_path: Path) -> None:
    store = filled_store(tmp_path, days=12)
    state, records = catalogue()
    entity = next(e for e in steam_eligibility(state, records) if e.slug == "counter-strike-2")
    app = SourceRecordKey(source="steam_catalogue", source_id=APP)
    owner = entity.canonical_game_id
    bound = MappingHistory().bind(app, owner, START - DAY, "first")
    as_of = START + 8 * DAY
    before = steam_family(store, bound, entity, as_of)

    rebound = bound.bind(app, ENTITY_B, START + 10 * DAY, "second")  # the app moves later
    after = steam_family(store, rebound, entity, as_of)
    assert after == before  # what the entity had at as_of is unchanged
    assert (before.signals[0].bucket_level.value or 0) > 0

    # After the move, the first entity's history simply stops; nothing is rewritten.
    much_later = steam_family(store, rebound, entity, START + 12 * DAY)
    assert much_later.signals[0].freshness is Freshness.STALE
    other = entity.model_copy(update={"canonical_game_id": ENTITY_B})
    moved = steam_family(store, rebound, other, START + 12 * DAY)
    assert moved.signals[0].freshness is Freshness.FRESH
    assert moved.signals[0].maturity is Maturity.WARMING_UP  # it starts its own history


def test_resuming_from_a_state_matches_the_full_history_path(tmp_path: Path) -> None:
    """The fast-path contract: state plus recent samples equals full history."""
    store = filled_store(tmp_path, days=30)
    config = steam_config()
    cut, end = START + 26 * DAY, START + 30 * DAY
    window_all = store.read(STEAM_SLOTS, APP, _window(START, end), end)
    full_signal, full_state = features_from_slots(config, APP, window_all, end)

    head = store.read(STEAM_SLOTS, APP, _window(START, cut), cut)
    _, head_state = features_from_slots(config, APP, head, cut)
    restored = BaselineState.model_validate_json(head_state.model_dump_json())
    assert restored == head_state
    recent = store.read(STEAM_SLOTS, APP, _window(cut, end), end)
    resumed_signal, resumed_state = features_from_slots(config, APP, recent, end, state=restored)
    assert resumed_signal == full_signal
    assert resumed_state.model_dump_json() == full_state.model_dump_json()


def _window(start: datetime, end: datetime):  # type: ignore[no-untyped-def]
    from metaspike.domain.timeseries import TimeWindow

    first = max(start, end - steam_config().history_buckets * HOUR)
    return TimeWindow(start=first, end=end - SLOT.step)


# --- publication safety ------------------------------------------------------------------


def test_steam_derived_features_cannot_be_published(month_store: LocalSampleStore) -> None:
    steam, _ = steam_app_features(month_store, APP, AS_OF)
    assert STEAM_CURRENT_PLAYERS_POLICY.publication_refusal() is not None
    with pytest.raises(SourcePolicyError, match="may enter a published artifact"):
        refuse_unpublishable([steam], policy_for)
    with pytest.raises(SourcePolicyError, match="steam_current_players"):
        refuse_unpublishable([wikimedia_signal(), steam], policy_for)
    refuse_unpublishable([wikimedia_signal()], policy_for)  # pageviews may be

    evidence = entity_evidence(
        ENTITY_A,
        AS_OF,
        (
            wikimedia_family(series_of(spiking()), RUN_TIME),
            family_reading(
                SourceFamily.STEAM_PLAYERS, FamilyApplicability.APPLICABLE_AND_AVAILABLE, (steam,)
            ),
        ),
    )
    with pytest.raises(SourcePolicyError):
        refuse_unpublishable_evidence(evidence, policy_for)
    # The policy itself is unchanged by Part B.
    for stage in (DeploymentStage.PUBLIC_LAUNCH, DeploymentStage.COMMERCIAL_PRODUCTION):
        assert STEAM_CURRENT_PLAYERS_POLICY.registration_refusal(
            SourcePurpose.DEMAND_SIGNALS, stage
        )
    assert not STEAM_CURRENT_PLAYERS_POLICY.cleared_for(DeploymentStage.PRIVATE_COLLECTION)


def test_no_public_artifact_can_carry_a_normalised_record() -> None:
    internal = (NormalisedSignal, EntityEvidence, BaselineState)
    assert not any(issubclass(model, ContractModel) for model in internal)
    schemas = "".join(schema_text(model) for model in ARTIFACT_MODELS.values()).lower()
    for word in (
        "steam",
        "concurrent_players",
        "anomaly_strength",
        "log_lift",
        "evidence_breadth",
        "signal_quality",
        "source_family",
        "seasonality_basis",
        "baseline_rules",
    ):
        assert word not in schemas, word
    # The artifact contract imports nothing from the feature engine.
    source = Path(artifacts.__file__).read_text(encoding="utf-8")
    assert "metaspike.features" not in source and "normalise" not in source
    # And the bundle builder takes no normalised signals at all.
    import inspect

    assert list(inspect.signature(build_bundle).parameters) == [
        "catalogue",
        "report",
        "assessments",
        "registry",
        "generated_at",
        # Event records and coverage checks, evaluated beside the score. Not signals.
        "events",
    ]


def test_a_registry_holding_the_steam_sampler_cannot_build_a_bundle() -> None:
    from metaspike.domain.metrics import sampled_metrics
    from tests.test_sampling import FakeSampler, Ticker

    registry = SourceRegistry(metrics=sampled_metrics())
    registry.register_sampler(FakeSampler(Ticker()), STEAM_CURRENT_PLAYERS_POLICY)
    with pytest.raises(SourcePolicyError, match="steam_current_players"):
        build_bundle(None, None, (), registry, AS_OF)  # type: ignore[arg-type]
