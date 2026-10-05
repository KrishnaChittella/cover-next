"""Validate artifacts as written on disk, against the contract and against each other."""

from pathlib import Path

from pydantic import ValidationError

from metaspike.publishing.artifacts import (
    CURRENT_FILE,
    GAMES_DIR,
    MANIFEST_FILE,
    METHODOLOGY_FILE,
    SCHEMA_VERSION,
    SOURCE_HEALTH_FILE,
    ContractModel,
    CurrentArtifact,
    GameArtifact,
    Manifest,
    MethodologyArtifact,
    SourceHealthArtifact,
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

    listed = [entry.slug for entry in current.entries] + [e.slug for e in current.unranked]
    _require(len(listed) == len(set(listed)), "current.json lists a game more than once")
    on_disk = sorted(path.stem for path in (version_dir / GAMES_DIR).glob("*.json"))
    _require(on_disk == sorted(listed), "game files do not match the games in current.json")
    games = {slug: read_artifact(version_dir / game_file(slug), GameArtifact) for slug in listed}

    for artifact in (current, health, methodology, *games.values()):
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
        manifest.ranked_count == len(current.entries)
        and manifest.game_count == len(current.entries) + len(current.unranked),
        "manifest counts disagree with current.json",
    )
    referenced = [
        manifest.artifacts.current,
        manifest.artifacts.source_health,
        manifest.artifacts.methodology,
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


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ArtifactValidationError(message)
