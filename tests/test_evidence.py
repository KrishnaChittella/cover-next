"""Multi-source game evidence, end to end and offline (U6.0).

Two kinds of input are used and never confused:

    RECORDED   the catalogue fixtures (IGDB, Steam's app list) and the six pageview
               recordings. Real responses, untouched.
    SYNTHETIC  player samples and announcements written by
               `metaspike.evidence.synthetic`, and a few records altered inside one
               test. Invented, and labelled as invented in every report they reach.

There is no recorded player history in the repository, so everything a test says about
player samples it says about synthetic ones.
"""

import json
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from metaspike import cli
from metaspike.catalogue.eligibility import bootstrap_scope, sampling_targets, steam_eligibility
from metaspike.catalogue.generate import CatalogueState, generate_catalogue
from metaspike.catalogue.persistence import BROAD_DISCOVERY_ENABLED
from metaspike.domain.families import SourceFamily
from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.signals import EntitySignals
from metaspike.domain.source_policy import DeploymentStage, SourcePolicy
from metaspike.domain.source_records import ExternalId, SourceRecord
from metaspike.domain.timeseries import Granularity, TimeWindow
from metaspike.events.heat import Availability as EventAvailability
from metaspike.events.heat import UnavailableReason
from metaspike.events.model import TimePrecision
from metaspike.evidence import synthetic
from metaspike.evidence.assemble import (
    COMMUNITY_INTEREST,
    CREATOR_SUPPLY,
    EvidenceInputs,
    _Assessment,
    assess_evidence,
    pageviews_at,
    publication_refusal,
)
from metaspike.evidence.model import (
    NO_INPUT,
    EvidenceKind,
    EvidenceReport,
    EvidenceState,
    GameEvidence,
    InputProvenance,
    Origin,
)
from metaspike.evidence.render import render
from metaspike.evidence.synthetic import SyntheticScenario, build_synthetic_scenario
from metaspike.pipeline.assess import assess_entity
from metaspike.pipeline.collection import CollectionReport, collect, window_ending
from metaspike.pipeline.history import read_entity_history
from metaspike.pipeline.signal_features import STEAM_SLOTS, steam_family_v2
from metaspike.resolution.recorded import load_recorded_catalogue
from metaspike.scoring.v0 import V0_INPUT_SIGNAL
from metaspike.sources.registry import SourcePolicyError, default_registry
from metaspike.sources.replay import replay_client
from metaspike.sources.steam_players import STEAM_CURRENT_PLAYERS_POLICY
from metaspike.sources.wikimedia import WIKIMEDIA_PAGEVIEWS_POLICY
from metaspike.storage.samples import LocalSampleStore
from metaspike.validation.history import RecordedHistory

REPO = Path(__file__).parent.parent
FIXTURES = REPO / "tests" / "fixtures"
RECORDED_END = date(2026, 10, 3)
# Every recorded catalogue row was fetched at this moment, so bindings begin here.
CATALOGUE_AT = datetime(2026, 10, 5, 2, tzinfo=UTC)
# The pageview recording is complete at this moment: 3 October is due and held.
REAL_AS_OF = datetime(2026, 10, 5, 12, tzinfo=UTC)
# Long after the recording ends, and with two weeks for synthetic samples to cover.
SYNTHETIC_AS_OF = datetime(2026, 10, 20, 12, tzinfo=UTC)
GOLDEN = json.loads((FIXTURES / "score_v0_replay_golden.json").read_text())

WIKIMEDIA = "wikimedia_pageviews"
PLAYERS = "steam_current_players"
NEWS = "steam_news"
RECORDED = InputProvenance(origin=Origin.RECORDED_RESPONSE, reference="tests/fixtures")
SYNTHETIC = InputProvenance(origin=Origin.SYNTHETIC, reference=synthetic.SCENARIO_NAME)


def counting_ids(start: int = 10_000) -> Callable[[], uuid.UUID]:
    issued = [0]

    def factory() -> uuid.UUID:
        issued[0] += 1
        return uuid.UUID(int=start + issued[0], version=4)

    return factory


class World:
    """The recorded catalogue, resolved once, and the recorded pageviews."""

    def __init__(self) -> None:
        self.records: list[SourceRecord] = [
            record
            for name in ("catalogue", "bootstrap_catalogue")
            for record in load_recorded_catalogue(FIXTURES / name).values()
        ]
        self.state = generate_catalogue(self.records, as_of=CATALOGUE_AT, id_factory=counting_ids())
        self.ids = {game.slug: game.canonical_game_id for game in self.state.catalogue.games}
        self.pageviews = recorded_pageviews(self.state)
        # The fifteen original games, which is where the sources overlap.
        self.scope = [
            game.canonical_game_id
            for game in self.state.catalogue.games
            if game.canonical_game_id in bootstrap_scope()
        ]

    def inputs(self, **changes: object) -> EvidenceInputs:
        base = EvidenceInputs(
            state=self.state,
            records=self.records,
            catalogue_provenance=RECORDED,
            state_persisted=False,
            stage=DeploymentStage.PRIVATE_DEVELOPMENT,
            policies=cli.EVIDENCE_POLICIES,
            wikimedia=self.pageviews,
            wikimedia_provenance=RECORDED,
        )
        return replace(base, **changes)  # type: ignore[arg-type]

    def with_scenario(self, scenario: SyntheticScenario, **changes: object) -> EvidenceInputs:
        return self.inputs(
            samples=scenario.samples,
            samples_provenance=SYNTHETIC,
            events=scenario.events,
            event_sources=scenario.event_sources,
            events_provenance=SYNTHETIC,
            **changes,
        )


def recorded_pageviews(state: CatalogueState) -> CollectionReport:
    with replay_client(FIXTURES / "wikimedia") as client:
        return collect(
            state.catalogue,
            default_registry(client),
            window_ending(RECORDED_END),
            observed_at=REAL_AS_OF,
            clock=lambda: REAL_AS_OF,
        )


@pytest.fixture(scope="module")
def world() -> World:
    return World()


@pytest.fixture(scope="module")
def scenario(world: World, tmp_path_factory: pytest.TempPathFactory) -> SyntheticScenario:
    root = tmp_path_factory.mktemp("synthetic-scenario")
    return build_synthetic_scenario(root, world.state, world.records, SYNTHETIC_AS_OF)


@pytest.fixture(scope="module")
def real(world: World) -> EvidenceReport:
    """Recorded inputs only, at a moment the pageview recording fully covers."""
    return assess_evidence(world.inputs(), REAL_AS_OF, world.scope)


@pytest.fixture(scope="module")
def invented(world: World, scenario: SyntheticScenario) -> EvidenceReport:
    """Recorded identity and pageviews beside SYNTHETIC samples and announcements."""
    return assess_evidence(world.with_scenario(scenario), SYNTHETIC_AS_OF, world.scope)


def of(report: EvidenceReport, world: World, slug: str) -> GameEvidence:
    return report.game(world.ids[slug])


# --- A, B: pageviews, and metadata beside them --------------------------------------------


def test_pageviews_alone_are_one_family_and_say_so(world: World) -> None:
    """A. A catalogue with no source records at all, and the recorded pageviews."""
    bootstrap = generate_catalogue()  # the original entities; nothing resolved
    inputs = world.inputs(state=bootstrap, records=[], wikimedia=recorded_pageviews(bootstrap))
    game = assess_evidence(inputs, REAL_AS_OF).game(world.ids["minecraft"])

    pageviews = game.source(WIKIMEDIA)
    assert pageviews.state is EvidenceState.AVAILABLE and pageviews.usable
    assert pageviews.kind is EvidenceKind.DEMAND_SIGNAL
    assert pageviews.family is SourceFamily.WIKIMEDIA_READERSHIP
    assert pageviews.provenance.origin is Origin.RECORDED_RESPONSE
    assert [b.source_native_id for b in pageviews.bindings] == ["Minecraft"]
    assert pageviews.bindings[0].basis == "catalogue_identifier"
    # The last day the source had published by then, and nothing after it.
    assert pageviews.latest_observed_at == datetime(2026, 10, 4, tzinfo=UTC)
    assert pageviews.policy is not None and pageviews.policy.publishable

    assert game.usable_sources == (WIKIMEDIA,)
    assert game.independent_demand_families == 1
    assert game.restricted_sources == () and game.synthetic_sources == ()
    # Nothing else is claimed: no metadata, no players, no events.
    assert game.source("igdb").state is EvidenceState.UNKNOWN
    assert game.source(PLAYERS).state is EvidenceState.UNKNOWN
    assert game.source(NEWS).state is EvidenceState.NOT_COLLECTED
    assert game.identity.bound_records == ()
    codes = {gap.code for gap in game.gaps}
    assert "single_demand_family" in codes and "event_coverage_unavailable" in codes
    # One movement, of one family, against its own history.
    (movement,) = game.movements
    assert movement.family is SourceFamily.WIKIMEDIA_READERSHIP and not movement.synthetic
    assert "log_lift" in movement.features and "change_point" in movement.withheld
    assert "change_point" not in movement.features


def test_metadata_gives_identity_and_is_never_demand(world: World, real: EvidenceReport) -> None:
    """B. IGDB metadata and pageviews for an identity the resolver bound by id."""
    game = of(real, world, "escape-from-tarkov")
    identity = game.identity
    assert identity.canonical_game_id == world.ids["escape-from-tarkov"]
    assert identity.id_persistence == "frozen_bootstrap"
    bound = {(record.source, record.source_id): record for record in identity.bound_records}
    igdb = bound[("igdb", "15536")]
    assert igdb.product_type == "main_game"
    # The release date keeps the precision the source gave it.
    assert igdb.release_dates == ("2025-11-15 (day)",)
    assert igdb.valid_from == CATALOGUE_AT and igdb.mapping_version >= 1
    assert ("steam_catalogue", "3932890") in bound

    metadata = game.source("igdb")
    assert metadata.state is EvidenceState.AVAILABLE
    assert metadata.kind is EvidenceKind.CATALOGUE_METADATA and metadata.family is None
    assert [b.source_native_id for b in metadata.bindings] == ["15536"]
    assert metadata.bindings[0].basis == "mapping_history"
    # Metadata is usable, and it is not a demand family: only pageviews are counted.
    assert game.source(WIKIMEDIA).state is EvidenceState.AVAILABLE
    assert set(game.usable_sources) == {"igdb", "steam_catalogue", WIKIMEDIA}
    assert game.independent_demand_families == 1
    assert [m.family for m in game.movements] == [SourceFamily.WIKIMEDIA_READERSHIP]
    assert {r.family for r in game.demand.families} == {
        SourceFamily.WIKIMEDIA_READERSHIP,
        SourceFamily.STEAM_PLAYERS,
    }
    # A game whose only "evidence" is metadata has no demand evidence at all.
    quiet = of(real, world, "helldivers-2")
    assert quiet.source("igdb").state is EvidenceState.AVAILABLE
    assert quiet.independent_demand_families == 0 and quiet.movements == ()
    assert "no_current_demand_evidence" in {gap.code for gap in quiet.gaps}
    # IGDB and Steam's list are restricted; pageviews are not.
    assert game.restricted_sources == ("igdb", "steam_catalogue")


# --- C to I: player samples ---------------------------------------------------------------


def test_samples_belong_to_the_game_their_app_is_bound_to(
    world: World, invented: EvidenceReport, scenario: SyntheticScenario
) -> None:
    """C. Samples are stored under the app id and reach the game through the binding."""
    dota = of(invented, world, "dota-2")
    players = dota.source(PLAYERS)
    assert players.state is EvidenceState.AVAILABLE
    (binding,) = players.bindings
    assert (binding.source, binding.source_native_id) == ("steam_catalogue", "570")
    assert binding.basis == "mapping_history" and binding.valid_from == CATALOGUE_AT
    assert binding.mapping_version is not None and binding.binding_ref is not None
    (signal,) = dota.demand.family(SourceFamily.STEAM_PLAYERS).signals
    assert signal.signal.subject == world.ids["dota-2"]
    assert [row.source_native_id for row in dota.sample_completeness] == ["570"]
    # The store holds four apps, and each one's samples reach only its own game.
    holders = {
        game.identity.slug: [row.source_native_id for row in game.sample_completeness]
        for game in invented.games
        if game.source(PLAYERS).state in (EvidenceState.AVAILABLE, EvidenceState.STALE)
    }
    assert holders == {
        "dota-2": ["570"],
        "counter-strike-2": ["730"],
        "rust": ["252490"],
        "no-mans-sky": ["275850"],
    }
    # They are synthetic, and the report says so on the source and on the movement.
    assert players.provenance.synthetic and not players.usable
    assert all(m.synthetic for m in dota.movements if m.family is SourceFamily.STEAM_PLAYERS)


def test_a_source_that_cannot_observe_a_game_is_inapplicable(
    world: World, invented: EvidenceReport
) -> None:
    """D. Grand Theft Auto VI has metadata that asserts no Steam app."""
    game = of(invented, world, "gta-vi")
    players = game.source(PLAYERS)
    assert players.state is EvidenceState.INAPPLICABLE
    assert players.reason == "The bound catalogue metadata asserts no Steam app."
    assert players.bindings == () and players.latest_observed_at is None
    # The verdict comes from identity, not from the store, so it is not synthetic.
    assert players.provenance is NO_INPUT and not players.provenance.synthetic
    assert PLAYERS not in game.synthetic_sources
    assert game.source(NEWS).state is EvidenceState.INAPPLICABLE
    breadth = game.demand.evidence_breadth
    assert breadth.inapplicable == (SourceFamily.STEAM_PLAYERS,)
    assert breadth.applicable_count == 1 and breadth.missing == ()
    assert game.sample_completeness == ()


def test_eligible_and_never_sampled_is_missing_and_never_zero(
    world: World, invented: EvidenceReport, real: EvidenceReport
) -> None:
    """E. An eligible app with no sample is missing; with no store at all, not collected."""
    game = of(invented, world, "escape-from-tarkov")
    players = game.source(PLAYERS)
    assert players.state is EvidenceState.MISSING
    assert [b.source_native_id for b in players.bindings] == ["3932890"]
    assert players.latest_observed_at is None and players.freshness == "no_data"
    assert not players.usable
    (row,) = game.sample_completeness
    assert row.current_level is None and not row.zero_reported
    assert row.last_hour.valid_count == 0 and row.last_hour.mean is None
    assert row.last_day.valid_samples == 0 and row.last_day.mean is None
    (signal,) = game.demand.family(SourceFamily.STEAM_PLAYERS).signals
    assert signal.signal.current_level.value is None
    assert signal.signal.bucket_level.value is None
    assert not [m for m in game.movements if m.family is SourceFamily.STEAM_PLAYERS]
    assert game.demand.evidence_breadth.missing == (SourceFamily.STEAM_PLAYERS,)
    # With no sample store given, the same game says something different: not collected.
    without = of(real, world, "escape-from-tarkov").source(PLAYERS)
    assert without.state is EvidenceState.NOT_COLLECTED
    assert without.reason == "no sample store was given to this assessment"
    assert without.provenance is NO_INPUT


def test_a_reported_zero_is_an_observation(world: World, invented: EvidenceReport) -> None:
    """F. Rust's synthetic samples end in two hours of reported zeros."""
    game = of(invented, world, "rust")
    players = game.source(PLAYERS)
    assert players.state is EvidenceState.AVAILABLE and players.freshness == "fresh"
    (row,) = game.sample_completeness
    assert row.zero_reported and row.current_level == 0
    assert row.current_level_observed_at is not None
    assert row.last_hour.valid and row.last_hour.mean == 0.0
    (signal,) = game.demand.family(SourceFamily.STEAM_PLAYERS).signals
    assert signal.signal.current_level.value == 0.0
    # Zero and missing are different states with different numbers behind them.
    missing = of(invented, world, "escape-from-tarkov")
    assert missing.source(PLAYERS).state is EvidenceState.MISSING
    assert missing.sample_completeness[0].current_level is None
    assert "ZERO" in render(invented) and "latest sample is no sample" in render(invented)


def test_stale_and_fresh_are_told_apart_for_each_source(
    world: World, invented: EvidenceReport, real: EvidenceReport
) -> None:
    """G. Sampled players go stale in half an hour; daily pageviews when a due day is missing."""
    fresh = of(invented, world, "dota-2").source(PLAYERS)
    stale = of(invented, world, "counter-strike-2").source(PLAYERS)
    assert (fresh.state, fresh.freshness) == (EvidenceState.AVAILABLE, "fresh")
    assert (stale.state, stale.freshness) == (EvidenceState.STALE, "stale")
    assert fresh.latest_observed_at is not None and stale.latest_observed_at is not None
    assert SYNTHETIC_AS_OF - fresh.latest_observed_at < timedelta(minutes=30)
    assert SYNTHETIC_AS_OF - stale.latest_observed_at > timedelta(hours=3)
    # Pageviews: every due day held on 5 October; sixteen due days missing on the 20th.
    then = of(real, world, "minecraft")
    later = of(invented, world, "minecraft")
    assert then.source(WIKIMEDIA).state is EvidenceState.AVAILABLE
    assert later.source(WIKIMEDIA).state is EvidenceState.STALE
    assert then.source(WIKIMEDIA).latest_observed_at == later.source(WIKIMEDIA).latest_observed_at
    assert then.independent_demand_families == 1 and later.independent_demand_families == 0
    # Score v0 says the same of staleness in its own terms: no confidence left.
    assert then.confidence_v0 is not None and then.confidence_v0.value > 0
    assert later.confidence_v0 is not None and later.confidence_v0.value == 0
    assert "[its pageviews are STALE at this moment]" in render(invented)


def test_incomplete_hours_and_days_are_reported_as_incomplete(
    world: World, invented: EvidenceReport
) -> None:
    """H. Two of four samples in the last hour; eight hours of yesterday never sampled."""
    (partial,) = of(invented, world, "no-mans-sky").sample_completeness
    assert partial.last_hour.valid_count == 2 and partial.last_hour.expected_count == 4
    assert not partial.last_hour.valid and partial.last_hour.mean is None
    assert partial.last_day.valid_samples == 64 and partial.last_day.expected_samples == 96
    assert partial.last_day.valid_hours == 16 and not partial.last_day.valid
    assert partial.last_day.peak is None and partial.last_day.mean is None
    # The source is still being sampled, so it is available; only the bucket is short.
    assert of(invented, world, "no-mans-sky").source(PLAYERS).state is EvidenceState.AVAILABLE
    (whole,) = of(invented, world, "dota-2").sample_completeness
    assert whole.last_hour.valid and whole.last_hour.valid_count == 4
    assert whole.last_day.valid and whole.last_day.valid_samples == 96


def rebound(world: World, at: datetime) -> tuple[CatalogueState, list[SourceRecord]]:
    """SYNTHETIC: app 553850 moved from Helldivers 2 to Dota 2 at `at`.

    A binding is closed and a new one opened, and Dota 2's metadata row is altered to
    list the app, as a correction would. Nothing recorded says this; it is invented to
    exercise a rebinding and a game with two eligible apps.
    """
    app = SourceRecordKey(source="steam_catalogue", source_id="553850")
    mapping = world.state.mapping.bind(app, world.ids["dota-2"], at, "synthetic-rebinding")
    state = world.state.model_copy(update={"mapping": mapping})
    records = [
        record.model_copy(
            update={
                "external_ids": (*record.external_ids, ExternalId(source="steam", value="553850")),
                # A revision: fetched when the correction was made, not before.
                "fetched_at": at,
            }
        )
        if record.key == SourceRecordKey(source="igdb", source_id="2963")
        else record
        for record in world.records
    ]
    return state, records


@pytest.fixture(scope="module")
def two_apps(
    world: World, tmp_path_factory: pytest.TempPathFactory
) -> tuple[EvidenceInputs, datetime, LocalSampleStore]:
    """SYNTHETIC samples of app 553850 across a rebinding, and of app 570 throughout."""
    moved_at = datetime(2026, 10, 12, tzinfo=UTC)
    state, records = rebound(world, moved_at)
    store = LocalSampleStore(
        tmp_path_factory.mktemp("rebinding"), lambda _: STEAM_CURRENT_PLAYERS_POLICY
    )
    targets = {
        target.source_native_id: target
        for target in sampling_targets(world.state, steam_eligibility(world.state, world.records))
    }
    slot = datetime(2026, 10, 9, tzinfo=UTC)
    while slot < datetime(2026, 10, 15, tzinfo=UTC):
        for app in ("553850", "570"):
            store.append(synthetic._attempt(targets[app], slot, 500))
        slot += Granularity.FIFTEEN_MINUTES.step
    inputs = world.inputs(state=state, records=records, samples=store, samples_provenance=SYNTHETIC)
    return inputs, moved_at, store


def test_a_game_with_several_apps_has_no_single_player_count(
    world: World, two_apps: tuple[EvidenceInputs, datetime, LocalSampleStore]
) -> None:
    """I. After the synthetic rebinding Dota 2 has two eligible apps."""
    inputs, moved_at, _ = two_apps
    dota = world.ids["dota-2"]
    game = assess_evidence(inputs, moved_at + timedelta(days=2, hours=12), [dota]).game(dota)
    players = game.source(PLAYERS)
    assert players.state is EvidenceState.UNRESOLVED
    assert players.reason == "multi_app_combination_unresolved"
    assert [b.source_native_id for b in players.bindings] == ["570", "553850"]
    family = game.demand.family(SourceFamily.STEAM_PLAYERS)
    # No entity-level signal exists, and each app keeps its own history.
    assert family.signals == () and len(family.per_native) == 2
    assert {s.signal.subject for s in family.per_native} == {"570", "553850"}
    assert game.demand.evidence_breadth.unresolved == (SourceFamily.STEAM_PLAYERS,)
    assert game.demand.evidence_breadth.observed == ()
    assert not [m for m in game.movements if m.family is SourceFamily.STEAM_PLAYERS]
    assert [row.source_native_id for row in game.sample_completeness] == ["570", "553850"]
    assert game.independent_demand_families == 0 and not players.usable


# --- J, K: identity -----------------------------------------------------------------------


def test_a_quarantined_record_is_never_treated_as_bound(world: World, real: EvidenceReport) -> None:
    """J. Recorded: IGDB's Minecraft row matches by title alone, and stays in quarantine."""
    game = of(real, world, "minecraft")
    assert game.identity.bound_records == ()
    (waiting,) = game.identity.unresolved_records
    assert (waiting.source, waiting.source_id, waiting.reason) == ("igdb", "121", "title_only")
    igdb = game.source("igdb")
    assert igdb.state is EvidenceState.UNRESOLVED and not igdb.usable and igdb.bindings == ()
    assert "in quarantine; none is bound" in (igdb.reason or "")
    # Nothing follows from a record that is not bound: Steam's applicability is unknown,
    # no metadata is shown, and the pageviews stand on the bootstrap identifier alone.
    assert game.source(PLAYERS).state is EvidenceState.UNKNOWN
    assert game.source(WIKIMEDIA).state is EvidenceState.AVAILABLE
    assert "unresolved_source_records" in {gap.code for gap in game.gaps}
    assert "igdb" not in game.usable_sources
    # A title is not an identity anywhere else either: Counter-Strike's older apps.
    cs2 = of(real, world, "counter-strike-2")
    assert ("steam_catalogue", "10", "title_only") in {
        (r.source, r.source_id, r.reason) for r in cs2.identity.unresolved_records
    }
    assert [b.source_native_id for b in cs2.source(PLAYERS).bindings] == ["730"]


def test_a_later_rebinding_changes_nothing_before_it(
    world: World, two_apps: tuple[EvidenceInputs, datetime, LocalSampleStore]
) -> None:
    """K. A sample belongs to the game its app was bound to when it was observed."""
    inputs, moved_at, store = two_apps
    before = moved_at - timedelta(hours=6)
    after = moved_at + timedelta(days=2, hours=12)
    stored = sorted(p.read_bytes() for p in Path(store._root).rglob("*.jsonl"))

    helldivers, dota = world.ids["helldivers-2"], world.ids["dota-2"]

    def read(given: EvidenceInputs, moment: datetime, game_id: str) -> GameEvidence:
        return assess_evidence(given, moment, [game_id]).game(game_id)

    # Read before the rebinding, the later mapping is invisible: with the records as
    # they were, the reading is exactly the one made from the state that never had it.
    original = replace(inputs, state=world.state, records=world.records)
    as_they_were = replace(inputs, records=world.records)
    for game_id in (helldivers, dota):
        assert read(as_they_were, before, game_id) == read(original, before, game_id)
    held = read(inputs, before, helldivers)
    assert held == read(original, before, helldivers)
    assert [b.source_native_id for b in held.source(PLAYERS).bindings] == ["553850"]
    assert held.source(PLAYERS).state is EvidenceState.AVAILABLE
    assert [row.source_native_id for row in held.sample_completeness] == ["553850"]
    # Dota 2's metadata row was re-fetched at the rebinding. Only that later version is
    # held, so before it the row's content is not shown, and nothing is taken from it:
    # Steam's standing reads as unknown instead of borrowing an app from the future.
    earlier = read(inputs, before, dota)
    row = next(r for r in earlier.identity.bound_records if r.source == "igdb")
    assert row.source_id == "2963" and row.product_type is None and row.release_dates == ()
    assert earlier.source(PLAYERS).state is EvidenceState.UNKNOWN
    assert "553850" not in earlier.model_dump_json()

    # Helldivers 2 no longer has the app: its metadata still asserts it, so Steam's
    # standing is unknown, and none of the app's samples are read for it any more.
    left = read(inputs, after, helldivers)
    assert left.source(PLAYERS).state is EvidenceState.UNKNOWN
    assert left.source(PLAYERS).bindings == () and left.sample_completeness == ()
    # Dota 2 holds the app from the rebinding on, and only from then.
    gained = read(inputs, after, dota)
    moved = next(b for b in gained.source(PLAYERS).bindings if b.source_native_id == "553850")
    assert moved.valid_from == moved_at
    window = TimeWindow(
        start=datetime(2026, 10, 9, tzinfo=UTC), end=datetime(2026, 10, 14, 23, 45, tzinfo=UTC)
    )

    def observed_for(slug: str) -> list[datetime]:
        history = read_entity_history(
            store,
            inputs.state.mapping,
            world.ids[slug],
            STEAM_SLOTS,
            "steam_catalogue",
            window,
            after,
        )
        return [
            slot.key.slot_start
            for native in history.by_native_id
            if native.source_native_id == "553850"
            for slot in native.slots
            if slot.selected is not None
        ]

    # Every sample of the moved app before the rebinding is still Helldivers 2's, and
    # every one after it is Dota 2's. None changed hands.
    assert observed_for("helldivers-2") and max(observed_for("helldivers-2")) < moved_at
    assert observed_for("dota-2") and min(observed_for("dota-2")) >= moved_at
    # Five and a half days of slots had been stored by the later moment.
    assert len(observed_for("helldivers-2")) == 3 * 96
    assert len(observed_for("dota-2")) == 2 * 96 + 48
    # And the stored samples were not rewritten.
    assert sorted(p.read_bytes() for p in Path(store._root).rglob("*.jsonl")) == stored


# --- L to O: announcements and events -----------------------------------------------------


def test_an_announcement_that_was_not_promoted_is_only_a_candidate(
    world: World, invented: EvidenceReport, scenario: SyntheticScenario
) -> None:
    """L. Three synthetic posts for Dota 2: two became events, one never did."""
    game = of(invented, world, "dota-2")
    counts = game.candidates
    assert (counts.total, counts.promoted, counts.not_promoted) == (3, 2, 1)
    assert counts.refusals == {"title_not_about_an_occurrence": 1}
    # The candidate is counted and is no part of event intelligence.
    assert len(game.events.contributions) == 2
    titles = {c.evaluation.title for c in game.events.contributions}
    assert not any("diary" in title.lower() for title in titles)
    stored = scenario.event_sources.latest_candidates(
        SYNTHETIC_AS_OF, "event_classification_rules_v1"
    )
    assert sum(c.canonical_game_id == world.ids["dota-2"] for c in stored) == 3
    assert len(scenario.events.events(SYNTHETIC_AS_OF)) == 2
    # No title or text of a post is printed.
    text = render(invented, scenario.description)
    assert "diary" not in text.lower() and "Hotfix" not in text and "Season 5" not in text


def test_a_promoted_event_is_reported_with_its_provenance(
    world: World, invented: EvidenceReport
) -> None:
    """M. A hotfix announced a day earlier: a typed, timed, promoted event."""
    game = of(invented, world, "dota-2")
    assert game.events.availability is EventAvailability.AVAILABLE
    assert game.events.rules_version == "event_heat_rules_v1"
    assert game.events.event_heat is not None and game.events.event_heat > 0
    assert game.events.coverage_sources == (NEWS,)
    types = {c.evaluation.event_type.value for c in game.events.contributions}
    assert types == {"minor_patch", "season"}
    assert all(c.evaluation.sources == (NEWS,) for c in game.events.contributions)
    news = game.source(NEWS)
    assert news.state is EvidenceState.AVAILABLE and news.kind is EvidenceKind.EVENT_EVIDENCE
    assert news.family is SourceFamily.OFFICIAL_EVENTS
    assert news.latest_observed_at == SYNTHETIC_AS_OF - timedelta(hours=1)
    assert [b.source_native_id for b in news.bindings] == ["570"]
    # An event is not demand: it adds nothing to the demand families.
    assert game.independent_demand_families == 0
    assert SourceFamily.OFFICIAL_EVENTS not in {r.family for r in game.demand.families}
    # Looked at, and nothing relevant: available with a heat of zero, not unavailable.
    rust = of(invented, world, "rust")
    assert rust.events.availability is EventAvailability.AVAILABLE
    assert rust.events.event_heat == 0 and rust.candidates.total == 0
    assert rust.source(NEWS).state is EvidenceState.AVAILABLE
    # Never asked: unavailable, and missing rather than zero.
    silent = of(invented, world, "no-mans-sky")
    assert silent.events.availability is EventAvailability.UNAVAILABLE
    assert silent.events.event_heat is None
    assert silent.events.unavailable_reasons == (UnavailableReason.NO_EVENT_COVERAGE,)
    assert silent.source(NEWS).state is EvidenceState.MISSING


def test_an_upcoming_event_carries_the_timing_it_was_given(
    world: World, invented: EvidenceReport
) -> None:
    """N. A season dated three days ahead in its title."""
    (item,) = of(invented, world, "dota-2").upcoming
    assert item.event_type.value == "season"
    assert item.starts_at == datetime(2026, 10, 23, tzinfo=UTC)
    assert item.time_precision is TimePrecision.DAY
    assert item.hours_until_start == 60 and item.sources == (NEWS,)


def test_an_event_whose_timing_is_not_established_is_not_an_event(
    world: World, invented: EvidenceReport
) -> None:
    """O. "Coming soon" has a type and no time, so it is refused and nothing is dated."""
    game = of(invented, world, "counter-strike-2")
    counts = game.candidates
    assert (counts.total, counts.classified, counts.promoted) == (1, 1, 0)
    assert counts.timing_not_established == 1
    assert counts.refusals == {"title_says_not_yet": 1}
    # The source looked, so coverage is current; it found no event, and none is invented.
    assert game.events.availability is EventAvailability.AVAILABLE
    assert game.events.contributions == () and game.events.event_heat == 0
    assert game.upcoming == ()


# --- P: point in time ---------------------------------------------------------------------


def test_a_reading_uses_nothing_later_than_its_moment(
    world: World, scenario: SyntheticScenario, invented: EvidenceReport
) -> None:
    """P. The same stores read at two moments."""
    inputs = world.with_scenario(scenario)
    earlier_at = SYNTHETIC_AS_OF - timedelta(days=2)
    earlier = assess_evidence(inputs, earlier_at, world.scope)
    # Deterministic: the same inputs and moment give the same report, byte for byte.
    pair = [world.ids["dota-2"], world.ids["rust"]]
    again = assess_evidence(inputs, earlier_at, pair)
    assert again.model_dump_json() == assess_evidence(inputs, earlier_at, pair).model_dump_json()
    assert all(game == earlier.game(game.canonical_game_id) for game in again.games)
    assert assess_evidence(inputs, SYNTHETIC_AS_OF, pair).games == tuple(
        invented.game(game_id) for game_id in pair
    )

    then, now = of(earlier, world, "dota-2"), of(invented, world, "dota-2")
    # The announcements were observed an hour before the later moment: not known earlier.
    assert then.candidates.total == 0 and then.upcoming == ()
    assert then.events.availability is EventAvailability.UNAVAILABLE
    assert then.source(NEWS).state is EvidenceState.MISSING
    assert now.candidates.total == 3 and len(now.upcoming) == 1
    # No sample observed after the moment is read.
    for report, moment in ((earlier, earlier_at), (invented, SYNTHETIC_AS_OF)):
        for game in report.games:
            for item in game.sources:
                assert item.latest_observed_at is None or item.latest_observed_at <= moment
            for row in game.sample_completeness:
                assert row.last_hour.end <= moment and row.last_day.end <= moment
                assert (
                    row.current_level_observed_at is None or row.current_level_observed_at <= moment
                )

    # The synthetic climb is in the last day and a half, so the earlier reading lacks it.
    def lift(game: GameEvidence) -> float:
        (signal,) = game.demand.family(SourceFamily.STEAM_PLAYERS).signals
        assert signal.signal.log_lift.value is not None
        return signal.signal.log_lift.value

    assert lift(now) > 0.3 > abs(lift(then))
    # Rust was reporting above zero two days earlier; the zeros had not happened yet.
    assert not of(earlier, world, "rust").sample_completeness[0].zero_reported

    # Bindings: before the catalogue existed there is nothing to read identity from.
    with pytest.raises(ValueError, match="did not exist then"):
        assess_evidence(inputs, CATALOGUE_AT - timedelta(seconds=1))
    with pytest.raises(ValueError, match="timezone-aware"):
        assess_evidence(inputs, datetime(2026, 10, 20, 12))


def test_pageviews_are_cut_to_what_had_been_published(world: World) -> None:
    """P. The publication-lag cut is the backtest's, day for day."""
    lag = WIKIMEDIA_PAGEVIEWS_POLICY.operations.expected_publication_lag
    history = RecordedHistory(world.pageviews, {WIKIMEDIA: lag})
    game_id = world.ids["minecraft"]
    recorded = world.pageviews.signals_for(game_id).get(V0_INPUT_SIGNAL)
    assert recorded is not None and recorded.series is not None
    for moment in (
        datetime(2026, 9, 10, tzinfo=UTC),
        datetime(2026, 9, 28, 17, 30, tzinfo=UTC),
        datetime(2026, 10, 4, 23, 59, tzinfo=UTC),
        datetime(2026, 10, 5, tzinfo=UTC),
    ):
        expected = history.inputs_at(moment).report.signals_for(game_id).get(V0_INPUT_SIGNAL)
        visible = pageviews_at(recorded, moment, lag)
        assert visible == expected, moment
        assert visible.series is not None
        # Nothing the source had not yet published is in it.
        newest = max(p.period_start for p in visible.series.points if p.value is not None)
        assert newest + timedelta(days=1) + lag <= moment
    # On 4 October at noon the 3rd is recorded and not yet published: it is not used.
    early = pageviews_at(recorded, datetime(2026, 10, 4, 12, tzinfo=UTC), lag)
    assert early.series is not None
    assert early.series.latest_due == datetime(2026, 10, 2, tzinfo=UTC)
    assert all(
        point.value is None
        for point in early.series.points
        if point.period_start > early.series.latest_due
    )


# --- Q: policy ----------------------------------------------------------------------------


def test_restricted_evidence_cannot_be_read_for_a_public_stage_or_published(
    world: World,
    scenario: SyntheticScenario,
    invented: EvidenceReport,
    real: EvidenceReport,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Q. The source policies decide, and they fail closed."""
    public = DeploymentStage.PUBLIC_LAUNCH
    # The recorded catalogue alone is refused for a public stage: IGDB is not cleared.
    with pytest.raises(SourcePolicyError, match="igdb: not cleared for public_launch"):
        assess_evidence(world.inputs(stage=public), REAL_AS_OF)
    # With only pageviews and the bootstrap entities, a public stage is allowed: the one
    # source in use is cleared for it.
    bootstrap = generate_catalogue()
    open_inputs = world.inputs(
        state=bootstrap, records=[], wikimedia=recorded_pageviews(bootstrap), stage=public
    )
    assert assess_evidence(open_inputs, REAL_AS_OF).stage is public
    # Each restricted input stops it, by its own policy.
    with pytest.raises(SourcePolicyError, match="steam_current_players: derived metrics"):
        assess_evidence(
            replace(open_inputs, samples=scenario.samples, samples_provenance=SYNTHETIC),
            REAL_AS_OF,
        )
    with pytest.raises(SourcePolicyError, match="steam_news: derived events"):
        assess_evidence(
            replace(open_inputs, events=scenario.events, events_provenance=SYNTHETIC), REAL_AS_OF
        )
    with pytest.raises(SourcePolicyError, match="steam_news"):
        assess_evidence(replace(open_inputs, event_sources=scenario.event_sources), REAL_AS_OF)
    for stage in (DeploymentStage.PRIVATE_COLLECTION, DeploymentStage.COMMERCIAL_PRODUCTION):
        with pytest.raises(SourcePolicyError):
            assess_evidence(world.with_scenario(scenario, stage=stage), SYNTHETIC_AS_OF)
    # No policy, no reading. A policy under another source's name is no policy.
    without = {k: v for k, v in cli.EVIDENCE_POLICIES.items() if k != WIKIMEDIA}
    with pytest.raises(SourcePolicyError, match="wikimedia_pageviews: no source policy"):
        assess_evidence(world.inputs(policies=without), REAL_AS_OF)
    swapped: dict[str, SourcePolicy] = {
        **cli.EVIDENCE_POLICIES,
        PLAYERS: WIKIMEDIA_PAGEVIEWS_POLICY,
    }
    with pytest.raises(SourcePolicyError, match="steam_current_players: no source policy"):
        assess_evidence(world.with_scenario(scenario, policies=swapped), SYNTHETIC_AS_OF)

    # What was read privately says which of it is restricted, from the policies.
    dota = of(invented, world, "dota-2")
    assert dota.restricted_sources == ("igdb", "steam_catalogue", PLAYERS, NEWS)
    assert "evidence_restricted_to_private_development" in {gap.code for gap in dota.gaps}
    views = {view.source: view for view in invented.policies}
    assert views[WIKIMEDIA].publishable and views[WIKIMEDIA].public_launch_refusal is None
    for name in ("igdb", "steam_catalogue", PLAYERS, NEWS):
        assert not views[name].publishable and views[name].public_launch_refusal, name
        assert DeploymentStage.PUBLIC_LAUNCH not in views[name].cleared_stages
    assert views[PLAYERS].cleared_stages == (DeploymentStage.PRIVATE_DEVELOPMENT,)
    # Every report is private, whatever is in it, and says why it cannot be published.
    for report in (real, invented):
        assert report.distribution == "private_development_only"
    assert "steam_current_players, steam_news" in publication_refusal(invented)
    assert "synthetic evidence" in publication_refusal(invented)
    assert publication_refusal(real).startswith("a game evidence report is never published")

    # The publishing path knows nothing of it: no import, no schema, no writer.
    publishing = REPO / "src" / "metaspike" / "publishing"
    assert not any("metaspike.evidence" in path.read_text() for path in publishing.glob("*.py"))
    assert not any("evidence" in path.name for path in (REPO / "schemas").iterdir())
    # The command refuses to write into the artifacts directory, and at a public stage.
    monkey_artifacts = tmp_path / "artifacts"
    target = monkey_artifacts / "v1" / "evidence.json"
    original = cli.DEFAULT_ARTIFACTS_DIR
    cli.DEFAULT_ARTIFACTS_DIR = monkey_artifacts
    try:
        code = cli.main(["evidence", "--as-of", REAL_AS_OF.isoformat(), "--out", str(target)])
    finally:
        cli.DEFAULT_ARTIFACTS_DIR = original
    assert code == cli.EXIT_SOURCE_POLICY and not target.exists()
    assert "never written there" in capsys.readouterr().err
    code = cli.main(["evidence", "--as-of", REAL_AS_OF.isoformat(), "--stage", "public_launch"])
    captured = capsys.readouterr()
    assert code == cli.EXIT_SOURCE_POLICY and captured.out == ""
    assert "not cleared for public_launch" in captured.err


# --- R, S: what is not real, and what is not there ----------------------------------------


def test_synthetic_evidence_is_never_presented_as_an_observation(
    world: World,
    scenario: SyntheticScenario,
    invented: EvidenceReport,
    real: EvidenceReport,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """R. Synthetic inputs are labelled wherever they surface, and count for nothing."""
    assert invented.contains_synthetic and not real.contains_synthetic
    assert invented.inputs[PLAYERS].origin is Origin.SYNTHETIC
    assert invented.inputs[NEWS].origin is Origin.SYNTHETIC
    assert invented.inputs["catalogue"].origin is Origin.RECORDED_RESPONSE
    assert invented.inputs[WIKIMEDIA].origin is Origin.RECORDED_RESPONSE
    for game in invented.games:
        for item in game.sources:
            if item.provenance.synthetic:
                # However current it looks, a synthetic source is never usable.
                assert not item.usable and item.source in game.synthetic_sources
                assert item.source not in game.usable_sources
        # Synthetic player samples never count as a demand family that observes a game.
        real_families = sum(
            item.usable for item in game.sources if item.kind is EvidenceKind.DEMAND_SIGNAL
        )
        assert game.independent_demand_families == real_families
    dota = of(invented, world, "dota-2")
    assert dota.source(PLAYERS).state is EvidenceState.AVAILABLE
    assert dota.independent_demand_families == 0
    assert dota.synthetic_sources == (PLAYERS, NEWS)
    assert "synthetic_evidence" in {gap.code for gap in dota.gaps}
    assert "SYNTHETIC, so it counts for nothing" in " ".join(gap.text for gap in dota.gaps)
    # The engine's own breadth counts it as observed; the report's count does not.
    assert dota.demand.evidence_breadth.observed == (SourceFamily.STEAM_PLAYERS,)

    text = render(invented, scenario.description)
    assert "*** THIS REPORT CONTAINS SYNTHETIC EVIDENCE. ***" in text
    assert "It is not\n  an observation of any game" in text
    assert text.count("[SYNTHETIC") >= 8 and "SYNTHETIC, not observations:" in text
    # Recorded inputs alone produce no such mark anywhere.
    document = real.model_dump_json()
    assert "SYNTHETIC" not in render(real)
    assert '"origin":"synthetic"' not in document and '"synthetic":true' not in document
    assert all(game.synthetic_sources == () for game in real.games)
    # Every synthetic file the scenario wrote says what it is.
    news = Path(scenario.samples._root).parent / "news"
    assert sorted(path.name for path in news.iterdir()) == [
        "news_252490.json",
        "news_570.json",
        "news_730.json",
    ]
    assert all(
        json.loads(path.read_text())["note"].startswith("SYNTHETIC. Written by MetaSpike")
        for path in news.iterdir()
    )
    assert all(
        attempt["collection_run_id"] == "synthetic-scenario"
        and attempt["attempt_id"].startswith("synthetic-")
        for path in Path(scenario.samples._root).rglob("*.jsonl")
        for attempt in map(json.loads, path.read_text().splitlines())
    )
    assert list(Path(scenario.samples._root).rglob("*.jsonl"))
    # Synthetic and stored evidence are never mixed by the command.
    code = cli.main(
        [
            "evidence",
            "--as-of",
            SYNTHETIC_AS_OF.isoformat(),
            "--synthetic-scenario",
            "--sample-store",
            "somewhere",
        ]
    )
    assert code == cli.EXIT_INVALID_CATALOGUE
    assert "never mixed" in capsys.readouterr().err


def test_creator_supply_and_community_interest_are_absent_not_zero(
    invented: EvidenceReport, real: EvidenceReport
) -> None:
    """S. There is no such source, and the report says exactly that for every game."""
    for report in (real, invented):
        for game in report.games:
            for name, kind in (
                (CREATOR_SUPPLY, EvidenceKind.CREATOR_SUPPLY),
                (COMMUNITY_INTEREST, EvidenceKind.COMMUNITY_INTEREST),
            ):
                item = game.source(name)
                assert item.state is EvidenceState.NOT_INTEGRATED and item.kind is kind
                assert not item.usable and not item.contributes
                assert item.latest_observed_at is None and item.bindings == ()
                assert item.policy is None and item.provenance is NO_INPUT
                assert "this is not a zero" in (item.reason or "")
                assert name not in game.usable_sources
            codes = [gap.code for gap in game.gaps]
            assert codes[:3] == [
                "creator_supply_not_integrated",
                "community_interest_not_integrated",
                "opportunity_score_not_defined",
            ]
            # Not a demand family, and not in any count.
            assert SourceFamily.CREATOR_SUPPLY not in {r.family for r in game.demand.families}
    # No number stands in for either anywhere in the report.
    document = json.loads(real.model_dump_json())
    creator = next(s for s in document["games"][0]["sources"] if s["source"] == CREATOR_SUPPLY)
    assert not any(isinstance(value, int | float) for value in creator.values())
    assert "Unknown is not low competition." in render(real)
    # And there is no opportunity score: the report carries score v0 and nothing like it.
    fields = set(GameEvidence.model_fields)
    assert "score_v0" in fields
    assert not {name for name in fields if "opportunity" in name or name == "score"}


# --- T: score v0 --------------------------------------------------------------------------


def test_score_v0_is_what_it_has_always_been(world: World, real: EvidenceReport) -> None:
    """T. The evidence path calls the frozen scorer and reports what it returns."""
    # With the publication lag set to zero the reading at midnight after the recording
    # sees what the published replay saw, and reproduces its frozen scores exactly.
    bootstrap = generate_catalogue()
    policy = WIKIMEDIA_PAGEVIEWS_POLICY
    no_lag = policy.model_copy(
        update={
            "operations": policy.operations.model_copy(
                update={"expected_publication_lag_seconds": 0}
            )
        }
    )
    inputs = world.inputs(
        state=bootstrap,
        records=[],
        wikimedia=recorded_pageviews(bootstrap),
        policies={**cli.EVIDENCE_POLICIES, WIKIMEDIA: no_lag},
    )
    report = assess_evidence(inputs, datetime(2026, 10, 4, tzinfo=UTC))
    assert len(report.games) == 15 == len(GOLDEN["games"])
    scored = 0
    for game in report.games:
        published = GOLDEN["games"][game.identity.slug]
        assert game.score_v0.model_dump(mode="json") == published["score"], game.identity.slug
        if published["confidence"] is None:
            assert game.confidence_v0 is None
            continue
        scored += 1
        assert game.confidence_v0 is not None
        assert game.confidence_v0.model_dump(mode="json") == published["confidence"]
    assert scored == 6

    # Under the real one-day lag it equals the scorer run on the backtest's own inputs.
    lag = policy.operations.expected_publication_lag
    history = RecordedHistory(world.pageviews, {WIKIMEDIA: lag})
    moment = datetime(2026, 10, 5, 3, tzinfo=UTC)
    at_moment = assess_evidence(world.inputs(), moment, world.scope)
    reference = history.inputs_at(moment).report
    for slug in ("minecraft", "gta-vi", "escape-from-tarkov", "counter-strike-2"):
        expected = assess_entity(reference.signals_for(world.ids[slug]))
        game = at_moment.game(world.ids[slug])
        assert game.score_v0 == expected.score and game.confidence_v0 == expected.confidence, slug
    # A game with no pageviews is unscored, never scored zero.
    assert of(real, world, "counter-strike-2").score_v0.value is None
    assert (
        assess_entity(EntitySignals(canonical_game_id=world.ids["fivem"])).score
        == of(real, world, "fivem").score_v0
    )
    # Adding players and events changes no score.
    for game in real.games:
        assert game.score_v0.score_version == "metaspike_score_v0"


def test_other_evidence_does_not_move_score_v0(
    world: World, scenario: SyntheticScenario, invented: EvidenceReport
) -> None:
    plain = assess_evidence(world.inputs(), SYNTHETIC_AS_OF, world.scope)
    for game in invented.games:
        other = plain.game(game.canonical_game_id)
        assert (game.score_v0, game.confidence_v0) == (other.score_v0, other.confidence_v0)


# --- the command --------------------------------------------------------------------------


def run(capsys: pytest.CaptureFixture[str], *arguments: str) -> tuple[int, str, str]:
    code = cli.main(["evidence", *arguments])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_the_command_is_offline_deterministic_and_says_what_it_read(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def no_network(*_: object, **__: object) -> None:
        raise AssertionError("the evidence command must never build a live client")

    monkeypatch.setattr(cli, "build_client", no_network)
    for name in ("STEAM_WEB_API_KEY", "IGDB_CLIENT_ID", "IGDB_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    arguments = (
        "--as-of",
        REAL_AS_OF.isoformat(),
        "--wikimedia-replay",
        str(FIXTURES / "wikimedia"),
        "--recorded-through",
        RECORDED_END.isoformat(),
    )
    code, first, _ = run(capsys, *arguments)
    assert code == cli.EXIT_OK
    assert run(capsys, *arguments)[1] == first  # byte for byte
    assert first.count("\n==========") == 16  # the fifteen bootstrap games, and the footer
    assert "LOCAL, PRIVATE reading. Not an artifact; never published." in first
    assert "no source was called, no credential was read, nothing was stored" in first
    assert (
        "steam_current_players   not given" in first
        and "steam_news              not given" in first
    )
    assert "wikimedia_pageviews     recorded_response" in first
    assert "public launch: derived metrics are not explicitly allowed (unknown)" in first
    assert "creator supply        -                   not_integrated" in first
    assert "SYNTHETIC" not in first
    assert "Not publishable: a game evidence report is never published" in first
    # Nothing was written anywhere by default.
    assert not (Path.cwd() / ".metaspike" / "evidence").exists()

    out = tmp_path / "local" / "evidence.json"
    code, printed, _ = run(capsys, *arguments, "--game", "minecraft", "--json", "--out", str(out))
    assert code == cli.EXIT_OK
    document = json.loads(printed)
    assert json.loads(out.read_text()) == document
    assert document["report_version"] == "game_evidence_v1"
    assert document["distribution"] == "private_development_only"
    assert document["as_of"] == "2026-10-05T12:00:00Z" and not document["contains_synthetic"]
    (game,) = document["games"]
    assert game["identity"]["slug"] == "minecraft"
    assert game["score_v0"]["score_version"] == "metaspike_score_v0"
    # No wall-clock time is in it: every moment is the one asked about, or data's own.
    assert "2026-10-14" not in printed and str(datetime.now(UTC).year + 1) not in printed

    # Entities resolved in memory get ids that are the same on every run, and are marked.
    code, everything, _ = run(capsys, *arguments, "--all", "--json")
    games = json.loads(everything)["games"]
    assert len(games) == 234
    kinds = {game["identity"]["id_persistence"] for game in games}
    assert kinds == {"frozen_bootstrap", "evaluation_only"}
    assert run(capsys, *arguments, "--all", "--json")[1] == everything


def test_the_command_refuses_what_it_cannot_read_honestly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    moment = REAL_AS_OF.isoformat()
    code, out, err = run(capsys, "--as-of", "2026-10-05T12:00:00")
    assert (code, out) == (cli.EXIT_INVALID_CATALOGUE, "") and "UTC offset" in err
    code, out, err = run(capsys, "--as-of", "2026-10-04T00:00:00+00:00")
    assert (code, out) == (cli.EXIT_INVALID_CATALOGUE, "") and "did not exist then" in err
    code, out, err = run(capsys, "--as-of", moment, "--game", "no-such-game")
    assert (code, out) == (cli.EXIT_INVALID_CATALOGUE, "") and "no game with slug" in err
    code, out, err = run(capsys, "--as-of", moment, "--wikimedia-replay", str(FIXTURES))
    assert (code, out) == (cli.EXIT_INVALID_CATALOGUE, "") and "given together" in err
    code, out, err = run(capsys, "--as-of", moment, "--all", "--game", "rust")
    assert (code, out) == (cli.EXIT_INVALID_CATALOGUE, "") and "cannot be given together" in err
    code, out, err = run(
        capsys,
        "--as-of",
        SYNTHETIC_AS_OF.isoformat(),
        "--synthetic-scenario",
        "--stage",
        "public_launch",
    )
    assert (code, out) == (cli.EXIT_SOURCE_POLICY, "")


def test_the_synthetic_command_run_matches_the_library_and_leaves_nothing_behind(
    world: World, capsys: pytest.CaptureFixture[str], invented: EvidenceReport
) -> None:
    arguments = (
        "--as-of",
        SYNTHETIC_AS_OF.isoformat(),
        "--wikimedia-replay",
        str(FIXTURES / "wikimedia"),
        "--recorded-through",
        RECORDED_END.isoformat(),
        "--synthetic-scenario",
        "--game",
        "dota-2",
        "--game",
        "rust",
        "--json",
    )
    code, printed, _ = run(capsys, *arguments)
    assert code == cli.EXIT_OK
    document = json.loads(printed)
    assert document["contains_synthetic"] is True
    assert document["inputs"][PLAYERS] == {
        "origin": "synthetic",
        "reference": "synthetic-overlap-v1",
        "note": "invented; discarded after this run",
    }
    assert "/tmp" not in printed and "metaspike-synthetic-" not in printed
    by_slug = {game["identity"]["slug"]: game for game in document["games"]}
    for slug in ("dota-2", "rust"):
        expected = json.loads(of(invented, world, slug).model_dump_json())
        # The command resolves the catalogue itself; the bootstrap ids are the same.
        assert by_slug[slug]["canonical_game_id"] == expected["canonical_game_id"]
        assert by_slug[slug]["candidates"] == expected["candidates"]
        assert by_slug[slug]["upcoming"] == expected["upcoming"]
        assert by_slug[slug]["sample_completeness"] == expected["sample_completeness"]
        assert by_slug[slug]["demand"] == expected["demand"]


def test_the_evidence_layer_adds_no_rule_of_its_own() -> None:
    """It reads through the existing components and defines no weight, score or threshold."""
    package = REPO / "src" / "metaspike" / "evidence"
    source = "\n".join(path.read_text() for path in sorted(package.glob("*.py")))
    for existing in (
        "steam_family_v2",
        "wikimedia_family_v2",
        "entity_evidence_v2",
        "assess_entity",
        "assess_events",
        "upcoming_radar",
        "usable_features",
        "steam_eligibility",
        "sampling_targets",
        "read_entity_history",
        "require_catalogue_sources",
        "require_event_source",
        "registration_refusal",
    ):
        assert existing in source, existing
    assemble = (package / "assemble.py").read_text()
    for forbidden in ("httpx", "build_client", "os.environ", "Secret", "weight", "def score"):
        assert forbidden not in assemble, forbidden
    assert "Consumer.OPPORTUNITY_SCORE" in assemble  # shadow-only features stay out
    assert "INTERNAL_DIAGNOSTIC" not in assemble
    # Nothing outside the command line and the package itself depends on it.
    users = [
        path.relative_to(REPO).as_posix()
        for path in (REPO / "src" / "metaspike").rglob("*.py")
        if "metaspike.evidence" in path.read_text() and "evidence" not in path.parts
    ]
    assert users == ["src/metaspike/cli.py"]
    assert BROAD_DISCOVERY_ENABLED is False


def test_a_game_s_own_binding_history_reads_the_same_as_the_whole_mapping(
    world: World,
    scenario: SyntheticScenario,
    two_apps: tuple[EvidenceInputs, datetime, LocalSampleStore],
) -> None:
    """The mapping handed to a history read is narrowed to the game's apps. Same answer."""
    rebinding, moved_at, _ = two_apps
    pair = ("dota-2", "helldivers-2")
    cases = (
        (world.with_scenario(scenario), SYNTHETIC_AS_OF, ("dota-2", "rust", "escape-from-tarkov")),
        (rebinding, moved_at - timedelta(hours=6), pair),
        (rebinding, moved_at + timedelta(days=2, hours=12), pair),
    )
    for inputs, moment, slugs in cases:
        assessment = _Assessment(inputs, moment)
        assert inputs.samples is not None
        for slug in slugs:
            eligibility = assessment.eligibility[world.ids[slug]]
            narrow = assessment.mapping_of(world.ids[slug])
            assert 0 < len(narrow.bindings) < len(inputs.state.mapping.bindings)
            assert steam_family_v2(inputs.samples, narrow, eligibility, moment) == steam_family_v2(
                inputs.samples, inputs.state.mapping, eligibility, moment
            ), (slug, moment)
