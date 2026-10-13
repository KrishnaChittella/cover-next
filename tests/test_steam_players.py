"""The Steam concurrent-player source, eligibility and the sampling commands, offline.

One response here is RECORDED: the single live call of 2026-10-06 for app 730. Every
other response is SYNTHETIC and fixes only how the adapter treats shapes that were
never observed; it is not evidence of what Steam returns.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from metaspike import cli
from metaspike.catalogue.bindings import TelemetryEligibility, audit_bootstrap_bindings
from metaspike.catalogue.eligibility import (
    SourceEligibility,
    bootstrap_scope,
    sampling_targets,
    steam_eligibility,
)
from metaspike.catalogue.evaluation import IncompleteRecords
from metaspike.catalogue.generate import CatalogueState, generate_catalogue
from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.samples import SampleFlag, SampleStatus, SlotState
from metaspike.domain.signals import SignalKey, SourceErrorCode
from metaspike.domain.source_policy import DeploymentStage
from metaspike.domain.source_records import ExternalId, SourceRecord
from metaspike.domain.timeseries import Granularity, TimeWindow
from metaspike.pipeline.history import read_entity_history
from metaspike.pipeline.sampling import run_sampling_slot
from metaspike.resolution.recorded import load_recorded_catalogue
from metaspike.sources.base import SourceError, SourceMode
from metaspike.sources.credentials import Secret
from metaspike.sources.http import LimitedHttpClient
from metaspike.sources.limiter import Clock, InMemoryQuotaLedger, LimiterPool, RateLimiter
from metaspike.sources.registry import SourcePolicyError
from metaspike.sources.sampling_sources import (
    build_steam_players,
    replay_sampling_client,
    sampling_registry,
)
from metaspike.sources.steam_catalogue import STEAM_CATALOGUE_POLICY
from metaspike.sources.steam_players import (
    CURRENT_PLAYERS_URL,
    FORBIDDEN_HOST,
    SAMPLING_RETRY,
    STEAM_CURRENT_PLAYERS_POLICY,
    SteamCurrentPlayersAdapter,
    interpret,
)
from metaspike.storage.samples import LocalSampleStore
from tests.conftest import mock_client
from tests.test_fixture_secrets import FIXTURES as SCANNED_FIXTURES
from tests.test_fixture_secrets import findings, text_files

FIXTURES = Path(__file__).parent / "fixtures"
RECORDED = FIXTURES / "steam_players"
RECORDED_730 = RECORDED / "current_players_730.json"
KEY = "0123456789ABCDEF0123456789ABCDEF"
NOW = datetime(2026, 10, 6, 12, 0, 30, tzinfo=UTC)
AS_OF = datetime(2026, 10, 5, tzinfo=UTC)
SIGNAL = SignalKey(
    source="steam_current_players",
    metric="concurrent_players",
    granularity=Granularity.FIFTEEN_MINUTES,
)
EIGHT_APPS = ["570", "730", "252490", "275850", "553850", "1086940", "1245620", "3932890"]


def count(players: Any, **envelope: Any) -> httpx.Response:
    return httpx.Response(200, json={"response": {"player_count": players, "result": 1} | envelope})


def adapter_for(
    *responses: httpx.Response | Exception, ledger: InMemoryQuotaLedger | None = None
) -> tuple[SteamCurrentPlayersAdapter, list[httpx.Request], list[float]]:
    queue = list(responses)
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    limiter = RateLimiter(
        STEAM_CURRENT_PLAYERS_POLICY.operations,
        ledger=ledger or InMemoryQuotaLedger(),
        clock=Clock(sleeps.append),
        wall_clock=lambda: NOW,
    )
    http = LimitedHttpClient(mock_client(handler), limiter, SAMPLING_RETRY)
    return SteamCurrentPlayersAdapter(http, Secret(KEY), now=lambda: NOW), requests, sleeps


# --- the request -------------------------------------------------------------------------


def test_the_documented_method_is_called_on_the_public_host_with_the_key_in_a_header() -> None:
    adapter, requests, _ = adapter_for(count(1234))
    reading = adapter.sample("730")
    (request,) = requests
    assert request.method == "GET"
    assert str(request.url) == f"{CURRENT_PLAYERS_URL}?appid=730"
    assert request.url.host == "api.steampowered.com" and request.url.scheme == "https"
    assert FORBIDDEN_HOST not in str(request.url)
    assert request.headers["x-webapi-key"] == KEY
    assert KEY not in str(request.url) and "key=" not in str(request.url)
    assert (reading.status, reading.value, reading.observed_at) == (
        SampleStatus.OBSERVED,
        1234,
        NOW,
    )
    assert adapter.mode is SourceMode.SAMPLE_NOW and not hasattr(adapter, "fetch")
    assert "sampled" in adapter.description


@pytest.mark.parametrize("native_id", ["", "abc", "0730", "73 0", "-5", "7.3", "730/../x"])
def test_only_a_plain_app_id_is_ever_sent(native_id: str) -> None:
    adapter, requests, _ = adapter_for(count(1))
    with pytest.raises(ValueError, match="not a Steam app id"):
        adapter.sample(native_id)
    assert requests == []


# --- the recorded response ---------------------------------------------------------------


def recorded() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(RECORDED_730.read_text(encoding="utf-8"))
    return loaded


def test_the_recorded_response_is_what_the_adapter_treats_as_success() -> None:
    fixture = recorded()
    assert fixture["request"] == {
        "method": "GET",
        "host": "api.steampowered.com",
        "path": "/ISteamUserStats/GetNumberOfCurrentPlayers/v1/",
        "query": {"appid": 730},
    }
    response = fixture["response"]
    assert response["status"] == 200
    assert set(response["body"]) == {"response"}
    assert set(response["body"]["response"]) == {"player_count", "result"}
    assert response["body"]["response"]["result"] == 1
    reading = interpret(200, response["body"], httpx.Headers(response["headers"]), NOW)
    assert reading.status is SampleStatus.OBSERVED
    assert reading.value == response["body"]["response"]["player_count"] > 0
    # The response was publicly cacheable for minutes and carried no Age header.
    assert response["headers"]["cache-control"] == "public, max-age=436"
    assert "age" not in response["headers"] and "age" not in response["other_header_names"]
    assert reading.cache_ttl_seconds == 436 and reading.cache_age_seconds is None


def test_the_recorded_fixture_holds_no_credential_and_is_scanned() -> None:
    assert RECORDED_730 in text_files(SCANNED_FIXTURES)  # the secret scanner reads it
    assert findings(RECORDED_730) == []
    text = RECORDED_730.read_text(encoding="utf-8").lower()
    for forbidden in ("x-webapi-key", "key=", "authorization", "set-cookie", "cookie"):
        assert forbidden not in text
    assert sorted(recorded()["response"]["headers"]) == [
        "cache-control",
        "content-type",
        "date",
        "expires",
        "vary",
    ]


# --- interpretation: rules for what was never observed -----------------------------------


def test_a_reported_zero_is_a_value_and_everything_else_is_not() -> None:
    adapter, _, _ = adapter_for(count(0))
    zero = adapter.sample("730")
    assert zero.status is SampleStatus.OBSERVED and zero.value == 0

    not_a_count: tuple[Any, ...] = (
        {"response": {"player_count": 0, "result": 42}},  # a zero that is not a success
        {"response": {"player_count": "12", "result": 1}},
        {"response": {"player_count": -1, "result": 1}},
        {"response": {"player_count": True, "result": 1}},
        {"response": {"player_count": 1.5, "result": 1}},
        {"response": {"player_count": None, "result": 1}},
        {"response": []},
        {"player_count": 5},
        [],
        None,
    )
    for body in not_a_count:
        reading = interpret(200, body, httpx.Headers(), NOW)
        assert reading.status is SampleStatus.FAILED and reading.value is None, body
        assert reading.error_code is SourceErrorCode.INVALID_RESPONSE


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (404, {"response": {"result": 42}}),
        (200, {"response": {"result": 42}}),
        (200, {"response": {}}),
    ],
)
def test_an_answer_with_no_count_is_unavailable_never_zero(status: int, body: Any) -> None:
    reading = interpret(status, body, httpx.Headers(), NOW)
    assert reading.status is SampleStatus.UNAVAILABLE and reading.value is None
    assert reading.error_code is SourceErrorCode.NOT_FOUND and not reading.halt_run


@pytest.mark.parametrize(
    ("answer", "code", "halts"),
    [
        (httpx.Response(401), SourceErrorCode.UNAUTHORIZED, True),
        (httpx.Response(403), SourceErrorCode.UNAUTHORIZED, True),
        (httpx.Response(429), SourceErrorCode.RATE_LIMITED, True),
        (httpx.Response(400), SourceErrorCode.HTTP_ERROR, False),
        (httpx.Response(500), SourceErrorCode.HTTP_ERROR, False),
        (httpx.Response(503), SourceErrorCode.HTTP_ERROR, False),
        (httpx.Response(200, text="<html>busy</html>"), SourceErrorCode.INVALID_RESPONSE, False),
        (httpx.ReadTimeout("slow"), SourceErrorCode.TIMEOUT, False),
        (httpx.ConnectError("down"), SourceErrorCode.NETWORK_ERROR, False),
    ],
)
def test_every_failure_is_a_failed_reading_with_a_reason_and_no_value(
    answer: httpx.Response | Exception, code: SourceErrorCode, halts: bool
) -> None:
    adapter, requests, sleeps = adapter_for(answer)
    reading = adapter.sample("730")
    assert reading.status is SampleStatus.FAILED and reading.value is None
    assert reading.error_code is code and reading.halt_run is halts
    assert len(requests) == 1  # one transmission per attempt: no hidden retries
    assert sleeps == []


def test_cache_headers_are_carried_on_the_reading() -> None:
    headers = {"Age": "37", "Cache-Control": "public, max-age=120"}
    adapter, _, _ = adapter_for(
        httpx.Response(200, json={"response": {"player_count": 9, "result": 1}}, headers=headers)
    )
    reading = adapter.sample("730")
    assert (reading.cache_age_seconds, reading.cache_ttl_seconds) == (37, 120)


# --- limiter and quota -------------------------------------------------------------------


def test_player_counts_share_the_one_steam_budget() -> None:
    pool = LimiterPool()
    assert pool.for_policy(STEAM_CURRENT_PLAYERS_POLICY) is pool.for_policy(STEAM_CATALOGUE_POLICY)
    ledger = InMemoryQuotaLedger()
    adapter, _, sleeps = adapter_for(count(5), ledger=ledger)
    for app in ("570", "730", "252490"):
        adapter.sample(app)
    assert ledger.used("steam_web_api", NOW.date().isoformat()) == 3
    assert len(sleeps) == 2 and all(0.99 < pause <= 1.0 for pause in sleeps)  # 1 per second


def test_an_exhausted_quota_refuses_without_sending(tmp_path: Path) -> None:
    ledger = InMemoryQuotaLedger()
    assert ledger.try_consume("steam_web_api", NOW.date().isoformat(), 90_000, 90_000)
    adapter, requests, _ = adapter_for(count(5), ledger=ledger)
    reading = adapter.sample("730")
    assert reading.status is SampleStatus.REFUSED and reading.value is None
    assert reading.error_code is SourceErrorCode.QUOTA_EXHAUSTED and reading.halt_run
    assert requests == []  # nothing left the machine

    store = LocalSampleStore(tmp_path, lambda _: STEAM_CURRENT_PLAYERS_POLICY)
    state, records = catalogue()
    targets = sampling_targets(state, steam_eligibility(state, records), bootstrap_scope())
    report = run_sampling_slot(targets, adapter, store, now=lambda: NOW)
    assert report.requests_sent == 0 and report.halted_by is SourceErrorCode.QUOTA_EXHAUSTED
    assert report.count(SampleStatus.REFUSED) == 8 and requests == []
    slot = store.resolve(SIGNAL, "730", Granularity.FIFTEEN_MINUTES.floor(NOW), NOW)
    assert slot.state is SlotState.REFUSED and slot.value is None


# --- credentials -------------------------------------------------------------------------


def test_the_key_appears_in_nothing_that_is_stored_logged_or_raised(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    adapter, _, _ = adapter_for(count(321), httpx.Response(500), httpx.ReadTimeout("slow"))
    store = LocalSampleStore(tmp_path, lambda _: STEAM_CURRENT_PLAYERS_POLICY)
    state, records = catalogue()
    targets = sampling_targets(state, steam_eligibility(state, records), bootstrap_scope())[:3]
    report = run_sampling_slot(targets, adapter, store, now=lambda: NOW)
    stored = "".join(p.read_text() for p in tmp_path.rglob("*.jsonl"))
    assert stored.count("\n") == 3
    for text in (stored, report.model_dump_json(), caplog.text, repr(adapter.__dict__)):
        assert KEY not in text

    failing, _, _ = adapter_for(httpx.ConnectError(f"cannot reach host with {KEY}"))
    with pytest.raises(SourceError) as raised:
        failing.probe(730)
    assert KEY not in str(raised.value) and KEY not in raised.value.message


def test_a_probe_reports_only_safe_headers_and_withholds_an_echoed_key() -> None:
    headers = {"Cache-Control": "public, max-age=60", "Set-Cookie": "session=abc", "Server": "x"}
    adapter, _, _ = adapter_for(
        httpx.Response(200, json={"response": {"player_count": 7, "result": 1}}, headers=headers)
    )
    probe = adapter.probe(730)
    assert probe.http_status == 200 and probe.reading.value == 7
    assert probe.headers == {
        "cache-control": "public, max-age=60",
        "content-type": "application/json",
    }
    assert "set-cookie" in probe.other_header_names  # the name is reported, never the value
    assert "session=abc" not in repr(probe)

    echoing, _, _ = adapter_for(httpx.Response(403, json={"error": f"bad key {KEY}"}))
    echoed = echoing.probe(730)
    assert KEY not in repr(echoed) and echoed.headers == {}
    assert echoed.reading.status is SampleStatus.FAILED


# --- eligibility -------------------------------------------------------------------------

_CATALOGUE: list[tuple[CatalogueState, list[SourceRecord]]] = []


def catalogue() -> tuple[CatalogueState, list[SourceRecord]]:
    if not _CATALOGUE:
        records = [
            record
            for directory in ("catalogue", "bootstrap_catalogue")
            for record in load_recorded_catalogue(FIXTURES / directory).values()
        ]
        _CATALOGUE.append((generate_catalogue(records, as_of=AS_OF), records))
    return _CATALOGUE[0]


def test_the_bootstrap_scope_yields_the_eight_trusted_apps() -> None:
    state, records = catalogue()
    eligibility = steam_eligibility(state, records)
    targets = sampling_targets(state, eligibility, bootstrap_scope())
    assert [t.source_native_id for t in targets] == sorted(EIGHT_APPS, key=int)
    by_slug = {e.slug: e for e in eligibility}
    assert {t.canonical_game_id for t in targets} == {
        by_slug[slug].canonical_game_id
        for slug in (
            "dota-2",
            "counter-strike-2",
            "rust",
            "no-mans-sky",
            "helldivers-2",
            "baldurs-gate-3",
            "elden-ring",
            "escape-from-tarkov",
        )
    }
    for chosen in targets:  # each target carries the binding that justifies it
        key = SourceRecordKey(source="steam_catalogue", source_id=chosen.source_native_id)
        assert state.mapping.canonical_for(key) == chosen.canonical_game_id
        assert chosen.mapping_version is not None and chosen.binding_ref


def test_gta_v_and_minecraft_stay_unknown_and_inapplicable_games_are_ineligible() -> None:
    state, records = catalogue()
    by_slug = {e.slug: e for e in steam_eligibility(state, records)}
    gta_v = by_slug["gta-v"]
    assert gta_v.state is SourceEligibility.UNKNOWN and gta_v.app_ids == ()
    assert gta_v.asserted_unconfirmed == ("271590",) and gta_v.quarantined == ("3240220",)
    assert by_slug["minecraft"].state is SourceEligibility.UNKNOWN
    assert by_slug["minecraft"].app_ids == ()
    for slug in ("star-citizen", "fortnite", "fivem", "gta-vi", "gta-online"):
        assert by_slug[slug].state is SourceEligibility.INELIGIBLE, slug
    sampled = {t.source_native_id for t in sampling_targets(state, tuple(by_slug.values()))}
    assert not sampled & {"271590", "3240220"}  # never GTA V, never 'Enhanced'
    enhanced = SourceRecordKey(source="steam_catalogue", source_id="3240220")
    assert state.mapping.canonical_for(enhanced) is None


def test_generic_eligibility_agrees_with_the_phase_two_audit_and_is_not_a_fixed_list() -> None:
    state, records = catalogue()
    eligibility = {e.canonical_game_id: e for e in steam_eligibility(state, records)}
    translate = {
        TelemetryEligibility.YES: SourceEligibility.ELIGIBLE,
        TelemetryEligibility.NO: SourceEligibility.INELIGIBLE,
        TelemetryEligibility.UNKNOWN: SourceEligibility.UNKNOWN,
    }
    for row in audit_bootstrap_bindings(state, records):
        entity = eligibility[row.canonical_game_id]
        assert entity.state is translate[row.steam_telemetry], row.slug
        if entity.state is SourceEligibility.ELIGIBLE:
            assert entity.app_ids == row.steam_app_ids
    # The rule covers every entity; the scope, not the rule, narrows a run to eight apps.
    assert len(eligibility) == len(state.catalogue.games) == 234
    everything = sampling_targets(state, tuple(eligibility.values()))
    assert len(everything) > 8
    assert set(EIGHT_APPS) < {t.source_native_id for t in everything}


def test_no_target_is_ever_derived_from_a_title() -> None:
    state, records = catalogue()
    by_key = {record.key: record for record in records}
    structured = {
        (state.mapping.canonical_for(record.key), link.value)
        for record in records
        if record.key.source == "igdb"
        for link in record.external_ids
        if link.source == "steam"
    }
    for chosen in sampling_targets(state, steam_eligibility(state, records)):
        assert (chosen.canonical_game_id, chosen.source_native_id) in structured
    # Take the structured ids away and nothing is eligible, however well titles match.
    stripped = [r.model_copy(update={"external_ids": ()}) for r in records]
    assert by_key  # the same records, same titles, same bindings
    without = steam_eligibility(state, stripped)
    assert not any(e.state is SourceEligibility.ELIGIBLE for e in without)
    assert sampling_targets(state, without) == ()


def test_eligibility_is_point_in_time() -> None:
    state, records = catalogue()
    before_any_binding = AS_OF - timedelta(days=1)
    earlier = {e.slug: e for e in steam_eligibility(state, records, as_of=before_any_binding)}
    assert earlier["dota-2"].state is SourceEligibility.UNKNOWN  # nothing was bound yet
    assert sampling_targets(state, tuple(earlier.values()), as_of=before_any_binding) == ()

    # A later rebinding of app 570 away from Dota 2 changes eligibility from then on only.
    dota, rust = (
        next(g.canonical_game_id for g in state.catalogue.games if g.slug == slug)
        for slug in ("dota-2", "rust")
    )
    moved_at = AS_OF + timedelta(days=10)
    app = SourceRecordKey(source="steam_catalogue", source_id="570")
    moved = state.model_copy(update={"mapping": state.mapping.bind(app, rust, moved_at, "test")})
    after = {e.slug: e for e in steam_eligibility(moved, records)}
    assert after["dota-2"].state is SourceEligibility.UNKNOWN and after["dota-2"].app_ids == ()
    assert "570" not in after["rust"].app_ids  # bound, but no structured id backs it
    then = {
        e.slug: e for e in steam_eligibility(moved, records, as_of=moved_at - timedelta(days=1))
    }
    assert then["dota-2"].state is SourceEligibility.ELIGIBLE and then["dota-2"].app_ids == ("570",)
    assert dota != rust


def test_an_entity_with_two_eligible_apps_is_sampled_twice_and_combined_never(
    tmp_path: Path,
) -> None:
    state, records = catalogue()
    elden = next(g.canonical_game_id for g in state.catalogue.games if g.slug == "elden-ring")
    second = SourceRecordKey(source="steam_catalogue", source_id="999999")
    two_apps = state.model_copy(
        update={"mapping": state.mapping.bind(second, elden, AS_OF + timedelta(hours=1), "test")}
    )
    listing = [
        r.model_copy(
            update={"external_ids": (*r.external_ids, ExternalId(source="steam", value="999999"))}
        )
        if r.key == SourceRecordKey(source="igdb", source_id="119133")
        else r
        for r in records
    ]
    entity = next(e for e in steam_eligibility(two_apps, listing) if e.canonical_game_id == elden)
    assert entity.state is SourceEligibility.ELIGIBLE and entity.app_ids == ("999999", "1245620")
    targets = [t for t in sampling_targets(two_apps, (entity,)) if t.canonical_game_id == elden]
    assert [t.source_native_id for t in targets] == ["999999", "1245620"]  # each on its own

    adapter, _, _ = adapter_for(count(10), count(2000))
    store = LocalSampleStore(tmp_path, lambda _: STEAM_CURRENT_PLAYERS_POLICY)
    run_sampling_slot(tuple(targets), adapter, store, now=lambda: NOW)
    slot = Granularity.FIFTEEN_MINUTES.floor(NOW)
    history = read_entity_history(
        store,
        two_apps.mapping,
        elden,
        SIGNAL,
        "steam_catalogue",
        TimeWindow(start=slot, end=slot),
        NOW + timedelta(minutes=1),
    )
    assert history.combined is None
    assert history.unavailable_reason == "multi_app_combination_unresolved"
    assert {h.source_native_id: h.slots[0].value for h in history.by_native_id} == {
        "999999": 10,
        "1245620": 2000,
    }


# --- wiring ------------------------------------------------------------------------------


def test_the_sampling_registry_clears_private_development_only() -> None:
    adapter = build_steam_players(
        mock_client(lambda r: count(1)), LimiterPool(), api_key=Secret(KEY)
    )
    registry = sampling_registry(adapter)
    assert registry.stage is DeploymentStage.PRIVATE_DEVELOPMENT
    assert [s.policy.source for s in registry.samplers()] == ["steam_current_players"]
    assert registry.publication_refusals() != {}
    for stage in (
        DeploymentStage.PRIVATE_COLLECTION,
        DeploymentStage.PUBLIC_LAUNCH,
        DeploymentStage.COMMERCIAL_PRODUCTION,
    ):
        with pytest.raises(SourcePolicyError):
            sampling_registry(adapter, stage)
    with pytest.raises(SourceError, match="METASPIKE_STEAM_WEB_API_KEY"):
        build_steam_players(mock_client(lambda r: count(1)), LimiterPool(), environ={})


def test_replay_answers_only_what_was_recorded() -> None:
    with replay_sampling_client(RECORDED) as client:
        pool = LimiterPool(clock=Clock(lambda seconds: None))
        adapter = build_steam_players(client, pool, api_key=Secret("replay-no-key"))
        recorded_count = recorded()["response"]["body"]["response"]["player_count"]
        assert adapter.sample("730").value == recorded_count
        unrecorded = adapter.sample("570")
        assert unrecorded.status is SampleStatus.FAILED and unrecorded.value is None


# --- the commands ------------------------------------------------------------------------


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """No waiting and no network: the commands run against recordings only."""
    monkeypatch.setattr(cli, "LimiterPool", lambda: LimiterPool(clock=Clock(lambda s: None)))

    def no_network() -> httpx.Client:
        raise AssertionError("a test tried to reach the network")

    monkeypatch.setattr(cli, "build_client", no_network)
    monkeypatch.delenv("METASPIKE_STEAM_WEB_API_KEY", raising=False)


def sample_arguments(tmp_path: Path) -> list[str]:
    return [
        "sample",
        "--recorded",
        str(FIXTURES / "catalogue"),
        str(FIXTURES / "bootstrap_catalogue"),
        "--state",
        str(tmp_path / "state.json"),
        "--store-dir",
        str(tmp_path / "samples"),
        "--replay",
        str(RECORDED),
    ]


def test_sample_runs_one_slot_and_is_safe_to_rerun(
    tmp_path: Path, offline: None, capsys: pytest.CaptureFixture[str]
) -> None:
    recorded_count = str(recorded()["response"]["body"]["response"]["player_count"])
    assert cli.main(sample_arguments(tmp_path)) == cli.EXIT_OK
    first = capsys.readouterr().out
    assert "Targets 8: 1 observed, 0 unavailable, 7 failed, 0 refused, 0 skipped" in first
    assert "8 requests sent" in first
    assert "Not sampled (unknown): gta-v, minecraft" in first
    assert "Not sampled (ineligible): fivem, fortnite, gta-online, gta-vi, star-citizen" in first
    assert recorded_count not in first  # values go to the store, not the screen

    store = LocalSampleStore(tmp_path / "samples", lambda _: STEAM_CURRENT_PLAYERS_POLICY)
    assert store.native_ids(SIGNAL) == tuple(sorted(EIGHT_APPS, key=int))
    now = datetime.now(UTC)
    slot = store.resolve(SIGNAL, "730", Granularity.FIFTEEN_MINUTES.floor(now), now)
    if slot.state is SlotState.OBSERVED:  # unless the test straddled a slot boundary
        assert str(slot.value) == recorded_count
        assert (
            slot.selected is not None
            and SampleFlag.ZERO_REPORTED not in slot.selected.quality_flags
        )

        assert cli.main(sample_arguments(tmp_path)) == cli.EXIT_OK
        second = capsys.readouterr().out
        assert "app 730       already_observed" in second
        assert "7 requests sent" in second  # only the failed apps were retried
    # Nothing was scheduled: the command ran once and left no job behind.
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "samples",
        "state.json",
        "state.provenance.json",  # says the state is local, and under which policy
    ]


def test_sample_keeps_the_catalogue_state_so_bindings_keep_their_dates(
    tmp_path: Path, offline: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(sample_arguments(tmp_path)) == cli.EXIT_OK
    saved = (tmp_path / "state.json").read_bytes()
    assert cli.main(sample_arguments(tmp_path)) == cli.EXIT_OK
    assert (tmp_path / "state.json").read_bytes() == saved
    capsys.readouterr()


def test_sample_status_reports_health_and_never_a_value_or_a_secret(
    tmp_path: Path,
    offline: None,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert cli.main(sample_arguments(tmp_path)) == cli.EXIT_OK
    capsys.readouterr()
    monkeypatch.setenv("METASPIKE_STEAM_WEB_API_KEY", KEY)
    arguments = [
        "sample-status",
        "--store-dir",
        str(tmp_path / "samples"),
        "--quota-dir",
        str(tmp_path / "quota"),
    ]
    assert cli.main([*arguments, "--json"]) == cli.EXIT_OK
    printed = capsys.readouterr().out
    status = json.loads(printed)
    recorded_count = str(recorded()["response"]["body"]["response"]["player_count"])
    assert KEY not in printed and recorded_count not in printed
    assert status["api_key_configured"] is True  # a yes or no, never the key
    assert status["mode"] == "sample_now" and status["scheduler_installed"] is False
    assert status["stage_clearance"] == {
        "private_development": True,
        "private_collection": False,
        "public_launch": False,
        "commercial_production": False,
    }
    assert status["derived_metrics_allowed"] == "unknown" and status["publishable"] is False
    assert status["quota_per_day"] == 100_000 and status["quota_remaining_today"] == 90_000
    assert [row["source_native_id"] for row in status["targets"]] == sorted(EIGHT_APPS, key=int)
    assert all("value" not in row for row in status["targets"])
    assert all(row["slots"] == 96 for row in status["targets"])

    assert cli.main(arguments) == cli.EXIT_OK
    table = capsys.readouterr().out
    assert "API key configured: yes" in table and "scheduler installed: no" in table
    assert KEY not in table and recorded_count not in table

    assert cli.main(["sample-status", "--store-dir", str(tmp_path / "empty")]) == cli.EXIT_OK
    assert "No samples stored." in capsys.readouterr().out


def test_the_player_probe_is_never_part_of_all_and_refuses_an_ineligible_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return count(5)

    monkeypatch.setattr(cli, "build_client", lambda: mock_client(handler))
    monkeypatch.setattr(
        cli, "default_limiter_pool", lambda: LimiterPool(clock=Clock(lambda s: None))
    )
    monkeypatch.setenv("METASPIKE_STEAM_WEB_API_KEY", KEY)
    recorded_dirs = [str(FIXTURES / "catalogue"), str(FIXTURES / "bootstrap_catalogue")]
    base = ["probe-sources", "--source", "steam-players", "--recorded", *recorded_dirs]

    for app in ("271590", "3240220", "12345"):  # GTA V, 'Enhanced', an unknown app
        assert cli.main([*base, "--app", app]) == cli.EXIT_SOURCE_POLICY
    assert requests == []
    capsys.readouterr()

    target = tmp_path / "probe.json"
    assert cli.main([*base, "--app", "570", "--record", str(target)]) == cli.EXIT_OK
    printed = capsys.readouterr().out
    assert len(requests) == 1 and requests[0].url.host == "api.steampowered.com"
    assert KEY not in printed and KEY not in target.read_text()
    assert findings(target) == []
    assert "HTTP status 200" in printed and "interpreted as observed" in printed


# --- reusing a catalogue state needs the evidence it was made from ----------------------


def recorded_subset(root: Path, *, igdb: bool, steam: bool) -> list[str]:
    """The two recorded fixture directories with one source's rows left out.

    Recorded rows, untouched; only which of them are supplied changes.
    """
    directories = []
    for name in ("catalogue", "bootstrap_catalogue"):
        target = root / name
        target.mkdir(parents=True)
        games = json.loads((FIXTURES / name / "igdb_games.json").read_text())
        apps = json.loads((FIXTURES / name / "steam_apps.json").read_text())
        (target / "igdb_games.json").write_text(
            json.dumps(games if igdb else {**games, "rows": []})
        )
        (target / "steam_apps.json").write_text(json.dumps(apps if steam else {**apps, "apps": []}))
        directories.append(str(target))
    return directories


def with_recorded(arguments: list[str], directories: list[str]) -> list[str]:
    start, end = arguments.index("--recorded") + 1, arguments.index("--state")
    return [*arguments[:start], *directories, *arguments[end:]]


def tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_the_sampler_reuses_a_state_only_with_the_evidence_it_was_made_from(
    tmp_path: Path,
    offline: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The gap: `generate-catalogue` required every earlier record when continuing a
    state, and the sampler did not. It would read bindings made from the complete
    records beside a smaller set, and choose its Steam targets from the mismatch."""
    work = tmp_path / "work"
    work.mkdir()
    arguments = sample_arguments(work)
    opened: list[Path] = []
    real_client = replay_sampling_client

    def counting_client(recordings: Path) -> httpx.Client:
        opened.append(recordings)
        return real_client(recordings)

    monkeypatch.setattr(cli, "replay_sampling_client", counting_client)

    # A. A state generated from the complete recorded IGDB and Steam catalogue fixtures,
    # at private development, by the first sampling run.
    assert cli.main(arguments) == cli.EXIT_OK
    first = capsys.readouterr().out
    assert "Targets 8:" in first and "8 requests sent" in first
    assert len(opened) == 1
    state_file, provenance_file = work / "state.json", work / "state.provenance.json"
    provenance = json.loads(provenance_file.read_text())
    assert provenance["stage"] == "private_development"
    assert provenance["retained_sources"] == ["igdb", "steam_catalogue"]
    state = CatalogueState.model_validate_json(state_file.read_bytes())
    made_from = len({d.record for d in state.decisions.decisions})

    # B, G. Reused with the complete records, across several slots: accepted, and the
    # state and its provenance are never rewritten.
    kept = (state_file.read_bytes(), provenance_file.read_bytes())
    for slot in ("2026-10-07T10:15:00+00:00", "2026-10-07T10:30:00+00:00"):
        assert cli.main([*arguments, "--scheduled-for", slot]) == cli.EXIT_OK
        assert "Targets 8:" in capsys.readouterr().out
        assert (state_file.read_bytes(), provenance_file.read_bytes()) == kept
    assert len(opened) == 3
    # I. The same eligibility as before: the targets are the same eight apps.
    records = [
        record
        for name in ("catalogue", "bootstrap_catalogue")
        for record in load_recorded_catalogue(FIXTURES / name).values()
    ]
    targets = sampling_targets(state, steam_eligibility(state, records), bootstrap_scope())
    assert len(targets) == 8

    before = tree(work)
    refusals = {
        # C. Only the IGDB records: the Steam records the state was made from are missing.
        "igdb-only": (
            recorded_subset(tmp_path / "igdb-only", igdb=True, steam=False),
            "steam_catalogue",
        ),
        # D. Only the Steam catalogue records: the IGDB records are missing.
        "steam-only": (recorded_subset(tmp_path / "steam-only", igdb=False, steam=True), "igdb"),
        # E. Nothing at all, and one of the two directories alone.
        "empty": (recorded_subset(tmp_path / "empty", igdb=False, steam=False), "igdb"),
        "one-directory": ([str(FIXTURES / "bootstrap_catalogue")], "igdb"),
    }
    for name, (directories, missing_source) in refusals.items():
        code = cli.main(with_recorded(arguments, directories))
        captured = capsys.readouterr()
        # F, G. An expected refusal with a clear message, not an internal error.
        assert code == cli.EXIT_INVALID_CATALOGUE, name
        assert code != cli.EXIT_UNEXPECTED
        assert "catalogue state cannot be reused" in captured.err, name
        assert "record(s) the state being continued was made from" in captured.err, name
        assert f"from {missing_source}" in captured.err, name
        assert "Traceback" not in captured.err and "unexpected failure" not in captured.err
        assert captured.out == ""  # no target was chosen and nothing was reported
        # No client was opened, so no request could be sent.
        assert len(opened) == 3, name
        # Nothing was written: no sample, and the state and provenance are byte-identical.
        assert tree(work) == before, name
    # The count is exact when everything is missing.
    assert cli.main(with_recorded(arguments, refusals["empty"][0])) == cli.EXIT_INVALID_CATALOGUE
    assert f"{made_from} record(s)" in capsys.readouterr().err
    # The refusal happens in the loader, before eligibility is computed at all.
    with pytest.raises(IncompleteRecords):
        cli._load_or_generate_state(state_file, [], DeploymentStage.PRIVATE_DEVELOPMENT)
    source = (FIXTURES.parent.parent / "src/metaspike/cli.py").read_text()
    loader = source[
        source.index("def _load_or_generate_state(") : source.index("def _probe_steam_players(")
    ]
    assert "require_complete(records, known_record_keys(state))" in loader
    handler = source[source.index("def _sample(") : source.index("def _print_sampling(")]
    assert handler.index("_load_or_generate_state(") < handler.index("steam_eligibility(")
    assert handler.index("_load_or_generate_state(") < handler.index("replay_sampling_client(")

    # Policy is not bypassed by any of this: the same state at a stage Steam's catalogue
    # does not clear is refused for that reason, with complete records or without.
    for supplied in (records, []):
        with pytest.raises(SourcePolicyError, match="not cleared for private_collection"):
            cli._load_or_generate_state(state_file, supplied, DeploymentStage.PRIVATE_COLLECTION)

    # H. Missing or tampered provenance is still refused, and still touches nothing.
    provenance_bytes = provenance_file.read_bytes()
    provenance_file.unlink()
    assert cli.main(arguments) == cli.EXIT_SOURCE_POLICY
    assert "has no provenance file" in capsys.readouterr().err
    provenance_file.write_text(json.dumps({**provenance, "retained_sources": ["igdb"]}))
    assert cli.main(arguments) == cli.EXIT_SOURCE_POLICY
    assert "but the state retains" in capsys.readouterr().err
    provenance_file.write_text(json.dumps({**provenance, "state_sha256": "0" * 64}))
    assert cli.main(arguments) == cli.EXIT_SOURCE_POLICY
    assert "digest differs" in capsys.readouterr().err
    assert len(opened) == 3 and state_file.read_bytes() == kept[0]
    # Restored, the complete evidence is accepted again.
    provenance_file.write_bytes(provenance_bytes)
    assert tree(work) == before
    assert cli.main([*arguments, "--scheduled-for", "2026-10-07T10:45:00+00:00"]) == cli.EXIT_OK
    assert "Targets 8:" in capsys.readouterr().out
    assert len(opened) == 4
