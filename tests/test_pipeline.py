"""End-to-end tests: the real adapter and publisher against recorded responses."""

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator

from metaspike import cli
from metaspike.catalogue.legacy_ids import canonical_id_for_legacy
from metaspike.catalogue.seed import load_seed_catalogue
from metaspike.pipeline.collection import window_ending
from metaspike.pipeline.run import CollectionFailedError, run_pipeline
from metaspike.publishing import publisher
from metaspike.publishing.artifacts import SCHEMA_VERSION
from metaspike.publishing.schemas import ARTIFACT_MODELS, schema_text, stale_schemas
from metaspike.publishing.validation import ArtifactValidationError, validate_published
from metaspike.sources.registry import default_registry
from tests.conftest import RECORDED_END, RECORDINGS, RUN_TIME, mock_client, run_fixture_pipeline

SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"
RECORDED_SLUGS = {
    "gta-v",
    "gta-vi",
    "escape-from-tarkov",
    "star-citizen",
    "no-mans-sky",
    "minecraft",
}
VOLATILE = {"generated_at", "run_id", "updated_at", "last_success"}


def load(path: Path) -> Any:
    def reject(constant: str) -> None:
        raise AssertionError(f"non-finite JSON constant {constant} in {path.name}")

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject)


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def without_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: without_volatile(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [without_volatile(item) for item in value]
    return value


def schema_name(relative: str) -> str:
    return "game" if relative.startswith("games/") else relative.removesuffix(".json")


def test_run_publishes_the_complete_artifact_set(artifacts_dir: Path) -> None:
    result = run_fixture_pipeline(artifacts_dir)

    top_level = {p.name for p in artifacts_dir.iterdir()}
    assert top_level == {
        "manifest.json",
        "current.json",
        "radar.json",
        "source-health.json",
        "methodology.json",
        "games",
        "versions",
    }
    assert {p.stem for p in (artifacts_dir / "games").glob("*.json")} == {
        game.slug for game in load_seed_catalogue().games
    }
    assert result.version_dir == artifacts_dir / "versions" / result.bundle.manifest.run_id
    manifest = validate_published(artifacts_dir)
    assert manifest.run_id == load(artifacts_dir / "current.json")["run_id"]


def test_every_published_file_validates_against_its_checked_in_json_schema(
    artifacts_dir: Path,
) -> None:
    run_fixture_pipeline(artifacts_dir)
    validators = {
        name: Draft202012Validator(load(SCHEMAS_DIR / f"{name}.schema.json"))
        for name in ARTIFACT_MODELS
    }
    version_dir = artifacts_dir / load(artifacts_dir / "manifest.json")["version_path"]
    checked = 0
    for root in (artifacts_dir, version_dir):
        for path in sorted(root.rglob("*.json")):
            if "versions" in path.relative_to(root).parts:
                continue
            document = load(path)
            validators[schema_name(str(path.relative_to(root)))].validate(document)
            assert document["schema_version"] == SCHEMA_VERSION
            checked += 1
    assert checked == (5 + 15) + (4 + 15)  # the radar is in both the mirror and the version


def test_checked_in_schemas_match_the_models() -> None:
    assert stale_schemas(SCHEMAS_DIR) == []
    for name, model in ARTIFACT_MODELS.items():
        Draft202012Validator.check_schema(json.loads(schema_text(model)))
        assert "schema_version" in json.loads(schema_text(model))["required"], name


def test_schema_rejects_a_document_that_breaks_the_contract(artifacts_dir: Path) -> None:
    run_fixture_pipeline(artifacts_dir)
    validator = Draft202012Validator(load(SCHEMAS_DIR / "current.schema.json"))
    current = load(artifacts_dir / "current.json")
    assert not list(validator.iter_errors(current))

    current["entries"][0]["score"] = 250
    del current["schema_version"]
    assert len(list(validator.iter_errors(current))) == 2


def test_leaderboard_ranks_scored_games_and_lists_the_rest_as_unranked(
    artifacts_dir: Path,
) -> None:
    run_fixture_pipeline(artifacts_dir)
    current = load(artifacts_dir / "current.json")

    entries, unranked = current["entries"], current["unranked"]
    assert {entry["slug"] for entry in entries} == RECORDED_SLUGS
    assert [entry["rank"] for entry in entries] == [1, 2, 3, 4, 5, 6]
    assert [e["score"] for e in entries] == sorted((e["score"] for e in entries), reverse=True)
    assert len(unranked) == 9 and len({e["slug"] for e in entries + unranked}) == 15
    assert current["score_version"] == "metaspike_score_v0"
    assert current["confidence_ceiling"] == 60
    assert "not a complete measure" in current["scope_note"]
    for entry in entries:
        assert 0 <= entry["score"] <= 100 and 0 <= entry["confidence"] <= 60
        assert entry["summary"] and entry["top_reasons"]
        assert entry["as_of_day"] == RECORDED_END.isoformat()
        assert entry["source_coverage"][0]["status"] == "covered"

    statuses = {e["slug"]: e["source_coverage"][0]["status"] for e in unranked}
    assert statuses["fivem"] == "not_mapped"
    assert statuses["fortnite"] == "failed"


def test_game_artifact_carries_identity_features_history_and_relationships(
    artifacts_dir: Path,
) -> None:
    run_fixture_pipeline(artifacts_dir)
    recorded = load(RECORDINGS / "Grand_Theft_Auto_VI.json")["items"]
    game = load(artifacts_dir / "games" / "gta-vi.json")

    assert (
        game["canonical_game_id"] == canonical_id_for_legacy("msg_0004")
        and game["name"] == "Grand Theft Auto VI"
    )
    assert game["lifecycle_state"] == "PRE_RELEASE"
    assert game["relationships"] == [
        {
            "relationship_type": "SEQUEL",
            "parent_game_id": canonical_id_for_legacy("msg_0001"),
            "parent_slug": "gta-v",
            "parent_name": "Grand Theft Auto V",
        }
    ]
    assert game["events"] == []
    assert game["methodology"]["score_version"] == game["score"]["score_version"]
    assert game["features"]["latest_views"] == recorded[-1]["views"]
    (history,) = game["history"]
    assert history["granularity"] == "daily" and "CC0" in history["attribution"]
    assert [point["value"] for point in history["points"]] == [i["views"] for i in recorded]
    assert history["points"][-1]["day"] == RECORDED_END.isoformat()
    assert history["points"][0]["rolling_mean_7d"] is None
    assert history["points"][-1]["rolling_mean_7d"] is not None

    fivem = load(artifacts_dir / "games" / "fivem.json")
    assert fivem["relationships"][0]["relationship_type"] == "MOD_ECOSYSTEM"
    assert fivem["score"] == {
        "score_version": "metaspike_score_v0",
        "status": "unscored",
        "value": None,
        "components": None,
        "unscored_reason": "no_observations",
    }
    assert fivem["confidence"] is None and fivem["features"] is None and fivem["history"] == []


def test_source_health_records_failures_without_leaking_internals(artifacts_dir: Path) -> None:
    run_fixture_pipeline(artifacts_dir)
    text = (artifacts_dir / "source-health.json").read_text()
    (source,) = json.loads(text)["sources"]

    assert source["source"] == "wikimedia_pageviews" and source["status"] == "degraded"
    assert (source["entities_attempted"], source["entities_succeeded"]) == (14, 6)
    assert (source["entities_failed"], source["entities_not_mapped"]) == (8, 1)
    assert source["last_success"] is not None
    assert source["freshness"] == {
        "expected_latest_day": "2026-10-03",
        "latest_observed_day": "2026-10-03",
        "staleness_days": 0,
    }
    assert {failure["error_code"] for failure in source["failures"]} == {"not_found"}
    assert set(source["failures"][0]) == {"canonical_game_id", "slug", "error_code"}
    assert "Traceback" not in text and "Error(" not in text


def test_methodology_documents_score_confidence_features_and_policy(artifacts_dir: Path) -> None:
    run_fixture_pipeline(artifacts_dir)
    methodology = load(artifacts_dir / "methodology.json")

    assert methodology["score"]["version"] == "metaspike_score_v0"
    assert [term["name"] for term in methodology["score"]["terms"]] == [
        "significance",
        "magnitude",
        "anomaly",
        "momentum",
        "score",
    ]
    assert all(t["formula"] and t["rationale"] for t in methodology["score"]["terms"])
    assert methodology["confidence"]["current_ceiling"] == 60
    assert methodology["features"]["granularity"] == "daily"
    assert len(methodology["features"]["definitions"]) >= 11
    assert len(methodology["limitations"]) >= 5
    policy = methodology["sources"][0]["policy"]
    assert policy["source"] == "wikimedia_pageviews" and policy["reviewed_at"] == "2026-10-04"


def test_rerunning_at_the_same_instant_is_byte_for_byte_identical(artifacts_dir: Path) -> None:
    run_fixture_pipeline(artifacts_dir)
    first = snapshot(artifacts_dir)
    run_fixture_pipeline(artifacts_dir)

    assert snapshot(artifacts_dir) == first
    assert len(list((artifacts_dir / "versions").iterdir())) == 1


def test_rerunning_later_changes_only_generation_metadata(artifacts_dir: Path) -> None:
    first = run_fixture_pipeline(artifacts_dir)
    before = {name: load(artifacts_dir / name) for name in ("current.json", "games/gta-v.json")}
    second = run_fixture_pipeline(artifacts_dir, when=RUN_TIME + timedelta(hours=3))

    assert second.bundle.manifest.content_hash == first.bundle.manifest.content_hash
    assert second.bundle.manifest.run_id != first.bundle.manifest.run_id
    for name, earlier in before.items():
        later = load(artifacts_dir / name)
        assert later != earlier
        assert without_volatile(later) == without_volatile(earlier)
    current = load(artifacts_dir / "current.json")
    assert len(current["entries"]) == 6 and len(current["unranked"]) == 9
    assert len(list((artifacts_dir / "games").glob("*.json"))) == 15
    validate_published(artifacts_dir)


def test_different_data_changes_the_content_hash(artifacts_dir: Path, tmp_path: Path) -> None:
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    for path in RECORDINGS.glob("*.json"):
        (recordings / path.name).write_bytes(path.read_bytes())
    document = load(recordings / "Minecraft.json")
    document["items"][-1]["views"] *= 3
    (recordings / "Minecraft.json").write_text(json.dumps(document))

    from metaspike.sources.replay import replay_client

    with replay_client(recordings) as client:
        changed = run_pipeline(
            load_seed_catalogue(),
            default_registry(client),
            window_ending(RECORDED_END),
            tmp_path / "other",
            clock=lambda: RUN_TIME,
        )
    original = run_fixture_pipeline(artifacts_dir)
    assert changed.bundle.manifest.content_hash != original.bundle.manifest.content_hash
    assert changed.bundle.current.entries[0].slug == "minecraft"
    assert changed.bundle.current.entries[0].score > original.bundle.current.entries[0].score


def test_a_failed_publish_leaves_the_previous_artifacts_untouched(
    artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_fixture_pipeline(artifacts_dir)
    before = snapshot(artifacts_dir)

    def reject(_: Path) -> None:
        raise ArtifactValidationError("simulated contract violation")

    monkeypatch.setattr(publisher, "validate_version", reject)
    with pytest.raises(ArtifactValidationError):
        run_fixture_pipeline(artifacts_dir, when=RUN_TIME + timedelta(hours=1))

    assert snapshot(artifacts_dir) == before
    assert [p.name for p in (artifacts_dir / "versions").iterdir()] == [
        load(artifacts_dir / "manifest.json")["run_id"]
    ]
    monkeypatch.undo()
    validate_published(artifacts_dir)


def test_a_crash_while_writing_leaves_no_partial_version(
    artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_fixture_pipeline(artifacts_dir)
    before = snapshot(artifacts_dir)
    real_to_json = publisher.to_json
    calls = 0

    def fail_midway(artifact: Any) -> str:
        nonlocal calls
        calls += 1
        if calls == 5:
            raise OSError("disk full")
        return real_to_json(artifact)

    monkeypatch.setattr(publisher, "to_json", fail_midway)
    with pytest.raises(OSError, match="disk full"):
        run_fixture_pipeline(artifacts_dir, when=RUN_TIME + timedelta(hours=1))
    assert snapshot(artifacts_dir) == before


def test_when_every_game_fails_nothing_is_published(artifacts_dir: Path) -> None:
    run_fixture_pipeline(artifacts_dir)
    before = snapshot(artifacts_dir)
    client = mock_client(lambda request: httpx.Response(404))

    with pytest.raises(CollectionFailedError):
        run_pipeline(
            load_seed_catalogue(),
            default_registry(client),
            window_ending(RECORDED_END),
            artifacts_dir,
            clock=lambda: RUN_TIME + timedelta(hours=1),
        )
    assert snapshot(artifacts_dir) == before


def test_old_versions_are_pruned_and_removed_games_leave_the_top_level_copy(
    artifacts_dir: Path,
) -> None:
    run_fixture_pipeline(artifacts_dir)
    stray = artifacts_dir / "games" / "removed-game.json"
    stray.write_text("{}")
    for hours in range(1, 8):
        run_fixture_pipeline(artifacts_dir, when=RUN_TIME + timedelta(hours=hours))

    versions = sorted(p.name for p in (artifacts_dir / "versions").iterdir())
    assert len(versions) == publisher.KEEP_VERSIONS
    assert versions[-1] == load(artifacts_dir / "manifest.json")["run_id"]
    assert not stray.exists()
    validate_published(artifacts_dir)


def test_validation_detects_tampering(artifacts_dir: Path) -> None:
    run_fixture_pipeline(artifacts_dir)
    current_path = artifacts_dir / "current.json"
    current_path.write_text(current_path.read_text().replace('"rank": 1', '"rank": 7'))
    with pytest.raises(ArtifactValidationError, match="out of date"):
        validate_published(artifacts_dir)

    version_dir = artifacts_dir / load(artifacts_dir / "manifest.json")["version_path"]
    versioned = version_dir / "current.json"
    document = load(versioned)
    document["entries"][0]["score"] = float("nan")
    versioned.write_text(json.dumps(document))
    with pytest.raises(ArtifactValidationError, match="contract violation"):
        validate_published(artifacts_dir)


def test_cli_run_validate_and_exit_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    artifacts = tmp_path / "artifacts"
    replay = ["--replay", str(RECORDINGS), "--as-of", RECORDED_END.isoformat()]

    assert cli.main(["validate-catalogue"]) == cli.EXIT_OK
    assert cli.main(["run", *replay, "--artifacts-dir", str(artifacts)]) == cli.EXIT_OK
    summary = capsys.readouterr().out
    assert "metaspike_score_v0" in summary and "6 succeeded, 8 failed, 1 not mapped" in summary
    assert cli.main(["validate-artifacts", "--artifacts-dir", str(artifacts)]) == cli.EXIT_OK
    assert cli.main(["export-schemas", "--check", "--schemas-dir", str(SCHEMAS_DIR)]) == cli.EXIT_OK

    empty = tmp_path / "no-recordings"
    empty.mkdir()
    failed = ["run", "--replay", str(empty), "--as-of", RECORDED_END.isoformat()]
    assert cli.main([*failed, "--artifacts-dir", str(artifacts)]) == cli.EXIT_COLLECTION_FAILED
    assert cli.main(["validate-artifacts", "--artifacts-dir", str(artifacts)]) == cli.EXIT_OK

    (artifacts / "manifest.json").write_text("{}")
    broken = cli.main(["validate-artifacts", "--artifacts-dir", str(artifacts)])
    assert broken == cli.EXIT_INVALID_OUTPUT
    assert "Traceback" not in capsys.readouterr().err


def test_cli_collect_then_build_matches_a_single_run(tmp_path: Path) -> None:
    replay = ["--replay", str(RECORDINGS), "--as-of", RECORDED_END.isoformat()]
    cache, split, single = tmp_path / "cache", tmp_path / "split", tmp_path / "single"

    assert cli.main(["collect", *replay, "--cache-dir", str(cache)]) == cli.EXIT_OK
    built = cli.main(["build", "--cache-dir", str(cache), "--artifacts-dir", str(split)])
    assert built == cli.EXIT_OK
    assert cli.main(["run", *replay, "--artifacts-dir", str(single)]) == cli.EXIT_OK

    assert without_volatile(load(split / "current.json")) == without_volatile(
        load(single / "current.json")
    )
    assert (
        load(split / "manifest.json")["content_hash"]
        == (load(single / "manifest.json")["content_hash"])
    )
