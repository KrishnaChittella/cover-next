"""Supervised Steam concurrent-player recording: plan, sample once, keep, inspect.

The way a NEW recording of the real source is made, for an owner to review and, only
if they approve, to pin. It is the Steam counterpart of `pageview_recorder` and reuses
the existing adapter, rate limiter, request budget, source policy, replay format and
acquisition records. It is not a collector and runs nothing on a schedule.

    plan      the default. Chooses the apps (at most 8, each an eligible bootstrap app
              named explicitly, with the binding it is sampled under), the ONE 15-minute
              slot the plan is for, and the exact requests. Opens no client and sends
              nothing. The plan's id covers the slot, so a confirmation can only ever
              authorise sampling inside that slot.
    record    sends exactly the planned requests through the existing adapter, one at a
              time under the policy's limit, one attempt each and never more than the
              plan's budget, and stops at the first sign of trouble or when the slot ends.
              Callers hand it a transport and the key: in tests a mock and a fake; a live
              transport and the real key are only ever built by the command line, behind
              an explicit opt-in that repeats the plan's id.
    inspect   reads a recording back: every file against its acquisition record, every
              outcome against the plan, each kept response replayed by the existing
              replay client and adapter, the digest a reviewer would pin, and the
              standing it has today.

**What a recording is.** One `current_players_<app>.json` per kept response, at the top
of the directory: the envelope `replay_sampling_client` and `replay_clock` already read,
exactly as for the response recorded on 2026-10-06, plus `body_text`, the body exactly
as it arrived. `recorded_at` is when the answer arrived, so a replay is dated when Steam
gave it. Beside them, under `acquisition/` and outside the digest: one acquisition
record per kept response, over the exact body bytes, and a manifest of the plan and of
every request's outcome, failures included.

Only a 200 the adapter reads as an observed count is kept. A 404 or a 200 without a
count (unavailable), an error, a refusal or an invalid body is listed in the manifest
and nothing is invented in its place.

**Never written:** the API key, the key header's value, any request header, response
headers outside the adapter's reportable list (other names only), environment values.
A response that echoes the key is not kept at all.

**Trust.** A new recording is UNVERIFIED. It becomes verified only when an owner has
reviewed it and its digest has been added to `PINNED_RECORDINGS` in a reviewed commit.
Nothing here pins, and there is no option to.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal

import httpx
from pydantic import AwareDatetime, Field

from metaspike.catalogue.eligibility import (
    STEAM_RECORD_SOURCE,
    active_binding,
    bootstrap_scope,
    sampling_targets,
    steam_eligibility,
)
from metaspike.catalogue.generate import CatalogueState
from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.model import FrozenModel
from metaspike.domain.samples import SampleStatus
from metaspike.domain.signals import SourceErrorCode
from metaspike.domain.source_policy import DeploymentStage, SourcePurpose
from metaspike.domain.source_records import SourceRecord
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
from metaspike.evidence.provenance import recording_digest, recording_provenance
from metaspike.pageview_recorder import (
    INCOMPLETE_SUFFIX,
    RecorderRefused,
    RecordingTransport,
    RunStatus,
    check_destination,
)
from metaspike.sources.base import SourceError
from metaspike.sources.credentials import Secret
from metaspike.sources.http import AttemptBudget, LimitedHttpClient, RequestBudgetExhausted
from metaspike.sources.limiter import Clock, LimiterPool
from metaspike.sources.sampling_sources import (
    REPLAY_FILE_PREFIX,
    replay_clock,
    replay_sampling_client,
)
from metaspike.sources.steam_players import (
    CURRENT_PLAYERS_URL,
    KEY_HEADER,
    PUBLIC_HOST,
    REPORTABLE_HEADERS,
    SAMPLED_PLAYERS,
    SAMPLING_RETRY,
    SOURCE_ID,
    STEAM_CURRENT_PLAYERS_POLICY,
    SteamCurrentPlayersAdapter,
    interpret,
)
from metaspike.validation.snapshot import content_id

RECORDING_FORMAT: Final = "steam_players_recording_v1"
MAX_APPS: Final = 8
SLOT: Final = SAMPLED_PLAYERS.granularity
STAGE: Final = DeploymentStage.PRIVATE_DEVELOPMENT
PURPOSE: Final = SourcePurpose.DEMAND_SIGNALS
ACQUISITION_DIR: Final = "acquisition"
MANIFEST: Final = "manifest.json"
RECORDS: Final = "records.jsonl"
REQUEST_PATH: Final = httpx.URL(CURRENT_PLAYERS_URL).path
ENVELOPE_NOTE: Final = (
    "One recorded response of ISteamUserStats/GetNumberOfCurrentPlayers/v1, made by the "
    "supervised recorder. The credential travelled in a request header and is not recorded."
)
# Text that must never appear in a kept file. The header NAME is listed too: no request
# header is ever written, so its appearance would mean something was.
_CREDENTIAL_MARKERS: Final = (KEY_HEADER, "key=", "api_key", "apikey", "authorization", "token")
_ACQUISITION_FILES: Final = frozenset({MANIFEST, RECORDS})
TRUST_STATEMENT: Final = (
    "UNVERIFIED: a new recording is not evidence of anything until an owner has reviewed "
    "it and its digest has been pinned in a reviewed commit. An acquisition record proves "
    "the bytes are unchanged and that the policy permitted the collection; it proves "
    "neither that Steam sent them nor any permission for public use."
)


class Target(FrozenModel):
    app_id: str = Field(pattern=r"^[1-9][0-9]{0,9}$")
    slug: str
    canonical_game_id: str
    # The binding the app is sampled under, as it stood when the plan was made.
    mapping_version: int
    binding_ref: str
    bound_from: AwareDatetime


class PlannedRequest(FrozenModel):
    app_id: str
    method: Literal["GET"] = "GET"
    url: str
    path: str
    query: tuple[tuple[str, str], ...]


class RecordingPlan(FrozenModel):
    plan_id: str = Field(pattern=r"^plan_[0-9a-f]{32}$")
    format: Literal["steam_players_recording_v1"] = RECORDING_FORMAT
    source: str = SOURCE_ID
    purpose: SourcePurpose = PURPOSE
    stage: DeploymentStage = STAGE
    policy_reviewed_at: date
    policy_digest: str
    # When the plan was made. Not part of the id; the slot it falls in is.
    planned_at: AwareDatetime
    slot_start: AwareDatetime
    slot_end: AwareDatetime
    targets: tuple[Target, ...]
    requests: tuple[PlannedRequest, ...]
    max_requests: int = Field(ge=1, le=MAX_APPS)
    rate_limit: str
    daily_quota: str
    retries: Literal[0] = 0
    # The NAME of the header the key travels in. Never its value.
    credential_header: str = KEY_HEADER
    destination: str


class TargetStatus(StrEnum):
    RECORDED = "recorded"
    UNAVAILABLE = "unavailable"  # 404, or a 200 without a count: nothing is kept
    FAILED = "failed"  # a response or a transport failure that is not a count
    REFUSED = "refused"  # our own limiter or budget refused: nothing was sent
    NOT_ATTEMPTED = "not_attempted"  # the run had stopped before it


class Exchange(FrozenModel):
    """One request and what came back. Never the request's headers."""

    method: str
    host: str
    path: str
    query: tuple[tuple[str, str], ...]
    requested_at: AwareDatetime
    received_at: AwareDatetime
    status: int
    headers: tuple[tuple[str, str], ...]
    body_bytes: int


class TargetOutcome(FrozenModel):
    app_id: str
    slug: str
    status: TargetStatus
    error_code: str | None = None
    error: str | None = None
    exchange: Exchange | None = None
    file: str | None = None
    record_id: str | None = None


class Manifest(FrozenModel):
    format: Literal["steam_players_recording_v1"] = RECORDING_FORMAT
    plan: RecordingPlan
    status: RunStatus
    stopped_because: str | None = None
    outcomes: tuple[TargetOutcome, ...]
    requests_sent: int
    # The digest a reviewer would pin, over the kept files only. NOT pinned.
    bodies_digest: str
    standing: Literal["unverified"] = "unverified"
    trust: str = TRUST_STATEMENT


def body_name(app_id: str) -> str:
    return f"{REPLAY_FILE_PREFIX}{app_id}.json"


def _plan_identity(plan: RecordingPlan) -> dict[str, object]:
    return plan.model_dump(mode="json", exclude={"plan_id", "planned_at"})


# --- planning ----------------------------------------------------------------------------


def plan_recording(
    state: CatalogueState,
    records: list[SourceRecord],
    app_ids: Sequence[str],
    planned_at: datetime,
    destination: Path,
) -> RecordingPlan:
    """The exact requests a supervised session would send, in one slot. Sends nothing."""
    if planned_at.tzinfo is None:
        raise RecorderRefused("the planning moment needs a time zone")
    refusal = STEAM_CURRENT_PLAYERS_POLICY.registration_refusal(PURPOSE, STAGE)
    if refusal is not None:
        raise RecorderRefused(f"{SOURCE_ID}: {refusal}")
    if not app_ids:
        raise RecorderRefused("name the apps to sample; nothing is chosen by default")
    if len(set(app_ids)) != len(app_ids):
        raise RecorderRefused("an app is named more than once")
    if len(app_ids) > MAX_APPS:
        raise RecorderRefused(f"at most {MAX_APPS} apps may be sampled in one session")
    eligibility = steam_eligibility(state, records, as_of=planned_at)
    slugs = {e.canonical_game_id: e.slug for e in eligibility}
    eligible = {
        t.source_native_id: t
        for t in sampling_targets(state, eligibility, bootstrap_scope(), as_of=planned_at)
    }
    targets = []
    for app_id in app_ids:
        target = eligible.get(app_id)
        if target is None or target.canonical_game_id is None:
            raise RecorderRefused(
                f"app {app_id} is not an eligible bootstrap app with a trusted binding at "
                f"{planned_at.isoformat()}; nothing else is sampled"
            )
        binding = active_binding(
            state, SourceRecordKey(source=STEAM_RECORD_SOURCE, source_id=app_id), planned_at
        )
        assert binding is not None and binding.canonical_game_id == target.canonical_game_id
        targets.append(
            Target(
                app_id=app_id,
                slug=slugs[target.canonical_game_id],
                canonical_game_id=target.canonical_game_id,
                mapping_version=binding.mapping_version,
                binding_ref=binding.decision_ref,
                bound_from=binding.valid_from,
            )
        )
    check_destination(destination)
    ordered = tuple(sorted(targets, key=lambda t: int(t.app_id)))
    requests = tuple(
        PlannedRequest(
            app_id=t.app_id,
            url=f"{CURRENT_PLAYERS_URL}?appid={t.app_id}",
            path=REQUEST_PATH,
            query=(("appid", t.app_id),),
        )
        for t in ordered
    )
    slot_start = SLOT.floor(planned_at)
    operations = STEAM_CURRENT_PLAYERS_POLICY.operations
    limit, quota = operations.rate_limit, operations.quota
    fields: dict[str, object] = {
        "policy_reviewed_at": STEAM_CURRENT_PLAYERS_POLICY.reviewed_at,
        "policy_digest": policy_digest(STEAM_CURRENT_PLAYERS_POLICY),
        "planned_at": planned_at,
        "slot_start": slot_start,
        "slot_end": slot_start + SLOT.step,
        "targets": ordered,
        "requests": requests,
        "max_requests": len(requests),
        "rate_limit": f"{limit.max_requests} per {limit.per_seconds}s ({limit.basis.value})",
        "daily_quota": "none" if quota is None else f"{quota.limit} {quota.unit} per day",
        "destination": str(destination),
    }
    draft = RecordingPlan.model_validate({**fields, "plan_id": "plan_" + "0" * 32})
    return draft.model_copy(update={"plan_id": content_id("plan", _plan_identity(draft))})


# --- recording ---------------------------------------------------------------------------


def _exchange(transport: RecordingTransport, index: int) -> Exchange:
    captured = transport.captured[index]
    url = captured.request.url
    return Exchange(
        method=captured.request.method,
        host=url.host,
        path=url.path,
        query=tuple(sorted(url.params.multi_items())),
        requested_at=captured.requested_at,
        received_at=captured.received_at,
        status=captured.response.status_code,
        headers=tuple(
            (name, captured.response.headers[name])
            for name in REPORTABLE_HEADERS
            if name in captured.response.headers
        ),
        body_bytes=len(captured.body),
    )


def _envelope(exchange: Exchange, response: httpx.Response, body: bytes) -> bytes:
    """The replay envelope for one kept response, plus its body exactly as received."""
    names = sorted(name.lower() for name in response.headers)
    document = {
        "note": ENVELOPE_NOTE,
        "recorded_at": exchange.received_at.isoformat(),
        "request": {
            "method": exchange.method,
            "host": exchange.host,
            "path": exchange.path,
            "query": {name: int(value) for name, value in exchange.query},
        },
        "response": {
            "status": exchange.status,
            "headers": dict(exchange.headers),
            "other_header_names": [n for n in names if n not in REPORTABLE_HEADERS],
            "body": json.loads(body),
            "body_text": body.decode("utf-8"),
        },
    }
    return (json.dumps(document, indent=1) + "\n").encode("utf-8")


@dataclass(frozen=True)
class RecordingResult:
    directory: Path
    manifest: Manifest
    records: tuple[AcquisitionRecord, ...]


def record_players(
    plan: RecordingPlan,
    transport: httpx.BaseTransport,
    api_key: Secret,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
) -> RecordingResult:
    """Send exactly the planned requests, inside the planned slot, and say what happened.

    The transport and the key are the caller's: a mock and a fake in tests. Requests go
    one at a time through the existing adapter and limiter, one attempt each, under a
    budget of exactly the plan's requests. The run stops at the first error other than
    "unavailable", at a refusal, or when the slot ends. Output is written to a
    `.incomplete` directory and renamed into place at the end, so an interrupted run is
    never mistaken for a finished one.
    """
    refusal = STEAM_CURRENT_PLAYERS_POLICY.registration_refusal(PURPOSE, STAGE)
    if refusal is not None:
        raise RecorderRefused(f"{SOURCE_ID}: {refusal}")
    if policy_digest(STEAM_CURRENT_PLAYERS_POLICY) != plan.policy_digest:
        raise RecorderRefused("the source policy changed since the plan was made; plan again")
    if plan.plan_id != content_id("plan", _plan_identity(plan)):
        raise RecorderRefused("the plan was edited after it was made; plan again")
    if SLOT.floor(clock()) != plan.slot_start:
        raise RecorderRefused(
            f"the plan is for the slot starting {plan.slot_start.isoformat()}, which is not "
            "the current slot; plan again. Nothing was sent."
        )
    destination = Path(plan.destination)
    check_destination(destination)
    recording = RecordingTransport(transport, clock)
    client = httpx.Client(transport=recording)
    pool = LimiterPool(clock=Clock(sleeper=sleep))
    http = LimitedHttpClient(
        client,
        pool.for_policy(STEAM_CURRENT_PLAYERS_POLICY),
        SAMPLING_RETRY,
        budget=AttemptBudget(limit=plan.max_requests),
    )
    adapter = SteamCurrentPlayersAdapter(http, api_key, clock)
    secret = api_key.reveal().encode("utf-8")
    workdir = destination.resolve().with_name(destination.name + INCOMPLETE_SUFFIX)
    (workdir / ACQUISITION_DIR).mkdir(parents=True)
    targets = {t.app_id: t for t in plan.targets}
    outcomes: list[TargetOutcome] = []
    records: list[AcquisitionRecord] = []
    stopped: str | None = None
    with client:
        for planned in plan.requests:
            target = targets[planned.app_id]

            if stopped is None and SLOT.floor(clock()) != plan.slot_start:
                stopped = f"the slot starting {plan.slot_start.isoformat()} ended"
            if stopped is not None:
                outcomes.append(_outcome(target, TargetStatus.NOT_ATTEMPTED))
                continue
            sent = len(recording.captured)
            attempts = recording.attempts
            try:
                probe = adapter.probe(int(planned.app_id))
            except (SourceError, RequestBudgetExhausted) as error:
                code = (
                    error.code
                    if isinstance(error, SourceError)
                    else SourceErrorCode.QUOTA_EXHAUSTED
                )
                message = error.message if isinstance(error, SourceError) else str(error)
                exchange = _exchange(recording, -1) if len(recording.captured) > sent else None
                nothing_sent = recording.attempts == attempts
                outcomes.append(
                    _outcome(
                        target,
                        TargetStatus.REFUSED if nothing_sent else TargetStatus.FAILED,
                        error_code=code.value,
                        error=message,
                        exchange=exchange,
                    )
                )
                stopped = f"app {planned.app_id}: {message}"
                continue
            exchange = _exchange(recording, -1)
            captured = recording.captured[-1]
            if (
                exchange.method != planned.method
                or exchange.host != PUBLIC_HOST
                or exchange.path != planned.path
                or exchange.query != planned.query
            ):
                stopped = f"app {planned.app_id}: the request sent was not the planned request"
                outcomes.append(
                    _outcome(target, TargetStatus.FAILED, error=stopped, exchange=exchange)
                )
                continue
            if secret and secret in captured.body:
                stopped = f"app {planned.app_id}: the response echoed the credential; not kept"
                outcomes.append(
                    _outcome(target, TargetStatus.FAILED, error=stopped, exchange=exchange)
                )
                continue
            if exchange.received_at >= plan.slot_end:
                # An answer that arrived in the next slot is not an observation of this
                # one: a replay would date it there. Not kept, and the session ends.
                stopped = f"app {planned.app_id}: the answer arrived after the slot ended"
                outcomes.append(
                    _outcome(target, TargetStatus.FAILED, error=stopped, exchange=exchange)
                )
                continue
            reading = probe.reading
            if reading.status is SampleStatus.UNAVAILABLE:
                outcomes.append(
                    _outcome(
                        target,
                        TargetStatus.UNAVAILABLE,
                        error_code=None if reading.error_code is None else reading.error_code.value,
                        exchange=exchange,
                    )
                )
                continue
            if reading.status is not SampleStatus.OBSERVED or exchange.status != httpx.codes.OK:
                code = reading.error_code or SourceErrorCode.INVALID_RESPONSE
                stopped = f"app {planned.app_id}: {code.value} (HTTP {exchange.status})"
                outcomes.append(
                    _outcome(
                        target,
                        TargetStatus.FAILED,
                        error_code=code.value,
                        error=stopped,
                        exchange=exchange,
                    )
                )
                continue
            record = record_acquisition(
                STEAM_CURRENT_PLAYERS_POLICY,
                PURPOSE,
                STAGE,
                f"{planned.method} {planned.path}?appid={planned.app_id}",
                captured.body,
                captured.requested_at,
                captured.received_at,
                (target.app_id,),
                (
                    NativeBinding(
                        native_id=target.app_id,
                        canonical_game_id=target.canonical_game_id,
                        bound_from=target.bound_from,
                    ),
                ),
            )
            name = body_name(target.app_id)
            (workdir / name).write_bytes(_envelope(exchange, captured.response, captured.body))
            records.append(record)
            outcomes.append(
                _outcome(
                    target,
                    TargetStatus.RECORDED,
                    exchange=exchange,
                    file=name,
                    record_id=record.record_id,
                )
            )
    assert recording.attempts <= plan.max_requests
    manifest = Manifest(
        plan=plan,
        status=_run_status(outcomes),
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


def _outcome(target: Target, status: TargetStatus, **fields: Any) -> TargetOutcome:
    return TargetOutcome(app_id=target.app_id, slug=target.slug, status=status, **fields)


def _run_status(outcomes: Sequence[TargetOutcome]) -> RunStatus:
    recorded = sum(o.status is TargetStatus.RECORDED for o in outcomes)
    answered = all(o.status in (TargetStatus.RECORDED, TargetStatus.UNAVAILABLE) for o in outcomes)
    if answered and recorded:
        return RunStatus.COMPLETE
    return RunStatus.PARTIAL if recorded else RunStatus.FAILED


# --- checking a recording ----------------------------------------------------------------


@dataclass(frozen=True)
class RecordingCheck:
    """Integrity, completeness and review-readiness of a recorder-made directory.

    `review_ready` (intact, complete, at least one response kept) is the only state an
    owner may take into the normal review-and-pin process. None of it is verified
    provenance: only an owner's approval of a reviewed recording, and a pinned digest,
    is that.
    """

    directory: Path
    problems: tuple[str, ...]
    manifest: Manifest | None
    records: tuple[AcquisitionRecord, ...]
    bodies: tuple[Path, ...]

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
        return not self.problems and self.run_status is RunStatus.COMPLETE and self.recorded > 0


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
        lines = (acquisition / RECORDS).read_text("utf-8").splitlines()
    except FileNotFoundError:
        problems.append("no acquisition records file")
        lines = []
    for number, line in enumerate(lines, start=1):
        if line.strip():
            try:
                records.append(AcquisitionRecord.model_validate_json(line))
            except ValueError:
                problems.append(f"acquisition record {number} is not valid")

    bodies = tuple(sorted(p for p in directory.iterdir() if p.is_file() and p.suffix == ".json"))
    for path in sorted(directory.iterdir()):
        if path.is_dir() and path.name != ACQUISITION_DIR:
            problems.append(f"unexpected directory {path.name}/")
        elif path.is_file() and (
            path.suffix != ".json"
            or not path.stem.removeprefix(REPLAY_FILE_PREFIX).isdigit()
            or not path.stem.startswith(REPLAY_FILE_PREFIX)
        ):
            problems.append(f"unexpected file {path.name}")
    if acquisition.is_dir():
        for path in sorted(acquisition.iterdir()):
            if path.name not in _ACQUISITION_FILES:
                problems.append(f"unexpected file {ACQUISITION_DIR}/{path.name}")

    by_app: dict[str, AcquisitionRecord] = {}
    for record in records:
        if not record_id_matches(record):
            problems.append(f"{record.record_id}: the record was edited after it was made")
        if len(record.native_ids) != 1:
            problems.append(f"{record.record_id}: a record names exactly one app")
            continue
        app_id = record.native_ids[0]
        if app_id in by_app:
            problems.append(f"app {app_id}: more than one acquisition record")
        by_app[app_id] = record
        if policy_changed(record, STEAM_CURRENT_PLAYERS_POLICY):
            problems.append(f"app {app_id}: the source policy changed since acquisition")

    for path in bodies:
        app_id = path.stem.removeprefix(REPLAY_FILE_PREFIX)
        held = by_app.get(app_id)
        if held is None:
            problems.append(f"{path.name}: no acquisition record")
            continue
        problems += [f"{path.name}: {p}" for p in _envelope_problems(path, app_id, held)]
    for app_id in sorted(set(by_app) - {p.stem.removeprefix(REPLAY_FILE_PREFIX) for p in bodies}):
        problems.append(f"{body_name(app_id)}: has a record but no file")

    if manifest is not None:
        problems += _manifest_problems(manifest, by_app, bodies, directory)
    return RecordingCheck(directory, tuple(problems), manifest, tuple(records), bodies)


def _envelope_problems(path: Path, app_id: str, record: AcquisitionRecord) -> list[str]:
    raw = path.read_bytes()
    lowered = raw.decode("utf-8", errors="replace").lower()
    if any(marker in lowered for marker in _CREDENTIAL_MARKERS):
        return ["the file contains credential-like text"]
    try:
        document = json.loads(raw)
        response, request = document["response"], document["request"]
        body_text: str = response["body_text"]
        recorded_at = datetime.fromisoformat(document["recorded_at"])
    except (ValueError, KeyError, TypeError):
        return ["not a recorder envelope"]
    problems = []
    try:
        check_content(record, body_text.encode("utf-8"))
    except ContentMismatch as mismatch:
        problems.append(str(mismatch))
    try:
        if json.loads(body_text) != response.get("body"):
            problems.append("the parsed body is not the body as received")
    except ValueError:
        problems.append("the body as received is not JSON")
    if recorded_at != record.received_at:
        problems.append("recorded_at is not when the acquisition record says it arrived")
    expected = {"appid": int(app_id)}
    if (
        request.get("host") != PUBLIC_HOST
        or request.get("path") != REQUEST_PATH
        or request.get("query") != expected
        or record.request != f"GET {REQUEST_PATH}?appid={app_id}"
    ):
        problems.append("the request is not the one request for this app")
    return problems


def _manifest_problems(
    manifest: Manifest,
    by_app: dict[str, AcquisitionRecord],
    bodies: tuple[Path, ...],
    directory: Path,
) -> list[str]:
    problems: list[str] = []
    plan = manifest.plan
    if plan.plan_id != content_id("plan", _plan_identity(plan)):
        problems.append("the manifest's plan was edited after it was made")
    if plan.max_requests > MAX_APPS or len(plan.requests) > MAX_APPS:
        problems.append(f"the plan exceeds {MAX_APPS} requests")
    if (
        plan.slot_start != SLOT.floor(plan.slot_start)
        or plan.slot_end != plan.slot_start + SLOT.step
    ):
        problems.append("the plan's slot is not one sampling slot")
    planned = {r.app_id: r for r in plan.requests}
    targets = {t.app_id: t for t in plan.targets}
    if [o.app_id for o in manifest.outcomes] != [r.app_id for r in plan.requests]:
        problems.append("the outcomes are not the planned apps in planned order")
    kept: set[str] = set()
    attempted = 0
    seen_stop = False
    for outcome in manifest.outcomes:
        request, target = planned.get(outcome.app_id), targets.get(outcome.app_id)
        if request is None or target is None or outcome.slug != target.slug:
            problems.append(f"app {outcome.app_id}: not a planned app")
            continue
        if outcome.status is TargetStatus.NOT_ATTEMPTED:
            seen_stop = True
            if outcome.exchange is not None or outcome.file is not None:
                problems.append(f"app {outcome.app_id}: not attempted, yet has an answer or a file")
            continue
        if seen_stop:
            problems.append(f"app {outcome.app_id}: attempted after the run had stopped")
        if outcome.status is not TargetStatus.REFUSED:
            attempted += 1
        exchange = outcome.exchange
        if exchange is not None:
            if (
                exchange.method != request.method
                or exchange.host != PUBLIC_HOST
                or exchange.path != request.path
                or exchange.query != request.query
            ):
                problems.append(
                    f"app {outcome.app_id}: the request sent was not the planned request"
                )
            if not (
                plan.planned_at <= exchange.requested_at <= exchange.received_at
                and plan.slot_start <= exchange.requested_at < plan.slot_end
            ):
                problems.append(f"app {outcome.app_id}: not requested inside the planned slot")
        if outcome.status is not TargetStatus.RECORDED:
            if outcome.file is not None or outcome.record_id is not None:
                problems.append(f"app {outcome.app_id}: {outcome.status.value}, yet a file is kept")
            continue
        name = body_name(target.app_id)
        record = by_app.get(target.app_id)
        if outcome.file != name or record is None or outcome.record_id != record.record_id:
            problems.append(f"app {outcome.app_id}: the outcome does not name its file and record")
            continue
        kept.add(name)
        if exchange is not None and exchange.received_at >= plan.slot_end:
            problems.append(f"app {outcome.app_id}: kept, yet it arrived after the slot ended")
        binding = record.bindings[0] if len(record.bindings) == 1 else None
        if (
            binding is None
            or binding.native_id != target.app_id
            or binding.canonical_game_id != target.canonical_game_id
            or binding.bound_from != target.bound_from
        ):
            problems.append(f"app {outcome.app_id}: the record binds another game or app")
        if record.policy_digest != plan.policy_digest or record.source != plan.source:
            problems.append(
                f"app {outcome.app_id}: the record's source or policy is not the plan's"
            )
        if exchange is None or (
            record.requested_at != exchange.requested_at
            or record.received_at != exchange.received_at
        ):
            problems.append(f"app {outcome.app_id}: the record's times are not the request's")
    if kept != {p.name for p in bodies}:
        problems.append("the files on disk are not the files the manifest lists")
    if set(by_app) != {
        name.removeprefix(REPLAY_FILE_PREFIX).removesuffix(".json") for name in kept
    }:
        problems.append("the records are not the records the manifest lists")
    if manifest.requests_sent != attempted or manifest.requests_sent > plan.max_requests:
        problems.append(
            f"the manifest says {manifest.requests_sent} requests were sent, but "
            f"{attempted} apps were attempted (budget {plan.max_requests})"
        )
    if seen_stop and manifest.stopped_because is None:
        problems.append("apps were not attempted, yet the run says it did not stop")
    if manifest.bodies_digest != recording_digest(directory):
        problems.append("the files' digest is not the one written at acquisition")
    if manifest.status is not _run_status(manifest.outcomes):
        problems.append(
            f"the run status says {manifest.status.value}, the outcomes say "
            f"{_run_status(manifest.outcomes).value}"
        )
    return problems


# --- inspection --------------------------------------------------------------------------


class Inspection(FrozenModel):
    directory: str
    integrity: Literal["intact", "problems found"]
    run_status: str
    review_ready: bool
    verdict: str
    files: int
    records: int
    recorded: int
    replayed_observed: int
    problems: tuple[str, ...]
    digest: str
    pinned: bool
    standing: str
    slot_start: AwareDatetime | None = None
    replay_dated_at: AwareDatetime | None = None


def inspect_recording(directory: Path) -> Inspection:
    """Everything a reviewer checks before approving a pin. Reads; changes nothing.

    Each kept file is replayed through the existing replay client and read by the
    adapter's own rules, and the replay clock is checked to fall inside the planned slot.
    """
    check = check_recording(directory)
    problems = list(check.problems)
    manifest = check.manifest
    observed = 0
    dated: datetime | None = None
    if manifest is not None and check.bodies and not check.problems:
        with replay_sampling_client(directory) as client:
            for path in check.bodies:
                app_id = path.stem.removeprefix(REPLAY_FILE_PREFIX)
                response = client.get(CURRENT_PLAYERS_URL, params={"appid": app_id})
                reading = interpret(
                    response.status_code,
                    response.json(),
                    response.headers,
                    manifest.plan.planned_at,
                )
                if reading.status is SampleStatus.OBSERVED:
                    observed += 1
                else:
                    problems.append(f"{path.name}: does not replay as an observed count")
        dated = replay_clock(directory)()
        if SLOT.floor(dated) != manifest.plan.slot_start:
            problems.append("a replay would be dated outside the planned slot")
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
        files=len(check.bodies),
        records=len(check.records),
        recorded=check.recorded,
        replayed_observed=observed,
        problems=tuple(problems),
        digest=digest,
        pinned=digest in provenance_module.PINNED_RECORDINGS,
        standing=recording_provenance([directory]).standing.value,
        slot_start=None if manifest is None else manifest.plan.slot_start,
        replay_dated_at=dated,
    )


# --- rendering (statuses, never values or credentials) -----------------------------------


def render_plan(plan: RecordingPlan) -> str:
    lines = [
        "STEAM PLAYERS RECORDING PLAN (nothing has been sent)",
        f"  plan id: {plan.plan_id}",
        f"  source: {plan.source} for {plan.purpose.value} at {plan.stage.value}",
        f"  policy reviewed {plan.policy_reviewed_at}, digest {plan.policy_digest[:16]}...",
        f"  slot: {plan.slot_start.isoformat()} to {plan.slot_end.isoformat()} "
        "(the plan is valid only inside it)",
        f"  requests: {plan.max_requests} (budget), one at a time, {plan.rate_limit}, no retry; "
        f"quota {plan.daily_quota}",
        f"  credential: header {plan.credential_header}, value never written",
        f"  destination: {plan.destination} (must not exist; written via "
        f"{Path(plan.destination).name}{INCOMPLETE_SUFFIX})",
    ]
    lines += [
        f"    GET {r.url}   ({t.slug}, mapping v{t.mapping_version})"
        for r, t in zip(plan.requests, plan.targets, strict=True)
    ]
    lines += [
        "",
        "  To send these requests in this slot, an owner who is authorised to do so adds:",
        f"    --live --confirm {plan.plan_id}",
        "  The result is UNVERIFIED until reviewed and pinned.",
        "",
    ]
    return "\n".join(lines)


def render_manifest(manifest: Manifest, directory: Path) -> str:
    lines = [
        f"STEAM PLAYERS RECORDING: {manifest.status.value.upper()}",
        f"  directory: {directory}",
        f"  requests sent: {manifest.requests_sent} of {manifest.plan.max_requests}",
    ]
    if manifest.stopped_because:
        lines.append(f"  STOPPED: {manifest.stopped_because}")
    for o in manifest.outcomes:
        detail = o.file or o.error or o.error_code or ""
        lines.append(f"    app {o.app_id:<9} {o.slug:<20} {o.status.value:<14} {detail}")
    lines += [
        f"  files digest (NOT pinned): {manifest.bodies_digest}",
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
        f"  files {inspection.files}, acquisition records {inspection.records}, "
        f"recorded apps {inspection.recorded}, replayed as observed {inspection.replayed_observed}",
        f"  slot {inspection.slot_start}; a replay is dated {inspection.replay_dated_at}",
        f"  digest: {inspection.digest}",
        f"  pinned: {'yes' if inspection.pinned else 'no'}; standing: {inspection.standing}",
        "  Integrity is not provenance: only an owner's approval of a reviewed recording, "
        "and a pinned digest, makes it verified.",
    ]
    lines += [f"  PROBLEM: {p}" for p in inspection.problems]
    lines.append("")
    return "\n".join(lines)
