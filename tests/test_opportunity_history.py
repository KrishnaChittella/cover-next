"""Offline historical evaluation of creator_opportunity_rules_v1 (U6.5).

Everything named "synthetic" here is invented and labelled: invented games, invented
pageviews, invented discussions, samples and events. The recorded tests replay the
pinned Wikimedia recording and nothing else; no source is contacted.
"""

import json
import socket
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from metaspike import cli
from metaspike.catalogue.seed import load_seed_catalogue
from metaspike.domain.games import Catalogue
from metaspike.domain.source_policy import DeploymentStage
from metaspike.evidence.assemble import assess_evidence
from metaspike.evidence.model import InputProvenance, Origin
from metaspike.evidence.provenance import local_store_provenance, recording_provenance
from metaspike.opportunity import history as history_module
from metaspike.opportunity.history import (
    ATTENTION_PROXY,
    CREATOR_SUCCESS_UNVALIDATED,
    EXPLORATORY_ROBUST_Z,
    NOT_ESTIMABLE_NO_CANDIDATES,
    OPPORTUNITY_SNAPSHOT_FORMAT,
    ContextFamily,
    ContextObservation,
    EvaluationMode,
    HistoryRefused,
    MissingReason,
    OpportunityHistoryReport,
    OpportunityOutcome,
    OpportunitySnapshot,
    OutcomeStatus,
    Reconstruction,
    evaluate_history,
    render_history,
    share,
)
from metaspike.opportunity.history_synthetic import (
    QUIET_DAYS,
    SCENARIO_DAYS,
    SYNTHETIC_HISTORY,
    at,
    creator_sample,
    day,
    discussion,
    event,
    pageview_recording,
    steady,
    synthetic_game,
    synthetic_history,
)
from metaspike.opportunity.rules import (
    NOTEWORTHY_ROBUST_Z,
    TIMELY_DISCUSSION,
    Direction,
    Readiness,
    movement_direction,
    shortlist,
)
from metaspike.pipeline.collection import CollectionReport, collect, window_ending
from metaspike.scoring.v0 import ROBUST_Z_OUTLIER_THRESHOLD, V0_INPUT_SIGNAL
from metaspike.sources.registry import default_registry
from metaspike.sources.replay import replay_client
from metaspike.validation.backtest import perturb_unavailable, prediction_moment, run_backtest
from metaspike.validation.history import first_forward_day
from metaspike.validation.rules import OUTCOME_RULES_V1, PROTOCOL_VERSION
from metaspike.validation.snapshot import SNAPSHOT_FORMAT
from tests.conftest import RECORDED_END, RECORDINGS, RUN_TIME
from tests.test_evidence import REAL_AS_OF, World

WHEN = datetime(2030, 6, 1, tzinfo=UTC)
LAG = {V0_INPUT_SIGNAL.source: timedelta(days=1)}
SYNTHETIC = EvaluationMode.SYNTHETIC
# With a one-day lag, the moment at 00:00 on day 62 is the first to see day 60.
SEES_RISE = QUIET_DAYS + 2


# --- helpers ------------------------------------------------------------------------------


def synthetic_run(
    context: Sequence[ContextObservation] | None = None, **changes: object
) -> OpportunityHistoryReport:
    invented = synthetic_history()
    arguments: dict[str, object] = {
        "catalogue": invented.catalogue,
        "recording": invented.recording,
        "publication_lag": invented.publication_lag,
        "first_day": invented.first_day,
        "last_day": invented.last_day,
        "cadence_days": 1,
        "universe": [game.slug for game in invented.catalogue.games],
        "demand_origin": SYNTHETIC_HISTORY,
        "mode": SYNTHETIC,
        "created_at": WHEN,
        "context": invented.context if context is None else context,
    }
    arguments.update(changes)
    return evaluate_history(**arguments)  # type: ignore[arg-type]


def at_moment(
    report: OpportunityHistoryReport, slug: str, index: int
) -> tuple[OpportunitySnapshot, OpportunityOutcome]:
    snapshot = next(
        s for s in report.snapshots if s.slug == slug and s.as_of == prediction_moment(day(index))
    )
    outcome = next(o for o in report.outcomes if o.snapshot_fingerprint == snapshot.fingerprint)
    return snapshot, outcome


def missing(snapshot: OpportunitySnapshot, family: str) -> MissingReason | None:
    return next((m.reason for m in snapshot.missing if m.family == family), None)


def one_game(
    values: Sequence[float | None], context: Sequence[ContextObservation] = (), **changes: object
) -> tuple[OpportunityHistoryReport, str]:
    """One invented game, named synthetic-golf, over the scenario's moments."""
    game = synthetic_game(7, "synthetic-golf")
    catalogue = Catalogue(games=(game,))
    recording = pageview_recording((game,), {game.slug: values}, at(SCENARIO_DAYS + 5))
    arguments: dict[str, object] = {
        "catalogue": catalogue,
        "recording": recording,
        "publication_lag": LAG,
        "first_day": day(QUIET_DAYS - 2),
        "last_day": day(SCENARIO_DAYS + 2),
        "cadence_days": 1,
        "universe": [game.slug],
        "demand_origin": SYNTHETIC_HISTORY,
        "mode": SYNTHETIC,
        "created_at": WHEN,
        "context": context,
    }
    arguments.update(changes)
    return evaluate_history(**arguments), game.canonical_game_id  # type: ignore[arg-type]


def quiet_then(rest: Sequence[float]) -> list[float | None]:
    tail = list(rest) + [rest[-1]] * (SCENARIO_DAYS - QUIET_DAYS - len(rest))
    return [*steady(QUIET_DAYS), *tail]


@pytest.fixture(scope="module")
def synthetic() -> OpportunityHistoryReport:
    return synthetic_run()


def real_recording() -> tuple[Catalogue, CollectionReport, dict[str, timedelta]]:
    catalogue = load_seed_catalogue()
    with replay_client(RECORDINGS) as client:
        registry = default_registry(client)
        report = collect(
            catalogue,
            registry,
            window_ending(RECORDED_END),
            observed_at=RUN_TIME,
            clock=lambda: RUN_TIME,
        )
        lags = {
            entry.policy.source: entry.policy.operations.expected_publication_lag
            for entry in registry.entries()
        }
    return catalogue, report, lags


def recorded_universe(catalogue: Catalogue, report: CollectionReport) -> list[str]:
    return sorted(
        game.slug
        for game in catalogue.games
        if any(
            s.canonical_game_id == game.canonical_game_id and s.series is not None
            for run in report.runs
            for s in run.outcomes
        )
    )


# The recording runs 6 July to 3 October 2026 with a one-day lag. The requested range
# runs past both ends on purpose, so unreconstructable moments are exercised.
RECORDED_FROM = date(2026, 7, 5)
RECORDED_TO = date(2026, 10, 7)


@pytest.fixture(scope="module")
def recorded() -> OpportunityHistoryReport:
    catalogue, report, lags = real_recording()
    return evaluate_history(
        catalogue,
        report,
        lags,
        RECORDED_FROM,
        RECORDED_TO,
        1,
        recorded_universe(catalogue, report),
        recording_provenance([RECORDINGS]),
        EvaluationMode.RECORDED,
        RUN_TIME,
    )


# --- the rules are those already written -----------------------------------------------------


def test_the_evaluation_reuses_the_rules_and_changes_none_of_them() -> None:
    assert NOTEWORTHY_ROBUST_Z == ROBUST_Z_OUTLIER_THRESHOLD == 3.5
    assert timedelta(days=7) == TIMELY_DISCUSSION
    assert OUTCOME_RULES_V1.version == "outcome_rules_v1"
    assert (OUTCOME_RULES_V1.primary_days, OUTCOME_RULES_V1.hit_threshold) == (3, 0.10)
    assert OUTCOME_RULES_V1.downside_threshold == -0.10
    assert PROTOCOL_VERSION == "validation_protocol_v1"
    assert SNAPSHOT_FORMAT == "recommendation_snapshot_v2"
    # The new snapshot is separately versioned, beside the old one, not in place of it.
    assert OPPORTUNITY_SNAPSHOT_FORMAT == "opportunity_snapshot_v1"
    # The direction rule the history reads is the rubric's own, at its thresholds.
    assert movement_direction(0.1, 3.5) is Direction.UP
    assert movement_direction(0.1, 3.49) is Direction.FLAT
    assert movement_direction(-0.1, -3.5) is Direction.DOWN
    assert movement_direction(0.0, 9.0) is Direction.FLAT
    assert movement_direction(None, 9.0) is Direction.UNKNOWN


# --- synthetic: every outcome the recorded data cannot reach ---------------------------------


def test_synthetic_candidate_followed_by_attention_rising(
    synthetic: OpportunityHistoryReport,
) -> None:
    snapshot, outcome = at_moment(synthetic, "synthetic-alpha", SEES_RISE)
    assert snapshot.readiness is Readiness.CANDIDATE
    assert snapshot.facts.moving_up and snapshot.facts.angle and snapshot.facts.coverage_known
    assert snapshot.creator_coverage == "complete_sample"
    assert outcome.status is OutcomeStatus.POSITIVE
    assert outcome.realized_uplift_72h is not None and outcome.realized_uplift_72h >= 0.10


def test_synthetic_candidate_followed_by_attention_falling(
    synthetic: OpportunityHistoryReport,
) -> None:
    snapshot, outcome = at_moment(synthetic, "synthetic-bravo", SEES_RISE)
    assert snapshot.readiness is Readiness.CANDIDATE
    assert outcome.status is OutcomeStatus.NEGATIVE
    assert outcome.realized_uplift_72h is not None and outcome.realized_uplift_72h <= -0.10


def test_synthetic_watchlist_with_either_outcome() -> None:
    rising, _ = one_game(quiet_then([4000.0]))
    snapshot, outcome = at_moment(rising, "synthetic-golf", SEES_RISE)
    assert snapshot.readiness is Readiness.WATCHLIST
    assert outcome.status is OutcomeStatus.POSITIVE
    # A timely event and nothing moving, then attention falls: a watchlist entry, negative.
    game_id = synthetic_game(7, "synthetic-golf").canonical_game_id
    announced = event(game_id, "synthetic-event-g", at(50), at(66))
    falling, _ = one_game(quiet_then([400.0]), [announced])
    snapshot, outcome = at_moment(falling, "synthetic-golf", QUIET_DAYS)
    assert snapshot.readiness is Readiness.WATCHLIST and snapshot.facts.timely_event
    assert not snapshot.facts.moving_up
    assert outcome.status is OutcomeStatus.NEGATIVE


def test_synthetic_insufficient_evidence(synthetic: OpportunityHistoryReport) -> None:
    snapshot, outcome = at_moment(synthetic, "synthetic-echo", SEES_RISE)
    assert snapshot.readiness is Readiness.INSUFFICIENT
    assert snapshot.why_now == ()
    # An outcome is still attached, so abstentions can be compared with what followed.
    assert outcome.status is OutcomeStatus.NEITHER


def test_synthetic_incomplete_forward_period_is_not_an_outcome(
    synthetic: OpportunityHistoryReport,
) -> None:
    # The invented recording ends on day 69; the moment on day 69 reads days 69 to 71.
    _, outcome = at_moment(synthetic, "synthetic-alpha", SCENARIO_DAYS - 1)
    assert outcome.status is OutcomeStatus.FORWARD_INCOMPLETE
    assert outcome.realized_uplift_72h is None
    assert outcome.outcome_window_end > outcome.data_through
    # And the moment the recording cannot reconstruct makes no prediction at all.
    unavailable = [m for m in synthetic.moments if not m.reconstructed]
    assert [m.as_of for m in unavailable] == [prediction_moment(day(SCENARIO_DAYS + 2))]
    assert not any(s.as_of == unavailable[0].as_of for s in synthetic.snapshots)
    assert synthetic.metrics.no_prediction == len(synthetic.universe)


def test_a_missing_source_observation_is_missing_never_filled() -> None:
    # synthetic-golf has no recorded series at all; synthetic-hotel is quiet beside it.
    golf, hotel = synthetic_game(7, "synthetic-golf"), synthetic_game(8, "synthetic-hotel")
    recording = pageview_recording(
        (golf, hotel), {hotel.slug: steady(SCENARIO_DAYS)}, at(SCENARIO_DAYS + 5)
    )
    report = evaluate_history(
        Catalogue(games=(golf, hotel)),
        recording,
        LAG,
        day(SEES_RISE),
        day(SEES_RISE),
        1,
        [golf.slug, hotel.slug],
        SYNTHETIC_HISTORY,
        SYNTHETIC,
        WHEN,
    )
    snapshot, outcome = at_moment(report, "synthetic-golf", SEES_RISE)
    assert snapshot.movements == ()
    assert missing(snapshot, "wikimedia_readership") is MissingReason.NOT_MAPPED
    assert missing(snapshot, "steam_concurrent_players") is MissingReason.NO_PLAYER_HISTORY
    assert snapshot.reconstruction is Reconstruction.NO_DEMAND
    assert snapshot.readiness is Readiness.INSUFFICIENT
    assert outcome.status is OutcomeStatus.UNAVAILABLE and outcome.realized_uplift_72h is None
    # A gap in the forward days makes the outcome unavailable, never zero.
    gappy = quiet_then([4000.0])
    gappy[SEES_RISE + 1] = None
    report, _ = one_game(gappy)
    _, outcome = at_moment(report, "synthetic-golf", SEES_RISE)
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    assert outcome.reason == "future_incomplete"


def test_a_later_rebinding_does_not_reach_an_earlier_moment() -> None:
    values = quiet_then([4000.0])
    game_id = synthetic_game(7, "synthetic-golf").canonical_game_id
    plain, _ = one_game(values)
    rebound, _ = one_game(values, bound_from={game_id: at(SEES_RISE + 3)})
    before = [s for s in rebound.snapshots if s.as_of < at(SEES_RISE + 3)]
    after = [s for s in rebound.snapshots if s.as_of >= at(SEES_RISE + 3)]
    assert before and after
    for snapshot in before:
        assert snapshot.reconstruction is Reconstruction.UNBOUND
        assert missing(snapshot, "wikimedia_readership") is MissingReason.UNBOUND
        assert snapshot.readiness is Readiness.INSUFFICIENT and snapshot.movements == ()
    unbound_outcomes = {o.snapshot_fingerprint: o for o in rebound.outcomes}
    assert all(unbound_outcomes[s.fingerprint].status is OutcomeStatus.UNAVAILABLE for s in before)
    # From the binding on, the same as if it had always been bound.
    same = {s.as_of: s.fingerprint for s in plain.snapshots}
    assert all(same[s.as_of] == s.fingerprint for s in after)


def test_an_event_observed_only_afterwards_does_not_count() -> None:
    game_id = synthetic_game(7, "synthetic-golf").canonical_game_id
    later = event(game_id, "synthetic-event-late", at(SEES_RISE + 1), at(66))
    without, _ = one_game(quiet_then([1000.0]))
    with_later, _ = one_game(quiet_then([1000.0]), [later])
    snapshot, _ = at_moment(with_later, "synthetic-golf", SEES_RISE)
    assert snapshot.readiness is Readiness.INSUFFICIENT and not snapshot.facts.timely_event
    assert missing(snapshot, "event_intelligence") is MissingReason.OBSERVED_ONLY_AFTER
    assert at_moment(without, "synthetic-golf", SEES_RISE)[0].readiness is snapshot.readiness
    # Once it has been retrieved, it does count.
    snapshot, _ = at_moment(with_later, "synthetic-golf", SEES_RISE + 1)
    assert snapshot.facts.timely_event and snapshot.readiness is Readiness.WATCHLIST


def test_creator_statistics_retrieved_afterwards_do_not_count() -> None:
    game_id = synthetic_game(7, "synthetic-golf").canonical_game_id
    context = [
        discussion(game_id, "synthetic-thread-g", at(59, 12), at(60, 6)),
        creator_sample(game_id, "synthetic-sample-late", at(SEES_RISE + 1)),
    ]
    report, _ = one_game(quiet_then([4000.0]), context)
    snapshot, _ = at_moment(report, "synthetic-golf", SEES_RISE)
    assert not snapshot.facts.coverage_known
    assert snapshot.creator_coverage == "not_observed_by_moment"
    assert missing(snapshot, "creator_supply") is MissingReason.OBSERVED_ONLY_AFTER
    assert snapshot.readiness is Readiness.WATCHLIST
    assert at_moment(report, "synthetic-golf", SEES_RISE + 1)[0].readiness is Readiness.CANDIDATE
    # An incomplete sample never establishes coverage, whenever it was retrieved.
    partial = [context[0], creator_sample(game_id, "synthetic-partial", at(60), complete=False)]
    report, _ = one_game(quiet_then([4000.0]), partial)
    snapshot, _ = at_moment(report, "synthetic-golf", SEES_RISE)
    assert snapshot.creator_coverage == "incomplete_sample"
    assert missing(snapshot, "creator_supply") is MissingReason.INCOMPLETE_SAMPLE


def test_reddit_posts_retrieved_afterwards_do_not_count(
    synthetic: OpportunityHistoryReport,
) -> None:
    # synthetic-foxtrot: rising, a complete sample, and a discussion retrieved on day 69.
    snapshot, _ = at_moment(synthetic, "synthetic-foxtrot", SEES_RISE)
    assert snapshot.readiness is Readiness.WATCHLIST and not snapshot.facts.angle
    assert missing(snapshot, "community_interest") is MissingReason.OBSERVED_ONLY_AFTER
    # The same discussion, retrieved in time, makes it a candidate.
    alpha, _ = at_moment(synthetic, "synthetic-alpha", SEES_RISE)
    assert alpha.readiness is Readiness.CANDIDATE
    # A discussion goes stale after the rubric's 7 days.
    stale, _ = at_moment(synthetic, "synthetic-alpha", 67)
    assert missing(stale, "community_interest") is MissingReason.NOT_TIMELY
    assert stale.readiness is Readiness.WATCHLIST


def test_a_changed_outcome_never_changes_a_frozen_snapshot() -> None:
    values = quiet_then([4000.0])
    original, _ = one_game(values)
    changed = list(values)
    for index in range(SEES_RISE, SCENARIO_DAYS):
        changed[index] = 100.0  # attention collapses after the moment instead
    rewritten, _ = one_game(changed)
    first, first_outcome = at_moment(original, "synthetic-golf", SEES_RISE)
    second, second_outcome = at_moment(rewritten, "synthetic-golf", SEES_RISE)
    assert first == second and first.fingerprint == second.fingerprint
    assert first_outcome.status is OutcomeStatus.POSITIVE
    assert second_outcome.status is OutcomeStatus.NEGATIVE
    assert first_outcome.outcome_id != second_outcome.outcome_id


# --- acceptance ------------------------------------------------------------------------------


def test_prediction_never_sees_what_was_not_yet_published(
    synthetic: OpportunityHistoryReport,
) -> None:
    invented = synthetic_history()
    moment = prediction_moment(day(SEES_RISE))
    # Every value not yet published at the moment, multiplied: the prediction cannot tell.
    perturbed = perturb_unavailable(invented.recording, moment, invented.publication_lag, 7.0)
    again = synthetic_run(recording=perturbed)
    fingerprints = {s.fingerprint for s in synthetic.snapshots if s.as_of <= moment}
    assert fingerprints == {s.fingerprint for s in again.snapshots if s.as_of <= moment}


def test_the_four_instants_stay_apart(synthetic: OpportunityHistoryReport) -> None:
    by_snapshot = {o.snapshot_fingerprint: o for o in synthetic.outcomes}
    for snapshot in synthetic.snapshots:
        moment_day = snapshot.as_of.date()
        assert snapshot.as_of == prediction_moment(moment_day)
        # The newest completed period, then the newest published one, a lag behind it.
        assert snapshot.evaluation_day == moment_day - timedelta(days=1)
        assert snapshot.latest_due_day == moment_day - timedelta(days=2)
        if snapshot.latest_observed_day is not None:
            assert snapshot.latest_observed_day <= snapshot.latest_due_day
        outcome = by_snapshot[snapshot.fingerprint]
        assert outcome.outcome_window_start == first_forward_day(snapshot.as_of) == moment_day
        assert outcome.outcome_window_start > snapshot.evaluation_day


def test_the_evaluation_is_deterministic_and_order_independent(
    synthetic: OpportunityHistoryReport,
) -> None:
    invented = synthetic_history()
    shuffled = synthetic_run(
        context=list(reversed(invented.context)),
        universe=[game.slug for game in reversed(invented.catalogue.games)],
        catalogue=Catalogue(games=tuple(reversed(invented.catalogue.games))),
        created_at=WHEN + timedelta(days=30),
    )
    assert shuffled == synthetic
    assert shuffled.fingerprint == synthetic.fingerprint
    assert synthetic.model_dump_json() == synthetic_run().model_dump_json()
    # Each moment's order is the rubric's own and explained.
    first_moment = [s for s in synthetic.snapshots if s.as_of == at(SEES_RISE)]
    assert [s.position for s in first_moment] == list(range(1, len(first_moment) + 1))
    assert [s.readiness for s in first_moment][:2] == [Readiness.CANDIDATE] * 2
    assert first_moment[-1].ahead_of_next == "last in the list"


def test_a_zero_denominator_is_not_estimable_never_zero() -> None:
    assert share(0, 0, "NOT ESTIMABLE — X") == "NOT ESTIMABLE — X"
    assert share(1, 4, "-").startswith("1/4 (25.0%)")
    assert "no interval" in share(1, 4, "-")
    # Every moment beyond the recording: nothing is assessed, and nothing divides by zero.
    report = synthetic_run(first_day=day(SCENARIO_DAYS + 2), last_day=day(SCENARIO_DAYS + 5))
    metrics = report.metrics
    assert metrics.assessments == 0 and metrics.moments_unavailable == 4
    assert metrics.abstention.startswith("NOT ESTIMABLE")
    assert metrics.candidate_positive_share == NOT_ESTIMABLE_NO_CANDIDATES
    assert all(g.positive_share.startswith("NOT ESTIMABLE") for g in metrics.groups)
    assert metrics.no_prediction == 4 * len(report.universe)


def test_every_outcome_is_labelled_an_attention_proxy(
    synthetic: OpportunityHistoryReport, recorded: OpportunityHistoryReport
) -> None:
    for report in (synthetic, recorded):
        assert all(o.measure == "attention_proxy" for o in report.outcomes)
        assert all(o.measure_label == ATTENTION_PROXY for o in report.outcomes)
        assert CREATOR_SUCCESS_UNVALIDATED in report.statements
        assert CREATOR_SUCCESS_UNVALIDATED == (
            "Creator-video success cannot yet be validated from the recorded data."
        )
        text = render_history(report)
        assert "ATTENTION PROXY" in text and CREATOR_SUCCESS_UNVALIDATED in text
    assert synthetic.statements[0].startswith("SYNTHETIC")
    assert not any(s.startswith("SYNTHETIC") for s in recorded.statements)


def test_policy_refuses_what_may_not_be_used() -> None:
    invented = synthetic_history()
    game_id = invented.catalogue.games[0].canonical_game_id
    # Private development only.
    with pytest.raises(HistoryRefused, match="private development only"):
        synthetic_run(stage=DeploymentStage.PUBLIC_LAUNCH)
    # Synthetic and recorded inputs are never mixed, either way round.
    real_thread = discussion(
        game_id, "thread", at(59), at(60), provenance=local_store_provenance("store")
    )
    with pytest.raises(HistoryRefused, match="invented inputs only"):
        synthetic_run(context=[real_thread])
    with pytest.raises(HistoryRefused, match="no synthetic input"):
        synthetic_run(mode=EvaluationMode.RECORDED)
    # Reddit data is refused while its approval is pending, whatever its standing.
    recorded_pages = InputProvenance(origin=Origin.LOCAL_STORE, reference="pages")
    with pytest.raises(HistoryRefused, match="reddit"):
        synthetic_run(
            mode=EvaluationMode.RECORDED, demand_origin=recorded_pages, context=[real_thread]
        )
    # A bounded universe of known games, and a real cadence.
    with pytest.raises(ValueError, match="no game with slug"):
        synthetic_run(universe=["not-a-game"])
    with pytest.raises(ValueError, match="distinct"):
        synthetic_run(universe=["synthetic-alpha", "synthetic-alpha"])
    with pytest.raises(ValueError, match="cadence"):
        synthetic_run(cadence_days=0)
    stray = ContextObservation(
        canonical_game_id="00000000-0000-4000-8000-000000000000",
        family=ContextFamily.EVENT,
        reference="x",
        retrieved_at=at(1),
        timely_from=at(1),
        timely_until=at(2),
        provenance=SYNTHETIC_HISTORY,
    )
    with pytest.raises(ValueError, match="outside the universe"):
        synthetic_run(context=[stray])


def test_cadence_thins_the_moments_and_counts_episodes(
    synthetic: OpportunityHistoryReport,
) -> None:
    sparse = synthetic_run(cadence_days=3)
    assert [m.as_of for m in sparse.moments] == [
        prediction_moment(synthetic_history().first_day + timedelta(days=3 * k))
        for k in range(len(sparse.moments))
    ]
    watch = next(g for g in synthetic.metrics.groups if g.readiness is Readiness.WATCHLIST)
    assert 1 <= watch.episodes <= watch.assessments
    assert watch.distinct_games <= len(synthetic.universe)


# --- recorded: recomputed, not restated ------------------------------------------------------


def test_recorded_history_reaches_no_candidate_and_says_so(
    recorded: OpportunityHistoryReport,
) -> None:
    metrics = recorded.metrics
    assert recorded.mode is EvaluationMode.RECORDED
    assert recorded.demand_origin.standing.value == "verified"
    assert metrics.candidates == 0
    assert metrics.candidate_positive_share == NOT_ESTIMABLE_NO_CANDIDATES
    assert any(NOT_ESTIMABLE_NO_CANDIDATES in s for s in recorded.statements)
    assert metrics.assessments == metrics.candidates + metrics.watchlist + metrics.insufficient
    # Nothing but readership was recorded at these moments, and nothing else is claimed.
    for snapshot in recorded.snapshots:
        assert set(snapshot.verified_families) <= {"wikimedia_readership"}
        for family in ("event_intelligence", "community_interest", "creator_supply"):
            assert missing(snapshot, family) is MissingReason.NOT_RECORDED
        assert missing(snapshot, "steam_concurrent_players") is MissingReason.NO_PLAYER_HISTORY
        assert snapshot.creator_coverage == "not_observed_by_moment"
        assert not snapshot.facts.angle and not snapshot.facts.coverage_known
    # The moments before the recording and after its last due day are unavailable.
    unavailable = [m.as_of.date() for m in recorded.moments if not m.reconstructed]
    assert unavailable == [date(2026, 7, 5), date(2026, 7, 6), date(2026, 10, 6), date(2026, 10, 7)]


def test_recorded_watchlist_entries_are_exactly_the_rubric_s_movements(
    recorded: OpportunityHistoryReport,
) -> None:
    for snapshot in recorded.snapshots:
        up = [m for m in snapshot.movements if m.direction is Direction.UP]
        assert (snapshot.readiness is Readiness.WATCHLIST) == bool(up)
        for m in snapshot.movements:
            assert m.direction is movement_direction(m.log_lift, m.robust_z)
    watch = next(g for g in recorded.metrics.groups if g.readiness is Readiness.WATCHLIST)
    if watch.assessments:
        assert any("are not independent" in s for s in recorded.statements)


def test_the_recorded_moment_matches_the_live_evidence_path() -> None:
    """At 5 October, 12:00, the history's readings equal the evidence reading's."""
    world = World()
    catalogue, report, lags = real_recording()
    by_slug = {
        a.slug: a
        for a in shortlist(assess_evidence(world.inputs(), REAL_AS_OF, world.scope)).assessments
    }
    history = evaluate_history(
        catalogue,
        report,
        lags,
        date(2026, 10, 5),
        date(2026, 10, 5),
        1,
        recorded_universe(catalogue, report),
        recording_provenance([RECORDINGS]),
        EvaluationMode.RECORDED,
        RUN_TIME,
    )
    assert history.snapshots
    for snapshot in history.snapshots:
        live = by_slug[snapshot.slug]
        assert [(m.direction, m.robust_z, m.log_lift) for m in snapshot.movements] == [
            (m.direction, m.robust_z, m.log_lift) for m in live.movements
        ]
        assert snapshot.readiness is live.readiness is Readiness.INSUFFICIENT


def test_score_v0_snapshots_are_the_backtest_s_own(recorded: OpportunityHistoryReport) -> None:
    catalogue, report, lags = real_recording()
    first, last = date(2026, 9, 28), date(2026, 10, 5)
    backtest = run_backtest(catalogue, report, first, last, RUN_TIME, lags)
    ids = {s.snapshot_id for s in backtest.snapshots}
    within = [s for s in recorded.snapshots if first <= s.as_of.date() <= last]
    assert within and {s.base_snapshot_id for s in within} <= ids


def test_the_command_runs_offline_in_both_modes_and_stays_private(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def no_socket(*_: object, **__: object) -> None:
        raise AssertionError("the history command tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", no_socket)

    def run(*arguments: str) -> tuple[int, str, str]:
        code = cli.main(["opportunity-history", *arguments])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    code, text, _ = run("--synthetic")
    assert code == cli.EXIT_OK and run("--synthetic")[1] == text
    assert text.startswith("SYNTHETIC: six invented games")
    assert "evidence_supported_candidate" in text and "ATTENTION PROXY" in text

    recorded_args = (
        "--recorded",
        "--replay",
        str(RECORDINGS),
        "--recorded-through",
        RECORDED_END.isoformat(),
        "--from",
        "2026-09-28",
        "--to",
        "2026-10-06",
        "--game",
        "minecraft",
        "--game",
        "gta-v",
    )
    out = tmp_path / "history.json"
    code, payload, _ = run(*recorded_args, "--json", "--out", str(out))
    assert code == cli.EXIT_OK
    loaded = OpportunityHistoryReport.model_validate_json(payload)
    assert loaded.universe == ("gta-v", "minecraft")
    assert loaded.metrics.candidate_positive_share == NOT_ESTIMABLE_NO_CANDIDATES
    assert json.loads(out.read_text()) == json.loads(payload)
    # Deterministic across runs made at different wall-clock moments.
    assert run(*recorded_args, "--json")[1] == payload

    code, _, err = run(*recorded_args, "--out", "artifacts/history.json")
    assert code == cli.EXIT_SOURCE_POLICY and "private" in err
    code, _, err = run("--recorded", "--replay", str(RECORDINGS), "--from", "2026-09-28")
    assert code == cli.EXIT_INVALID_CATALOGUE and "--recorded-through" in err
    code, _, err = run("--synthetic", "--replay", str(RECORDINGS))
    assert code == cli.EXIT_INVALID_CATALOGUE and "never mixed" in err
    code, _, err = run(*recorded_args[:9], "--game", "not-a-game")
    assert code == cli.EXIT_INVALID_CATALOGUE and "not-a-game" in err


def test_exploratory_sensitivity_is_labelled_and_decides_nothing(
    recorded: OpportunityHistoryReport,
    synthetic: OpportunityHistoryReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for report in (recorded, synthetic):
        rows = report.metrics.sensitivity
        assert [row.robust_z for row in rows] == list(EXPLORATORY_ROBUST_Z)
        assert NOTEWORTHY_ROBUST_Z not in EXPLORATORY_ROBUST_Z
        assert report.metrics.sensitivity_label.startswith("EXPLORATORY")
        assert "no outcome is attached" in report.metrics.sensitivity_label
        # A higher bar never adds a watchlist entry, and every row covers every decision.
        watch = [row.watchlist for row in rows]
        assert watch == sorted(watch, reverse=True)
        for row in rows:
            assert row.candidates + row.watchlist + row.insufficient == report.metrics.assessments
    # Recomputed at the rubric's own threshold, no decision changes.
    monkeypatch.setattr(history_module, "EXPLORATORY_ROBUST_Z", (NOTEWORTHY_ROBUST_Z,))
    (same,) = history_module._sensitivity(recorded.snapshots)
    assert same.changed == 0
    assert (same.candidates, same.watchlist) == (
        recorded.metrics.candidates,
        recorded.metrics.watchlist,
    )
