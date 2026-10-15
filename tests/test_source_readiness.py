"""Source readiness, acquisition records and the offline real-source rehearsal (U6.6).

Offline throughout. Nothing here contacts a source, reads a credential or touches the
cloud. The only real responses used are the committed recordings.
"""

import json
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from metaspike import cli
from metaspike.community_sources.reddit import REDDIT_DATA_POLICY
from metaspike.creator_sources.youtube import YOUTUBE_DATA_POLICY
from metaspike.domain.source_policy import DeploymentStage, Permission, SourcePurpose
from metaspike.evidence.acquisition import (
    TRUSTED_ACQUISITION_STEP,
    AcquisitionRecord,
    AcquisitionRefused,
    ContentMismatch,
    NativeBinding,
    acquisition_standing,
    check_content,
    policy_changed,
    record_acquisition,
    usable_at,
)
from metaspike.evidence.assemble import EvidenceInputs
from metaspike.evidence.model import Standing
from metaspike.source_readiness import (
    SPECS,
    STAGE_ORDER,
    EvidenceStanding,
    ReadinessReport,
    assess_readiness,
    assess_source,
)
from metaspike.sources.limiter import Clock, LimiterPool
from metaspike.sources.steam_players import STEAM_CURRENT_PLAYERS_POLICY

REPO = Path(__file__).parent.parent
FIXTURES = REPO / "tests" / "fixtures"
STEAM_RECORDING = FIXTURES / "steam_players" / "current_players_730.json"
# The Steam response was recorded at this moment; nothing may claim it arrived earlier.
STEAM_RECORDED_AT = datetime(2026, 10, 6, 1, 25, 26, tzinfo=UTC)
UI_SCHEMA = REPO / "apps/dev-ui/src/private/opportunities/report.schema.json"


@pytest.fixture(scope="module")
def readiness() -> ReadinessReport:
    return assess_readiness(REPO)


def by_family(report: ReadinessReport, family: str):  # type: ignore[no-untyped-def]
    return next(s for s in report.sources if s.family == family)


# --- readiness comes from the policies and the code, not from opinion -----------------------


def test_every_source_family_is_covered_once(readiness: ReadinessReport) -> None:
    assert [s.family for s in readiness.sources] == [
        "wikimedia_pageviews",
        "igdb_catalogue",
        "steam_app_catalogue",
        "steam_concurrent_players",
        "steam_news",
        "youtube_creator_supply",
        "reddit_community_interest",
    ]


def test_stage_results_are_the_policy_s_own_refusals(readiness: ReadinessReport) -> None:
    for spec, source in zip(SPECS, readiness.sources, strict=True):
        assert [st.stage for st in source.stages] == list(STAGE_ORDER)
        for status in source.stages:
            expected = spec.policy.registration_refusal(spec.purpose, status.stage)
            assert status.refusal == expected
            assert status.permitted is (expected is None)
            assert status.declared_cleared is spec.policy.cleared_for(status.stage)
        row = {p.name: p.value for p in source.permissions}
        assert row["derived_metrics"] == spec.policy.derived_metrics_allowed.value
        assert row["historical_storage"] == spec.policy.historical_storage_allowed.value


def test_permission_pending_blocks_every_stage_and_is_reported(
    readiness: ReadinessReport,
) -> None:
    reddit = by_family(readiness, "reddit_community_interest")
    assert reddit.furthest_permitted_stage is None
    assert all(not st.permitted for st in reddit.stages)
    # Declared cleared for offline parsing, yet not usable: the two are kept apart.
    assert reddit.stages[0].declared_cleared and not reddit.stages[0].permitted
    assert any("approval" in b and "PENDING" in b for b in reddit.blockers)
    assert reddit.publication.startswith("not publishable: required approval")
    # Unknown stays unknown: nothing is reported as allowed that the policy does not say.
    assert {p.name: p.value for p in reddit.permissions}["derived_metrics"] == "unknown"


def test_collection_allowed_is_not_publication_allowed(readiness: ReadinessReport) -> None:
    igdb = by_family(readiness, "igdb_catalogue")
    permitted = {st.stage: st.permitted for st in igdb.stages}
    assert permitted[DeploymentStage.PRIVATE_COLLECTION]
    assert not permitted[DeploymentStage.PUBLIC_LAUNCH]
    assert igdb.publication.startswith("not publishable")
    players = by_family(readiness, "steam_concurrent_players")
    assert players.furthest_permitted_stage is DeploymentStage.PRIVATE_DEVELOPMENT
    assert "derived metrics are not explicitly allowed" in players.publication
    wikimedia = by_family(readiness, "wikimedia_pageviews")
    assert wikimedia.furthest_permitted_stage is DeploymentStage.COMMERCIAL_PRODUCTION
    assert wikimedia.publication == "derived output may be published"


def test_recordings_are_verified_only_when_pinned(readiness: ReadinessReport) -> None:
    standing = {(s.family, e.path): e.standing for s in readiness.sources for e in s.evidence}
    assert standing[("wikimedia_pageviews", "tests/fixtures/wikimedia")] is (
        EvidenceStanding.VERIFIED
    )
    assert standing[("igdb_catalogue", "tests/fixtures/catalogue")] is EvidenceStanding.VERIFIED
    # A genuine recording of a Steam response, never reviewed in: unverified.
    assert standing[("steam_concurrent_players", "tests/fixtures/steam_players")] is (
        EvidenceStanding.UNVERIFIED
    )
    assert standing[("steam_news", "tests/fixtures/steam_news")] is EvidenceStanding.SYNTHETIC
    verified = {s.family for s in readiness.sources if s.verified_evidence_today}
    assert verified == {"wikimedia_pageviews", "igdb_catalogue", "steam_app_catalogue"}
    for family in ("steam_concurrent_players", "steam_news", "youtube_creator_supply"):
        assert any(TRUSTED_ACQUISITION_STEP in b for b in by_family(readiness, family).blockers)


def test_a_local_store_is_unverified_and_an_absent_one_is_absent(tmp_path: Path) -> None:
    spec = next(s for s in SPECS if s.family == "steam_concurrent_players")
    absent = assess_source(spec, tmp_path)
    assert {e.path: e.standing for e in absent.evidence}[".metaspike/samples"] is (
        EvidenceStanding.ABSENT
    )
    store = tmp_path / ".metaspike" / "samples"
    store.mkdir(parents=True)
    (store / "2026-10.jsonl").write_text("{}\n")
    found = assess_source(spec, tmp_path)
    assert {e.path: e.standing for e in found.evidence}[".metaspike/samples"] is (
        EvidenceStanding.UNVERIFIED
    )
    assert not found.verified_evidence_today


def test_an_absent_capability_is_reported_absent(tmp_path: Path) -> None:
    from dataclasses import replace

    spec = replace(SPECS[0], adapter="metaspike.sources.wikimedia:NoSuchAdapter", replay=None)
    capabilities = {c.name: c.present for c in assess_source(spec, tmp_path).capabilities}
    assert capabilities == {"adapter": False, "offline_replay": False, "local_store": False}
    real = {c.name: c.present for c in assess_source(SPECS[0], REPO).capabilities}
    assert real["adapter"] and real["offline_replay"]


def test_the_first_source_is_demand_and_never_catalogue(readiness: ReadinessReport) -> None:
    assert readiness.first_source == "wikimedia_pageviews"
    assert by_family(readiness, readiness.first_source).purpose is SourcePurpose.DEMAND_SIGNALS


def test_the_command_is_offline_private_deterministic_and_reads_no_credential(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def no_socket(*_: object, **__: object) -> None:
        raise AssertionError("the readiness command tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", no_socket)
    for name in ("METASPIKE_STEAM_WEB_API_KEY", "METASPIKE_IGDB_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)

    def run(*arguments: str) -> tuple[int, str, str]:
        code = cli.main(["source-readiness", "--root", str(REPO), *arguments])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    code, without, _ = run("--json")
    assert code == cli.EXIT_OK
    # A credential being set changes nothing, and its value never appears.
    monkeypatch.setenv("METASPIKE_STEAM_WEB_API_KEY", "CANARY0123456789")
    monkeypatch.setenv("METASPIKE_IGDB_CLIENT_SECRET", "CANARYSECRET")
    code, with_values, _ = run("--json")
    assert with_values == without
    code, text, _ = run()
    assert "CANARY" not in text + with_values
    assert "METASPIKE_STEAM_WEB_API_KEY" in text  # named, never read
    assert "FIRST SOURCE FOR GENUINE DEMAND EVIDENCE: wikimedia_pageviews" in text
    ReadinessReport.model_validate_json(without)

    out = tmp_path / "readiness.json"
    assert run("--json", "--out", str(out))[0] == cli.EXIT_OK
    assert json.loads(out.read_text()) == json.loads(without)
    code, _, err = run("--out", "artifacts/readiness.json")
    assert code == cli.EXIT_SOURCE_POLICY and "private" in err
    code, _, err = run("--out", str(tmp_path / "public" / "readiness.json"))
    assert code == cli.EXIT_SOURCE_POLICY


# --- acquisition records: integrity and policy, never origin --------------------------------


def steam_bytes() -> bytes:
    return STEAM_RECORDING.read_bytes()


def steam_record(**changes: object) -> AcquisitionRecord:
    arguments: dict[str, object] = {
        "policy": STEAM_CURRENT_PLAYERS_POLICY,
        "purpose": SourcePurpose.DEMAND_SIGNALS,
        "stage": DeploymentStage.PRIVATE_DEVELOPMENT,
        "request": "GET /ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid=730",
        "raw": steam_bytes(),
        "requested_at": STEAM_RECORDED_AT - timedelta(seconds=1),
        "received_at": STEAM_RECORDED_AT,
        "native_ids": ("730",),
        "bindings": (
            NativeBinding(
                native_id="730",
                canonical_game_id="counter-strike-2",
                bound_from=datetime(2026, 10, 5, 2, tzinfo=UTC),
            ),
        ),
    }
    arguments.update(changes)
    return record_acquisition(**arguments)  # type: ignore[arg-type]


def test_an_acquisition_record_keeps_identity_time_and_policy() -> None:
    record = steam_record()
    assert record.source == "steam_current_players"
    assert record.purpose is SourcePurpose.DEMAND_SIGNALS
    assert record.stage is DeploymentStage.PRIVATE_DEVELOPMENT
    assert record.policy_reviewed_at == STEAM_CURRENT_PLAYERS_POLICY.reviewed_at
    assert record.content_bytes == len(steam_bytes())
    assert record.native_ids == ("730",) and record.bindings[0].native_id == "730"
    # Deterministic: the same acquisition gives the same record.
    assert steam_record() == record
    # Point in time: unusable before it arrived.
    assert not usable_at(record, STEAM_RECORDED_AT - timedelta(seconds=1))
    assert usable_at(record, STEAM_RECORDED_AT)


def test_integrity_is_checked_and_is_not_origin() -> None:
    record = steam_record()
    check_content(record, steam_bytes())
    # Intact, permitted, real-looking, and still unverified: a hash authenticates nothing.
    assert acquisition_standing(record, steam_bytes()) is Standing.UNVERIFIED
    with pytest.raises(ContentMismatch):
        check_content(record, steam_bytes() + b" ")
    with pytest.raises(ContentMismatch):
        acquisition_standing(record, steam_bytes().replace(b"730", b"731", 1))
    # There is no field through which a record could be declared verified.
    assert not any("verif" in name for name in AcquisitionRecord.model_fields)


def test_a_record_cannot_be_made_where_the_policy_refuses() -> None:
    with pytest.raises(AcquisitionRefused, match="private_collection"):
        steam_record(stage=DeploymentStage.PRIVATE_COLLECTION)
    with pytest.raises(AcquisitionRefused, match="approval"):
        record_acquisition(
            REDDIT_DATA_POLICY,
            SourcePurpose.COMMUNITY_INTEREST,
            DeploymentStage.PRIVATE_DEVELOPMENT,
            "GET /r/example/search.json",
            b"{}",
            STEAM_RECORDED_AT,
            STEAM_RECORDED_AT,
            ("t3_x",),
        )
    # A YouTube-shaped response may be recorded privately, and that makes it nothing more.
    shaped = record_acquisition(
        YOUTUBE_DATA_POLICY,
        SourcePurpose.CREATOR_SUPPLY,
        DeploymentStage.PRIVATE_DEVELOPMENT,
        "GET /youtube/v3/search?part=snippet&q=game",
        b'{"kind": "youtube#searchListResponse", "items": []}',
        STEAM_RECORDED_AT,
        STEAM_RECORDED_AT,
        ("search",),
    )
    assert acquisition_standing(shaped, b'{"kind": "youtube#searchListResponse", "items": []}') is (
        Standing.UNVERIFIED
    )


def test_a_record_refuses_credentials_backdating_and_later_bindings() -> None:
    with pytest.raises(AcquisitionRefused, match="credential"):
        steam_record(request="GET /ISteamUserStats/GetNumberOfCurrentPlayers/v1/?key=ABC")
    with pytest.raises(AcquisitionRefused, match="before its request"):
        steam_record(requested_at=STEAM_RECORDED_AT + timedelta(seconds=1))
    later = NativeBinding(
        native_id="730",
        canonical_game_id="counter-strike-2",
        bound_from=STEAM_RECORDED_AT + timedelta(days=1),
    )
    with pytest.raises(AcquisitionRefused, match="later record"):
        steam_record(bindings=(later,))
    unknown = later.model_copy(update={"native_id": "570", "bound_from": STEAM_RECORDED_AT})
    with pytest.raises(AcquisitionRefused, match="did not hold"):
        steam_record(bindings=(unknown,))


def test_a_policy_change_after_acquisition_is_visible() -> None:
    record = steam_record()
    assert not policy_changed(record, STEAM_CURRENT_PLAYERS_POLICY)
    revised = STEAM_CURRENT_PLAYERS_POLICY.model_copy(
        update={"derived_metrics_allowed": Permission.ALLOWED}
    )
    assert policy_changed(record, revised)
    assert policy_changed(record, YOUTUBE_DATA_POLICY)


# --- the offline end-to-end rehearsal --------------------------------------------------------


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """No network, no waiting, no credential, and a working directory of its own."""

    def no_socket(*_: object, **__: object) -> None:
        raise AssertionError("the rehearsal tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", no_socket)
    monkeypatch.setattr(cli, "LimiterPool", lambda: LimiterPool(clock=Clock(lambda s: None)))
    monkeypatch.delenv("METASPIKE_STEAM_WEB_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_the_whole_path_keeps_every_standing_honest(
    offline: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """input, provenance, policy, game evidence, assessment, private report, UI schema."""
    # 1. A real recorded Steam response, replayed AFTER the moment it was recorded.
    sampled_at = STEAM_RECORDED_AT + timedelta(minutes=5)
    monkeypatch.setattr(cli, "_now", lambda: sampled_at)
    assert (
        cli.main(
            [
                "sample",
                "--recorded",
                str(FIXTURES / "catalogue"),
                str(FIXTURES / "bootstrap_catalogue"),
                "--state",
                str(offline / "state.json"),
                "--store-dir",
                str(offline / "samples"),
                "--replay",
                str(FIXTURES / "steam_players"),
            ]
        )
        == cli.EXIT_OK
    )
    capsys.readouterr()

    # 2. The pinned pageviews and that local store, read together, privately.
    as_of = sampled_at + timedelta(minutes=30)
    report_file = offline / "private" / "verified.json"
    assert (
        cli.main(
            [
                "evidence",
                "--as-of",
                as_of.isoformat(),
                "--recorded",
                str(FIXTURES / "catalogue"),
                str(FIXTURES / "bootstrap_catalogue"),
                "--wikimedia-replay",
                str(FIXTURES / "wikimedia"),
                "--recorded-through",
                "2026-10-03",
                "--sample-store",
                str(offline / "samples"),
                "--opportunities",
                "--json",
                "--out",
                str(report_file),
            ]
        )
        == cli.EXIT_OK
    )
    capsys.readouterr()
    shortlist = json.loads(report_file.read_text())

    # 3. The private report is what the UI's own schema accepts.
    schema = json.loads(UI_SCHEMA.read_text())
    assert not list(Draft202012Validator(schema).iter_errors(shortlist))
    assert shortlist["distribution"] == "private_development_only"

    sources = {(a["slug"], s["source"]): s for a in shortlist["assessments"] for s in a["sources"]}
    # Pinned pageviews stay verified, and stay distinct from what is missing.
    assert sources[("minecraft", "wikimedia_pageviews")]["standing"] == "verified"
    # A genuine Steam response in a local store, with no trusted acquisition: unverified.
    players = sources[("counter-strike-2", "steam_current_players")]
    assert players["standing"] == "unverified"
    # Creator supply and community were not supplied: no standing at all, never filled.
    assert all(
        s["standing"] == "none"
        for (_, source), s in sources.items()
        if source in ("youtube_data", "reddit_data")
    )
    # Nothing unverified reaches a real readiness, and the order is the engine's.
    by_id = {a["canonical_game_id"]: a for a in shortlist["assessments"]}
    for entry in shortlist["entries"]:
        assert entry["readiness"] == by_id[entry["canonical_game_id"]]["readiness"]
    cs2 = next(a for a in shortlist["assessments"] if a["slug"] == "counter-strike-2")
    assert "steam_players" not in " ".join(s["text"] for s in cs2["why_now"])
    # 4. Nothing was written where it could be published or served.
    assert not (offline / "artifacts").exists()
    assert not any("public" in p.parts for p in offline.rglob("*"))


def test_simulated_readiness_never_becomes_real_in_the_private_report(
    offline: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Invented YouTube, Reddit, Steam and event evidence: the rubric runs; nothing is real."""
    report_file = offline / "synthetic.json"
    games = [arg for slug in ("rust", "dota-2", "minecraft") for arg in ("--game", slug)]
    assert (
        cli.main(
            [
                "evidence",
                "--as-of",
                "2026-10-20T12:00:00+00:00",
                "--recorded",
                str(FIXTURES / "catalogue"),
                str(FIXTURES / "bootstrap_catalogue"),
                "--synthetic-scenario",
                *games,
                "--opportunities",
                "--json",
                "--out",
                str(report_file),
            ]
        )
        == cli.EXIT_OK
    )
    capsys.readouterr()
    shortlist = json.loads(report_file.read_text())
    assert shortlist["contains_synthetic"]
    assert {e["readiness"] for e in shortlist["entries"]} == {"insufficient_evidence"}
    assert any(e["readiness"] != "insufficient_evidence" for e in shortlist["simulated_entries"])
    for assessment in shortlist["assessments"]:
        for angle in assessment["angles"]:
            if angle["origin"] == "community":
                # An invented discussion is never confirmed player interest.
                assert angle["standing"] == "synthetic" and not angle["confirmed"]
    schema = json.loads(UI_SCHEMA.read_text())
    assert not list(Draft202012Validator(schema).iter_errors(shortlist))


def test_fresh_multi_source_input_has_a_defined_way_in(readiness: ReadinessReport) -> None:
    fields = set(EvidenceInputs.__dataclass_fields__)
    assert {"wikimedia", "samples", "events", "event_sources", "creator_supply", "community"} <= (
        fields
    )
    for source in readiness.sources:
        assert source.integration.startswith(("Already integrated", "Defined"))
