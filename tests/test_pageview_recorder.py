"""The supervised Wikimedia recorder (U6.7). MOCK TRANSPORTS ONLY.

Every response in this file is invented by a mock transport. Nothing here contacts
Wikimedia or any other host: sockets are blocked in every test, and the one function
that builds a network transport is replaced. No value served here is a real pageview.
"""

import json
import shutil
import socket
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import unquote

import httpx
import pytest

from metaspike import cli, pageview_recorder
from metaspike.catalogue.eligibility import bootstrap_scope
from metaspike.catalogue.seed import load_seed_catalogue
from metaspike.domain.games import CanonicalGame, Catalogue, LifecycleState, SourceIdentifier
from metaspike.domain.signals import CollectionStatus, Signal, SourceErrorCode
from metaspike.domain.source_policy import Permission
from metaspike.evidence import provenance
from metaspike.evidence.acquisition import AcquisitionRecord, acquisition_standing, check_content
from metaspike.evidence.assemble import EvidenceInputs, assess_evidence
from metaspike.evidence.model import EvidenceReport, InputProvenance, Origin, Standing
from metaspike.evidence.provenance import recording_digest, recording_provenance
from metaspike.pageview_recorder import (
    ACQUISITION_DIR,
    MANIFEST,
    MAX_GAMES,
    RECORDS,
    WINDOW_DAYS,
    Manifest,
    RecorderRefused,
    RunStatus,
    TargetStatus,
    inspect_recording,
    plan_recording,
    record_pageviews,
)
from metaspike.pipeline.collection import CollectionReport, collect, window_ending
from metaspike.sources.registry import default_registry
from metaspike.sources.replay import replay_client
from metaspike.sources.wikimedia import WIKIMEDIA_PAGEVIEWS_POLICY
from tests.test_evidence import REAL_AS_OF, World

# A moment after the soak, as a future owner session might run. Mock time only.
PLANNED = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
DUE = date(2026, 10, 18)  # one day's lag behind the last whole day before PLANNED
CATALOGUE = load_seed_catalogue()
WITH_ARTICLES = sorted(
    g.slug
    for g in CATALOGUE.games
    if g.canonical_game_id in bootstrap_scope()
    and any(i.source == "wikimedia_pageviews" for i in g.source_identifiers)
)
CONTACT = "owner-contact-CANARY@example.invalid"
ARTICLES = sorted(
    i.value
    for g in CATALOGUE.games
    if g.slug in WITH_ARTICLES
    for i in g.source_identifiers
    if i.source == "wikimedia_pageviews"
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("a recorder test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)

    def no_live_transport() -> httpx.BaseTransport:
        raise AssertionError("a recorder test tried to build a live transport")

    monkeypatch.setattr(cli, "_live_transport", no_live_transport)
    monkeypatch.setenv("METASPIKE_CONTACT", CONTACT)


def body(article: str, start: date, days: int, views: Callable[[int], int]) -> bytes:
    """An invented response in the Analytics API's shape, deliberately oddly spaced."""
    items = [
        {
            "project": "en.wikipedia",
            "article": article,
            "granularity": "daily",
            "timestamp": f"{start + timedelta(days=offset):%Y%m%d}00",
            "access": "all-access",
            "agent": "user",
            "views": views(offset),
        }
        for offset in range(days)
    ]
    return (json.dumps({"items": items}, indent=3) + "\n\n").encode("utf-8")


class Source:
    """A mock Wikimedia: answers each article as told, and remembers what was asked."""

    def __init__(self, special: dict[str, httpx.Response] | None = None) -> None:
        self.special = special or {}
        self.seen: list[httpx.Request] = []
        self.served: dict[str, bytes] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        segments = request.url.raw_path.decode().split("/")
        article = unquote(segments[9])
        start = datetime.strptime(segments[11], "%Y%m%d").date()
        end = datetime.strptime(segments[12], "%Y%m%d").date()
        if article in self.special:
            return self.special[article]
        spike = 4 if article == "Minecraft" else 1
        content = body(
            article,
            start,
            (end - start).days + 1,
            lambda o: (1000 + 37 * (o % 5)) * (spike if o >= (end - start).days - 2 else 1),
        )
        self.served[article] = content
        return httpx.Response(
            200,
            content=content,
            headers={
                "content-type": "application/json; charset=utf-8",
                "etag": 'W/"mock"',
                "set-cookie": "session=CANARYCOOKIE",
                "x-request-secret": "CANARYHEADER",
            },
        )

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def ticking(start: datetime = PLANNED) -> Callable[[], datetime]:
    moments: Iterator[datetime] = (start + timedelta(seconds=n) for n in range(10_000))
    return lambda: next(moments)


def run(
    tmp_path: Path,
    source: Source,
    slugs: list[str] | None = None,
    name: str = "recording",
    sleeps: list[float] | None = None,
) -> pageview_recorder.RecordingResult:
    plan = plan_recording(CATALOGUE, slugs or WITH_ARTICLES, PLANNED, tmp_path / name)
    waited = sleeps if sleeps is not None else []
    return record_pageviews(plan, CATALOGUE, source.transport, ticking(), waited.append)


def tree(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*")}


# --- planning ----------------------------------------------------------------------------


def test_a_plan_sends_nothing_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A. The default is a plan: no transport is built and no directory appears."""
    monkeypatch.setattr(cli, "_now", lambda: PLANNED)
    code = cli.main(["record-pageviews", "--all-bootstrap", "--out", str(tmp_path / "r")])
    text = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "nothing has been sent" in text and "--live --confirm plan_" in text
    assert tree(tmp_path) == set()


def test_targets_are_exactly_the_bootstrap_games_with_an_article() -> None:
    """B and C. Explicit, bounded, bootstrap only."""
    plan = plan_recording(CATALOGUE, WITH_ARTICLES, PLANNED, Path("/tmp/never-written"))
    assert [t.slug for t in plan.targets] == WITH_ARTICLES
    assert len(plan.targets) == 14 and "fivem" not in WITH_ARTICLES
    assert plan.max_requests == len(plan.requests) == 14
    with pytest.raises(RecorderRefused, match="no Wikipedia article"):
        plan_recording(CATALOGUE, ["fivem"], PLANNED, Path("/tmp/x"))
    with pytest.raises(RecorderRefused, match="nothing is chosen by default"):
        plan_recording(CATALOGUE, [], PLANNED, Path("/tmp/x"))
    with pytest.raises(RecorderRefused, match="more than once"):
        plan_recording(CATALOGUE, ["gta-v", "gta-v"], PLANNED, Path("/tmp/x"))
    with pytest.raises(RecorderRefused, match=f"at most {MAX_GAMES}"):
        plan_recording(CATALOGUE, [f"g{n}" for n in range(16)], PLANNED, Path("/tmp/x"))
    outsider = CanonicalGame(
        canonical_game_id="00000000-0000-4000-8000-0000000000aa",
        slug="outsider",
        name="Outsider",
        lifecycle_state=LifecycleState.LIVE_SERVICE,
        source_identifiers=(
            SourceIdentifier(
                source="wikimedia_pageviews", namespace="en.wikipedia.org", value="Outsider"
            ),
        ),
    )
    wider = Catalogue(games=(*CATALOGUE.games, outsider))
    with pytest.raises(RecorderRefused, match="not a bootstrap game"):
        plan_recording(wider, ["outsider"], PLANNED, Path("/tmp/x"))


def test_one_ninety_day_window_ending_on_a_published_day() -> None:
    """D and E. The window is 90 days and never reaches a day not yet due."""
    plan = plan_recording(CATALOGUE, ["gta-v"], PLANNED, Path("/tmp/x"))
    assert plan.latest_due_day == DUE and plan.window_end == DUE
    assert (plan.window_end - plan.window_start).days + 1 == WINDOW_DAYS == 90
    assert plan.publication_lag_hours == 24
    late = plan_recording(
        CATALOGUE, ["gta-v"], datetime(2026, 10, 20, 23, 59, tzinfo=UTC), Path("/tmp/x")
    )
    assert late.latest_due_day == DUE
    midnight = plan_recording(
        CATALOGUE, ["gta-v"], datetime(2026, 10, 21, tzinfo=UTC), Path("/tmp/x")
    )
    assert midnight.latest_due_day == DUE + timedelta(days=1)
    with pytest.raises(RecorderRefused, match="had not been published"):
        plan_recording(
            CATALOGUE, ["gta-v"], PLANNED, Path("/tmp/x"), through=DUE + timedelta(days=1)
        )
    earlier = plan_recording(
        CATALOGUE, ["gta-v"], PLANNED, Path("/tmp/x"), through=date(2026, 9, 30)
    )
    assert (earlier.window_end, (earlier.window_end - earlier.window_start).days) == (
        date(2026, 9, 30),
        89,
    )
    assert "20260721/20261018" in plan.requests[0].url


def test_unsafe_or_existing_destinations_are_refused(tmp_path: Path) -> None:
    """O and P."""
    existing = tmp_path / "taken"
    existing.mkdir()
    with pytest.raises(RecorderRefused, match="already exists"):
        plan_recording(CATALOGUE, ["gta-v"], PLANNED, existing)
    (tmp_path / "half.incomplete").mkdir()
    with pytest.raises(RecorderRefused, match="already exists"):
        plan_recording(CATALOGUE, ["gta-v"], PLANNED, tmp_path / "half")
    for unsafe in (
        "artifacts/rec",
        "site/public/rec",
        "tests/fixtures/wikimedia2",
        "apps/dev-ui/rec",
    ):
        with pytest.raises(RecorderRefused, match="never written"):
            plan_recording(CATALOGUE, ["gta-v"], PLANNED, tmp_path / unsafe)
    # A destination that appears between planning and recording is still refused.
    plan = plan_recording(CATALOGUE, ["gta-v"], PLANNED, tmp_path / "later")
    (tmp_path / "later").mkdir()
    with pytest.raises(RecorderRefused, match="already exists"):
        record_pageviews(plan, CATALOGUE, Source().transport, ticking(), lambda s: None)


def test_the_policy_is_checked_and_cannot_be_bypassed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Q."""
    plan = plan_recording(CATALOGUE, ["gta-v"], PLANNED, tmp_path / "r")
    revised = WIKIMEDIA_PAGEVIEWS_POLICY.model_copy(update={"notes": "revised after planning"})
    monkeypatch.setattr(pageview_recorder, "WIKIMEDIA_PAGEVIEWS_POLICY", revised)
    with pytest.raises(RecorderRefused, match="policy changed since the plan"):
        record_pageviews(plan, CATALOGUE, Source().transport, ticking(), lambda s: None)
    refused = WIKIMEDIA_PAGEVIEWS_POLICY.model_copy(
        update={"derived_metrics_allowed": Permission.PROHIBITED}
    )
    monkeypatch.setattr(pageview_recorder, "WIKIMEDIA_PAGEVIEWS_POLICY", refused)
    with pytest.raises(RecorderRefused, match="prohibited"):
        plan_recording(CATALOGUE, ["gta-v"], PLANNED, tmp_path / "r2")
    with pytest.raises(RecorderRefused, match="prohibited"):
        record_pageviews(plan, CATALOGUE, Source().transport, ticking(), lambda s: None)
    assert tree(tmp_path) == set()


# --- recording ---------------------------------------------------------------------------


def test_a_full_recording_is_byte_exact_replayable_and_unverified(tmp_path: Path) -> None:
    """G, H, I and R."""
    source = Source()
    sleeps: list[float] = []
    result = run(tmp_path, source, sleeps=sleeps)
    manifest = result.manifest
    directory = tmp_path / "recording"
    assert manifest.status is RunStatus.COMPLETE and manifest.requests_sent == 14
    assert not (tmp_path / "recording.incomplete").exists()
    # G. Exactly the planned requests, in order, with no query and no retry.
    assert [r.url.raw_path.decode() for r in source.seen] == [
        p.path for p in manifest.plan.requests
    ]
    assert all(r.method == "GET" and not r.url.query for r in source.seen)
    # H. Every body byte for byte, in the replay format.
    for article, content in source.served.items():
        assert (directory / f"{article}.json").read_bytes() == content
    with replay_client(directory) as client:
        replayed = collect(
            CATALOGUE,
            default_registry(client),
            window_ending(DUE),
            observed_at=PLANNED,
            clock=lambda: PLANNED,
        )
    series = [s for run_ in replayed.runs for s in run_.outcomes if s.series is not None]
    assert len(series) == 14
    # I. One acquisition record per body, consistent with it.
    records = [
        AcquisitionRecord.model_validate_json(line)
        for line in (directory / ACQUISITION_DIR / RECORDS).read_text().splitlines()
    ]
    assert len(records) == 14
    for record in records:
        article = record.native_ids[0]
        check_content(record, (directory / f"{article}.json").read_bytes())
        assert (
            record.source == "wikimedia_pageviews" and record.stage.value == "private_development"
        )
        assert record.request.startswith("GET /api/rest_v1/metrics/pageviews/per-article/")
        assert record.requested_at < record.received_at
        assert record.bindings[0].native_id == article
        assert record.bindings[0].canonical_game_id in bootstrap_scope()
    # R. New and intact, and still unverified.
    assert recording_provenance([directory]).standing is Standing.UNVERIFIED
    assert (
        acquisition_standing(
            records[0], (directory / f"{records[0].native_ids[0]}.json").read_bytes()
        )
        is Standing.UNVERIFIED
    )
    inspection = inspect_recording(directory, CATALOGUE)
    assert inspection.problems == () and not inspection.pinned
    assert inspection.standing == "unverified" and inspection.replayed_series == 14
    assert inspection.digest == manifest.bodies_digest == recording_digest(directory)


def test_requests_are_sequential_under_the_policy_limit_without_retry(tmp_path: Path) -> None:
    """F. One request at a time, under the policy's documented limit, with no retry.

    The existing limiter enforces the policy's window (200 requests in any 60 seconds)
    rather than a fixed gap, so a run of at most 15 requests never has to wait. What
    holds, and is checked: each request is sent only after the previous one returned;
    the budget is one attempt per target; the plan states the policy's own limit.
    """
    sleeps: list[float] = []
    result = run(tmp_path, Source(), sleeps=sleeps)
    exchanges = [o.exchange for o in result.manifest.outcomes if o.exchange is not None]
    assert len(exchanges) == 14
    for previous, following in pairwise(exchanges):
        assert previous.received_at <= following.requested_at
    limit = WIKIMEDIA_PAGEVIEWS_POLICY.operations.rate_limit
    assert result.manifest.plan.rate_limit.startswith(
        f"{limit.max_requests} per {limit.per_seconds}s"
    )
    assert limit.max_concurrency == result.manifest.plan.max_concurrency == 1
    assert result.manifest.plan.retries == 0
    assert result.manifest.requests_sent == result.manifest.plan.max_requests == 14
    assert sleeps == []  # 14 requests are far inside 200 a minute


def test_no_secret_header_cookie_or_contact_is_written(tmp_path: Path) -> None:
    """J."""
    run(tmp_path, Source())
    written = b"".join(p.read_bytes() for p in (tmp_path / "recording").rglob("*") if p.is_file())
    for canary in (
        b"CANARYCOOKIE",
        b"CANARYHEADER",
        CONTACT.encode(),
        b"User-Agent",
        b"set-cookie",
    ):
        assert canary not in written
    manifest = Manifest.model_validate_json(
        (tmp_path / "recording" / ACQUISITION_DIR / MANIFEST).read_text()
    )
    kept = {name for o in manifest.outcomes if o.exchange for name, _ in o.exchange.headers}
    assert kept <= set(pageview_recorder.KEPT_HEADERS)


def test_the_same_inputs_record_the_same_thing(tmp_path: Path) -> None:
    """K."""
    first = run(tmp_path, Source(), name="one")
    second = run(tmp_path, Source(), name="two")
    assert first.records == second.records
    assert first.manifest.bodies_digest == second.manifest.bodies_digest
    assert first.manifest.plan.plan_id != second.manifest.plan.plan_id  # destinations differ
    assert first.manifest.outcomes == second.manifest.outcomes


def test_no_data_is_recorded_as_such_and_nothing_is_invented(tmp_path: Path) -> None:
    """L (a 404 for one article: the run goes on, and that article has no file)."""
    missing = httpx.Response(404, json={"status": 404, "title": "Not found."})
    result = run(tmp_path, Source({"Dota_2": missing}))
    outcome = {o.slug: o for o in result.manifest.outcomes}
    assert outcome["dota-2"].status is TargetStatus.NO_DATA and outcome["dota-2"].file is None
    assert not (tmp_path / "recording" / "Dota_2.json").exists()
    assert result.manifest.status is RunStatus.COMPLETE
    assert sum(o.status is TargetStatus.RECORDED for o in result.manifest.outcomes) == 13


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(503, text="unavailable"), "http_error"),  # L: a source failure
        (httpx.Response(429, headers={"retry-after": "30"}), "rate_limited"),  # M
        (httpx.Response(403, text="forbidden"), "http_error"),
        (httpx.Response(200, content=b'{"items": "not a list"}'), "invalid_response"),  # N
        (httpx.Response(200, content=b"<html>not json</html>"), "invalid_response"),  # N
    ],
)
def test_a_failure_stops_the_run_and_keeps_only_what_succeeded(
    tmp_path: Path, response: httpx.Response, code: str
) -> None:
    source = Source({"Escape_from_Tarkov": response})
    result = run(tmp_path, source)
    outcomes = result.manifest.outcomes
    by_slug = {o.slug: o for o in outcomes}
    failed = by_slug["escape-from-tarkov"]
    assert failed.status is TargetStatus.FAILED and failed.error_code == code
    assert failed.file is None and not (tmp_path / "recording" / "Escape_from_Tarkov.json").exists()
    # Exactly one request for it: no retry. Nothing after it is sent.
    assert sum("Escape_from_Tarkov" in r.url.raw_path.decode() for r in source.seen) == 1
    after = list(outcomes[outcomes.index(failed) + 1 :])
    assert after and all(o.status is TargetStatus.NOT_ATTEMPTED for o in after)
    assert result.manifest.requests_sent == outcomes.index(failed) + 1
    assert result.manifest.status is RunStatus.PARTIAL
    assert result.manifest.stopped_because is not None
    # I. Intact bytes and records, inspectable for diagnosis, and never pin-ready.
    inspection = inspect_recording(tmp_path / "recording", CATALOGUE)
    assert inspection.problems == () and inspection.integrity == "intact"
    assert inspection.run_status == "partial" and not inspection.review_ready
    assert "PARTIAL" in inspection.verdict and "NOT ready for pinning" in inspection.verdict


def test_a_transport_failure_never_falls_back_to_the_network(tmp_path: Path) -> None:
    """T. The mock refuses to connect; the run stops; no socket was ever opened."""

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("mock: unreachable", request=request)

    plan = plan_recording(CATALOGUE, WITH_ARTICLES, PLANNED, tmp_path / "r")
    result = record_pageviews(
        plan, CATALOGUE, httpx.MockTransport(unreachable), ticking(), lambda s: None
    )
    assert result.manifest.status is RunStatus.FAILED
    assert result.manifest.requests_sent == 1
    assert {o.status for o in result.manifest.outcomes[1:]} == {TargetStatus.NOT_ATTEMPTED}
    assert not list((tmp_path / "r").glob("*.json"))


def test_altered_or_extra_content_is_found_on_inspection(tmp_path: Path) -> None:
    """S."""
    run(tmp_path, Source())
    directory = tmp_path / "recording"
    target = directory / "Minecraft.json"
    target.write_bytes(target.read_bytes().replace(b"1000", b"1001", 1))
    (directory / "Invented.json").write_bytes(b'{"items": []}')
    problems = " ".join(inspect_recording(directory, CATALOGUE).problems)
    assert "Minecraft.json" in problems and "not what was acquired" in problems
    assert "Invented.json: no acquisition record" in problems
    assert "digest is not the one written at acquisition" in problems


def test_the_command_records_only_with_the_matching_plan_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The live path, exercised with a mock transport put where the network one would be."""
    source = Source()
    monkeypatch.setattr(cli, "_now", ticking())
    monkeypatch.setattr(cli, "_recorder_sleep", lambda s: None)
    out = tmp_path / "rec"
    base = ["record-pageviews", "--game", "minecraft", "--game", "gta-v", "--out", str(out)]
    assert cli.main([*base, "--json"]) == cli.EXIT_OK
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    # Without the plan id, or with another, nothing is built, sent or written.
    assert cli.main([*base, "--live"]) == cli.EXIT_SOURCE_POLICY
    assert cli.main([*base, "--live", "--confirm", "plan_" + "0" * 32]) == cli.EXIT_SOURCE_POLICY
    assert not out.exists() and source.seen == []
    capsys.readouterr()
    monkeypatch.setattr(cli, "_live_transport", lambda: source.transport)
    assert cli.main([*base, "--live", "--confirm", plan_id]) == cli.EXIT_OK
    text = capsys.readouterr().out
    assert "WIKIMEDIA RECORDING: COMPLETE" in text and "NOT pinned" in text
    assert len(source.seen) == 2
    assert cli.main(["record-pageviews", "--inspect", str(out)]) == cli.EXIT_OK
    assert "standing: unverified" in capsys.readouterr().out
    # Recording again into the same place is refused.
    assert cli.main([*base, "--live", "--confirm", plan_id]) == cli.EXIT_SOURCE_POLICY


def test_no_production_or_cloud_path_is_reachable_from_the_recorder() -> None:
    """W."""
    text = Path(pageview_recorder.__file__).read_text().lower()
    for marker in (
        "google",
        "gcloud",
        "bigquery",
        "gcs",
        "storage.cloud",
        "terraform",
        "artifacts_dir",
    ):
        assert marker not in text


# --- end to end, with a test-only trust setup ------------------------------------------------


def test_a_reviewed_and_pinned_mock_recording_reaches_the_opportunity_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """U. Mock response -> recorder -> records -> integrity -> replay -> unverified ->
    TEST-ONLY pin -> verified evidence -> opportunity assessment -> private report.

    The pin exists only inside this test (the real PINNED_RECORDINGS is untouched), and
    the values are invented: this proves the path, not anything about any game.
    """
    run(tmp_path, Source())
    directory = tmp_path / "recording"
    inspection = inspect_recording(directory, CATALOGUE)
    assert inspection.problems == () and inspection.standing == "unverified"

    def private_report(name: str) -> dict[str, Any]:
        out = tmp_path / name
        assert (
            cli.main(
                [
                    "evidence",
                    "--as-of",
                    (PLANNED + timedelta(hours=1)).isoformat(),
                    "--wikimedia-replay",
                    str(directory),
                    "--recorded-through",
                    DUE.isoformat(),
                    "--opportunities",
                    "--json",
                    "--out",
                    str(out),
                ]
            )
            == cli.EXIT_OK
        )
        capsys.readouterr()
        return dict(json.loads(out.read_text()))

    def standing(report: dict[str, Any], slug: str) -> str:
        game = next(a for a in report["assessments"] if a["slug"] == slug)
        return str(
            next(s for s in game["sources"] if s["source"] == "wikimedia_pageviews")["standing"]
        )

    before = private_report("before.json")
    assert standing(before, "minecraft") == "unverified"
    assert {e["readiness"] for e in before["entries"]} == {"insufficient_evidence"}

    pinned = MappingProxyType({**provenance.PINNED_RECORDINGS, inspection.digest: "TEST-ONLY mock"})
    monkeypatch.setattr(provenance, "PINNED_RECORDINGS", pinned)
    after = private_report("after.json")
    assert standing(after, "minecraft") == "verified"
    minecraft = next(a for a in after["assessments"] if a["slug"] == "minecraft")
    # The invented rise is read through the unchanged rubric: a watchlist, never more.
    assert minecraft["readiness"] == "provisional_watchlist"
    assert after["distribution"] == "private_development_only"
    assert not (tmp_path / "artifacts").exists()


# --- U6.7.1: acquisition time is enforced; integrity, completeness and readiness differ -------


def pin_for_this_test(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    """A TEST-ONLY trust table. The real PINNED_RECORDINGS is never changed."""
    digest = recording_digest(directory)
    pinned = MappingProxyType({**provenance.PINNED_RECORDINGS, digest: "TEST-ONLY"})
    monkeypatch.setattr(provenance, "PINNED_RECORDINGS", pinned)


def evidence_at(
    as_of: datetime, directory: Path, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str]:
    code = cli.main(
        [
            "evidence",
            "--as-of",
            as_of.isoformat(),
            "--wikimedia-replay",
            str(directory),
            "--recorded-through",
            DUE.isoformat(),
            "--opportunities",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def received(directory: Path) -> datetime:
    check = pageview_recorder.check_recording(directory)
    assert check.received_until is not None
    return check.received_until


def test_a_response_is_usable_only_after_it_was_received(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A and B, at the command line and at the library boundary, pinned or not."""
    run(tmp_path, Source())
    directory = tmp_path / "recording"
    last = received(directory)
    for pinned in (False, True):
        if pinned:
            pin_for_this_test(monkeypatch, directory)
        # A. At or after the last receipt: read.
        code, _ = evidence_at(last, directory, capsys)
        assert code == cli.EXIT_OK
        # B. One second before it: refused, and nothing is assessed.
        code, text = evidence_at(last - timedelta(seconds=1), directory, capsys)
        assert code == cli.EXIT_SOURCE_POLICY
        assert "could not have had them" in text and '"assessments"' not in text

    from metaspike.evidence.assemble import assess_evidence
    from tests.test_evidence import World

    world = World()
    with replay_client(directory) as client:
        report = collect(
            world.state.catalogue,
            default_registry(client),
            window_ending(DUE),
            observed_at=last,
            clock=lambda: last,
        )
    inputs = world.inputs(wikimedia=report, wikimedia_provenance=recording_provenance([directory]))
    assert inputs.wikimedia_provenance.standing is Standing.VERIFIED  # the test-only pin
    with pytest.raises(pageview_recorder.RecordingRefused, match="could not have had them"):
        assess_evidence(inputs, last - timedelta(seconds=1), world.scope)
    assert assess_evidence(inputs, last, world.scope).as_of == last


def test_the_legacy_pinned_fixture_keeps_its_approved_behaviour() -> None:
    """C. The U6.0.1 pageview fixture has no acquisition records and is not one."""
    from metaspike.evidence.assemble import assess_evidence
    from tests.test_evidence import REAL_AS_OF, World

    legacy = Path(__file__).parent / "fixtures" / "wikimedia"
    honest = recording_provenance([legacy])
    assert honest.standing is Standing.VERIFIED
    assert recording_digest(legacy) in pageview_recorder.LEGACY_PAGEVIEW_RECORDINGS
    assert not pageview_recorder.is_recorder_output(legacy)
    pageview_recorder.require_usable_recording(honest, REAL_AS_OF)
    world = World()
    report = assess_evidence(world.inputs(), REAL_AS_OF, world.scope)
    assert report.inputs["wikimedia_pageviews"].standing is Standing.VERIFIED


def rewrite_records(
    directory: Path, change: Callable[[list[AcquisitionRecord]], list[str]]
) -> None:
    path = directory / ACQUISITION_DIR / RECORDS
    records = [
        AcquisitionRecord.model_validate_json(line) for line in path.read_text().splitlines()
    ]
    path.write_text("".join(line + "\n" for line in change(records)))


def sync_record_ids(directory: Path) -> None:
    """Point the manifest at the records as they now are, so deeper checks are reached."""
    records = {
        AcquisitionRecord.model_validate_json(line).native_ids[0]: line
        for line in (directory / ACQUISITION_DIR / RECORDS).read_text().splitlines()
    }
    path = directory / ACQUISITION_DIR / MANIFEST
    data = json.loads(path.read_text())
    for outcome in data["outcomes"]:
        if outcome["article"] in records:
            outcome["record_id"] = json.loads(records[outcome["article"]])["record_id"]
    path.write_text(json.dumps(data))


def remade(record: AcquisitionRecord, directory: Path, **changes: object) -> str:
    """A self-consistent record (its id matches its content) that differs as asked."""
    from metaspike.domain.source_policy import DeploymentStage, SourcePurpose
    from metaspike.evidence.acquisition import record_acquisition

    arguments: dict[str, object] = {
        "policy": WIKIMEDIA_PAGEVIEWS_POLICY,
        "purpose": SourcePurpose.DEMAND_SIGNALS,
        "stage": DeploymentStage.PRIVATE_DEVELOPMENT,
        "request": record.request,
        "raw": (directory / f"{record.native_ids[0]}.json").read_bytes(),
        "requested_at": record.requested_at,
        "received_at": record.received_at,
        "native_ids": record.native_ids,
        "bindings": record.bindings,
    }
    arguments.update(changes)
    return record_acquisition(**arguments).model_dump_json()  # type: ignore[arg-type]


def problems_after(tmp_path: Path, change: Callable[[Path], None], name: str) -> str:
    run(tmp_path, Source(), name=name)
    directory = tmp_path / name
    change(directory)
    check = pageview_recorder.check_recording(directory)
    assert not check.intact and not check.review_ready
    with pytest.raises(pageview_recorder.RecordingRefused, match="do not hold together"):
        pageview_recorder.require_usable_recording(
            recording_provenance([directory]), PLANNED + timedelta(days=1)
        )
    return " | ".join(check.problems)


def test_altered_missing_duplicate_or_misattributed_records_are_refused(tmp_path: Path) -> None:
    """D, E, F and G."""

    def edit_manifest(directory: Path) -> None:
        path = directory / ACQUISITION_DIR / MANIFEST
        data = json.loads(path.read_text())
        data["outcomes"][0]["status"] = "no_data"
        path.write_text(json.dumps(data))

    def edit_plan(directory: Path) -> None:
        path = directory / ACQUISITION_DIR / MANIFEST
        data = json.loads(path.read_text())
        data["plan"]["window_start"] = "2026-07-01"
        path.write_text(json.dumps(data))

    def edit_record_time(directory: Path) -> None:
        rewrite_records(
            directory,
            lambda records: [
                r.model_dump_json().replace(
                    r.received_at.isoformat().replace("+00:00", "Z"), "2026-10-21T00:00:00Z"
                )
                if i == 0
                else r.model_dump_json()
                for i, r in enumerate(records)
            ],
        )

    def drop_record(directory: Path) -> None:
        rewrite_records(directory, lambda records: [r.model_dump_json() for r in records[1:]])

    def duplicate_record(directory: Path) -> None:
        rewrite_records(
            directory, lambda records: [r.model_dump_json() for r in [*records, records[0]]]
        )

    def wrong_game(directory: Path) -> None:
        def change(records: list[AcquisitionRecord]) -> list[str]:
            first = records[0]
            binding = first.bindings[0].model_copy(
                update={"canonical_game_id": "00000000-0000-4000-8000-0000000000bb"}
            )
            return [remade(first, directory, bindings=(binding,))] + [
                r.model_dump_json() for r in records[1:]
            ]

        rewrite_records(directory, change)
        sync_record_ids(directory)

    def wrong_request(directory: Path) -> None:
        def change(records: list[AcquisitionRecord]) -> list[str]:
            first = records[0]
            return [remade(first, directory, request=first.request + "x")] + [
                r.model_dump_json() for r in records[1:]
            ]

        rewrite_records(directory, change)
        sync_record_ids(directory)

    def wrong_times(directory: Path) -> None:
        def change(records: list[AcquisitionRecord]) -> list[str]:
            first = records[0]
            return [
                remade(
                    first,
                    directory,
                    requested_at=first.requested_at - timedelta(hours=1),
                    received_at=first.received_at - timedelta(hours=1),
                )
            ] + [r.model_dump_json() for r in records[1:]]

        rewrite_records(directory, change)
        sync_record_ids(directory)

    def extra_file(directory: Path) -> None:
        (directory / "notes.txt").write_text("hello")

    expected = {
        "manifest": (edit_manifest, "yet a body is kept"),
        "plan": (edit_plan, "plan was edited"),
        "record-time": (edit_record_time, "edited after it was made"),
        "drop": (drop_record, "no acquisition record"),
        "duplicate": (duplicate_record, "more than one acquisition record"),
        "game": (wrong_game, "binds another game"),
        "request": (wrong_request, "not the planned request"),
        "times": (wrong_times, "times are not the request's"),
        "extra": (extra_file, "unexpected file notes.txt"),
    }
    for name, (change, message) in expected.items():
        assert message in problems_after(tmp_path, change, name), name


def test_completeness_and_readiness_are_reported_apart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """H, I, J and K, and the exit codes that say them."""
    run(tmp_path, Source(), name="complete")
    run(tmp_path, Source({"Escape_from_Tarkov": httpx.Response(503)}), name="partial")
    missing = httpx.Response(404, json={"status": 404})
    run(tmp_path, Source(dict.fromkeys(ARTICLES, missing)), name="empty")

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("mock: unreachable", request=request)

    plan = plan_recording(CATALOGUE, WITH_ARTICLES, PLANNED, tmp_path / "failed")
    record_pageviews(plan, CATALOGUE, httpx.MockTransport(unreachable), ticking(), lambda s: None)

    expectations = {
        "complete": ("complete", True, cli.EXIT_OK, "INTACT AND COMPLETE"),
        "partial": ("partial", False, cli.EXIT_COLLECTION_FAILED, "INTACT BUT PARTIAL"),
        "empty": ("failed", False, cli.EXIT_COLLECTION_FAILED, "INTACT BUT FAILED"),
        "failed": ("failed", False, cli.EXIT_COLLECTION_FAILED, "INTACT BUT FAILED"),
    }
    for name, (status, ready, code, verdict) in expectations.items():
        directory = tmp_path / name
        inspection = inspect_recording(directory, CATALOGUE)
        assert inspection.integrity == "intact", name
        assert (inspection.run_status, inspection.review_ready) == (status, ready), name
        assert verdict in inspection.verdict
        assert cli.main(["record-pageviews", "--inspect", str(directory)]) == code
        assert verdict in capsys.readouterr().out
    # K. Every target answered "no data": complete in effect, but nothing to stand on.
    assert inspect_recording(tmp_path / "empty", CATALOGUE).bodies == 0
    # Unpinned, a partial run may be read privately as unverified: diagnostic, counted
    # for nothing.
    pageview_recorder.require_usable_recording(
        recording_provenance([tmp_path / "partial"]), PLANNED + timedelta(days=1)
    )
    # Pinned in a test-only table, a partial, failed or empty run is still refused.
    for name in ("partial", "empty", "failed"):
        directory = tmp_path / name
        pin_for_this_test(monkeypatch, directory)
        with pytest.raises(pageview_recorder.RecordingRefused, match="never verified demand"):
            pageview_recorder.require_usable_recording(
                recording_provenance([directory]), PLANNED + timedelta(days=1)
            )


def test_a_pinned_recording_without_acquisition_records_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting the acquisition files never turns a new recording into a legacy one."""
    pageview_recorder_pins = provenance.PINNED_RECORDINGS
    run(tmp_path, Source())
    directory = tmp_path / "recording"
    for path in (directory / ACQUISITION_DIR).iterdir():
        path.unlink()
    (directory / ACQUISITION_DIR).rmdir()
    pin_for_this_test(monkeypatch, directory)
    with pytest.raises(pageview_recorder.RecordingRefused, match="must carry its acquisition"):
        pageview_recorder.require_usable_recording(
            recording_provenance([directory]), PLANNED + timedelta(days=1)
        )
    # Unpinned, it is simply unverified files: read, and counted for nothing.
    monkeypatch.setattr(provenance, "PINNED_RECORDINGS", pageview_recorder_pins)
    assert recording_provenance([directory]).standing is Standing.UNVERIFIED


def test_history_reconstructs_periods_and_does_not_claim_retrieval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two modes, stated and tested.

    A point-in-time reading refuses a moment before receipt (above). A historical
    evaluation is a retrospective reconstruction of source periods under the
    publication lag: its moments may precede retrieval, as validation_protocol_v1
    allows, provided the recording is intact and complete. A partial one is refused.
    """
    from metaspike.opportunity.history import EvaluationMode, HistoryRefused, evaluate_history

    run(tmp_path, Source(), name="complete")
    run(tmp_path, Source({"Escape_from_Tarkov": httpx.Response(503)}), name="partial")
    lags = {"wikimedia_pageviews": timedelta(days=1)}

    def evaluate(directory: Path) -> object:
        pin_for_this_test(monkeypatch, directory)
        with replay_client(directory) as client:
            report = collect(
                CATALOGUE,
                default_registry(client),
                window_ending(DUE),
                observed_at=received(directory),
                clock=lambda: received(directory),
            )
        return evaluate_history(
            CATALOGUE,
            report,
            lags,
            date(2026, 10, 1),
            date(2026, 10, 3),
            1,
            ["minecraft", "gta-v"],
            recording_provenance([directory]),
            EvaluationMode.RECORDED,
            PLANNED,
        )

    result = evaluate(tmp_path / "complete")
    assert result.demand_origin.standing is Standing.VERIFIED  # type: ignore[attr-defined]
    assert all(s.as_of < received(tmp_path / "complete") for s in result.snapshots)  # type: ignore[attr-defined]
    with pytest.raises(HistoryRefused, match="intact and complete"):
        evaluate(tmp_path / "partial")


# --- U6.7.2: verified pageviews are bound to their pinned directory's content --------------

LEGACY = Path(__file__).parent / "fixtures" / "wikimedia"


@pytest.fixture(scope="module")
def world() -> World:
    return World()


def assess(world: World, inputs: EvidenceInputs, as_of: datetime | None = None) -> EvidenceReport:
    return assess_evidence(inputs, as_of or REAL_AS_OF, world.scope)


Change = Callable[[list[Signal]], list[Signal]]


def with_signals(report: CollectionReport, change: Change) -> CollectionReport:
    runs = tuple(
        run_.model_copy(update={"outcomes": tuple(change(list(run_.outcomes)))})
        for run_ in report.runs
    )
    return report.model_copy(update={"runs": runs})


def test_honest_pinned_pageviews_are_accepted_as_verified(world: World) -> None:
    """A."""
    report = assess(world, world.inputs())
    assert report.inputs["wikimedia_pageviews"].standing is Standing.VERIFIED
    minecraft = report.game(world.ids["minecraft"])
    assert "wikimedia_readership" in {f.value for f in minecraft.verified_demand_families}


def test_a_pinned_digest_claimed_for_another_directory_is_refused(world: World) -> None:
    """B."""
    forged = InputProvenance(
        origin=Origin.RECORDED_RESPONSE,
        reference=str(LEGACY.parent / "catalogue"),
        digests=(recording_digest(LEGACY),),
    )
    with pytest.raises(ValueError, match="no longer has the claimed pinned content"):
        assess(world, world.inputs(wikimedia_provenance=forged))


def test_altered_in_memory_pageviews_cannot_claim_verified(world: World) -> None:
    """C, D and E: values, identity, key, failures, a missing signal, a duplicate."""

    def first(signals: list[Signal], status: CollectionStatus) -> int:
        return next(i for i, s in enumerate(signals) if s.status is status)

    def value(signals: list[Signal]) -> list[Signal]:
        i = first(signals, CollectionStatus.COLLECTED)
        series = signals[i].series
        assert series is not None
        points = list(series.points)
        j = next(k for k, p in enumerate(points) if p.value is not None)
        old = points[j].value
        assert old is not None
        points[j] = points[j].model_copy(update={"value": old + 1})
        signals[i] = signals[i].model_copy(
            update={"series": series.model_copy(update={"points": tuple(points)})}
        )
        return signals

    def identity(signals: list[Signal]) -> list[Signal]:
        i = first(signals, CollectionStatus.COLLECTED)
        stranger = "00000000-0000-4000-8000-0000000000cc"  # a game nothing else claims
        signals[i] = signals[i].model_copy(update={"canonical_game_id": stranger})
        return signals

    def key(signals: list[Signal]) -> list[Signal]:
        i = first(signals, CollectionStatus.COLLECTED)
        changed = signals[i].key.model_copy(update={"source": "another_source"})
        signals[i] = signals[i].model_copy(update={"key": changed})
        return signals

    def failure(signals: list[Signal]) -> list[Signal]:
        i = first(signals, CollectionStatus.FAILED)
        signals[i] = signals[i].model_copy(update={"error_code": SourceErrorCode.HTTP_ERROR})
        return signals

    def missing(signals: list[Signal]) -> list[Signal]:
        del signals[first(signals, CollectionStatus.FAILED)]
        return signals

    def duplicate(signals: list[Signal]) -> list[Signal]:
        return [*signals, signals[first(signals, CollectionStatus.COLLECTED)]]

    for change in (value, identity, key, failure, missing):
        altered = with_signals(world.pageviews, change)
        with pytest.raises(ValueError, match="not what the pinned recording holds"):
            assess(world, world.inputs(wikimedia=altered))
    with pytest.raises(ValueError, match="more than one entry"):
        assess(world, world.inputs(wikimedia=with_signals(world.pageviews, duplicate)))


def test_a_pinned_recorder_recording_is_bound_and_time_guarded(
    world: World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F, G and H at the library boundary, with a TEST-ONLY pin."""
    run(tmp_path, Source(), name="complete")
    run(tmp_path, Source({"Escape_from_Tarkov": httpx.Response(503)}), name="partial")

    def inputs_for(
        directory: Path, as_of: datetime, change: Change | None = None
    ) -> EvidenceInputs:
        with replay_client(directory) as client:
            report = collect(
                world.state.catalogue,
                default_registry(client),
                window_ending(DUE),
                observed_at=as_of,
                clock=lambda: as_of,
            )
        if change is not None:
            report = with_signals(report, change)
        return world.inputs(
            wikimedia=report, wikimedia_provenance=recording_provenance([directory])
        )

    complete = tmp_path / "complete"
    pin_for_this_test(monkeypatch, complete)
    last = received(complete)
    # F. After receipt, the genuine replay is verified.
    report = assess(world, inputs_for(complete, last), last)
    assert report.inputs["wikimedia_pageviews"].standing is Standing.VERIFIED
    # G. Before receipt, refused.
    with pytest.raises(ValueError, match="could not have had them"):
        assess(world, inputs_for(complete, last), last - timedelta(seconds=1))

    # The same pinned directory with altered in-memory data, after receipt: refused.
    def bump(signals: list[Signal]) -> list[Signal]:
        return [*signals, signals[0]]

    with pytest.raises(ValueError, match="more than one entry"):
        assess(world, inputs_for(complete, last, bump), last)
    # H. A pinned partial recording, and a pinned corrupted one: refused.
    partial = tmp_path / "partial"
    pin_for_this_test(monkeypatch, partial)
    with pytest.raises(ValueError, match="never verified demand evidence"):
        assess(world, inputs_for(partial, received(partial)), received(partial))
    body = complete / "Minecraft.json"
    original = body.read_bytes()
    body.write_bytes(original.replace(b"1000", b"1001", 1))
    pin_for_this_test(monkeypatch, complete)  # pin even the corrupted bytes, in this test
    with pytest.raises(ValueError, match="do not hold together"):
        assess(world, inputs_for(complete, last), last)


def test_unverified_and_synthetic_pageviews_are_never_counted_as_verified(
    world: World,
    tmp_path: Path,
) -> None:
    """I and J."""
    copy = tmp_path / "copy"
    shutil.copytree(LEGACY, copy)
    (copy / "Minecraft.json").write_bytes((copy / "Minecraft.json").read_bytes() + b" ")
    copied = recording_provenance([copy])
    assert copied.standing is Standing.UNVERIFIED
    synthetic = InputProvenance(origin=Origin.SYNTHETIC, reference="synthetic")
    for provenance_ in (
        copied,
        synthetic,
        recording_provenance([LEGACY]).model_copy(
            update={
                "origin": Origin.UNVERIFIED_FILES,
                "standing": Standing.UNVERIFIED,
                "digests": (),
            }
        ),
    ):
        report = assess(world, world.inputs(wikimedia_provenance=provenance_))
        assert report.inputs["wikimedia_pageviews"].standing is provenance_.standing
        for game in report.games:
            assert "wikimedia_readership" not in {f.value for f in game.verified_demand_families}
