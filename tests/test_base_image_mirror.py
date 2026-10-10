"""The image build takes its digest-pinned base from a mirror first, or fails closed.

The build script is run for real against stand-in `curl` and `docker` programs that
answer from a description of what each registry serves. Nothing here reaches a network.
"""

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).parent.parent
SCRIPT = REPO / ".github" / "scripts" / "build-image.sh"
WORKFLOWS = REPO / ".github" / "workflows"
MANIFEST = b'{"schemaVersion":2,"manifests":[{"digest":"sha256:feed"}]}\n'
DIGEST = "sha256:" + hashlib.sha256(MANIFEST).hexdigest()
MIRROR, HUB = "mirror.gcr.io/library", "docker.io/library"
TOKEN = "anonymous-token-that-must-not-be-printed"

FAKE_CURL = """#!{python}
import json, sys
world = json.load(open({world!r}))
args = sys.argv[1:]
out = headers = fmt = None
bearer = False
positional = []
i = 0
while i < len(args):
    a = args[i]
    if a in ("-o", "-D", "-w", "-H", "--max-time", "--data-urlencode"):
        value = args[i + 1]
        if a == "-o": out = value
        if a == "-D": headers = value
        if a == "-w": fmt = value
        if a == "-H" and value.startswith("Authorization: Bearer "):
            bearer = value == "Authorization: Bearer " + {token!r}
        i += 2
        continue
    if not a.startswith("-"):
        positional.append(a)
    i += 1
url = positional[-1]
open({calls!r}, "a").write(url + "\\n")
host = url.split("/")[2]
if host == "auth.example":
    print(json.dumps({{"token": {token!r}}}))
    sys.exit(0)
registry = world.get(host)
if registry is None:
    sys.stderr.write("curl: (6) could not resolve host\\n"); sys.exit(6)
code, body, head = "404", b"not found", ""
if registry.get("needs_token") and not bearer:
    code, body = "401", b"unauthorized"
    head = 'www-authenticate: Bearer realm="https://auth.example/token",service="registry.docker.io",scope="repository:library/python:pull"\\r\\n'
elif registry.get("manifest") is not None:
    code, body = "200", registry["manifest"].encode()
if headers: open(headers, "w").write("HTTP/2 " + code + "\\r\\n" + head)
if out: open(out, "wb").write(body)
if fmt: sys.stdout.write(code)
"""

FAKE_DOCKER = """#!{python}
import json, sys
world = json.load(open({world!r}))
args = sys.argv[1:]
open({calls!r}, "a").write(" ".join(args) + "\\n")
if args[0] == "pull":
    sys.exit(0 if args[-1].split("/")[0] not in world.get("pull_fails", []) else 1)
if args[0] == "build":
    sys.exit(0)
sys.stderr.write("unexpected docker call"); sys.exit(2)
"""


def dockerfile(*lines: str) -> str:
    return "ARG BASE_REGISTRY=docker.io/library\n" + "\n".join(lines) + "\n"


PINNED = dockerfile(
    f"FROM ${{BASE_REGISTRY}}/python:3.12-slim-bookworm@{DIGEST} AS build",
    "RUN true",
    f"FROM ${{BASE_REGISTRY}}/python:3.12-slim-bookworm@{DIGEST}",
)
SERVES = {"manifest": MANIFEST.decode()}


class Run:
    def __init__(self, done: subprocess.CompletedProcess[str], tmp: Path) -> None:
        self.code, self.out, self.err = done.returncode, done.stdout, done.stderr
        self.urls = (tmp / "curl.txt").read_text().splitlines()
        self.docker = (tmp / "docker.txt").read_text().splitlines()
        summary = tmp / "summary.md"
        self.summary = summary.read_text() if summary.exists() else ""

    @property
    def built_from(self) -> str | None:
        builds = [c for c in self.docker if c.startswith("build ")]
        assert len(builds) <= 1
        if not builds:
            return None
        assert "--pull --no-cache" in builds[0] and builds[0].endswith(" --tag test:ci ctx")
        found = re.search(r"--build-arg BASE_REGISTRY=(\S+)", builds[0])
        assert found
        return found.group(1)


def build(tmp_path: Path, world: dict[str, Any], text: str = PINNED) -> Run:
    if shutil.which("jq") is None or shutil.which("bash") is None:
        pytest.skip("bash and jq are needed to run the build script")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (tmp_path / "ctx").mkdir()
    (tmp_path / "ctx" / "Dockerfile").write_text(text)
    (tmp_path / "world.json").write_text(json.dumps(world))
    for name, source in (("curl", FAKE_CURL), ("docker", FAKE_DOCKER)):
        calls = tmp_path / f"{name}.txt"
        calls.write_text("")
        fake = binaries / name
        fake.write_text(
            source.format(
                python=sys.executable,
                world=str(tmp_path / "world.json"),
                calls=str(calls),
                token=TOKEN,
            )
        )
        fake.chmod(0o755)
    done = subprocess.run(
        ["bash", str(SCRIPT), "test:ci", "ctx"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
        env={
            "PATH": f"{binaries}:/usr/bin:/bin:/sbin:/usr/local/bin:/opt/homebrew/bin",
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        },
    )
    return Run(done, tmp_path)


def test_the_mirror_is_used_first_and_docker_hub_is_not_contacted(tmp_path: Path) -> None:
    run = build(tmp_path, {"mirror.gcr.io": SERVES, "registry-1.docker.io": SERVES})
    assert run.code == 0, run.err
    assert run.built_from == MIRROR
    assert run.urls == [f"https://mirror.gcr.io/v2/library/python/manifests/{DIGEST}"]
    assert run.docker[0] == f"pull --quiet {MIRROR}/python@{DIGEST}"  # by digest, never a tag
    assert DIGEST in run.summary and MIRROR in run.summary


def test_docker_hub_is_the_fallback_when_the_mirror_lacks_the_digest(tmp_path: Path) -> None:
    hub = {**SERVES, "needs_token": True}
    run = build(tmp_path, {"mirror.gcr.io": {"manifest": None}, "registry-1.docker.io": hub})
    assert run.code == 0, run.err
    assert run.built_from == HUB
    assert run.docker[0] == f"pull --quiet {HUB}/python@{DIGEST}"
    assert "mirror.gcr.io/library did not serve the pinned base digest" in run.err
    # The anonymous token handshake happened, and the token is nowhere in the output.
    assert any(url.startswith("https://auth.example/token") for url in run.urls)
    assert TOKEN not in run.out + run.err + run.summary


def test_a_mirror_serving_other_bytes_is_not_trusted(tmp_path: Path) -> None:
    wrong = {"manifest": MANIFEST.decode().replace("feed", "f00d")}
    run = build(tmp_path, {"mirror.gcr.io": wrong, "registry-1.docker.io": SERVES})
    assert run.code == 0, run.err
    assert run.built_from == HUB
    assert "served bytes that are not the pinned digest" in run.err
    assert not [c for c in run.docker if c.startswith("pull") and "mirror.gcr.io" in c]


def test_a_mirror_whose_pull_fails_gives_way_to_docker_hub(tmp_path: Path) -> None:
    world = {
        "mirror.gcr.io": SERVES,
        "registry-1.docker.io": SERVES,
        "pull_fails": ["mirror.gcr.io"],
    }
    run = build(tmp_path, world)
    assert run.code == 0, run.err
    assert run.built_from == HUB


@pytest.mark.parametrize(
    "world",
    [
        {"mirror.gcr.io": {"manifest": None}, "registry-1.docker.io": {"manifest": None}},
        {},  # neither registry can be reached at all
        {
            "mirror.gcr.io": {"manifest": "x"},
            "registry-1.docker.io": {"manifest": "y", "needs_token": True},
        },
        {"mirror.gcr.io": SERVES, "registry-1.docker.io": SERVES,
         "pull_fails": ["mirror.gcr.io", "docker.io"]},
    ],
)  # fmt: skip
def test_it_fails_closed_when_no_registry_gives_the_exact_digest(
    tmp_path: Path, world: dict[str, Any]
) -> None:
    run = build(tmp_path, world)
    assert run.code != 0
    assert run.built_from is None  # nothing was built
    assert "::error::build-image: no registry served the pinned base" in run.err


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (dockerfile("FROM ${BASE_REGISTRY}/python:3.12-slim-bookworm"), "does not take the pinned"),
        (dockerfile(f"FROM python:3.12-slim-bookworm@{DIGEST}"), "does not take the pinned"),
        (dockerfile("FROM ${BASE_REGISTRY}/python:latest"), "does not take the pinned"),
        (
            dockerfile(
                f"FROM ${{BASE_REGISTRY}}/python:3.12-slim-bookworm@{DIGEST} AS build",
                f"FROM ${{BASE_REGISTRY}}/python:3.12-slim-bookworm@sha256:{'0' * 64}",
            ),
            "pin different base digests",
        ),
        ("RUN true\n", "no FROM line"),
    ],
)
def test_a_dockerfile_without_one_pinned_base_is_refused_before_any_network(
    tmp_path: Path, text: str, reason: str
) -> None:
    run = build(tmp_path, {"mirror.gcr.io": SERVES, "registry-1.docker.io": SERVES}, text)
    assert run.code != 0 and reason in run.err, run.err
    assert run.urls == [] and run.docker == []


def test_the_real_dockerfile_takes_one_pinned_base_through_the_registry_argument() -> None:
    text = (REPO / "Dockerfile").read_text()
    stages = re.findall(r"^FROM (\S+)", text, re.M)
    assert len(stages) == 2
    digests = set()
    for stage in stages:
        found = re.fullmatch(
            r"\$\{BASE_REGISTRY\}/python:3\.12-slim-bookworm@(sha256:[0-9a-f]{64})", stage
        )
        assert found, stage
        digests.add(found.group(1))
    assert len(digests) == 1
    # The default is the canonical source, declared once, before the first stage.
    assert text.count("ARG BASE_REGISTRY=docker.io/library") == 1
    assert text.index("ARG BASE_REGISTRY") < text.index("\nFROM ")
    assert text.count("BASE_REGISTRY=") == 1  # no stage overrides it


def test_the_script_uses_no_credential_and_never_a_tag() -> None:
    script = SCRIPT.read_text()
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    assert 'registries="${BASE_REGISTRIES:-mirror.gcr.io/library docker.io/library}"' in code
    for word in ("docker login", "DOCKERHUB", "secrets.", "password", "--password", "-u "):
        assert word not in code, word
    assert "docker build --pull --no-cache --build-arg" in code and code.count("docker build") == 1
    assert 'reference="${registry}/${image}@${digest}"' in code  # pulled by digest only
    assert "sha256sum" in code and '[ "${served}" != "${digest}" ]' in code
    assert "set -euo pipefail" in code
    assert not re.search(r"echo[^\n]*\$\{?token", code)  # the anonymous token is not echoed


def test_both_workflows_build_through_the_script_and_keep_every_gate() -> None:
    for name in ("ci.yml", "deploy.yml"):
        text = (WORKFLOWS / name).read_text()
        assert text.count(".github/scripts/build-image.sh ") == 1, name
        assert not re.search(r"^\s*run: docker build", text, re.M), name
        scan = text[text.index("trivy-action") :][:400]
        assert "severity: CRITICAL,HIGH" in scan and 'exit-code: "1"' in scan
        assert "docker login" not in text and "DOCKERHUB" not in text.upper().replace("_", "")
    ci = (WORKFLOWS / "ci.yml").read_text()
    image = ci[ci.index("\n  image:") :]
    # Build, run, dbt, scan: the image job is what it was, with one line changed.
    for step in ("Build (not pushed)", "The image runs and is not root", "Scan the image"):
        assert step in image, step
