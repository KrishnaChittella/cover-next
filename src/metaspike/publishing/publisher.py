"""Atomic local publisher.

Layout under the artifacts root:

    manifest.json                 <- the single pointer consumers trust
    versions/{run_id}/            <- immutable, complete, validated run output
        current.json  source-health.json  methodology.json  games/{slug}.json
    current.json, games/, ...     <- convenience copy of the selected version

A run is written to a staging directory, validated from disk, renamed into
`versions/` in one step, and only then selected by atomically replacing
manifest.json. A run that fails at any earlier point leaves the previous manifest
and everything it references untouched. The same sequence maps directly onto an
object store later: upload the version prefix, then overwrite the manifest object.

The top-level copy is refreshed last, file by file. It exists so the artifacts are
easy to browse; `manifest.json` remains the source of truth.
"""

import contextlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from metaspike.domain.model import FrozenModel
from metaspike.publishing.artifacts import (
    GAMES_DIR,
    MANIFEST_FILE,
    VERSIONS_DIR,
    ArtifactBundle,
)
from metaspike.publishing.validation import validate_published, validate_version

KEEP_VERSIONS = 5
_STAGING_PREFIX = ".staging-"


def to_json(artifact: FrozenModel) -> str:
    """Stable, human-readable JSON. allow_nan=False makes NaN/Infinity a hard error."""
    return (
        json.dumps(artifact.model_dump(mode="json"), indent=2, ensure_ascii=False, allow_nan=False)
        + "\n"
    )


def publish(bundle: ArtifactBundle, root: Path, keep_versions: int = KEEP_VERSIONS) -> Path:
    """Publish a bundle and return the version directory it now lives in."""
    versions = root / VERSIONS_DIR
    versions.mkdir(parents=True, exist_ok=True)
    for leftover in versions.glob(f"{_STAGING_PREFIX}*"):
        shutil.rmtree(leftover, ignore_errors=True)

    files = bundle.version_files()
    final = versions / bundle.manifest.run_id
    staging = Path(tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=versions))
    try:
        for relative, artifact in files.items():
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(to_json(artifact), encoding="utf-8")
        validate_version(staging)
        if final.exists():
            # Same second and same content hash: an identical re-publish.
            shutil.rmtree(final)
        staging.replace(final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    _write_atomically(root / MANIFEST_FILE, to_json(bundle.manifest))  # the commit point
    _refresh_top_level_copy(root, final, list(files))
    _prune_versions(versions, keep=keep_versions, current=final)
    validate_published(root)
    return final


def _write_atomically(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            Path(temporary).unlink()
        raise


def _refresh_top_level_copy(root: Path, version_dir: Path, files: list[str]) -> None:
    for relative in files:
        _write_atomically(root / relative, (version_dir / relative).read_text(encoding="utf-8"))
    published = {Path(relative).name for relative in files if relative.startswith(GAMES_DIR)}
    for stale in (root / GAMES_DIR).glob("*.json"):
        if stale.name not in published:
            stale.unlink()


def _prune_versions(versions: Path, keep: int, current: Path) -> None:
    """Keep the newest `keep` versions. Run ids start with a UTC timestamp, so they sort."""
    existing = sorted(
        path
        for path in versions.iterdir()
        if path.is_dir() and not path.name.startswith(_STAGING_PREFIX)
    )
    for old in existing[: max(len(existing) - keep, 0)]:
        if old != current:
            shutil.rmtree(old)
