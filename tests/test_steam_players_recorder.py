"""The supervised Steam concurrent-player recorder, entirely offline.

Every response comes from a mock transport; no test reaches Steam, reads a real key or
writes outside its temporary directory. The recorder must plan without sending, sample
one slot of at most 8 eligible apps with at most 8 requests, keep replay-format files
with acquisition records and no credential, stop on trouble, and leave every recording
UNVERIFIED.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from metaspike import cli
from metaspike import steam_players_recorder as recorder
from metaspike.catalogue.eligibility import bootstrap_scope, sampling_targets, steam_eligibility
from metaspike.catalogue.generate import CatalogueState, generate_catalogue
from metaspike.domain.samples import SampleStatus
from metaspike.domain.source_policy import DeploymentStage, StageClearance
from metaspike.domain.source_records import SourceRecord
from metaspike.evidence.acquisition import AcquisitionRecord
from metaspike.pageview_recorder import RecorderRefused, RunStatus
from metaspike.pipeline.sampling import run_sampling_slot
from metaspike.resolution.recorded import load_recorded_catalogue
from metaspike.sources.credentials import STEAM_WEB_API_KEY_ENV, Secret
from metaspike.sources.limiter import LimiterPool
from metaspike.sources.sampling_sources import (
    build_steam_players,
    replay_clock,
    replay_sampling_client,
    sampling_registry,
)
from metaspike.sources.steam_players import KEY_HEADER, PUBLIC_HOST, STEAM_CURRENT_PLAYERS_POLICY
from metaspike.storage.samples import LocalSampleStore

FIXTURES = Path(__file__).parent / "fixtures"
AS_OF = datetime(2026, 10, 5, tzinfo=UTC)
PLANNED = datetime(2026, 10, 22, 12, 1, tzinfo=UTC)  # slot 12:00 to 12:15
EIGHT_APPS = ["570", "730", "252490", "275850", "553850", "1086940", "1245620", "3932890"]
KEY = "fake-steam-key-used-only-in-tests"
COUNTS = {app: 1000 + index for index, app in enumerate(EIGHT_APPS)}
_STATE: list[tuple[CatalogueState, list[SourceRecord]]] = []


def catalogue() -> tuple[CatalogueState, list[SourceRecord]]:
    if not _STATE:
        records = [
            record
            for directory in ("catalogue", "bootstrap_catalogue")
            for record in load_recorded_catalogue(FIXTURES / directory).values()
        ]
        _STATE.append((generate_catalogue(records, as_of=AS_OF), records))
    return _STATE[0]


class Ticking:
    """A clock that moves one second each time it is read."""

    def __init__(self, start: datetime) -> None:
        self.moment = start

    def __call__(self) -> datetime:
        self.moment += timedelta(seconds=1)
        return self.moment


def count_body(app: str) -> bytes:
    return json.dumps({"response": {"player_count": COUNTS[app], "result": 1}}).encode()


def steam(
    special: dict[str, Callable[[httpx.Request], httpx.Response]] | None = None,
) -> tuple[httpx.MockTransport, list[str]]:
    """A fake Steam that answers every app with a count, unless told otherwise."""
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == PUBLIC_HOST
        assert request.headers[KEY_HEADER] == KEY  # sent, and only ever in the header
        assert KEY not in str(request.url)
        app = request.url.params["appid"]
        asked.append(app)
        if special and app in special:
            return special[app](request)
        return httpx.Response(
            200,
            content=count_body(app),
            headers={
                "content-type": "application/json; charset=UTF-8",
                "cache-control": "public, max-age=436",
                "server": "fake",
            },
        )

    return httpx.MockTransport(handler), asked


def plan_for(tmp_path: Path, apps: list[str] = EIGHT_APPS, at: datetime = PLANNED) -> Any:
    state, records = catalogue()
    return recorder.plan_recording(state, records, apps, at, tmp_path / "rec")


def run(
    plan: Any, transport: httpx.BaseTransport, clock: Callable[[], datetime] | None = None
) -> Any:
    return recorder.record_players(
        plan,
        transport,
        Secret(KEY),
        clock or Ticking(PLANNED + timedelta(seconds=30)),
        lambda s: None,
    )


def every_byte(directory: Path) -> bytes:
    return b"".join(p.read_bytes() for p in sorted(directory.rglob("*")) if p.is_file())


# --- planning ----------------------------------------------------------------------------


def test_planning_sends_nothing_and_names_one_slot_and_eight_requests(tmp_path: Path) -> None:
    plan = plan_for(tmp_path)
    assert [t.app_id for t in plan.targets] == sorted(EIGHT_APPS, key=int)
    assert plan.max_requests == len(plan.requests) == 8 and plan.retries == 0
    assert plan.slot_start == datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    assert plan.slot_end - plan.slot_start == timedelta(minutes=15)
    assert all(r.url.endswith(f"?appid={r.app_id}") for r in plan.requests)
    assert {t.slug for t in plan.targets if t.app_id == "730"} == {"counter-strike-2"}
    assert plan.credential_header == KEY_HEADER and KEY not in plan.model_dump_json()
    assert not (tmp_path / "rec").exists()
    # The id covers the slot: planned again in the same slot it is the same plan, in the
    # next slot it is another, so a confirmation never carries over to a later slot.
    assert plan_for(tmp_path, at=PLANNED + timedelta(minutes=10)).plan_id == plan.plan_id
    assert plan_for(tmp_path, at=PLANNED + timedelta(minutes=15)).plan_id != plan.plan_id


@pytest.mark.parametrize(
    ("apps", "message"),
    [
        ([], "name the apps"),
        (["730", "730"], "more than once"),
        ([*EIGHT_APPS, "440"], "at most 8"),
        (["440"], "not an eligible bootstrap app"),
    ],
)
def test_planning_refuses_anything_outside_the_bounded_scope(
    tmp_path: Path, apps: list[str], message: str
) -> None:
    with pytest.raises(RecorderRefused, match=message):
        plan_for(tmp_path, apps)


def test_planning_refuses_a_published_or_existing_destination(tmp_path: Path) -> None:
    state, records = catalogue()
    with pytest.raises(RecorderRefused, match="fixtures"):
        recorder.plan_recording(state, records, ["730"], PLANNED, tmp_path / "fixtures" / "x")
    (tmp_path / "rec").mkdir()
    with pytest.raises(RecorderRefused, match="already exists"):
        plan_for(tmp_path, ["730"])


# --- a complete session ------------------------------------------------------------------


def test_a_complete_session_keeps_replayable_files_records_and_no_credential(
    tmp_path: Path,
) -> None:
    plan = plan_for(tmp_path)
    transport, asked = steam()
    result = run(plan, transport)
    manifest = result.manifest
    assert manifest.status is RunStatus.COMPLETE and manifest.requests_sent == 8
    assert asked == [r.app_id for r in plan.requests]
    directory = result.directory
    files = sorted(p.name for p in directory.glob("*.json"))
    assert files == sorted(f"current_players_{app}.json" for app in EIGHT_APPS)
    assert KEY.encode() not in every_byte(directory)
    assert KEY_HEADER.encode() not in b"".join(p.read_bytes() for p in directory.glob("*.json"))
    assert manifest.standing == "unverified" and "UNVERIFIED" in manifest.trust

    # Each file is the replay envelope, dated when the answer arrived, with the body as sent.
    records = {r.native_ids[0]: r for r in result.records}
    for app in EIGHT_APPS:
        document = json.loads((directory / f"current_players_{app}.json").read_text())
        assert document["request"] == {
            "method": "GET",
            "host": PUBLIC_HOST,
            "path": "/ISteamUserStats/GetNumberOfCurrentPlayers/v1/",
            "query": {"appid": int(app)},
        }
        assert document["response"]["body_text"].encode() == count_body(app)
        assert document["response"]["other_header_names"] == ["content-length", "server"]
        assert datetime.fromisoformat(document["recorded_at"]) == records[app].received_at
        assert records[app].bindings[0].canonical_game_id == next(
            t.canonical_game_id for t in plan.targets if t.app_id == app
        )
        assert records[app].request == f"GET {recorder.REQUEST_PATH}?appid={app}"

    inspection = recorder.inspect_recording(directory)
    assert inspection.problems == () and inspection.review_ready
    assert inspection.replayed_observed == 8
    assert inspection.pinned is False and inspection.standing == "unverified"
    assert inspection.slot_start == plan.slot_start
    assert inspection.replay_dated_at is not None
    assert plan.slot_start <= inspection.replay_dated_at < plan.slot_end


def test_the_existing_sample_command_path_replays_it_at_its_own_moment(tmp_path: Path) -> None:
    plan = plan_for(tmp_path)
    result = run(plan, steam()[0])
    state, records = catalogue()
    targets = sampling_targets(state, steam_eligibility(state, records), bootstrap_scope())
    clock = replay_clock(result.directory)
    store = LocalSampleStore(tmp_path / "samples", lambda _: STEAM_CURRENT_PLAYERS_POLICY)
    with replay_sampling_client(result.directory) as client:
        adapter = build_steam_players(client, LimiterPool(), now=clock, api_key=Secret("replay"))
        sampling_registry(adapter)
        report = run_sampling_slot(targets, adapter, store, now=clock)
    assert report.count(SampleStatus.OBSERVED) == 8
    # Dated inside the slot the recording was made in, never at the replay's wall clock.
    assert report.scheduled_for == plan.slot_start
    assert {o.slot_start for o in report.outcomes} == {plan.slot_start}


def test_an_app_steam_has_nothing_for_is_listed_and_the_session_continues(tmp_path: Path) -> None:
    transport, asked = steam({"570": lambda r: httpx.Response(404)})
    result = run(plan_for(tmp_path), transport)
    statuses = {o.app_id: o.status for o in result.manifest.outcomes}
    assert statuses["570"] is recorder.TargetStatus.UNAVAILABLE and len(asked) == 8
    assert not (result.directory / "current_players_570.json").exists()
    assert result.manifest.status is RunStatus.COMPLETE
    assert recorder.inspect_recording(result.directory).review_ready


# --- partial, failed and refused sessions -------------------------------------------------


def test_a_rate_limit_stops_the_session_and_leaves_a_partial_recording(tmp_path: Path) -> None:
    plan = plan_for(tmp_path)
    third = plan.requests[2].app_id
    transport, asked = steam({third: lambda r: httpx.Response(429)})
    result = run(plan, transport)
    manifest = result.manifest
    assert manifest.status is RunStatus.PARTIAL and manifest.requests_sent == 3 == len(asked)
    statuses = [o.status for o in manifest.outcomes]
    assert statuses[:2] == [recorder.TargetStatus.RECORDED] * 2
    assert statuses[2] is recorder.TargetStatus.FAILED
    assert statuses[3:] == [recorder.TargetStatus.NOT_ATTEMPTED] * 5
    assert manifest.stopped_because is not None and "rate limited" in manifest.stopped_because
    inspection = recorder.inspect_recording(result.directory)
    assert inspection.problems == () and not inspection.review_ready
    assert inspection.verdict.startswith("INTACT BUT PARTIAL")


@pytest.mark.parametrize(
    "answer",
    [
        lambda r: httpx.Response(401),
        lambda r: httpx.Response(403),
        lambda r: httpx.Response(200, json={"unexpected": True}),
        lambda r: httpx.Response(200, json={"response": {"player_count": -1, "result": 1}}),
    ],
)
def test_a_refused_key_or_an_invalid_answer_fails_the_session_and_keeps_nothing(
    tmp_path: Path, answer: Callable[[httpx.Request], httpx.Response]
) -> None:
    plan = plan_for(tmp_path)
    transport, asked = steam({plan.requests[0].app_id: answer})
    result = run(plan, transport)
    assert result.manifest.status is RunStatus.FAILED and len(asked) == 1
    assert result.manifest.requests_sent == 1 and result.records == ()
    assert list(result.directory.glob("*.json")) == []
    assert recorder.inspect_recording(result.directory).verdict.startswith("INTACT BUT FAILED")


def test_a_network_failure_counts_as_sent_and_stops(tmp_path: Path) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    plan = plan_for(tmp_path)
    result = run(plan, steam({plan.requests[0].app_id: broken})[0])
    assert result.manifest.status is RunStatus.FAILED and result.manifest.requests_sent == 1
    assert result.manifest.outcomes[0].error_code == "network_error"
    assert recorder.inspect_recording(result.directory).problems == ()


def test_a_response_that_echoes_the_key_is_never_kept(tmp_path: Path) -> None:
    plan = plan_for(tmp_path)
    leak = {plan.requests[0].app_id: lambda r: httpx.Response(200, text=f'{{"echo": "{KEY}"}}')}
    result = run(plan, steam(leak)[0])
    assert result.manifest.status is RunStatus.FAILED
    assert KEY.encode() not in every_byte(result.directory)


def test_an_uncleared_policy_refuses_before_anything_is_sent_or_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = plan_for(tmp_path)
    policy = STEAM_CURRENT_PLAYERS_POLICY
    refused = policy.model_copy(
        update={
            "stage_clearance": tuple(
                StageClearance(stage=c.stage, cleared=False, reason="withdrawn for the test")
                if c.stage is DeploymentStage.PRIVATE_DEVELOPMENT
                else c
                for c in policy.stage_clearance
            )
        }
    )
    monkeypatch.setattr(recorder, "STEAM_CURRENT_PLAYERS_POLICY", refused)
    transport, asked = steam()
    with pytest.raises(RecorderRefused):
        run(plan, transport)
    with pytest.raises(RecorderRefused):
        plan_for(tmp_path)
    assert asked == [] and list(tmp_path.iterdir()) == []


def test_a_plan_for_a_slot_that_has_passed_sends_nothing(tmp_path: Path) -> None:
    plan = plan_for(tmp_path)
    transport, asked = steam()
    with pytest.raises(RecorderRefused, match="not the current slot"):
        run(plan, transport, Ticking(plan.slot_end))
    assert asked == [] and list(tmp_path.iterdir()) == []


def test_a_session_that_reaches_the_end_of_its_slot_stops_there(tmp_path: Path) -> None:
    plan = plan_for(tmp_path)
    clock = Ticking(plan.slot_end - timedelta(seconds=12))  # a few readings of time left
    transport, asked = steam()
    result = run(plan, transport, clock)
    assert result.manifest.stopped_because is not None
    assert "ended" in result.manifest.stopped_because
    assert 0 < len(asked) < 8 and result.manifest.status is RunStatus.PARTIAL
    assert recorder.inspect_recording(result.directory).problems == ()


def test_an_edited_plan_is_refused(tmp_path: Path) -> None:
    plan = plan_for(tmp_path, ["730"])
    edited = plan.model_copy(update={"max_requests": 2})
    with pytest.raises(RecorderRefused, match="edited"):
        run(edited, steam()[0])


# --- inspection catches tampering ---------------------------------------------------------


def test_inspection_finds_every_kind_of_tampering(tmp_path: Path) -> None:
    result = run(plan_for(tmp_path), steam()[0])
    directory = result.directory
    target = directory / "current_players_730.json"
    document = json.loads(target.read_text())
    document["response"]["body"]["response"]["player_count"] = 1
    target.write_text(json.dumps(document, indent=1) + "\n")
    problems = recorder.inspect_recording(directory).problems
    assert any("not the body as received" in p for p in problems)
    assert any("digest" in p for p in problems)

    document["response"]["body_text"] = json.dumps(document["response"]["body"])
    target.write_text(json.dumps(document, indent=1) + "\n")
    assert any("730" in p for p in recorder.inspect_recording(directory).problems)

    (directory / "notes.txt").write_text("extra")
    assert any(
        "unexpected file notes.txt" in p for p in recorder.inspect_recording(directory).problems
    )


def test_a_record_cannot_be_rebound_to_another_game(tmp_path: Path) -> None:
    result = run(plan_for(tmp_path), steam()[0])
    path = result.directory / "acquisition" / "records.jsonl"
    rows = [AcquisitionRecord.model_validate_json(line) for line in path.read_text().splitlines()]
    binding = rows[0].bindings[0].model_copy(update={"canonical_game_id": "another-game"})
    rows[0] = rows[0].model_copy(update={"bindings": (binding,)})
    path.write_text("".join(r.model_dump_json() + "\n" for r in rows))
    problems = recorder.inspect_recording(result.directory).problems
    assert any("edited after it was made" in p for p in problems)
    assert any("binds another game" in p for p in problems)


# --- the command line ---------------------------------------------------------------------


def test_the_command_plans_by_default_and_sends_only_with_key_and_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport, asked = steam()
    monkeypatch.setattr(cli, "_live_transport", lambda: transport)
    monkeypatch.setattr(cli, "_now", Ticking(PLANNED))
    monkeypatch.setattr(cli, "_recorder_sleep", lambda s: None)
    monkeypatch.delenv(STEAM_WEB_API_KEY_ENV, raising=False)
    out = tmp_path / "rec"
    base = [
        "record-steam-players", "--all-eligible", "--out", str(out),
        "--state", str(tmp_path / "state.json"),
        "--recorded", str(FIXTURES / "catalogue"), str(FIXTURES / "bootstrap_catalogue"),
    ]  # fmt: skip

    assert cli.main([*base, "--json"]) == cli.EXIT_OK
    plan_id = json.loads(capsys.readouterr().out)["plan_id"]
    assert asked == [] and not out.exists()
    assert cli.main([*base, "--live"]) == cli.EXIT_SOURCE_POLICY
    assert cli.main([*base, "--live", "--confirm", "plan_" + "0" * 32]) == cli.EXIT_SOURCE_POLICY
    # The right plan, but no key in the environment: refused, and still nothing sent.
    assert cli.main([*base, "--live", "--confirm", plan_id]) == cli.EXIT_SOURCE_UNAVAILABLE
    assert asked == [] and not out.exists()

    monkeypatch.setenv(STEAM_WEB_API_KEY_ENV, KEY)
    capsys.readouterr()
    assert cli.main([*base, "--live", "--confirm", plan_id]) == cli.EXIT_OK
    printed = capsys.readouterr()
    assert len(asked) == 8 and KEY not in printed.out + printed.err
    assert "COMPLETE" in printed.out and "NOT pinned" in printed.out
    assert str(COUNTS["730"]) not in printed.out  # statuses, never values

    assert cli.main(["record-steam-players", "--inspect", str(out)]) == cli.EXIT_OK
    assert "ready for owner review (still UNVERIFIED)" in capsys.readouterr().out
