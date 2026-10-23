"""One day's views of every game article, from Wikimedia's daily pageview dump (CC0).

    https://dumps.wikimedia.org/other/pageview_complete/YYYY/YYYY-MM/pageviews-YYYYMMDD-user.bz2

Each line is `wiki title page_id platform daily_total hourly`; a page appears once per
platform (desktop, mobile-web, mobile-app). The dump lists every page viewed at least once
that day, so a game article absent from a complete dump had no recorded views: zero, for
that day only. The `user` file already excludes known bots. Same source and licence as the
pageviews API (`WIKIMEDIA_PAGEVIEWS_POLICY`); one file a day, one connection at a time.
"""

import bz2
from collections.abc import Iterable, Iterator
from datetime import date
from typing import Final

import httpx

from metaspike.domain.signals import SourceErrorCode
from metaspike.sources.base import SourceError

DUMP_URL: Final = (
    "https://dumps.wikimedia.org/other/pageview_complete/"
    "{day:%Y}/{day:%Y-%m}/pageviews-{day:%Y%m%d}-user.bz2"
)
PROJECT: Final = "en.wikipedia"
_CHUNK: Final = 1 << 20


def count_views(lines: Iterable[str], articles: set[str]) -> dict[str, int]:
    """Sum each wanted article's daily total across platforms. Others are skipped."""
    prefix = PROJECT + " "
    views: dict[str, int] = {}
    for line in lines:
        if not line.startswith(prefix):
            continue
        parts = line.split(" ")
        if len(parts) < 5 or parts[1] not in articles or not parts[4].isdigit():
            continue
        views[parts[1]] = views.get(parts[1], 0) + int(parts[4])
    return views


def stream_lines(client: httpx.Client, day: date) -> Iterator[str]:
    """Decompress the day's file as it downloads. The file may hold several bz2 streams."""
    url = DUMP_URL.format(day=day)
    try:
        with client.stream("GET", url, timeout=httpx.Timeout(120.0, connect=10.0)) as response:
            if response.status_code == httpx.codes.NOT_FOUND:
                raise SourceError(SourceErrorCode.NOT_FOUND, f"no pageview dump for {day} yet")
            if response.status_code != httpx.codes.OK:
                raise SourceError(
                    SourceErrorCode.HTTP_ERROR, f"dump {day}: HTTP {response.status_code}"
                )
            decompressor = bz2.BZ2Decompressor()
            tail = b""
            for chunk in response.iter_bytes(_CHUNK):
                while chunk:
                    data = decompressor.decompress(chunk)
                    chunk = b""
                    if decompressor.eof:  # a new stream follows in the same file
                        chunk = decompressor.unused_data
                        decompressor = bz2.BZ2Decompressor()
                    *lines, tail = (tail + data).split(b"\n")
                    for line in lines:
                        yield line.decode("utf-8", "replace")
            if tail:
                yield tail.decode("utf-8", "replace")
    except httpx.HTTPError as error:
        raise SourceError(
            SourceErrorCode.NETWORK_ERROR, f"dump {day}: {type(error).__name__}"
        ) from None
