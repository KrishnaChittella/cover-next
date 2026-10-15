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
from metaspike.evidence.acquisition import (
    AcquisitionRecord,
    ContentMismatch,
    NativeBinding,
    check_content,
    policy_changed,
    policy_digest,
    record_acquisition,
)
from metaspike.evidence.provenance import PINNED_RECORDINGS, recording_digest, recording_provenance
from metaspike.features.attention import HISTORY_DAYS
from metaspike.pipeline.collection import collect, window_ending
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


# --- inspection --------------------------------------------------------------------------


class Inspection(FrozenModel):
    directory: str
    status: str
    bodies: int
    records: int
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
    """Everything a reviewer checks before approving a pin. Reads; changes nothing."""
    problems: list[str] = []
    manifest_path = directory / ACQUISITION_DIR / MANIFEST
    records_path = directory / ACQUISITION_DIR / RECORDS
    manifest: Manifest | None = None
    records: list[AcquisitionRecord] = []
    if not manifest_path.is_file() or not records_path.is_file():
        problems.append("no acquisition manifest or records: not a recording made here")
    else:
        manifest = Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        records = [
            AcquisitionRecord.model_validate_json(line)
            for line in records_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    bodies = sorted(p for p in directory.glob("*.json") if p.is_file())
    by_article = {r.native_ids[0]: r for r in records}
    if len(by_article) != len(records):
        problems.append("more than one acquisition record for the same article")
    for path in bodies:
        record = by_article.get(path.stem)
        if record is None:
            problems.append(f"{path.name}: no acquisition record")
            continue
        try:
            check_content(record, path.read_bytes())
        except ContentMismatch as mismatch:
            problems.append(f"{path.name}: {mismatch}")
        if policy_changed(record, WIKIMEDIA_PAGEVIEWS_POLICY):
            problems.append(f"{path.name}: the source policy changed since acquisition")
    for article in set(by_article) - {p.stem for p in bodies}:
        problems.append(f"{article}.json: recorded in the manifest, missing on disk")
    if manifest is not None:
        kept = {o.file for o in manifest.outcomes if o.file is not None}
        if kept != {p.name for p in bodies}:
            problems.append("the files on disk are not the files the manifest lists")
        if manifest.bodies_digest != recording_digest(directory):
            problems.append("the bodies' digest is not the one written at acquisition")
    replayed = 0
    if manifest is not None and bodies:
        with replay_client(directory) as client:
            report = collect(
                catalogue,
                default_registry(client),
                window_ending(manifest.plan.window_end),
                observed_at=max(r.received_at for r in records) if records else MIGRATED_AT,
                clock=lambda: MIGRATED_AT,
            )
        replayed = sum(1 for run in report.runs for s in run.outcomes if s.series is not None)
        if replayed != len(bodies):
            problems.append(f"{len(bodies)} bodies, but {replayed} replayed as a series")
    digest = recording_digest(directory)
    provenance = recording_provenance([directory])
    return Inspection(
        directory=str(directory),
        status="no problems found" if not problems else f"{len(problems)} problem(s)",
        bodies=len(bodies),
        records=len(records),
        problems=tuple(problems),
        digest=digest,
        pinned=digest in PINNED_RECORDINGS,
        standing=provenance.standing.value,
        replayed_series=replayed,
        window_start=None if manifest is None else manifest.plan.window_start,
        window_end=None if manifest is None else manifest.plan.window_end,
        acquired_from=min((r.requested_at for r in records), default=None),
        acquired_until=max((r.received_at for r in records), default=None),
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
        f"RECORDING INSPECTION: {inspection.status}",
        f"  directory: {inspection.directory}",
        f"  bodies {inspection.bodies}, acquisition records {inspection.records}, "
        f"replayed series {inspection.replayed_series}",
        f"  window {inspection.window_start} to {inspection.window_end}; acquired "
        f"{inspection.acquired_from} to {inspection.acquired_until}",
        f"  digest: {inspection.digest}",
        f"  pinned: {'yes' if inspection.pinned else 'no'}; standing: {inspection.standing}",
    ]
    lines += [f"  PROBLEM: {p}" for p in inspection.problems]
    lines.append("")
    return "\n".join(lines)
