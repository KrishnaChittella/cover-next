"""Supervised Wikimedia pageview recording: plan, acquire, keep, inspect.

The way a NEW recording of the real source is made, for an owner to review and, only
if they approve, to pin. It reuses the existing adapter, rate limiter, source policy,
replay format and acquisition records; it is not a collector and runs nothing on a
schedule.

    plan      the default. Chooses the targets (at most the 15 bootstrap games, named
              explicitly), the one 90-day window ending on a day Wikimedia had already
              published, and the exact requests. Opens no client and sends nothing.
    record    sends exactly the planned requests through the existing adapter, one at
              a time under the policy's documented limit, no retry, and stops at the
              first sign of trouble. Callers must hand it a transport: in tests that is
              a mock; a live transport is only ever built by the command line, behind
              an explicit opt-in that repeats the plan's id.
    inspect   reads a recording back: every body against its acquisition record, every
              file accounted for, the replay parsed by the existing reader, the digest
              a reviewer would pin, and the standing it has today.

**What a recording is.** The response bodies, byte for byte, saved as `<article>.json`
at the top of the directory: the format `replay_client` serves and `recording_digest`
hashes, exactly as for the recording already pinned. Beside them, under `acquisition/`,
and outside the digest: one acquisition record per kept body (`records.jsonl`) and a
manifest of the plan and of every request's outcome, including failures.

Only a 200 whose body the adapter parsed becomes a `<article>.json`. A 404 (no data),
an error, a rate limit or an invalid body is listed in the manifest and nothing is
invented in its place.

**Never written:** request headers (so no User-Agent contact, cookie or authorization),
response headers outside a short list of caching and content headers, environment
values, credentials. Wikimedia is keyless; nothing here reads a credential.

**Trust.** A new recording is UNVERIFIED. It becomes verified only when an owner has
reviewed it and its digest has been added to `PINNED_RECORDINGS` in a reviewed commit.
Nothing here pins, and there is no option to.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal
from urllib.parse import quote

import httpx
from pydantic import AwareDatetime, Field

from metaspike.catalogue.eligibility import bootstrap_scope
from metaspike.catalogue.legacy_ids import MIGRATED_AT
from metaspike.domain.games import CanonicalGame, Catalogue, SourceIdentifier
from metaspike.domain.model import FrozenModel
from metaspike.domain.signals import SourceErrorCode
from metaspike.domain.source_policy import DeploymentStage, SourcePurpose
from metaspike.domain.timeseries import Granularity
from metaspike.evidence import provenance as provenance_module
from metaspike.evidence.acquisition import (
    AcquisitionRecord,
    ContentMismatch,
    NativeBinding,
    check_content,
    policy_changed,
    policy_digest,
    record_acquisition,
    record_id_matches,
)
from metaspike.evidence.model import InputProvenance, Standing
from metaspike.evidence.provenance import recording_digest, recording_provenance, require_honest
from metaspike.features.attention import HISTORY_DAYS
from metaspike.pipeline.collection import CollectionReport, collect, window_ending
from metaspike.pipeline.normalize import latest_due_period
from metaspike.sources.base import SourceError
from metaspike.sources.http import AttemptBudget, RetryConfig
from metaspike.sources.registry import default_registry
from metaspike.sources.replay import replay_client
from metaspike.sources.wikimedia import (
    BASE_URL,
    DAILY_PAGEVIEWS,
    SOURCE_ID,
    WIKIMEDIA_PAGEVIEWS_POLICY,
    WikimediaPageviewsAdapter,
    user_agent,
)
from metaspike.validation.snapshot import content_id

RECORDING_FORMAT: Final = "wikimedia_recording_v1"
MAX_GAMES: Final = 15
WINDOW_DAYS: Final = HISTORY_DAYS
STAGE: Final = DeploymentStage.PRIVATE_DEVELOPMENT
PURPOSE: Final = SourcePurpose.DEMAND_SIGNALS
ACQUISITION_DIR: Final = "acquisition"
MANIFEST: Final = "manifest.json"
RECORDS: Final = "records.jsonl"
INCOMPLETE_SUFFIX: Final = ".incomplete"
# Response headers worth keeping for review. Nothing outside this list is written.
KEPT_HEADERS: Final = (
    "content-type",
    "content-length",
    "date",
    "etag",
    "last-modified",
    "cache-control",
    "age",
    "retry-after",
)
# Directories a recording may never be written into, by path component.
_FORBIDDEN_PARTS: Final = frozenset({"artifacts", "public", "fixtures", "infra", ".github", "apps"})
# An error that stops the run: further requests would only repeat or worsen it.
_STOPPING: Final = frozenset(
    {
        SourceErrorCode.RATE_LIMITED,
        SourceErrorCode.INVALID_RESPONSE,
        SourceErrorCode.HTTP_ERROR,
        SourceErrorCode.NETWORK_ERROR,
        SourceErrorCode.TIMEOUT,
        SourceErrorCode.QUOTA_EXHAUSTED,
        SourceErrorCode.MISSING_CREDENTIALS,
    }
)
TRUST_STATEMENT: Final = (
    "UNVERIFIED: a new recording is not evidence of anything until an owner has reviewed "
    "it and its digest has been pinned in a reviewed commit. An acquisition record proves "
    "the bytes are unchanged and that the policy permitted the collection; it proves "
    "neither that Wikimedia sent them nor any permission for public use."
)


class RecorderRefused(Exception):
    """The plan or the run must not proceed."""


class Target(FrozenModel):
    slug: str
    canonical_game_id: str
    namespace: str
    article: str
    # The binding the request is made under, as it stood when the plan was made.
    bound_from: AwareDatetime


class PlannedRequest(FrozenModel):
    slug: str
    article: str
    method: Literal["GET"] = "GET"
    url: str
    path: str


class RecordingPlan(FrozenModel):
    plan_id: str = Field(pattern=r"^plan_[0-9a-f]{32}$")
    format: Literal["wikimedia_recording_v1"] = RECORDING_FORMAT
    source: str = SOURCE_ID
    purpose: SourcePurpose = PURPOSE
    stage: DeploymentStage = STAGE
    policy_reviewed_at: date
    policy_digest: str
    # When the plan was made. Not part of the id: the same scope planned again is the
    # same plan, as long as it asks for the same days.
    planned_at: AwareDatetime
    # The newest day Wikimedia had published at `planned_at`, under its policy's lag.
    latest_due_day: date
    publication_lag_hours: float
    window_start: date
    window_end: date
    targets: tuple[Target, ...]
    requests: tuple[PlannedRequest, ...]
    max_requests: int
    rate_limit: str
    max_concurrency: int
    retries: int
    destination: str


class TargetStatus(StrEnum):
    RECORDED = "recorded"
    NO_DATA = "no_data"  # 404: the source holds nothing for this article and range
    FAILED = "failed"  # anything else; nothing is kept
    NOT_ATTEMPTED = "not_attempted"  # the run had stopped before it


class Exchange(FrozenModel):
    """One request and what came back. Never the request's headers."""

    method: str
    path: str
    query: tuple[tuple[str, str], ...]
    requested_at: AwareDatetime
    received_at: AwareDatetime
    status: int
    headers: tuple[tuple[str, str], ...]
    body_bytes: int


class TargetOutcome(FrozenModel):
    slug: str
    article: str
    status: TargetStatus
    error_code: str | None = None
    error: str | None = None
    exchange: Exchange | None = None
    file: str | None = None
    record_id: str | None = None


class RunStatus(StrEnum):
    COMPLETE = "complete"  # every target answered: recorded or no data
    PARTIAL = "partial"  # some recorded, then a target failed or the run stopped
    FAILED = "failed"  # nothing recorded


class Manifest(FrozenModel):
    format: Literal["wikimedia_recording_v1"] = RECORDING_FORMAT
    plan: RecordingPlan
    status: RunStatus
    stopped_because: str | None = None
    outcomes: tuple[TargetOutcome, ...]
    requests_sent: int
    # The digest a reviewer would pin, over the bodies only. NOT pinned.
    bodies_digest: str
    standing: Literal["unverified"] = "unverified"
    trust: str = TRUST_STATEMENT


# --- planning ----------------------------------------------------------------------------


def _wikimedia_identifier(game: CanonicalGame) -> SourceIdentifier | None:
    return next((i for i in game.source_identifiers if i.source == SOURCE_ID), None)


def request_url(identifier: SourceIdentifier, start: date, end: date) -> str:
    """The URL exactly as the adapter builds it."""
    return (
        f"{BASE_URL}/{identifier.namespace}/all-access/user/"
        f"{quote(identifier.value, safe='')}/daily/{start:%Y%m%d}/{end:%Y%m%d}"
    )


def check_destination(destination: Path) -> None:
    """Refuse a place a recording must never go, or one that already holds something."""
    resolved = destination.resolve()
    forbidden = _FORBIDDEN_PARTS & set(resolved.parts)
    if forbidden:
        raise RecorderRefused(
            f"{destination} is inside {sorted(forbidden)[0]}/: a recording is private and "
            "is never written where it is published, served or pinned"
        )
    incomplete = resolved.with_name(resolved.name + INCOMPLETE_SUFFIX)
    for path in (resolved, incomplete):
        if path.exists():
            raise RecorderRefused(f"{path} already exists; a recording never overwrites")


def plan_recording(
    catalogue: Catalogue,
    slugs: Sequence[str],
    planned_at: datetime,
    destination: Path,
    through: date | None = None,
) -> RecordingPlan:
    """The exact requests a supervised run would send. Sends nothing."""
    if planned_at.tzinfo is None:
        raise RecorderRefused("the planning moment needs a time zone")
    refusal = WIKIMEDIA_PAGEVIEWS_POLICY.registration_refusal(PURPOSE, STAGE)
    if refusal is not None:
        raise RecorderRefused(f"{SOURCE_ID}: {refusal}")
    if not slugs:
        raise RecorderRefused("name the games to record; nothing is chosen by default")
    if len(set(slugs)) != len(slugs):
        raise RecorderRefused("a game is named more than once")
    if len(slugs) > MAX_GAMES:
        raise RecorderRefused(f"at most {MAX_GAMES} games may be recorded in one run")
    games = {game.slug: game for game in catalogue.games}
    scope = bootstrap_scope()
    targets = []
    for slug in slugs:
        game = games.get(slug)
        if game is None:
            raise RecorderRefused(f"no game with slug {slug}")
        if game.canonical_game_id not in scope:
            raise RecorderRefused(f"{slug} is not a bootstrap game; nothing else is recorded")
        identifier = _wikimedia_identifier(game)
        if identifier is None:
            raise RecorderRefused(f"{slug} has no Wikipedia article bound; nothing to request")
        targets.append(
            Target(
                slug=slug,
                canonical_game_id=game.canonical_game_id,
                namespace=identifier.namespace,
                article=identifier.value,
                bound_from=MIGRATED_AT,
            )
        )
    lag = WIKIMEDIA_PAGEVIEWS_POLICY.operations.expected_publication_lag
    due = latest_due_period(Granularity.DAILY, planned_at, lag).date()
    end = due if through is None else through
    if end > due:
        raise RecorderRefused(
            f"{end} had not been published by {planned_at.isoformat()} (latest due {due}); "
            "a recording asks only for days the source had published"
        )
    window = window_ending(end)
    start = window.start.date()
    assert (end - start).days + 1 == WINDOW_DAYS
    check_destination(destination)
    requests = []
    for target in sorted(targets, key=lambda t: t.slug):
        identifier = SourceIdentifier(
            source=SOURCE_ID, namespace=target.namespace, value=target.article
        )
        url = request_url(identifier, start, end)
        requests.append(
            PlannedRequest(
                slug=target.slug,
                article=target.article,
                url=url,
                path=httpx.URL(url).raw_path.decode(),
            )
        )
    limit = WIKIMEDIA_PAGEVIEWS_POLICY.operations.rate_limit
    fields: dict[str, object] = {
        "policy_reviewed_at": WIKIMEDIA_PAGEVIEWS_POLICY.reviewed_at,
        "policy_digest": policy_digest(WIKIMEDIA_PAGEVIEWS_POLICY),
        "planned_at": planned_at,
        "latest_due_day": due,
        "publication_lag_hours": lag.total_seconds() / 3600,
        "window_start": start,
        "window_end": end,
        "targets": tuple(sorted(targets, key=lambda t: t.slug)),
        "requests": tuple(requests),
        "max_requests": len(requests),
        "rate_limit": f"{limit.max_requests} per {limit.per_seconds}s ({limit.basis.value})",
        "max_concurrency": 1,
        "retries": 0,
        "destination": str(destination),
    }
    draft = RecordingPlan.model_validate({**fields, "plan_id": "plan_" + "0" * 32})
    identity = draft.model_dump(mode="json", exclude={"plan_id", "planned_at", "latest_due_day"})
    return draft.model_copy(update={"plan_id": content_id("plan", identity)})


# --- recording ---------------------------------------------------------------------------


@dataclass
class _Captured:
    request: httpx.Request
    requested_at: datetime
    received_at: datetime
    response: httpx.Response
    body: bytes


class RecordingTransport(httpx.BaseTransport):
    """Passes each request to `inner` and keeps what came back, body and all."""

    def __init__(self, inner: httpx.BaseTransport, clock: Callable[[], datetime]) -> None:
        self._inner = inner
        self._clock = clock
        self.captured: list[_Captured] = []
        # Every request handed on, answered or not: a connection failure is still sent.
        self.attempts = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        requested_at = self._clock()
        self.attempts += 1
        response = self._inner.handle_request(request)
        body = response.read()
        received_at = self._clock()
        self.captured.append(_Captured(request, requested_at, received_at, response, body))
        return httpx.Response(
            response.status_code, headers=response.headers, content=body, request=request
        )

    def close(self) -> None:
        self._inner.close()


def _exchange(captured: _Captured) -> Exchange:
    url = captured.request.url
    return Exchange(
        method=captured.request.method,
        path=url.raw_path.decode().split("?")[0],
        query=tuple(sorted(url.params.multi_items())),
        requested_at=captured.requested_at,
        received_at=captured.received_at,
        status=captured.response.status_code,
        headers=tuple(
            (name, captured.response.headers[name])
            for name in KEPT_HEADERS
            if name in captured.response.headers
        ),
        body_bytes=len(captured.body),
    )


@dataclass(frozen=True)
class RecordingResult:
    directory: Path
    manifest: Manifest
    records: tuple[AcquisitionRecord, ...]


def record_pageviews(
    plan: RecordingPlan,
    catalogue: Catalogue,
    transport: httpx.BaseTransport,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
) -> RecordingResult:
    """Send exactly the planned requests, keep what came back, and say what happened.

    The transport is the caller's: a mock in tests. Requests go one at a time through the
    existing adapter and limiter, with no retry and a budget of one attempt per target.
    The run stops at the first error other than "no data". Output is written to a
    `.incomplete` directory and renamed into place when the run ends, so an interrupted
    run is never mistaken for a finished one.
    """
    refusal = WIKIMEDIA_PAGEVIEWS_POLICY.registration_refusal(PURPOSE, STAGE)
    if refusal is not None:
        raise RecorderRefused(f"{SOURCE_ID}: {refusal}")
    if policy_digest(WIKIMEDIA_PAGEVIEWS_POLICY) != plan.policy_digest:
        raise RecorderRefused("the source policy changed since the plan was made; plan again")
    destination = Path(plan.destination)
    check_destination(destination)
    games = {game.slug: game for game in catalogue.games}
    recording = RecordingTransport(transport, clock)
    client = httpx.Client(transport=recording, headers={"User-Agent": user_agent()})
    adapter = WikimediaPageviewsAdapter(
        client,
        retry=RetryConfig(max_attempts=1),
        sleep=sleep,
        budget=AttemptBudget(limit=plan.max_requests),
    )
    window = window_ending(plan.window_end)
    workdir = destination.resolve().with_name(destination.name + INCOMPLETE_SUFFIX)
    (workdir / ACQUISITION_DIR).mkdir(parents=True)
    outcomes: list[TargetOutcome] = []
    records: list[AcquisitionRecord] = []
    stopped: str | None = None
    targets = {t.slug: t for t in plan.targets}
    with client:
        for planned in plan.requests:
            target = targets[planned.slug]
            if stopped is not None:
                outcomes.append(
                    TargetOutcome(
                        slug=planned.slug,
                        article=planned.article,
                        status=TargetStatus.NOT_ATTEMPTED,
                    )
                )
                continue
            identifier = SourceIdentifier(
                source=SOURCE_ID, namespace=target.namespace, value=target.article
            )
            sent = len(recording.captured)
            error: SourceError | None = None
            try:
                adapter.fetch(games[planned.slug], identifier, DAILY_PAGEVIEWS, window, clock())
            except SourceError as raised:
                error = raised
            exchanges = recording.captured[sent:]
            exchange = _exchange(exchanges[-1]) if exchanges else None
            if exchange is not None and (
                exchange.path != planned.path or exchange.method != planned.method or exchange.query
            ):
                stopped = f"{planned.slug}: the request sent was not the planned request"
                outcomes.append(
                    TargetOutcome(
                        slug=planned.slug,
                        article=planned.article,
                        status=TargetStatus.FAILED,
                        error=stopped,
                        exchange=exchange,
                    )
                )
                continue
            if error is None and exchanges:
                captured = exchanges[-1]
                record = record_acquisition(
                    WIKIMEDIA_PAGEVIEWS_POLICY,
                    PURPOSE,
                    STAGE,
                    f"{planned.method} {planned.path}",
                    captured.body,
                    captured.requested_at,
                    captured.received_at,
                    (target.article,),
                    (
                        NativeBinding(
                            native_id=target.article,
                            canonical_game_id=target.canonical_game_id,
                            bound_from=target.bound_from,
                        ),
                    ),
                )
                name = f"{target.article}.json"
                (workdir / name).write_bytes(captured.body)
                records.append(record)
                outcomes.append(
                    TargetOutcome(
                        slug=planned.slug,
                        article=planned.article,
                        status=TargetStatus.RECORDED,
                        exchange=exchange,
                        file=name,
                        record_id=record.record_id,
                    )
                )
                continue
            assert error is not None
            no_data = error.code is SourceErrorCode.NOT_FOUND
            outcomes.append(
                TargetOutcome(
                    slug=planned.slug,
                    article=planned.article,
                    status=TargetStatus.NO_DATA if no_data else TargetStatus.FAILED,
                    error_code=error.code.value,
                    error=error.message,
                    exchange=exchange,
                )
            )
            # Any error but "no data" stops the run: a 401, 403, 429 or 5xx, an invalid
            # body, or a network failure. Further requests would only repeat it.
            if not no_data and (error.code in _STOPPING or exchange is None):
                stopped = f"{planned.slug}: {error.message}"
    recorded = sum(1 for o in outcomes if o.status is TargetStatus.RECORDED)
    answered = all(o.status in (TargetStatus.RECORDED, TargetStatus.NO_DATA) for o in outcomes)
    status = (
        RunStatus.COMPLETE
        if answered and recorded
        else RunStatus.PARTIAL
        if recorded
        else RunStatus.FAILED
    )
    manifest = Manifest(
        plan=plan,
        status=status,
        stopped_because=stopped,
        outcomes=tuple(outcomes),
        requests_sent=recording.attempts,
        bodies_digest=recording_digest(workdir),
    )
    (workdir / ACQUISITION_DIR / MANIFEST).write_text(
        manifest.model_dump_json(indent=1) + "\n", encoding="utf-8"
    )
    (workdir / ACQUISITION_DIR / RECORDS).write_text(
        "".join(r.model_dump_json() + "\n" for r in records), encoding="utf-8"
    )
    workdir.replace(destination.resolve())
    return RecordingResult(destination, manifest, tuple(records))


# --- checking a recording ----------------------------------------------------------------

# Pageview recordings pinned before acquisition records existed (U6.0.1). They are
# retrospective fixtures: when each response was retrieved is not recorded, so they are
# read under the backtest's publication-lag assumptions and never as a point-in-time
# acquisition. Every recording pinned after them must carry valid acquisition records.
LEGACY_PAGEVIEW_RECORDINGS: Final = frozenset(
    {"260d787eda9046d7a07fbc9765309ebc1e2a95041ce8a5a51f0524c0c593b098"}
)
_ACQUISITION_FILES: Final = frozenset({MANIFEST, RECORDS})


class RecordingRefused(ValueError):
    """A recording that must not be read as it was asked to be."""


@dataclass(frozen=True)
class RecordingCheck:
    """What the files of a recorder-made directory establish, and what they do not.

    Three separate answers:

        integrity     the manifest, records and bodies agree with each other and with
                      the plan, and nothing was added or changed (`problems` is empty)
        completeness  the run itself: complete, partial or failed
        review_ready  intact AND complete AND at least one body kept: the only state an
                      owner may take into the normal review-and-pin process

    None of them is verified provenance. Only an owner's approval of a reviewed
    recording, and a pinned digest, is that.
    """

    directory: Path
    problems: tuple[str, ...]
    manifest: Manifest | None
    records: tuple[AcquisitionRecord, ...]
    bodies: tuple[Path, ...]

    @property
    def intact(self) -> bool:
        return not self.problems

    @property
    def run_status(self) -> RunStatus | None:
        return None if self.manifest is None else self.manifest.status

    @property
    def recorded(self) -> int:
        if self.manifest is None:
            return 0
        return sum(o.status is TargetStatus.RECORDED for o in self.manifest.outcomes)

    @property
    def review_ready(self) -> bool:
        return self.intact and self.run_status is RunStatus.COMPLETE and self.recorded > 0

    @property
    def received_until(self) -> datetime | None:
        """The latest moment any kept response arrived. Nothing is usable before it."""
        return max((r.received_at for r in self.records), default=None)


def is_recorder_output(directory: Path) -> bool:
    """Whether a directory says it was made by this recorder (it has acquisition files)."""
    return (directory / ACQUISITION_DIR).exists()


def _plan_id_matches(plan: RecordingPlan) -> bool:
    identity = plan.model_dump(mode="json", exclude={"plan_id", "planned_at", "latest_due_day"})
    return plan.plan_id == content_id("plan", identity)


def check_recording(directory: Path) -> RecordingCheck:
    """Validate a recorder-made directory against itself. Reads; changes nothing."""
    problems: list[str] = []
    acquisition = directory / ACQUISITION_DIR
    manifest: Manifest | None = None
    records: list[AcquisitionRecord] = []
    try:
        manifest = Manifest.model_validate_json((acquisition / MANIFEST).read_text("utf-8"))
    except FileNotFoundError:
        problems.append("no acquisition manifest")
    except ValueError as error:
        problems.append(f"the acquisition manifest is not valid ({type(error).__name__})")
    try:
        for number, line in enumerate(
            (acquisition / RECORDS).read_text("utf-8").splitlines(), start=1
        ):
            if line.strip():
                try:
                    records.append(AcquisitionRecord.model_validate_json(line))
                except ValueError:
                    problems.append(f"acquisition record {number} is not valid")
    except FileNotFoundError:
        problems.append("no acquisition records file")

    # Nothing but bodies at the top, and nothing but the two acquisition files beside them.
    bodies = tuple(sorted(p for p in directory.iterdir() if p.is_file() and p.suffix == ".json"))
    for path in sorted(directory.iterdir()):
        if path.is_dir() and path.name != ACQUISITION_DIR:
            problems.append(f"unexpected directory {path.name}/")
        elif path.is_file() and path.suffix != ".json":
            problems.append(f"unexpected file {path.name}")
    if acquisition.is_dir():
        for path in sorted(acquisition.iterdir()):
            if path.name not in _ACQUISITION_FILES:
                problems.append(f"unexpected file {ACQUISITION_DIR}/{path.name}")

    by_article: dict[str, AcquisitionRecord] = {}
    for record in records:
        if not record_id_matches(record):
            problems.append(f"{record.record_id}: the record was edited after it was made")
        if len(record.native_ids) != 1:
            problems.append(f"{record.record_id}: a record names exactly one article")
            continue
        article = record.native_ids[0]
        if article in by_article:
            problems.append(f"{article}: more than one acquisition record")
        by_article[article] = record
        if record.requested_at > record.received_at:
            problems.append(f"{article}: received before it was requested")

    for path in bodies:
        held = by_article.get(path.stem)
        if held is None:
            problems.append(f"{path.name}: no acquisition record")
            continue
        try:
            check_content(held, path.read_bytes())
        except ContentMismatch as mismatch:
            problems.append(f"{path.name}: {mismatch}")
        if policy_changed(held, WIKIMEDIA_PAGEVIEWS_POLICY):
            problems.append(f"{path.name}: the source policy changed since acquisition")
    for article in sorted(set(by_article) - {p.stem for p in bodies}):
        problems.append(f"{article}.json: has a record but no body")

    if manifest is not None:
        problems += _manifest_problems(manifest, by_article, bodies, directory)
    return RecordingCheck(directory, tuple(problems), manifest, tuple(records), bodies)


def _manifest_problems(
    manifest: Manifest,
    by_article: dict[str, AcquisitionRecord],
    bodies: tuple[Path, ...],
    directory: Path,
) -> list[str]:
    problems: list[str] = []
    plan = manifest.plan
    if not _plan_id_matches(plan):
        problems.append("the manifest's plan was edited after it was made")
    planned = {r.slug: r for r in plan.requests}
    targets = {t.slug: t for t in plan.targets}
    if [o.slug for o in manifest.outcomes] != [r.slug for r in plan.requests]:
        problems.append("the outcomes are not the planned targets in planned order")
    kept: set[str] = set()
    attempted = 0
    seen_not_attempted = False
    for outcome in manifest.outcomes:
        request, target = planned.get(outcome.slug), targets.get(outcome.slug)
        if request is None or target is None or outcome.article != target.article:
            problems.append(f"{outcome.slug}: not a planned target and article")
            continue
        if outcome.status is TargetStatus.NOT_ATTEMPTED:
            seen_not_attempted = True
            if outcome.exchange is not None or outcome.file is not None:
                problems.append(f"{outcome.slug}: not attempted, yet has an answer or a file")
            continue
        if seen_not_attempted:
            problems.append(f"{outcome.slug}: attempted after the run had stopped")
        attempted += 1
        exchange = outcome.exchange
        if exchange is not None and (
            exchange.method != request.method or exchange.path != request.path or exchange.query
        ):
            problems.append(f"{outcome.slug}: the request sent was not the planned request")
        if exchange is not None and not (
            plan.planned_at <= exchange.requested_at <= exchange.received_at
        ):
            problems.append(f"{outcome.slug}: request times are out of order")
        if outcome.status is not TargetStatus.RECORDED:
            if outcome.file is not None or outcome.record_id is not None:
                problems.append(f"{outcome.slug}: {outcome.status.value}, yet a body is kept")
            continue
        name = f"{target.article}.json"
        record = by_article.get(target.article)
        if outcome.file != name or record is None or outcome.record_id != record.record_id:
            problems.append(f"{outcome.slug}: the outcome does not name its body and record")
            continue
        kept.add(name)
        binding = record.bindings[0] if len(record.bindings) == 1 else None
        if (
            binding is None
            or binding.native_id != target.article
            or binding.canonical_game_id != target.canonical_game_id
            or binding.bound_from != target.bound_from
        ):
            problems.append(f"{outcome.slug}: the record binds another game or article")
        if record.request != f"{request.method} {request.path}":
            problems.append(f"{outcome.slug}: the record's request is not the planned request")
        if record.policy_digest != plan.policy_digest or record.source != plan.source:
            problems.append(f"{outcome.slug}: the record's source or policy is not the plan's")
        if exchange is None or (
            record.requested_at != exchange.requested_at
            or record.received_at != exchange.received_at
        ):
            problems.append(f"{outcome.slug}: the record's times are not the request's")
    if kept != {p.name for p in bodies}:
        problems.append("the bodies on disk are not the bodies the manifest lists")
    if set(by_article) != {Path(name).stem for name in kept}:
        problems.append("the records are not the records the manifest lists")
    if manifest.requests_sent != attempted:
        problems.append(
            f"the manifest says {manifest.requests_sent} requests were sent, but "
            f"{attempted} targets were attempted"
        )
    if seen_not_attempted and manifest.stopped_because is None:
        problems.append("targets were not attempted, yet the run says it did not stop")
    if manifest.bodies_digest != recording_digest(directory):
        problems.append("the bodies' digest is not the one written at acquisition")
    recorded = len(kept)
    answered = all(
        o.status in (TargetStatus.RECORDED, TargetStatus.NO_DATA) for o in manifest.outcomes
    )
    expected = (
        RunStatus.COMPLETE
        if answered and recorded
        else RunStatus.PARTIAL
        if recorded
        else RunStatus.FAILED
    )
    if manifest.status is not expected:
        problems.append(
            f"the run status says {manifest.status.value}, the outcomes say {expected.value}"
        )
    return problems


def require_usable_recording(provenance: InputProvenance, as_of: datetime) -> None:
    """Refuse a point-in-time reading that would use a response before it was received.

    For every directory a pageview input names: a recorder-made recording must be
    intact, and no response in it may have been received after `as_of`; read as
    verified, it must also be review-ready. A verified (pinned) recording without
    acquisition records is accepted only if it is a legacy retrospective fixture.
    Raises RecordingRefused; changes nothing.
    """
    verified = provenance.standing is Standing.VERIFIED
    for reference in (part.strip() for part in provenance.reference.split(",")):
        directory = Path(reference)
        if not reference or not directory.is_dir() or not is_recorder_output(directory):
            continue
        check = check_recording(directory)
        if check.problems:
            raise RecordingRefused(
                f"{directory}: the recording's acquisition files do not hold together "
                f"({check.problems[0]}{'; ...' if len(check.problems) > 1 else ''})"
            )
        received = check.received_until
        if received is not None and as_of < received:
            raise RecordingRefused(
                f"{directory}: responses in it were received up to {received.isoformat()}; "
                f"a reading at {as_of.isoformat()} could not have had them"
            )
        if verified and not check.review_ready:
            raise RecordingRefused(
                f"{directory}: a {check.run_status.value if check.run_status else 'broken'} "
                "recording is never verified demand evidence"
            )
    if verified:
        unaccounted = [
            digest
            for digest, reference in zip(
                provenance.digests,
                (part.strip() for part in provenance.reference.split(",")),
                strict=False,
            )
            if digest not in LEGACY_PAGEVIEW_RECORDINGS
            and not (Path(reference).is_dir() and is_recorder_output(Path(reference)))
        ]
        if unaccounted or len(provenance.digests) != len(provenance.reference.split(",")):
            raise RecordingRefused(
                f"{provenance.reference}: a pinned pageview recording made after the legacy "
                "fixtures must carry its acquisition records"
            )


def material_content(recording: CollectionReport) -> tuple[object, ...]:
    """Everything in a recording that carries historical meaning, in a canonical order.

    Kept: the requested window; which sources ran; for every signal its source, game,
    signal key, collection status, error code and message; for every series its
    identity, granularity, window, latest due period and every point's identity,
    period, status, value and source reference. A recording that lacks a failed signal,
    or holds one twice, differs.

    Left out, on purpose: when the collection ran (`observed_at`, run start and finish,
    each point's `observed_at` and the freshness lag computed from it). A replay is run
    at a moment of its choosing; those stamps describe the replay, not the history.

    Raises RecordingRefused if any signal appears twice for the same source, game and
    key: a duplicate is never silently collapsed.
    """
    seen: set[tuple[str, ...]] = set()
    entries: list[tuple[object, ...]] = []
    for run in recording.runs:
        for signal in run.outcomes:
            identity = (
                run.source,
                signal.canonical_game_id,
                signal.key.source,
                signal.key.metric,
                signal.key.granularity.value,
            )
            if identity in seen:
                raise RecordingRefused(
                    f"the recording holds more than one entry for {'/'.join(identity)}"
                )
            seen.add(identity)
            series = signal.series
            entries.append(
                (
                    *identity,
                    signal.status.value,
                    None if signal.error_code is None else signal.error_code.value,
                    signal.error_message,
                    None
                    if series is None
                    else (
                        series.canonical_game_id,
                        series.source,
                        series.metric,
                        series.granularity.value,
                        series.window_start.isoformat(),
                        series.window_end.isoformat(),
                        series.latest_due.isoformat(),
                        tuple(
                            (
                                point.canonical_game_id,
                                point.source,
                                point.metric,
                                point.granularity.value,
                                point.period_start.isoformat(),
                                point.status.value,
                                point.value,
                                point.reference,
                                None
                                if point.source_timestamp is None
                                else point.source_timestamp.isoformat(),
                            )
                            for point in series.points
                        ),
                    ),
                )
            )
    return (
        recording.window.start.isoformat(),
        recording.window.end.isoformat(),
        tuple(sorted(run.source for run in recording.runs)),
        tuple(sorted(entries, key=repr)),
    )


def bind_verified_pageviews(
    catalogue: Catalogue,
    recording: CollectionReport,
    provenance: InputProvenance,
    as_of: datetime | None,
) -> None:
    """Refuse a VERIFIED pageview input that is not exactly its pinned directory's content.

    A verified standing is a claim. It is backed only when all of these hold:

    1. The honesty guard: every claimed digest is pinned (`require_honest`).
    2. The input names exactly one directory, and that directory's content still has
       the claimed digest. A pinned digest attached to another directory proves nothing.
    3. A recorder-made directory is intact, complete and non-empty, and, for a reading
       at `as_of`, every response in it was received by then. A pinned directory without
       acquisition records is accepted only if it is a legacy retrospective fixture.
       `as_of=None` is the retrospective mode of a historical evaluation, which
       reconstructs source periods and claims nothing about when a response arrived.
    4. The supplied recording's `material_content` equals that of a replay of the
       directory: identities, statuses, failures, windows, due periods and values.
       Only replay-time stamps are ignored.

    Unverified and synthetic inputs are not this function's business: they are read
    as what they are and counted for nothing. This is a comparison with reviewed
    bytes, not cryptographic authentication of the source.
    """
    if provenance.standing is not Standing.VERIFIED:
        return
    try:
        require_honest(provenance)
    except ValueError as error:
        raise RecordingRefused(str(error)) from None
    directories = [part.strip() for part in provenance.reference.split(",")]
    if len(directories) != 1 or len(provenance.digests) != 1:
        raise RecordingRefused(
            f"{provenance.reference}: verified demand must name exactly one pinned recording"
        )
    directory = Path(directories[0])
    if not directory.is_dir() or recording_digest(directory) != provenance.digests[0]:
        raise RecordingRefused(
            f"{provenance.reference}: the directory no longer has the claimed pinned content"
        )
    if is_recorder_output(directory):
        check = check_recording(directory)
        if check.problems or not check.review_ready:
            raise RecordingRefused(
                f"{provenance.reference}: a recording that is not intact and complete is "
                "never verified demand evidence"
            )
        received = check.received_until
        if as_of is not None and received is not None and as_of < received:
            raise RecordingRefused(
                f"{directory}: responses in it were received up to {received.isoformat()}; "
                f"a reading at {as_of.isoformat()} could not have had them"
            )
    elif provenance.digests[0] not in LEGACY_PAGEVIEW_RECORDINGS:
        raise RecordingRefused(
            f"{provenance.reference}: a pinned recording made after the legacy fixtures must "
            "carry its acquisition records"
        )
    with replay_client(directory) as client:
        replayed = collect(
            catalogue,
            default_registry(client),
            recording.window,
            observed_at=recording.observed_at,
            clock=lambda: recording.observed_at,
        )
    if material_content(replayed) != material_content(recording):
        raise RecordingRefused(
            f"{provenance.reference}: the supplied pageviews are not what the pinned "
            "recording holds, so they cannot be counted as verified"
        )


# --- inspection --------------------------------------------------------------------------


class Inspection(FrozenModel):
    directory: str
    # Integrity of the files: manifest, records and bodies agree, nothing changed or added.
    integrity: Literal["intact", "problems found"]
    # What the run did: complete, partial or failed.
    run_status: str
    # Only an intact, complete run with at least one body may be taken to review and
    # pinning. Integrity is never provenance: this says nothing about who sent the bytes.
    review_ready: bool
    verdict: str
    bodies: int
    records: int
    recorded: int
    problems: tuple[str, ...]
    digest: str
    pinned: bool
    standing: str
    replayed_series: int
    window_start: date | None = None
    window_end: date | None = None
    acquired_from: AwareDatetime | None = None
    acquired_until: AwareDatetime | None = None


def inspect_recording(directory: Path, catalogue: Catalogue) -> Inspection:
    """Everything a reviewer checks before approving a pin. Reads; changes nothing.

    A partial or failed run is still inspected in full, for diagnosis, and reported as
    such: it is never described as ready for the normal pinning process.
    """
    check = check_recording(directory)
    problems = list(check.problems)
    manifest, records, bodies = check.manifest, check.records, check.bodies
    replayed = 0
    if manifest is not None and bodies:
        with replay_client(directory) as client:
            report = collect(
                catalogue,
                default_registry(client),
                window_ending(manifest.plan.window_end),
                observed_at=check.received_until or manifest.plan.planned_at,
                clock=lambda: manifest.plan.planned_at,
            )
        replayed = sum(1 for run in report.runs for s in run.outcomes if s.series is not None)
        if replayed != len(bodies):
            problems.append(f"{len(bodies)} bodies, but {replayed} replayed as a series")
    digest = recording_digest(directory)
    status = "unknown" if check.run_status is None else check.run_status.value
    intact = not problems
    ready = intact and check.review_ready
    if not intact:
        verdict = "PROBLEMS FOUND: not a usable recording"
    elif ready:
        verdict = "INTACT AND COMPLETE: ready for owner review (still UNVERIFIED)"
    elif check.run_status is RunStatus.PARTIAL:
        verdict = "INTACT BUT PARTIAL: diagnostic only, NOT ready for pinning"
    else:
        verdict = "INTACT BUT FAILED: nothing usable was recorded, NOT ready for pinning"
    return Inspection(
        directory=str(directory),
        integrity="intact" if intact else "problems found",
        run_status=status,
        review_ready=ready,
        verdict=verdict,
        bodies=len(bodies),
        records=len(records),
        recorded=check.recorded,
        problems=tuple(problems),
        digest=digest,
        pinned=digest in provenance_module.PINNED_RECORDINGS,
        standing=recording_provenance([directory]).standing.value,
        replayed_series=replayed,
        window_start=None if manifest is None else manifest.plan.window_start,
        window_end=None if manifest is None else manifest.plan.window_end,
        acquired_from=min((r.requested_at for r in records), default=None),
        acquired_until=check.received_until,
    )


def render_plan(plan: RecordingPlan) -> str:
    lines = [
        "WIKIMEDIA RECORDING PLAN (nothing has been sent)",
        f"  plan id: {plan.plan_id}",
        f"  source: {plan.source} for {plan.purpose.value} at {plan.stage.value}",
        f"  policy reviewed {plan.policy_reviewed_at}, digest {plan.policy_digest[:16]}...",
        f"  planned at {plan.planned_at.isoformat()}; latest day published then: "
        f"{plan.latest_due_day} (lag {plan.publication_lag_hours:g}h)",
        f"  window: {plan.window_start} to {plan.window_end} ({WINDOW_DAYS} days)",
        f"  requests: {plan.max_requests}, one at a time, {plan.rate_limit}, no retry",
        f"  destination: {plan.destination} (must not exist; written via "
        f"{Path(plan.destination).name}{INCOMPLETE_SUFFIX})",
    ]
    lines += [f"    GET {r.url}" for r in plan.requests]
    lines += [
        "",
        "  To send these requests, an owner who is authorised to do so adds:",
        f"    --live --confirm {plan.plan_id}",
        "  The result is UNVERIFIED until reviewed and pinned.",
        "",
    ]
    return "\n".join(lines)


def render_manifest(manifest: Manifest, directory: Path) -> str:
    lines = [
        f"WIKIMEDIA RECORDING: {manifest.status.value.upper()}",
        f"  directory: {directory}",
        f"  requests sent: {manifest.requests_sent} of {manifest.plan.max_requests}",
    ]
    if manifest.stopped_because:
        lines.append(f"  STOPPED: {manifest.stopped_because}")
    for o in manifest.outcomes:
        detail = o.file or o.error or ""
        lines.append(f"    {o.slug:<20} {o.status.value:<14} {detail}")
    lines += [
        f"  bodies digest (NOT pinned): {manifest.bodies_digest}",
        f"  {manifest.trust}",
        "",
    ]
    return "\n".join(lines)


def render_inspection(inspection: Inspection) -> str:
    lines = [
        f"RECORDING INSPECTION: {inspection.verdict}",
        f"  directory: {inspection.directory}",
        f"  integrity: {inspection.integrity}; run: {inspection.run_status}; "
        f"review-ready: {'yes' if inspection.review_ready else 'NO'}",
        f"  bodies {inspection.bodies}, acquisition records {inspection.records}, "
        f"recorded targets {inspection.recorded}, replayed series {inspection.replayed_series}",
        f"  window {inspection.window_start} to {inspection.window_end}; acquired "
        f"{inspection.acquired_from} to {inspection.acquired_until}",
        f"  digest: {inspection.digest}",
        f"  pinned: {'yes' if inspection.pinned else 'no'}; standing: {inspection.standing}",
        "  Integrity is not provenance: only an owner's approval of a reviewed recording, "
        "and a pinned digest, makes it verified.",
    ]
    lines += [f"  PROBLEM: {p}" for p in inspection.problems]
    lines.append("")
    return "\n".join(lines)
