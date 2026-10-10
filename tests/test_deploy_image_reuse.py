"""An infrastructure-only plan reuses the image the jobs already run, or fails.

The resolving script is run for real, against a stand-in `gcloud` that answers from a
description of the "deployed" jobs and the "registry". Nothing here reaches a cloud.
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).parent.parent
SCRIPT = REPO / ".github" / "scripts" / "resolve-deployed-image.sh"
DEPLOY = REPO / ".github" / "workflows" / "deploy.yml"
PROJECT, REGION = "test-project", "us-central1"
REPOSITORY = f"{REGION}-docker.pkg.dev/{PROJECT}/metaspike/metaspike"
LIVE = f"{REPOSITORY}@sha256:{'a' * 64}"
OTHER = f"{REPOSITORY}@sha256:{'b' * 64}"
JOBS = ("ms-catalogue", "ms-dispatch", "ms-features", "ms-parity", "ms-steam-players",
        "ms-warehouse", "ms-wikimedia")  # fmt: skip

FAKE_GCLOUD = """#!{python}
import json, sys
world = json.load(open({world!r}))
args = sys.argv[1:]
open({calls!r}, "a").write(" ".join(args) + "\\n")
if args[:3] == ["run", "jobs", "list"]:
    print("\\n".join(world["jobs"]))
elif args[:3] == ["run", "jobs", "describe"]:
    job = world["jobs"][args[3]]
    env = [{{"name": "METASPIKE_STAGE", "value": "private_collection"}}]
    if job.get("declared") is not None:
        env.append({{"name": "METASPIKE_IMAGE", "value": job["declared"]}})
    containers = [{{"image": job["image"], "env": env}}] * job.get("containers", 1)
    spec = {{"containers": containers}}
    print(json.dumps({{"spec": {{"template": {{"spec": {{"template": {{"spec": spec}}}}}}}}}}))
elif args[:4] == ["artifacts", "docker", "images", "describe"]:
    if args[4] not in world["registry"]:
        sys.stderr.write("NOT_FOUND\\n"); sys.exit(1)
    print(world["registry"][args[4]])
else:
    sys.stderr.write("unexpected gcloud call: " + " ".join(args)); sys.exit(2)
"""


def deployed(image: str = LIVE) -> dict[str, Any]:
    return {
        "jobs": {job: {"image": image, "declared": image} for job in JOBS},
        "registry": {image: image},
    }


def resolve(tmp_path: Path, world: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    if shutil.which("jq") is None or shutil.which("bash") is None:
        pytest.skip("bash and jq are needed to run the resolving script")
    binaries = tmp_path / "bin"
    binaries.mkdir(exist_ok=True)
    (tmp_path / "world.json").write_text(json.dumps(world))
    calls = tmp_path / "calls.txt"
    calls.write_text("")
    fake = binaries / "gcloud"
    fake.write_text(
        FAKE_GCLOUD.format(
            python=sys.executable, world=str(tmp_path / "world.json"), calls=str(calls)
        )
    )
    fake.chmod(0o755)
    return subprocess.run(
        ["bash", str(SCRIPT), PROJECT, REGION],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": f"{binaries}:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
    )


def test_it_resolves_the_one_image_all_seven_jobs_run(tmp_path: Path) -> None:
    done = resolve(tmp_path, deployed())
    assert done.returncode == 0, done.stderr
    assert done.stdout == LIVE + "\n"  # the image, and nothing else, on standard output
    calls = (tmp_path / "calls.txt").read_text().splitlines()
    described = [c.split()[3] for c in calls if c.startswith("run jobs describe")]
    assert sorted(described) == sorted(JOBS)  # every one of the seven was read
    assert any(c.startswith(f"artifacts docker images describe {LIVE}") for c in calls)
    # It only reads: no build, no push, no tag, no deploy.
    assert not [c for c in calls if re.search(r"\b(push|build|deploy|update|tag|delete)\b", c)]


def refused(tmp_path: Path, world: dict[str, Any], reason: str) -> None:
    done = resolve(tmp_path, world)
    assert done.returncode != 0, "it should have refused"
    assert done.stdout == ""  # no image is offered when it refuses
    assert "::error::reuse_deployed_image:" in done.stderr and reason in done.stderr, done.stderr


def test_the_seven_jobs_must_agree(tmp_path: Path) -> None:
    world = deployed()
    world["jobs"]["ms-warehouse"] = {"image": OTHER, "declared": OTHER}
    world["registry"][OTHER] = OTHER
    refused(tmp_path, world, "do not agree on one image")


def test_each_jobs_own_record_of_its_image_must_agree(tmp_path: Path) -> None:
    world = deployed()
    world["jobs"]["ms-dispatch"]["declared"] = OTHER
    refused(tmp_path, world, "METASPIKE_IMAGE")
    world = deployed()
    world["jobs"]["ms-dispatch"]["declared"] = None
    refused(tmp_path, world, "METASPIKE_IMAGE (unset)")


@pytest.mark.parametrize(
    "image",
    [
        f"{REGION}-docker.pkg.dev/{PROJECT}/metaspike/other@sha256:{'a' * 64}",
        f"{REGION}-docker.pkg.dev/another-project/metaspike/metaspike@sha256:{'a' * 64}",
        f"europe-west1-docker.pkg.dev/{PROJECT}/metaspike/metaspike@sha256:{'a' * 64}",
        f"docker.io/library/python@sha256:{'a' * 64}",
        f"evil.example/{REPOSITORY}@sha256:{'a' * 64}",
        f"{REPOSITORY}:latest",
        f"{REPOSITORY}:{'a' * 40}",
        f"{REPOSITORY}@sha256:{'a' * 63}",
        f"{REPOSITORY}@sha256:{'a' * 65}",
        f"{REPOSITORY}@sha256:{'A' * 64}",
        f"{REPOSITORY}@sha256:{'g' * 64}",
        f"{REPOSITORY}@sha512:{'a' * 64}",
        "",
    ],
)
def test_only_an_immutable_digest_in_the_projects_own_repository_is_accepted(
    tmp_path: Path, image: str
) -> None:
    done = resolve(tmp_path, deployed(image))
    assert done.returncode != 0 and done.stdout == "", image
    assert "::error::reuse_deployed_image:" in done.stderr


def test_the_registry_must_hold_exactly_that_digest(tmp_path: Path) -> None:
    world = deployed()
    world["registry"] = {}
    refused(tmp_path, world, "the registry does not hold")
    world = deployed()
    world["registry"] = {LIVE: OTHER}
    refused(tmp_path, world, "the registry answered with another image")


def test_a_missing_or_an_extra_job_is_refused(tmp_path: Path) -> None:
    world = deployed()
    del world["jobs"]["ms-parity"]
    refused(tmp_path, world, "expected exactly the seven jobs")
    world = deployed()
    world["jobs"]["ms-stranger"] = {"image": LIVE, "declared": LIVE}
    refused(tmp_path, world, "expected exactly the seven jobs")
    world = deployed()
    world["jobs"]["ms-features"]["containers"] = 2
    refused(tmp_path, world, "has 2 containers")
    refused(tmp_path, {"jobs": {}, "registry": {}}, "found: none")


def test_the_script_takes_no_image_from_its_caller() -> None:
    text = SCRIPT.read_text()
    # Two arguments, the project and the region. Nothing names an image.
    assert 'project="${1:?project id}"' in text and 'region="${2:?region}"' in text
    assert "${3" not in text and "$3" not in text and '"$@"' not in text
    assert "docker push" not in text and "docker build" not in text and "docker tag" not in text
    for line in text.splitlines():
        if line.strip().startswith("gcloud"):
            assert re.match(
                r"\s*gcloud (run jobs (list|describe)|artifacts docker images describe)", line
            )
    assert "set -euo pipefail" in text


# --- the workflow ----------------------------------------------------------------------------


def plan_steps() -> list[dict[str, Any]]:
    workflow = yaml.safe_load(DEPLOY.read_text())
    steps: list[dict[str, Any]] = workflow["jobs"]["plan"]["steps"]
    return steps


def named(name: str) -> dict[str, Any]:
    (step,) = [s for s in plan_steps() if s.get("name") == name]
    return step


def test_the_default_plan_still_builds_scans_and_pushes_a_new_image() -> None:
    workflow = yaml.safe_load(DEPLOY.read_text())
    option = workflow[True]["workflow_dispatch"]["inputs"]["reuse_deployed_image"]
    assert option["type"] == "boolean" and option["default"] is False
    for name in ("Build", "Smoke", "Scan before anything is pushed", "Push and record the digest"):
        assert named(name)["if"] == "${{ !inputs.reuse_deployed_image }}", name
    assert "docker push" in named("Push and record the digest")["run"]
    assert named("Build")["run"] == ".github/scripts/build-image.sh candidate"
    script = (REPO / ".github" / "scripts" / "build-image.sh").read_text()
    assert "docker build --pull --no-cache" in script
    assert named("Scan before anything is pushed")["with"]["exit-code"] == "1"
    choose = named("Choose the image to plan with")
    assert choose["env"]["BUILT"] == "${{ steps.push.outputs.image }}"
    assert 'image="${BUILT}"' in choose["run"]


def test_an_infrastructure_only_plan_builds_nothing_and_plans_with_the_resolved_image() -> None:
    resolve_step = named("Resolve the image the jobs already run")
    assert (
        resolve_step["if"] == "${{ inputs.reuse_deployed_image }}"
        and resolve_step["id"] == "deployed"
    )
    assert ".github/scripts/resolve-deployed-image.sh" in resolve_step["run"]
    # It is given the project and the region, and no image.
    assert "inputs." not in resolve_step["run"] and "sha256" not in resolve_step["run"]
    names = [s.get("name") or s.get("uses", "") for s in plan_steps()]

    def order(fragment: str) -> int:
        return next(i for i, n in enumerate(names) if fragment in n)

    # Authenticated first; resolved before the plan.
    assert order("Authenticate") < order("Resolve the image")
    assert order("Resolve the image") < order("Choose the image") < order("Plan")
    choose = named("Choose the image to plan with")
    assert choose.get("if") is None  # it always runs, in either mode
    assert choose["env"]["REUSE"] == "${{ inputs.reuse_deployed_image }}"
    assert choose["env"]["DEPLOYED"] == "${{ steps.deployed.outputs.image }}"
    # One or the other, never a fallback from one to the other.
    assert "||" not in "".join(str(v) for v in choose["env"].values())
    assert 'test -z "${BUILT}"' in choose["run"] and 'test -z "${DEPLOYED}"' in choose["run"]
    assert "@sha256:[0-9a-f]{64}$" in choose["run"]
    plan = named("Plan")["run"]
    assert '-var "image=${{ steps.image.outputs.image }}"' in plan
    assert "steps.push.outputs.image" not in plan and "steps.deployed.outputs.image" not in plan
    summary = named("Show the plan, and say loudly if it destroys or replaces anything")["run"]
    assert "image origin" in summary and "steps.image.outputs.origin" in summary


def test_nobody_can_name_an_image_to_deploy() -> None:
    workflow = yaml.safe_load(DEPLOY.read_text())
    inputs = workflow[True]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {
        "operation", "plan_run_id", "plan_sha256", "allow_destroy", "reuse_deployed_image"
    }  # fmt: skip
    assert [name for name, spec in inputs.items() if spec["type"] == "string"] == [
        "plan_run_id", "plan_sha256"
    ]  # fmt: skip
    text = DEPLOY.read_text()
    assert not re.search(r"inputs\.(image|digest)", text)


def test_the_apply_path_and_the_trust_boundary_are_untouched() -> None:
    workflow = yaml.safe_load(DEPLOY.read_text())
    apply = workflow["jobs"]["apply"]
    text = json.dumps(apply)
    assert "reuse_deployed_image" not in text and "resolve-deployed-image" not in text
    assert "inputs.plan_run_id != '' && inputs.plan_sha256 != ''" in apply["if"]
    names = [s.get("name", "") for s in apply["steps"]]
    assert "It must be exactly the plan whose hash was given" in names
    assert "Refuse an unannounced destroy, then apply exactly that plan" in names
    assert "terraform plan" not in text and "docker" not in text
    # Same trigger, same permissions, same identity, no secret, no new credential.
    assert set(workflow[True]) == {"push", "workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    plan = workflow["jobs"]["plan"]
    assert plan["permissions"] == {"contents": "read", "id-token": "write"}
    assert "inputs.operation == 'plan'" in plan["if"]
    assert "workflow_dispatch" in plan["if"] and "refs/heads/main" in plan["if"]
    whole = DEPLOY.read_text()
    assert "secrets." not in whole and "credentials_json" not in whole
    assert whole.count("google-github-actions/auth@") == 3
    bootstrap = (REPO / "infra" / "bootstrap" / "main.tf").read_text()
    assert ".github/workflows/deploy.yml@refs/heads/main" in bootstrap
    assert "assertion.event_name == 'workflow_dispatch'" in bootstrap
