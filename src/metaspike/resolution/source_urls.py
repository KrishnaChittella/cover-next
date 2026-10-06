"""Strict extraction of a source's own id from a recognised source URL.

Only an exact, allowlisted host with an exact, recognised path yields an id:

    https://store.steampowered.com/app/123456            -> steam 123456
    https://store.steampowered.com/app/123456/Any_Name/  -> steam 123456

Nothing else does. There is no hostname similarity, no substring search and no
guessing: a look-alike host, a different page type (bundle, package, search,
publisher, curator, age gate), a non-numeric id or a longer path is rejected.
Query strings and fragments are ignored.

An extracted id is an ASSERTION by the record that carries the URL, of the same kind
as an external id. It is not title similarity, it is subject to every veto, and on
its own it is not enough to merge anything (see the resolver).
"""

import functools
import re
from urllib.parse import urlsplit

from metaspike.domain.source_records import ExternalId

_STEAM_STORE_HOST = "store.steampowered.com"
# /app/<id>, optionally followed by one name segment and a trailing slash.
_STEAM_APP_PATH = re.compile(r"/app/([1-9][0-9]{0,9})(?:/[^/]*)?/?")


@functools.lru_cache(maxsize=65536)
def recognised_source_id(url: str) -> ExternalId | None:
    """The source id a URL addresses, or None if the URL is not an exact entity page."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or port is not None:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    if parts.netloc.lower() != _STEAM_STORE_HOST:
        return None
    match = _STEAM_APP_PATH.fullmatch(parts.path)
    if match is None:
        return None
    return ExternalId(source="steam", value=match.group(1))


@functools.lru_cache(maxsize=65536)
def url_asserted_ids(urls: tuple[str, ...]) -> tuple[ExternalId, ...]:
    """Every source id asserted by a record's URLs, in order, without duplicates."""
    found = (recognised_source_id(url) for url in urls)
    return tuple(dict.fromkeys(identifier for identifier in found if identifier is not None))
