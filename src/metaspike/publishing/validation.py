"""Validate artifacts as written on disk, against the contract and against each other."""

from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from pydantic import ValidationError

from metaspike.publishing.artifacts import (
    CURRENT_FILE,
    GAMES_DIR,
    MANIFEST_FILE,
    METHODOLOGY_FILE,
    RADAR_FILE,
    SCHEMA_VERSION,
    SOURCE_HEALTH_FILE,
    VALIDATION_FILE,
    ContractModel,
    CurrentArtifact,
    GameArtifact,
    Manifest,
    MethodologyArtifact,
    RadarArtifact,
    SourceHealthArtifact,
    ValidationArtifact,
    game_file,
)


class ArtifactValidationError(Exception):
    """A written artifact breaks the public contract."""


def read_artifact[ModelT: ContractModel](path: Path, model: type[ModelT]) -> ModelT:
    try:
        return model.model_validate_json(path.read_bytes())
    except FileNotFoundError:
        raise ArtifactValidationError(f"{path.name}: missing") from None
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"])
        raise ArtifactValidationError(
            f"{path.name}: {exc.error_count()} contract violation(s), first at "
            f"'{location}': {first['msg']}"
        ) from None


def validate_version(version_dir: Path) -> CurrentArtifact:
    """Validate one complete version directory. Returns its leaderboard."""
    current = read_artifact(version_dir / CURRENT_FILE, CurrentArtifact)
    health = read_artifact(version_dir / SOURCE_HEALTH_FILE, SourceHealthArtifact)
    methodology = read_artifact(version_dir / METHODOLOGY_FILE, MethodologyArtifact)
    radar = read_artifact(version_dir / RADAR_FILE, RadarArtifact)
    validation = read_artifact(version_dir / VALIDATION_FILE, ValidationArtifact)

    listed = [entry.slug for entry in current.entries] + [e.slug for e in current.unranked]
    _require(len(listed) == len(set(listed)), "current.json lists a game more than once")
    on_disk = sorted(path.stem for path in (version_dir / GAMES_DIR).glob("*.json"))
    _require(on_disk == sorted(listed), "game files do not match the games in current.json")
    games = {slug: read_artifact(version_dir / game_file(slug), GameArtifact) for slug in listed}

    for artifact in (current, health, methodology, radar, validation, *games.values()):
        _require(artifact.schema_version == SCHEMA_VERSION, "unexpected schema_version")
        _require(artifact.run_id == current.run_id, "artifacts come from different runs")

    ranks = [entry.rank for entry in current.entries]
    _require(ranks == list(range(1, len(ranks) + 1)), "ranks are not contiguous from 1")
    scores = [entry.score for entry in current.entries]
    _require(scores == sorted(scores, reverse=True), "leaderboard is not ordered by score")
    for entry in current.entries:
        game = games[entry.slug]
        _require(game.slug == entry.slug, f"{entry.slug}: file name and slug disagree")
        _require(
            game.score.value == entry.score and game.rank == entry.rank,
            f"{entry.slug}: leaderboard and game artifact disagree",
        )
        _require(
            entry.confidence <= current.confidence_ceiling,
            f"{entry.slug}: confidence exceeds the ceiling",
        )
    _validate_events(games, methodology, radar)
    _validate_history(validation)
    _validate_event_sources(health)
    for unranked in current.unranked:
        game = games[unranked.slug]
        _require(
            game.score.value is None and game.rank is None,
            f"{unranked.slug}: unranked game carries a score",
        )
    return current


def validate_published(root: Path) -> Manifest:
    """Validate what a consumer sees: the manifest, the version it selects, and the mirror."""
    manifest = read_artifact(root / MANIFEST_FILE, Manifest)
    version_dir = root / manifest.version_path
    current = validate_version(version_dir)
    _require(current.run_id == manifest.run_id, "manifest selects a different run")
    _require(
        (manifest.distribution == "private_development_only") == bool(manifest.restricted_sources),
        "manifest distribution disagrees with its restricted sources",
    )
    health = read_artifact(version_dir / SOURCE_HEALTH_FILE, SourceHealthArtifact)
    named = {restricted.source for restricted in manifest.restricted_sources}
    for source in health.event_sources:
        public = [c for c in source.stage_clearance if c.stage == "public_launch"]
        asked = source.status == "collected" or source.fetches_after_as_of > 0
        _require(
            bool(public) and (not asked or public[0].cleared or source.source in named),
            f"{source.source}: not cleared for public launch, yet the manifest does not "
            "mark the bundle as private",
        )
    # Every source an event or a coverage check in the bundle is attributed to must be
    # accounted for: named as restricted, or described here as cleared for public
    # launch. An event source nobody declared is not allowed to be in a bundle at all.
    public_sources = {
        source.source
        for source in health.event_sources
        if any(c.stage == "public_launch" and c.cleared for c in source.stage_clearance)
    }
    radar = read_artifact(version_dir / RADAR_FILE, RadarArtifact)
    used = {name for item in radar.items for name in item.sources}
    for relative in manifest.artifacts.games.values():
        game = read_artifact(root / relative, GameArtifact)
        used.update(game.event_intelligence.coverage_sources)
        used.update(name for item in game.event_intelligence.contributions for name in item.sources)
    undeclared = sorted(used - named - public_sources)
    _require(
        not undeclared,
        f"event source(s) {', '.join(undeclared)} appear in the bundle without being "
        "declared as restricted or as cleared for public launch",
    )
    _require(
        manifest.ranked_count == len(current.entries)
        and manifest.game_count == len(current.entries) + len(current.unranked),
        "manifest counts disagree with current.json",
    )
    referenced = [
        manifest.artifacts.current,
        manifest.artifacts.source_health,
        manifest.artifacts.methodology,
        manifest.artifacts.radar,
        manifest.artifacts.validation,
        *manifest.artifacts.games.values(),
    ]
    for relative in referenced:
        versioned = root / relative
        _require(versioned.is_file(), f"manifest references a missing file: {relative}")
        mirror = root / Path(relative).relative_to(manifest.version_path)
        _require(
            mirror.is_file() and mirror.read_bytes() == versioned.read_bytes(),
            f"top-level copy is out of date: {mirror.relative_to(root)}",
        )
    return manifest


def _validate_events(
    games: dict[str, GameArtifact], methodology: MethodologyArtifact, radar: RadarArtifact
) -> None:
    """Event intelligence agrees with itself, with the rules and with the radar.

    Above all: unavailable is never a number, and a number is never unavailable.
    """
    rules = methodology.event_heat
    slugs_with_coverage = set()
    for slug, game in games.items():
        events = game.event_intelligence
        _require(
            events.rules_version == rules.version,
            f"{slug}: event intelligence uses rules the methodology does not describe",
        )
        _require(events.as_of == radar.as_of, f"{slug}: events and radar differ on as_of")
        if events.availability == "unavailable":
            _require(
                events.event_heat is None and not events.contributions and not game.events,
                f"{slug}: unavailable event intelligence carries a heat or events",
            )
            continue
        slugs_with_coverage.add(slug)
        _require(events.event_heat is not None, f"{slug}: available without a heat")
        counted = [item for item in events.contributions if item.counted]
        _require(
            [item.rank for item in counted] == list(range(1, len(counted) + 1)),
            f"{slug}: counted events are not ranked from 1",
        )
        _require(
            (events.top_event_id is None) == (not counted)
            and (not counted or counted[0].event_id == events.top_event_id),
            f"{slug}: top event is not the strongest counted event",
        )
        # Three states, never two: no counted event is exactly 0, and any counted event
        # is above 0 however small. A positive heat is never published as zero.
        _require(
            all(item.contribution > 0 for item in counted),
            f"{slug}: a counted event contributes nothing",
        )
        _require(
            (events.event_heat == 0) == (not counted),
            f"{slug}: heat and counted events disagree",
        )
    _require(
        radar.games_with_coverage == len(slugs_with_coverage)
        and radar.games_with_coverage + radar.games_without_coverage == len(games),
        "radar coverage counts disagree with the games",
    )
    _require(
        (radar.availability == "available") == bool(slugs_with_coverage),
        "radar availability disagrees with the games' event coverage",
    )
    for item in radar.items:
        _require(item.slug in slugs_with_coverage, f"radar lists {item.slug} without coverage")
        _require(
            any(
                contribution.event_id == item.event_id and contribution.phase == "upcoming"
                for contribution in games[item.slug].event_intelligence.contributions
            ),
            f"radar lists an event {item.slug} does not carry as upcoming",
        )


def _validate_event_sources(health: SourceHealthArtifact) -> None:
    """An event source's counts agree with themselves. A post is never counted as an event."""
    for source in health.event_sources:
        name = source.source
        _require(
            source.candidates_promoted + source.candidates_not_promoted == source.candidates,
            f"{name}: promoted and unpromoted candidates do not add up",
        )
        _require(
            source.candidates_promoted <= source.candidates_classified <= source.candidates,
            f"{name}: more candidates promoted than classified",
        )
        _require(
            sum(source.promoted_event_types.values()) == source.candidates_promoted,
            f"{name}: promoted event types do not add up to the promoted count",
        )
        _require(
            source.candidates_promoted == 0 or source.fetches_ok > 0,
            f"{name}: promoted candidates without a successful fetch",
        )
        collected = source.fetches_ok + source.fetches_failed + source.fetches_not_sent > 0
        _require(
            (source.status == "collected") == collected,
            f"{name}: status disagrees with its fetches",
        )
        _require(
            collected
            or (source.posts_observed == 0 and source.candidates == 0 and not source.games),
            f"{name}: reports posts or candidates without having been asked",
        )
        _require(
            len({stage.stage for stage in source.stage_clearance}) == 4,
            f"{name}: does not state every deployment stage",
        )
        _require(
            sum(game.candidates_promoted for game in source.games) <= source.candidates_promoted,
            f"{name}: games report more promoted candidates than the source",
        )


def _validate_history(validation: ValidationArtifact) -> None:
    """A published backtest report agrees with itself, and claims no more than it shows."""
    report = validation.report
    if report is None:
        return
    _require(
        report.protocol_version == validation.protocol_version
        and report.outcome_rules_version == validation.outcome_definition.version,
        "validation report and artifact disagree on the rules used",
    )
    _require(
        report.ranked + report.unranked == report.recommendations,
        "validation counts do not add up",
    )
    _require(
        report.primary_evaluable == report.overall.count <= report.ranked,
        "evaluable outcomes disagree with the overall summary",
    )
    _require(
        any(strategy.strategy == report.score_version for strategy in report.strategies),
        "the score under test is not among the strategies",
    )
    # The status must be the highest one whose requirements were all met, and no higher.
    reached = [check.status for check in report.evidence.checks if check.reached]
    expected = reached[-1] if reached else "framework_only"
    _require(report.evidence.status == expected, "evidence status is not what its checks support")
    for check in report.evidence.checks:
        _require(
            not check.reached or all(item.met for item in check.requirements),
            f"evidence status {check.status} is marked reached with an unmet requirement",
        )
        _require(
            all(item.met == (item.actual >= item.required) for item in check.requirements),
            "an evidence requirement is marked against its own numbers",
        )
    _require(
        all(check.passed for check in report.leakage_checks) and bool(report.leakage_checks),
        "a leakage check did not pass",
    )
    _require(
        any(check.name == "source_publication_lag_respected" for check in report.leakage_checks),
        "the source publication lag was not checked",
    )
    # Point-in-time: nothing a recommendation saw may be newer than its source had
    # published by then, and nothing it is judged on may start before it was made.
    _require(bool(report.publication_lag_hours), "no source publication lag is stated")
    _require(
        all(hours >= 0 for hours in report.publication_lag_hours.values()),
        "a source publication lag is negative",
    )
    shortest = timedelta(hours=min(report.publication_lag_hours.values()))
    _require(
        report.first_prediction_day <= report.last_prediction_day
        and report.first_latest_due_day <= report.last_latest_due_day
        and report.first_latest_due_day < report.first_prediction_day
        and report.last_latest_due_day < report.last_prediction_day,
        "the newest visible day is not before the day of the recommendation",
    )
    for day in report.inspection:
        published = datetime.combine(day.latest_due_day, time(), UTC) + timedelta(days=1)
        _require(
            day.as_of.astimezone(UTC).date() == day.day and published + shortest <= day.as_of,
            f"{day.day}: the newest visible day had not been published at the recommendation",
        )
        _require(
            datetime.combine(day.primary_window_start, time(), UTC) >= day.as_of
            and day.primary_window_start <= day.primary_window_end,
            f"{day.day}: the outcome window does not start after the recommendation",
        )
    _require(
        all(
            len(value) == 64 and all(character in "0123456789abcdef" for character in value)
            for value in (report.snapshot_set_hash, report.label_set_hash)
        ),
        "a provenance hash is not a SHA-256 digest",
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ArtifactValidationError(message)
