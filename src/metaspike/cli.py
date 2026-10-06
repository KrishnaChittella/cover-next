"""Command-line interface. `metaspike run` performs the complete local vertical slice."""

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import httpx
from pydantic import ValidationError

from metaspike.catalogue.bindings import audit_bootstrap_bindings
from metaspike.catalogue.eligibility import (
    SourceEligibility,
    bootstrap_scope,
    sampling_targets,
    steam_eligibility,
)
from metaspike.catalogue.generate import CatalogueState, generate_catalogue
from metaspike.catalogue.seed import CatalogueError, bootstrap_catalogue, load_seed_catalogue
from metaspike.domain.samples import SampleStatus
from metaspike.domain.signals import CollectionStatus, SignalKey
from metaspike.domain.source_policy import DeploymentStage
from metaspike.domain.source_records import SourceRecord
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
from metaspike.pipeline.sampling import (
    SamplingRunReport,
    run_sampling_slot,
    target_health,
)
from metaspike.publishing.publisher import to_json
from metaspike.publishing.schemas import export_schemas, stale_schemas
from metaspike.publishing.validation import ArtifactValidationError, validate_published
from metaspike.resolution.evaluate import evaluate
from metaspike.resolution.golden import load_golden_set
from metaspike.resolution.recorded import load_recorded_catalogue
from metaspike.scoring.v0 import ScoreInvariantError
from metaspike.sources.base import SourceError
from metaspike.sources.catalogue_sources import (
    build_igdb,
    build_steam,
    default_limiter_pool,
    register_catalogue_sources,
)
from metaspike.sources.credentials import STEAM_WEB_API_KEY_ENV, Secret
from metaspike.sources.igdb import LOOKUP_TABLES
from metaspike.sources.limiter import FileQuotaLedger, LimiterPool, RateLimiter
from metaspike.sources.registry import SourcePolicyError, SourceRegistry, default_registry
from metaspike.sources.replay import replay_client
from metaspike.sources.sampling_sources import (
    build_steam_players,
    replay_sampling_client,
    sampling_registry,
)
from metaspike.sources.steam_catalogue import STEAM_CATALOGUE_POLICY, SteamAppKind
from metaspike.sources.steam_players import (
    SAMPLED_PLAYERS,
    STEAM_CURRENT_PLAYERS_POLICY,
)
from metaspike.sources.steam_players import (
    SOURCE_ID as STEAM_PLAYERS_SOURCE,
)
from metaspike.sources.wikimedia import build_client
from metaspike.storage.base import StoragePolicyError
from metaspike.storage.local import LocalObservationStore
from metaspike.storage.samples import LocalSampleStore

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_INVALID_CATALOGUE = 2  # argparse also uses 2 for invalid usage
EXIT_COLLECTION_FAILED = 3
EXIT_INVALID_OUTPUT = 4
EXIT_SOURCE_POLICY = 5
EXIT_SOURCE_UNAVAILABLE = 6

DEFAULT_ARTIFACTS_DIR = Path("artifacts")
DEFAULT_SCHEMAS_DIR = Path("schemas")
DEFAULT_CACHE_DIR = Path(".metaspike/cache")
DEFAULT_GOLDEN_SET = Path("tests/fixtures/resolution_golden.json")
DEFAULT_RECORDED_CATALOGUE = Path("tests/fixtures/catalogue")
DEFAULT_RECORDED_DIRECTORIES = (
    DEFAULT_RECORDED_CATALOGUE,
    Path("tests/fixtures/bootstrap_catalogue"),
)
DEFAULT_CATALOGUE_STATE = Path(".metaspike/catalogue/state.json")
DEFAULT_SAMPLE_STORE = Path(".metaspike/samples")
DEFAULT_QUOTA_DIR = Path(".metaspike/quota")
DEFAULT_PROBE_APP = 730
_PLAYERS_HOST = "api.steampowered.com"
_PLAYERS_PATH = "/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
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

    probe = commands.add_parser(
        "probe-sources",
        help="LIVE: one small authenticated call per catalogue source; reports field "
        "names and vocabularies, never credentials",
    )
    probe.add_argument(
        "--source",
        choices=["steam", "igdb", "all", "steam-players"],
        default="all",
        help="'all' covers the catalogue sources only; steam-players must be asked for",
    )
    probe.add_argument("--app", type=int, default=DEFAULT_PROBE_APP, metavar="APPID")
    probe.add_argument(
        "--record", type=Path, default=None, metavar="FILE", help="write a scrubbed fixture"
    )
    probe.add_argument(
        "--recorded", type=Path, nargs="+", default=list(DEFAULT_RECORDED_DIRECTORIES)
    )
    probe.set_defaults(handler=_probe_sources)

    sample = commands.add_parser(
        "sample",
        help="sample concurrent players once, for one slot, into the local sample store "
        "(safe to rerun; installs no schedule)",
    )
    sample.add_argument(
        "--recorded", type=Path, nargs="+", default=list(DEFAULT_RECORDED_DIRECTORIES)
    )
    sample.add_argument("--state", type=Path, default=DEFAULT_CATALOGUE_STATE)
    sample.add_argument("--store-dir", type=Path, default=DEFAULT_SAMPLE_STORE)
    sample.add_argument(
        "--scheduled-for",
        type=datetime.fromisoformat,
        default=None,
        metavar="ISO-TIME",
        help="the slot this run was aimed at (default: the current slot)",
    )
    sample.add_argument(
        "--replay",
        type=Path,
        default=None,
        metavar="DIR",
        help="answer from recorded responses in DIR instead of calling Steam",
    )
    sample.set_defaults(handler=_sample)

    status = commands.add_parser(
        "sample-status", help="health of the local sample store; never values or secrets"
    )
    status.add_argument("--store-dir", type=Path, default=DEFAULT_SAMPLE_STORE)
    status.add_argument("--quota-dir", type=Path, default=DEFAULT_QUOTA_DIR)
    status.add_argument("--hours", type=int, default=24, help="window to summarise")
    status.add_argument("--json", action="store_true", help="print the status as JSON")
    status.set_defaults(handler=_sample_status)

    evaluate_cmd = commands.add_parser(
        "evaluate-resolver",
        help="evaluate the resolver on the frozen golden set (fails on any false merge)",
    )
    evaluate_cmd.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN_SET)
    evaluate_cmd.add_argument("--recorded", type=Path, default=DEFAULT_RECORDED_CATALOGUE)
    evaluate_cmd.add_argument("--failures", action="store_true", help="list every miss")
    evaluate_cmd.set_defaults(handler=_evaluate_resolver)

    generate = commands.add_parser(
        "generate-catalogue",
        help="resolve recorded source records into a generated catalogue state file",
    )
    generate.add_argument(
        "--recorded", type=Path, nargs="+", default=list(DEFAULT_RECORDED_DIRECTORIES)
    )
    generate.add_argument("--state", type=Path, default=DEFAULT_CATALOGUE_STATE)
    generate.add_argument("--as-of", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD")
    generate.set_defaults(handler=_generate_catalogue)

    audit = commands.add_parser(
        "audit-bootstrap-bindings",
        help="show which source records and Steam apps are proven for the 15 bootstrap entities",
    )
    audit.add_argument(
        "--recorded", type=Path, nargs="+", default=list(DEFAULT_RECORDED_DIRECTORIES)
    )
    audit.add_argument("--json", action="store_true", help="print the audit as JSON")
    audit.set_defaults(handler=_audit_bootstrap_bindings)

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


def _percent(value: float | None) -> str:
    return "   n/a" if value is None else f"{100 * value:5.1f}%"


def _evaluate_resolver(args: argparse.Namespace) -> int:
    golden = load_golden_set(args.golden, load_recorded_catalogue(args.recorded))
    report = evaluate(golden)
    overall = report.overall
    print(
        f"Golden set v{report.golden_version} ({golden.status}, {overall.cases} cases, "
        f"hash {report.golden_hash[:12]})  resolver {report.resolver_version}"
    )
    print(f"FALSE AUTO_MATCH MERGES    {overall.false_auto_merges}")
    print(
        f"AUTO_MATCH PRECISION       {_percent(overall.precision)}  "
        f"({overall.correct_auto_matches}/{overall.auto_matches})"
    )
    plain, masked = report.unmasked_positives, report.masked
    print(
        f"REAL-EVIDENCE RECALL       {_percent(plain.recall)}  "
        f"({plain.correct_auto_matches_on_positives}/{plain.positives})  id link visible"
    )
    print(
        f"MASKED STRESS-TEST RECALL  {_percent(masked.recall)}  "
        f"({masked.correct_auto_matches_on_positives}/{masked.positives})  id link withheld"
    )
    print(
        f"ALL-POSITIVES RECALL       {_percent(overall.recall)}  "
        f"({overall.correct_auto_matches_on_positives}/{overall.positives})  both combined"
    )
    print(
        f"QUARANTINE RATE            {_percent(overall.quarantine_rate)}  "
        f"({overall.quarantined}/{overall.cases})"
    )
    print(
        f"AVOIDABLE QUARANTINE RATE  {_percent(overall.avoidable_quarantine_rate)}  "
        f"({overall.avoidable_quarantines}/{overall.not_expecting_quarantine})"
    )
    print(
        f"NEW_ENTITY ACCURACY        {_percent(overall.new_entity_accuracy)}  "
        f"({overall.correct_new_entity_decisions}/{overall.new_entity_decisions})"
    )
    print()
    print(f"{'category':<34}{'cases':>6}{'right':>6}{'auto':>6}{'false':>6}{'quar':>6}{'new':>5}")
    for category, metrics in report.by_category.items():
        print(
            f"{category:<34}{metrics.cases:>6}{metrics.correct:>6}{metrics.auto_matches:>6}"
            f"{metrics.false_auto_merges:>6}{metrics.quarantined:>6}"
            f"{metrics.new_entity_decisions:>5}"
        )
    if args.failures:
        print()
        for miss in report.failures():
            print(
                f"{miss.case_id}: expected {miss.expected.value}, got {miss.decided.value}"
                f"{' [masked]' if miss.masked else ''} - {miss.explanation}"
            )
    if not report.passes_safety_gate:
        return _fail(EXIT_INVALID_OUTPUT, "the resolver made a false automatic merge")
    return EXIT_OK


def _recorded(directories: Sequence[Path]) -> list[SourceRecord]:
    return [r for directory in directories for r in load_recorded_catalogue(directory).values()]


def _audit_bootstrap_bindings(args: argparse.Namespace) -> int:
    records = _recorded(args.recorded)
    audit = audit_bootstrap_bindings(generate_catalogue(records, as_of=_now()), records)
    if args.json:
        print(json.dumps([row.model_dump(mode="json") for row in audit], indent=1))
        return EXIT_OK
    print(f"{'entity':<22}{'legacy':<10}{'igdb':<8}{'steam app':<11}{'telemetry':<10}reason")
    for row in audit:
        print(
            f"{row.slug:<22}{row.legacy_id:<10}{','.join(row.igdb_ids) or '-':<8}"
            f"{','.join(row.steam_app_ids) or '-':<11}{row.steam_telemetry.value:<10}{row.reason}"
        )
    return EXIT_OK


def _generate_catalogue(args: argparse.Namespace) -> int:
    records = _recorded(args.recorded)
    previous = (
        CatalogueState.model_validate_json(args.state.read_bytes())
        if args.state.is_file()
        else None
    )
    as_of = _now() if args.as_of is None else datetime.combine(args.as_of, time.min, tzinfo=UTC)
    state = generate_catalogue(records, as_of=as_of, previous=previous)
    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(to_json(state), encoding="utf-8")
    metrics = state.quarantine.metrics(as_of)
    created = len(state.catalogue.games) - (
        len(bootstrap_catalogue().games) if previous is None else len(previous.catalogue.games)
    )
    print(
        f"Catalogue: {len(state.catalogue.games)} entities ({created} new this run), "
        f"{len(state.mapping.bindings)} source bindings, "
        f"{len(state.related_products)} related products"
    )
    print(f"Quarantine: {metrics.total} records  by reason {metrics.by_reason}")
    print(f"Decisions logged: {len(state.decisions.decisions)}  resolver {state.resolver_version}")
    print(f"State: {args.state}")
    return EXIT_OK


def _probe_sources(args: argparse.Namespace) -> int:
    """Explicit live check of the catalogue sources. Never run by tests."""
    registry = SourceRegistry()
    pool = default_limiter_pool()
    outcome = EXIT_OK
    with build_client() as client:
        if args.source == "steam-players":
            try:
                return _probe_steam_players(client, pool, args)
            except SourceError as error:
                print(f"steam-players: {error.code.value}: {error.message}")
                return EXIT_SOURCE_UNAVAILABLE
        probes = {"steam": _probe_steam, "igdb": _probe_igdb}
        for name in probes if args.source == "all" else [args.source]:
            try:
                probes[name](client, pool, registry)
            except SourceError as error:
                print(f"{name}: {error.code.value}: {error.message}")
                outcome = EXIT_SOURCE_UNAVAILABLE
    return outcome


def _probe_steam(client: httpx.Client, pool: LimiterPool, registry: SourceRegistry) -> None:
    steam = build_steam(client, pool)
    register_catalogue_sources(registry, steam=steam)
    first = steam.fetch_page(SteamAppKind.GAMES, max_results=5)
    print("steam: a normal Web API key was accepted on api.steampowered.com")
    print(f"steam: envelope fields observed: {sorted(first.observed_envelope_fields)}")
    print(f"steam: app fields observed: {sorted(first.observed_app_fields)}")
    print(
        f"steam: games page of 5 -> {first.apps_returned} apps, "
        f"{first.apps_without_a_name} without a name, has_more={first.has_more}, "
        f"next_after_appid={first.next_after_appid}"
    )
    second = steam.fetch_page(SteamAppKind.GAMES, after_appid=first.next_after_appid, max_results=5)
    overlap = {r.key for r in first.records} & {r.key for r in second.records}
    print(f"steam: continuation page -> {second.apps_returned} apps, {len(overlap)} repeated")
    recent = steam.fetch_page(
        SteamAppKind.GAMES, modified_since=_now() - timedelta(days=1), max_results=5
    )
    print(f"steam: modified in the last day (page of 5) -> {recent.apps_returned} apps")
    dlc = steam.fetch_page(SteamAppKind.DLC, max_results=5)
    print(f"steam: dlc-only page of 5 -> {dlc.apps_returned} apps")
    print(
        f"steam: quota remaining today: {pool.for_policy(STEAM_CATALOGUE_POLICY).quota_remaining()}"
    )


def _load_or_generate_state(path: Path, records: list[SourceRecord]) -> CatalogueState:
    """The persisted catalogue state, so bindings keep the dates they were made on."""
    if path.is_file():
        return CatalogueState.model_validate_json(path.read_bytes())
    state = generate_catalogue(records, as_of=_now())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_json(state), encoding="utf-8")
    return state


def _probe_steam_players(client: httpx.Client, pool: LimiterPool, args: argparse.Namespace) -> int:
    """ONE live current-player call for ONE eligible app. Never run by tests."""
    records = _recorded(args.recorded)
    state = generate_catalogue(records, as_of=_now())
    targets = sampling_targets(state, steam_eligibility(state, records), bootstrap_scope())
    if str(args.app) not in {target.source_native_id for target in targets}:
        return _fail(EXIT_SOURCE_POLICY, f"app {args.app} is not an eligible sampling target")
    adapter = build_steam_players(client, pool)
    sampling_registry(adapter)  # refuses unless the policy clears this stage
    probe = adapter.probe(args.app)
    reading = probe.reading
    print(f"steam-players: GET https://{_PLAYERS_HOST}{_PLAYERS_PATH}?appid={probe.app_id}")
    print("steam-players: key sent in the documented request header, never in the URL")
    print(f"steam-players: HTTP status {probe.http_status}")
    print(f"steam-players: body {json.dumps(probe.body, sort_keys=True)}")
    print(f"steam-players: cache/timing headers {json.dumps(probe.headers, sort_keys=True)}")
    print(f"steam-players: other response header names {list(probe.other_header_names)}")
    print(
        f"steam-players: interpreted as {reading.status.value}"
        f"{'' if reading.error_code is None else ' (' + reading.error_code.value + ')'}"
        f", cache age {reading.cache_age_seconds}"
    )
    remaining = pool.for_policy(STEAM_CURRENT_PLAYERS_POLICY).quota_remaining()
    print(f"steam-players: quota remaining today: {remaining}")
    if args.record is not None and reading.status is SampleStatus.OBSERVED:
        fixture = {
            "note": (
                "One recorded response of ISteamUserStats/GetNumberOfCurrentPlayers/v1. "
                "The API key travelled in a request header and is not recorded."
            ),
            "recorded_at": _now().replace(microsecond=0).isoformat(),
            "request": {
                "method": "GET",
                "host": _PLAYERS_HOST,
                "path": _PLAYERS_PATH,
                "query": {"appid": probe.app_id},
            },
            "response": {
                "status": probe.http_status,
                "headers": probe.headers,
                "other_header_names": list(probe.other_header_names),
                "body": probe.body,
            },
        }
        args.record.parent.mkdir(parents=True, exist_ok=True)
        args.record.write_text(json.dumps(fixture, indent=1) + "\n", encoding="utf-8")
        print(f"steam-players: recorded {args.record}")
    return EXIT_OK if reading.status is SampleStatus.OBSERVED else EXIT_SOURCE_UNAVAILABLE


def _sample(args: argparse.Namespace) -> int:
    """One slot, every eligible in-scope app. Values go to the store, not the screen."""
    records = _recorded(args.recorded)
    state = _load_or_generate_state(args.state, records)
    eligibility = steam_eligibility(state, records)
    targets = sampling_targets(state, eligibility, bootstrap_scope())
    scheduled_for = args.scheduled_for
    if scheduled_for is not None and scheduled_for.tzinfo is None:
        scheduled_for = scheduled_for.replace(tzinfo=UTC)
    replaying = args.replay is not None
    pool = LimiterPool() if replaying else default_limiter_pool()
    with replay_sampling_client(args.replay) if replaying else build_client() as client:
        adapter = build_steam_players(
            client, pool, api_key=Secret("replay-no-key") if replaying else None
        )
        registry = sampling_registry(adapter)
        store = LocalSampleStore(args.store_dir, registry.policy)
        report = run_sampling_slot(targets, adapter, store, now=_now, scheduled_for=scheduled_for)
    _print_sampling(report, len(targets))
    in_scope = [e for e in eligibility if e.canonical_game_id in bootstrap_scope()]
    for state_name in (SourceEligibility.INELIGIBLE, SourceEligibility.UNKNOWN):
        names = sorted(e.slug for e in in_scope if e.state is state_name)
        print(f"Not sampled ({state_name.value}): {', '.join(names) or '-'}")
    print(f"Store: {args.store_dir}")
    if report.halted_by is not None:
        return _fail(EXIT_SOURCE_UNAVAILABLE, f"run halted: {report.halted_by.value}")
    return EXIT_OK


def _print_sampling(report: SamplingRunReport, targets: int) -> None:
    print(
        f"Sampling run {report.collection_run_id}  source {report.source}  "
        f"slot {report.scheduled_for:%Y-%m-%dT%H:%MZ}"
    )
    print(
        f"Targets {targets}: {report.count(SampleStatus.OBSERVED)} observed, "
        f"{report.count(SampleStatus.UNAVAILABLE)} unavailable, "
        f"{report.count(SampleStatus.FAILED)} failed, "
        f"{report.count(SampleStatus.REFUSED)} refused, "
        f"{sum(1 for o in report.outcomes if o.status is None)} skipped; "
        f"{report.requests_sent} requests sent"
    )
    for outcome in report.outcomes:
        detail = outcome.action.value if outcome.status is None else outcome.status.value
        if outcome.error_code is not None:
            detail += f" ({outcome.error_code.value})"
        notes = [n for n, on in (("late", outcome.late), ("next slot", outcome.crossed_slot)) if on]
        print(
            f"  app {outcome.source_native_id:<9} {detail}"
            f"{'  [' + ', '.join(notes) + ']' if notes else ''}"
        )


def _sample_status(args: argparse.Namespace) -> int:
    policy = STEAM_CURRENT_PLAYERS_POLICY
    store = LocalSampleStore(args.store_dir, lambda _: policy)
    key = SignalKey(
        source=STEAM_PLAYERS_SOURCE,
        metric=SAMPLED_PLAYERS.metric,
        granularity=SAMPLED_PLAYERS.granularity,
    )
    now = _now()
    slots = args.hours * 4
    health = [
        target_health(store, key, native_id, now, slots, now) for native_id in store.native_ids(key)
    ]
    limiter = RateLimiter(policy.operations, ledger=FileQuotaLedger(args.quota_dir))
    quota = policy.operations.quota
    status = {
        "source": STEAM_PLAYERS_SOURCE,
        "mode": "sample_now",
        "as_of": now.replace(microsecond=0).isoformat(),
        "window_hours": args.hours,
        "slot_minutes": 15,
        "api_key_configured": bool(os.environ.get(STEAM_WEB_API_KEY_ENV, "").strip()),
        "budget_group": policy.operations.budget_group,
        "quota_per_day": None if quota is None else quota.limit,
        "quota_remaining_today": limiter.quota_remaining(),
        "stage_clearance": {stage.value: policy.cleared_for(stage) for stage in DeploymentStage},
        "derived_metrics_allowed": policy.derived_metrics_allowed.value,
        "publishable": policy.publication_refusal() is None,
        "scheduler_installed": False,
        "torn_lines_skipped": store.torn_lines_skipped,
        "targets": [row.model_dump(mode="json") for row in health],
    }
    if args.json:
        print(json.dumps(status, indent=1))
        return EXIT_OK
    print(f"Source {status['source']} ({status['mode']})  as of {status['as_of']}")
    print(
        f"API key configured: {'yes' if status['api_key_configured'] else 'no'}   "
        f"quota remaining today: {status['quota_remaining_today']} of {status['quota_per_day']} "
        f"(group {status['budget_group']})"
    )
    cleared = ", ".join(f"{k}={'yes' if v else 'no'}" for k, v in status["stage_clearance"].items())
    print(f"Cleared: {cleared}")
    print(
        f"Derived metrics: {status['derived_metrics_allowed']}  publishable: "
        f"{'yes' if status['publishable'] else 'no'}  scheduler installed: no"
    )
    print(
        f"Last {args.hours}h, {slots} slots per app; torn lines skipped: "
        f"{status['torn_lines_skipped']}"
    )
    if not health:
        print("No samples stored.")
        return EXIT_OK
    print(
        f"{'app':<10}{'obs':>5}{'fail':>6}{'ref':>5}{'unav':>6}{'none':>6}{'cover':>8}"
        f"{'dup':>5}{'late':>6}  last observed"
    )
    for row in health:
        last = "-" if row.last_observed_at is None else f"{row.last_observed_at:%Y-%m-%dT%H:%M:%SZ}"
        print(
            f"{row.source_native_id:<10}{row.observed:>5}{row.failed:>6}{row.refused:>5}"
            f"{row.unavailable:>6}{row.not_sampled:>6}{100 * row.coverage:>7.1f}%"
            f"{row.duplicates:>5}{row.late:>6}  {last}"
        )
    return EXIT_OK


def _probe_igdb(client: httpx.Client, pool: LimiterPool, registry: SourceRegistry) -> None:
    igdb = build_igdb(client, pool)
    register_catalogue_sources(registry, igdb=igdb)
    for table in LOOKUP_TABLES:
        print(f"igdb: {table}: {list(igdb.vocabulary(table))}")
    sample = igdb.search_games("Grand Theft Auto V", limit=3)
    print(f"igdb: search returned {len(sample)} records")
    for record in sample:
        populated = sorted(
            name for name, value in record if value not in (None, (), "") and name != "key"
        )
        print(
            f"igdb: {record.key.source_id} {record.title!r} type={record.product_type.value} "
            f"category={record.source_category!r} populated={populated}"
        )


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
