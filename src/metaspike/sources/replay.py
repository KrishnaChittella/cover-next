"""Offline HTTP client that replays recorded Wikimedia responses from a directory.

Used by the test suite and by `metaspike run --replay DIR` so the whole pipeline,
including the real adapter, can run without network access. A recording is the
response body saved as `<article title>.json`; an article with no recording
answers 404, exactly as the live API does for an article it has no data for.
"""

from pathlib import Path
from urllib.parse import unquote

import httpx

from metaspike.sources.wikimedia import user_agent

# /api/rest_v1/metrics/pageviews/per-article/{project}/{access}/{agent}/{article}/...
_ARTICLE_SEGMENT = 9


def replay_client(recordings: Path) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        segments = request.url.raw_path.decode("ascii").split("/")
        article = unquote(segments[_ARTICLE_SEGMENT])
        recording = recordings / f"{article}.json"
        if not recording.is_file():
            return httpx.Response(404, json={"status": 404, "title": "Not Found"})
        return httpx.Response(
            200, content=recording.read_bytes(), headers={"Content-Type": "application/json"}
        )

    return httpx.Client(
        transport=httpx.MockTransport(handler), headers={"User-Agent": user_agent()}
    )
