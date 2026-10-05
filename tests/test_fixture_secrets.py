"""No credential may ever be committed.

This scans every tracked-style text file in the repository's fixtures, sources, tests
and docs for anything that looks like a credential, and, when real credentials are
present in the environment, for those exact values anywhere in the repository.
"""

import os
import re
from pathlib import Path

import pytest

from metaspike.sources.credentials import (
    IGDB_CLIENT_ID_ENV,
    IGDB_CLIENT_SECRET_ENV,
    STEAM_WEB_API_KEY_ENV,
)

REPO = Path(__file__).parent.parent
FIXTURES = REPO / "tests" / "fixtures"
SCANNED_ROOTS = ("src", "tests", "docs", "schemas", ".github")
TEXT_SUFFIXES = {".py", ".json", ".jsonl", ".md", ".toml", ".yml", ".yaml", ".txt", ".csv"}

# What a leaked credential looks like in recorded HTTP traffic.
FIXTURE_PATTERNS = {
    "steam key in a URL": re.compile(r"(?i)[?&]key=[0-9a-z]"),
    "steam-key-shaped value": re.compile(r"\b[0-9A-F]{32}\b"),
    "authorization header": re.compile(r"(?i)\bauthorization\b"),
    "bearer token": re.compile(r"(?i)\bbearer\s+[a-z0-9._-]{8,}"),
    "oauth access token": re.compile(r"(?i)access_token"),
    "client secret": re.compile(r"(?i)client[_-]secret"),
    "client id header": re.compile(r"(?i)client-id\s*[:=]"),
    "twitch-credential-shaped value": re.compile(r"(?<![a-z0-9])[a-z0-9]{30}(?![a-z0-9])"),
}


def text_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix in TEXT_SUFFIXES and "__pycache__" not in path.parts
    )


def findings(path: Path) -> list[str]:
    content = path.read_text(encoding="utf-8", errors="replace")
    return [name for name, pattern in FIXTURE_PATTERNS.items() if pattern.search(content)]


def test_no_fixture_contains_anything_credential_shaped() -> None:
    fixtures = text_files(FIXTURES)
    assert len(fixtures) >= 7  # the scan is really looking at files
    leaks = {str(p.relative_to(REPO)): found for p in fixtures if (found := findings(p))}
    assert leaks == {}


@pytest.mark.parametrize(
    "leak",
    [
        '{"url": "https://api.steampowered.com/IStoreService/GetAppList/v1/?key=AAAA&max_results=5"}',
        '{"k": "0123456789ABCDEF0123456789ABCDEF"}',
        '{"headers": {"Authorization": "Bearer abcdefghij0123456789"}}',
        '{"access_token": "abcdefghij0123456789abcdefghij", "expires_in": 5000}',
        "client_secret=abcdefghij0123456789abcdefghij",
        '{"Client-ID": "abcdefghij0123456789abcdefghij"}',
        '{"note": "abcdefghij0123456789abcdefghij"}',
    ],
)
def test_the_scanner_catches_each_kind_of_leak(leak: str, tmp_path: Path) -> None:
    planted = tmp_path / "recorded.json"
    planted.write_text(leak)
    assert findings(planted) != []


def test_the_scanner_does_not_flag_ordinary_recorded_data(tmp_path: Path) -> None:
    clean = tmp_path / "recorded.json"
    clean.write_text(
        '{"response": {"apps": [{"appid": 730, "name": "Synthetic Shooter", '
        '"last_modified": 1790000000}], "have_more_results": true, "last_appid": 730}, '
        '"id": "780b4850-6248-4a81-8fe0-7f0762376cfc", '
        '"hash": "bc254cf7d70e417b1bbfa2e0aa74146f3783376bf61bec14778ba6d3f160f551"}'
    )
    assert findings(clean) == []


def test_real_credential_values_appear_nowhere_in_the_repository() -> None:
    """With credentials in the environment, their exact values must not be in any file."""
    secrets = {
        name: value
        for name in (IGDB_CLIENT_ID_ENV, IGDB_CLIENT_SECRET_ENV, STEAM_WEB_API_KEY_ENV)
        if len(value := os.environ.get(name, "").strip()) >= 8
    }
    scanned = [path for root in SCANNED_ROOTS for path in text_files(REPO / root)]
    scanned += [REPO / "README.md", REPO / "pyproject.toml"]
    assert len(scanned) > 80
    for path in scanned:
        content = path.read_text(encoding="utf-8", errors="replace")
        for name, value in secrets.items():
            assert value not in content, f"{name} is present in {path.relative_to(REPO)}"


def test_local_secret_locations_are_git_ignored() -> None:
    ignored = (REPO / ".gitignore").read_text().splitlines()
    for required in (".env", ".env.*", ".metaspike/"):
        assert required in ignored
