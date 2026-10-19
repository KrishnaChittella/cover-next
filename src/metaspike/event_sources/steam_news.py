"""Steam News: announcements for one Steam app, from the documented public method.

Docs: https://partner.steamgames.com/doc/webapi/ISteamNews  (reviewed 2026-10-11)

    GET https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/

    appid      uint32, required   "AppID to retrieve news for"
    maxlength  uint32, optional   "Maximum length for the content to return, if this is
                                   0 the full content is returned"
    enddate    uint32, optional   "Retrieve posts earlier than this date (unix epoch
                                   timestamp)"
    count      uint32, optional   "# of posts to retrieve (default 20)"
    feeds      string, optional   "Comma-seperated list of feed names to return news for"

WHAT IS AND IS NOT DOCUMENTED
Documented: the interface, method, version and the five parameters above. The method
lists no key parameter, and this adapter sends none: no credential is read, held or
transmitted here. The page also documents `GetNewsForAppAuthed`, which needs a
publisher key; it is never used. NOT documented: the response body, any field of it,
error behaviour, caching, and whether `enddate` is exclusive.

Because the response shape is not promised, only three fields of an entry are required
(`gid`, `title`, `date`); every other field is read if present and ignored if not, and
each fetch reports the field names it actually saw. Two fields are deliberately never
read: `contents` (the post body) and `author`. The body is the publisher's own text and
is not needed to read a title; an author is a person. `maxlength` is sent as 1, the
smallest bounded value the documentation allows, so that no body is even transferred in
full.

This is an announcement feed. A post is evidence that something may have happened; it
is not an event. Nothing in this module says what a post means.

Only `api.steampowered.com` is called. No store page is requested, no link in a post
is followed, no asset is downloaded and no HTML is rendered or executed: a title is
kept as plain text.
"""

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from metaspike.domain.signals import SourceErrorCode
from metaspike.domain.source_policy import (
    ApprovalStatus,
    DeploymentStage,
    DisplayRules,
    FieldRule,
    LimitBasis,
    Permission,
    Requirement,
    SourceOperations,
    SourcePolicy,
    StageClearance,
)
from metaspike.event_sources.base import (
    EventSourceFetch,
    FetchProvenance,
    FetchStatus,
    ItemContent,
    RejectedEntry,
    SourceGame,
    SourceItem,
    fetch_id,
    make_item,
)
from metaspike.sources.base import SourceError
from metaspike.sources.http import LimitedHttpClient, RequestBudgetExhausted, RetryConfig
from metaspike.sources.steam_catalogue import STEAM_BUDGET_GROUP, STEAM_CATALOGUE_POLICY

SOURCE_ID = "steam_news"
APP_NAMESPACE = "app"
PUBLIC_HOST = "api.steampowered.com"
NEWS_URL = f"https://{PUBLIC_HOST}/ISteamNews/GetNewsForApp/v2/"

# Request bounds. All self-imposed, all far inside what the method documents.
DEFAULT_COUNT = 5
MAX_COUNT = 20  # the documented default; never asked for more in one request
CONTENT_MAX_LENGTH = 1  # 0 would mean "the full content"
# One transmission per fetch: a retry would be a second request for the same game, and
# the request budget is counted per game.
NEWS_RETRY = RetryConfig(max_attempts=1)
MAX_TITLE_LENGTH = 300
# A post dated further ahead than this, against our own clock, is not believed.
_CLOCK_TOLERANCE = timedelta(hours=24)

# Fields of an entry that are never read, whatever the policy later becomes.
NEVER_READ_FIELDS = frozenset({"contents", "author"})

STEAM_NEWS_POLICY = SourcePolicy(
    source=SOURCE_ID,
    # Conditional, as for every Steam source: stored Steam Data must be disclosed to end
    # users and kept in a country named in a privacy policy.
    raw_storage_allowed=Permission.RESTRICTED,
    retention_days=None,
    historical_storage_allowed=Permission.RESTRICTED,
    # An event derived from a post (a type and a time) is a derived output. The terms do
    # not address derived data. Unanswered is not allowed.
    derived_metrics_allowed=Permission.UNKNOWN,
    commercial_use_allowed=Permission.UNKNOWN,
    # "Distribute Steam Data to end users for their personal use", as is, with
    # conditions; and a post's text belongs to its publisher, which the terms do not
    # speak to at all.
    redistribution_allowed=Permission.RESTRICTED,
    attribution_required=Requirement.REQUIRED,
    attribution_text="Steam news provided by Valve via the Steam Web API.",
    approval_required=Requirement.NOT_REQUIRED,
    approval_status=ApprovalStatus.NOT_REQUIRED,
    commercial_approval_required=Requirement.UNKNOWN,
    commercial_approval_status=ApprovalStatus.PENDING,
    stage_clearance=(
        StageClearance(
            stage=DeploymentStage.PRIVATE_DEVELOPMENT,
            cleared=True,
            reason=(
                "The method is documented, public and keyless. Building the adapter, a "
                "bounded supervised probe and a local store on the owner's machine stay "
                "private and publish nothing. Post bodies and authors are never read."
            ),
        ),
        StageClearance(
            stage=DeploymentStage.PRIVATE_COLLECTION,
            cleared=False,
            reason=(
                "Not cleared. Unattended collection and storage need the same conditions "
                "as every Steam source (a public privacy/data notice with a Steam Data "
                "section, storage only in a named country, calls far below 100,000 a "
                "day), and in addition a decision on keeping publisher-authored titles, "
                "which the Web API terms do not address. No historical backfill has been "
                "budgeted or approved."
            ),
        ),
        StageClearance(
            stage=DeploymentStage.PUBLIC_LAUNCH,
            cleared=False,
            reason=(
                "Derived events are not addressed by the terms, and a post's title is its "
                "publisher's text. Display would also need disclosure of stored Steam "
                "Data, 'as is' disclaimers, Valve attribution and followable links, and "
                "no implied affiliation. A public endpoint is not a permission to show "
                "what it returns."
            ),
        ),
        StageClearance(
            stage=DeploymentStage.COMMERCIAL_PRODUCTION,
            cleared=False,
            reason=(
                "Neither commercial use nor derived data is addressed by the Steam Web "
                "API Terms of Use (July 2010). Both stay unknown until Valve answers."
            ),
        ),
    ),
    operations=SourceOperations(
        budget_group=STEAM_BUDGET_GROUP,
        # One allowance across every Steam endpoint, keyed or not: this source spends
        # from the same budget as the catalogue and the player counts.
        rate_limit=STEAM_CATALOGUE_POLICY.operations.rate_limit,
        quota=STEAM_CATALOGUE_POLICY.operations.quota,
        cache_max_age_seconds=None,
        # A post carries its own publication time and is taken as visible from then.
        # Nothing documents a delay between publishing and the API returning a post.
        expected_publication_lag_seconds=0,
        publication_lag_basis=LimitBasis.SELF_IMPOSED,
    ),
    display=DisplayRules(
        link_back_required=Requirement.REQUIRED,
        branding_required=Requirement.REQUIRED,
        note=(
            "Valve name, logo and links on any page incorporating Steam Data, without "
            "'nofollow'; no appearance of endorsement or affiliation; data 'as is' with "
            "the terms' disclaimers. A derived event must be described in MetaSpike's "
            "own words and must not reproduce a post's body."
        ),
    ),
    field_rules=(
        FieldRule(
            field="contents",
            storage_allowed=Permission.PROHIBITED,
            display_allowed=Permission.PROHIBITED,
            note="Self-imposed. The post body is the publisher's text and is never read.",
        ),
        FieldRule(
            field="author",
            storage_allowed=Permission.PROHIBITED,
            display_allowed=Permission.PROHIBITED,
            note="Self-imposed. An author is a person; nothing here needs one.",
        ),
        FieldRule(
            field="title",
            storage_allowed=Permission.RESTRICTED,
            display_allowed=Permission.UNKNOWN,
            note=(
                "Kept privately as the evidence a classification rests on. Whether a "
                "publisher's title may be shown publicly is not addressed by the terms."
            ),
        ),
        FieldRule(
            field="url",
            storage_allowed=Permission.RESTRICTED,
            display_allowed=Permission.RESTRICTED,
            note="Kept as a reference. Never requested, followed or resolved.",
        ),
    ),
    reviewed_at=date(2026, 10, 11),
    references=(
        "https://partner.steamgames.com/doc/webapi/ISteamNews",
        "https://partner.steamgames.com/doc/webapi_overview",
        "https://steamcommunity.com/dev/apiterms",
        "https://store.steampowered.com/subscriber_agreement/",
    ),
    notes=(
        "Purpose: event intelligence only. This is not the Steam catalogue or player-count "
        "policy and grants nothing to either. Unresolved, recorded and not assumed away: "
        "(1) The Steam Web API Terms of Use are written around an API key; this method "
        "takes none. Whether the terms' licence, limits and conditions apply to a keyless "
        "call is not stated. They are applied here in full, as the stricter reading. "
        "(2) The terms license distributing Steam Data 'as is' for personal use and say "
        "nothing of data derived from it. A classified event is derived. (3) A post is "
        "written by the game's publisher, not by Valve; the terms do not say what a third "
        "party's text may be used for. (4) The Subscriber Agreement's clause on automated "
        "systems, recorded on the player-count policy, applies equally. (5) The response "
        "body, caching and the exact meaning of `enddate` are undocumented; one supervised "
        "probe's observations are in docs/event-sources.md and are not guarantees. "
        "(6) Commercial use is not addressed anywhere reviewed. Being publicly reachable "
        "without a key is a technical fact about the endpoint, not a permission."
    ),
)


_STATUS_ERRORS: dict[int, SourceErrorCode] = {
    401: SourceErrorCode.UNAUTHORIZED,
    403: SourceErrorCode.UNAUTHORIZED,
    404: SourceErrorCode.NOT_FOUND,
    429: SourceErrorCode.RATE_LIMITED,
}


class InvalidEnvelope(Exception):
    """The response as a whole is not the shape this adapter understands."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def app_id_of(source_game_id: str) -> int:
    if not source_game_id.isdigit() or source_game_id.startswith("0"):
        raise ValueError(f"not a Steam app id: {source_game_id!r}")
    return int(source_game_id)


def steam_app(app_id: int | str) -> SourceGame:
    return SourceGame(source=SOURCE_ID, namespace=APP_NAMESPACE, value=str(app_id))


class SteamNewsAdapter:
    def __init__(
        self,
        http: LimitedHttpClient,
        now: Callable[[], datetime] = _utc_now,
        count: int = DEFAULT_COUNT,
    ) -> None:
        if not 1 <= count <= MAX_COUNT:
            raise ValueError(f"count must be between 1 and {MAX_COUNT}")
        self._http = http
        self._now = now
        self._count = count

    @property
    def source(self) -> str:
        return SOURCE_ID

    @property
    def namespace(self) -> str:
        return APP_NAMESPACE

    @property
    def description(self) -> str:
        return (
            "Announcements posted for a Steam app, from the Steam Web API's documented "
            "public news method. Titles and structured tags only; post bodies are not read."
        )

    def fetch(self, source_game_id: str, *, before: datetime | None = None) -> EventSourceFetch:
        app_id = app_id_of(source_game_id)
        game = steam_app(app_id)
        parameters: dict[str, int | str] = {
            "appid": app_id,
            "count": self._count,
            "maxlength": CONTENT_MAX_LENGTH,
        }
        if before is not None:
            if before.tzinfo is None:
                raise ValueError("before must be timezone-aware")
            parameters["enddate"] = int(before.timestamp())
        observed_at = self._now()
        identity = fetch_id(SOURCE_ID, game, parameters, observed_at)

        def outcome(status: FetchStatus, **fields: Any) -> EventSourceFetch:
            return EventSourceFetch(
                fetch_id=identity,
                source=SOURCE_ID,
                game=game,
                endpoint=NEWS_URL,
                parameters=parameters,
                observed_at=observed_at,
                status=status,
                attempts=self._http.budget.used - spent_before,
                **fields,
            )

        spent_before = self._http.budget.used
        try:
            response = self._http.request(
                "GET", NEWS_URL, label=f"steam news (app {app_id})", params=parameters
            )
        except RequestBudgetExhausted:
            return outcome(FetchStatus.NOT_SENT)
        except SourceError as error:
            return outcome(FetchStatus.FAILED, error_code=error.code)
        status = response.status_code
        if status != httpx.codes.OK:
            code = _STATUS_ERRORS.get(status, SourceErrorCode.HTTP_ERROR)
            return outcome(FetchStatus.FAILED, error_code=code, http_status=status)
        try:
            body = response.json()
        except ValueError:
            body = None
        try:
            parsed = parse_news(body, game, observed_at, identity)
        except InvalidEnvelope:
            return outcome(
                FetchStatus.FAILED, error_code=SourceErrorCode.INVALID_RESPONSE, http_status=status
            )
        return outcome(
            FetchStatus.OK,
            http_status=status,
            items=parsed.items,
            rejected=parsed.rejected,
            reported_total=parsed.reported_total,
            observed_envelope_fields=parsed.envelope_fields,
            observed_item_fields=parsed.item_fields,
        )

    def backfill(
        self, source_game_id: str, *, max_requests: int, before: datetime | None = None
    ) -> Iterator[EventSourceFetch]:
        """Walk backwards with the documented `enddate`, one page per request.

        Each page asks for posts earlier than the oldest post of the page before. Stops
        when a fetch fails, a page is empty, or a page's oldest post is no older than
        the cursor (no progress, whatever `enddate` turns out to mean at the boundary).
        """
        if max_requests < 1:
            raise ValueError("a backfill needs a request allowance of at least 1")
        cursor = before
        for _ in range(max_requests):
            page = self.fetch(source_game_id, before=cursor)
            yield page
            if not page.succeeded or not page.items:
                return
            oldest = min(item.published_at for item in page.items)
            if cursor is not None and oldest >= cursor:
                return
            cursor = oldest


class ParsedNews:
    def __init__(
        self,
        items: tuple[SourceItem, ...],
        rejected: tuple[RejectedEntry, ...],
        reported_total: int | None,
        envelope_fields: tuple[str, ...],
        item_fields: tuple[str, ...],
    ) -> None:
        self.items = items
        self.rejected = rejected
        self.reported_total = reported_total
        self.envelope_fields = envelope_fields
        self.item_fields = item_fields


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _plain(text: str) -> str:
    """A title as plain text: control characters removed, whitespace collapsed.

    Nothing is interpreted. Markup in a title stays as the characters it is written
    with; it is never parsed, rendered or executed here or anywhere downstream.
    """
    cleaned = "".join(" " if (ord(char) < 32 or ord(char) == 127) else char for char in text)
    return " ".join(cleaned.split())


def parse_news(
    body: object, requested: SourceGame, observed_at: datetime, fetch: str
) -> ParsedNews:
    """Read a response. A bad envelope fails the request; a bad entry fails alone."""
    envelope = body.get("appnews") if isinstance(body, dict) else None
    if not isinstance(envelope, dict):
        raise InvalidEnvelope("no appnews object")
    entries = envelope.get("newsitems")
    if not isinstance(entries, list):
        raise InvalidEnvelope("no newsitems list")
    answered_for = envelope.get("appid")
    if answered_for is not None and str(answered_for) != requested.value:
        raise InvalidEnvelope("the response is for a different app")
    total = _int(envelope.get("count"))
    items: list[SourceItem] = []
    rejected: list[RejectedEntry] = []
    seen_ids: set[str] = set()
    fields: set[str] = set()
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict):
            rejected.append(RejectedEntry(position=position, reason="not an object"))
            continue
        fields.update(str(name) for name in entry)
        try:
            content = _entry(entry, requested, observed_at, fetch, position)
        except ValueError as problem:
            rejected.append(RejectedEntry(position=position, reason=str(problem)))
            continue
        if content.source_item_id in seen_ids:
            rejected.append(RejectedEntry(position=position, reason="repeats an id in this page"))
            continue
        seen_ids.add(content.source_item_id)
        items.append(make_item(content))
    return ParsedNews(
        items=tuple(items),
        rejected=tuple(rejected),
        reported_total=total if total is not None and total >= 0 else None,
        envelope_fields=tuple(sorted(str(name) for name in envelope)),
        item_fields=tuple(sorted(fields)),
    )


def _entry(
    entry: dict[str, Any], requested: SourceGame, observed_at: datetime, fetch: str, position: int
) -> ItemContent:
    gid = entry.get("gid")
    if _int(gid) is not None:
        gid = str(gid)
    if not isinstance(gid, str) or not gid.strip():
        raise ValueError("no id")
    title = entry.get("title")
    if not isinstance(title, str) or not _plain(title):
        raise ValueError("no title")
    title = _plain(title)
    if len(title) > MAX_TITLE_LENGTH:
        raise ValueError("title too long")
    stamp = _int(entry.get("date"))
    if stamp is None or stamp <= 0:
        raise ValueError("no publication time")
    try:
        published_at = datetime.fromtimestamp(stamp, tz=UTC)
    except (OverflowError, OSError, ValueError):
        raise ValueError("publication time out of range") from None
    if published_at > observed_at + _CLOCK_TOLERANCE:
        raise ValueError("published in the future")
    game = requested
    if entry.get("appid") is not None:
        own_app = _int(entry.get("appid"))
        if own_app is None or own_app <= 0:
            raise ValueError("unreadable app id")
        game = steam_app(own_app)
    url = entry.get("url")
    reference = url.strip() if isinstance(url, str) else None
    if reference is not None and not reference.lower().startswith(("https://", "http://")):
        reference = None
    tags = entry.get("tags")
    external = entry.get("is_external_url")
    return ItemContent(
        source=SOURCE_ID,
        game=game,
        source_item_id=gid.strip(),
        published_at=published_at,
        title=title,
        summary=None,  # the body is never read; see NEVER_READ_FIELDS
        reference=reference or None,
        feed_name=_text(entry.get("feedname")),
        feed_label=_text(entry.get("feedlabel")),
        feed_type=_int(entry.get("feed_type")),
        tags=tuple(sorted({tag.strip() for tag in tags if isinstance(tag, str) and tag.strip()}))
        if isinstance(tags, list)
        else (),
        is_external_reference=external if isinstance(external, bool) else None,
        observed_at=observed_at,
        provenance=FetchProvenance(
            fetch_id=fetch, endpoint=NEWS_URL, requested_game=requested, position=position
        ),
    )


def _text(value: object) -> str | None:
    return _plain(value)[:120] or None if isinstance(value, str) else None
