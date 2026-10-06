"""signal_semantics_v2, and the guard that keeps shadow features out of operations.

Histories are SYNTHETIC samples in a temporary store. Nothing calls a network.
"""

from datetime import timedelta
from pathlib import Path

import pytest

from metaspike.catalogue.eligibility import SteamEligibility, steam_eligibility
from metaspike.domain.families import SourceFamily
from metaspike.domain.identity import MappingHistory, SourceRecordKey
from metaspike.domain.timeseries import Granularity
from metaspike.features.access import (
    OPERATIONAL_CONSUMERS,
    READABLE_FEATURES,
    RULE_METADATA,
    Consumer,
    FeatureAccessError,
    RuleStatus,
    RuleVisibility,
    ShadowFeatureError,
    access_refusal,
    read_feature,
    rule_metadata,
    usable_features,
)
from metaspike.features.engine import ChangePointView, FeatureValue, Maturity
from metaspike.features.normalise import Freshness, entity_evidence
from metaspike.features.semantics import (
    SIGNAL_SEMANTICS_V1,
    SIGNAL_SEMANTICS_V2,
    Applicability,
    Availability,
    EvidenceBreadthV2,
    FamilyReadingV2,
    RawFreshnessBasis,
    entity_evidence_v2,
    family_reading_v2,
)
from metaspike.pipeline.signal_features import (
    steam_app_features,
    steam_app_signal_v2,
    steam_family,
    steam_family_v2,
    wikimedia_family,
    wikimedia_family_v2,
)
from metaspike.publishing.safety import refuse_unpublishable_v2
from metaspike.sources.registry import SourcePolicyError
from metaspike.sources.steam_players import STEAM_CURRENT_PLAYERS_POLICY
from metaspike.storage.samples import LocalSampleStore
from tests.conftest import RUN_TIME, series_of, spiking, steady
from tests.test_normalised_signals import (
    APP,
    AS_OF,
    DAY,
    HOUR,
    OTHER_APP,
    SLOT,
    START,
    filled_store,
    players,
    policy_for,
    sample,
)
from tests.test_steam_players import catalogue

MINUTE = timedelta(minutes=1)
# The last synthetic sample is observed one minute into the final slot before AS_OF.
LAST_SAMPLE = AS_OF - SLOT.step + MINUTE


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> LocalSampleStore:
    return filled_store(tmp_path_factory.mktemp("v2"), days=30)


def bound_entity(
    slug: str = "counter-strike-2", *apps: str
) -> tuple[SteamEligibility, MappingHistory]:
    state, records = catalogue()
    entity = next(e for e in steam_eligibility(state, records) if e.slug == slug)
    mapping = MappingHistory()
    for index, app in enumerate(apps or (APP,)):
        mapping = mapping.bind(
            SourceRecordKey(source="steam_catalogue", source_id=app),
            entity.canonical_game_id,
            START - DAY + index * MINUTE,
            f"synthetic:{app}",
        )
    return entity, mapping


# --- raw freshness versus aggregate freshness --------------------------------------------


def test_raw_freshness_is_measured_on_raw_slots_with_a_thirty_minute_limit(
    store: LocalSampleStore,
) -> None:
    exactly = steam_app_signal_v2(store, APP, LAST_SAMPLE + 30 * MINUTE).raw_freshness
    assert exactly.status is Freshness.FRESH  # 30 minutes old is still fresh
    assert exactly.age_seconds == 1800 and exactly.stale_after_seconds == 1800
    assert exactly.basis is RawFreshnessBasis.RAW_SLOTS
    assert exactly.latest_observed_at == LAST_SAMPLE

    just_over = steam_app_signal_v2(store, APP, LAST_SAMPLE + 30 * MINUTE + timedelta(seconds=1))
    assert just_over.raw_freshness.status is Freshness.STALE  # older than two slots
    assert just_over.raw_freshness.age_seconds == 1801

    fresh = steam_app_signal_v2(store, APP, AS_OF)
    assert fresh.raw_freshness.status is Freshness.FRESH
    assert fresh.raw_freshness.age_seconds == 14 * 60
    assert fresh.semantics_version == SIGNAL_SEMANTICS_V2 == "signal_semantics_v2"
    assert fresh.rule_version == "baseline_rules_v1"


def test_raw_and_aggregate_freshness_are_separate_and_can_disagree(
    store: LocalSampleStore,
) -> None:
    # 45 minutes after the last sample: the SOURCE is stale, the hourly BUCKET is not.
    later = steam_app_signal_v2(store, APP, AS_OF + 31 * MINUTE)
    assert later.raw_freshness.status is Freshness.STALE
    assert later.aggregate_freshness.status is Freshness.FRESH
    assert later.signal.freshness is Freshness.FRESH  # the engine record is the aggregate view
    assert later.signal.log_lift.value is not None  # so its features are still reported

    aggregate = later.aggregate_freshness
    assert aggregate.granularity is Granularity.HOURLY
    assert aggregate.valid_through == AS_OF
    assert aggregate.latest_valid_bucket_start == AS_OF - HOUR
    assert aggregate.bucket_age_seconds == 31 * 60  # since the latest valid hour ended

    # Two full hours with no valid bucket: now the aggregate is stale as well.
    much_later = steam_app_signal_v2(store, APP, AS_OF + 2 * HOUR + MINUTE)
    assert much_later.aggregate_freshness.status is Freshness.STALE
    assert much_later.aggregate_freshness.bucket_age_seconds == 2 * 3600 + 60
    assert much_later.raw_freshness.status is Freshness.STALE
    assert much_later.signal.maturity is Maturity.MATURE  # neither says anything about history


def test_a_source_can_be_fresh_before_its_first_bucket_is_complete(tmp_path: Path) -> None:
    fresh = LocalSampleStore(tmp_path, lambda _: STEAM_CURRENT_PLAYERS_POLICY)
    for index in range(2):  # two samples, half an hour of sampling
        fresh.append(sample(index, players(index)))
    as_of = START + 31 * MINUTE
    signal = steam_app_signal_v2(fresh, APP, as_of)
    assert signal.raw_freshness.status is Freshness.FRESH  # it is being sampled on time
    assert signal.aggregate_freshness.status is Freshness.NO_DATA  # no valid hour exists yet
    assert signal.aggregate_freshness.bucket_age_seconds is None
    assert signal.signal.bucket_level.value is None

    entity, mapping = bound_entity()
    reading = steam_family_v2(fresh, mapping, entity, as_of)
    assert reading.availability is Availability.APPLICABLE_AND_AVAILABLE


def test_daily_pageviews_have_no_finer_raw_layer_and_say_so() -> None:
    reading = wikimedia_family_v2(series_of(spiking()), RUN_TIME)
    (signal,) = reading.signals
    assert signal.raw_freshness.basis is RawFreshnessBasis.SOURCE_PERIODS
    assert signal.raw_freshness.stale_after_seconds is None
    assert signal.raw_freshness.status is signal.aggregate_freshness.status is Freshness.FRESH
    assert reading.availability is Availability.APPLICABLE_AND_AVAILABLE
    values = steady()
    values[-1] = None
    late = wikimedia_family_v2(series_of(values), RUN_TIME)
    assert late.availability is Availability.APPLICABLE_BUT_STALE
    unmapped = wikimedia_family_v2(None, RUN_TIME)
    assert unmapped.availability is Availability.UNKNOWN and unmapped.signals == ()


# --- the six availability states ---------------------------------------------------------


def test_there_are_exactly_six_availability_states() -> None:
    assert {state.value for state in Availability} == {
        "applicable_and_available",
        "applicable_but_missing",
        "applicable_but_stale",
        "inapplicable",
        "unknown",
        "unresolved",
    }


def test_each_condition_gets_its_own_state(store: LocalSampleStore, tmp_path: Path) -> None:
    state, records = catalogue()
    by_slug = {e.slug: e for e in steam_eligibility(state, records)}
    empty = LocalSampleStore(tmp_path / "empty", lambda _: STEAM_CURRENT_PLAYERS_POLICY)
    entity, mapping = bound_entity()

    available = steam_family_v2(store, mapping, entity, AS_OF)
    stale = steam_family_v2(store, mapping, entity, AS_OF + HOUR)
    never = steam_family_v2(empty, mapping, entity, AS_OF)
    inapplicable = steam_family_v2(empty, state.mapping, by_slug["star-citizen"], AS_OF)
    unknown = steam_family_v2(empty, state.mapping, by_slug["gta-v"], AS_OF)

    assert available.availability is Availability.APPLICABLE_AND_AVAILABLE
    assert stale.availability is Availability.APPLICABLE_BUT_STALE
    assert never.availability is Availability.APPLICABLE_BUT_MISSING
    assert inapplicable.availability is Availability.INAPPLICABLE
    assert unknown.availability is Availability.UNKNOWN
    states = [r.availability for r in (available, stale, never, inapplicable, unknown)]
    assert len(set(states)) == 5  # none of them collapses into another

    # Never sampled and stale differ in substance, not only in name.
    assert never.signals[0].raw_freshness.status is Freshness.NO_DATA
    assert never.signals[0].raw_freshness.latest_observed_at is None
    assert stale.signals[0].raw_freshness.status is Freshness.STALE
    assert stale.signals[0].raw_freshness.latest_observed_at == LAST_SAMPLE
    assert stale.signals[0].signal.maturity is Maturity.MATURE
    assert never.signals[0].signal.maturity is Maturity.NO_HISTORY
    assert inapplicable.signals == () and unknown.signals == ()
    for slug in ("fortnite", "fivem", "gta-vi"):
        reading = steam_family_v2(empty, state.mapping, by_slug[slug], AS_OF)
        assert reading.availability is Availability.INAPPLICABLE, slug
    minecraft = steam_family_v2(empty, state.mapping, by_slug["minecraft"], AS_OF)
    assert minecraft.availability is Availability.UNKNOWN


def test_unknown_and_unresolved_are_different_things(tmp_path: Path) -> None:
    two = filled_store(tmp_path, days=30, app=APP)
    filled_store(tmp_path, days=30, app=OTHER_APP, base=900)
    entity, mapping = bound_entity("elden-ring", APP, OTHER_APP)
    unresolved = steam_family_v2(two, mapping, entity, AS_OF)
    state, records = catalogue()
    gta_v = next(e for e in steam_eligibility(state, records) if e.slug == "gta-v")
    unknown = steam_family_v2(two, state.mapping, gta_v, AS_OF)

    assert unresolved.availability is Availability.UNRESOLVED
    assert unknown.availability is Availability.UNKNOWN
    assert unresolved.reason == "multi_app_combination_unresolved"
    assert "271590" in (unknown.reason or "")  # unproven binding, not unresolved semantics
    # Unknown has no data at all; unresolved has two complete histories it will not merge.
    assert unknown.per_native == () and unknown.signals == ()
    assert unresolved.signals == ()  # no entity-level value: not a sum, average or pick
    assert [s.signal.subject for s in unresolved.per_native] == [OTHER_APP, APP]
    for per_app in unresolved.per_native:
        assert per_app.raw_freshness.status is Freshness.FRESH
        assert per_app.signal.maturity is Maturity.MATURE
        assert per_app.signal.log_lift.value is not None
    assert unresolved.per_native[1] == steam_app_signal_v2(two, APP, AS_OF)
    with pytest.raises(ValueError, match="no entity-level signal"):
        family_reading_v2(
            SourceFamily.STEAM_PLAYERS, Applicability.UNRESOLVED, signals=unresolved.per_native
        )


# --- evidence breadth --------------------------------------------------------------------


def test_an_unresolved_family_adds_nothing_to_observed_breadth(tmp_path: Path) -> None:
    two = filled_store(tmp_path, days=30, app=APP)
    filled_store(tmp_path, days=30, app=OTHER_APP, base=900)
    entity, mapping = bound_entity("elden-ring", APP, OTHER_APP)
    wikimedia = wikimedia_family_v2(series_of(spiking()), RUN_TIME)
    evidence = entity_evidence_v2(
        entity.canonical_game_id, AS_OF, (wikimedia, steam_family_v2(two, mapping, entity, AS_OF))
    )
    breadth = evidence.evidence_breadth
    assert breadth.unresolved == (SourceFamily.STEAM_PLAYERS,)
    assert breadth.observed == (SourceFamily.WIKIMEDIA_READERSHIP,)
    assert breadth.observed_count == 1  # Steam contributes zero, despite two live apps
    assert breadth.applicable_count == 2 and breadth.missing == () and breadth.stale == ()
    assert evidence.semantics_version == "signal_semantics_v2"
    assert evidence.rule_version == "baseline_rules_v1"

    # The same entity with ONE app is observed by two families.
    single_entity, single_mapping = bound_entity("elden-ring", APP)
    single = entity_evidence_v2(
        single_entity.canonical_game_id,
        AS_OF,
        (wikimedia, steam_family_v2(two, single_mapping, single_entity, AS_OF)),
    )
    assert single.evidence_breadth.observed_count == 2
    assert single.evidence_breadth.unresolved == ()


def test_breadth_lists_every_state_separately_and_inapplicable_is_not_missing(
    store: LocalSampleStore, tmp_path: Path
) -> None:
    state, records = catalogue()
    by_slug = {e.slug: e for e in steam_eligibility(state, records)}
    empty = LocalSampleStore(tmp_path, lambda _: STEAM_CURRENT_PLAYERS_POLICY)
    wikimedia = wikimedia_family_v2(series_of(spiking()), RUN_TIME)
    entity, mapping = bound_entity()

    def breadth(steam: FamilyReadingV2) -> EvidenceBreadthV2:
        return entity_evidence_v2(
            entity.canonical_game_id, AS_OF, (wikimedia, steam)
        ).evidence_breadth

    star_citizen = breadth(steam_family_v2(empty, state.mapping, by_slug["star-citizen"], AS_OF))
    assert star_citizen.inapplicable == (SourceFamily.STEAM_PLAYERS,)
    assert (star_citizen.observed_count, star_citizen.applicable_count) == (1, 1)
    assert star_citizen.missing == star_citizen.stale == star_citizen.unknown == ()

    stale = breadth(steam_family_v2(store, mapping, entity, AS_OF + HOUR))
    assert stale.stale == (SourceFamily.STEAM_PLAYERS,) and stale.missing == ()
    assert (stale.observed_count, stale.applicable_count) == (1, 2)

    never = breadth(steam_family_v2(empty, mapping, entity, AS_OF))
    assert never.missing == (SourceFamily.STEAM_PLAYERS,) and never.stale == ()

    both = breadth(steam_family_v2(store, mapping, entity, AS_OF))
    assert both.observed_count == both.applicable_count == 2
    with pytest.raises(ValueError, match="every active family"):
        entity_evidence_v2(entity.canonical_game_id, AS_OF, (wikimedia,))
    with pytest.raises(ValueError, match="only once"):
        entity_evidence_v2(entity.canonical_game_id, AS_OF, (wikimedia, wikimedia))


# --- signal_semantics_v1 is still there, unchanged ---------------------------------------


def test_semantics_v1_is_still_callable_and_says_what_it_always_said(tmp_path: Path) -> None:
    two = filled_store(tmp_path, days=30, app=APP)
    filled_store(tmp_path, days=30, app=OTHER_APP, base=900)
    entity, mapping = bound_entity("elden-ring", APP, OTHER_APP)
    assert SIGNAL_SEMANTICS_V1 == "signal_semantics_v1"

    # v1 reading of a multi-app entity: observed, and listed as unresolved. Reproducible.
    old = entity_evidence(
        entity.canonical_game_id,
        AS_OF,
        (
            wikimedia_family(series_of(spiking()), RUN_TIME),
            steam_family(two, mapping, entity, AS_OF),
        ),
    )
    assert old.evidence_breadth.observed_count == 2
    assert old.evidence_breadth.unresolved == (SourceFamily.STEAM_PLAYERS,)

    # v1 freshness: the hourly rule, under which a 45-minute-old source is still fresh.
    v1_signal, _ = steam_app_features(two, APP, AS_OF + 31 * MINUTE)
    assert v1_signal.freshness is Freshness.FRESH
    # v2 wraps that very record without recomputing a single field of it.
    v2 = steam_app_signal_v2(two, APP, AS_OF + 31 * MINUTE)
    assert v2.signal == v1_signal
    assert v2.raw_freshness.status is Freshness.STALE  # the v2 addition


def test_v2_records_are_no_more_publishable_than_v1_records(tmp_path: Path) -> None:
    two = filled_store(tmp_path, days=30, app=APP)
    filled_store(tmp_path, days=30, app=OTHER_APP, base=900)
    wikimedia = wikimedia_family_v2(series_of(spiking()), RUN_TIME)
    single_entity, single_mapping = bound_entity()
    multi_entity, multi_mapping = bound_entity("elden-ring", APP, OTHER_APP)
    for entity, mapping in ((single_entity, single_mapping), (multi_entity, multi_mapping)):
        evidence = entity_evidence_v2(
            entity.canonical_game_id,
            AS_OF,
            (wikimedia, steam_family_v2(two, mapping, entity, AS_OF)),
        )
        with pytest.raises(SourcePolicyError, match="steam_current_players"):
            refuse_unpublishable_v2(evidence, policy_for)  # including per-app histories


# --- the shadow-only guard ---------------------------------------------------------------


def test_baseline_rules_v1_is_internal_reproducible_and_uncalibrated() -> None:
    metadata = rule_metadata("baseline_rules_v1")
    assert metadata.status is RuleStatus.UNCALIBRATED
    assert metadata.visibility is RuleVisibility.INTERNAL
    assert metadata.reproducible and metadata.shadow_only == ("change_point",)
    assert "eight weeks" in metadata.note and "28 days is a sanity check" in metadata.note
    assert set(RULE_METADATA) == {"baseline_rules_v1"}
    assert {
        Consumer.OPPORTUNITY_SCORE,
        Consumer.EVENT_HEAT,
        Consumer.MONITORING_TIER,
        Consumer.NOTIFICATION,
        Consumer.PUBLIC_ARTIFACT,
    } == OPERATIONAL_CONSUMERS


@pytest.mark.parametrize("consumer", sorted(OPERATIONAL_CONSUMERS))
def test_no_operational_consumer_can_read_a_shadow_change_point(
    consumer: Consumer, store: LocalSampleStore
) -> None:
    v1_signal, _ = steam_app_features(store, APP, AS_OF)
    v2_signal = steam_app_signal_v2(store, APP, AS_OF)
    assert v1_signal.change_point.supported  # it exists and is computed; it is just not usable
    for signal in (v1_signal, v2_signal):
        with pytest.raises(FeatureAccessError) as refused:
            read_feature(signal, "change_point", consumer)
        if consumer is not Consumer.PUBLIC_ARTIFACT:
            assert isinstance(refused.value, ShadowFeatureError)
            assert "shadow-only" in str(refused.value) and consumer.value in str(refused.value)
        assert "change_point" not in usable_features(signal, consumer)
    assert access_refusal("baseline_rules_v1", "change_point", consumer) is not None


def test_operational_consumers_can_still_read_what_is_not_shadow(store: LocalSampleStore) -> None:
    signal = steam_app_signal_v2(store, APP, AS_OF)
    for consumer in (
        Consumer.OPPORTUNITY_SCORE,
        Consumer.EVENT_HEAT,
        Consumer.MONITORING_TIER,
        Consumer.NOTIFICATION,
    ):
        lift = read_feature(signal, "log_lift", consumer)
        assert isinstance(lift, FeatureValue) and lift.value is not None
        usable = usable_features(signal, consumer)
        assert set(usable) == set(READABLE_FEATURES) - {"change_point"}
    # A public artifact gets nothing at all from an internal rule version.
    assert usable_features(signal, Consumer.PUBLIC_ARTIFACT) == {}
    with pytest.raises(FeatureAccessError, match="internal"):
        read_feature(signal, "log_lift", Consumer.PUBLIC_ARTIFACT)


def test_diagnostics_and_calibration_may_read_the_shadow_feature(store: LocalSampleStore) -> None:
    signal = steam_app_signal_v2(store, APP, AS_OF)
    for consumer in (Consumer.INTERNAL_DIAGNOSTIC, Consumer.CALIBRATION):
        view = read_feature(signal, "change_point", consumer)
        assert isinstance(view, ChangePointView) and view.supported
        assert set(usable_features(signal, consumer)) == set(READABLE_FEATURES)


def test_the_guard_refuses_what_it_does_not_know(store: LocalSampleStore) -> None:
    signal, _ = steam_app_features(store, APP, AS_OF)
    with pytest.raises(FeatureAccessError, match="not a feature"):
        read_feature(signal, "baselines", Consumer.OPPORTUNITY_SCORE)
    unknown_rule = signal.model_copy(update={"rule_version": "baseline_rules_v7"})
    with pytest.raises(FeatureAccessError, match="no status is recorded"):
        read_feature(unknown_rule, "log_lift", Consumer.INTERNAL_DIAGNOSTIC)
    # Calibrating a rule is what lifts the restriction, and only for that rule version.
    RULE_METADATA["baseline_rules_v2"] = rule_metadata("baseline_rules_v1").model_copy(
        update={"rule_version": "baseline_rules_v2", "status": RuleStatus.CALIBRATED}
    )
    try:
        assert access_refusal("baseline_rules_v2", "change_point", Consumer.EVENT_HEAT) is None
        assert access_refusal("baseline_rules_v1", "change_point", Consumer.EVENT_HEAT) is not None
    finally:
        del RULE_METADATA["baseline_rules_v2"]
