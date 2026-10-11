"""Historical validation: no look-ahead, exact outcomes, honest evidence.

Offline throughout. Synthetic series and synthetic events exist only in this file. The
real recorded replay is used for reproduction and for the fixture backtest.
"""

import hashlib
import inspect
import json
import socket
import statistics
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from metaspike import cli
from metaspike.catalogue.seed import load_seed_catalogue
from metaspike.domain.events import EventType
from metaspike.domain.games import CanonicalGame, Catalogue, LifecycleState, SourceIdentifier
from metaspike.domain.observations import Freshness, Observation, ObservationStatus
from metaspike.domain.signals import CollectionStatus, Signal
from metaspike.domain.source_policy import DeploymentStage
from metaspike.domain.timeseries import Granularity, TimeWindow, start_of_day
from metaspike.events.model import CoverageCheck, EventReport, TimePrecision, observe
from metaspike.events.rules import EVENT_HEAT_RULES_V1
from metaspike.events.store import EventStore
from metaspike.pipeline.assess import assess
from metaspike.pipeline.collection import CollectionReport, SourceRun, collect, window_ending
from metaspike.pipeline.normalize import build_time_series
from metaspike.pipeline.run import build_and_publish
from metaspike.publishing.artifacts import SCHEMA_VERSION, ArtifactBundle, ValidationArtifact
from metaspike.publishing.build import EventInputs, build_bundle
from metaspike.publishing.publisher import publish as publish_bundle
from metaspike.publishing.validation import ArtifactValidationError, validate_published
from metaspike.scoring.v0 import V0_INPUT_SIGNAL
from metaspike.sources.registry import SourcePolicyError, default_registry
from metaspike.sources.replay import replay_client
from metaspike.storage.base import StorageCorruptionError
from metaspike.validation import backtest as backtest_module
from metaspike.validation import snapshot as snapshot_module
from metaspike.validation.backtest import (
    BacktestResult,
    perturb_unavailable,
    prediction_moment,
    run_backtest,
)
from metaspike.validation.history import (
    FutureValues,
    LeakageError,
    PredictionInputs,
    RecordedHistory,
    ensure_available,
    evaluation_day_at,
    first_forward_day,
    latest_due_at,
)
from metaspike.validation.metrics import (
    STRATEGIES,
    evaluate_strategy,
    order,
    prediction_day,
    spearman,
    summarise,
)
from metaspike.validation.outcomes import (
    OutcomeAvailability,
    OutcomeLabel,
    OutcomeUnavailable,
    PeakStatus,
    label,
)
from metaspike.validation.report import (
    ValidationReport,
    assess_evidence,
    build_report,
    set_hash,
)
from metaspike.validation.rules import (
    OUTCOME_RULES_V1,
    VALIDATION_PROTOCOL_V1,
    EvidenceStatus,
    OutcomeRules,
)
from metaspike.validation.snapshot import RecommendationSnapshot, predict
from metaspike.validation.store import STORE_GENERATION, AccountabilityStore
from tests.conftest import RECORDED_END, RECORDINGS, RUN_TIME, make_id, steady
from tests.synthetic_policy import (
    SYNTHETIC_EVENT_SOURCE,
    synthetic_event_policies,
    synthetic_event_policy,
)

REPO = Path(__file__).parent.parent
GOLDEN = json.loads((REPO / "tests" / "fixtures" / "score_v0_replay_golden.json").read_text())
START = date(2026, 3, 1)
DAY = timedelta(days=1)
WHEN = datetime(2026, 12, 1, tzinfo=UTC)
RULES = OUTCOME_RULES_V1
PROTOCOL = VALIDATION_PROTOCOL_V1
SOURCE = V0_INPUT_SIGNAL.source


def day(index: int) -> date:
    return START + index * DAY


def game(number: int, slug: str) -> CanonicalGame:
    return CanonicalGame(
        canonical_game_id=make_id(number),
        slug=slug,
        name=slug.title(),
        lifecycle_state=LifecycleState.LIVE_SERVICE,
    )


def recording(
    series: Mapping[str, Sequence[float | None]],
) -> tuple[Catalogue, CollectionReport]:
    """A synthetic recording: one daily series per slug, all starting on START."""
    length = max(len(values) for values in series.values())
    window = TimeWindow.of_days(START, day(length - 1))
    catalogue = Catalogue(
        games=tuple(game(number, slug) for number, slug in enumerate(series, start=1))
    )
    signals = []
    for entry in catalogue.games:
        values = series[entry.slug]
        if not any(value is not None for value in values):
            signals.append(
                Signal(
                    canonical_game_id=entry.canonical_game_id,
                    key=V0_INPUT_SIGNAL,
                    status=CollectionStatus.NOT_MAPPED,
                )
            )
            continue
        observations = [
            Observation(
                canonical_game_id=entry.canonical_game_id,
                source=SOURCE,
                metric=V0_INPUT_SIGNAL.metric,
                granularity=Granularity.DAILY,
                period_start=start_of_day(day(index)),
                value=value,
                status=ObservationStatus.OBSERVED,
                observed_at=WHEN,
                freshness=Freshness(period_end=start_of_day(day(index)) + DAY, lag_hours=0),
            )
            for index, value in enumerate(values)
            if value is not None
        ]
        built = build_time_series(
            entry.canonical_game_id, V0_INPUT_SIGNAL, window, observations, WHEN
        )
        signals.append(
            Signal(
                canonical_game_id=entry.canonical_game_id,
                key=V0_INPUT_SIGNAL,
                status=CollectionStatus.COLLECTED,
                series=built,
            )
        )
    run = SourceRun(source=SOURCE, started_at=WHEN, finished_at=WHEN, outcomes=tuple(signals))
    return catalogue, CollectionReport(window=window, observed_at=WHEN, runs=(run,))


ZERO = {SOURCE: timedelta(0)}
ONE_DAY = {SOURCE: DAY}


def moment(index: int) -> datetime:
    """00:00 UTC at the start of day `index`."""
    return start_of_day(day(index))


def sees(
    report: CollectionReport, index: int, lag: Mapping[str, timedelta] = ZERO
) -> PredictionInputs:
    """Inputs for the moment at which, with no publication lag, day `index` is the newest
    day visible: 00:00 UTC on the following day."""
    return RecordedHistory(report, lag).inputs_at(moment(index + 1))


CUTOFF = 59  # the newest day visible in the scenarios below


def scenario(future: Sequence[float | None]) -> tuple[RecommendationSnapshot, OutcomeLabel]:
    """Sixty quiet days (baseline 100, latest 110, 3-day average 103.33), then `future`."""
    catalogue, report = recording({"alpha": [*steady(CUTOFF + 1), *future]})
    history = RecordedHistory(report, ZERO)
    (snapshot,) = predict(catalogue, history.inputs_at(moment(CUTOFF + 1)), WHEN)
    return snapshot, label(snapshot, history.future(), RULES)


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


# The recording runs 6 July to 3 October 2026 and Wikimedia's policy lag is one day, so
# the first moment it can stand in for is 00:00 on 7 July and the last 00:00 on 5 October.
FIRST_PREDICTION = date(2026, 7, 7)
LAST_PREDICTION = date(2026, 10, 5)


@pytest.fixture(scope="module")
def fixture_backtest() -> BacktestResult:
    catalogue, report, lags = real_recording()
    assert lags == {SOURCE: DAY}
    return run_backtest(catalogue, report, FIRST_PREDICTION, LAST_PREDICTION, RUN_TIME, lags)


def fingerprint(model: Any) -> str:
    return hashlib.sha256(
        json.dumps(model.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()


# --- the rules are fixed --------------------------------------------------------------


def test_the_outcome_rules_and_the_protocol_are_versioned_and_pinned() -> None:
    assert RULES.version == "outcome_rules_v1" and PROTOCOL.version == "validation_protocol_v1"
    assert (RULES.primary_days, RULES.movement_days, RULES.persistence_days) == (3, 1, 7)
    assert RULES.peak_search_days == 6 and RULES.future_days == 7
    assert (RULES.hit_threshold, RULES.downside_threshold) == (0.10, -0.10)
    assert PROTOCOL.top_k == (1, 3, 5) and PROTOCOL.supports_holdout is False
    # A change to any value must be a new version, made on purpose.
    assert fingerprint(RULES) == "013474696e6f2e02e97bfc5890dc0590d64cdc735dacf2e08a9ad8e4d03d5e22"
    assert (
        fingerprint(PROTOCOL) == "41c91c3b092ee117fac03965a1b3b55d0c665b8b67d7c654154f158a1f5121e7"
    )
    for name in ("primary", "movement", "persistence", "peak", "hit", "downside"):
        assert getattr(RULES, f"{name}_rationale")
    with pytest.raises(ValidationError, match="windows must nest"):
        OutcomeRules.model_validate({**RULES.model_dump(), "primary_days": 9})


def test_event_heat_rules_v1_are_unchanged() -> None:
    assert EVENT_HEAT_RULES_V1.version == "event_heat_rules_v1"
    assert (
        fingerprint(EVENT_HEAT_RULES_V1)
        == "0f3a11ee44d64ba48e4906b70886d3cb6738da4192d47e0a9f84223575c9717e"
    )


# --- no look-ahead --------------------------------------------------------------------


def test_inputs_rebuilt_for_a_moment_hold_only_what_was_published_by_then() -> None:
    catalogue, report = recording({"alpha": steady(80), "beta": steady(80)})
    history = RecordedHistory(report, ZERO)
    inputs = history.inputs_at(moment(51))
    assert inputs.as_of == moment(51) and inputs.evaluation_day == day(50)
    assert inputs.evaluation_day == evaluation_day_at(inputs.as_of)
    assert inputs.report.window.end == start_of_day(day(50))
    assert inputs.report.observed_at == inputs.as_of
    for run in inputs.report.runs:
        for signal in run.outcomes:
            assert signal.series is not None
            observed = [p.period_day for p in signal.series.points if p.value is not None]
            assert max(observed) == day(50) and len(observed) == 51
    assert len(catalogue.games) == 2 and ensure_available(inputs) == 2
    # The recording cannot stand in for a moment whose visible data it does not hold.
    assert history.can_reconstruct(moment(80)) and not history.can_reconstruct(moment(81))
    assert not history.can_reconstruct(moment(0))
    with pytest.raises(ValueError, match="cannot be reconstructed"):
        history.inputs_at(moment(81))
    with pytest.raises(ValueError, match="timezone-aware"):
        history.inputs_at(datetime(2026, 4, 1))
    # A lag must be stated for every source, and cannot be negative.
    with pytest.raises(ValueError, match="no publication lag given"):
        RecordedHistory(report, {})
    with pytest.raises(ValueError, match="cannot be negative"):
        RecordedHistory(report, {SOURCE: -DAY})


def test_inputs_holding_what_was_not_yet_visible_are_refused() -> None:
    catalogue, report = recording({"alpha": steady(80)})
    whole = PredictionInputs(
        as_of=moment(51), evaluation_day=day(50), publication_lag=ZERO, report=report
    )
    with pytest.raises(LeakageError, match="runs past the evaluation day"):
        ensure_available(whole)
    with pytest.raises(LeakageError):
        predict(catalogue, whole, WHEN)
    # A window that ends on the evaluation day but still carries a later point.
    honest = sees(report, 50)
    later = sees(report, 51)
    smuggled = PredictionInputs(
        as_of=moment(51),
        evaluation_day=day(50),
        publication_lag=ZERO,
        report=later.report.model_copy(update={"window": honest.report.window}),
    )
    with pytest.raises(LeakageError):
        ensure_available(smuggled)
    late = PredictionInputs(
        as_of=moment(51),
        evaluation_day=day(50),
        publication_lag=ZERO,
        report=honest.report.model_copy(update={"observed_at": moment(53)}),
    )
    with pytest.raises(LeakageError, match="observed after the simulated moment"):
        ensure_available(late)
    wrong_day = PredictionInputs(
        as_of=moment(51), evaluation_day=day(51), publication_lag=ZERO, report=honest.report
    )
    with pytest.raises(LeakageError, match="not the last whole day"):
        ensure_available(wrong_day)
    unknown = PredictionInputs(
        as_of=moment(51), evaluation_day=day(50), publication_lag={}, report=honest.report
    )
    with pytest.raises(LeakageError, match="no publication lag is known"):
        ensure_available(unknown)


# --- publication lag: ended is not the same as published ------------------------------


def with_spike(index: int, value: float, length: int = 80) -> list[float | None]:
    values = steady(length)
    values[index] = value
    return values


def test_a_period_that_had_ended_but_was_not_yet_published_is_withheld() -> None:
    """The regression for the leak in the first version of this backtest.

    P is 00:00 on day 61, and the source publishes a day one day after it ends. Day 59
    (X) is due at P. Day 60 (X+1) had ended at P and is in the recording, but the
    source had not published it. Truncating by the calendar, as the first version did,
    lets day 60 in; it must not be there.
    """
    as_of = moment(61)
    assert latest_due_at(as_of, Granularity.DAILY, DAY).date() == day(59)
    assert evaluation_day_at(as_of) == day(60)  # ended, and not yet published

    def at_p(x_plus_1: float) -> tuple[PredictionInputs, RecommendationSnapshot]:
        catalogue, report = recording(
            {"alpha": with_spike(60, x_plus_1), "beta": [v * 3 for v in steady(80) if v]}
        )
        inputs = RecordedHistory(report, ONE_DAY).inputs_at(as_of)
        alpha = next(s for s in predict(catalogue, inputs, WHEN) if s.slug == "alpha")
        return inputs, alpha

    ordinary_inputs, ordinary = at_p(90.0)
    enormous_inputs, enormous = at_p(9_000_000.0)

    for inputs in (ordinary_inputs, enormous_inputs):
        assert inputs.evaluation_day == day(60)
        for run in inputs.report.runs:
            for signal in run.outcomes:
                assert signal.series is not None
                by_day = {p.period_day: p.value for p in signal.series.points}
                assert by_day[day(60)] is None  # X+1 is absent: not zero, not a value
                assert by_day[day(59)] is not None  # X is there
                assert max(d for d, v in by_day.items() if v is not None) == day(59)
                assert signal.series.latest_due == start_of_day(day(59))
    # The prediction is the same snapshot whether X+1 was ordinary or enormous.
    assert enormous == ordinary and enormous.snapshot_id == ordinary.snapshot_id
    assert ordinary.score == enormous.score and ordinary.rank == enormous.rank
    assert ordinary.confidence == enormous.confidence
    assert ordinary.features is not None
    assert ordinary.features.as_of_day == day(59)  # the features anchor on X
    assert ordinary.features.latest_views == steady(80)[59]
    # The snapshot says when it was made and what it could see, and they differ.
    assert ordinary.as_of == as_of
    assert ordinary.evaluation_day == day(60)
    assert ordinary.latest_due_day == day(59) == ordinary.latest_observed_day
    assert ordinary.publication_lag_hours == 24.0
    assert ordinary.input_window_end == day(60)

    # The calendar-only truncation of the first version DOES let the spike in, which is
    # what makes this a regression test: under zero lag at the same P, day 60 is visible
    # and the answer changes.
    catalogue, report = recording(
        {"alpha": with_spike(60, 9_000_000.0), "beta": [v * 3 for v in steady(80) if v]}
    )
    leaky = next(
        s
        for s in predict(catalogue, RecordedHistory(report, ZERO).inputs_at(as_of), WHEN)
        if s.slug == "alpha"
    )
    assert leaky.features is not None and leaky.features.latest_views == 9_000_000.0
    assert leaky.latest_observed_day == day(60)
    assert leaky.score.value != ordinary.score.value
    # And inputs built that way are refused when the true lag is stated.
    stale_policy = RecordedHistory(report, ZERO).inputs_at(as_of)
    relabelled = PredictionInputs(
        as_of=as_of, evaluation_day=day(60), publication_lag=ONE_DAY, report=stale_policy.report
    )
    with pytest.raises(LeakageError):
        ensure_available(relabelled)
    with pytest.raises(LeakageError):
        predict(catalogue, relabelled, WHEN)


def test_the_first_outcome_day_follows_the_recommendation_not_the_data() -> None:
    values = [*steady(60), 5000.0, 120.0, 130.0, 140.0, 100.0, 100.0, 100.0, 100.0, 100.0]
    catalogue, report = recording({"alpha": values})
    history = RecordedHistory(report, ONE_DAY)
    (snapshot,) = predict(catalogue, history.inputs_at(moment(61)), WHEN)
    assert snapshot.latest_observed_day == day(59)
    outcome = label(snapshot, history.future())
    # Not day 60 (the data's next day, unseen at P and already over), but day 61.
    assert first_forward_day(snapshot.as_of) == day(61)
    assert (outcome.primary_window_start, outcome.primary_window_end) == (day(61), day(63))
    assert outcome.prediction_at == moment(61)
    assert [item.day for item in outcome.forward] == [day(61 + k) for k in range(7)]
    assert outcome.realized_uplift_72h == pytest.approx(0.30)  # 120, 130, 140 over 100
    # The 5,000 on day 60 is on neither side.
    assert outcome.realized_uplift_72h is not None and outcome.realized_uplift_72h < 1
    # Day 60 is used for one thing only: to say whether day 61 was a rise. It was not.
    assert outcome.peak_status is PeakStatus.FOUND and outcome.lead_time_days == 3
    with pytest.raises(ValidationError, match="must start after the recommendation"):
        OutcomeLabel.model_validate({**outcome.model_dump(), "primary_window_start": day(59)})


def test_a_moment_inside_a_day_takes_the_next_whole_day_as_its_first() -> None:
    assert first_forward_day(moment(61)) == day(61)  # exactly on the boundary
    assert first_forward_day(moment(61) + timedelta(microseconds=1)) == day(62)
    assert first_forward_day(moment(61) + timedelta(hours=13)) == day(62)
    assert first_forward_day(moment(62) - timedelta(seconds=1)) == day(62)
    east = moment(61).astimezone(timezone(timedelta(hours=9)))  # the same instant
    assert first_forward_day(east) == day(61)
    assert evaluation_day_at(moment(61) + timedelta(hours=13)) == day(60)
    values = [*steady(61), 120.0, 130.0, 140.0, 100.0, 100.0, 100.0, 100.0]
    catalogue, report = recording({"alpha": values})
    history = RecordedHistory(report, ZERO)
    (midday,) = predict(catalogue, history.inputs_at(moment(61) + timedelta(hours=13)), WHEN)
    outcome = label(midday, history.future())
    assert outcome.primary_window_start == day(62)
    assert outcome.realized_uplift_72h == pytest.approx((130 + 140 + 100) / 3 / 100 - 1)


def test_the_due_boundary_is_exact_to_the_second() -> None:
    values = with_spike(59, 777.0)
    catalogue, report = recording({"alpha": values})
    history = RecordedHistory(report, ONE_DAY)
    # Day 59 ends at moment(60); with a one-day lag it is published at moment(61).

    def newest(as_of: datetime) -> tuple[date, float | None]:
        (snapshot,) = predict(catalogue, history.inputs_at(as_of), WHEN)
        assert snapshot.features is not None
        return snapshot.latest_due_day, snapshot.features.latest_views

    assert newest(moment(61) - timedelta(seconds=1)) == (day(58), steady(80)[58])
    assert newest(moment(61)) == (day(59), 777.0)  # due at exactly its publication time
    assert newest(moment(61) + timedelta(seconds=1)) == (day(59), 777.0)
    assert newest(moment(62) - timedelta(seconds=1)) == (day(59), 777.0)
    assert latest_due_at(moment(61) - timedelta(seconds=1), Granularity.DAILY, DAY) == moment(58)
    # A longer lag withholds more; a shorter one less.
    for hours, due in ((0, 60), (1, 59), (24, 59), (25, 58), (48, 58), (72, 57)):
        lag = timedelta(hours=hours)
        assert latest_due_at(moment(61), Granularity.DAILY, lag) == moment(due), hours


def test_zero_publication_lag_sees_the_day_that_has_just_ended() -> None:
    catalogue, report = recording({"alpha": with_spike(60, 555.0)})
    (snapshot,) = predict(catalogue, RecordedHistory(report, ZERO).inputs_at(moment(61)), WHEN)
    assert snapshot.latest_due_day == day(60) == snapshot.evaluation_day
    assert snapshot.latest_observed_day == day(60) and snapshot.publication_lag_hours == 0
    assert snapshot.features is not None and snapshot.features.latest_views == 555.0


def test_a_day_not_yet_due_is_missing_never_zero() -> None:
    catalogue, report = recording({"alpha": steady(80)})
    inputs = RecordedHistory(report, {SOURCE: 3 * DAY}).inputs_at(moment(61))
    series = inputs.report.runs[0].outcomes[0].series
    assert series is not None
    tail = {p.period_day: (p.value, p.status) for p in series.points[-4:]}
    assert tail[day(57)][0] is not None  # the newest day published under a three-day lag
    for index in (58, 59, 60):
        value, status = tail[day(index)]
        assert value is None and status is ObservationStatus.MISSING
    (snapshot,) = predict(catalogue, inputs, WHEN)
    assert snapshot.features is not None
    assert snapshot.features.as_of_day == day(57) == snapshot.latest_due_day
    # The withheld days are not counted as gaps in the history, and not as zero views.
    assert snapshot.features.missing_days == 0 and snapshot.features.staleness_days == 0
    assert snapshot.features.latest_views in (90.0, 110.0)


def test_sources_with_different_lags_are_each_held_to_their_own() -> None:
    _, report = recording({"alpha": steady(80)})
    slow_source = "slow_source"
    run = report.runs[0]
    slow = run.model_copy(update={"source": slow_source})
    both = report.model_copy(update={"runs": (run, slow)})
    lags = {SOURCE: timedelta(0), slow_source: 2 * DAY}
    inputs = RecordedHistory(both, lags).inputs_at(moment(61))
    newest = {
        item.source: max(
            p.period_day
            for p in item.outcomes[0].series.points  # type: ignore[union-attr]
            if p.value is not None
        )
        for item in inputs.report.runs
    }
    assert newest == {SOURCE: day(60), slow_source: day(58)}
    assert ensure_available(inputs) == 2
    # Swapping the lags makes the same report a leak for the slower source.
    swapped = PredictionInputs(
        as_of=inputs.as_of,
        evaluation_day=inputs.evaluation_day,
        publication_lag={SOURCE: 2 * DAY, slow_source: timedelta(0)},
        report=inputs.report,
    )
    with pytest.raises(LeakageError):
        ensure_available(swapped)


def test_a_computation_that_looked_ahead_would_give_a_different_answer() -> None:
    """The control for every equality test below: here, looking ahead visibly matters.

    Features computed on the whole recording and read off for a past day are not the
    features that day could have had. If the backtest did that, the assertions in the
    next tests would fail, because they compare against exactly this.
    """
    values = [*steady(60), 400.0, 900.0, 1500.0, 2000.0, 2600.0, 3000.0, 3200.0, 3300.0]
    catalogue, report = recording({"alpha": values})
    history = RecordedHistory(report, ZERO)
    (honest,) = predict(catalogue, history.inputs_at(moment(59 + 1)), WHEN)
    (looked_ahead,) = assess(catalogue, report)  # the whole recording, future included
    assert honest.features is not None and looked_ahead.scored_features is not None
    assert honest.features.latest_views == 110.0
    assert looked_ahead.scored_features.latest_views == 3300.0
    assert honest.score.value != looked_ahead.score.value
    assert honest.features.baseline_median_28d == 100.0
    assert looked_ahead.scored_features.avg_3d != honest.features.avg_3d


def test_changing_or_appending_the_future_cannot_change_a_past_prediction() -> None:
    quiet = [*steady(60), *steady(20)]
    loud = [*steady(60), *[5000.0 + 100 * index for index in range(20)]]
    gone: list[float | None] = [*steady(60), *([None] * 20)]
    predictions = []
    for future in (quiet, loud, gone, steady(60)):
        catalogue, report = recording({"alpha": future, "beta": steady(len(future))})
        history = RecordedHistory(report, ZERO)
        predictions.append(predict(catalogue, history.inputs_at(moment(59 + 1)), WHEN))
    first = predictions[0]
    for other in predictions[1:]:
        assert [item.snapshot_id for item in other] == [item.snapshot_id for item in first]
        assert other == first
    # The same through the checker the backtest itself runs.
    catalogue, report = recording({"alpha": quiet, "beta": loud})
    altered = perturb_unavailable(report, moment(60), ZERO, 1000.0)
    assert altered != report
    before = predict(catalogue, sees(report, 59), WHEN)
    after = predict(catalogue, sees(altered, 59), WHEN)
    assert before == after
    # While a day inside the perturbed range does change, so the check is not vacuous.
    assert predict(catalogue, sees(report, 70), WHEN) != predict(catalogue, sees(altered, 70), WHEN)


def test_a_snapshot_rebuilt_later_is_identical_and_its_id_ignores_when_it_was_made() -> None:
    catalogue, report = recording({"alpha": steady(80), "beta": [*steady(70), *([300.0] * 10)]})
    history = RecordedHistory(report, ZERO)
    first = predict(catalogue, history.inputs_at(moment(72 + 1)), WHEN)
    later = predict(catalogue, sees(report, 72), WHEN + 400 * DAY)
    assert [a.snapshot_id for a in first] == [b.snapshot_id for b in later]
    assert all(a.created_at != b.created_at for a, b in zip(first, later, strict=True))
    assert all(
        a.model_dump(exclude={"created_at"}) == b.model_dump(exclude={"created_at"})
        for a, b in zip(first, later, strict=True)
    )
    assert all(item.snapshot_id.startswith("snap_") for item in first)
    other_day = predict(catalogue, history.inputs_at(moment(73 + 1)), WHEN)
    assert {a.snapshot_id for a in first}.isdisjoint({b.snapshot_id for b in other_day})
    # The id is a hash of the content: tampering with a stored snapshot is detectable.
    tampered = first[0].model_copy(update={"rank": 7})
    assert snapshot_module.snapshot_id(tampered.model_dump(mode="json")) != first[0].snapshot_id


def test_nothing_that_predicts_can_be_handed_or_can_import_an_outcome() -> None:
    package = REPO / "src" / "metaspike"
    predicting = [
        *(package / "scoring").rglob("*.py"),
        *(package / "features").rglob("*.py"),
        *(package / "events").rglob("*.py"),
        *(package / "domain").rglob("*.py"),
        *(package / "sources").rglob("*.py"),
        *(package / "storage").rglob("*.py"),
        package / "pipeline" / "assess.py",
        package / "pipeline" / "collection.py",
        package / "pipeline" / "normalize.py",
    ]
    assert len(predicting) > 20
    for path in sorted(predicting):
        assert "metaspike.validation" not in path.read_text(encoding="utf-8"), path
    # Publishing may carry a finished report; it may not reach the engine or a label.
    for path in [*sorted((package / "publishing").rglob("*.py")), package / "pipeline" / "run.py"]:
        text = path.read_text(encoding="utf-8")
        for forbidden in ("validation.outcomes", "validation.backtest", "validation.history"):
            assert forbidden not in text, (path, forbidden)
    # Inside validation, the prediction side does not import the outcome side.
    for module in ("history.py", "snapshot.py"):
        text = (package / "validation" / module).read_text(encoding="utf-8")
        for forbidden in ("validation.outcomes", "validation.metrics", "validation.report"):
            assert forbidden not in text, (module, forbidden)
        assert ("OutcomeLabel" not in text and "FutureValues" not in text.split('"""', 2)[2]) or (
            module == "history.py"
        )
    parameters = inspect.signature(predict).parameters
    assert list(parameters) == [
        "catalogue",
        "inputs",
        "created_at",
        "event_records",
        "coverage_checks",
        "event_rules",
    ]
    annotations = " ".join(str(parameter.annotation) for parameter in parameters.values())
    for outcome_type in ("OutcomeLabel", "FutureValues", "ValidationReport"):
        assert outcome_type not in annotations
    # A snapshot has no field an outcome could sit in.
    fields = set(RecommendationSnapshot.model_fields)
    assert not {name for name in fields if "outcome" in name or "uplift" in name or "label" in name}
    assert not hasattr(PredictionInputs, "future")


# --- events at a past moment ----------------------------------------------------------


# Invented events go through the same policy gate as real ones: the test-only policy,
# at the one stage it clears.
SYNTHETIC_USE: dict[str, Any] = {
    "event_source_policies": synthetic_event_policies(),
    "stage": DeploymentStage.PRIVATE_DEVELOPMENT,
}


def event_store(root: Path, catalogue: Catalogue, *, with_revision: bool) -> EventStore:
    """Synthetic: a patch announced on day 40 for day 62, later moved to day 75."""
    store = EventStore(root)
    alpha = catalogue.games[0]
    identified = Catalogue(
        games=(
            alpha.model_copy(
                update={
                    "source_identifiers": (
                        SourceIdentifier(source="synthetic_events", namespace="g", value="alpha"),
                    )
                }
            ),
            *catalogue.games[1:],
        )
    )

    def report(starts: date) -> EventReport:
        return EventReport(
            game=identified.games[0].source_identifiers[0],
            event_type=EventType.MAJOR_PATCH,
            title="Synthetic Patch",
            starts_at=start_of_day(starts),
            time_precision=TimePrecision.DAY,
            source_event_id="synthetic-1",
        )

    store.append([observe(report(day(62)), identified, start_of_day(day(40)))])
    # The source keeps looking weekly, so coverage stays current through the range.
    store.record_checks(
        CoverageCheck(
            canonical_game_id=alpha.canonical_game_id,
            source="synthetic_events",
            checked_at=start_of_day(day(index)),
        )
        for index in (47, 54, 61, 68, 75)
    )
    if with_revision:
        store.append([observe(report(day(75)), identified, start_of_day(day(66)) + DAY / 2)])
    return store


def test_an_event_or_revision_seen_later_cannot_rewrite_event_intelligence_at_t(
    tmp_path: Path,
) -> None:
    catalogue, report = recording({"alpha": steady(85), "beta": steady(85)})
    early = event_store(tmp_path / "early", catalogue, with_revision=False)
    revised = event_store(tmp_path / "revised", catalogue, with_revision=True)
    runs = {
        name: run_backtest(
            catalogue, report, day(37), day(81), WHEN, ZERO, event_store=store, **SYNTHETIC_USE
        )
        for name, store in (("early", early), ("revised", revised))
    }

    def alpha_on(name: str, index: int) -> RecommendationSnapshot:
        return next(
            item
            for item in runs[name].snapshots
            if item.slug == "alpha" and item.latest_due_day == day(index)
        )

    # Before the event was first observed, nobody had looked: unavailable, no number.
    assert alpha_on("revised", 38).events.availability == "unavailable"
    assert alpha_on("revised", 38).events.event_heat is None
    # In its ramp, before the revision was observed: heat, and identical in both runs.
    for index in (58, 60, 62, 64, 65):
        assert alpha_on("early", index) == alpha_on("revised", index), index
        assert alpha_on("revised", index).snapshot_id == alpha_on("early", index).snapshot_id
    in_ramp = alpha_on("revised", 60).events
    assert in_ramp.availability == "available" and in_ramp.event_heat is not None
    assert in_ramp.event_heat > 0
    # After the revision is known the two histories part, as they should.
    assert alpha_on("early", 68).events.event_heat != alpha_on("revised", 68).events.event_heat
    assert alpha_on("revised", 68).events.event_heat == 0  # moved back out of its ramp
    # The other game was never looked at.
    beta = next(item for item in runs["revised"].snapshots if item.slug == "beta")
    assert beta.events.availability == "unavailable" and beta.events.event_heat is None


def test_events_add_nothing_to_score_v0_in_a_backtest(tmp_path: Path) -> None:
    catalogue, report = recording({"alpha": [*steady(60), *([260.0] * 25)], "beta": steady(85)})
    store = event_store(tmp_path, catalogue, with_revision=True)
    plain = run_backtest(catalogue, report, day(37), day(81), WHEN, ZERO)
    eventful = run_backtest(
        catalogue, report, day(37), day(81), WHEN, ZERO, event_store=store, **SYNTHETIC_USE
    )
    assert any(item.events.event_heat for item in eventful.snapshots)
    score_side = {"score", "rank", "confidence", "features", "explanation", "ranked_count"}
    for without, with_events in zip(plain.snapshots, eventful.snapshots, strict=True):
        assert without.model_dump(include=score_side) == with_events.model_dump(include=score_side)
    assert [item.realized_uplift_72h for item in plain.labels] == [
        item.realized_uplift_72h for item in eventful.labels
    ]
    assert plain.report.strategies == eventful.report.strategies
    assert eventful.report.events.real_event_history is True
    assert plain.report.events.real_event_history is False
    assert "unavailable" in plain.report.events.note
    with pytest.raises(ValueError, match="after the snapshot"):
        predict(
            catalogue,
            sees(report, 50),
            WHEN,
            event_records=store.events(),
        )


# --- event-source provenance and policy in a backtest ----------------------------------


def coverage_only_store(root: Path, catalogue: Catalogue, *indices: int) -> EventStore:
    """Synthetic: a source that looked at alpha on the given days and reported nothing."""
    store = EventStore(root)
    store.record_checks(
        CoverageCheck(
            canonical_game_id=catalogue.games[0].canonical_game_id,
            source=SYNTHETIC_EVENT_SOURCE,
            checked_at=start_of_day(day(index)),
        )
        for index in indices
    )
    return store


def test_a_backtest_refuses_visible_event_data_without_a_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gap: event data reached snapshots, and so a report, with no policy consulted."""
    catalogue, report = recording({"alpha": steady(85), "beta": steady(85)})
    with_record = event_store(tmp_path / "record", catalogue, with_revision=False)
    only_checks = coverage_only_store(tmp_path / "checks", catalogue, 47, 54, 61)
    policies = synthetic_event_policies()
    private = DeploymentStage.PRIVATE_DEVELOPMENT
    refusals: tuple[dict[str, Any], ...] = (
        {},  # nothing stated at all
        {"stage": private},  # a stage, no policy
        {"event_source_policies": {}, "stage": private},
        {"event_source_policies": policies},  # a policy, no stage: nothing is assumed
        {
            "event_source_policies": synthetic_event_policies("synthetic_other"),
            "stage": private,
        },  # every policy but this source's
        {
            "event_source_policies": {
                SYNTHETIC_EVENT_SOURCE: synthetic_event_policy("synthetic_other")
            },
            "stage": private,
        },  # a policy filed under the wrong name
        # The policy does not clear these stages.
        {"event_source_policies": policies, "stage": DeploymentStage.PRIVATE_COLLECTION},
        {"event_source_policies": policies, "stage": DeploymentStage.PUBLIC_LAUNCH},
        {"event_source_policies": policies, "stage": DeploymentStage.COMMERCIAL_PRODUCTION},
    )
    # A. a visible event record; B. a visible coverage check alone.
    for store in (with_record, only_checks):
        for options in refusals:
            with pytest.raises(SourcePolicyError, match=SYNTHETIC_EVENT_SOURCE):
                run_backtest(
                    catalogue, report, day(37), day(81), WHEN, ZERO, event_store=store, **options
                )
    # Refused before any of it could reach a snapshot: prediction itself was never asked.
    asked: list[object] = []
    real_predict = snapshot_module.predict

    def counting(*args: Any, **kwargs: Any) -> Any:
        asked.append(args[1].as_of)
        return real_predict(*args, **kwargs)

    monkeypatch.setattr(backtest_module, "predict", counting)
    with pytest.raises(SourcePolicyError):
        run_backtest(catalogue, report, day(47), day(60), WHEN, ZERO, event_store=only_checks)
    assert asked == []  # the check of day 47 is already visible at 00:00 that day
    with pytest.raises(SourcePolicyError):
        run_backtest(catalogue, report, day(45), day(60), WHEN, ZERO, event_store=only_checks)
    # Days 45 and 46 see nothing and are predicted; the first day that would see the
    # check is refused before it is predicted.
    assert asked == [moment(45), moment(46)]
    monkeypatch.undo()
    # There is no default and no name test in the backtest.
    parameters = inspect.signature(run_backtest).parameters
    assert parameters["event_source_policies"].default is None
    assert parameters["stage"].default is None
    source = Path(backtest_module.__file__).read_text()
    assert "PRIVATE_DEVELOPMENT" not in source and "synthetic" not in source.lower()
    assert "startswith" not in source


def test_event_data_never_visible_to_a_prediction_is_not_provenance(tmp_path: Path) -> None:
    catalogue, report = recording({"alpha": steady(85), "beta": steady(85)})
    plain = run_backtest(catalogue, report, day(37), day(60), WHEN, ZERO)
    # The store holds an observation and checks, all made after the last prediction.
    later = EventStore(tmp_path / "later")
    full = event_store(tmp_path / "full", catalogue, with_revision=True)
    after = moment(60)
    later.append(item for item in full.observations() if item.observed_at > after)
    later.record_checks(check for check in full.checks() if check.checked_at > after)
    assert later.observations() and later.checks()  # it is not an empty store
    # C. Nothing is visible to any prediction, so no policy is needed...
    unused = run_backtest(catalogue, report, day(37), day(60), WHEN, ZERO, event_store=later)
    # ...and nothing is recorded as used. Having a store is not having used it.
    assert unused.report.events.event_sources_used == ()
    assert unused.report.events.real_event_history is False
    assert unused.report.events.snapshots_with_event_intelligence == 0
    assert unused.report.events.snapshots_with_positive_event_heat == 0
    # The result is the no-event result, to the report id.
    assert [s.snapshot_id for s in unused.snapshots] == [s.snapshot_id for s in plain.snapshots]
    assert unused.report == plain.report
    assert "took no part" in " ".join(plain.report.limitations)
    assert "Real event backtesting is unavailable" in plain.report.events.note
    # One day further and the first later check is visible: now policy is required.
    with pytest.raises(SourcePolicyError, match=SYNTHETIC_EVENT_SOURCE):
        run_backtest(catalogue, report, day(37), day(62), WHEN, ZERO, event_store=later)


def test_a_report_records_exactly_the_event_sources_its_predictions_saw(tmp_path: Path) -> None:
    catalogue, report = recording({"alpha": steady(85), "beta": steady(85)})
    store = event_store(tmp_path / "events", catalogue, with_revision=True)
    # A second synthetic source that only looks after the range: never visible.
    store.record_checks(
        [
            CoverageCheck(
                canonical_game_id=catalogue.games[1].canonical_game_id,
                source="synthetic_latecomer",
                checked_at=start_of_day(day(84)),
            )
        ]
    )
    # D. With the test-only policy at the stage it clears, the backtest runs. The
    # latecomer needs no policy, because it is never used.
    result = run_backtest(
        catalogue, report, day(37), day(81), WHEN, ZERO, event_store=store, **SYNTHETIC_USE
    )
    events = result.report.events
    # E. Exactly the sources used: not every source in the store, and none registered.
    assert events.event_sources_used == (SYNTHETIC_EVENT_SOURCE,)
    assert events.real_event_history is True
    with_coverage = [s for s in result.snapshots if s.events.availability == "available"]
    with_heat = [s for s in result.snapshots if (s.events.event_heat or 0) > 0]
    assert events.snapshots_with_event_intelligence == len(with_coverage) > 0
    assert events.snapshots_with_positive_event_heat == len(with_heat) > 0
    assert len(with_heat) < len(with_coverage)  # covered with a heat of 0 is still covered
    assert f"Event sources used: {SYNTHETIC_EVENT_SOURCE}." in events.note
    assert "unavailable" not in events.note
    assert any(SYNTHETIC_EVENT_SOURCE in line for line in result.report.limitations)
    assert not any("took no part. There is no real" in line for line in result.report.limitations)
    # Extending the range to where the latecomer is visible makes it used, and gated.
    with pytest.raises(SourcePolicyError, match="synthetic_latecomer"):
        run_backtest(
            catalogue, report, day(37), day(85), WHEN, ZERO, event_store=store, **SYNTHETIC_USE
        )
    both = run_backtest(
        catalogue,
        report,
        day(37),
        day(85),
        WHEN,
        ZERO,
        event_store=store,
        event_source_policies=synthetic_event_policies(
            SYNTHETIC_EVENT_SOURCE, "synthetic_latecomer"
        ),
        stage=DeploymentStage.PRIVATE_DEVELOPMENT,
    )
    assert both.report.events.event_sources_used == ("synthetic_events", "synthetic_latecomer")

    # F. Deterministic: the same inputs give the same snapshots, labels and report.
    again = run_backtest(
        catalogue, report, day(37), day(81), WHEN + DAY, ZERO, event_store=store, **SYNTHETIC_USE
    )
    assert [s.snapshot_id for s in again.snapshots] == [s.snapshot_id for s in result.snapshots]
    assert [item.label_id for item in again.labels] == [item.label_id for item in result.labels]
    assert again.report.report_id == result.report.report_id
    assert again.report.events == result.report.events
    # The provenance is part of the report's identity.
    plain = run_backtest(catalogue, report, day(37), day(81), WHEN, ZERO)
    assert plain.report.report_id != result.report.report_id
    # K. And none of it touches score v0 or any outcome.
    score_side = {"score", "rank", "confidence", "features", "explanation", "ranked_count"}
    for without, with_events in zip(plain.snapshots, result.snapshots, strict=True):
        assert without.model_dump(include=score_side) == with_events.model_dump(include=score_side)
    assert plain.report.strategies == result.report.strategies
    assert plain.report.overall == result.report.overall
    assert plain.report.label_set_hash != "" and [
        (item.realized_uplift_72h, item.movement_24h) for item in plain.labels
    ] == [(item.realized_uplift_72h, item.movement_24h) for item in result.labels]


def test_coverage_alone_is_provenance(tmp_path: Path) -> None:
    catalogue, report = recording({"alpha": steady(85), "beta": steady(85)})
    store = coverage_only_store(tmp_path, catalogue, 47, 54, 61, 68, 75)
    assert store.observations() == ()
    result = run_backtest(
        catalogue, report, day(37), day(81), WHEN, ZERO, event_store=store, **SYNTHETIC_USE
    )
    events = result.report.events
    # J. No event was ever observed, and the source is still on record as used.
    assert events.event_sources_used == (SYNTHETIC_EVENT_SOURCE,)
    assert events.real_event_history is False
    assert events.snapshots_with_event_intelligence > 0
    assert events.snapshots_with_positive_event_heat == 0
    covered = [s for s in result.snapshots if s.events.availability == "available"]
    assert covered and all(s.events.event_heat == 0 for s in covered)
    assert {s.slug for s in covered} == {"alpha"}
    # A report cannot say its snapshots carried event intelligence and name no source.
    for claim in (
        {"event_sources_used": ()},
        {
            "event_sources_used": (),
            "snapshots_with_event_intelligence": 0,
            "real_event_history": True,
        },
        {"event_sources_used": ("b", "a")},
        {"event_sources_used": ("a", "a")},
        {"snapshots_with_positive_event_heat": events.snapshots_with_event_intelligence + 1},
    ):
        with pytest.raises(ValidationError):
            type(events).model_validate({**events.model_dump(), **claim})
    with pytest.raises(ValidationError, match="no event source is named"):
        build_report(
            list(result.pairs),
            RULES,
            PROTOCOL,
            EVENT_HEAT_RULES_V1.version,
            result.report.leakage_checks,
            real_event_history=False,
            data_through=result.report.data_through,
            created_at=WHEN,
        )


def bundle_with(
    report: ValidationReport | None,
    events: EventInputs | None,
    stage: DeploymentStage = DeploymentStage.PRIVATE_DEVELOPMENT,
) -> ArtifactBundle:
    catalogue, recording_, _ = real_recording()
    with replay_client(RECORDINGS) as client:
        registry = default_registry(client)
        registry.stage = stage
        return build_bundle(
            catalogue, recording_, assess(catalogue, recording_), registry, RUN_TIME, events, report
        )


def test_a_report_that_used_an_event_source_carries_its_policy_into_the_bundle(
    tmp_path: Path, fixture_backtest: BacktestResult
) -> None:
    catalogue, recording_, lags = real_recording()
    minecraft = next(game for game in catalogue.games if game.slug == "minecraft")
    store = EventStore(tmp_path / "events")
    store.record_checks(
        [
            CoverageCheck(
                canonical_game_id=minecraft.canonical_game_id,
                source=SYNTHETIC_EVENT_SOURCE,
                checked_at=datetime(2026, 9, 1, tzinfo=UTC),
            )
        ]
    )
    used = run_backtest(
        catalogue,
        recording_,
        FIRST_PREDICTION,
        LAST_PREDICTION,
        RUN_TIME,
        lags,
        event_store=store,
        **SYNTHETIC_USE,
    )
    report = used.report
    assert report.events.event_sources_used == (SYNTHETIC_EVENT_SOURCE,)
    policies = synthetic_event_policies()

    # G. The run itself has no event data at all. The report alone makes the bundle private.
    no_current_events = EventInputs(source_policies=policies)
    assert not no_current_events.records and not no_current_events.checks
    private = bundle_with(report, no_current_events)
    assert private.manifest.distribution == "private_development_only"
    (restricted,) = private.manifest.restricted_sources
    assert restricted.source == SYNTHETIC_EVENT_SOURCE
    assert restricted.cleared_stages == ("private_development",)
    published = private.validation.report
    assert published is not None
    assert published.events.event_sources_used == (SYNTHETIC_EVENT_SOURCE,)
    assert published.events.real_event_history is False
    assert published.events.snapshots_with_positive_event_heat == 0
    assert all(g.event_intelligence.availability == "unavailable" for g in private.games.values())
    # The same report with no policy to answer for it cannot be published as internal.
    for events in (None, EventInputs(), EventInputs(source_policies={})):
        with pytest.raises(SourcePolicyError, match=f"{SYNTHETIC_EVENT_SOURCE}: no source policy"):
            bundle_with(report, events)
    with pytest.raises(SourcePolicyError, match="no source policy"):
        bundle_with(
            report, EventInputs(source_policies=synthetic_event_policies("synthetic_other"))
        )
    # And at any stage the policy does not clear, it is refused.
    for stage in (
        DeploymentStage.PRIVATE_COLLECTION,
        DeploymentStage.PUBLIC_LAUNCH,
        DeploymentStage.COMMERCIAL_PRODUCTION,
    ):
        with pytest.raises(SourcePolicyError, match=SYNTHETIC_EVENT_SOURCE):
            bundle_with(report, no_current_events, stage)

    # I. No historical event source and no current one: the ordinary internal bundle.
    plain_report = fixture_backtest.report
    assert plain_report.events.event_sources_used == ()
    for events in (None, EventInputs()):
        ordinary = bundle_with(plain_report, events)
        assert ordinary.manifest.distribution == "internal"
        assert ordinary.manifest.restricted_sources == ()
    assert bundle_with(plain_report, None, DeploymentStage.PUBLIC_LAUNCH).manifest.distribution == (
        "internal"
    )
    # Coverage made no difference to any prediction's score or to any outcome.
    assert report.snapshot_set_hash != plain_report.snapshot_set_hash  # snapshots carry it
    assert report.strategies == plain_report.strategies
    assert report.overall == plain_report.overall

    # Published, the validator holds the same line from the outside.
    root = tmp_path / "artifacts"
    publish_bundle(private, root)
    manifest = validate_published(root)
    assert manifest.schema_version == "1.5.0"
    original = (root / "manifest.json").read_text()
    document = json.loads(original)
    document["distribution"], document["restricted_sources"] = "internal", []
    (root / "manifest.json").write_text(json.dumps(document, indent=2) + "\n")
    with pytest.raises(
        ArtifactValidationError, match=f"{SYNTHETIC_EVENT_SOURCE} appear in the bundle"
    ):
        validate_published(root)
    (root / "manifest.json").write_text(original)
    version = root / manifest.version_path
    body = (version / "validation.json").read_text()
    for path in (version / "validation.json", root / "validation.json"):
        tampered = json.loads(body)
        tampered["report"]["events"]["event_sources_used"] = []
        path.write_text(json.dumps(tampered, indent=2) + "\n")
    with pytest.raises(ArtifactValidationError, match="names no event source"):
        validate_published(root)


def test_the_no_event_fixture_backtest_is_exactly_what_it_was(
    fixture_backtest: BacktestResult,
) -> None:
    """Provenance was added around the backtest; no prediction or outcome moved."""
    report = fixture_backtest.report
    # The same records as before provenance existed, by their content hashes.
    assert report.snapshot_set_hash == (
        "8a0f192d11aac11fd92d37736598cb3cd7ea130c253c84cac5efeb86770ee748"
    )
    assert report.label_set_hash == (
        "6b8aa3ce15dbe68977bf1bd68778d4ed65f0e2cfd704a94af7adf70d182cabdf"
    )
    assert report.format == "validation_report_v3"
    assert report.events.event_sources_used == ()
    assert report.events.real_event_history is False
    assert report.events.snapshots_with_event_intelligence == 0
    assert report.events.snapshots_with_positive_event_heat == 0
    assert "Real event backtesting is unavailable" in report.events.note
    assert report.evidence.status is EvidenceStatus.FRAMEWORK_ONLY


def test_a_report_from_before_provenance_is_kept_and_never_read_as_current(
    tmp_path: Path, fixture_backtest: BacktestResult
) -> None:
    store = AccountabilityStore(tmp_path)
    store.add_report(fixture_backtest.report)
    reports = tmp_path / STORE_GENERATION / "reports"
    # An earlier report: the same thing without the provenance fields.
    older = json.loads(fixture_backtest.report.model_dump_json())
    older["format"] = "validation_report_v2"
    older["report_id"] = "report_" + "1" * 32
    del older["events"]["event_sources_used"]
    del older["events"]["snapshots_with_positive_event_heat"]
    older["created_at"] = "2027-01-01T00:00:00Z"  # newer by the clock, older by format
    old_path = reports / f"{older['report_id']}.json"
    old_path.write_text(json.dumps(older, indent=2) + "\n")
    before = old_path.read_bytes()
    # It cannot say which event sources it used, so it is not offered for publication.
    assert [r.report_id for r in store.reports()] == [fixture_backtest.report.report_id]
    assert store.latest_report() == fixture_backtest.report
    assert old_path.read_bytes() == before  # kept, untouched
    # A current-format report that is broken is still corruption, not silently skipped.
    (reports / "report_broken.json").write_text('{"format": "validation_report_v3"}\n')
    with pytest.raises(StorageCorruptionError):
        store.reports()


# --- outcomes: exact definitions and boundaries ---------------------------------------


def test_the_primary_outcome_is_the_next_three_days_against_the_frozen_baseline() -> None:
    snapshot, outcome = scenario([120.0, 130.0, 140.0, 100.0, 100.0, 100.0, 100.0])
    assert snapshot.features is not None
    assert snapshot.features.baseline_median_28d == 100.0 and snapshot.features.latest_views == 110
    assert outcome.availability is OutcomeAvailability.AVAILABLE
    assert outcome.realized_uplift_72h == pytest.approx(0.30)
    assert outcome.reference_baseline == 100.0
    assert outcome.reference_avg_3d == pytest.approx(310 / 3)
    assert (outcome.primary_window_start, outcome.primary_window_end) == (day(60), day(62))
    assert [(item.day, item.value) for item in outcome.forward][:3] == [
        (day(60), 120.0),
        (day(61), 130.0),
        (day(62), 140.0),
    ]
    assert len(outcome.forward) == RULES.future_days == 7
    assert outcome.snapshot_id == snapshot.snapshot_id and outcome.label_id.startswith("label_")
    assert outcome.outcome_rules_version == "outcome_rules_v1"
    # The fourth day is outside the primary window: it changes nothing.
    _, other = scenario([120.0, 130.0, 140.0, 9000.0, 100.0, 100.0, 100.0])
    assert other.realized_uplift_72h == pytest.approx(0.30)


def test_zero_means_exactly_at_baseline_and_negative_outcomes_are_kept() -> None:
    _, at_baseline = scenario([100.0, 100.0, 100.0])
    assert at_baseline.realized_uplift_72h == 0.0
    assert at_baseline.availability is OutcomeAvailability.AVAILABLE
    _, below = scenario([50.0, 60.0, 70.0])
    assert below.realized_uplift_72h == pytest.approx(-0.40)
    _, collapse = scenario([0.0, 0.0, 0.0])
    assert collapse.realized_uplift_72h == -1.0  # an observed zero is a value
    _, huge = scenario([5000.0, 6000.0, 7000.0])
    assert huge.realized_uplift_72h == pytest.approx(59.0)  # not clipped


@pytest.mark.parametrize(
    "future",
    [
        [],
        [120.0],
        [120.0, 130.0],
        [120.0, None, 140.0],
        [None, 130.0, 140.0],
        [120.0, 130.0, None, 150.0, 160.0],
    ],
)
def test_an_incomplete_forward_window_is_unavailable_never_zero(future: list[float | None]) -> None:
    _, outcome = scenario(future)
    assert outcome.availability is OutcomeAvailability.UNAVAILABLE
    assert outcome.unavailable_reason is OutcomeUnavailable.FUTURE_INCOMPLETE
    assert outcome.realized_uplift_72h is None  # not 0, and not a mean of what is there
    assert len(outcome.forward) == 7
    with pytest.raises(ValidationError, match="a number exactly when it is available"):
        OutcomeLabel.model_validate({**outcome.model_dump(), "realized_uplift_72h": 0.0})


def test_exactly_three_complete_days_are_enough_for_the_primary_outcome() -> None:
    _, outcome = scenario([120.0, 130.0, 140.0])
    assert outcome.realized_uplift_72h == pytest.approx(0.30)
    assert outcome.persistence_7d is None  # the week is not complete
    assert outcome.peak_status is PeakStatus.UNAVAILABLE and outcome.lead_time_days is None
    assert outcome.movement_24h is not None


def test_the_24_hour_movement_is_the_next_day_against_the_known_three_day_level() -> None:
    _, outcome = scenario([155.0])
    assert outcome.movement_24h == pytest.approx(155 / (310 / 3) - 1)
    assert outcome.availability is OutcomeAvailability.UNAVAILABLE  # primary still is not
    _, flat = scenario([310 / 3, 100.0, 100.0])
    assert flat.movement_24h == pytest.approx(0.0, abs=1e-12)
    _, missing = scenario([None, 130.0, 140.0])
    assert missing.movement_24h is None
    _, none = scenario([])
    assert none.movement_24h is None


def test_persistence_needs_the_whole_week_and_counts_days_strictly_above_baseline() -> None:
    week = [101.0, 100.0, 99.0, 150.0, 100.0001, 100.0, 200.0]
    _, outcome = scenario(week)
    assert outcome.persistence_7d == pytest.approx(4 / 7)  # 100 exactly is not above
    _, six = scenario(week[:6])
    assert six.persistence_7d is None
    _, gap = scenario([*week[:3], None, *week[4:]])
    assert gap.persistence_7d is None
    _, eighth_day = scenario([*week, 5000.0])
    assert eighth_day.persistence_7d == pytest.approx(4 / 7)  # day 8 is outside the window
    _, always = scenario([101.0] * 7)
    assert always.persistence_7d == 1.0
    _, never = scenario([100.0] * 7)
    assert never.persistence_7d == 0.0


@pytest.mark.parametrize(
    ("future", "status", "lead"),
    [
        # Day 1 is above day 0 (110) and not below day 2: a peak at once.
        ([120.0, 115.0, 100.0, 100.0, 100.0, 100.0, 100.0], PeakStatus.FOUND, 1),
        # Rising to day 3, then falling.
        ([111.0, 112.0, 113.0, 112.0, 100.0, 100.0, 100.0], PeakStatus.FOUND, 3),
        # A plateau counts on its first day: not below the next.
        ([105.0, 120.0, 120.0, 119.0, 100.0, 100.0, 100.0], PeakStatus.FOUND, 2),
        # The last searchable day, confirmed by day 7.
        ([100.0, 100.0, 100.0, 100.0, 100.0, 130.0, 120.0], PeakStatus.FOUND, 6),
        # Still rising on day 7: no confirmed peak inside the window.
        ([111.0, 112.0, 113.0, 114.0, 115.0, 116.0, 117.0], PeakStatus.NONE_IN_WINDOW, None),
        # A local peak that is not above normal is not an attention peak.
        ([50.0, 60.0, 55.0, 54.0, 53.0, 52.0, 51.0], PeakStatus.NONE_IN_WINDOW, None),
        # Falling throughout.
        ([109.0, 108.0, 107.0, 106.0, 105.0, 104.0, 103.0], PeakStatus.NONE_IN_WINDOW, None),
        # Day 7 missing: day 6 cannot be confirmed, so the search is unavailable.
        ([100.0, 100.0, 100.0, 100.0, 100.0, 130.0], PeakStatus.UNAVAILABLE, None),
        # A gap anywhere in the path.
        ([120.0, None, 100.0, 100.0, 100.0, 100.0, 100.0], PeakStatus.UNAVAILABLE, None),
    ],
)
def test_lead_time_is_the_first_confirmed_peak_above_normal(
    future: list[float | None], status: PeakStatus, lead: int | None
) -> None:
    _, outcome = scenario(future)
    assert (outcome.peak_status, outcome.lead_time_days) == (status, lead)


def test_a_game_with_no_score_at_the_snapshot_has_no_outcome_to_answer_for() -> None:
    catalogue, report = recording(
        {"alpha": [*steady(60), 500.0, 500.0, 500.0], "beta": [None] * 63}
    )
    history = RecordedHistory(report, ZERO)
    snapshots = predict(catalogue, history.inputs_at(moment(59 + 1)), WHEN)
    beta = next(item for item in snapshots if item.slug == "beta")
    assert beta.rank is None and beta.score.value is None and beta.features is None
    outcome = label(beta, history.future())
    assert outcome.availability is OutcomeAvailability.UNAVAILABLE
    assert outcome.unavailable_reason is OutcomeUnavailable.NOT_SCORED_AT_SNAPSHOT
    assert (outcome.realized_uplift_72h, outcome.movement_24h, outcome.persistence_7d) == (
        None,
        None,
        None,
    )
    assert outcome.forward == ()


def test_a_label_reads_the_snapshot_and_the_future_and_never_recomputes_the_reference() -> None:
    snapshot, outcome = scenario([120.0, 130.0, 140.0, 100.0, 100.0, 100.0, 100.0])
    # Hand the labeller a future in which the past is also different: it cannot matter,
    # because the only past it has is what the snapshot recorded.
    assert snapshot.scored_signal is not None
    future = FutureValues(
        {
            (snapshot.canonical_game_id, snapshot.scored_signal): {
                **{day(index): 99999.0 for index in range(60)},
                day(60): 120.0,
                day(61): 130.0,
                day(62): 140.0,
            }
        },
        data_through=day(62),
    )
    again = label(snapshot, future)
    assert again.realized_uplift_72h == outcome.realized_uplift_72h == pytest.approx(0.30)
    assert again.reference_baseline == 100.0
    assert again.data_through == day(62) and again.label_id != outcome.label_id
    assert list(inspect.signature(label).parameters) == ["snapshot", "future", "rules"]


# --- the universe at T ----------------------------------------------------------------


def test_the_ranking_at_t_uses_only_games_scorable_at_t() -> None:
    latecomer: list[float | None] = [*([None] * 40), *steady(40)]
    vanisher: list[float | None] = [*steady(55), *([None] * 25)]
    catalogue, report = recording(
        {"steady": steady(80), "latecomer": latecomer, "vanisher": vanisher, "never": [None] * 80}
    )
    result = run_backtest(catalogue, report, day(37), day(80), WHEN, ZERO)

    def on(index: int) -> dict[str, RecommendationSnapshot]:
        return {s.slug: s for s in result.snapshots if s.latest_due_day == day(index)}

    early = on(45)
    assert early["latecomer"].rank is None  # five days of data: no baseline yet
    assert early["latecomer"].score.unscored_reason is not None
    assert early["vanisher"].rank is not None  # it vanishes later; at T it was there
    assert early["never"].rank is None and early["steady"].rank is not None
    assert {item.universe_size for item in early.values()} == {4}
    assert {item.ranked_count for item in early.values()} == {2}
    late = on(79)
    assert late["latecomer"].rank is not None
    # A game whose data stopped is still scored on its last observed day, as score v0
    # does today, but with a confidence of zero: its staleness is what the snapshot shows.
    assert {item.ranked_count for item in late.values()} == {3}
    stale = late["vanisher"]
    assert stale.rank == 3 and stale.confidence is not None and stale.confidence.value == 0
    assert stale.features is not None and stale.features.as_of_day == day(54)
    assert stale.features.staleness_days is not None and stale.features.staleness_days >= 20
    # Every game is in every day's snapshots, ranked or not, with its reason.
    assert len(result.snapshots) == 44 * 4
    for snapshot in result.snapshots:
        assert (snapshot.rank is None) == (snapshot.score.value is None)
        if snapshot.rank is None:
            assert snapshot.score.unscored_reason is not None
    report_ = result.report
    assert (report_.universe_size, report_.games_ranked) == (4, 3)
    assert report_.ranked + report_.unranked == report_.recommendations == 176
    assert report_.primary_unavailable["not_scored_at_snapshot"] == report_.unranked


def test_ties_are_broken_by_slug_in_every_strategy() -> None:
    values = [*steady(60), 300.0, 320.0, 340.0, 300.0, 300.0, 300.0, 300.0]
    catalogue, report = recording({"zulu": values, "alpha": values, "mike": values})
    snapshots = predict(catalogue, sees(report, 62), WHEN)
    by_slug = {item.slug: item for item in snapshots}
    assert (
        by_slug["alpha"].score.value == by_slug["mike"].score.value == by_slug["zulu"].score.value
    )
    assert [by_slug[slug].rank for slug in ("alpha", "mike", "zulu")] == [1, 2, 3]
    for strategy in STRATEGIES:
        assert [item.slug for item in order(strategy, list(snapshots))] == ["alpha", "mike", "zulu"]
    again = predict(catalogue, sees(report, 62), WHEN)
    assert [item.rank for item in again] == [item.rank for item in snapshots]


def test_comparators_order_the_same_universe_by_values_the_snapshot_already_held() -> None:
    big_flat = [value * 100 for value in steady(70) if value is not None]
    lifting = [*steady(64), 100.0, 100.0, 100.0, 180.0, 180.0, 180.0]
    accelerating = [*steady(64), 60.0, 60.0, 60.0, 150.0, 150.0, 150.0]
    catalogue, report = recording(
        {"big": big_flat, "lifting": lifting, "accelerating": accelerating, "absent": [None] * 70}
    )
    snapshots = predict(catalogue, sees(report, 69), WHEN)
    ranked = [item for item in snapshots if item.rank is not None]
    assert {item.slug for item in ranked} == {"big", "lifting", "accelerating"}
    features = {item.slug: item.features for item in ranked}
    assert all(value is not None for value in features.values())
    assert order("raw_popularity", ranked)[0].slug == "big"
    assert order("simple_lift", ranked)[0].slug == "lifting"
    assert order("simple_velocity", ranked)[0].slug == "accelerating"
    assert [item.rank for item in order("metaspike_score_v0", ranked)] == [1, 2, 3]
    for strategy in STRATEGIES:
        assert {item.slug for item in order(strategy, ranked)} == {"big", "lifting", "accelerating"}
    assert set(STRATEGIES) == {
        "metaspike_score_v0",
        "raw_popularity",
        "simple_lift",
        "simple_velocity",
    }


# --- metrics --------------------------------------------------------------------------


def test_summaries_keep_negative_outcomes_and_an_empty_one_has_no_statistics() -> None:
    values = [0.5, 0.1, 0.0, -0.1, -0.6, 0.09, -0.09]
    summary = summarise(values, RULES)
    assert summary.count == 7 and summary.minimum == -0.6 and summary.maximum == 0.5
    assert summary.mean == pytest.approx(statistics.fmean(values))
    assert summary.median == 0.0
    assert summary.hit_rate == pytest.approx(2 / 7)  # 0.5 and exactly 0.10
    assert summary.downside_rate == pytest.approx(2 / 7)  # -0.6 and exactly -0.10
    empty = summarise([], RULES)
    assert empty.count == 0
    assert (empty.mean, empty.median, empty.hit_rate, empty.downside_rate) == (None,) * 4


def test_spearman_is_exact_on_known_cases() -> None:
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    assert spearman([1, 2, 3, 4, 5], [2, 1, 4, 3, 5]) == pytest.approx(0.8)
    assert spearman([1, 2, 3], [5, 5, 5]) is None  # no variation, no correlation
    assert spearman([1], [1]) is None
    assert spearman([1, 2, 2, 3], [1, 2, 2, 3]) == pytest.approx(1.0)  # ties share ranks
    assert spearman([1, 2, 3], [1, 2]) is None


def test_top_k_and_association_match_an_independent_count(fixture_backtest: BacktestResult) -> None:
    pairs = fixture_backtest.pairs
    by_day: dict[date, list[tuple[RecommendationSnapshot, OutcomeLabel]]] = {}
    for pair in pairs:
        by_day.setdefault(prediction_day(pair[0]), []).append(pair)
    for strategy in STRATEGIES:
        result = evaluate_strategy(strategy, pairs, RULES, PROTOCOL)
        assert [group.k for group in result.top_k] == [1, 3, 5]
        for group in result.top_k:
            expected: list[float] = []
            full = 0
            for day_pairs in by_day.values():
                labels = {s.snapshot_id: o for s, o in day_pairs}
                ordered = order(strategy, [s for s, _ in day_pairs if s.rank is not None])
                full += len(ordered) >= group.k
                expected += [
                    uplift
                    for snapshot in ordered[: group.k]
                    if (uplift := labels[snapshot.snapshot_id].realized_uplift_72h) is not None
                ]
            assert group.days_with_full_group == full
            assert group.outcomes.count == len(expected)
            assert group.outcomes.mean == pytest.approx(statistics.fmean(expected))
            assert group.outcomes.minimum == min(expected)
        association = result.association
        assert association.days_used + association.days_skipped == len(by_day)
        assert association.positive_days + association.negative_days <= association.days_used
    v0 = evaluate_strategy("metaspike_score_v0", pairs, RULES, PROTOCOL)
    assert v0 == next(s for s in fixture_backtest.report.strategies if s.strategy == v0.strategy)
    # A wider group can only hold at least as many outcomes.
    counts = [group.outcomes.count for group in v0.top_k]
    assert counts == sorted(counts)


# --- evidence -------------------------------------------------------------------------


def test_the_evidence_status_follows_the_stated_thresholds() -> None:
    def status(dates: int, games: int, outcomes: int, sources: int = 1) -> EvidenceStatus:
        return assess_evidence(PROTOCOL, dates, games, outcomes, sources).status

    assert status(0, 0, 0) is EvidenceStatus.FRAMEWORK_ONLY
    assert status(90, 6, 402) is EvidenceStatus.FRAMEWORK_ONLY  # the real fixture: six games
    assert status(29, 20, 500) is EvidenceStatus.FRAMEWORK_ONLY
    assert status(30, 19, 500) is EvidenceStatus.FRAMEWORK_ONLY
    assert status(30, 20, 499) is EvidenceStatus.FRAMEWORK_ONLY
    assert status(30, 20, 500) is EvidenceStatus.INSUFFICIENT_SAMPLE
    assert status(89, 100, 5000) is EvidenceStatus.INSUFFICIENT_SAMPLE
    assert status(90, 100, 5000) is EvidenceStatus.EXPLORATORY
    assert status(10_000, 100_000, 10_000_000, 9) is EvidenceStatus.EXPLORATORY
    # A higher bar met while a lower one is not does not count.
    assert status(200, 600, 60000, 2) is EvidenceStatus.EXPLORATORY


def test_validated_cannot_be_reached_under_protocol_v1() -> None:
    huge = assess_evidence(PROTOCOL, 10**6, 10**6, 10**9, 10)
    assert huge.status is EvidenceStatus.EXPLORATORY and huge.holdout_supported is False
    validated = next(check for check in huge.checks if check.status is EvidenceStatus.VALIDATED)
    assert validated.reached is False
    held_out = next(item for item in validated.requirements if item.name == "held_out_period")
    assert (held_out.required, held_out.actual, held_out.met) == (1, 0, False)
    assert all(item.met for item in validated.requirements if item.name != "held_out_period")
    assert "cannot report validated" in huge.holdout_rationale
    # Even a protocol that did support a holdout needs every size requirement too.
    with_holdout = PROTOCOL.model_copy(update={"supports_holdout": True})
    assert assess_evidence(with_holdout, 180, 500, 50000, 1).status is EvidenceStatus.EXPLORATORY
    assert assess_evidence(with_holdout, 180, 500, 50000, 2).status is EvidenceStatus.VALIDATED


# --- the real recorded replay ---------------------------------------------------------


def test_with_no_lag_the_backtest_reproduces_score_v0_as_it_was_published() -> None:
    """The published replay scored every day the source returned, 3 October included.

    Under the source's one-day policy lag that day is not visible at 00:00 on the 4th,
    so the lag-safe backtest does not, and must not, equal the published run. With the
    lag set to zero it sees exactly what the published run saw, and reproduces it: the
    scoring is the same code, and only the simulation's view of time has changed.
    """
    catalogue, report, _ = real_recording()
    moment_after = RECORDED_END + DAY
    zero_lag = run_backtest(catalogue, report, moment_after, moment_after, RUN_TIME, ZERO)
    assert len(zero_lag.snapshots) == 15 == len(GOLDEN["games"])
    for snapshot in zero_lag.snapshots:
        published = GOLDEN["games"][snapshot.slug]
        assert snapshot.latest_due_day == RECORDED_END
        assert snapshot.rank == published["rank"], snapshot.slug
        assert snapshot.score.model_dump(mode="json") == published["score"]
        if published["confidence"] is None:
            assert snapshot.confidence is None and snapshot.features is None
            continue
        assert snapshot.confidence is not None and snapshot.features is not None
        assert snapshot.confidence.model_dump(mode="json") == published["confidence"]
        exact = snapshot.features.model_dump(mode="json")
        for name, value in published["features"].items():
            if isinstance(value, float):
                assert exact[name] == pytest.approx(value, abs=1e-4), (snapshot.slug, name)
            else:
                assert exact[name] == value, (snapshot.slug, name)
        assert snapshot.explanation.model_dump(mode="json") == published["explanation"]
        assert snapshot.score_version == "metaspike_score_v0"
        assert snapshot.events.availability == "unavailable"
        assert snapshot.events.event_heat is None


def test_under_the_policy_lag_no_snapshot_sees_a_day_its_source_had_not_published(
    fixture_backtest: BacktestResult,
) -> None:
    for snapshot in fixture_backtest.snapshots:
        assert snapshot.as_of == prediction_moment(prediction_day(snapshot))
        assert snapshot.evaluation_day == prediction_day(snapshot) - DAY
        assert snapshot.latest_due_day == prediction_day(snapshot) - 2 * DAY
        assert snapshot.publication_lag_hours == 24.0
        if snapshot.latest_observed_day is not None:
            assert snapshot.latest_observed_day <= snapshot.latest_due_day
            assert snapshot.features is not None
            assert snapshot.features.as_of_day == snapshot.latest_observed_day
    for snapshot, outcome in fixture_backtest.pairs:
        assert outcome.prediction_at == snapshot.as_of
        assert outcome.primary_window_start == prediction_day(snapshot)
        if snapshot.latest_observed_day is not None:
            # Two days separate what was seen from what is judged.
            assert (outcome.primary_window_start - snapshot.latest_observed_day).days >= 2
    # At 00:00 on 4 October the published run's last day, 3 October, is not yet visible.
    fourth = [
        s for s in fixture_backtest.snapshots if prediction_day(s) == date(2026, 10, 4) and s.rank
    ]
    assert fourth and {s.latest_observed_day for s in fourth} == {date(2026, 10, 2)}


def test_the_fixture_backtest_reports_exactly_what_the_recording_shows(
    fixture_backtest: BacktestResult,
) -> None:
    report = fixture_backtest.report
    assert (report.first_prediction_day, report.last_prediction_day) == (
        FIRST_PREDICTION,
        LAST_PREDICTION,
    )
    assert (report.first_latest_due_day, report.last_latest_due_day) == (
        date(2026, 7, 5),
        RECORDED_END,
    )
    assert report.publication_lag_hours == {SOURCE: 24.0}
    assert report.snapshot_dates == 91 and report.data_through == RECORDED_END
    assert (report.universe_size, report.games_ranked, report.games_with_outcome) == (15, 6, 6)
    assert (report.recommendations, report.ranked, report.unranked) == (1365, 420, 945)
    assert report.primary_evaluable == 396 == report.overall.count
    assert report.primary_unavailable == {"future_incomplete": 24, "not_scored_at_snapshot": 945}
    # Recommendations from 2 to 5 October have no complete three days ahead: six games each.
    assert report.primary_unavailable["future_incomplete"] == 4 * 6
    first_ranked = min(prediction_day(s) for s in fixture_backtest.snapshots if s.rank)
    assert first_ranked == date(2026, 7, 28)  # rests on data through 26 July
    assert report.independent_sources == 1
    # Six games is nowhere near enough: the machinery is shown to work, nothing more.
    assert report.evidence.status is EvidenceStatus.FRAMEWORK_ONLY
    assert "nothing here should be read as evidence" in report.evidence.conclusion
    assert not any(check.reached for check in report.evidence.checks)
    assert [check.name for check in report.leakage_checks] == [
        "source_publication_lag_respected",
        "outcome_window_after_prediction",
        "reproduced_after_labelling",
        "unchanged_when_unseen_values_altered",
        "reference_frozen_at_snapshot",
    ]
    assert all(check.passed and check.checked > 0 for check in report.leakage_checks)
    assert report.leakage_checks[0].checked == 91 * 6  # every series given to a prediction
    assert report.leakage_checks[1].checked == 1365 == report.leakage_checks[2].checked
    assert report.events.real_event_history is False
    assert report.events.snapshots_with_event_intelligence == 0
    assert report.overall.minimum is not None and report.overall.minimum < 0  # negatives kept
    assert report.overall.downside_rate is not None and report.overall.downside_rate > 0
    assert sum(stratum.outcomes.count for stratum in report.score_bins) == 396
    assert sum(stratum.outcomes.count for stratum in report.confidence_bands) == 396
    assert [stratum.label for stratum in report.score_bins] == [
        "0",
        "0 to 10",
        "10 to 25",
        "25 to 50",
        "50 to 100",
    ]
    high = next(s for s in report.confidence_bands if s.label == "high")
    assert high.outcomes.count == 0 and high.outcomes.mean is None  # empty, not zero
    assert len(report.inspection) == 60 and report.inspection_truncated is True
    last = report.inspection[-1]
    assert (last.day, last.latest_due_day) == (LAST_PREDICTION, RECORDED_END)
    assert (last.primary_window_start, last.primary_window_end) == (
        date(2026, 10, 5),
        date(2026, 10, 7),
    )
    top = last.ranked[0]
    assert (top.rank, top.slug) == (1, "minecraft")
    assert top.outcome_availability == "unavailable" and top.realized_uplift_72h is None
    assert len(last.unranked) == 9
    scored_day = report.inspection[0]
    assert all(entry.realized_uplift_72h is not None for entry in scored_day.ranked)
    assert any("In-sample only" in line for line in report.limitations)
    assert any("not independent" in line for line in report.limitations)
    assert any("has not been backtested" in line for line in report.limitations)
    assert any("publication lag" in line for line in report.limitations)


def test_the_corrected_fixture_result_is_reported_as_it_is(
    fixture_backtest: BacktestResult,
) -> None:
    by_name = {s.strategy: s for s in fixture_backtest.report.strategies}
    v0, lift = by_name["metaspike_score_v0"], by_name["simple_lift"]
    assert v0.association.mean_rho is not None and lift.association.mean_rho is not None
    # The corrected figures, pinned so a change in the simulation cannot pass unnoticed.
    assert v0.top_k[0].outcomes.mean == pytest.approx(1.1437, abs=5e-4)
    assert v0.top_k[0].outcomes.median == pytest.approx(0.5012, abs=5e-4)
    assert v0.association.mean_rho == pytest.approx(0.623, abs=5e-4)
    assert lift.association.mean_rho == pytest.approx(0.706, abs=5e-4)
    assert fixture_backtest.report.overall.median == pytest.approx(0.0609, abs=5e-4)
    # Lower than the first version's +129.3%, +0.68 and +0.78, which let an unpublished
    # day into each prediction and so into each ranking.
    assert v0.top_k[0].outcomes.mean is not None and v0.top_k[0].outcomes.mean < 1.2926
    assert v0.association.mean_rho is not None and v0.association.mean_rho < 0.679
    assert lift.association.mean_rho < 0.779
    # Plain lift still orders the games at least as well as score v0. Reported, not hidden.
    assert lift.association.mean_rho >= v0.association.mean_rho
    assert v0.association.days_used == lift.association.days_used == 66
    assert v0.top_k[0].outcomes.count == 66
    assert by_name["raw_popularity"].association.mean_rho is not None


def test_the_backtest_is_deterministic_and_linear_in_days_and_games(
    fixture_backtest: BacktestResult,
) -> None:
    catalogue, report, lags = real_recording()
    again = run_backtest(catalogue, report, FIRST_PREDICTION, LAST_PREDICTION, RUN_TIME + DAY, lags)
    assert again.report.report_id == fixture_backtest.report.report_id
    assert [s.snapshot_id for s in again.snapshots] == [
        s.snapshot_id for s in fixture_backtest.snapshots
    ]
    assert [item.label_id for item in again.labels] == [
        item.label_id for item in fixture_backtest.labels
    ]
    assert again.report.created_at != fixture_backtest.report.created_at
    assert len(fixture_backtest.snapshots) == 91 * 15 == len(fixture_backtest.labels)
    half = run_backtest(catalogue, report, FIRST_PREDICTION, date(2026, 8, 20), RUN_TIME, lags)
    assert len(half.snapshots) == 45 * 15
    # A shorter run's snapshots are the same snapshots: a day does not depend on the range.
    assert [s.snapshot_id for s in half.snapshots] == [
        s.snapshot_id for s in fixture_backtest.snapshots[: 45 * 15]
    ]
    # The recording cannot stand in for a moment before it starts, or one at which a
    # day it does not hold would already have been published.
    for first, last in (
        (date(2026, 7, 6), LAST_PREDICTION),
        (FIRST_PREDICTION, date(2026, 10, 6)),
    ):
        with pytest.raises(ValueError, match="cannot be reconstructed"):
            run_backtest(catalogue, report, first, last, RUN_TIME, lags)
    # With no lag the reachable range is one day shorter at the end, not longer.
    run_backtest(catalogue, report, date(2026, 10, 4), date(2026, 10, 4), RUN_TIME, ZERO)
    with pytest.raises(ValueError, match="cannot be reconstructed"):
        run_backtest(catalogue, report, date(2026, 10, 5), date(2026, 10, 5), RUN_TIME, ZERO)
    with pytest.raises(ValueError, match="ends before it starts"):
        run_backtest(catalogue, report, LAST_PREDICTION, FIRST_PREDICTION, RUN_TIME, lags)
    # A lag has to be stated; there is no default to fall back on.
    assert inspect.signature(run_backtest).parameters["publication_lag"].default is inspect._empty


# --- provenance -----------------------------------------------------------------------


def test_a_report_names_the_exact_records_it_was_made_from(
    fixture_backtest: BacktestResult,
) -> None:
    report = fixture_backtest.report
    snapshot_ids = [s.snapshot_id for s in fixture_backtest.snapshots]
    label_ids = [item.label_id for item in fixture_backtest.labels]
    assert report.snapshot_set_hash == set_hash(snapshot_ids)
    assert report.label_set_hash == set_hash(label_ids)
    assert len(report.snapshot_set_hash) == 64 and report.snapshot_set_hash != report.label_set_hash
    # Order does not matter; membership does.
    assert set_hash(reversed(snapshot_ids)) == report.snapshot_set_hash
    assert set_hash(snapshot_ids[:-1]) != report.snapshot_set_hash
    assert set_hash([*snapshot_ids[:-1], "snap_" + "0" * 32]) != report.snapshot_set_hash
    assert set_hash([]) == hashlib.sha256(b"").hexdigest()
    # The report's identity includes its provenance.
    pairs = list(fixture_backtest.pairs)
    rebuilt = build_report(
        pairs,
        RULES,
        PROTOCOL,
        EVENT_HEAT_RULES_V1.version,
        report.leakage_checks,
        real_event_history=False,
        data_through=report.data_through,
        created_at=RUN_TIME,
    )
    assert rebuilt.report_id == report.report_id
    assert rebuilt.snapshot_set_hash == report.snapshot_set_hash
    fewer = build_report(
        pairs[:-15],
        RULES,
        PROTOCOL,
        EVENT_HEAT_RULES_V1.version,
        report.leakage_checks,
        real_event_history=False,
        data_through=report.data_through,
        created_at=RUN_TIME,
    )
    assert fewer.snapshot_set_hash != report.snapshot_set_hash
    assert fewer.label_set_hash != report.label_set_hash
    assert fewer.report_id != report.report_id
    # Only ids and hashes: no observation, value series or source payload.
    dumped = report.model_dump_json()
    assert "forward" not in dumped and "points" not in dumped and "period_start" not in dumped


def test_one_changed_snapshot_or_label_changes_the_matching_hash() -> None:
    quiet = {"alpha": [*steady(60), *steady(20)], "beta": steady(80)}
    loud = {"alpha": [*steady(60), *steady(20)], "beta": with_spike(40, 4000.0)}
    later = {"alpha": [*steady(60), *([900.0] * 20)], "beta": steady(80)}
    reports = {}
    for name, series in (("quiet", quiet), ("loud", loud), ("later", later)):
        catalogue, report = recording(series)
        reports[name] = run_backtest(catalogue, report, day(40), day(60), WHEN, ZERO).report
    again_catalogue, again_report = recording(quiet)
    again = run_backtest(again_catalogue, again_report, day(40), day(60), WHEN + DAY, ZERO).report
    assert again.snapshot_set_hash == reports["quiet"].snapshot_set_hash
    assert again.label_set_hash == reports["quiet"].label_set_hash
    assert again.report_id == reports["quiet"].report_id
    # A different visible past: different snapshots, and so different labels.
    assert reports["loud"].snapshot_set_hash != reports["quiet"].snapshot_set_hash
    assert reports["loud"].label_set_hash != reports["quiet"].label_set_hash
    # The same visible past and a different future: the same snapshots, different labels.
    assert reports["later"].snapshot_set_hash == reports["quiet"].snapshot_set_hash
    assert reports["later"].label_set_hash != reports["quiet"].label_set_hash
    assert reports["later"].report_id != reports["quiet"].report_id


# --- the store ------------------------------------------------------------------------


def test_the_accountability_store_is_append_only_and_idempotent(
    tmp_path: Path, fixture_backtest: BacktestResult
) -> None:
    store = AccountabilityStore(tmp_path)
    assert store.snapshots() == () and store.labels() == () and store.latest_report() is None
    assert store.add_snapshots(fixture_backtest.snapshots) == 1365
    assert store.add_labels(fixture_backtest.labels) == 1365
    assert store.add_report(fixture_backtest.report) is True
    assert [path.name for path in tmp_path.iterdir()] == [STORE_GENERATION]
    tmp_path = tmp_path / STORE_GENERATION
    files = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file())
    before = {path: (tmp_path / path).read_bytes() for path in files}
    assert store.add_snapshots(fixture_backtest.snapshots) == 0
    assert store.add_labels(fixture_backtest.labels) == 0
    assert store.add_report(fixture_backtest.report) is False
    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file())
    assert after == files
    assert all((tmp_path / path).read_bytes() == body for path, body in before.items())
    assert {path.parts[0] for path in files} == {"snapshots", "labels", "reports"}
    snapshot_days = sorted(path.parts[1] for path in files if path.parts[0] == "snapshots")
    assert len(snapshot_days) == 91 and snapshot_days[0] == "2026-07-07"  # by prediction day

    reopened = AccountabilityStore(tmp_path.parent)
    assert {s.snapshot_id for s in reopened.snapshots()} == {
        s.snapshot_id for s in fixture_backtest.snapshots
    }
    assert reopened.latest_report() == fixture_backtest.report
    day_one = reopened.snapshots(date(2026, 9, 1))
    assert len(day_one) == 15 and [s.rank for s in day_one[:6]] == [1, 2, 3, 4, 5, 6]
    assert {s.latest_due_day for s in day_one} == {date(2026, 8, 30)}
    first = fixture_backtest.snapshots[0]
    assert reopened.label_for(first.snapshot_id) == fixture_backtest.labels[0]
    assert reopened.label_for("snap_" + "f" * 32) is None


def test_records_of_the_superseded_simulation_are_kept_and_never_read(
    tmp_path: Path, fixture_backtest: BacktestResult
) -> None:
    """The first generation's records let an unpublished day in. They stay; they are not used."""
    old = {
        "snapshots/2026-10-03/aa.ndjson": '{"snapshot_id":"snap_old","data_cutoff_day":"x"}\n',
        "labels/2026-10-03/bb.ndjson": '{"label_id":"label_old","snapshot_id":"snap_old"}\n',
        "reports/report_old.json": '{"format":"validation_report_v1","first_snapshot_day":"x"}\n',
    }
    for name, body in old.items():
        (tmp_path / name).parent.mkdir(parents=True)
        (tmp_path / name).write_text(body)
    store = AccountabilityStore(tmp_path)
    assert store.snapshots() == () and store.labels() == () and store.latest_report() is None
    store.add_snapshots(fixture_backtest.snapshots)
    store.add_labels(fixture_backtest.labels)
    store.add_report(fixture_backtest.report)
    assert store.latest_report() == fixture_backtest.report
    assert store.audit(fixture_backtest.report) == ()
    assert all((tmp_path / name).read_text() == body for name, body in old.items())


def test_the_store_can_audit_a_report_against_the_records_it_holds(
    tmp_path: Path, fixture_backtest: BacktestResult
) -> None:
    report = fixture_backtest.report
    complete = AccountabilityStore(tmp_path / "complete")
    complete.add_snapshots(fixture_backtest.snapshots)
    complete.add_labels(fixture_backtest.labels)
    assert complete.audit(report) == ()
    # A snapshot missing from the store.
    partial = AccountabilityStore(tmp_path / "partial")
    partial.add_snapshots(fixture_backtest.snapshots[:-1])
    partial.add_labels(fixture_backtest.labels)
    problems = partial.audit(report)
    assert len(problems) == 2 and "snapshot_set_hash" in problems[0]
    # Every snapshot, one label missing.
    unlabelled = AccountabilityStore(tmp_path / "unlabelled")
    unlabelled.add_snapshots(fixture_backtest.snapshots)
    unlabelled.add_labels(fixture_backtest.labels[:-1])
    assert len(unlabelled.audit(report)) == 1 and "label_set_hash" in unlabelled.audit(report)[0]
    # An empty store holds nothing the report describes.
    assert len(AccountabilityStore(tmp_path / "empty").audit(report)) == 2
    # The store may hold more than one run. A report is audited against its own period:
    # a shorter run's report matches the larger store, and the longer report does not
    # match a store that holds only the shorter run.
    catalogue, recording_, lags = real_recording()
    shorter = run_backtest(
        catalogue, recording_, FIRST_PREDICTION, date(2026, 9, 1), RUN_TIME, lags
    )
    shorter_store = AccountabilityStore(tmp_path / "shorter")
    shorter_store.add_snapshots(shorter.snapshots)
    shorter_store.add_labels(shorter.labels)
    assert shorter_store.audit(shorter.report) == ()
    assert len(shorter_store.audit(report)) == 2


def test_a_later_label_is_kept_beside_the_earlier_one(tmp_path: Path) -> None:
    catalogue, short = recording({"alpha": [*steady(60), 120.0]})
    _, longer = recording({"alpha": [*steady(60), 120.0, 130.0, 140.0]})
    snapshot = predict(catalogue, sees(short, 59), WHEN)[0]
    same = predict(catalogue, sees(longer, 59), WHEN)[0]
    assert same.snapshot_id == snapshot.snapshot_id
    early = label(snapshot, RecordedHistory(short, ZERO).future())
    late = label(snapshot, RecordedHistory(longer, ZERO).future())
    assert early.availability is OutcomeAvailability.UNAVAILABLE
    assert late.realized_uplift_72h == pytest.approx(0.30)
    store = AccountabilityStore(tmp_path)
    assert store.add_snapshots([snapshot, same]) == 1
    assert store.add_labels([early]) == 1 and store.add_labels([late, early]) == 1
    assert store.labels() == (early, late)  # the earlier "unavailable" is still there
    assert store.label_for(snapshot.snapshot_id) == late


# --- the artifact ---------------------------------------------------------------------


def publish(root: Path, report: ValidationReport | None) -> Path:
    catalogue, recording_, _ = real_recording()
    with replay_client(RECORDINGS) as client:
        registry = default_registry(client)
        build_and_publish(catalogue, recording_, registry, root, RUN_TIME, validation=report)
    return root


def test_validation_is_published_through_the_artifact_contract(
    tmp_path: Path, fixture_backtest: BacktestResult
) -> None:
    root = publish(tmp_path, fixture_backtest.report)
    manifest = validate_published(root)
    assert manifest.schema_version == SCHEMA_VERSION == "1.5.0"
    assert manifest.artifacts.validation == f"{manifest.version_path}/validation.json"
    document = json.loads((root / "validation.json").read_text())
    schema = json.loads((REPO / "schemas" / "validation.schema.json").read_text())
    Draft202012Validator(schema).validate(document)
    artifact = ValidationArtifact.model_validate(document)
    assert artifact.availability == "available" and artifact.report is not None
    published = artifact.report
    report = fixture_backtest.report
    assert published.report_id == report.report_id
    assert published.evidence.status == "framework_only"
    assert published.primary_evaluable == 396 and published.overall.mean == report.overall.mean
    # When each recommendation was simulated, and what it could see at that moment.
    assert (published.first_prediction_day, published.last_prediction_day) == (
        FIRST_PREDICTION,
        LAST_PREDICTION,
    )
    assert (published.first_latest_due_day, published.last_latest_due_day) == (
        date(2026, 7, 5),
        RECORDED_END,
    )
    assert published.publication_lag_hours == {SOURCE: 24.0}
    assert published.snapshot_set_hash == report.snapshot_set_hash
    assert published.label_set_hash == report.label_set_hash
    assert "first_snapshot_day" not in document["report"]
    for shown in published.inspection:
        assert shown.as_of == prediction_moment(shown.day)
        assert shown.latest_due_day == shown.day - 2 * DAY
        assert shown.primary_window_start == shown.day
        assert shown.primary_window_end == shown.day + 2 * DAY
    assert [s.strategy for s in published.strategies] == list(STRATEGIES)
    # Published exactly as computed: nothing is rounded at the boundary.
    assert published.strategies[0].association.mean_rho == report.strategies[0].association.mean_rho
    assert artifact.outcome_definition.version == "outcome_rules_v1"
    assert [term.name for term in artifact.outcome_definition.terms] == [
        "realized_uplift_72h (primary)",
        "movement_24h",
        "persistence_7d",
        "lead_time_days",
    ]
    formulas = [term.formula for term in artifact.outcome_definition.terms]
    assert formulas[0] == "mean(x[f] .. x[f+2]) / baseline_median_28d[P] - 1"
    assert formulas[1] == "x[f] / avg_3d[P] - 1"
    assert formulas[2] == "count(x[f+k] > baseline_median_28d[P] for k in 0..6) / 7"
    assert formulas[3].startswith("1 + smallest k in 0..5 with x[f+k] > x[f+k-1]")
    assert "first whole UTC day" in artifact.outcome_definition.terms[0].rationale
    assert not any("[t" in formula for formula in formulas)  # no day-of-data notation left
    assert "not evidence about the final" in artifact.scope_note
    # Score v0 in the same bundle is untouched.
    for slug, expected in GOLDEN["games"].items():
        game_document = json.loads((root / "games" / f"{slug}.json").read_text())
        assert all(game_document[field] == value for field, value in expected.items()), slug


def test_no_backtest_is_published_as_not_run_and_never_as_an_empty_result(tmp_path: Path) -> None:
    root = publish(tmp_path, None)
    validate_published(root)
    document = json.loads((root / "validation.json").read_text())
    assert document["availability"] == "not_run" and document["report"] is None
    assert document["outcome_definition"]["version"] == "outcome_rules_v1"
    with pytest.raises(ValidationError, match="exactly when validation is available"):
        ValidationArtifact.model_validate({**document, "availability": "available"})


def test_validation_refuses_a_report_that_claims_more_than_it_shows(
    tmp_path: Path, fixture_backtest: BacktestResult
) -> None:
    root = publish(tmp_path, fixture_backtest.report)
    version = root / json.loads((root / "manifest.json").read_text())["version_path"]
    original = (version / "validation.json").read_text()

    def tamper(change: Any) -> None:
        for directory in (version, root):
            document = json.loads(original)
            change(document["report"])
            (directory / "validation.json").write_text(json.dumps(document, indent=2) + "\n")

    def promote(report: dict[str, Any]) -> None:
        report["evidence"]["status"] = "validated"

    def mark_reached(report: dict[str, Any]) -> None:
        report["evidence"]["checks"][0]["reached"] = True
        report["evidence"]["status"] = "insufficient_sample"

    def miscount(report: dict[str, Any]) -> None:
        report["ranked"] += 1

    def fail_check(report: dict[str, Any]) -> None:
        report["leakage_checks"][2]["passed"] = False

    def drop_v0(report: dict[str, Any]) -> None:
        report["strategies"] = report["strategies"][1:]

    def drop_lag_check(report: dict[str, Any]) -> None:
        report["leakage_checks"] = report["leakage_checks"][1:]

    def see_the_unpublished(report: dict[str, Any]) -> None:
        shown = report["inspection"][0]
        shown["latest_due_day"] = (date.fromisoformat(shown["day"]) - DAY).isoformat()

    def judge_from_the_past(report: dict[str, Any]) -> None:
        shown = report["inspection"][0]
        shown["primary_window_start"] = shown["latest_due_day"]

    def no_lag_stated(report: dict[str, Any]) -> None:
        report["publication_lag_hours"] = {}

    def blank_hash(report: dict[str, Any]) -> None:
        report["snapshot_set_hash"] = "unknown"

    for change, message in (
        (promote, "not what its checks support"),
        (mark_reached, "reached with an unmet requirement"),
        (miscount, "counts do not add up"),
        (fail_check, "leakage check did not pass"),
        (drop_v0, "not among the strategies"),
        (drop_lag_check, "publication lag was not checked"),
        (see_the_unpublished, "had not been published at the recommendation"),
        (judge_from_the_past, "does not start after the recommendation"),
        (no_lag_stated, "no source publication lag is stated"),
        (blank_hash, "not a SHA-256 digest"),
    ):
        tamper(change)
        with pytest.raises(ArtifactValidationError, match=message):
            validate_published(root)
    for directory in (version, root):
        (directory / "validation.json").write_text(original)
    validate_published(root)


# --- the command ----------------------------------------------------------------------


def test_the_backtest_command_runs_offline_and_writes_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_network(*_: object, **__: object) -> None:
        raise AssertionError("the backtest tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(cli, "build_client", no_network)
    arguments = [
        "backtest",
        "--replay",
        str(RECORDINGS),
        "--recorded-through",
        "2026-10-03",
        "--from",
        "2026-08-01",
        "--to",
        "2026-09-30",
        "--store-dir",
        str(tmp_path / "validation"),
        "--events-dir",
        str(tmp_path / "no-events"),
    ]
    assert cli.main(arguments) == 0
    output = capsys.readouterr().out
    assert (
        "Backtest of recommendations made at 00:00 UTC, 2026-08-01 to 2026-09-30 (61 days)"
        in output
    )
    assert "publication lag: wikimedia_pageviews 24 h" in output
    assert "newest day visible 2026-07-30 to 2026-09-28; data through 2026-10-03" in output
    assert "Provenance: snapshots " in output and "(store matches)" in output
    assert "EVIDENCE STATUS: framework_only" in output
    assert "Leakage checks passed: 5 of 5" in output
    assert "Real event backtesting is unavailable" in output
    assert "Event sources used by the backtest: none" in output
    assert "+915 snapshots, +915 labels" in output
    for strategy in STRATEGIES:
        assert strategy in output
    store = AccountabilityStore(tmp_path / "validation")
    report = store.latest_report()
    assert report is not None and report.snapshot_dates == 61
    assert len(store.snapshots()) == 61 * 15
    assert store.audit(report) == ()
    # Every day of this range has its three forward days inside the recording.
    assert report.primary_unavailable.get("future_incomplete", 0) == 0
    # The range is checked against what the recording could have shown at each moment.
    for first, last in (("2026-07-06", "2026-09-30"), ("2026-08-01", "2026-10-06")):
        outside = [*arguments]
        outside[outside.index("--from") + 1] = first
        outside[outside.index("--to") + 1] = last
        assert cli.main(outside) != 0
        assert "cannot be reconstructed" in capsys.readouterr().err
    # A second run adds nothing.
    assert cli.main(arguments) == 0
    assert "+0 snapshots, +0 labels" in capsys.readouterr().out
    assert len(store.reports()) == 1


def test_the_backtest_command_has_no_live_mode_and_needs_an_explicit_range(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for missing in ("--replay", "--recorded-through", "--from", "--to"):
        arguments = [
            "backtest",
            "--replay",
            str(RECORDINGS),
            "--recorded-through",
            "2026-10-03",
            "--from",
            "2026-08-01",
            "--to",
            "2026-09-30",
        ]
        index = arguments.index(missing)
        with pytest.raises(SystemExit):
            cli.main(arguments[:index] + arguments[index + 2 :])
        assert missing in capsys.readouterr().err
    source = (REPO / "src" / "metaspike" / "cli.py").read_text()
    handler = source[source.index("def _backtest(") : source.index("def _signed(")]
    assert "replay_client(args.replay)" in handler and "build_client" not in handler
    # An ordinary run never performs a backtest; it only publishes one that exists.
    run_handler = source[source.index("def _run(") : source.index("def _validate_artifacts(")]
    assert "run_backtest" not in run_handler and "latest_report()" in run_handler
