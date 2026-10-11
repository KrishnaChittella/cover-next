"""Event intelligence in the published artifacts, and proof that score v0 is untouched.

The pipeline is run offline against the recorded responses. Any event here is synthetic
and exists only in this file: the real replay has no event coverage at all.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from metaspike.catalogue.seed import load_seed_catalogue
from metaspike.domain.events import EventType
from metaspike.domain.games import Catalogue
from metaspike.events.model import CoverageCheck, EventReport, EventStatus, TimePrecision, observe
from metaspike.events.reconcile import reconcile
from metaspike.events.rules import EVENT_HEAT_RULES_V1
from metaspike.events.store import EventStore
from metaspike.pipeline.collection import CollectionReport, collect, window_ending
from metaspike.pipeline.run import RunResult, build_and_publish
from metaspike.publishing.artifacts import (
    SCHEMA_VERSION,
    EventIntelligenceView,
    GameArtifact,
    RadarArtifact,
)
from metaspike.publishing.build import EventInputs
from metaspike.publishing.event_views import event_as_of
from metaspike.publishing.schemas import ARTIFACT_MODELS
from metaspike.publishing.validation import ArtifactValidationError, validate_published
from metaspike.sources.registry import default_registry
from metaspike.sources.replay import replay_client
from tests.conftest import RECORDED_END, RECORDINGS, RUN_TIME, run_fixture_pipeline

REPO = Path(__file__).parent.parent
SCHEMAS = REPO / "schemas"
GOLDEN = json.loads((REPO / "tests" / "fixtures" / "score_v0_replay_golden.json").read_text())
AS_OF = datetime(2026, 10, 4, 0, 0, tzinfo=UTC)  # the end of the replay's last day
DAY = timedelta(days=1)
SCORE_FIELDS = (
    "rank",
    "score",
    "confidence",
    "features",
    "explanation",
    "history",
    "source_coverage",
)


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def run_with(artifacts_dir: Path, events: EventInputs | None) -> RunResult:
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
        return build_and_publish(catalogue, report, registry, artifacts_dir, RUN_TIME, events)


def report_for(
    catalogue: Catalogue,
    slug: str,
    kind: EventType,
    title: str,
    starts: datetime,
    ends: datetime | None = None,
    status: EventStatus = EventStatus.SCHEDULED,
    precision: TimePrecision = TimePrecision.DAY,
) -> EventReport:
    game = next(game for game in catalogue.games if game.slug == slug)
    return EventReport(
        game=game.source_identifiers[0],
        event_type=kind,
        title=title,
        starts_at=starts,
        ends_at=ends,
        time_precision=precision,
        status=status,
    )


def synthetic_events() -> EventInputs:
    """A handful of invented events around the replay's as-of day, and who was checked."""
    catalogue = load_seed_catalogue()
    observed = AS_OF - 3 * DAY
    reports = [
        report_for(
            catalogue, "minecraft", EventType.MAJOR_PATCH, "Synthetic Update", AS_OF - 2 * DAY
        ),
        report_for(catalogue, "minecraft", EventType.SHOWCASE, "Synthetic Live", AS_OF + 4 * DAY),
        report_for(
            catalogue, "minecraft", EventType.TRAILER, "Synthetic Trailer", AS_OF + 20 * DAY
        ),
        report_for(catalogue, "gta-vi", EventType.RELEASE, "Synthetic Launch", AS_OF + 12 * DAY),
        report_for(
            catalogue,
            "no-mans-sky",
            EventType.EXPEDITION,
            "Synthetic Expedition",
            AS_OF - 5 * DAY,
            AS_OF + 30 * DAY,
        ),
        report_for(
            catalogue,
            "star-citizen",
            EventType.FREE_WEEKEND,
            "Synthetic Free Fly",
            AS_OF + DAY,
            status=EventStatus.CANCELLED,
        ),
    ]
    records = reconcile([observe(report, catalogue, observed) for report in reports])
    by_slug = {game.slug: game.canonical_game_id for game in catalogue.games}
    checks = tuple(
        CoverageCheck(
            canonical_game_id=by_slug[slug], source="synthetic_events", checked_at=AS_OF - DAY
        )
        for slug in ("minecraft", "gta-vi", "no-mans-sky", "star-citizen", "gta-v")
    )
    return EventInputs(records=records, checks=checks)


def games_of(artifacts_dir: Path) -> dict[str, dict[str, Any]]:
    return {path.stem: load(path) for path in sorted((artifacts_dir / "games").glob("*.json"))}


# --- score v0 is unchanged ------------------------------------------------------------


def test_score_v0_is_identical_to_what_was_published_before_event_intelligence(
    artifacts_dir: Path,
) -> None:
    run_fixture_pipeline(artifacts_dir)
    games = games_of(artifacts_dir)
    assert GOLDEN["score_version"] == "metaspike_score_v0"
    assert set(games) == set(GOLDEN["games"]) and len(games) == 15
    for slug, expected in GOLDEN["games"].items():
        assert set(expected) == set(SCORE_FIELDS)
        for field in SCORE_FIELDS:
            assert games[slug][field] == expected[field], (slug, field)
        assert games[slug]["score"]["score_version"] == "metaspike_score_v0"


def test_events_change_no_score_rank_confidence_feature_or_reason(tmp_path: Path) -> None:
    without, with_events = tmp_path / "without", tmp_path / "with"
    run_with(without, None)
    run_with(with_events, synthetic_events())
    plain, eventful = games_of(without), games_of(with_events)
    assert any(game["event_intelligence"]["event_heat"] for game in eventful.values())
    for slug, expected in GOLDEN["games"].items():
        for field in SCORE_FIELDS:
            assert plain[slug][field] == eventful[slug][field] == expected[field], (slug, field)

    # The leaderboard is byte-for-byte the same list, in the same order.
    def board(root: Path) -> dict[str, Any]:
        return {k: v for k, v in load(root / "current.json").items() if k != "run_id"}

    assert board(without) == board(with_events)
    assert "event" not in json.dumps(load(with_events / "current.json")).lower()
    # Only the event fields, and so the content hash and run id, differ.
    assert (
        load(without / "manifest.json")["content_hash"]
        != load(with_events / "manifest.json")["content_hash"]
    )


def test_the_score_engine_does_not_import_the_event_engine() -> None:
    package = REPO / "src" / "metaspike"
    for area in ("scoring", "features", "pipeline/assess.py"):
        target = package / area
        files = [target] if target.is_file() else sorted(target.rglob("*.py"))
        assert files
        for path in files:
            assert "metaspike.events" not in path.read_text(encoding="utf-8"), path
    # And the event engine takes nothing from scoring.
    for path in sorted((package / "events").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "metaspike.scoring" not in text and "metaspike.features" not in text, path


# --- the real replay: nothing is known about events -----------------------------------


def test_the_real_replay_publishes_unavailable_not_zero(artifacts_dir: Path) -> None:
    run_fixture_pipeline(artifacts_dir)
    for slug, game in games_of(artifacts_dir).items():
        events = game["event_intelligence"]
        assert events["availability"] == "unavailable", slug
        assert events["event_heat"] is None  # null in the file, not 0
        assert events["unavailable_reasons"] == ["no_event_coverage"]
        assert events["contributions"] == [] and events["top_event_id"] is None
        assert events["coverage_checked_at"] is None and events["coverage_sources"] == []
        assert events["rules_version"] == "event_heat_rules_v1"
        assert events["rules_status"] == "internal_uncalibrated"
        assert events["as_of"] == "2026-10-04T00:00:00Z"
        assert game["events"] == []
    radar = load(artifacts_dir / "radar.json")
    assert radar["availability"] == "unavailable" and radar["items"] == []
    assert radar["unavailable_reasons"] == ["no_event_coverage"]
    assert (radar["games_with_coverage"], radar["games_without_coverage"]) == (0, 15)
    assert "not the same as nothing upcoming" in radar["summary"]
    raw = (artifacts_dir / "games" / "minecraft.json").read_text()
    assert '"event_heat": null' in raw and '"event_heat": 0' not in raw


def test_an_absent_or_empty_event_store_means_unavailable(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "no-such-directory")
    assert store.events(AS_OF) == () and store.checks(AS_OF) == ()
    result = run_with(
        tmp_path / "artifacts", EventInputs(records=store.events(AS_OF), checks=store.checks(AS_OF))
    )
    assert result.bundle.radar.availability == "unavailable"
    assert all(game.event_intelligence.event_heat is None for game in result.bundle.games.values())


def test_events_are_evaluated_as_of_the_runs_last_day_not_the_wall_clock() -> None:
    window = window_ending(RECORDED_END)
    much_later = CollectionReport(
        window=window, observed_at=datetime(2027, 3, 1, tzinfo=UTC), runs=()
    )
    same_day = CollectionReport(
        window=window, observed_at=datetime(2026, 10, 3, 9, tzinfo=UTC), runs=()
    )
    assert event_as_of(much_later) == AS_OF  # a replay months later still evaluates the same day
    assert event_as_of(same_day) == datetime(2026, 10, 3, 9, tzinfo=UTC)


# --- synthetic events: the available state --------------------------------------------


def test_synthetic_events_publish_heat_contributions_and_a_radar(tmp_path: Path) -> None:
    result = run_with(tmp_path, synthetic_events())
    assert validate_published(tmp_path).run_id == result.bundle.manifest.run_id
    games = games_of(tmp_path)

    minecraft = games["minecraft"]["event_intelligence"]
    assert minecraft["availability"] == "available"
    patch, showcase, trailer = minecraft["contributions"]
    assert (patch["title"], patch["phase"], patch["counted"], patch["rank"]) == (
        "Synthetic Update", "decaying", True, 1,
    )  # fmt: skip
    assert patch["contribution"] == pytest.approx(0.6 * 0.5 ** (2 / 4), abs=1e-4)
    assert patch["end_assumed"] is True and patch["text"].startswith(
        "Major update launched 2 days ago"
    )
    assert (showcase["phase"], showcase["in_ramp"], showcase["rank"]) == ("upcoming", True, 2)
    assert (trailer["phase"], trailer["in_ramp"], trailer["counted"], trailer["contribution"]) == (
        "upcoming", False, False, 0,
    )  # fmt: skip
    expected = 100 * (1 - (1 - patch["contribution"]) * (1 - showcase["contribution"]))
    assert minecraft["event_heat"] == pytest.approx(expected, abs=0.05)
    assert minecraft["top_event_id"] == patch["event_id"]
    assert minecraft["summary"] == patch["text"] == minecraft["reasons"][0]["text"]
    assert minecraft["coverage_sources"] == ["synthetic_events", "wikimedia_pageviews"]
    assert [event["title"] for event in games["minecraft"]["events"]] == [
        "Synthetic Update", "Synthetic Live", "Synthetic Trailer",
    ]  # fmt: skip
    assert games["minecraft"]["events"][0]["ends_on"] is None  # an assumed end is not a stated one

    # Looked, and found nothing relevant: a real zero.
    gta_v = games["gta-v"]["event_intelligence"]
    assert (gta_v["availability"], gta_v["event_heat"]) == ("available", 0.0)
    assert gta_v["contributions"] == [] and gta_v["reasons"][0]["code"] == "no_relevant_event"
    # A cancelled event is not relevant: also zero, also available.
    citizen = games["star-citizen"]["event_intelligence"]
    assert (citizen["availability"], citizen["event_heat"], citizen["contributions"]) == (
        "available",
        0.0,
        [],
    )
    # Nobody looked: still unavailable, with these events all around it.
    rust = games["rust"]["event_intelligence"]
    assert (rust["availability"], rust["event_heat"]) == ("unavailable", None)
    # An expedition with a stated end, five days in.
    expedition = games["no-mans-sky"]["event_intelligence"]["contributions"][0]
    assert (expedition["phase"], expedition["end_assumed"]) == ("active", False)
    assert games["no-mans-sky"]["events"][0]["ends_on"] == "2026-11-03"

    radar = load(tmp_path / "radar.json")
    assert radar["availability"] == "available" and radar["unavailable_reasons"] == []
    assert (radar["games_with_coverage"], radar["games_without_coverage"]) == (5, 10)
    assert [(item["slug"], item["title"], item["window_hours"]) for item in radar["items"]] == [
        ("minecraft", "Synthetic Live", 168),
        ("gta-vi", "Synthetic Launch", 720),
        ("minecraft", "Synthetic Trailer", 720),
    ]
    launch = radar["items"][1]
    assert launch["hours_until_start"] == 288 and launch["in_ramp"] is True
    assert (
        launch["artifact_path"] == "games/gta-vi.json" and launch["name"] == "Grand Theft Auto VI"
    )
    assert launch["game_event_heat"] == games["gta-vi"]["event_intelligence"]["event_heat"]
    assert "ranks nothing and recommends nothing" in radar["scope_note"]


# --- a positive heat is never published as zero ---------------------------------------


def ramp_edge_events(offset: timedelta) -> EventInputs:
    """One synthetic major patch starting `offset` after the end of its five-day ramp."""
    catalogue = load_seed_catalogue()
    ramp = timedelta(days=EVENT_HEAT_RULES_V1.rule(EventType.MAJOR_PATCH).ramp_days)
    report = report_for(
        catalogue,
        "minecraft",
        EventType.MAJOR_PATCH,
        "Synthetic Edge Update",
        AS_OF + ramp + offset,
        precision=TimePrecision.EXACT,
    )
    return EventInputs(records=reconcile([observe(report, catalogue, AS_OF - DAY)]))


@pytest.mark.parametrize("offset", [timedelta(0), timedelta(seconds=1), timedelta(hours=3)])
def test_at_or_before_the_ramp_boundary_the_published_heat_is_exactly_zero(
    tmp_path: Path, offset: timedelta
) -> None:
    run_with(tmp_path, ramp_edge_events(offset))
    validate_published(tmp_path)
    events = load(tmp_path / "games" / "minecraft.json")["event_intelligence"]
    assert events["availability"] == "available"
    assert events["event_heat"] == 0 and events["top_event_id"] is None
    (upcoming,) = events["contributions"]
    assert (upcoming["phase"], upcoming["in_ramp"], upcoming["counted"]) == (
        "upcoming",
        False,
        False,
    )
    assert upcoming["strength"] == 0 and upcoming["contribution"] == 0


def test_one_second_inside_the_ramp_publishes_a_heat_above_zero(tmp_path: Path) -> None:
    result = run_with(tmp_path, ramp_edge_events(timedelta(seconds=-1)))
    validate_published(tmp_path)  # zero heat with a counted event would be refused here
    rule = EVENT_HEAT_RULES_V1.rule(EventType.MAJOR_PATCH)
    strength = rule.pre_event_peak * (1 / 86400) / rule.ramp_days
    expected = 100 * rule.importance * strength

    events = load(tmp_path / "games" / "minecraft.json")["event_intelligence"]
    (edge,) = events["contributions"]
    assert (edge["phase"], edge["in_ramp"], edge["counted"], edge["rank"]) == (
        "upcoming",
        True,
        True,
        1,
    )
    assert 0 < edge["strength"] < 1e-5 and edge["strength"] == pytest.approx(strength, rel=1e-6)
    assert 0 < edge["contribution"] < 1e-5
    assert events["event_heat"] > 0  # and not the 0.0 that rounding to a decimal would give
    assert events["event_heat"] == pytest.approx(expected, rel=1e-6)
    assert round(events["event_heat"], 1) == 0.0  # which is exactly why it must not be rounded
    assert events["top_event_id"] == edge["event_id"]
    # The file holds the engine's own number, unrounded, and the model round-trips it.
    intelligence = result.bundle.games["minecraft"].event_intelligence
    assert intelligence.event_heat == events["event_heat"]
    assert (
        GameArtifact.model_validate(load(tmp_path / "games" / "minecraft.json")).event_intelligence
        == intelligence
    )
    raw = (tmp_path / "games" / "minecraft.json").read_text()
    assert '"event_heat": 0.0' not in raw and '"event_heat": 0,' not in raw
    # The radar carries the same unrounded figures.
    (item,) = load(tmp_path / "radar.json")["items"]
    assert (
        item["game_event_heat"] == events["event_heat"]
        and item["contribution"] == edge["contribution"]
    )
    assert item["hours_until_start"] == pytest.approx(120 - 1 / 3600)
    # Score v0 beside it is what it always was.
    game = load(tmp_path / "games" / "minecraft.json")
    assert all(game[field] == GOLDEN["games"]["minecraft"][field] for field in SCORE_FIELDS)


def test_the_three_published_states_stay_distinct(tmp_path: Path) -> None:
    catalogue = load_seed_catalogue()
    by_slug = {game.slug: game.canonical_game_id for game in catalogue.games}
    inputs = ramp_edge_events(timedelta(seconds=-1))
    looked = CoverageCheck(
        canonical_game_id=by_slug["gta-v"], source="synthetic_events", checked_at=AS_OF - DAY
    )
    run_with(tmp_path, EventInputs(records=inputs.records, checks=(looked,)))
    validate_published(tmp_path)
    games = games_of(tmp_path)
    positive = games["minecraft"]["event_intelligence"]
    zero = games["gta-v"]["event_intelligence"]
    unknown = games["rust"]["event_intelligence"]
    assert positive["availability"] == "available" and positive["event_heat"] > 0
    assert zero["availability"] == "available" and zero["event_heat"] == 0
    assert isinstance(zero["event_heat"], float) and zero["contributions"] == []
    assert unknown["availability"] == "unavailable" and unknown["event_heat"] is None
    assert (
        len(
            {
                repr(positive["event_heat"] > 0),
                repr(zero["event_heat"]),
                repr(unknown["event_heat"]),
            }
        )
        == 3
    )


def test_validation_refuses_a_zero_heat_beside_a_counted_event(tmp_path: Path) -> None:
    run_with(tmp_path, ramp_edge_events(timedelta(seconds=-1)))
    version = tmp_path / load(tmp_path / "manifest.json")["version_path"]
    for root in (version, tmp_path):
        document = load(root / "games" / "minecraft.json")
        document["event_intelligence"]["event_heat"] = 0.0  # what rounding used to publish
        (root / "games" / "minecraft.json").write_text(json.dumps(document, indent=2) + "\n")
    with pytest.raises(ArtifactValidationError, match="heat and counted events disagree"):
        validate_published(tmp_path)


def test_nothing_at_the_contract_boundary_rounds_an_event_value() -> None:
    source = (REPO / "src" / "metaspike" / "publishing" / "event_views.py").read_text()
    code = "\n".join(line.split("#")[0] for line in source.splitlines())
    assert "round(" not in code and "_DECIMALS" not in code


def test_every_published_file_matches_its_checked_in_schema_with_and_without_events(
    tmp_path: Path,
) -> None:
    validators = {
        name: Draft202012Validator(load(SCHEMAS / f"{name}.schema.json"))
        for name in ARTIFACT_MODELS
    }
    assert "radar" in validators
    for name, events in (("plain", None), ("eventful", synthetic_events())):
        root = tmp_path / name
        run_with(root, events)
        manifest = load(root / "manifest.json")
        assert manifest["schema_version"] == SCHEMA_VERSION == "1.1.0"
        assert manifest["artifacts"]["radar"] == f"{manifest['version_path']}/radar.json"
        validators["manifest"].validate(manifest)
        validators["radar"].validate(load(root / manifest["artifacts"]["radar"]))
        validators["methodology"].validate(load(root / "methodology.json"))
        for path in sorted((root / "games").glob("*.json")):
            validators["game"].validate(load(path))
        assert (root / "radar.json").read_bytes() == (
            root / manifest["artifacts"]["radar"]
        ).read_bytes()


def test_the_schema_allows_a_null_heat_and_no_negative_one() -> None:
    schema = load(SCHEMAS / "game.schema.json")
    heat = schema["$defs"]["EventIntelligenceView"]["properties"]["event_heat"]
    assert {"type": "null"} in heat["anyOf"]
    assert {"maximum": 100, "minimum": 0, "type": "number"} in heat["anyOf"]
    assert "event_intelligence" in schema["required"]
    assert schema["$defs"]["EventIntelligenceView"]["properties"]["availability"]["enum"] == [
        "available",
        "unavailable",
    ]


def test_the_contract_refuses_a_heat_on_unavailable_intelligence(tmp_path: Path) -> None:
    run_with(tmp_path, None)
    game = load(tmp_path / "games" / "minecraft.json")
    GameArtifact.model_validate(game)
    for patch, message in (
        ({"event_heat": 0.0}, "event_heat is a number exactly when"),
        ({"availability": "available"}, "event_heat is a number exactly when"),
        ({"unavailable_reasons": []}, "unavailable_reasons are given exactly when"),
    ):
        broken = {**game, "event_intelligence": {**game["event_intelligence"], **patch}}
        with pytest.raises(ValidationError, match=message):
            GameArtifact.model_validate(broken)
    radar = load(tmp_path / "radar.json")
    with pytest.raises(ValidationError, match="unavailable_reasons are given exactly when"):
        RadarArtifact.model_validate({**radar, "availability": "available"})
    with pytest.raises(ValidationError):
        EventIntelligenceView.model_validate({**game["event_intelligence"], "event_heat": -1})


def test_validation_catches_event_data_that_disagrees_with_itself(tmp_path: Path) -> None:
    run_with(tmp_path, synthetic_events())
    version = tmp_path / load(tmp_path / "manifest.json")["version_path"]

    def tamper(relative: str, change: Any) -> None:
        for root in (version, tmp_path):
            document = load(root / relative)
            change(document)
            (root / relative).write_text(json.dumps(document, indent=2) + "\n")

    def restore(relative: str, original: str) -> None:
        for root in (version, tmp_path):
            (root / relative).write_text(original)

    cases = [
        ("radar.json", lambda d: d.update(games_with_coverage=4), "radar coverage counts disagree"),
        ("radar.json", lambda d: d["items"][0].update(slug="rust"), "without coverage"),
        (
            "radar.json",
            lambda d: d["items"][0].update(event_id="evt_missing"),
            "does not carry as upcoming",
        ),
        ("radar.json", lambda d: d.update(as_of="2026-10-05T00:00:00Z"), "differ on as_of"),
        (
            "games/minecraft.json",
            lambda d: d["event_intelligence"].update(rules_version="event_heat_rules_v0"),
            "rules the methodology does not describe",
        ),
        (
            "games/minecraft.json",
            lambda d: d["event_intelligence"].update(top_event_id="evt_other"),
            "top event is not the strongest",
        ),
        (
            "games/gta-v.json",
            lambda d: d["event_intelligence"].update(event_heat=12.5),
            "heat and counted events disagree",
        ),
    ]
    for relative, change, message in cases:
        original = (version / relative).read_text()
        tamper(relative, change)
        with pytest.raises(ArtifactValidationError, match=message):
            validate_published(tmp_path)
        restore(relative, original)
    validate_published(tmp_path)


def test_an_event_for_a_game_outside_the_catalogue_is_refused(tmp_path: Path) -> None:
    catalogue = load_seed_catalogue()
    record = reconcile(
        [
            observe(
                report_for(catalogue, "rust", EventType.WIPE, "Synthetic Wipe", AS_OF),
                catalogue,
                AS_OF - DAY,
            )
        ]
    )[0]
    stray = record.model_copy(update={"canonical_game_id": "00000000-0000-4000-8000-0000000000aa"})
    with pytest.raises(ValueError, match="games not in the catalogue"):
        run_with(tmp_path, EventInputs(records=(stray,)))
    assert not (tmp_path / "manifest.json").exists()  # nothing was published


def test_the_methodology_publishes_every_rule_and_says_it_is_uncalibrated(
    artifacts_dir: Path,
) -> None:
    run_fixture_pipeline(artifacts_dir)
    methodology = load(artifacts_dir / "methodology.json")
    events = methodology["event_heat"]
    assert (
        events["version"] == "event_heat_rules_v1" and events["status"] == "internal_uncalibrated"
    )
    assert events["range"] == [0, 100] and events["radar_windows_hours"] == [24, 168, 720]
    assert [rule["event_type"] for rule in events["types"]] == [
        kind.value for kind in EVENT_HEAT_RULES_V1.types
    ]
    assert {rule["event_type"] for rule in events["types"]} == {kind.value for kind in EventType}
    for published in events["types"]:
        rule = EVENT_HEAT_RULES_V1.rule(EventType(published["event_type"]))
        assert {key: published[key] for key in rule.model_dump()} == rule.model_dump()
        assert published["label"] and published["rationale"]
    assert {parameter["name"] for parameter in events["parameters"]} == {
        "scale",
        "max_contributing_events",
        "coverage_max_age_days",
        "horizon_days",
        "radar_windows_hours",
    }
    assert all(parameter["rationale"] for parameter in events["parameters"])
    assert [term["name"] for term in events["availability"]] == [
        "available, heat 0", "available, heat above 0", "unavailable",
    ]  # fmt: skip
    assert "not part of it" in events["scope_note"] and "never means zero" in events["scope_note"]
    assert any("not part of metaspike_score_v0" in line for line in events["limitations"])
    assert any("No event source is collected yet" in line for line in events["limitations"])
    # The score's own methodology is exactly what it was.
    assert methodology["score"]["version"] == "metaspike_score_v0"
    assert methodology["score"]["terms"][-1]["formula"] == "100 * (0.7 * anomaly + 0.3 * momentum)"
