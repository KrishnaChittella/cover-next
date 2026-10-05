"""Command-line interface. `metaspike run` performs the complete local vertical slice."""

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
from pydantic import ValidationError

from metaspike.catalogue.seed import CatalogueError, load_seed_catalogue
from metaspike.domain.signals import CollectionStatus
from metaspike.pipeline.collection import (
    CollectionReport,
    collect,
    latest_complete_day,
    window_ending,
)
from metaspike.pipeline.run import (
    CollectionFailedError,
    RunResult,
    build_and_publish,
    store_report,
)
from metaspike.publishing.publisher import to_json
from metaspike.publishing.schemas import export_schemas, stale_schemas
from metaspike.publishing.validation import ArtifactValidationError, validate_published
from metaspike.scoring.v0 import ScoreInvariantError
from metaspike.sources.registry import SourcePolicyError, SourceRegistry, default_registry
from metaspike.sources.replay import replay_client
from metaspike.sources.wikimedia import build_client
from metaspike.storage.base import StoragePolicyError
from metaspike.storage.local import LocalObservationStore

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_INVALID_CATALOGUE = 2  # argparse also uses 2 for invalid usage
EXIT_COLLECTION_FAILED = 3
EXIT_INVALID_OUTPUT = 4
EXIT_SOURCE_POLICY = 5

DEFAULT_ARTIFACTS_DIR = Path("artifacts")
DEFAULT_SCHEMAS_DIR = Path("schemas")
DEFAULT_CACHE_DIR = Path(".metaspike/cache")
_SNAPSHOT_FILE = "collection.json"
_SUMMARY_ROWS = 10

logger = logging.getLogger("metaspike")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        return int(args.handler(args))
    except CatalogueError as error:
        return _fail(EXIT_INVALID_CATALOGUE, f"invalid catalogue: {error}")
    except CollectionFailedError as error:
        return _fail(EXIT_COLLECTION_FAILED, f"collection failed: {error}")
    except (ArtifactValidationError, ScoreInvariantError, ValidationError) as error:
        return _fail(EXIT_INVALID_OUTPUT, f"invalid output, nothing was published: {error}")
    except (SourcePolicyError, StoragePolicyError) as error:
        return _fail(EXIT_SOURCE_POLICY, f"source policy: {error}")
    except Exception:
        logger.exception("unexpected failure")
        return EXIT_UNEXPECTED


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="metaspike", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="log progress to stderr")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate-catalogue", help="validate the seed catalogue")
    validate.set_defaults(handler=_validate_catalogue)

    collect_cmd = commands.add_parser(
        "collect", help="collect from all sources into the local cache"
    )
    _add_collection_options(collect_cmd)
    collect_cmd.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    collect_cmd.set_defaults(handler=_collect)

    build = commands.add_parser("build", help="build and publish artifacts from the cache")
    build.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    build.add_argument("--artifacts-dir", type=Path, default=DEFAULT_ARTIFACTS_DIR)
    build.set_defaults(handler=_build)

    run = commands.add_parser("run", help="collect, score and publish in one step")
    _add_collection_options(run)
    run.add_argument("--artifacts-dir", type=Path, default=DEFAULT_ARTIFACTS_DIR)
    run.set_defaults(handler=_run)

    check = commands.add_parser("validate-artifacts", help="validate published artifacts")
    check.add_argument("--artifacts-dir", type=Path, default=DEFAULT_ARTIFACTS_DIR)
    check.set_defaults(handler=_validate_artifacts)

    schemas = commands.add_parser("export-schemas", help="write JSON Schemas for the artifacts")
    schemas.add_argument("--schemas-dir", type=Path, default=DEFAULT_SCHEMAS_DIR)
    schemas.add_argument("--check", action="store_true", help="fail if schemas are out of date")
    schemas.set_defaults(handler=_export_schemas)
    return parser


def _add_collection_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="last day of the history window (default: yesterday, UTC)",
    )
    parser.add_argument(
        "--replay",
        type=Path,
        default=None,
        metavar="DIR",
        help="replay recorded responses from DIR instead of calling the network",
    )
    parser.add_argument(
        "--store-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="also append collected observations to a local point-in-time store in DIR",
    )


def _validate_catalogue(_: argparse.Namespace) -> int:
    catalogue = load_seed_catalogue()
    related = sum(1 for game in catalogue.games if game.relationships)
    print(f"Catalogue OK: {len(catalogue.games)} games, {related} with relationships.")
    return EXIT_OK


def _client(args: argparse.Namespace) -> httpx.Client:
    return replay_client(args.replay) if args.replay else build_client()


def _collect_report(args: argparse.Namespace, registry: SourceRegistry) -> CollectionReport:
    catalogue = load_seed_catalogue()
    window = window_ending(args.as_of or latest_complete_day(_now()))
    report = collect(catalogue, registry, window, observed_at=_now(), clock=_now)
    if args.store_dir is not None:
        kept = store_report(report, LocalObservationStore(args.store_dir, registry.policy))
        logger.info("stored %d new observation versions in %s", kept, args.store_dir)
    return report


def _collect(args: argparse.Namespace) -> int:
    with _client(args) as client:
        registry = default_registry(client)
        report = _collect_report(args, registry)
    for entry in registry.entries():
        if not entry.policy.raw_storage_allowed.permits:
            raise SourcePolicyError(
                f"{entry.policy.source}: policy does not permit storing collected data, "
                "so it cannot be cached; use `metaspike run` instead"
            )
    snapshot = args.cache_dir / _SNAPSHOT_FILE
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(to_json(report), encoding="utf-8")
    _print_collection(report)
    print(f"Cached collection: {snapshot}")
    return EXIT_OK if report.any_success else EXIT_COLLECTION_FAILED


def _build(args: argparse.Namespace) -> int:
    snapshot = args.cache_dir / _SNAPSHOT_FILE
    if not snapshot.is_file():
        raise CollectionFailedError(f"no cached collection at {snapshot}; run `collect` first")
    report = CollectionReport.model_validate_json(snapshot.read_bytes())
    with build_client() as client:  # the registry needs an adapter; no request is made
        result = build_and_publish(
            load_seed_catalogue(), report, default_registry(client), args.artifacts_dir, _now()
        )
    _print_summary(result, args.artifacts_dir)
    return EXIT_OK


def _run(args: argparse.Namespace) -> int:
    with _client(args) as client:
        registry = default_registry(client)
        report = _collect_report(args, registry)
        result = build_and_publish(
            load_seed_catalogue(), report, registry, args.artifacts_dir, _now()
        )
    _print_summary(result, args.artifacts_dir)
    return EXIT_OK


def _validate_artifacts(args: argparse.Namespace) -> int:
    manifest = validate_published(args.artifacts_dir)
    print(
        f"Artifacts OK: run {manifest.run_id}, schema {manifest.schema_version}, "
        f"{manifest.ranked_count} ranked of {manifest.game_count} games."
    )
    return EXIT_OK


def _export_schemas(args: argparse.Namespace) -> int:
    if args.check:
        stale = stale_schemas(args.schemas_dir)
        if stale:
            return _fail(
                EXIT_INVALID_OUTPUT,
                f"schemas out of date: {', '.join(stale)}; run `metaspike export-schemas`",
            )
        print("Schemas are up to date.")
        return EXIT_OK
    for path in export_schemas(args.schemas_dir):
        print(f"Wrote {path}")
    return EXIT_OK


def _print_collection(report: CollectionReport) -> None:
    first, last = report.window.start.date(), report.window.end.date()
    print(f"Window: {first} to {last} ({(last - first).days + 1} days)")
    for run in report.runs:
        succeeded = len(run.with_status(CollectionStatus.COLLECTED))
        failed = run.with_status(CollectionStatus.FAILED)
        unmapped = len(run.with_status(CollectionStatus.NOT_MAPPED))
        print(
            f"Source {run.source}: {succeeded} succeeded, {len(failed)} failed, "
            f"{unmapped} not mapped"
        )
        for outcome in failed:
            print(f"  failed {outcome.canonical_game_id}: {outcome.error_code}")


def _print_summary(result: RunResult, artifacts_dir: Path) -> None:
    current = result.bundle.current
    manifest = result.bundle.manifest
    print(f"MetaSpike run {manifest.run_id}  ({manifest.score_version})")
    _print_collection(result.report)
    print()
    print(f"{'#':>2}  {'Game':<26} {'Score':>5}  {'Conf':>4}  Why")
    for entry in current.entries[:_SUMMARY_ROWS]:
        print(
            f"{entry.rank:>2}  {entry.name:<26.26} {entry.score:>5.1f}  "
            f"{entry.confidence:>4.0f}  {entry.summary}"
        )
    if len(current.entries) > _SUMMARY_ROWS:
        print(f"    ... and {len(current.entries) - _SUMMARY_ROWS} more ranked")
    for unranked in current.unranked:
        print(f" -  {unranked.name:<26.26} {'':>5}  {'':>4}  {unranked.summary}")
    print()
    print(f"Confidence ceiling: {current.confidence_ceiling:g}/100 (single source).")
    print(f"Content hash: {manifest.content_hash[:16]}")
    print(f"Artifacts: {artifacts_dir.resolve()}  (manifest -> {manifest.version_path})")


def _now() -> datetime:
    return datetime.now(UTC)


def _fail(code: int, message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
