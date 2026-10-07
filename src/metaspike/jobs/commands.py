"""The job commands as the image runs them: `metaspike <command>`.

Each command builds its runtime from the environment, runs its job between a start
and a finish record, and returns the job's exit code. Policy is checked inside the
job body, before any adapter that needs a credential is built, so a refused job
never touches a secret.
"""

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

from metaspike.catalogue.eligibility import SamplingTarget
from metaspike.catalogue.seed import BOOTSTRAP_ANCHORS
from metaspike.domain.source_policy import SourcePurpose
from metaspike.domain.source_records import SourceRecord
from metaspike.jobs import runlog
from metaspike.jobs.baseline_jobs import features, parity
from metaspike.jobs.catalogue_job import Fetcher, collect_catalogue
from metaspike.jobs.collect import collect_samples, collect_wikimedia
from metaspike.jobs.dispatch import CloudRunJobs, dispatch
from metaspike.jobs.runtime import (
    BUCKETS,
    ConfigurationError,
    Runtime,
    runtime_from_environment,
)
from metaspike.jobs.smoke import MODES, SmokeFailed, smoke
from metaspike.jobs.targets import load_targets
from metaspike.jobs.warehouse_job import warehouse
from metaspike.sources.catalogue_sources import build_igdb
from metaspike.sources.limiter import LimiterPool
from metaspike.sources.sampling_sources import build_steam_players
from metaspike.sources.steam_players import SOURCE_ID as STEAM_PLAYERS
from metaspike.sources.wikimedia import SOURCE_ID as WIKIMEDIA
from metaspike.sources.wikimedia import WikimediaPageviewsAdapter, build_client
from metaspike.warehouse.auth import MetadataToken

EXIT_CONFIGURATION = 2
_TOKEN_CACHE = Path("/tmp/metaspike-igdb-token.json")


def _dispatch(runtime: Runtime) -> int:
    client = httpx.Client(timeout=60.0)
    starter = CloudRunJobs(client, runtime.project, runtime.region, MetadataToken(client))
    return runlog.run_job(
        runtime, runlog.landed(runtime, "dispatch"), lambda: dispatch(runtime, starter)
    )


def _collect_wikimedia(runtime: Runtime) -> int:
    def body() -> Mapping[str, Any]:
        runtime.refuse_unless_cleared(WIKIMEDIA, SourcePurpose.DEMAND_SIGNALS)
        return collect_wikimedia(runtime, WikimediaPageviewsAdapter(build_client()))

    return runlog.run_job(runtime, runlog.landed(runtime, WIKIMEDIA), body, source=WIKIMEDIA)


def _collect_steam_players(runtime: Runtime) -> int:
    def body() -> Mapping[str, Any]:
        # Refused here, before the adapter is built, so the key is never read.
        runtime.refuse_unless_cleared(STEAM_PLAYERS, SourcePurpose.DEMAND_SIGNALS)
        adapter = build_steam_players(build_client(), LimiterPool(), runtime.environ)
        document = load_targets(runtime.state, runtime.now())
        targets = [
            SamplingTarget(
                source_native_id=target.source_native_id,
                canonical_game_id=target.canonical_game_id,
                mapping_version=target.mapping_version,
                binding_ref=target.binding_ref,
            )
            for target in document.for_source(STEAM_PLAYERS)
        ]
        return collect_samples(runtime, adapter, targets)

    return runlog.run_job(
        runtime, runlog.landed(runtime, STEAM_PLAYERS), body, source=STEAM_PLAYERS
    )


def _igdb_fetcher(runtime: Runtime) -> Fetcher:
    def fetch() -> Sequence[SourceRecord]:
        adapter = build_igdb(build_client(), LimiterPool(), runtime.environ, _TOKEN_CACHE)
        anchors = sorted(
            int(key.source_id) for key in BOOTSTRAP_ANCHORS.values() if key.source == "igdb"
        )
        return adapter.games_by_ids(anchors)

    return fetch


def _steam_catalogue_fetcher() -> Sequence[SourceRecord]:
    # Unreachable while the source is uncleared: the job refuses this portion first.
    raise NotImplementedError("Steam catalogue collection is not built; the source is not cleared")


def _collect_catalogue(runtime: Runtime) -> int:
    fetchers: dict[str, Fetcher] = {
        "igdb": _igdb_fetcher(runtime),
        "steam_catalogue": _steam_catalogue_fetcher,
    }
    return runlog.run_job(
        runtime, runlog.landed(runtime, "catalogue"), lambda: collect_catalogue(runtime, fetchers)
    )


def _warehouse(runtime: Runtime) -> int:
    return runlog.run_job(runtime, runlog.direct(runtime), lambda: warehouse(runtime))


def _features(runtime: Runtime) -> int:
    # This identity may write only to ms_features, so its run log stays in its own
    # operations prefix instead of the run-log table.
    return runlog.run_job(runtime, runlog.kept(runtime, "features/"), lambda: features(runtime))


def _parity(runtime: Runtime) -> int:
    return runlog.run_job(runtime, runlog.direct(runtime), lambda: parity(runtime))


JOBS: dict[str, tuple[str, Callable[[Runtime], int], str]] = {
    "dispatch": ("ms-dispatch", _dispatch, "decide what is due and start it"),
    "collect-steam-players": (
        "ms-steam-players",
        _collect_steam_players,
        "sample Steam concurrent players (refused until the source is cleared)",
    ),
    "collect-wikimedia": ("ms-wikimedia", _collect_wikimedia, "collect daily pageviews"),
    "collect-catalogue": ("ms-catalogue", _collect_catalogue, "collect and resolve the catalogue"),
    "warehouse": ("ms-warehouse", _warehouse, "load landed batches and build the models"),
    "features": ("ms-features", _features, "continue the baseline states"),
    "parity": ("ms-parity", _parity, "rebuild baseline states and compare"),
}


def _run(args: argparse.Namespace) -> int:
    job, handler, _ = JOBS[args.command]
    try:
        runtime = runtime_from_environment(job)
    except ConfigurationError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_CONFIGURATION
    return handler(runtime)


_EXPECTATION = {"allowed": True, "denied": False}


def _smoke(args: argparse.Namespace) -> int:
    try:
        runtime = runtime_from_environment("smoke")
    except ConfigurationError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_CONFIGURATION
    try:
        result = smoke(
            runtime,
            args.bucket,
            args.allowed_prefix,
            args.outside_prefix,
            args.mode,
            read_object=args.read_object,
            expect_read=_EXPECTATION.get(args.expect_read),
            expect_list=_EXPECTATION.get(args.expect_list),
            strict_list=args.strict_list,
        )
    except SmokeFailed as failure:
        print(json.dumps({**failure.result, "reason": str(failure)}, sort_keys=True))
        return runlog.EXIT_FAILED
    print(json.dumps(result, sort_keys=True))
    return runlog.EXIT_OK


def register(commands: Any) -> None:
    """Add the job commands and `smoke` to the command-line parser."""
    for name, (_, _, summary) in JOBS.items():
        commands.add_parser(name, help=f"job: {summary}").set_defaults(handler=_run)
    parser = commands.add_parser(
        "smoke", help="check this identity's storage permissions; calls no source"
    )
    parser.add_argument("--bucket", required=True, choices=list(BUCKETS))
    parser.add_argument("--mode", default="user", choices=MODES)
    parser.add_argument("--allowed-prefix", default="", help="empty for a whole-bucket mode")
    parser.add_argument("--outside-prefix", default=None)
    parser.add_argument(
        "--read-object",
        default=None,
        help="an existing object to read, for an identity that cannot create one",
    )
    parser.add_argument(
        "--expect-read",
        choices=["allowed", "denied"],
        help="override the shape where another reviewed grant gives read on this bucket",
    )
    parser.add_argument(
        "--expect-list",
        choices=["allowed", "denied"],
        help="override the shape where another reviewed grant gives listing on this bucket",
    )
    parser.add_argument(
        "--strict-list", action="store_true", help="fail when listing is not as expected"
    )
    parser.set_defaults(handler=_smoke)
