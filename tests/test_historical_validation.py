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
from datetime import UTC, date, datetime, timedelta
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
from metaspike.domain.timeseries import Granularity, TimeWindow, start_of_day
from metaspike.events.model import CoverageCheck, EventReport, TimePrecision, observe
from metaspike.events.rules import EVENT_HEAT_RULES_V1
from metaspike.events.store import EventStore
from metaspike.pipeline.assess import assess
from metaspike.pipeline.collection import CollectionReport, SourceRun, collect, window_ending
from metaspike.pipeline.normalize import build_time_series
from metaspike.pipeline.run import build_and_publish
from metaspike.publishing.artifacts import SCHEMA_VERSION, ValidationArtifact
from metaspike.publishing.validation import ArtifactValidationError, validate_published
from metaspike.scoring.v0 import V0_INPUT_SIGNAL
from metaspike.sources.registry import default_registry
from metaspike.sources.replay import replay_client
from metaspike.validation import snapshot as snapshot_module
from metaspike.validation.backtest import BacktestResult, perturb_after, run_backtest
from metaspike.validation.history import (
    FutureValues,
    LeakageError,
    PredictionInputs,
    RecordedHistory,
    as_of_for,
    ensure_no_future,
)
from metaspike.validation.metrics import STRATEGIES, evaluate_strategy, order, spearman, summarise
from metaspike.validation.outcomes import (
    OutcomeAvailability,
    OutcomeLabel,
    OutcomeUnavailable,
    PeakStatus,
    label,
)
from metaspike.validation.report import ValidationReport, assess_evidence
from metaspike.validation.rules import (
    OUTCOME_RULES_V1,
    VALIDATION_PROTOCOL_V1,
    EvidenceStatus,
    OutcomeRules,
)
from metaspike.validation.snapshot import RecommendationSnapshot, predict
from metaspike.validation.store import AccountabilityStore
from tests.conftest import RECORDED_END, RECORDINGS, RUN_TIME, make_id, steady

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


CUTOFF = 59  # the index of the snapshot day in the scenarios below


def scenario(future: Sequence[float | None]) -> tuple[RecommendationSnapshot, OutcomeLabel]:
    """Sixty quiet days (baseline 100, latest 110, 3-day average 103.33), then `future`."""
    catalogue, report = recording({"alpha": [*steady(CUTOFF + 1), *future]})
    history = RecordedHistory(report)
    (snapshot,) = predict(catalogue, history.inputs_at(day(CUTOFF)), WHEN)
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


@pytest.fixture(scope="module")
def fixture_backtest() -> BacktestResult:
    catalogue, report, lags = real_recording()
    history = RecordedHistory(report, lags)
    return run_backtest(
        catalogue, report, history.first_day, history.last_day, RUN_TIME, publication_lag=lags
    )


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


def test_inputs_rebuilt_for_a_day_hold_nothing_after_it() -> None:
    catalogue, report = recording({"alpha": steady(80), "beta": steady(80)})
    history = RecordedHistory(report)
    inputs = history.inputs_at(day(50))
    assert inputs.cutoff_day == day(50) and inputs.as_of == as_of_for(day(50))
    assert inputs.as_of == start_of_day(day(51))
    assert inputs.report.window.end == start_of_day(day(50))
    assert inputs.report.observed_at == inputs.as_of
    for run in inputs.report.runs:
        for signal in run.outcomes:
            assert signal.series is not None
            observed = [p.period_day for p in signal.series.points if p.value is not None]
            assert max(observed) == day(50) and len(observed) == 51
    assert len(catalogue.games) == 2
    with pytest.raises(ValueError, match="outside the recording"):
        history.inputs_at(day(80))


def test_inputs_that_reach_past_their_cutoff_are_refused() -> None:
    catalogue, report = recording({"alpha": steady(80)})
    leaky = PredictionInputs(cutoff_day=day(50), as_of=as_of_for(day(50)), report=report)
    with pytest.raises(LeakageError, match="runs past the cutoff"):
        ensure_no_future(leaky)
    with pytest.raises(LeakageError):
        predict(catalogue, leaky, WHEN)
    # A window that ends on the cutoff but still carries a later point is caught too.
    honest = RecordedHistory(report).inputs_at(day(50))
    later = RecordedHistory(report).inputs_at(day(51))
    smuggled = PredictionInputs(
        cutoff_day=day(50),
        as_of=as_of_for(day(50)),
        report=later.report.model_copy(update={"window": honest.report.window}),
    )
    with pytest.raises(LeakageError):
        ensure_no_future(smuggled)
    late = PredictionInputs(
        cutoff_day=day(50),
        as_of=as_of_for(day(50)),
        report=honest.report.model_copy(update={"observed_at": as_of_for(day(52))}),
    )
    with pytest.raises(LeakageError, match="observed after the snapshot"):
        ensure_no_future(late)


def test_a_computation_that_looked_ahead_would_give_a_different_answer() -> None:
    """The control for every equality test below: here, looking ahead visibly matters.

    Features computed on the whole recording and read off for a past day are not the
    features that day could have had. If the backtest did that, the assertions in the
    next tests would fail, because they compare against exactly this.
    """
    values = [*steady(60), 400.0, 900.0, 1500.0, 2000.0, 2600.0, 3000.0, 3200.0, 3300.0]
    catalogue, report = recording({"alpha": values})
    history = RecordedHistory(report)
    (honest,) = predict(catalogue, history.inputs_at(day(59)), WHEN)
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
        history = RecordedHistory(report)
        predictions.append(predict(catalogue, history.inputs_at(day(59)), WHEN))
    first = predictions[0]
    for other in predictions[1:]:
        assert [item.snapshot_id for item in other] == [item.snapshot_id for item in first]
        assert other == first
    # The same through the checker the backtest itself runs.
    catalogue, report = recording({"alpha": quiet, "beta": loud})
    altered = perturb_after(report, day(59), 1000.0)
    assert altered != report
    before = predict(catalogue, RecordedHistory(report).inputs_at(day(59)), WHEN)
    after = predict(catalogue, RecordedHistory(altered).inputs_at(day(59)), WHEN)
    assert before == after
    # While a day inside the perturbed range does change, so the check is not vacuous.
    assert predict(catalogue, RecordedHistory(report).inputs_at(day(70)), WHEN) != predict(
        catalogue, RecordedHistory(altered).inputs_at(day(70)), WHEN
    )


def test_a_snapshot_rebuilt_later_is_identical_and_its_id_ignores_when_it_was_made() -> None:
    catalogue, report = recording({"alpha": steady(80), "beta": [*steady(70), *([300.0] * 10)]})
    history = RecordedHistory(report)
    first = predict(catalogue, history.inputs_at(day(72)), WHEN)
    later = predict(catalogue, RecordedHistory(report).inputs_at(day(72)), WHEN + 400 * DAY)
    assert [a.snapshot_id for a in first] == [b.snapshot_id for b in later]
    assert all(a.created_at != b.created_at for a, b in zip(first, later, strict=True))
    assert all(
        a.model_dump(exclude={"created_at"}) == b.model_dump(exclude={"created_at"})
        for a, b in zip(first, later, strict=True)
    )
    assert all(item.snapshot_id.startswith("snap_") for item in first)
    other_day = predict(catalogue, history.inputs_at(day(73)), WHEN)
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
        name: run_backtest(catalogue, report, day(36), day(80), WHEN, event_store=store)
        for name, store in (("early", early), ("revised", revised))
    }

    def alpha_on(name: str, index: int) -> RecommendationSnapshot:
        return next(
            item
            for item in runs[name].snapshots
            if item.slug == "alpha" and item.data_cutoff_day == day(index)
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
    plain = run_backtest(catalogue, report, day(36), day(80), WHEN)
    eventful = run_backtest(catalogue, report, day(36), day(80), WHEN, event_store=store)
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
            RecordedHistory(report).inputs_at(day(50)),
            WHEN,
            event_records=store.events(),
        )


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
    history = RecordedHistory(report)
    snapshots = predict(catalogue, history.inputs_at(day(59)), WHEN)
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
    result = run_backtest(catalogue, report, day(36), day(79), WHEN)

    def on(index: int) -> dict[str, RecommendationSnapshot]:
        return {s.slug: s for s in result.snapshots if s.data_cutoff_day == day(index)}

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
    snapshots = predict(catalogue, RecordedHistory(report).inputs_at(day(62)), WHEN)
    by_slug = {item.slug: item for item in snapshots}
    assert (
        by_slug["alpha"].score.value == by_slug["mike"].score.value == by_slug["zulu"].score.value
    )
    assert [by_slug[slug].rank for slug in ("alpha", "mike", "zulu")] == [1, 2, 3]
    for strategy in STRATEGIES:
        assert [item.slug for item in order(strategy, list(snapshots))] == ["alpha", "mike", "zulu"]
    again = predict(catalogue, RecordedHistory(report).inputs_at(day(62)), WHEN)
    assert [item.rank for item in again] == [item.rank for item in snapshots]


def test_comparators_order_the_same_universe_by_values_the_snapshot_already_held() -> None:
    big_flat = [value * 100 for value in steady(70) if value is not None]
    lifting = [*steady(64), 100.0, 100.0, 100.0, 180.0, 180.0, 180.0]
    accelerating = [*steady(64), 60.0, 60.0, 60.0, 150.0, 150.0, 150.0]
    catalogue, report = recording(
        {"big": big_flat, "lifting": lifting, "accelerating": accelerating, "absent": [None] * 70}
    )
    snapshots = predict(catalogue, RecordedHistory(report).inputs_at(day(69)), WHEN)
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
        by_day.setdefault(pair[0].data_cutoff_day, []).append(pair)
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


def test_the_backtest_reproduces_score_v0_as_it_was_published(
    fixture_backtest: BacktestResult,
) -> None:
    last = [s for s in fixture_backtest.snapshots if s.data_cutoff_day == RECORDED_END]
    assert len(last) == 15 == len(GOLDEN["games"])
    for snapshot in last:
        published = GOLDEN["games"][snapshot.slug]
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


def test_the_fixture_backtest_reports_exactly_what_the_recording_shows(
    fixture_backtest: BacktestResult,
) -> None:
    report = fixture_backtest.report
    assert (report.first_snapshot_day, report.last_snapshot_day) == (
        date(2026, 7, 6),
        RECORDED_END,
    )
    assert report.snapshot_dates == 90 and report.data_through == RECORDED_END
    assert (report.universe_size, report.games_ranked, report.games_with_outcome) == (15, 6, 6)
    assert (report.recommendations, report.ranked, report.unranked) == (1350, 420, 930)
    assert report.primary_evaluable == 402 == report.overall.count
    assert report.primary_unavailable == {"future_incomplete": 18, "not_scored_at_snapshot": 930}
    # The last three days cannot have a 72-hour outcome: six ranked games each.
    assert report.primary_unavailable["future_incomplete"] == 3 * 6
    assert report.independent_sources == 1
    # Six games is nowhere near enough: the machinery is shown to work, nothing more.
    assert report.evidence.status is EvidenceStatus.FRAMEWORK_ONLY
    assert "nothing here should be read as evidence" in report.evidence.conclusion
    assert not any(check.reached for check in report.evidence.checks)
    assert [check.name for check in report.leakage_checks] == [
        "inputs_end_at_cutoff",
        "reproduced_after_labelling",
        "unchanged_when_future_altered",
        "reference_frozen_at_snapshot",
    ]
    assert all(check.passed and check.checked > 0 for check in report.leakage_checks)
    assert report.leakage_checks[1].checked == 1350
    assert report.events.real_event_history is False
    assert report.events.snapshots_with_event_intelligence == 0
    assert report.overall.minimum is not None and report.overall.minimum < 0  # negatives kept
    assert report.overall.downside_rate is not None and report.overall.downside_rate > 0
    assert sum(stratum.outcomes.count for stratum in report.score_bins) == 402
    assert sum(stratum.outcomes.count for stratum in report.confidence_bands) == 402
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
    assert report.inspection[-1].day == RECORDED_END
    top = report.inspection[-1].ranked[0]
    assert (top.rank, top.slug, top.score) == (1, "minecraft", 12.2)
    assert top.outcome_availability == "unavailable" and top.realized_uplift_72h is None
    assert len(report.inspection[-1].unranked) == 9
    scored_day = report.inspection[0]
    assert all(entry.realized_uplift_72h is not None for entry in scored_day.ranked)
    assert any("In-sample only" in line for line in report.limitations)
    assert any("not independent" in line for line in report.limitations)
    assert any("has not been backtested" in line for line in report.limitations)


def test_the_fixture_result_is_reported_as_it_is_including_where_v0_does_not_lead(
    fixture_backtest: BacktestResult,
) -> None:
    by_name = {s.strategy: s for s in fixture_backtest.report.strategies}
    v0, lift = by_name["metaspike_score_v0"], by_name["simple_lift"]
    assert v0.association.mean_rho is not None and lift.association.mean_rho is not None
    # On this recording plain lift orders games at least as well as score v0. The report
    # says so; nothing is arranged to hide it.
    assert lift.association.mean_rho >= v0.association.mean_rho
    assert v0.association.days_used == lift.association.days_used == 67
    assert v0.top_k[0].outcomes.count == 67
    assert by_name["raw_popularity"].association.mean_rho is not None


def test_the_backtest_is_deterministic_and_linear_in_days_and_games(
    fixture_backtest: BacktestResult,
) -> None:
    catalogue, report, lags = real_recording()
    again = run_backtest(
        catalogue, report, date(2026, 7, 6), RECORDED_END, RUN_TIME + DAY, publication_lag=lags
    )
    assert again.report.report_id == fixture_backtest.report.report_id
    assert [s.snapshot_id for s in again.snapshots] == [
        s.snapshot_id for s in fixture_backtest.snapshots
    ]
    assert [item.label_id for item in again.labels] == [
        item.label_id for item in fixture_backtest.labels
    ]
    assert again.report.created_at != fixture_backtest.report.created_at
    assert len(fixture_backtest.snapshots) == 90 * 15 == len(fixture_backtest.labels)
    half = run_backtest(
        catalogue, report, date(2026, 7, 6), date(2026, 8, 19), RUN_TIME, publication_lag=lags
    )
    assert len(half.snapshots) == 45 * 15
    # A shorter run's snapshots are the same snapshots: a day does not depend on the range.
    assert [s.snapshot_id for s in half.snapshots] == [
        s.snapshot_id for s in fixture_backtest.snapshots[: 45 * 15]
    ]
    with pytest.raises(ValueError, match="inside the recording"):
        run_backtest(catalogue, report, date(2026, 7, 1), RECORDED_END, RUN_TIME)
    with pytest.raises(ValueError, match="ends before it starts"):
        run_backtest(catalogue, report, RECORDED_END, date(2026, 7, 6), RUN_TIME)


# --- the store ------------------------------------------------------------------------


def test_the_accountability_store_is_append_only_and_idempotent(
    tmp_path: Path, fixture_backtest: BacktestResult
) -> None:
    store = AccountabilityStore(tmp_path)
    assert store.snapshots() == () and store.labels() == () and store.latest_report() is None
    assert store.add_snapshots(fixture_backtest.snapshots) == 1350
    assert store.add_labels(fixture_backtest.labels) == 1350
    assert store.add_report(fixture_backtest.report) is True
    files = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file())
    before = {path: (tmp_path / path).read_bytes() for path in files}
    assert store.add_snapshots(fixture_backtest.snapshots) == 0
    assert store.add_labels(fixture_backtest.labels) == 0
    assert store.add_report(fixture_backtest.report) is False
    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file())
    assert after == files
    assert all((tmp_path / path).read_bytes() == body for path, body in before.items())
    assert {path.parts[0] for path in files} == {"snapshots", "labels", "reports"}
    assert len([path for path in files if path.parts[0] == "snapshots"]) == 90

    reopened = AccountabilityStore(tmp_path)
    assert {s.snapshot_id for s in reopened.snapshots()} == {
        s.snapshot_id for s in fixture_backtest.snapshots
    }
    assert reopened.latest_report() == fixture_backtest.report
    day_one = reopened.snapshots(date(2026, 9, 1))
    assert len(day_one) == 15 and [s.rank for s in day_one[:6]] == [1, 2, 3, 4, 5, 6]
    first = fixture_backtest.snapshots[0]
    assert reopened.label_for(first.snapshot_id) == fixture_backtest.labels[0]
    assert reopened.label_for("snap_" + "f" * 32) is None


def test_a_later_label_is_kept_beside_the_earlier_one(tmp_path: Path) -> None:
    catalogue, short = recording({"alpha": [*steady(60), 120.0]})
    _, longer = recording({"alpha": [*steady(60), 120.0, 130.0, 140.0]})
    snapshot = predict(catalogue, RecordedHistory(short).inputs_at(day(59)), WHEN)[0]
    same = predict(catalogue, RecordedHistory(longer).inputs_at(day(59)), WHEN)[0]
    assert same.snapshot_id == snapshot.snapshot_id
    early = label(snapshot, RecordedHistory(short).future())
    late = label(snapshot, RecordedHistory(longer).future())
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
    assert manifest.schema_version == SCHEMA_VERSION == "1.2.0"
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
    assert published.primary_evaluable == 402 and published.overall.mean == report.overall.mean
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
    assert "baseline_median_28d[t]" in artifact.outcome_definition.terms[0].formula
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

    for change, message in (
        (promote, "not what its checks support"),
        (mark_reached, "reached with an unmet requirement"),
        (miscount, "counts do not add up"),
        (fail_check, "leakage check did not pass"),
        (drop_v0, "not among the strategies"),
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
    assert "Backtest 2026-08-01 to 2026-09-30 (61 days, data through 2026-10-03)" in output
    assert "EVIDENCE STATUS: framework_only" in output
    assert "Leakage checks passed: 4 of 4" in output
    assert "Real event backtesting is unavailable" in output
    assert "+915 snapshots, +915 labels" in output
    for strategy in STRATEGIES:
        assert strategy in output
    store = AccountabilityStore(tmp_path / "validation")
    report = store.latest_report()
    assert report is not None and report.snapshot_dates == 61
    assert len(store.snapshots()) == 61 * 15
    # Every day of this range has its three forward days inside the recording.
    assert report.primary_unavailable.get("future_incomplete", 0) == 0
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
