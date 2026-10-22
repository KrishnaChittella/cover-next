"""Command-line interface. `metaspike run` performs the complete local vertical slice."""

import argparse
import hashlib
import json
import logging
import os
import sys
import tempfile
import time as time_module
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from time import perf_counter

import httpx
from pydantic import ValidationError

from metaspike import steam_players_recorder as steam_recorder
from metaspike.catalogue.bindings import audit_bootstrap_bindings
from metaspike.catalogue.eligibility import (
    SourceEligibility,
    bootstrap_scope,
    sampling_targets,
    steam_eligibility,
)
from metaspike.catalogue.evaluation import (
    IncompleteRecords,
    evaluate_broad_catalogue,
    known_record_keys,
    require_catalogue_sources,
    require_complete,
    require_visible,
    retained_sources,
)
from metaspike.catalogue.generate import CatalogueState, generate_catalogue
from metaspike.catalogue.persistence import BROAD_DISCOVERY_ENABLED
from metaspike.catalogue.seed import CatalogueError, bootstrap_catalogue, load_seed_catalogue
from metaspike.community_sources.reddit import REDDIT_DATA_POLICY
from metaspike.community_sources.store import CommunityStore
from metaspike.community_sources.synthetic import build_synthetic_community
from metaspike.creator_sources.store import CreatorSupplyStore
from metaspike.creator_sources.synthetic import build_synthetic_creator_supply
from metaspike.creator_sources.youtube import YOUTUBE_DATA_POLICY
from metaspike.domain.samples import SampleStatus
from metaspike.domain.signals import CollectionStatus, SignalKey
from metaspike.domain.source_policy import DeploymentStage, SourcePolicy, SourcePurpose
from metaspike.domain.source_records import SourceRecord
from metaspike.domain.timeseries import Granularity
from metaspike.event_sources.candidates import (
    EVENT_CLASSIFICATION_RULES_V1,
    EVENT_CLASSIFICATION_RULES_V2,
)
from metaspike.event_sources.ingest import IngestReport, ingest, reclassify
from metaspike.event_sources.registry import (
    EVENT_SOURCE_POLICIES,
    build_steam_news,
    replay_news_client,
)
from metaspike.event_sources.resolve import steam_app_bindings, steam_news_resolver
from metaspike.event_sources.steam_news import (
    DEFAULT_COUNT,
    MAX_COUNT,
    NEWS_URL,
    STEAM_NEWS_POLICY,
)
from metaspike.event_sources.store import EventSourceStore
from metaspike.event_sources.summary import summarise
from metaspike.events.store import EventStore
from metaspike.evidence.assemble import (
    EvidenceInputs,
    assess_evidence,
    publication_refusal,
)
from metaspike.evidence.model import NO_INPUT, InputProvenance, Origin
from metaspike.evidence.provenance import local_store_provenance, recording_provenance
from metaspike.evidence.render import render
from metaspike.evidence.synthetic import (
    PAGEVIEW_PATTERNS,
    SCENARIO_NAME,
    build_synthetic_scenario,
    write_synthetic_pageviews,
)
from metaspike.jobs import commands as job_commands
from metaspike.opportunity.history import (
    MAX_UNIVERSE,
    EvaluationMode,
    HistoryRefused,
    evaluate_history,
    render_history,
)
from metaspike.opportunity.history_synthetic import SYNTHETIC_HISTORY, synthetic_history
from metaspike.opportunity.render import render_shortlist
from metaspike.opportunity.rules import shortlist as opportunity_shortlist
from metaspike.pageview_recorder import (
    MAX_GAMES,
    RecorderRefused,
    RecordingRefused,
    inspect_recording,
    plan_recording,
    record_pageviews,
    render_inspection,
    render_manifest,
    render_plan,
    require_usable_recording,
)
from metaspike.pipeline.collection import (
    CollectionReport,
    collect,
    latest_complete_day,
    window_ending,
)
from metaspike.pipeline.normalize import latest_due_period
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
from metaspike.publishing.build import EventInputs
from metaspike.publishing.event_views import event_as_of
from metaspike.publishing.publisher import to_json
from metaspike.publishing.schemas import export_schemas, stale_schemas
from metaspike.publishing.validation import ArtifactValidationError, validate_published
from metaspike.resolution.evaluate import evaluate
from metaspike.resolution.golden import load_golden_set
from metaspike.resolution.recorded import load_recorded_catalogue
from metaspike.scoring.v0 import ScoreInvariantError
from metaspike.source_readiness import assess_readiness, render_readiness
from metaspike.sources.base import SourceError
from metaspike.sources.catalogue_sources import (
    CATALOGUE_SOURCE_POLICIES,
    build_igdb,
    build_steam,
    default_limiter_pool,
    register_catalogue_sources,
)
from metaspike.sources.credentials import STEAM_WEB_API_KEY_ENV, Secret, secret_from_env
from metaspike.sources.igdb import LOOKUP_TABLES
from metaspike.sources.limiter import FileQuotaLedger, LimiterPool, RateLimiter
from metaspike.sources.registry import SourcePolicyError, SourceRegistry, default_registry
from metaspike.sources.replay import replay_client
from metaspike.sources.sampling_sources import (
    build_steam_players,
    replay_clock,
    replay_recorded_apps,
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
from metaspike.sources.wikimedia import WIKIMEDIA_PAGEVIEWS_POLICY, build_client
from metaspike.storage.base import StoragePolicyError
from metaspike.storage.local import LocalObservationStore
from metaspike.storage.samples import LocalSampleStore
from metaspike.validation.backtest import run_backtest
from metaspike.validation.report import ValidationReport
from metaspike.validation.store import AccountabilityStore

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
DEFAULT_EVENTS_DIR = Path(".metaspike/events")
DEFAULT_VALIDATION_DIR = Path(".metaspike/validation")
DEFAULT_EVENT_SOURCES_DIR = Path(".metaspike/event-sources")
# A supervised live look at an event source: never more than this many apps, and never
# more than one request for each.
LIVE_EVENT_PROBE_MAX_APPS = 3
EVENT_RULES = {
    rules.version: rules for rules in (EVENT_CLASSIFICATION_RULES_V1, EVENT_CLASSIFICATION_RULES_V2)
}
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
    build.add_argument("--events-dir", type=Path, default=DEFAULT_EVENTS_DIR)
    build.add_argument("--event-sources-dir", type=Path, default=DEFAULT_EVENT_SOURCES_DIR)
    build.add_argument("--validation-dir", type=Path, default=DEFAULT_VALIDATION_DIR)
    build.set_defaults(handler=_build)

    run = commands.add_parser("run", help="collect, score and publish in one step")
    _add_collection_options(run)
    run.add_argument("--artifacts-dir", type=Path, default=DEFAULT_ARTIFACTS_DIR)
    run.add_argument("--events-dir", type=Path, default=DEFAULT_EVENTS_DIR)
    run.add_argument("--event-sources-dir", type=Path, default=DEFAULT_EVENT_SOURCES_DIR)
    run.add_argument("--validation-dir", type=Path, default=DEFAULT_VALIDATION_DIR)

    backtest = commands.add_parser(
        "backtest",
        help="walk forward over recorded history and score what was said against what followed",
        description=(
            "Offline only. Replays recorded responses, rebuilds what was known on each day "
            "of the range, and compares each day's ranking with what the same signal did "
            "afterwards. It never contacts a source, which is why --replay is required."
        ),
    )
    backtest.add_argument(
        "--replay",
        type=Path,
        required=True,
        metavar="DIR",
        help="recorded responses to replay; there is no live mode",
    )
    backtest.add_argument(
        "--recorded-through",
        type=date.fromisoformat,
        required=True,
        metavar="YYYY-MM-DD",
        help="the last day the recording covers",
    )
    backtest.add_argument(
        "--from",
        dest="first_day",
        type=date.fromisoformat,
        required=True,
        metavar="YYYY-MM-DD",
        help="first day a recommendation is simulated on, at 00:00 UTC",
    )
    backtest.add_argument(
        "--to", dest="last_day", type=date.fromisoformat, required=True, metavar="YYYY-MM-DD"
    )
    backtest.add_argument("--store-dir", type=Path, default=DEFAULT_VALIDATION_DIR)
    backtest.add_argument("--events-dir", type=Path, default=DEFAULT_EVENTS_DIR)
    backtest.set_defaults(handler=_backtest)

    history = commands.add_parser(
        "opportunity-history",
        help="evaluate creator_opportunity_rules_v1 over past moments, offline and private",
        description=(
            "Offline and private. Walks forward over declared moments, freezes what the "
            "opportunity rubric would have said with only what was visible then, and only "
            "afterwards attaches what attention did next, as an ATTENTION PROXY. Recorded "
            "mode replays a pageview recording; synthetic mode uses invented games and "
            "invented evidence, labelled throughout. It never contacts a source."
        ),
    )
    history_mode = history.add_mutually_exclusive_group(required=True)
    history_mode.add_argument(
        "--recorded", action="store_true", help="replay a recording (--replay, --recorded-through)"
    )
    history_mode.add_argument(
        "--synthetic", action="store_true", help="invented games and evidence; SYNTHETIC"
    )
    history.add_argument("--replay", type=Path, default=None, metavar="DIR")
    history.add_argument(
        "--recorded-through", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD"
    )
    history.add_argument(
        "--from",
        dest="first_day",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="first moment, at 00:00 UTC (required in recorded mode)",
    )
    history.add_argument(
        "--to", dest="last_day", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD"
    )
    history.add_argument("--cadence-days", type=int, default=1, metavar="N")
    history.add_argument(
        "--game",
        action="append",
        default=None,
        metavar="SLUG",
        help=f"a game in the universe; repeatable; at most {MAX_UNIVERSE}. Default: every "
        "game the recording holds a series for",
    )
    history.add_argument("--json", action="store_true", help="print the report as JSON")
    history.add_argument(
        "--out", type=Path, default=None, metavar="FILE", help="also write the JSON locally"
    )
    history.set_defaults(handler=_opportunity_history)

    readiness = commands.add_parser(
        "source-readiness",
        help="what each source can contribute today and what stops it; offline",
        description=(
            "Offline and private. Reads every source's declared policy, checks which "
            "adapters, replays and stores exist, and gives the standing of the committed "
            "recordings and default local stores. It contacts no source, reads no "
            "credential (variables are named, never read) and queries no cloud."
        ),
    )
    readiness.add_argument(
        "--root",
        type=Path,
        default=Path(),
        metavar="DIR",
        help="the repository root whose fixtures and local stores are read",
    )
    readiness.add_argument("--json", action="store_true", help="print the report as JSON")
    readiness.add_argument(
        "--out", type=Path, default=None, metavar="FILE", help="also write the JSON locally"
    )
    readiness.set_defaults(handler=_source_readiness)

    recorder = commands.add_parser(
        "record-pageviews",
        help="plan (default), or with explicit owner opt-in make, a supervised Wikimedia "
        "recording; or inspect one",
        description=(
            "Supervised and private. By default this only PLANS: it prints the exact "
            "requests and sends nothing. Sending them needs --live and --confirm with the "
            "plan's id, and is for an owner authorised to do so. A recording is "
            "UNVERIFIED until reviewed and pinned; nothing here pins. --inspect reads a "
            "recording back and checks it."
        ),
    )
    recorder.add_argument(
        "--game",
        action="append",
        default=None,
        metavar="SLUG",
        help=f"a bootstrap game to record; repeatable; at most {MAX_GAMES}",
    )
    recorder.add_argument(
        "--all-bootstrap",
        action="store_true",
        help="every bootstrap game with a Wikipedia article bound",
    )
    recorder.add_argument(
        "--through",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="the last day of the 90-day window; default: the latest day already published",
    )
    recorder.add_argument(
        "--out",
        type=Path,
        default=None,
        metavar="DIR",
        help="a new local directory for the recording; it must not exist",
    )
    recorder.add_argument(
        "--live",
        action="store_true",
        help="SEND the planned requests to Wikimedia. Owner use only, with --confirm",
    )
    recorder.add_argument(
        "--confirm",
        default=None,
        metavar="PLAN_ID",
        help="the plan id printed by the plan; --live refuses without the matching id",
    )
    recorder.add_argument(
        "--inspect", type=Path, default=None, metavar="DIR", help="check a recording"
    )
    recorder.add_argument("--json", action="store_true", help="print JSON")
    recorder.set_defaults(handler=_record_pageviews)

    steam = commands.add_parser(
        "record-steam-players",
        help="plan (default), or with explicit owner opt-in make, a supervised Steam "
        "concurrent-player recording for one slot; or inspect one",
        description=(
            "Supervised and private. By default this only PLANS: it prints the exact "
            "requests for the current 15-minute slot and sends nothing. Sending them needs "
            "--live and --confirm with the plan's id, inside the same slot, the key in "
            f"{STEAM_WEB_API_KEY_ENV}, and is for an owner authorised to do so. A recording "
            "is UNVERIFIED until reviewed and pinned; nothing here pins. --inspect reads a "
            "recording back and checks it."
        ),
    )
    steam.add_argument(
        "--app",
        action="append",
        default=None,
        metavar="APPID",
        help=f"an eligible bootstrap Steam app; repeatable; at most {steam_recorder.MAX_APPS}",
    )
    steam.add_argument("--all-eligible", action="store_true", help="every eligible bootstrap app")
    steam.add_argument(
        "--recorded", type=Path, nargs="+", default=list(DEFAULT_RECORDED_DIRECTORIES)
    )
    steam.add_argument("--state", type=Path, default=DEFAULT_CATALOGUE_STATE)
    steam.add_argument(
        "--out",
        type=Path,
        default=None,
        metavar="DIR",
        help="a new local directory for the recording; it must not exist",
    )
    steam.add_argument(
        "--live",
        action="store_true",
        help="SEND the planned requests to Steam. Owner use only, with --confirm",
    )
    steam.add_argument(
        "--confirm",
        default=None,
        metavar="PLAN_ID",
        help="the plan id printed by the plan; --live refuses without the matching id",
    )
    steam.add_argument(
        "--inspect", type=Path, default=None, metavar="DIR", help="check a recording"
    )
    steam.add_argument("--json", action="store_true", help="print JSON")
    steam.set_defaults(handler=_record_steam_players)
    run.set_defaults(handler=_run)

    check = commands.add_parser("validate-artifacts", help="validate published artifacts")
    check.add_argument("--artifacts-dir", type=Path, default=DEFAULT_ARTIFACTS_DIR)
    check.set_defaults(handler=_validate_artifacts)

    events = commands.add_parser(
        "ingest-events",
        help="read an event source's announcements into candidates; promote what passes",
        description=(
            "Fetch announcements for Steam apps bound to catalogue games, keep them as "
            "candidates, and promote to event observations only those whose type and "
            "timing are both established. Offline from recordings with --replay. --live "
            f"is a supervised probe: at most {LIVE_EVENT_PROBE_MAX_APPS} bound apps, one "
            "request each, no credential, and nothing of a post is printed."
        ),
    )
    events.add_argument("--source", choices=["steam-news"], default="steam-news")
    mode = events.add_mutually_exclusive_group(required=True)
    mode.add_argument("--replay", type=Path, metavar="DIR", help="answer from recorded files")
    mode.add_argument("--live", action="store_true", help="call the source: a bounded probe")
    mode.add_argument(
        "--reclassify",
        action="store_true",
        help="read the posts already kept in --event-sources-dir under --rules; sends nothing",
    )
    events.add_argument("--apps", type=int, nargs="+", required=True, metavar="APPID")
    events.add_argument("--count", type=int, default=DEFAULT_COUNT, help=f"posts, 1-{MAX_COUNT}")
    events.add_argument(
        "--backfill-requests",
        type=int,
        default=0,
        metavar="N",
        help="walk back up to N pages per app with the documented enddate (replay only)",
    )
    events.add_argument("--observed-at", type=datetime.fromisoformat, default=None)
    events.add_argument(
        "--rules",
        choices=sorted(EVENT_RULES),
        default=EVENT_CLASSIFICATION_RULES_V1.version,
        help="the classification rules version (default: v1, the frozen one)",
    )
    events.add_argument(
        "--recorded", type=Path, nargs="+", default=list(DEFAULT_RECORDED_DIRECTORIES)
    )
    events.add_argument("--event-sources-dir", type=Path, default=DEFAULT_EVENT_SOURCES_DIR)
    events.add_argument("--events-dir", type=Path, default=DEFAULT_EVENTS_DIR)
    events.add_argument("--quota-dir", type=Path, default=DEFAULT_QUOTA_DIR)
    events.set_defaults(handler=_ingest_events)

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

    evidence = commands.add_parser(
        "evidence",
        help="OFFLINE: what is held about each game at a moment, source by source",
        description=(
            "Read what the given local inputs held about each game as known at --as-of: "
            "its identity, each source's coverage and freshness, movements against each "
            "source's own history, known events, and what is missing. Offline and "
            "read-only: no source is called, no credential is read, nothing is stored. "
            "An input that is not given is reported as not collected. The result is a "
            "private reading, not an artifact, and is never published."
        ),
    )
    evidence.add_argument(
        "--as-of",
        type=datetime.fromisoformat,
        required=True,
        metavar="ISO-TIME",
        help="the moment to read at, with a UTC offset; nothing later is used",
    )
    evidence.add_argument(
        "--game", action="append", default=None, metavar="SLUG", help="a game; repeatable"
    )
    evidence.add_argument(
        "--all", action="store_true", help="every entity, not only the bootstrap games"
    )
    evidence.add_argument(
        "--recorded", type=Path, nargs="+", default=list(DEFAULT_RECORDED_DIRECTORIES)
    )
    evidence.add_argument(
        "--state",
        type=Path,
        default=None,
        metavar="FILE",
        help="a local catalogue state to read (never written); default: resolve the "
        "recorded records in memory, dated at their latest fetch",
    )
    evidence.add_argument(
        "--wikimedia-replay", type=Path, default=None, metavar="DIR", help="recorded pageviews"
    )
    evidence.add_argument(
        "--recorded-through",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="the last day the pageview recording holds (required with --wikimedia-replay)",
    )
    evidence.add_argument("--sample-store", type=Path, default=None, metavar="DIR")
    evidence.add_argument("--events-dir", type=Path, default=None, metavar="DIR")
    evidence.add_argument("--event-sources-dir", type=Path, default=None, metavar="DIR")
    evidence.add_argument(
        "--creator-supply-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="a local creator-supply store (read as found: its origin is unverified)",
    )
    evidence.add_argument(
        "--community-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="a local community-interest store (refused unless it holds no Reddit data)",
    )
    evidence.add_argument(
        "--synthetic-scenario",
        action="store_true",
        help="use INVENTED player samples and announcements, labelled synthetic "
        "throughout, in place of --sample-store and the event stores",
    )
    evidence.add_argument(
        "--stage",
        type=DeploymentStage,
        choices=list(DeploymentStage),
        default=DeploymentStage.PRIVATE_DEVELOPMENT,
        help="every input's source must clear this stage, or the reading is refused",
    )
    evidence.add_argument(
        "--opportunities",
        action="store_true",
        help="read the evidence through creator_opportunity_rules_v1 and print a "
        "private, explained shortlist instead of the evidence report",
    )
    evidence.add_argument("--json", action="store_true", help="print the report as JSON")
    evidence.add_argument(
        "--out", type=Path, default=None, metavar="FILE", help="also write the JSON locally"
    )
    evidence.set_defaults(handler=_evidence)

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
        help="resolve recorded source records into a LOCAL catalogue state file",
        description=(
            "Resolve recorded source records into a catalogue state file on local disk. "
            "This is local, offline state for development: it is not the production "
            "catalogue, it is not scoped as production is, and it does not move the "
            "production pointer. Every source of the records must clear the stated stage."
        ),
    )
    generate.add_argument(
        "--recorded", type=Path, nargs="+", default=list(DEFAULT_RECORDED_DIRECTORIES)
    )
    generate.add_argument("--state", type=Path, default=DEFAULT_CATALOGUE_STATE)
    generate.add_argument("--as-of", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD")
    generate.add_argument(
        "--stage",
        type=DeploymentStage,
        choices=list(DeploymentStage),
        required=True,
        help=(
            "the deployment stage this local state is generated for; every source of the "
            "records must clear it for catalogue metadata"
        ),
    )
    generate.set_defaults(handler=_generate_catalogue)

    expansion = commands.add_parser(
        "evaluate-catalogue",
        help="offline: what a broad catalogue would hold, from recorded source records",
        description=(
            "Resolve every recorded source record with the existing resolver and report, "
            "in counts, what broad discovery would admit, quarantine and leave as related "
            "products, and which monitoring sources could be asked about each entity. "
            "Offline and in memory: no source is called, nothing is stored and the "
            "catalogue pointer is not touched. Broad discovery itself stays off."
        ),
    )
    expansion.add_argument(
        "--recorded", type=Path, nargs="+", default=list(DEFAULT_RECORDED_DIRECTORIES)
    )
    expansion.add_argument(
        "--stage",
        type=DeploymentStage,
        choices=list(DeploymentStage),
        required=True,
        help="the deployment stage this evaluation runs at; every source must clear it",
    )
    expansion.add_argument("--as-of", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD")
    expansion.add_argument(
        "--cross-check",
        action="store_true",
        help="re-ask every question the all-pairs way and fail on any difference (slow)",
    )
    expansion.add_argument("--json", action="store_true", help="print the summary as JSON")
    expansion.set_defaults(handler=_evaluate_catalogue)

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

    job_commands.register(commands)
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


def _event_inputs(args: argparse.Namespace, report: CollectionReport) -> EventInputs:
    """What the local stores knew at the run's evaluation moment. Nothing is fetched.

    Events and coverage come from the event store, which holds only what was promoted.
    Each event source's counts come from the source store. Both are read as of the
    evaluation moment, so anything observed later takes no part. With neither store
    present every game's event intelligence is published as unavailable.
    """
    as_of = event_as_of(report)
    store = EventStore(args.events_dir)
    sources = EventSourceStore(args.event_sources_dir)
    return EventInputs(
        records=store.events(as_of),
        checks=store.checks(as_of),
        as_of=as_of,
        source_policies=EVENT_SOURCE_POLICIES,
        source_summaries=tuple(
            summarise(sources, policy, EVENT_CLASSIFICATION_RULES_V1.version, as_of)
            for policy in EVENT_SOURCE_POLICIES.values()
        ),
    )


def _ingest_events(args: argparse.Namespace) -> int:
    """One ingestion pass over an event source. Prints counts, never a post."""
    if args.observed_at is not None and args.observed_at.tzinfo is None:
        return _fail(EXIT_INVALID_CATALOGUE, "--observed-at must carry a UTC offset")
    apps = [str(app) for app in dict.fromkeys(args.apps)]
    records = _recorded(args.recorded)
    bindings = steam_app_bindings(generate_catalogue(records, as_of=_now()), records)
    if args.reclassify:
        report = reclassify(
            STEAM_NEWS_POLICY.source,
            steam_news_resolver(bindings),
            EventSourceStore(args.event_sources_dir),
            EventStore(args.events_dir),
            rules=EVENT_RULES[args.rules],
            source_game_ids=apps,
        )
        _print_ingest(report, live=False, budget=0, reclassified=True)
        print(f"\nSource store: {args.event_sources_dir}   Event store: {args.events_dir}")
        return EXIT_OK
    if args.live:
        if len(apps) > LIVE_EVENT_PROBE_MAX_APPS:
            return _fail(
                EXIT_SOURCE_POLICY,
                f"a live probe covers at most {LIVE_EVENT_PROBE_MAX_APPS} apps",
            )
        if args.backfill_requests:
            return _fail(EXIT_SOURCE_POLICY, "a live backfill is not approved; use --replay")
        unbound = [app for app in apps if app not in bindings]
        if unbound:
            return _fail(
                EXIT_SOURCE_POLICY,
                f"app(s) {', '.join(unbound)} are not bound to a catalogue game; a live "
                "probe uses known bindings only",
            )
    requests = len(apps) * max(1, args.backfill_requests)
    clock = _now if args.observed_at is None else (lambda: args.observed_at)
    pool = LimiterPool() if args.replay else LimiterPool(ledger=FileQuotaLedger(args.quota_dir))
    with replay_news_client(args.replay) if args.replay else build_client() as client:
        adapter = build_steam_news(client, pool, max_requests=requests, now=clock, count=args.count)
        report = ingest(
            adapter,
            STEAM_NEWS_POLICY,
            apps,
            steam_news_resolver(bindings),
            EventSourceStore(args.event_sources_dir),
            EventStore(args.events_dir),
            stage=DeploymentStage.PRIVATE_DEVELOPMENT,
            rules=EVENT_RULES[args.rules],
            backfill_requests=args.backfill_requests,
        )
    _print_ingest(report, live=args.live, budget=requests)
    print(f"\nSource store: {args.event_sources_dir}   Event store: {args.events_dir}")
    return EXIT_OK if report.fetches_failed == 0 else EXIT_SOURCE_UNAVAILABLE


def _print_ingest(
    report: IngestReport, live: bool, budget: int, reclassified: bool = False
) -> None:
    mode = "LIVE probe" if live else "offline replay"
    if reclassified:
        mode = "reclassification of kept posts, nothing sent"
    print(f"Event ingestion ({mode}): {report.source}, {report.rules_version}")
    print(f"  endpoint: GET {NEWS_URL}  (no key; body and author never read)")
    print(
        f"  requests sent: {report.requests} of a budget of {budget}; fetches ok "
        f"{report.fetches_ok}, failed {report.fetches_failed}, not sent {report.fetches_not_sent}"
    )
    for target in report.targets:
        game = target.canonical_game_id or "not bound to a catalogue game"
        errors = f"  errors: {', '.join(target.error_codes)}" if target.error_codes else ""
        print(
            f"    app {target.source_game_id:<9} requests {target.requests}  posts "
            f"{target.posts}  coverage {'recorded' if target.coverage_recorded else 'none'}"
            f"  {game}{errors}"
        )
    print(f"  response fields seen: envelope {list(report.observed_envelope_fields)}")
    print(f"                        post {list(report.observed_item_fields)}")
    print(f"  feeds seen: {report.observed_feed_names}")
    print(f"  tags seen:  {report.observed_tags}")
    print("\n  Posts are announcements, not events:")
    print(f"    posts observed            {report.posts_observed}")
    print(f"    posts unreadable          {report.posts_rejected}")
    print(f"    new post versions kept    {report.new_post_versions}")
    print(f"    candidates created        {report.candidates_created}")
    print(f"    candidates classified     {report.candidates_classified}")
    print(f"    candidates PROMOTED       {report.candidates_promoted}")
    print(f"    candidates not promoted   {report.candidates_not_promoted}")
    print(f"    refusals                  {report.refusals}")
    print(f"    promoted event types      {report.promoted_event_types}")
    print(f"    event observations added  {report.observations_appended}")
    print(f"    withdrawals added         {report.withdrawals_appended}")
    print(f"    coverage checks recorded  {report.coverage_checks_recorded}")
    if report.candidates_promoted == 0:
        print("  No candidate could safely become an event in this pass.")


def _backtest(args: argparse.Namespace) -> int:
    """Walk forward over a recording, keep the result, and say plainly what it is worth."""
    catalogue = load_seed_catalogue()
    started = _now()
    with replay_client(args.replay) as client:
        registry = default_registry(client)
        recording = collect(
            catalogue,
            registry,
            window_ending(args.recorded_through),
            observed_at=started,
            clock=_now,
        )
        lags = {
            entry.policy.source: entry.policy.operations.expected_publication_lag
            for entry in registry.entries()
        }
    if not recording.any_success:
        raise CollectionFailedError(f"no recorded response in {args.replay} could be replayed")
    events = EventStore(args.events_dir)
    clock = perf_counter()
    try:
        result = run_backtest(
            catalogue,
            recording,
            args.first_day,
            args.last_day,
            created_at=started,
            publication_lag=lags,
            event_store=events,
            # A private, local command: event data is used at this stage or not at all.
            event_source_policies=EVENT_SOURCE_POLICIES,
            stage=DeploymentStage.PRIVATE_DEVELOPMENT,
        )
    except ValueError as error:  # a range the recording cannot stand in for
        return _fail(EXIT_INVALID_CATALOGUE, f"backtest range: {error}")
    seconds = perf_counter() - clock
    store = AccountabilityStore(args.store_dir)
    new_snapshots = store.add_snapshots(result.snapshots)
    new_labels = store.add_labels(result.labels)
    new_report = store.add_report(result.report)
    _print_backtest(result.report, seconds)
    problems = store.audit(result.report)
    print(
        f"\n  Provenance: snapshots {result.report.snapshot_set_hash[:16]}…, "
        f"labels {result.report.label_set_hash[:16]}…  "
        f"({'store matches' if not problems else '; '.join(problems)})"
    )
    print(
        f"\nStore: {args.store_dir}  (+{new_snapshots} snapshots, +{new_labels} labels, "
        f"report {result.report.report_id} {'kept' if new_report else 'already present'})"
    )
    return EXIT_OK


def _signed(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.1%}"


def _print_backtest(report: ValidationReport, seconds: float) -> None:
    lags = ", ".join(
        f"{source} {hours:g} h" for source, hours in report.publication_lag_hours.items()
    )
    print(
        f"Backtest of recommendations made at 00:00 UTC, {report.first_prediction_day} to "
        f"{report.last_prediction_day} ({report.snapshot_dates} days), "
        f"{report.protocol_version} / {report.outcome_rules_version} / {report.score_version}"
    )
    print(
        f"  publication lag: {lags}; newest day visible {report.first_latest_due_day} to "
        f"{report.last_latest_due_day}; data through {report.data_through}"
    )
    print(
        f"  universe {report.universe_size} games; {report.games_ranked} ever ranked; "
        f"{report.recommendations} snapshots, {report.ranked} ranked, {report.unranked} unranked"
    )
    unavailable = ", ".join(f"{name} {count}" for name, count in report.primary_unavailable.items())
    print(f"  primary outcome evaluable for {report.primary_evaluable}; unavailable: {unavailable}")
    print(f"  ran in {seconds:.2f} s\n")
    print("  72h uplift vs the baseline known at the snapshot (mean / hit rate), by strategy")
    for strategy in report.strategies:
        cells = "  ".join(
            f"top-{group.k} {_signed(group.outcomes.mean)} / "
            f"{'n/a' if group.outcomes.hit_rate is None else f'{group.outcomes.hit_rate:.0%}'}"
            f" (n={group.outcomes.count})"
            for group in strategy.top_k
        )
        rho = strategy.association.mean_rho
        print(
            f"  {strategy.strategy:<20} {cells}  rank assoc "
            f"{'n/a' if rho is None else f'{rho:+.2f}'} over {strategy.association.days_used} days"
        )
    print(f"\n  EVIDENCE STATUS: {report.evidence.status.value}")
    print(f"  {report.evidence.conclusion}")
    passed = sum(check.passed for check in report.leakage_checks)
    print(f"  Leakage checks passed: {passed} of {len(report.leakage_checks)}")
    used = ", ".join(report.events.event_sources_used) or "none"
    print(f"  Event sources used by the backtest: {used}")
    print(f"  Events: {report.events.note}")


def _build(args: argparse.Namespace) -> int:
    snapshot = args.cache_dir / _SNAPSHOT_FILE
    if not snapshot.is_file():
        raise CollectionFailedError(f"no cached collection at {snapshot}; run `collect` first")
    report = CollectionReport.model_validate_json(snapshot.read_bytes())
    with build_client() as client:  # the registry needs an adapter; no request is made
        result = build_and_publish(
            load_seed_catalogue(),
            report,
            default_registry(client),
            args.artifacts_dir,
            _now(),
            _event_inputs(args, report),
            AccountabilityStore(args.validation_dir).latest_report(),
        )
    _print_summary(result, args.artifacts_dir)
    return EXIT_OK


def _run(args: argparse.Namespace) -> int:
    with _client(args) as client:
        registry = default_registry(client)
        report = _collect_report(args, registry)
        result = build_and_publish(
            load_seed_catalogue(),
            report,
            registry,
            args.artifacts_dir,
            _now(),
            _event_inputs(args, report),
            AccountabilityStore(args.validation_dir).latest_report(),
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


def _evaluate_catalogue(args: argparse.Namespace) -> int:
    """What a broad catalogue would hold, from records already on disk. Counts only."""
    records = _recorded(args.recorded)
    as_of = _now() if args.as_of is None else datetime.combine(args.as_of, time.min, tzinfo=UTC)
    try:
        evaluation = evaluate_broad_catalogue(
            records,
            policies=CATALOGUE_SOURCE_POLICIES,
            stage=args.stage,
            as_of=as_of,
            cross_check=args.cross_check,
        )
    except ValueError as error:  # an as_of earlier than the records it would stamp
        return _fail(EXIT_INVALID_CATALOGUE, str(error))
    summary = evaluation.summary
    if args.json:
        print(summary.model_dump_json(indent=1))
        return EXIT_OK
    coverage, work = summary.coverage, summary.broad_work
    print(f"Catalogue evaluation ({summary.scope}), stage {summary.stage.value}")
    print("  Offline and in memory. No source called; nothing stored; CURRENT untouched.")
    print(f"  Broad discovery in production: {'ON' if BROAD_DISCOVERY_ENABLED else 'off'}")
    print(f"  resolver {summary.resolver_version}, evaluated at {summary.as_of:%Y-%m-%d %H:%M} UTC")
    for source in summary.sources:
        print(
            f"    {source.source:<16} {source.records:>6} records   cleared for: "
            f"{', '.join(source.cleared_stages)}   policy reviewed {source.reviewed_on}"
        )
    print("\n  Source records")
    print(f"    supplied                         {summary.source_records}")
    print(f"    in production's scope            {summary.records_in_production_scope}")
    print(f"    seen only by a broad evaluation  {summary.records_only_broad_sees}")
    print(f"    bound to an entity               {summary.bound_records}")
    print(f"      admitted as a new entity       {summary.records_admitted_as_new_entities}")
    print(f"      resolved to an existing entity {summary.records_resolved_to_existing_entities}")
    print(f"    quarantined (no identity)        {summary.quarantined_records}")
    print(f"      by reason                      {summary.quarantine_reasons}")
    print(f"    related products (not entities)  {summary.related_products}")
    print(f"      by type                        {summary.related_product_types}")
    print("\n  Canonical entities")
    print(f"    bootstrap                        {summary.bootstrap_entities}")
    print(f"    in production's scope            {summary.entities_in_production_scope}")
    print(f"    with a broad evaluation          {summary.canonical_entities}")
    print(f"    only in the broad scope          {summary.entities_only_in_broad_scope}")
    print("\n  Monitoring readiness (an identifier exists; not that anything is collected)")
    for name, states in coverage.by_source.items():
        print(
            f"    {name:<22} eligible {states['eligible']:>6}   inapplicable "
            f"{states['ineligible']:>6}   unknown {states['unknown']:>6}"
        )
    print(f"    ready for both                   {coverage.entities_ready_for_both}")
    print(f"    without any eligible source      {coverage.entities_without_an_eligible_source}")
    print("    Unknown and inapplicable mean no data. They never mean zero demand.")
    share = work.share_of_all_pairs
    print("\n  Work (broad pass)")
    print(f"    resolver calls                   {work.resolver_calls}")
    print(f"    entities examined                {work.entities_offered}")
    print(f"    all-pairs would examine          {work.entities_all_pairs}")
    print(f"    share of all-pairs               {'n/a' if share is None else f'{share:.2%}'}")
    print(f"    full scans                       {work.full_scans}")
    print(f"    cross-checked                    {work.cross_checked}")
    print(f"    seconds                          {work.seconds:.2f}")
    return EXIT_OK


LOCAL_STATE_KIND = "local_offline_catalogue_state"


def _provenance_path(state_path: Path) -> Path:
    return state_path.with_name(f"{state_path.stem}.provenance.json")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_local_state(
    path: Path,
    state: CatalogueState,
    records: Sequence[SourceRecord],
    policies: Mapping[str, SourcePolicy],
    stage: DeploymentStage,
) -> None:
    """Write a local catalogue state and, beside it, what it is and what it rests on.

    The state file alone looks like any catalogue state. The file beside it says that
    this one is local and offline, which stage it was generated for, and every source
    RETAINED in the state: not the sources of the latest batch of records, but every
    source that left something in it, read from the state itself. It carries the
    state file's digest, so that it describes that file and no other.

    It is a description, never a grant. Permission comes from the source policies, and
    is checked again every time the state is used.
    """
    retained = sorted(retained_sources(state))
    missing = [source for source in retained if source not in policies]
    if missing:
        raise StoragePolicyError(
            f"{', '.join(missing)}: retained in the state with no policy checked for it"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    body = to_json(state).encode("utf-8")
    path.write_bytes(body)
    by_source = Counter(record.key.source for record in records)
    provenance = {
        "kind": LOCAL_STATE_KIND,
        "is_production_catalogue": False,
        "note": (
            "Generated locally and offline from recorded source records, without "
            "production's scope. The production catalogue lives in the private state "
            "bucket under catalogue/CURRENT and is not this file. This file describes "
            "the state beside it; it grants nothing."
        ),
        "stage": stage.value,
        "generated_at": state.generated_at.isoformat(),
        "resolver_version": state.resolver_version,
        "state_file": path.name,
        "state_sha256": _sha256(body),
        # Every source that left a binding, a decision, a quarantine entry or a related
        # product in the state, whenever it was supplied.
        "retained_sources": retained,
        "sources": [
            {
                "source": source,
                "records_in_this_run": by_source[source],
                "purpose": SourcePurpose.CATALOGUE_METADATA.value,
                "cleared_stages": [
                    c.stage.value for c in policies[source].stage_clearance if c.cleared
                ],
                "policy_reviewed_on": policies[source].reviewed_at.isoformat(),
            }
            for source in retained
        ],
    }
    _provenance_path(path).write_text(json.dumps(provenance, indent=1) + "\n", encoding="utf-8")


def _load_local_state(path: Path) -> CatalogueState:
    """A previously written local state, only if what it rests on can be established.

    Refused, and left exactly as it is on disk, when: there is no provenance file (a
    state from before provenance was written); the provenance file is unreadable or is
    not for a local state; its digest is not this state file's; or the sources it
    declares are not the sources the state actually retains. A refused state is never
    repaired, rewritten or deleted here.

    Passing this says only that the state and its description belong together. The
    caller still has to clear every retained source for the stage it wants.
    """
    sidecar = _provenance_path(path)
    how = (
        f"Leave {path} where it is or move it aside, and generate a new state with "
        "`metaspike generate-catalogue --stage ... --state <a new path>` from recorded "
        "inputs that clear the stage."
    )
    if not sidecar.is_file():
        raise StoragePolicyError(
            f"{path} has no provenance file, so the sources it was made from cannot be "
            f"established. It will not be reused. {how}"
        )
    body = path.read_bytes()
    try:
        provenance = json.loads(sidecar.read_text(encoding="utf-8"))
    except ValueError:
        provenance = None
    if not isinstance(provenance, dict) or provenance.get("kind") != LOCAL_STATE_KIND:
        raise StoragePolicyError(f"{sidecar} is not the provenance of a local state. {how}")
    if provenance.get("is_production_catalogue") is not False:
        raise StoragePolicyError(f"{sidecar} does not describe a local state. {how}")
    if provenance.get("state_sha256") != _sha256(body):
        raise StoragePolicyError(
            f"{sidecar} does not describe {path.name} as it is now: the state's digest "
            f"differs. {how}"
        )
    try:
        state = CatalogueState.model_validate_json(body)
    except ValidationError as error:
        raise StoragePolicyError(f"{path} is not a readable catalogue state: {error}") from None
    retained = sorted(retained_sources(state))
    if provenance.get("retained_sources") != retained:
        raise StoragePolicyError(
            f"{sidecar} declares sources {provenance.get('retained_sources')!r}, but the "
            f"state retains {retained!r}. {how}"
        )
    return state


def _generate_catalogue(args: argparse.Namespace) -> int:
    """Resolve recorded records into LOCAL state. Policy-gated; not the production catalogue."""
    records = _recorded(args.recorded)
    # Before anything is resolved or written: every source must clear the stated stage.
    require_catalogue_sources(records, CATALOGUE_SOURCE_POLICIES, args.stage)
    previous = _load_local_state(args.state) if args.state.is_file() else None
    # A state being continued still holds what earlier sources put into it. Those
    # sources are held to this stage too, whether or not today's records include them.
    retained = frozenset() if previous is None else retained_sources(previous)
    policies = require_catalogue_sources(
        records, CATALOGUE_SOURCE_POLICIES, args.stage, retained=retained
    )
    as_of = _now() if args.as_of is None else datetime.combine(args.as_of, time.min, tzinfo=UTC)
    try:
        # as_of dates the bindings made here; it cannot be earlier than their evidence,
        # or than the state this run builds on.
        require_visible(records, as_of)
        if previous is not None:
            if as_of < previous.generated_at:
                raise ValueError(
                    f"as_of ({as_of.isoformat()}) is earlier than the state being "
                    f"continued ({previous.generated_at.isoformat()}); a generation "
                    "cannot be dated before the one it builds on"
                )
            # The engine reads its records as all the evidence there is.
            require_complete(records, known_record_keys(previous))
    except ValueError as error:
        return _fail(EXIT_INVALID_CATALOGUE, str(error))
    state = generate_catalogue(records, as_of=as_of, previous=previous)
    _write_local_state(args.state, state, records, policies, args.stage)
    metrics = state.quarantine.metrics(as_of)
    created = len(state.catalogue.games) - (
        len(bootstrap_catalogue().games) if previous is None else len(previous.catalogue.games)
    )
    print(f"LOCAL catalogue state, stage {args.stage.value}. Not the production catalogue.")
    print(f"Sources retained in the state: {', '.join(sorted(policies)) or 'none'}")
    print(
        f"Catalogue: {len(state.catalogue.games)} entities ({created} new this run), "
        f"{len(state.mapping.bindings)} source bindings, "
        f"{len(state.related_products)} related products"
    )
    print(f"Quarantine: {metrics.total} records  by reason {metrics.by_reason}")
    print(f"Decisions logged: {len(state.decisions.decisions)}  resolver {state.resolver_version}")
    print(f"State: {args.state}   Provenance: {_provenance_path(args.state)}")
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


def _load_or_generate_state(
    path: Path, records: list[SourceRecord], stage: DeploymentStage
) -> CatalogueState:
    """The persisted LOCAL catalogue state, so bindings keep the dates they were made on.

    Whether it is read or generated, everything it rests on must clear `stage` for
    catalogue metadata: the records supplied now, and every source retained in a state
    that is being reused. The caller states the stage; nothing is assumed here.

    A reused state must also be given every record it was made from, exactly as
    `generate-catalogue` requires. The state's bindings are then read beside `records`
    to decide which Steam apps are eligible, and bindings made from one set of records
    must not be judged against a different, smaller one. Raises `IncompleteRecords`
    otherwise, having changed nothing.
    """
    require_catalogue_sources(records, CATALOGUE_SOURCE_POLICIES, stage)
    if path.is_file():
        state = _load_local_state(path)
        require_catalogue_sources(
            records, CATALOGUE_SOURCE_POLICIES, stage, retained=retained_sources(state)
        )
        require_complete(records, known_record_keys(state))
        return state
    state = generate_catalogue(records, as_of=_now())
    policies = require_catalogue_sources(
        records, CATALOGUE_SOURCE_POLICIES, stage, retained=retained_sources(state)
    )
    _write_local_state(path, state, records, policies, stage)
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
    # Sampling is a private-development command: its policy clears no other stage, and
    # the local catalogue state it keeps is held to the same one, stated here.
    try:
        state = _load_or_generate_state(args.state, records, DeploymentStage.PRIVATE_DEVELOPMENT)
    except IncompleteRecords as error:
        # Refused before any target is chosen, any client is opened or anything is written.
        return _fail(EXIT_INVALID_CATALOGUE, f"catalogue state cannot be reused: {error}")
    eligibility = steam_eligibility(state, records)
    targets = sampling_targets(state, eligibility, bootstrap_scope())
    scheduled_for = args.scheduled_for
    if scheduled_for is not None and scheduled_for.tzinfo is None:
        scheduled_for = scheduled_for.replace(tzinfo=UTC)
    replaying = args.replay is not None
    clock = _now
    unrecorded: list[str] = []
    if replaying:
        # A replay answers only what was recorded, dated when it was recorded. An app
        # with no recording is not asked: replaying it would invent a failed attempt.
        try:
            clock = replay_clock(args.replay)
        except ValueError as error:
            return _fail(EXIT_COLLECTION_FAILED, str(error))
        if scheduled_for is not None and scheduled_for > clock():
            return _fail(
                EXIT_COLLECTION_FAILED,
                "a replay is dated when it was recorded: --scheduled-for is after the recording",
            )
        held = replay_recorded_apps(args.replay)
        unrecorded = sorted(
            (t.source_native_id for t in targets if t.source_native_id not in held), key=int
        )
        targets = tuple(t for t in targets if t.source_native_id in held)
    pool = LimiterPool() if replaying else default_limiter_pool()
    with replay_sampling_client(args.replay) if replaying else build_client() as client:
        adapter = build_steam_players(
            client, pool, now=clock, api_key=Secret("replay-no-key") if replaying else None
        )
        registry = sampling_registry(adapter)
        store = LocalSampleStore(args.store_dir, registry.policy)
        report = run_sampling_slot(targets, adapter, store, now=clock, scheduled_for=scheduled_for)
    _print_sampling(report, len(targets))
    if replaying:
        print(f"Not sampled (no recording): {', '.join(unrecorded) or '-'}")
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


EVIDENCE_POLICIES: Mapping[str, SourcePolicy] = {
    **CATALOGUE_SOURCE_POLICIES,
    **EVENT_SOURCE_POLICIES,
    YOUTUBE_DATA_POLICY.source: YOUTUBE_DATA_POLICY,
    REDDIT_DATA_POLICY.source: REDDIT_DATA_POLICY,
    WIKIMEDIA_PAGEVIEWS_POLICY.source: WIKIMEDIA_PAGEVIEWS_POLICY,
    STEAM_CURRENT_PLAYERS_POLICY.source: STEAM_CURRENT_PLAYERS_POLICY,
}
_EVALUATION_ID_BASE = 0xE71DE9CE_0000_4000_8000_000000000000


def _evaluation_id_factory() -> Callable[[], uuid.UUID]:
    """Ids for entities resolved in memory for one reading: the same on every run.

    They are stored nowhere and stand for nothing outside the reading, which says so
    for each such entity. The bootstrap entities keep their own frozen ids.
    """
    issued = [0]

    def factory() -> uuid.UUID:
        issued[0] += 1
        return uuid.UUID(int=_EVALUATION_ID_BASE + issued[0], version=4)

    return factory


def _evidence_state(
    args: argparse.Namespace, records: list[SourceRecord]
) -> tuple[CatalogueState, bool]:
    """The catalogue state to read identity from, and whether it came from disk."""
    require_catalogue_sources(records, CATALOGUE_SOURCE_POLICIES, args.stage)
    if args.state is not None:
        state = _load_local_state(args.state)
        require_catalogue_sources(
            records, CATALOGUE_SOURCE_POLICIES, args.stage, retained=retained_sources(state)
        )
        require_complete(records, known_record_keys(state))
        return state, True
    if not records:
        raise ValueError("no recorded source records were found in --recorded")
    dated = max(record.fetched_at for record in records)
    return generate_catalogue(records, as_of=dated, id_factory=_evaluation_id_factory()), False


def _evidence(args: argparse.Namespace) -> int:
    """A private, offline reading of what is held about each game at one moment."""
    as_of: datetime = args.as_of
    if as_of.tzinfo is None:
        return _fail(EXIT_INVALID_CATALOGUE, "--as-of must carry a UTC offset")
    given = [
        args.sample_store,
        args.events_dir,
        args.event_sources_dir,
        args.creator_supply_dir,
        args.community_dir,
    ]
    if args.synthetic_scenario and any(path is not None for path in given):
        return _fail(
            EXIT_INVALID_CATALOGUE,
            "--synthetic-scenario replaces --sample-store, --events-dir, "
            "--event-sources-dir, --creator-supply-dir and --community-dir; synthetic and "
            "stored evidence are never mixed",
        )
    if args.out is not None and _inside(args.out, DEFAULT_ARTIFACTS_DIR):
        return _fail(
            EXIT_SOURCE_POLICY,
            f"{args.out} is inside the published artifacts directory; a game evidence "
            "report is private and is never written there",
        )
    if (args.wikimedia_replay is None) != (args.recorded_through is None):
        return _fail(
            EXIT_INVALID_CATALOGUE,
            "--wikimedia-replay and --recorded-through are given together or not at all",
        )
    records = _recorded(args.recorded)
    try:
        state, persisted = _evidence_state(args, records)
    except (IncompleteRecords, ValueError) as error:
        return _fail(EXIT_INVALID_CATALOGUE, f"catalogue state: {error}")
    if as_of < state.generated_at:
        return _fail(
            EXIT_INVALID_CATALOGUE,
            f"--as-of ({as_of.isoformat()}) is earlier than the catalogue state "
            f"({state.generated_at.isoformat()}); the catalogue it describes did not exist then",
        )
    try:
        games = _evidence_games(args, state)
    except ValueError as error:
        return _fail(EXIT_INVALID_CATALOGUE, str(error))

    wikimedia = None
    pageviews = NO_INPUT
    if args.wikimedia_replay is not None:
        # Served from files, as the backtest reads them: the window is the recording's,
        # and what was not yet published at --as-of is cut away when it is read. The
        # clock is --as-of itself, so the same inputs give the same reading.
        with replay_client(args.wikimedia_replay) as client:
            wikimedia = collect(
                state.catalogue,
                default_registry(client),
                window_ending(args.recorded_through),
                observed_at=as_of,
                clock=lambda: as_of,
            )
        # Verified only if the directory's content matches a pinned recording.
        pageviews = recording_provenance(
            [args.wikimedia_replay], f"recorded through {args.recorded_through.isoformat()}"
        )
        try:
            # Checked again inside assess_evidence; here it fails with a clear message.
            require_usable_recording(pageviews, as_of)
        except RecordingRefused as error:
            return _fail(EXIT_SOURCE_POLICY, str(error))

    def stores(
        samples_dir: Path | None,
        events_dir: Path | None,
        sources_dir: Path | None,
        creators_dir: Path | None,
        community_dir: Path | None,
        synthetic: bool,
    ) -> EvidenceInputs:
        def origin_of(reference: str) -> InputProvenance:
            if synthetic:
                return InputProvenance(
                    origin=Origin.SYNTHETIC,
                    reference=SCENARIO_NAME,
                    note="invented; discarded after this run",
                )
            return local_store_provenance(reference)

        return EvidenceInputs(
            state=state,
            records=records,
            # A local state file is a local store: its bindings are read as found.
            catalogue_provenance=local_store_provenance(str(args.state))
            if persisted
            else recording_provenance(args.recorded, "state resolved in memory"),
            state_persisted=persisted,
            stage=args.stage,
            policies=EVIDENCE_POLICIES,
            wikimedia=wikimedia,
            wikimedia_provenance=pageviews,
            samples=None
            if samples_dir is None
            else LocalSampleStore(samples_dir, lambda _: STEAM_CURRENT_PLAYERS_POLICY),
            samples_provenance=NO_INPUT if samples_dir is None else origin_of(str(samples_dir)),
            events=None if events_dir is None else EventStore(events_dir),
            event_sources=None if sources_dir is None else EventSourceStore(sources_dir),
            events_provenance=NO_INPUT
            if events_dir is None and sources_dir is None
            else origin_of(", ".join(str(p) for p in (events_dir, sources_dir) if p is not None)),
            creator_supply=None if creators_dir is None else CreatorSupplyStore(creators_dir),
            creator_supply_provenance=NO_INPUT
            if creators_dir is None
            else origin_of(str(creators_dir)),
            community=None if community_dir is None else CommunityStore(community_dir),
            community_provenance=NO_INPUT
            if community_dir is None
            else origin_of(str(community_dir)),
        )

    scenario: tuple[str, ...] = ()
    if args.synthetic_scenario:
        # Checked before anything synthetic is written, as it is again when it is read.
        for policy, purpose in (
            (STEAM_CURRENT_PLAYERS_POLICY, SourcePurpose.DEMAND_SIGNALS),
            (STEAM_NEWS_POLICY, SourcePurpose.EVENT_INTELLIGENCE),
            (YOUTUBE_DATA_POLICY, SourcePurpose.CREATOR_SUPPLY),
        ):
            refusal = policy.registration_refusal(purpose, args.stage)
            if refusal is not None:
                raise SourcePolicyError(f"{policy.source}: {refusal}")
        with tempfile.TemporaryDirectory(prefix="metaspike-synthetic-") as temporary:
            root = Path(temporary)
            built = build_synthetic_scenario(root, state, records, as_of)
            named = {
                game.slug: (game.canonical_game_id, game.name) for game in state.catalogue.games
            }
            built_pages: tuple[str, ...] = ()
            if args.wikimedia_replay is None:
                # No recording asked for: invented pageviews, labelled as invented.
                due = latest_due_period(
                    Granularity.DAILY,
                    as_of,
                    WIKIMEDIA_PAGEVIEWS_POLICY.operations.expected_publication_lag,
                ).date()
                articles = {
                    identifier.value: PAGEVIEW_PATTERNS[game.slug]
                    for game in state.catalogue.games
                    if game.slug in PAGEVIEW_PATTERNS
                    for identifier in game.source_identifiers
                    if identifier.source == WIKIMEDIA_PAGEVIEWS_POLICY.source
                }
                built_pages = tuple(write_synthetic_pageviews(root / "pageviews", articles, due))
                with replay_client(root / "pageviews") as client:
                    wikimedia = collect(
                        state.catalogue,
                        default_registry(client),
                        window_ending(due),
                        observed_at=as_of,
                        clock=lambda: as_of,
                    )
                pageviews = InputProvenance(
                    origin=Origin.SYNTHETIC,
                    reference=SCENARIO_NAME,
                    note="invented; discarded after this run",
                )
            creators = build_synthetic_creator_supply(root, named, as_of)
            communities = build_synthetic_community(root, named, as_of)
            scenario = (
                *built_pages,
                *built.description,
                *creators.description,
                *communities.description,
            )
            inputs = stores(
                root / "samples",
                root / "events",
                root / "event-sources",
                creators.store.root,
                communities.store.root,
                synthetic=True,
            )
            report = assess_evidence(inputs, as_of, games)
    else:
        inputs = stores(
            args.sample_store,
            args.events_dir,
            args.event_sources_dir,
            args.creator_supply_dir,
            args.community_dir,
            synthetic=False,
        )
        report = assess_evidence(inputs, as_of, games)

    if args.opportunities:
        found = opportunity_shortlist(report)
        listed = found.model_dump_json(indent=1)
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(listed + "\n", encoding="utf-8")
        if args.json:
            print(listed)
        else:
            print(render_shortlist(found, scenario), end="")
            print(f"Not publishable: {publication_refusal(report)}.")
        return EXIT_OK
    payload = report.model_dump_json(indent=1)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
    if args.json:
        print(payload)
    else:
        print(render(report, scenario), end="")
        print(f"Not publishable: {publication_refusal(report)}.")
        if args.out is not None:
            print(f"Local JSON: {args.out}")
    return EXIT_OK


def _live_transport() -> httpx.BaseTransport:
    """The only place a recorder transport that reaches the network is built."""
    return httpx.HTTPTransport()


# How the live recorder waits between requests. Replaced in tests, never by a flag.
_recorder_sleep: Callable[[float], None] = time_module.sleep


def _record_pageviews(args: argparse.Namespace) -> int:
    """Plan a supervised recording; make it only on explicit opt-in; or inspect one."""
    catalogue = load_seed_catalogue()
    if args.inspect is not None:
        if args.live or args.game or args.all_bootstrap or args.out is not None:
            return _fail(EXIT_INVALID_CATALOGUE, "--inspect takes nothing else")
        inspection = inspect_recording(args.inspect, catalogue)
        if args.json:
            print(inspection.model_dump_json(indent=1))
        else:
            print(render_inspection(inspection), end="")
        # 0: intact, complete, ready for owner review. 3: intact but partial or failed,
        # diagnostic only. 4: the files do not hold together.
        if inspection.problems:
            return EXIT_INVALID_OUTPUT
        return EXIT_OK if inspection.review_ready else EXIT_COLLECTION_FAILED
    if args.game and args.all_bootstrap:
        return _fail(EXIT_INVALID_CATALOGUE, "--game and --all-bootstrap cannot be combined")
    if args.out is None:
        return _fail(EXIT_INVALID_CATALOGUE, "--out names the new recording directory")
    scope = bootstrap_scope()
    slugs = args.game or (
        sorted(
            game.slug
            for game in catalogue.games
            if game.canonical_game_id in scope
            and any(i.source == WIKIMEDIA_PAGEVIEWS_POLICY.source for i in game.source_identifiers)
        )
        if args.all_bootstrap
        else []
    )
    try:
        plan = plan_recording(catalogue, slugs, _now(), args.out, args.through)
    except RecorderRefused as error:
        return _fail(EXIT_SOURCE_POLICY, str(error))
    if not args.live:
        if args.confirm is not None:
            return _fail(EXIT_INVALID_CATALOGUE, "--confirm is only meaningful with --live")
        print(plan.model_dump_json(indent=1) if args.json else render_plan(plan), end="\n")
        return EXIT_OK
    if args.confirm != plan.plan_id:
        return _fail(
            EXIT_SOURCE_POLICY,
            "--live needs --confirm with this plan's id "
            f"({plan.plan_id}); review the plan first. Nothing was sent.",
        )
    try:
        result = record_pageviews(plan, catalogue, _live_transport(), _now, _recorder_sleep)
    except RecorderRefused as error:
        return _fail(EXIT_SOURCE_POLICY, str(error))
    if args.json:
        print(result.manifest.model_dump_json(indent=1))
    else:
        print(render_manifest(result.manifest, result.directory), end="")
    return EXIT_OK if result.manifest.status.value == "complete" else EXIT_COLLECTION_FAILED


def _record_steam_players(args: argparse.Namespace) -> int:
    """Plan a supervised Steam session; make it only on explicit opt-in; or inspect one."""
    if args.inspect is not None:
        if args.live or args.app or args.all_eligible or args.out is not None:
            return _fail(EXIT_INVALID_CATALOGUE, "--inspect takes nothing else")
        inspection = steam_recorder.inspect_recording(args.inspect)
        if args.json:
            print(inspection.model_dump_json(indent=1))
        else:
            print(steam_recorder.render_inspection(inspection), end="")
        # 0: intact, complete, ready for owner review. 3: intact but partial or failed,
        # diagnostic only. 4: the files do not hold together.
        if inspection.problems:
            return EXIT_INVALID_OUTPUT
        return EXIT_OK if inspection.review_ready else EXIT_COLLECTION_FAILED
    if args.app and args.all_eligible:
        return _fail(EXIT_INVALID_CATALOGUE, "--app and --all-eligible cannot be combined")
    if args.out is None:
        return _fail(EXIT_INVALID_CATALOGUE, "--out names the new recording directory")
    records = _recorded(args.recorded)
    try:
        state = _load_or_generate_state(args.state, records, DeploymentStage.PRIVATE_DEVELOPMENT)
    except IncompleteRecords as error:
        return _fail(EXIT_INVALID_CATALOGUE, f"catalogue state cannot be reused: {error}")
    planned_at = _now()
    apps = args.app or (
        [
            t.source_native_id
            for t in sampling_targets(
                state, steam_eligibility(state, records, as_of=planned_at), bootstrap_scope(),
                as_of=planned_at,
            )
        ]
        if args.all_eligible
        else []
    )  # fmt: skip
    try:
        plan = steam_recorder.plan_recording(state, records, apps, planned_at, args.out)
    except RecorderRefused as error:
        return _fail(EXIT_SOURCE_POLICY, str(error))
    if not args.live:
        if args.confirm is not None:
            return _fail(EXIT_INVALID_CATALOGUE, "--confirm is only meaningful with --live")
        print(plan.model_dump_json(indent=1) if args.json else steam_recorder.render_plan(plan))
        return EXIT_OK
    if args.confirm != plan.plan_id:
        return _fail(
            EXIT_SOURCE_POLICY,
            "--live needs --confirm with this slot's plan id "
            f"({plan.plan_id}); review the plan first. Nothing was sent.",
        )
    try:
        key = secret_from_env(STEAM_WEB_API_KEY_ENV)
    except SourceError as error:
        return _fail(EXIT_SOURCE_UNAVAILABLE, f"{error.message}. Nothing was sent.")
    try:
        result = steam_recorder.record_players(plan, _live_transport(), key, _now, _recorder_sleep)
    except RecorderRefused as error:
        return _fail(EXIT_SOURCE_POLICY, str(error))
    if args.json:
        print(result.manifest.model_dump_json(indent=1))
    else:
        print(steam_recorder.render_manifest(result.manifest, result.directory), end="")
    return EXIT_OK if result.manifest.status.value == "complete" else EXIT_COLLECTION_FAILED


def _source_readiness(args: argparse.Namespace) -> int:
    """Each source's readiness, read offline. Private: never written where it is served."""
    if args.out is not None and (
        _inside(args.out, DEFAULT_ARTIFACTS_DIR) or "public" in args.out.resolve().parts
    ):
        return _fail(
            EXIT_SOURCE_POLICY,
            f"{args.out} is in a published or served directory; a readiness report is "
            "private and is never written there",
        )
    report = assess_readiness(args.root)
    payload = report.model_dump_json(indent=1)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
    if args.json:
        print(payload)
    else:
        print(render_readiness(report), end="")
        if args.out is not None:
            print(f"Local JSON: {args.out}")
    return EXIT_OK


def _opportunity_history(args: argparse.Namespace) -> int:
    """The rubric over past moments, then what attention did next. Private; offline."""
    if args.out is not None and (
        _inside(args.out, DEFAULT_ARTIFACTS_DIR) or "public" in args.out.resolve().parts
    ):
        return _fail(
            EXIT_SOURCE_POLICY,
            f"{args.out} is in a published or served directory; a historical opportunity "
            "evaluation is private and is never written there",
        )
    started = _now()
    if args.synthetic:
        if args.replay is not None or args.recorded_through is not None:
            return _fail(
                EXIT_INVALID_CATALOGUE,
                "--synthetic takes no recording; synthetic and recorded data are never mixed",
            )
        invented = synthetic_history()
        catalogue, recording, lags = (
            invented.catalogue,
            invented.recording,
            invented.publication_lag,
        )
        context, origin, mode = invented.context, SYNTHETIC_HISTORY, EvaluationMode.SYNTHETIC
        first_day = args.first_day or invented.first_day
        last_day = args.last_day or invented.last_day
    else:
        if args.replay is None or args.recorded_through is None:
            return _fail(EXIT_INVALID_CATALOGUE, "--recorded needs --replay and --recorded-through")
        if args.first_day is None or args.last_day is None:
            return _fail(EXIT_INVALID_CATALOGUE, "--recorded needs an explicit --from and --to")
        catalogue = load_seed_catalogue()
        with replay_client(args.replay) as client:
            registry = default_registry(client)
            recording = collect(
                catalogue,
                registry,
                window_ending(args.recorded_through),
                observed_at=started,
                clock=_now,
            )
            lags = {
                entry.policy.source: entry.policy.operations.expected_publication_lag
                for entry in registry.entries()
            }
        if not recording.any_success:
            raise CollectionFailedError(f"no recorded response in {args.replay} could be replayed")
        context, mode = (), EvaluationMode.RECORDED
        # Verified only if the directory's content matches a pinned recording.
        origin = recording_provenance(
            [args.replay], f"recorded through {args.recorded_through.isoformat()}"
        )
        first_day, last_day = args.first_day, args.last_day
    universe = args.game or sorted(
        game.slug
        for game in catalogue.games
        if any(
            signal.canonical_game_id == game.canonical_game_id and signal.series is not None
            for run in recording.runs
            for signal in run.outcomes
        )
    )
    try:
        report = evaluate_history(
            catalogue,
            recording,
            lags,
            first_day,
            last_day,
            args.cadence_days,
            universe,
            origin,
            mode,
            created_at=started,
            context=context,
        )
    except HistoryRefused as error:
        return _fail(EXIT_SOURCE_POLICY, str(error))
    except ValueError as error:
        return _fail(EXIT_INVALID_CATALOGUE, str(error))
    payload = report.model_dump_json(indent=1)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
    if args.json:
        print(payload)
    else:
        if args.synthetic:
            print("\n".join(invented.description))
        print(render_history(report), end="")
        if args.out is not None:
            print(f"Local JSON: {args.out}")
    return EXIT_OK


def _inside(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
    except ValueError:
        return False
    return True


def _evidence_games(args: argparse.Namespace, state: CatalogueState) -> list[str] | None:
    """The games asked for, as canonical ids: named slugs, everything, or the bootstrap set."""
    if args.all and args.game:
        raise ValueError("--all and --game cannot be given together")
    if args.all:
        return None
    by_slug = {game.slug: game.canonical_game_id for game in state.catalogue.games}
    if args.game:
        unknown = [slug for slug in args.game if slug not in by_slug]
        if unknown:
            raise ValueError(f"no game with slug: {', '.join(unknown)}")
        return [by_slug[slug] for slug in dict.fromkeys(args.game)]
    scope = bootstrap_scope()
    return [
        game.canonical_game_id for game in state.catalogue.games if game.canonical_game_id in scope
    ]


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
