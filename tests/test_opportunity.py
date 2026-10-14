"""Creator-opportunity intelligence, `creator_opportunity_rules_v1` (U6.3). Offline.

Everything synthetic here is labelled so in the output it reaches. Synthetic evidence
never counts toward a real readiness; the SIMULATED readiness beside it shows the rubric
working on invented data and is never a recommendation.
"""

import json
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from metaspike import cli
from metaspike.community_sources.synthetic import build_synthetic_community
from metaspike.creator_sources.synthetic import build_synthetic_creator_supply
from metaspike.domain.families import SourceFamily
from metaspike.evidence.assemble import assess_evidence
from metaspike.evidence.model import EvidenceReport, GameEvidence, Standing
from metaspike.evidence.provenance import local_store_provenance
from metaspike.evidence.synthetic import (
    PAGEVIEW_PATTERNS,
    SyntheticScenario,
    build_synthetic_scenario,
    write_synthetic_pageviews,
)
from metaspike.features.engine import FeatureValue
from metaspike.opportunity.render import render_shortlist
from metaspike.opportunity.rules import (
    NOTEWORTHY_ROBUST_Z,
    OPPORTUNITY_RULES_VERSION,
    Direction,
    Facts,
    OpportunityShortlist,
    Readiness,
    assess_opportunity,
    readiness,
    shortlist,
)
from metaspike.pipeline.collection import collect, window_ending
from metaspike.publishing.artifacts import SCHEMA_VERSION
from metaspike.sources.registry import default_registry
from metaspike.sources.replay import replay_client
from tests.test_evidence import (
    REAL_AS_OF,
    RECORDED_END,
    SYNTHETIC,
    World,
)

REPO = Path(__file__).parent.parent
AS_OF = datetime(2026, 10, 20, 12, tzinfo=UTC)
FIXTURES = REPO / "tests" / "fixtures"
SLUGS = ("rust", "dota-2", "counter-strike-2", "elden-ring", "no-mans-sky", "minecraft")


@pytest.fixture(scope="module")
def world() -> World:
    return World()


def pageviews(world: World, root: Path, moment: datetime) -> object:
    due = (moment - timedelta(days=2)).date()
    articles = {
        identifier.value: PAGEVIEW_PATTERNS[game.slug]
        for game in world.state.catalogue.games
        if game.slug in PAGEVIEW_PATTERNS
        for identifier in game.source_identifiers
        if identifier.source == "wikimedia_pageviews"
    }
    write_synthetic_pageviews(root / "pageviews", articles, due)
    with replay_client(root / "pageviews") as client:
        return collect(
            world.state.catalogue,
            default_registry(client),
            window_ending(due),
            observed_at=moment,
            clock=lambda: moment,
        )


@pytest.fixture(scope="module")
def stores(world: World, tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    root = tmp_path_factory.mktemp("opportunity")
    games = {g.slug: (g.canonical_game_id, g.name) for g in world.state.catalogue.games}
    scenario = build_synthetic_scenario(root, world.state, world.records, AS_OF)
    return {
        "scenario": scenario,
        "creators": build_synthetic_creator_supply(root, games, AS_OF).store,
        "community": build_synthetic_community(root, games, AS_OF).store,
        "pageviews": pageviews(world, root, AS_OF),
    }


def synthetic_inputs(world: World, stores: dict[str, object], **changes: object) -> object:
    scenario = stores["scenario"]
    assert isinstance(scenario, SyntheticScenario)
    base: dict[str, object] = {
        "wikimedia": stores["pageviews"],
        "wikimedia_provenance": SYNTHETIC,
        "samples": scenario.samples,
        "samples_provenance": SYNTHETIC,
        "events": scenario.events,
        "event_sources": scenario.event_sources,
        "events_provenance": SYNTHETIC,
        "creator_supply": stores["creators"],
        "creator_supply_provenance": SYNTHETIC,
        "community": stores["community"],
        "community_provenance": SYNTHETIC,
    }
    base.update(changes)
    return world.inputs(**base)


@pytest.fixture(scope="module")
def report(world: World, stores: dict[str, object]) -> EvidenceReport:
    ids = [world.ids[slug] for slug in SLUGS]
    return assess_evidence(synthetic_inputs(world, stores), AS_OF, ids)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def listed(report: EvidenceReport) -> OpportunityShortlist:
    return shortlist(report)


def of(listed: OpportunityShortlist, world: World, slug: str):  # type: ignore[no-untyped-def]
    return next(a for a in listed.assessments if a.canonical_game_id == world.ids[slug])


# --- the rubric itself ----------------------------------------------------------------------


def test_the_rubric_is_explicit_and_needs_everything_for_a_candidate() -> None:
    assert OPPORTUNITY_RULES_VERSION == "creator_opportunity_rules_v1"
    assert NOTEWORTHY_ROBUST_Z == 3.5  # score v0's own outlier threshold, reused
    up = (SourceFamily.WIKIMEDIA_READERSHIP,)
    whole = Facts(moving_up=up, angle=True, coverage_known=True)
    assert readiness(whole) is Readiness.CANDIDATE
    # Every condition is needed for a candidate.
    for missing in ({"moving_up": ()}, {"angle": False}, {"coverage_known": False}):
        assert readiness(whole.model_copy(update=missing)) is not Readiness.CANDIDATE
    assert readiness(whole.model_copy(update={"disagreement": True})) is Readiness.WATCHLIST
    # One family moving, with nothing else: a watchlist entry, never more.
    assert readiness(Facts(moving_up=up)) is Readiness.WATCHLIST
    # A timely event alone: a reason to look.
    assert readiness(Facts(timely_event=True)) is Readiness.WATCHLIST
    # An angle and coverage without anything saying "now": insufficient.
    assert readiness(Facts(angle=True, coverage_known=True)) is Readiness.INSUFFICIENT
    assert readiness(Facts(moving_down=up)) is Readiness.INSUFFICIENT
    assert readiness(Facts()) is Readiness.INSUFFICIENT


# --- the synthetic shortlist ----------------------------------------------------------------


def test_synthetic_evidence_never_reaches_a_real_readiness(
    world: World, listed: OpportunityShortlist
) -> None:
    assert listed.contains_synthetic and listed.distribution == "private_development_only"
    # The real order: nothing synthetic counts, so nothing is ready, and it says so.
    assert all(entry.readiness is Readiness.INSUFFICIENT for entry in listed.entries)
    assert listed.summary.startswith("No real recommendation can be justified")
    for item in listed.assessments:
        assert item.why_now == ()
        assert item.facts.moving_up == () and not item.facts.timely_event
        assert not item.facts.angle and not item.facts.coverage_known
        assert item.angle_status == "not_yet_established"
        assert all(angle.standing is Standing.SYNTHETIC for angle in item.angles)
        assert all(not angle.confirmed for angle in item.angles)
        assert all(s.standing is not Standing.VERIFIED for s in item.unconfirmed)
    # The simulated order shows the rubric distinguishing the invented cases.
    simulated = {e.slug: e.readiness for e in listed.simulated_entries}
    assert simulated == {
        "dota-2": Readiness.CANDIDATE,  # B, C: two families up, an upcoming event
        "rust": Readiness.WATCHLIST,  # H: up and down at once
        "counter-strike-2": Readiness.INSUFFICIENT,  # falling, stale players
        "no-mans-sky": Readiness.INSUFFICIENT,  # flat
        "elden-ring": Readiness.INSUFFICIENT,
        "minecraft": Readiness.INSUFFICIENT,  # J: nothing at all
    }
    assert [e.slug for e in listed.simulated_entries][:2] == ["dota-2", "rust"]
    text = render_shortlist(listed)
    assert "*** THIS SHORTLIST IS BUILT FROM SYNTHETIC EVIDENCE. ***" in text
    assert "NOT a recommendation" in text and "[SYNTHETIC]" in text


def test_agreement_events_and_disagreement_are_read_from_the_families(
    world: World, listed: OpportunityShortlist
) -> None:
    dota = of(listed, world, "dota-2")
    # B. Two demand families up, each against its own history; no raw values compared.
    assert set(dota.simulated_facts.moving_up) == {
        SourceFamily.WIKIMEDIA_READERSHIP,
        SourceFamily.STEAM_PLAYERS,
    }
    assert all(m.direction is Direction.UP for m in dota.movements)
    assert all(m.robust_z is not None and m.robust_z >= NOTEWORTHY_ROBUST_Z for m in dota.movements)
    # C. An upcoming event, its date, its precision; not its publisher's title.
    (event,) = dota.events
    assert (event.event_type, event.phase, event.time_precision) == ("season", "upcoming", "day")
    assert event.starts_at == datetime(2026, 10, 23, tzinfo=UTC)
    assert dota.simulated_facts.timely_event and dota.simulated_facts.angle
    assert "Season 5" not in dota.model_dump_json()
    # E. Coverage: the bounded sample returned nothing; that is never "low competition".
    assert "not evidence of low competition" in dota.creator_coverage
    assert "low competition" not in dota.creator_coverage.replace(
        "not evidence of low competition", ""
    )
    rust = of(listed, world, "rust")
    # H. One family up, another down: a disagreement, which blocks a candidate.
    assert rust.simulated_facts.moving_up == (SourceFamily.WIKIMEDIA_READERSHIP,)
    assert rust.simulated_facts.moving_down == (SourceFamily.STEAM_PLAYERS,)
    assert rust.simulated_facts.disagreement
    assert "demand families disagree: one is up while another is down" in rust.caveats
    # D. Creator coverage present but bounded and incomplete.
    assert "Incomplete sample" in rust.creator_coverage
    assert "total creator competition is unknown: a sample is a lower bound" in rust.caveats
    cs = of(listed, world, "counter-strike-2")
    # A negative movement is a caveat, and a stale family says nothing about "now".
    assert cs.movements[0].direction is Direction.DOWN
    assert "wikimedia_readership is DOWN against its own history (synthetic)" in cs.caveats
    assert "steam_current_players is stale: its latest observation is not current" in cs.caveats
    assert SourceFamily.STEAM_PLAYERS not in {m.family for m in cs.movements}
    assert of(listed, world, "no-mans-sky").movements[0].direction is Direction.FLAT


def test_angles_cite_what_supports_them_and_say_what_is_assumed(
    world: World, report: EvidenceReport, listed: OpportunityShortlist
) -> None:
    rust = of(listed, world, "rust")
    community = report.game(world.ids["rust"]).community_interest
    assert community is not None
    supported = {d.post_id for d in community.discussions if d.association.value == "supported"}
    patch, offline = [a for a in rust.angles if a.origin == "community"]
    # F. Two community angles, each citing only supported posts.
    assert patch.about == ("version 2.3",) and set(patch.references) <= supported
    assert len(patch.references) == 3 and patch.timely
    assert "offlined" in offline.about and set(offline.references) <= supported
    for angle in (patch, offline):
        assert "total competition is unknown" in " ".join(angle.assumptions)
        assert "nothing here measures that" in " ".join(angle.assumptions)
    # G. A theme seen in one post is not an angle.
    assert "synr006" not in {ref for a in rust.angles for ref in a.references}
    assert community.observed_once == ("synr006",)
    # An event angle cites the event; its id is in the event intelligence.
    dota = of(listed, world, "dota-2")
    (event_angle,) = [a for a in dota.angles if a.origin == "event"]
    contributions = report.game(world.ids["dota-2"]).events.contributions
    assert event_angle.references[0] in {c.evaluation.event_id for c in contributions}
    # A game with no supported angle says so, and invents none.
    assert of(listed, world, "minecraft").angles == ()
    assert "angle: not yet established" in render_shortlist(listed).lower().replace(
        "video angle (verified evidence): not yet established", "angle: not yet established"
    )
    # No post title or body is copied into the output.
    document = listed.model_dump_json()
    for copied in ("offlined every wipe", "borrow checker", "synthetic body", "Hotfix 2.0.1"):
        assert copied not in document, copied


def test_the_order_is_explained_and_stable(
    world: World, report: EvidenceReport, listed: OpportunityShortlist
) -> None:
    assert shortlist(report) == listed  # deterministic
    for entries in (listed.entries, listed.simulated_entries):
        assert [e.position for e in entries] == list(range(1, len(entries) + 1))
        assert all(e.ahead_of_next for e in entries)
        assert entries[-1].ahead_of_next == "last in the list"
    simulated = listed.simulated_entries
    assert simulated[0].ahead_of_next == "readier on the rubric"
    # Two games the evidence cannot tell apart are ordered by id, and it is said so.
    indistinct = [e for e in listed.entries if "indistinguishable" in e.ahead_of_next]
    assert indistinct and all("which means nothing" in e.ahead_of_next for e in indistinct)
    ids = [e.canonical_game_id for e in listed.entries if e.ahead_of_next.startswith("indistinct")]
    assert ids == sorted(ids)
    # The order never depends on input order.
    reversed_report = report.model_copy(update={"games": tuple(reversed(report.games))})
    assert shortlist(reversed_report).entries == listed.entries


# --- verified evidence, unverified evidence -------------------------------------------------


def moved(game: GameEvidence, family: SourceFamily, lift: float, z: float) -> GameEvidence:
    """A copy of a game's evidence with one family's movement replaced. TEST ONLY.

    Used to exercise the rubric on a verified family that moves: no recorded series in
    the repository moves enough to do it. The returned object is never written anywhere.
    """
    readings = []
    for reading in game.demand.families:
        if reading.family is family:
            signal = reading.signals[0]
            changed = signal.signal.model_copy(
                update={"log_lift": FeatureValue(value=lift), "robust_z": FeatureValue(value=z)}
            )
            reading = reading.model_copy(
                update={"signals": (signal.model_copy(update={"signal": changed}),)}
            )
        readings.append(reading)
    return game.model_copy(
        update={"demand": game.demand.model_copy(update={"families": tuple(readings)})}
    )


def test_recorded_evidence_alone_justifies_nothing_today(world: World) -> None:
    """The recorded-only run: real pageviews for six games, at a moment they are fresh."""
    recorded = shortlist(assess_evidence(world.inputs(), REAL_AS_OF, world.scope))
    assert not recorded.contains_synthetic and not recorded.contains_unverified
    assert all(e.readiness is Readiness.INSUFFICIENT for e in recorded.entries)
    assert recorded.summary.startswith("No real recommendation can be justified")
    verified = [m for a in recorded.assessments for m in a.movements]
    # Six verified pageview series, and none moves past the threshold either way.
    assert len(verified) == 6 and all(m.standing is Standing.VERIFIED for m in verified)
    assert {m.direction for m in verified} == {Direction.FLAT}
    assert max(abs(m.robust_z or 0) for m in verified) < NOTEWORTHY_ROBUST_Z
    minecraft = next(a for a in recorded.assessments if a.slug == "minecraft")
    assert minecraft.creator_coverage.startswith("No creator-supply input")
    assert minecraft.community.startswith("No community input")
    assert recorded.entries == recorded.simulated_entries  # nothing to simulate


def test_a_verified_single_source_movement_is_a_watchlist_entry_and_no_more(world: World) -> None:
    """A. Verified attention, one source, no creator or community evidence."""
    game = assess_evidence(world.inputs(), REAL_AS_OF, [world.ids["minecraft"]]).game(
        world.ids["minecraft"]
    )
    rising = assess_opportunity(moved(game, SourceFamily.WIKIMEDIA_READERSHIP, 0.9, 6.0))
    assert rising.readiness is Readiness.WATCHLIST
    assert rising.facts.moving_up == (SourceFamily.WIKIMEDIA_READERSHIP,)
    (why,) = rising.why_now
    assert why.standing is Standing.VERIFIED and "up against its own history" in why.text
    # It never claims low competition or a known community: both are unknown.
    assert rising.creator_coverage.startswith("No creator-supply input")
    assert rising.community.startswith("No community input")
    assert "low competition" not in rising.model_dump_json().replace("not low competition", "")
    assert rising.angle_status == "not_yet_established"
    falling = assess_opportunity(moved(game, SourceFamily.WIKIMEDIA_READERSHIP, -0.9, -6.0))
    assert falling.readiness is Readiness.INSUFFICIENT and falling.facts.moving_down
    assert any("DOWN" in caveat for caveat in falling.caveats)
    # Just under the threshold is flat.
    edge = assess_opportunity(moved(game, SourceFamily.WIKIMEDIA_READERSHIP, 0.5, 3.49))
    assert (
        edge.readiness is Readiness.INSUFFICIENT and edge.movements[0].direction is Direction.FLAT
    )


def test_unverified_events_and_stores_never_explain_now(
    world: World, stores: dict[str, object]
) -> None:
    scenario = stores["scenario"]
    assert isinstance(scenario, SyntheticScenario)
    # The scenario's stores handed over as plain local stores: unverified, then detected.
    inputs = world.inputs(
        samples=scenario.samples,
        samples_provenance=local_store_provenance("s"),
        events=scenario.events,
        event_sources=scenario.event_sources,
        events_provenance=local_store_provenance("e"),
    )
    report = assess_evidence(inputs, AS_OF, [world.ids["dota-2"], world.ids["rust"]])
    for item in shortlist(report).assessments:
        assert item.readiness is Readiness.INSUFFICIENT and item.why_now == ()
        assert all(e.standing is not Standing.VERIFIED for e in item.events)
        assert not item.facts.timely_event


# --- time -----------------------------------------------------------------------------------


def test_an_earlier_assessment_sees_nothing_that_came_later(
    world: World, stores: dict[str, object], listed: OpportunityShortlist, tmp_path: Path
) -> None:
    earlier_at = AS_OF - timedelta(days=2)
    inputs = synthetic_inputs(world, stores, wikimedia=pageviews(world, tmp_path, earlier_at))
    ids = [world.ids[slug] for slug in SLUGS]
    earlier = shortlist(assess_evidence(inputs, earlier_at, ids))  # type: ignore[arg-type]
    # L. Earlier, the announcements, the climb and the searches had not happened.
    dota = of(earlier, world, "dota-2")
    assert dota.events == () and not dota.simulated_facts.timely_event
    assert SourceFamily.STEAM_PLAYERS not in dota.simulated_facts.moving_up
    assert dota.simulated_readiness is not Readiness.CANDIDATE
    rust = of(earlier, world, "rust")
    assert "No creator-supply input" not in rust.creator_coverage
    assert "Not queried" in rust.creator_coverage and "Not queried" in rust.community
    # K. Nothing retrieved after the moment appears in either reading.
    for document in (earlier.model_dump_json(), listed.model_dump_json()):
        for later in ("SYNrust0010", "synr011"):
            assert later not in document, later
    # The same evidence read twice gives the same conclusions.
    again = shortlist(assess_evidence(inputs, earlier_at, ids))  # type: ignore[arg-type]
    assert again == earlier


def test_a_later_rebinding_does_not_reach_an_earlier_assessment(world: World) -> None:
    from tests.test_evidence import rebound

    moved_at = datetime(2026, 10, 12, tzinfo=UTC)
    state, _ = rebound(world, moved_at)
    before = moved_at - timedelta(hours=6)
    ids = [world.ids["dota-2"], world.ids["helldivers-2"]]
    with_later = shortlist(assess_evidence(world.inputs(state=state), before, ids))
    without = shortlist(assess_evidence(world.inputs(), before, ids))
    assert with_later == without


# --- publication, policy, the command -------------------------------------------------------


def test_opportunities_stay_private_and_reach_no_artifact() -> None:
    assert SCHEMA_VERSION == "1.5.0"
    for path in (REPO / "src/metaspike/publishing").glob("*.py"):
        assert "metaspike.opportunity" not in path.read_text(), path.name
    assert not any("opportunit" in p.name for p in (REPO / "schemas").iterdir())
    users = sorted(
        p.relative_to(REPO / "src/metaspike").as_posix()
        for p in (REPO / "src/metaspike").rglob("*.py")
        if "metaspike.opportunity" in p.read_text() and "opportunity" not in p.parts
    )
    assert users == ["cli.py"]
    rules = (REPO / "src/metaspike/opportunity/rules.py").read_text()
    for forbidden in ("score_v0(", "assess_entity", "httpx", "os.environ", "weight ="):
        assert forbidden not in rules, forbidden


def test_the_command_prints_an_explained_private_shortlist_offline(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def no_socket(*_: object, **__: object) -> None:
        raise AssertionError("the opportunity command tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", no_socket)

    def run(*arguments: str) -> tuple[int, str, str]:
        code = cli.main(["evidence", "--as-of", AS_OF.isoformat(), "--opportunities", *arguments])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    games = [arg for slug in ("rust", "dota-2", "minecraft") for arg in ("--game", slug)]
    code, text, _ = run("--synthetic-scenario", *games)
    assert code == cli.EXIT_OK and run("--synthetic-scenario", *games)[1] == text
    assert "creator_opportunity_rules_v1" in text and "Thresholds are UNVALIDATED" in text
    assert "*** THIS SHORTLIST IS BUILT FROM SYNTHETIC EVIDENCE. ***" in text
    assert "No real recommendation can be justified" in text
    assert "Dota_2: synthetic pageviews (rising)" in text
    assert "Not publishable: a game evidence report is never published" in text
    code, printed, _ = run("--synthetic-scenario", *games, "--json")
    document = json.loads(printed)
    assert document["rules_version"] == "creator_opportunity_rules_v1"
    assert document["distribution"] == "private_development_only"
    assert {e["readiness"] for e in document["entries"]} == {"insufficient_evidence"}
    # The recorded-only run, at a moment the recorded pageviews are fresh.
    code = cli.main(
        [
            "evidence",
            "--as-of",
            REAL_AS_OF.isoformat(),
            "--wikimedia-replay",
            str(FIXTURES / "wikimedia"),
            "--recorded-through",
            RECORDED_END.isoformat(),
            "--opportunities",
        ]
    )
    recorded = capsys.readouterr().out
    assert code == cli.EXIT_OK and "SYNTHETIC" not in recorded
    assert "No real recommendation can be justified" in recorded
    assert "Movement [verified]: wikimedia_readership flat" in recorded
    # The artifacts directory is refused for this output too.
    original = cli.DEFAULT_ARTIFACTS_DIR
    cli.DEFAULT_ARTIFACTS_DIR = tmp_path / "artifacts"
    try:
        out = tmp_path / "artifacts" / "shortlist.json"
        code, _, err = run("--synthetic-scenario", "--out", str(out))
    finally:
        cli.DEFAULT_ARTIFACTS_DIR = original
    assert code == cli.EXIT_SOURCE_POLICY and not out.exists() and "never written there" in err


def test_score_v0_is_untouched_by_the_rubric(world: World, report: EvidenceReport) -> None:
    plain = assess_evidence(world.inputs(), AS_OF, [g.canonical_game_id for g in report.games])
    for game in plain.games:
        assessment = assess_opportunity(game)
        assert game.score_v0.score_version == "metaspike_score_v0"
        # The rubric holds no score of any kind, and does not carry score v0's.
        assert not [f for f in type(assessment).model_fields if "score" in f]
        assert str(game.score_v0.value) not in {s.text for s in assessment.why_now}
