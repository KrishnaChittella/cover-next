"""Event source ingestion: policy, the Steam News adapter, candidates, the classifier,
the promotion gate, storage, coverage and what reaches Event Heat.

Everything is offline. Every post here is SYNTHETIC: invented in this file or in
`tests/fixtures/steam_news`, in the shape the adapter reads. Nothing is a recording.
"""

import hashlib
import json
import re
import socket
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from metaspike import cli
from metaspike.catalogue.generate import generate_catalogue
from metaspike.domain.events import EventType
from metaspike.domain.signals import SourceErrorCode
from metaspike.domain.source_policy import (
    DeploymentStage,
    Permission,
    Requirement,
    SourcePurpose,
)
from metaspike.event_sources import steam_news as steam_news_module
from metaspike.event_sources.base import (
    EventSourceAdapter,
    EventSourceFetch,
    FetchStatus,
    SourceGame,
    SourceItem,
)
from metaspike.event_sources.candidates import (
    EVENT_CLASSIFICATION_RULES_V1,
    RULES_VERSION,
    EventCandidate,
    EvidenceCategory,
    GameBinding,
    PromotionStatus,
    Refusal,
    TimingBasis,
    classify,
    dates_in,
    has_phrase,
    promoted_observation,
    words,
)
from metaspike.event_sources.ingest import IngestReport, ingest
from metaspike.event_sources.registry import (
    EVENT_SOURCE_POLICIES,
    build_steam_news,
    event_source_refusal,
    replay_news_client,
)
from metaspike.event_sources.resolve import steam_app_bindings, steam_news_resolver
from metaspike.event_sources.steam_news import (
    CONTENT_MAX_LENGTH,
    NEWS_URL,
    SOURCE_ID,
    STEAM_NEWS_POLICY,
    InvalidEnvelope,
    SteamNewsAdapter,
    parse_news,
    steam_app,
)
from metaspike.event_sources.store import STORE_GENERATION, EventSourceStore, storage_refusal
from metaspike.event_sources.summary import summarise
from metaspike.events.heat import Availability, EventIntelligence
from metaspike.events.intelligence import assess_events
from metaspike.events.model import EventStatus, TimePrecision
from metaspike.events.rules import EVENT_HEAT_RULES_V1
from metaspike.events.store import EventStore
from metaspike.publishing.artifacts import ArtifactBundle
from metaspike.publishing.build import EventInputs
from metaspike.sources.http import AttemptBudget, LimitedHttpClient
from metaspike.sources.limiter import Clock, LimiterPool, RateLimiter
from metaspike.sources.registry import SourcePolicyError
from metaspike.sources.steam_catalogue import STEAM_CATALOGUE_POLICY
from metaspike.sources.steam_players import STEAM_CURRENT_PLAYERS_POLICY
from metaspike.storage.base import StoragePolicyError
from tests.conftest import make_id

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "steam_news"
RULES = EVENT_CLASSIFICATION_RULES_V1
OFFICIAL = "steam_community_announcements"
GAME, OTHER = make_id(1), make_id(2)
APP, OTHER_APP, UNBOUND_APP = "730", "570", "424242"
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)
HOUR = timedelta(hours=1)
PUBLISHED = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
BINDINGS = {
    APP: GameBinding(canonical_game_id=GAME, binding_ref="decision-1", mapping_version=3),
    OTHER_APP: GameBinding(canonical_game_id=OTHER),
}
RESOLVER = steam_news_resolver(BINDINGS)


def stamp(moment: datetime) -> int:
    return int(moment.timestamp())


def post(
    gid: str | int,
    title: str,
    *,
    published: datetime = PUBLISHED,
    feed: str | None = OFFICIAL,
    tags: Sequence[Any] | None = None,
    app: int | None = 730,
    **extra: Any,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "gid": gid,
        "title": title,
        "date": stamp(published),
        "url": f"https://example.invalid/news/{gid}",
        "author": "synthetic-author",
        "contents": "synthetic body that must never be kept",
    }
    if feed is not None:
        entry["feedname"] = feed
    if tags is not None:
        entry["tags"] = list(tags)
    if app is not None:
        entry["appid"] = app
    entry.update(extra)
    return entry


def envelope(entries: Sequence[Any], app: int = 730, **extra: Any) -> dict[str, Any]:
    return {"appnews": {"appid": app, "newsitems": list(entries), "count": len(entries), **extra}}


Handler = Callable[[httpx.Request], httpx.Response]


def serving(payloads: Mapping[str, Any]) -> tuple[Handler, list[httpx.Request]]:
    """Answer each app id with its payload: a body, or an (status, body) pair."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        payload = payloads[request.url.params["appid"]]
        if isinstance(payload, tuple):
            return httpx.Response(payload[0], json=payload[1])
        if isinstance(payload, Exception):
            raise payload
        return httpx.Response(200, json=payload)

    return handler, seen


class Ticking:
    """A clock that says a fixed time until told otherwise."""

    def __init__(self, moment: datetime = NOW) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


def adapter_for(
    handler: Handler, clock: Callable[[], datetime] | None = None, budget: int | None = None
) -> SteamNewsAdapter:
    limiter = RateLimiter(STEAM_NEWS_POLICY.operations, clock=Clock(sleeper=lambda _: None))
    http = LimitedHttpClient(
        httpx.Client(transport=httpx.MockTransport(handler)),
        limiter,
        steam_news_module.NEWS_RETRY,
        budget=AttemptBudget(budget),
    )
    return SteamNewsAdapter(http, clock or Ticking())


def item_of(title: str, **fields: Any) -> SourceItem:
    """One parsed item, as the adapter would return it."""
    observed = fields.pop("observed", NOW)
    parsed = parse_news(
        envelope([post("42", title, **fields)]), steam_app(730), observed, "fetch_" + "0" * 32
    )
    assert not parsed.rejected, parsed.rejected
    return parsed.items[0]


def read(title: str, *, bound: bool = True, cleared: bool = True, **fields: Any) -> EventCandidate:
    return classify(
        item_of(title, **fields), BINDINGS[APP] if bound else None, source_cleared=cleared
    )


class Stores:
    def __init__(self, root: Path) -> None:
        self.sources = EventSourceStore(root / "event-sources")
        self.events = EventStore(root / "events")

    def ingest(
        self, adapter: SteamNewsAdapter, apps: Sequence[str] = (APP,), **options: Any
    ) -> IngestReport:
        return ingest(
            adapter, STEAM_NEWS_POLICY, apps, RESOLVER, self.sources, self.events, **options
        )

    def heat(self, as_of: datetime, game: str = GAME) -> EventIntelligence:
        return assess_events(game, self.events.events(as_of), self.events.checks(as_of), as_of)


# --- the source policy ----------------------------------------------------------------


def test_the_event_source_has_its_own_policy_and_states_every_permission() -> None:
    policy = STEAM_NEWS_POLICY
    assert policy.source == SOURCE_ID == "steam_news"
    # Its own policy, not the catalogue's or the player counts' reused.
    assert policy is not STEAM_CATALOGUE_POLICY and policy is not STEAM_CURRENT_PLAYERS_POLICY
    assert policy.source not in (STEAM_CATALOGUE_POLICY.source, STEAM_CURRENT_PLAYERS_POLICY.source)
    cleared = {c.stage: c.cleared for c in policy.stage_clearance}
    assert cleared == {
        DeploymentStage.PRIVATE_DEVELOPMENT: True,
        DeploymentStage.PRIVATE_COLLECTION: False,
        DeploymentStage.PUBLIC_LAUNCH: False,
        DeploymentStage.COMMERCIAL_PRODUCTION: False,
    }
    assert all(c.reason for c in policy.stage_clearance)
    assert policy.raw_storage_allowed is Permission.RESTRICTED
    assert policy.historical_storage_allowed is Permission.RESTRICTED
    # Unknown stays unknown: a derived event and commercial use are both unanswered.
    assert policy.derived_metrics_allowed is Permission.UNKNOWN
    assert policy.commercial_use_allowed is Permission.UNKNOWN
    assert policy.redistribution_allowed is Permission.RESTRICTED
    assert policy.attribution_required is Requirement.REQUIRED and policy.attribution_text
    assert not policy.commercial_production_permitted
    assert policy.publication_refusal() is not None
    # One allowance across every Steam endpoint.
    operations = policy.operations
    assert operations.budget_group == STEAM_CATALOGUE_POLICY.operations.budget_group
    assert operations.rate_limit == STEAM_CATALOGUE_POLICY.operations.rate_limit
    assert operations.quota == STEAM_CATALOGUE_POLICY.operations.quota
    assert operations.expected_publication_lag == timedelta(0)
    assert "https://partner.steamgames.com/doc/webapi/ISteamNews" in policy.references
    assert "https://steamcommunity.com/dev/apiterms" in policy.references
    for unresolved in ("keyless", "derived", "publisher", "Commercial use is not addressed"):
        assert unresolved in policy.notes
    assert "not a permission" in policy.notes
    rules = {rule.field: rule for rule in policy.field_rules}
    assert rules["contents"].storage_allowed is Permission.PROHIBITED
    assert rules["author"].storage_allowed is Permission.PROHIBITED
    assert rules["title"].display_allowed is Permission.UNKNOWN
    assert policy.field_retention_permitted("title") and policy.field_retention_permitted("url")
    assert not policy.field_retention_permitted("contents")
    assert not policy.field_retention_permitted("author")
    assert {SOURCE_ID: policy} == EVENT_SOURCE_POLICIES


def test_the_policy_gate_clears_private_development_and_nothing_further() -> None:
    purpose = SourcePurpose.EVENT_INTELLIGENCE
    assert (
        STEAM_NEWS_POLICY.registration_refusal(purpose, DeploymentStage.PRIVATE_DEVELOPMENT) is None
    )
    assert event_source_refusal(SOURCE_ID, DeploymentStage.PRIVATE_DEVELOPMENT) is None
    collection = event_source_refusal(SOURCE_ID, DeploymentStage.PRIVATE_COLLECTION)
    assert collection is not None and "not cleared for private_collection" in collection
    for stage in (DeploymentStage.PUBLIC_LAUNCH, DeploymentStage.COMMERCIAL_PRODUCTION):
        refusal = event_source_refusal(SOURCE_ID, stage)
        # Being reachable without a key is not permission to show what comes back.
        assert refusal is not None and "derived events are not explicitly allowed" in refusal
    assert event_source_refusal("somewhere_else", DeploymentStage.PRIVATE_DEVELOPMENT) == (
        "no source policy is registered for this event source"
    )
    # A prohibition holds at every stage; an unknown storage permission stops collection.
    prohibited = STEAM_NEWS_POLICY.model_copy(
        update={"derived_metrics_allowed": Permission.PROHIBITED}
    )
    assert "prohibited" in (
        prohibited.registration_refusal(purpose, DeploymentStage.PRIVATE_DEVELOPMENT) or ""
    )
    unknown = STEAM_NEWS_POLICY.model_copy(
        update={"historical_storage_allowed": Permission.UNKNOWN}
    )
    assert "storing" in (
        unknown.registration_refusal(purpose, DeploymentStage.PRIVATE_DEVELOPMENT) or ""
    )
    # Even with derived events allowed, showing a publisher's title needs its own yes.
    derived = STEAM_NEWS_POLICY.model_copy(update={"derived_metrics_allowed": Permission.ALLOWED})
    assert "showing the source's announcements" in (
        derived.registration_refusal(purpose, DeploymentStage.PUBLIC_LAUNCH) or ""
    )


def test_ingestion_is_refused_before_any_request_at_a_stage_that_is_not_cleared(
    tmp_path: Path,
) -> None:
    handler, seen = serving({APP: envelope([post("1", "Hotfix 1.0", tags=["patchnotes"])])})
    stores = Stores(tmp_path)
    for stage in (
        DeploymentStage.PRIVATE_COLLECTION,
        DeploymentStage.PUBLIC_LAUNCH,
        DeploymentStage.COMMERCIAL_PRODUCTION,
    ):
        with pytest.raises(SourcePolicyError):
            stores.ingest(adapter_for(handler), stage=stage)
    with pytest.raises(SourcePolicyError, match="policy is for"):
        ingest(
            adapter_for(handler),
            STEAM_CATALOGUE_POLICY,
            [APP],
            RESOLVER,
            stores.sources,
            stores.events,
        )
    assert seen == [] and stores.sources.fetches() == () and stores.events.checks() == ()


# --- the adapter ----------------------------------------------------------------------


def test_the_adapter_sends_only_the_documented_bounded_request_and_no_credential() -> None:
    handler, seen = serving({APP: envelope([])})
    concrete = adapter_for(handler)
    adapter: EventSourceAdapter = concrete  # it satisfies the source-independent boundary
    fetch = adapter.fetch(APP)
    (request,) = seen
    assert request.method == "GET"
    assert str(request.url).split("?")[0] == NEWS_URL
    assert NEWS_URL == "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
    assert dict(request.url.params) == {"appid": "730", "count": "5", "maxlength": "1"}
    assert CONTENT_MAX_LENGTH == 1  # 0 would ask for the full body
    sent = " ".join([str(request.url), *request.headers.keys(), *request.headers.values()]).lower()
    for credential in ("key", "token", "authorization", "x-webapi-key"):
        assert credential not in sent
    assert fetch.status is FetchStatus.OK and fetch.items == () and fetch.attempts == 1
    assert fetch.parameters == {"appid": 730, "count": 5, "maxlength": 1}
    assert fetch.endpoint == NEWS_URL and fetch.game == steam_app(730)
    # `before` is the documented enddate, in unix seconds.
    adapter.fetch(APP, before=PUBLISHED)
    assert seen[-1].url.params["enddate"] == str(stamp(PUBLISHED))
    with pytest.raises(ValueError, match="timezone-aware"):
        adapter.fetch(APP, before=datetime(2026, 10, 1))
    for bad in ("0730", "abc", "-1", "", "7 30"):
        with pytest.raises(ValueError, match="not a Steam app id"):
            adapter.fetch(bad)
    for count in (0, 21, -1):
        with pytest.raises(ValueError, match="count must be between"):
            SteamNewsAdapter(concrete._http, count=count)
    assert len(seen) == 2
    # The module reads no credential and names no other host or method.
    source = Path(steam_news_module.__file__).read_text()
    code = source.split('"""', 2)[2]
    for forbidden in ("secret_from_env", "Secret", "api_key", "GetNewsForAppAuthed", "environ"):
        assert forbidden not in code
    assert "partner.steam-api.com" not in source  # the keyed partner host is never named
    assert "store.steampowered.com" not in code.replace(
        "https://store.steampowered.com/subscriber_agreement/", ""
    )


def test_a_response_is_read_conservatively() -> None:
    body = envelope(
        [
            post("1001", "  Hotfix\t1.2\n now  ", tags=["patchnotes", "patchnotes", 7, " "]),
            post("1002", "Only the three required fields", feed=None, app=None, url=None),
            post("1003", "Unexpected fields are ignored", brand_new_field={"nested": True}),
            post(1004, "A numeric id is accepted", url="javascript:alert(1)"),
        ],
        something_new="ignored",
    )
    parsed = parse_news(body, steam_app(730), NOW, "fetch_" + "a" * 32)
    assert parsed.rejected == () and len(parsed.items) == 4
    first, bare, extra, numeric = parsed.items
    # A title is plain text: control characters and runs of space removed, nothing parsed.
    assert first.title == "Hotfix 1.2 now"
    assert first.tags == ("patchnotes",)
    assert first.published_at == PUBLISHED and first.observed_at == NOW
    assert first.source == SOURCE_ID and first.source_item_id == "1001"
    assert first.reference == "https://example.invalid/news/1001"
    assert first.feed_name == OFFICIAL and first.game == steam_app(730)
    assert first.provenance.fetch_id == "fetch_" + "a" * 32 and first.provenance.position == 0
    assert first.provenance.requested_game == steam_app(730)
    # The body and the author are never kept, whatever the response carried.
    for item in parsed.items:
        dumped = item.model_dump_json()
        assert item.summary is None
        assert "synthetic body" not in dumped and "synthetic-author" not in dumped
        assert "author" not in item.model_dump() and "contents" not in item.model_dump()
    # Optional fields stay optional.
    assert bare.feed_name is None and bare.reference is None and bare.tags == ()
    assert bare.feed_type is None and bare.is_external_reference is None
    assert bare.game == steam_app(730)  # falls back to the app that was asked about
    assert extra.title == "Unexpected fields are ignored"
    assert numeric.source_item_id == "1004"
    assert numeric.reference is None  # not an http(s) reference: dropped, never followed
    # The fetch reports the names it saw, never their values.
    assert parsed.envelope_fields == ("appid", "count", "newsitems", "something_new")
    assert {"gid", "title", "date", "contents", "author", "brand_new_field"} <= set(
        parsed.item_fields
    )
    assert parsed.reported_total == 4
    # Markup in a title is kept as characters and never interpreted.
    scripted = item_of("<script>alert(1)</script> Hotfix <b>2.0</b>")
    assert scripted.title == "<script>alert(1)</script> Hotfix <b>2.0</b>"


def test_a_malformed_entry_fails_alone_and_a_malformed_envelope_fails_the_request() -> None:
    entries = [
        post("2001", "A good post"),
        "not an object",
        post("", "No id"),
        post("2003", "   "),
        post("2004", "No date", date=None),
        post("2005", "A date that is not a number", date="yesterday"),
        post("2006", "A boolean date", date=True),
        post("2007", "Negative date", date=-5),
        post("2008", "From the future", published=NOW + 3 * DAY),
        post("2009", "x" * 301),
        post("2010", "Unreadable app", app=None, appid="seven"),
        post("2001", "The same id again"),
        post("2012", "Another good post", published=NOW + HOUR),  # within clock tolerance
    ]
    parsed = parse_news(envelope(entries), steam_app(730), NOW, "fetch_" + "b" * 32)
    assert [item.source_item_id for item in parsed.items] == ["2001", "2012"]
    assert {entry.position: entry.reason for entry in parsed.rejected} == {
        1: "not an object",
        2: "no id",
        3: "no title",
        4: "no publication time",
        5: "no publication time",
        6: "no publication time",
        7: "no publication time",
        8: "published in the future",
        9: "title too long",
        10: "unreadable app id",
        11: "repeats an id in this page",
    }
    # A rejection never carries the entry: only where it was and why.
    assert "x" * 50 not in json.dumps([entry.model_dump() for entry in parsed.rejected])
    broken_envelopes: tuple[Any, ...] = (
        None,
        [],
        "text",
        {},
        {"appnews": None},
        {"appnews": []},
        {"appnews": {}},
        {"appnews": {"newsitems": None}},
        {"appnews": {"newsitems": {"0": post("1", "x")}}},
        {"appnews": {"appid": 440, "newsitems": []}},  # an answer about a different app
    )
    for broken in broken_envelopes:
        with pytest.raises(InvalidEnvelope):
            parse_news(broken, steam_app(730), NOW, "fetch_" + "c" * 32)
    assert (
        parse_news({"appnews": {"newsitems": []}}, steam_app(730), NOW, "fetch_" + "c" * 32).items
        == ()
    )


def test_every_failure_is_a_failed_fetch_and_never_an_empty_success() -> None:
    cases: dict[str, tuple[Any, SourceErrorCode]] = {
        "1": ((401, {}), SourceErrorCode.UNAUTHORIZED),
        "2": ((403, {}), SourceErrorCode.UNAUTHORIZED),
        "3": ((404, {}), SourceErrorCode.NOT_FOUND),
        "4": ((429, {}), SourceErrorCode.RATE_LIMITED),
        "5": ((500, {}), SourceErrorCode.HTTP_ERROR),
        "6": ((503, {}), SourceErrorCode.HTTP_ERROR),
        "7": ((302, {}), SourceErrorCode.HTTP_ERROR),
        "8": ({"unexpected": True}, SourceErrorCode.INVALID_RESPONSE),
        "9": ((200, "<html>not json</html>"), SourceErrorCode.INVALID_RESPONSE),
        "10": (httpx.ConnectError("refused"), SourceErrorCode.NETWORK_ERROR),
        "11": (httpx.ReadTimeout("slow"), SourceErrorCode.TIMEOUT),
    }
    handler, seen = serving({app: payload for app, (payload, _) in cases.items()})
    adapter = adapter_for(handler)
    for app, (_, code) in cases.items():
        fetch = adapter.fetch(app)
        assert fetch.status is FetchStatus.FAILED and not fetch.succeeded, app
        assert fetch.error_code is code, app
        assert fetch.items == () and fetch.attempts == 1, app
    assert len(seen) == len(cases)  # one request each: no retry behind the caller's back
    with pytest.raises(ValidationError, match="carries no items"):
        EventSourceFetch.model_validate(
            {**adapter.fetch("1").model_dump(), "items": [item_of("x").model_dump()]}
        )
    with pytest.raises(ValidationError, match="exactly when the source failed"):
        EventSourceFetch.model_validate({**adapter.fetch("1").model_dump(), "error_code": None})


def test_the_request_budget_is_a_hard_stop_and_is_not_a_source_failure(tmp_path: Path) -> None:
    handler, seen = serving({APP: envelope([]), OTHER_APP: envelope([])})
    adapter = adapter_for(handler, budget=1)
    assert adapter.fetch(APP).succeeded
    refused = adapter.fetch(OTHER_APP)
    assert refused.status is FetchStatus.NOT_SENT and refused.error_code is None
    assert refused.attempts == 0 and len(seen) == 1
    # Nothing was learned about that game, so it gets no coverage.
    stores = Stores(tmp_path)
    report = stores.ingest(adapter_for(handler, budget=1), (APP, OTHER_APP))
    assert (report.requests, report.fetches_ok, report.fetches_not_sent) == (1, 1, 1)
    assert [check.canonical_game_id for check in stores.events.checks()] == [GAME]
    # The builder shares the Steam budget group and bounds the adapter by construction.
    pool = LimiterPool(clock=Clock(sleeper=lambda _: None))
    built = build_steam_news(
        httpx.Client(transport=httpx.MockTransport(handler)), pool, max_requests=2, now=Ticking()
    )
    assert pool.for_policy(STEAM_CATALOGUE_POLICY) is pool.for_policy(STEAM_NEWS_POLICY)
    assert [built.fetch(APP).status, built.fetch(APP).status, built.fetch(APP).status] == [
        FetchStatus.OK,
        FetchStatus.OK,
        FetchStatus.NOT_SENT,
    ]


def test_ids_are_deterministic_and_separate_what_was_said_from_when_it_was_seen() -> None:
    first, again = item_of("Hotfix 1.0"), item_of("Hotfix 1.0")
    later = item_of("Hotfix 1.0", observed=NOW + DAY)
    edited = item_of("Hotfix 1.0 (updated)", observed=NOW + DAY)
    assert first == again and first.item_version_id == again.item_version_id
    assert later.content_id == first.content_id  # the same post, seen later
    assert later.item_version_id != first.item_version_id
    assert edited.content_id != first.content_id  # the post changed
    assert first.key == later.key == edited.key == (SOURCE_ID, "42")
    for field, value in (
        ("content_id", "itemc_" + "0" * 32),
        ("item_version_id", "itemv_" + "0" * 32),
    ):
        with pytest.raises(ValidationError, match="does not match"):
            SourceItem.model_validate({**first.model_dump(), field: value})
    with pytest.raises(ValidationError):
        first.title = "changed"  # type: ignore[misc]
    # A candidate is as deterministic as the item it reads.
    assert read("Hotfix 1.0", tags=["patchnotes"]) == read("Hotfix 1.0", tags=["patchnotes"])
    candidate = read("Hotfix 1.0", tags=["patchnotes"])
    with pytest.raises(ValidationError, match="candidate_id does not match"):
        EventCandidate.model_validate({**candidate.model_dump(), "title": "Hotfix 9.9"})


# --- canonical game resolution --------------------------------------------------------


def test_a_steam_app_resolves_only_through_the_catalogue_binding() -> None:
    assert RESOLVER(steam_app(730)) == BINDINGS[APP]
    assert RESOLVER(steam_app(424242)) is None
    assert RESOLVER(SourceGame(source="steam_catalogue", namespace="app", value="730")) is None
    assert RESOLVER(SourceGame(source=SOURCE_ID, namespace="package", value="730")) is None
    # The real recorded catalogue: only apps the catalogue itself confirms.
    records = cli._recorded(cli.DEFAULT_RECORDED_DIRECTORIES)
    state = generate_catalogue(records, as_of=NOW)
    bindings = steam_app_bindings(state, records)
    by_slug = {game.canonical_game_id: game.slug for game in state.catalogue.games}
    assert by_slug[bindings["730"].canonical_game_id] == "counter-strike-2"
    assert by_slug[bindings["570"].canonical_game_id] == "dota-2"
    assert bindings["730"].binding_ref is not None
    # GTA V's app is asserted by IGDB but absent from Steam's recorded list: not bound.
    assert "271590" not in bindings and "424242" not in bindings
    # Nothing in resolution reads a title.
    source = (REPO / "src/metaspike/event_sources/resolve.py").read_text()
    assert re.search(r"\.name\b|alias|normalise|slug|title\(", source.split('"""', 2)[2]) is None


def test_an_unresolved_app_is_kept_as_a_candidate_and_never_becomes_an_event(
    tmp_path: Path,
) -> None:
    title = "Hotfix 3.1"
    candidate = read(title, tags=["patchnotes"], bound=False)
    # Everything else about it would have been promoted.
    assert read(title, tags=["patchnotes"]).status is PromotionStatus.PROMOTED
    assert candidate.status is PromotionStatus.NOT_PROMOTED
    assert candidate.refusals == (Refusal.UNRESOLVED_GAME,)
    assert candidate.canonical_game_id is None and candidate.observation_id is None
    assert candidate.classified and candidate.proposed_event_type is EventType.MINOR_PATCH
    assert "not bound to a canonical game" in candidate.explanation
    stores = Stores(tmp_path)
    handler, _ = serving(
        {
            UNBOUND_APP: envelope([post("5", title, tags=["patchnotes"], app=424242)], app=424242),
            # A bound app's feed carrying a post that names an app the catalogue lacks.
            APP: envelope([post("6", title, tags=["patchnotes"], app=424242)]),
        }
    )
    report = stores.ingest(adapter_for(handler), (UNBOUND_APP, APP))
    assert report.candidates_created == 2 and report.candidates_promoted == 0
    assert report.refusals == {"unresolved_game": 2}
    assert {c.source_game.value for c in stores.sources.candidates()} == {"424242"}
    assert stores.events.observations() == () and stores.events.events() == ()
    # The unbound app gets no coverage. The bound one was checked, and has none of its own.
    assert [check.canonical_game_id for check in stores.events.checks()] == [GAME]
    assert [t.coverage_recorded for t in report.targets] == [False, True]
    assert stores.heat(NOW).event_heat == 0


# --- the classifier -------------------------------------------------------------------


def test_event_classification_rules_v1_are_pinned() -> None:
    assert RULES.version == RULES_VERSION == "event_classification_rules_v1"
    assert RULES.status == "internal_precision_first_unvalidated"
    assert [(rule.rule_id, rule.event_type) for rule in RULES.rules] == [
        ("patch_notes_hotfix", EventType.MINOR_PATCH),
        ("patch_notes_major_update", EventType.MAJOR_PATCH),
        ("season_start", EventType.SEASON),
        ("free_weekend", EventType.FREE_WEEKEND),
        ("expansion_release", EventType.EXPANSION),
        ("dlc_release", EventType.DLC),
    ]
    assert RULES.official_feed_names == (OFFICIAL,) and RULES.patch_tag == "patchnotes"
    assert RULES.max_days_ahead == 180
    for rule in RULES.rules:
        assert rule.evidence and rule.rationale and rule.title_phrases
        assert rule.publication_time_anchoring is True
        # Only a publisher's own patch-notes filing may anchor without the title saying "live".
        assert rule.anchoring_needs_live_phrase is (not rule.required_tags)
    fingerprint = hashlib.sha256(RULES.model_dump_json().encode()).hexdigest()
    assert fingerprint == PINNED_RULES_FINGERPRINT, fingerprint
    source = (REPO / "src/metaspike/event_sources/candidates.py").read_text()
    for forbidden in ("embedding", "openai", "anthropic", "difflib", "fuzz", "similar", "random"):
        assert forbidden not in source.lower().replace("similarity", ""), forbidden


PINNED_RULES_FINGERPRINT = "20103a107c30155018fa8e0971018f8811c1757c4a019a50c855c988cb841772"


def test_title_words_and_phrases_are_exact() -> None:
    assert words("Season 5 — Now LIVE!") == ("season", "5", "now", "live")
    assert words("Pokémon: Mise-à-jour") == ("pokemon", "mise", "a", "jour")
    tokens = words("The Hotfixed build, season five, seasons 5")
    assert not has_phrase(tokens, ("hotfix",))  # whole words only
    assert not has_phrase(
        tokens, ("season", "#")
    )  # "five" is not a number; "seasons" is not "season"
    assert has_phrase(words("season 12 begins"), ("season", "#"))
    assert has_phrase(words("it is now live"), ("now", "live"))
    assert not has_phrase(words("live now and then"), ("now", "live"))
    assert not has_phrase((), ("now",))


@pytest.mark.parametrize(
    ("title", "tags", "event_type", "rule", "category"),
    [
        ("Hotfix 1.4.2", ["patchnotes"], EventType.MINOR_PATCH, "patch_notes_hotfix", "tag"),
        ("Hot Fix for build 9", ["patchnotes"], EventType.MINOR_PATCH, "patch_notes_hotfix", "tag"),
        (
            "Major Update 3.0",
            ["patchnotes"],
            EventType.MAJOR_PATCH,
            "patch_notes_major_update",
            "tag",
        ),
        ("Season 5 is now live", None, EventType.SEASON, "season_start", "title"),
        ("Free Weekend - available now", None, EventType.FREE_WEEKEND, "free_weekend", "title"),
        (
            "The Frozen North expansion is out now",
            None,
            EventType.EXPANSION,
            "expansion_release",
            "title",
        ),
        ("New DLC now available", None, EventType.DLC, "dlc_release", "title"),
    ],
)
def test_each_rule_promotes_exactly_what_it_describes(
    title: str, tags: list[str] | None, event_type: EventType, rule: str, category: str
) -> None:
    candidate = read(title, tags=tags)
    assert candidate.status is PromotionStatus.PROMOTED and candidate.refusals == ()
    assert candidate.proposed_event_type is event_type
    assert candidate.classification_rule == rule and candidate.matched_rules == (rule,)
    assert candidate.evidence_category is (
        EvidenceCategory.STRUCTURED_TAG_AND_TITLE_PHRASE
        if category == "tag"
        else EvidenceCategory.EXACT_TITLE_PHRASE
    )
    # No date in the title: the start is the post's own time, and it says it is inferred.
    assert candidate.timing_basis is TimingBasis.PUBLICATION_TIME
    assert candidate.timing_is_explicit is False
    assert candidate.proposed_starts_at == PUBLISHED
    assert candidate.time_precision is TimePrecision.EXACT and candidate.proposed_ends_at is None
    assert candidate.canonical_game_id == GAME and candidate.binding_ref == "decision-1"
    assert candidate.mapping_version == 3 and candidate.rules_version == RULES_VERSION
    assert candidate.announced_at == PUBLISHED and candidate.observed_at == NOW
    assert candidate.observation_id is not None and candidate.observation_id.startswith("obs_")
    assert f"feed: {OFFICIAL}" in candidate.evidence
    assert any(line.startswith(f"rule {rule}:") for line in candidate.evidence)
    assert candidate.explanation.startswith(f"Promoted as {event_type.value} by {rule}")


@pytest.mark.parametrize(
    ("title", "tags", "feed", "refusal"),
    [
        # No type evidence.
        ("Thank you for an incredible year", None, OFFICIAL, Refusal.NO_TYPE_EVIDENCE),
        ("Weekly community spotlight", None, OFFICIAL, Refusal.NO_TYPE_EVIDENCE),
        ("Seasons change and so do we", None, OFFICIAL, Refusal.NO_TYPE_EVIDENCE),
        ("Season five is now live", None, OFFICIAL, Refusal.NO_TYPE_EVIDENCE),
        ("Hotfix 1.4.2", None, OFFICIAL, Refusal.NO_TYPE_EVIDENCE),  # no patch-notes tag
        ("Major Update 3.0", None, OFFICIAL, Refusal.NO_TYPE_EVIDENCE),
        # Filed as patch notes, size never stated: a patch of unknown size is not an event type.
        ("Release Notes", ["patchnotes"], OFFICIAL, Refusal.PATCH_SIZE_NOT_ESTABLISHED),
        ("Update 1.4.2", ["patchnotes"], OFFICIAL, Refusal.PATCH_SIZE_NOT_ESTABLISHED),
        ("Patch 7", ["patchnotes"], OFFICIAL, Refusal.PATCH_SIZE_NOT_ESTABLISHED),
        (
            "A major overhaul and an update",
            ["patchnotes"],
            OFFICIAL,
            Refusal.PATCH_SIZE_NOT_ESTABLISHED,
        ),
        # More than one type.
        ("Major Update 3.0 Hotfix", ["patchnotes"], OFFICIAL, Refusal.AMBIGUOUS_TYPE),
        ("Season 5 and the new expansion are now live", None, OFFICIAL, Refusal.AMBIGUOUS_TYPE),
        ("Free weekend and DLC out now", None, OFFICIAL, Refusal.AMBIGUOUS_TYPE),
        # Not the game's own feed, whatever the title says.
        ("Season 5 is now live", None, "synthetic_press", Refusal.NOT_AN_OFFICIAL_FEED),
        ("Hotfix 1.4.2", ["patchnotes"], "synthetic_press", Refusal.NOT_AN_OFFICIAL_FEED),
        ("Season 5 is now live", None, None, Refusal.NOT_AN_OFFICIAL_FEED),
        (
            "Season 5 is now live",
            None,
            "Steam_Community_Announcements",
            Refusal.NOT_AN_OFFICIAL_FEED,
        ),
        # Commentary, promotion, an ending or a delay.
        ("Season 5 trailer out now", None, OFFICIAL, Refusal.TITLE_NOT_ABOUT_AN_OCCURRENCE),
        ("Season 5 roadmap", None, OFFICIAL, Refusal.TITLE_NOT_ABOUT_AN_OCCURRENCE),
        ("Season 5 ends - last chance", None, OFFICIAL, Refusal.TITLE_NOT_ABOUT_AN_OCCURRENCE),
        ("Season 5 recap", None, OFFICIAL, Refusal.TITLE_NOT_ABOUT_AN_OCCURRENCE),
        ("The expansion is delayed", None, OFFICIAL, Refusal.TITLE_NOT_ABOUT_AN_OCCURRENCE),
        ("DLC sale: 50% off, out now", None, OFFICIAL, Refusal.TITLE_NOT_ABOUT_AN_OCCURRENCE),
        ("Free weekend is over", None, OFFICIAL, Refusal.TITLE_NOT_ABOUT_AN_OCCURRENCE),
        ("Hotfix postponed", ["patchnotes"], OFFICIAL, Refusal.TITLE_NOT_ABOUT_AN_OCCURRENCE),
        (
            "Expansion soundtrack now available",
            None,
            OFFICIAL,
            Refusal.TITLE_NOT_ABOUT_AN_OCCURRENCE,
        ),
        ("Developer diary: Season 5", None, OFFICIAL, Refusal.TITLE_NOT_ABOUT_AN_OCCURRENCE),
    ],
)
def test_without_an_established_type_there_is_no_event(
    title: str, tags: list[str] | None, feed: str | None, refusal: Refusal
) -> None:
    candidate = read(title, tags=tags, feed=feed)
    assert candidate.status is PromotionStatus.NOT_PROMOTED
    assert candidate.refusals == (refusal,)
    assert candidate.proposed_event_type is None and candidate.classification_rule is None
    assert not candidate.classified
    # No type: the timing question is never even asked, and no date is made up.
    assert candidate.proposed_starts_at is None and candidate.timing_basis is TimingBasis.NONE
    assert candidate.timing_is_explicit is None and candidate.time_precision is None
    assert candidate.observation_id is None
    assert candidate.explanation.startswith("Not promoted: ")
    # A publisher's tag is recorded as evidence even where it settles nothing.
    if feed == OFFICIAL and tags:
        assert candidate.evidence_category is EvidenceCategory.STRUCTURED_TAG_ONLY
        assert "tag: patchnotes" in candidate.evidence
    else:
        assert candidate.evidence_category is EvidenceCategory.NONE


@pytest.mark.parametrize(
    ("title", "tags", "event_type", "refusal"),
    [
        # A recognisable announcement with no safe date: a publication time is not an event time.
        ("Season 5", None, EventType.SEASON, Refusal.NO_TIMING_EVIDENCE),
        ("Season 5 patch", None, EventType.SEASON, Refusal.NO_TIMING_EVIDENCE),
        ("Introducing Season 5", None, EventType.SEASON, Refusal.NO_TIMING_EVIDENCE),
        ("Free weekend", None, EventType.FREE_WEEKEND, Refusal.NO_TIMING_EVIDENCE),
        ("The Frozen North expansion", None, EventType.EXPANSION, Refusal.NO_TIMING_EVIDENCE),
        ("About our DLC plans", None, EventType.DLC, Refusal.NO_TIMING_EVIDENCE),
        # The title says it has not happened yet, and gives no date.
        ("Season 5 coming soon", None, EventType.SEASON, Refusal.TITLE_SAYS_NOT_YET),
        ("Season 5 arrives next week", None, EventType.SEASON, Refusal.TITLE_SAYS_NOT_YET),
        (
            "Announcing the expansion - out now on other platforms",
            None,
            EventType.EXPANSION,
            Refusal.TITLE_SAYS_NOT_YET,
        ),
        (
            "Hotfix coming tomorrow",
            ["patchnotes"],
            EventType.MINOR_PATCH,
            Refusal.TITLE_SAYS_NOT_YET,
        ),
        ("Major Update preview", ["patchnotes"], EventType.MAJOR_PATCH, Refusal.TITLE_SAYS_NOT_YET),
        ("Free weekend will be live", None, EventType.FREE_WEEKEND, Refusal.TITLE_SAYS_NOT_YET),
        # A date that cannot be read with certainty.
        ("Season 5 starts October 20", None, EventType.SEASON, Refusal.AMBIGUOUS_DATE),
        ("Season 5 starts 20 Oct", None, EventType.SEASON, Refusal.AMBIGUOUS_DATE),
        ("Season 5 starts 10/11/2026", None, EventType.SEASON, Refusal.AMBIGUOUS_DATE),
        ("Season 5 starts 10/11", None, EventType.SEASON, Refusal.AMBIGUOUS_DATE),
        ("Season 5 starts February 30, 2027", None, EventType.SEASON, Refusal.AMBIGUOUS_DATE),
        ("Season 5 starts 2026-13-01", None, EventType.SEASON, Refusal.AMBIGUOUS_DATE),
        (
            "Season 5 starts October 20, 2026 or October 27, 2026",
            None,
            EventType.SEASON,
            Refusal.AMBIGUOUS_DATE,
        ),
        ("Season 5 is now live - Oct 20", None, EventType.SEASON, Refusal.AMBIGUOUS_DATE),
        # A full date whose role is not clearly "start".
        ("Season 5 - October 20, 2026", None, EventType.SEASON, Refusal.DATE_ROLE_UNCLEAR),
        (
            "Free weekend until October 20, 2026",
            None,
            EventType.FREE_WEEKEND,
            Refusal.DATE_ROLE_UNCLEAR,
        ),
        (
            "Free weekend live through October 20, 2026",
            None,
            EventType.FREE_WEEKEND,
            Refusal.DATE_ROLE_UNCLEAR,
        ),
        ("Hotfix 2026-10-20", ["patchnotes"], EventType.MINOR_PATCH, Refusal.DATE_ROLE_UNCLEAR),
        # A full, readable start date that cannot be right.
        (
            "Season 5 starts October 9, 2026",
            None,
            EventType.SEASON,
            Refusal.DATE_BEFORE_PUBLICATION,
        ),
        (
            "Season 5 starts October 20, 2025",
            None,
            EventType.SEASON,
            Refusal.DATE_BEFORE_PUBLICATION,
        ),
        ("Season 5 starts April 9, 2027", None, EventType.SEASON, Refusal.DATE_TOO_FAR_AHEAD),
    ],
)
def test_without_established_timing_there_is_no_event(
    title: str, tags: list[str] | None, event_type: EventType, refusal: Refusal
) -> None:
    candidate = read(title, tags=tags)
    assert candidate.status is PromotionStatus.NOT_PROMOTED
    assert candidate.refusals == (refusal,)
    # The type was recognised and is recorded; it is still only a candidate.
    assert candidate.classified and candidate.proposed_event_type is event_type
    assert candidate.classification_rule is not None
    assert candidate.proposed_starts_at is None and candidate.timing_basis is TimingBasis.NONE
    assert candidate.observation_id is None
    assert candidate.explanation.startswith("Not promoted, though ")
    with pytest.raises(ValueError, match="only a promoted candidate"):
        promoted_observation(candidate, item_of(title, tags=tags), RULES)


def test_an_explicit_date_is_read_only_in_three_unambiguous_forms() -> None:
    twentieth = date(2026, 10, 20)
    for title in (
        "starts 2026-10-20",
        "starts October 20, 2026",
        "starts October 20 2026",
        "starts Oct 20, 2026",
        "starts Oct. 20th, 2026",
        "starts 20 October 2026",
        "starts 20th Oct 2026",
        "STARTS OCTOBER 20, 2026",
    ):
        found = dates_in(title)
        assert found.dates == (twentieth,) and not found.unreadable, title
    assert dates_in("Sept 3, 2026").dates == (date(2026, 9, 3),)
    assert dates_in("May 1, 2027 and May 1, 2027").dates == (date(2027, 5, 1),)  # one date, twice
    for title in (
        "Hotfix 1.4.2",
        "Build 2026.10 notes",
        "Version 10.20.2026",
        "1,000,000 players",
        "Season 5",
    ):
        found = dates_in(title)
        assert found.dates == () and not found.unreadable, title
    for title in (
        "October 20",
        "20 October",
        "20th of October",
        "Oct 20th",
        "10/20/2026",
        "20/10/2026",
        "10/20",
        "2026/10/20",
        "February 30, 2027",
        "2026-02-30",
        "2026-00-10",
        "October 20, 2026 - October 27",
    ):
        assert dates_in(title).unreadable, title
    assert dates_in("October 20, 2026 to October 27, 2026").dates == (twentieth, date(2026, 10, 27))
    # "May" the month is only a date beside a day and a year.
    assert dates_in("You may now play").dates == () and not dates_in("You may now play").unreadable


def test_an_explicit_start_date_promotes_with_day_precision_and_its_boundaries() -> None:
    candidate = read("Season 5 starts October 20, 2026")
    assert candidate.status is PromotionStatus.PROMOTED
    assert candidate.timing_basis is TimingBasis.EXPLICIT_DATE_IN_TITLE
    assert candidate.timing_is_explicit is True
    assert candidate.proposed_starts_at == datetime(2026, 10, 20, tzinfo=UTC)
    assert candidate.time_precision is TimePrecision.DAY
    assert "date in title: 2026-10-20" in candidate.evidence
    assert "an explicit date in the title" in candidate.explanation
    # A date wins over wording about the future: "coming" with a date is a dated event.
    assert read("Season 5 arrives October 20, 2026").status is PromotionStatus.PROMOTED
    assert read("Hotfix live 2026-10-12", tags=["patchnotes"]).status is PromotionStatus.PROMOTED
    # Published 10 October 09:00 UTC. The day of publication is allowed; the day before is not.
    assert read("Season 5 starts October 10, 2026").proposed_starts_at == datetime(
        2026, 10, 10, tzinfo=UTC
    )
    assert read("Season 5 starts October 9, 2026").refusals == (Refusal.DATE_BEFORE_PUBLICATION,)
    # 180 days ahead is believed; 181 is not.
    limit = date(2026, 10, 10) + timedelta(days=180)
    assert limit == date(2027, 4, 8)
    assert read("Season 5 starts April 8, 2027").status is PromotionStatus.PROMOTED
    assert read("Season 5 starts April 9, 2027").refusals == (Refusal.DATE_TOO_FAR_AHEAD,)
    # "Day of publication" is the UTC day, whatever offset the timestamp carries.
    late = datetime(2026, 10, 10, 23, 59, 59, tzinfo=UTC)
    assert (
        read("Season 5 starts October 10, 2026", published=late).status is PromotionStatus.PROMOTED
    )
    assert read(
        "Season 5 starts October 10, 2026", published=late + timedelta(seconds=1)
    ).refusals == (Refusal.DATE_BEFORE_PUBLICATION,)


def test_publication_time_anchors_an_event_only_where_a_rule_allows_it() -> None:
    # Title rules need the title itself to say the thing is live.
    for phrase in (
        "is now live",
        "is live",
        "out now",
        "available now",
        "now available",
        "has begun",
        "has started",
        "starts now",
        "begins now",
        "live now",
    ):
        assert read(f"Season 5 {phrase}").timing_basis is TimingBasis.PUBLICATION_TIME, phrase
    for near_miss in ("goes live", "lives", "is alive now", "now", "live", "live and now"):
        candidate = read(f"Season 5 {near_miss}")
        assert candidate.status is PromotionStatus.NOT_PROMOTED, near_miss
    # "now, live" does read as "now live" once punctuation is dropped; that is the rule.
    assert read("Season 5: now, live").status is PromotionStatus.PROMOTED
    # A publisher's patch-notes filing anchors without the phrase...
    hotfix = read("Hotfix 1.4.2", tags=["patchnotes"])
    assert hotfix.timing_basis is TimingBasis.PUBLICATION_TIME
    assert "publication time, allowed by the rule" in hotfix.evidence
    # ...and only with the tag: the same title without it is not even a type.
    assert read("Hotfix 1.4.2").refusals == (Refusal.NO_TYPE_EVIDENCE,)
    # The anchor is exactly the post's own time, to the second.
    odd = datetime(2026, 10, 3, 17, 45, 13, tzinfo=UTC)
    assert read("Season 5 is now live", published=odd).proposed_starts_at == odd
    # A rule that forbade anchoring would time nothing from a publication time.
    strict = RULES.model_copy(
        update={
            "rules": tuple(
                rule.model_copy(update={"publication_time_anchoring": False})
                for rule in RULES.rules
            )
        }
    )
    for title, tags in (("Season 5 is now live", None), ("Hotfix 1.4.2", ["patchnotes"])):
        refused = classify(
            item_of(title, tags=tags), BINDINGS[APP], source_cleared=True, rules=strict
        )
        assert refused.refusals == (Refusal.NO_TIMING_EVIDENCE,)
    assert (
        classify(
            item_of("Season 5 starts October 20, 2026"),
            BINDINGS[APP],
            source_cleared=True,
            rules=strict,
        ).status
        is PromotionStatus.PROMOTED
    )


def test_a_source_that_is_not_cleared_promotes_nothing() -> None:
    candidate = read("Hotfix 1.4.2", tags=["patchnotes"], cleared=False)
    assert candidate.refusals == (Refusal.SOURCE_NOT_CLEARED,)
    assert candidate.status is PromotionStatus.NOT_PROMOTED and candidate.observation_id is None
    both = read("Hotfix 1.4.2", tags=["patchnotes"], cleared=False, bound=False)
    assert both.refusals == (Refusal.UNRESOLVED_GAME, Refusal.SOURCE_NOT_CLEARED)
    invalid_states: tuple[dict[str, Any], ...] = (
        {"status": "promoted"},  # refusals still present
        {"refusals": []},  # not promoted, yet nothing refuses
    )
    for invalid in invalid_states:
        with pytest.raises(ValidationError):
            EventCandidate.model_validate({**candidate.model_dump(), **invalid})


def test_a_promoted_candidate_becomes_exactly_one_observation_through_the_resolver() -> None:
    item = item_of("Season 5 is now live")
    candidate = classify(item, BINDINGS[APP], source_cleared=True)
    observation = promoted_observation(candidate, item, RULES)
    assert observation.observation_id == candidate.observation_id
    assert observation.canonical_game_id == GAME and observation.source == SOURCE_ID
    assert (
        observation.event_type is EventType.SEASON and observation.status is EventStatus.SCHEDULED
    )
    assert observation.starts_at == PUBLISHED and observation.ends_at is None
    assert observation.time_precision is TimePrecision.EXACT
    assert observation.observed_at == NOW
    # Provenance the event model already has room for: the source's id and reference.
    assert observation.source_event_id == "42"
    assert observation.reference == "https://example.invalid/news/42"
    # The rule and the timing basis stay with the candidate, linked by the observation id.
    assert candidate.classification_rule == "season_start"
    assert set(observation.model_dump()) == {
        "observation_id", "canonical_game_id", "source", "observed_at", "event_type", "title",
        "starts_at", "ends_at", "time_precision", "status", "source_event_id", "reference",
    }  # fmt: skip
    with pytest.raises(ValueError, match="not made from this item"):
        promoted_observation(candidate, item_of("Season 5 is now live", observed=NOW + DAY), RULES)


# --- storage --------------------------------------------------------------------------


def test_the_source_store_is_append_only_idempotent_and_content_addressed(tmp_path: Path) -> None:
    handler, seen = serving(
        {APP: envelope([post("1", "Hotfix 1.0", tags=["patchnotes"]), post("2", "Hello")])}
    )
    stores = Stores(tmp_path)
    first = stores.ingest(adapter_for(handler))
    assert (first.new_post_versions, first.candidates_created, first.observations_appended) == (
        2,
        2,
        1,
    )
    assert first.coverage_checks_recorded == 1
    root = tmp_path / "event-sources"
    assert [path.name for path in root.iterdir()] == [STORE_GENERATION]
    files = sorted(path for path in root.rglob("*") if path.is_file())
    assert {path.relative_to(root / STORE_GENERATION).parts[0] for path in files} == {
        "fetches", "items", "candidates",
    }  # fmt: skip
    for path in files:
        assert path.parent.name == "2026-10-11"  # by observed day
        assert path.stem == hashlib.sha256(path.read_bytes()).hexdigest()[:32]
    before = {path: path.read_bytes() for path in files}
    events_before = sorted(p for p in (tmp_path / "events").rglob("*") if p.is_file())

    # The same fetch again, at the same moment: nothing new anywhere.
    again = stores.ingest(adapter_for(handler))
    assert (again.posts_observed, again.new_post_versions, again.candidates_created) == (2, 0, 0)
    assert (again.observations_appended, again.coverage_checks_recorded) == (0, 0)
    assert sorted(path for path in root.rglob("*") if path.is_file()) == files
    assert sorted(p for p in (tmp_path / "events").rglob("*") if p.is_file()) == events_before

    # The same posts fetched a day later: a new fetch and a new coverage check, no new post.
    later = stores.ingest(adapter_for(handler, Ticking(NOW + DAY)))
    assert (later.new_post_versions, later.candidates_created, later.observations_appended) == (
        0,
        0,
        0,
    )
    assert later.coverage_checks_recorded == 1
    assert len(stores.sources.fetches()) == 2 and len(stores.sources.items()) == 2
    assert len(stores.events.observations()) == 1 and len(stores.events.checks()) == 2
    assert all(path.read_bytes() == body for path, body in before.items())  # nothing rewritten
    assert len(seen) == 3
    # A fetch record holds no post, only ids and the shape of the response.
    record = stores.sources.fetches()[0]
    assert record.items_returned == 2 and len(record.item_version_ids) == 2
    assert record.canonical_game_id == GAME and record.parameters["appid"] == 730
    assert "Hotfix" not in record.model_dump_json()
    # Reopened from disk, it is the same store.
    reopened = EventSourceStore(root)
    assert reopened.items() == stores.sources.items()
    assert reopened.candidates() == stores.sources.candidates()


def test_storage_is_policy_aware(tmp_path: Path) -> None:
    item = item_of("Hotfix 1.0", tags=["patchnotes"])
    store = EventSourceStore(tmp_path)
    assert storage_refusal(item, STEAM_NEWS_POLICY) is None
    # An item carrying a body is refused whole, not trimmed.
    with_body = item.model_copy(update={"summary": "the publisher's text"})
    assert storage_refusal(with_body, STEAM_NEWS_POLICY) == (
        "policy does not permit keeping the source's 'contents' field"
    )
    with pytest.raises(StoragePolicyError, match="contents"):
        store.add_items([item, with_body], STEAM_NEWS_POLICY)
    assert store.items() == ()  # nothing of the batch was kept
    # Another source's policy does not cover this item.
    assert "policy is for" in (storage_refusal(item, STEAM_CATALOGUE_POLICY) or "")
    # Were storage of history unanswered, nothing textual could be kept at all.
    unknown = STEAM_NEWS_POLICY.model_copy(
        update={"historical_storage_allowed": Permission.UNKNOWN}
    )
    with pytest.raises(StoragePolicyError, match="title"):
        store.add_items([item], unknown)
    assert store.add_items([item], STEAM_NEWS_POLICY) == (item,)


def test_an_edited_post_is_a_new_version_and_revises_the_event_it_became(tmp_path: Path) -> None:
    stores = Stores(tmp_path)
    clock = Ticking()
    feed: dict[str, Any] = {APP: envelope([post("77", "Season 5 starts October 20, 2026")])}
    handler, _ = serving(feed)
    adapter = adapter_for(handler, clock)
    stores.ingest(adapter)
    (event,) = stores.events.events()
    assert event.start_day == date(2026, 10, 20)

    # The publisher moves the date. Same post id, new content.
    clock.moment = NOW + DAY
    feed[APP] = envelope([post("77", "Season 5 starts October 27, 2026")])
    moved = stores.ingest(adapter)
    assert (moved.new_post_versions, moved.candidates_created, moved.observations_appended) == (
        1,
        1,
        1,
    )
    versions = stores.sources.versions_of(SOURCE_ID, "77")
    assert [v.title for v in versions] == [
        "Season 5 starts October 20, 2026",
        "Season 5 starts October 27, 2026",
    ]
    assert len(stores.sources.candidates()) == 2 and len(stores.sources.latest_candidates()) == 1
    (revised,) = stores.events.events()  # one event, revised: the source's id carries it
    assert revised.event_id == event.event_id and revised.start_day == date(2026, 10, 27)
    assert [r.matched_by.value for r in revised.revisions] == ["new_event", "same_source_native_id"]

    # Then the post is rewritten into something that is no longer an event at all.
    clock.moment = NOW + 2 * DAY
    feed[APP] = envelope([post("77", "Season 5 is delayed")])
    gone = stores.ingest(adapter)
    assert (gone.candidates_promoted, gone.observations_appended, gone.withdrawals_appended) == (
        0,
        0,
        1,
    )
    (withdrawn,) = stores.events.events()
    assert withdrawn.event_id == event.event_id and withdrawn.status is EventStatus.WITHDRAWN
    assert len(stores.sources.versions_of(SOURCE_ID, "77")) == 3
    assert len(stores.events.observations()) == 3  # nothing was removed to do it

    # Point in time: each moment still sees exactly what was known then.
    assert stores.events.events(NOW)[0].start_day == date(2026, 10, 20)
    assert stores.events.events(NOW)[0].status is EventStatus.SCHEDULED
    assert stores.events.events(NOW + DAY)[0].start_day == date(2026, 10, 27)
    assert [c.title for c in stores.sources.latest_candidates(NOW)] == [
        "Season 5 starts October 20, 2026"
    ]
    assert stores.sources.latest_items(NOW + DAY)[0].title == "Season 5 starts October 27, 2026"
    assert stores.sources.items(NOW - HOUR) == () and stores.sources.candidates(NOW - HOUR) == ()
    assert stores.sources.fetches(NOW - HOUR) == ()
    # A post that goes back to its first wording is a version again, not a silent no-op.
    clock.moment = NOW + 3 * DAY
    feed[APP] = envelope([post("77", "Season 5 starts October 20, 2026")])
    back = stores.ingest(adapter)
    assert back.new_post_versions == 1 and len(stores.sources.versions_of(SOURCE_ID, "77")) == 4
    assert stores.events.events()[0].status is EventStatus.SCHEDULED


# --- coverage -------------------------------------------------------------------------


def test_a_successful_check_is_coverage_whatever_it_found(tmp_path: Path) -> None:
    cases = {
        "no posts at all": envelope([]),
        "posts, none of which classify": envelope(
            [post("1", "Thank you all"), post("2", "Weekly notes")]
        ),
        "candidates that classify and cannot be promoted": envelope(
            [post("3", "Season 5 coming soon"), post("4", "Release Notes", tags=["patchnotes"])]
        ),
        "only entries that could not be read": envelope(["junk", post("", "no id")]),
    }
    for index, (name, body) in enumerate(cases.items()):
        stores = Stores(tmp_path / str(index))
        handler, _ = serving({APP: body})
        report = stores.ingest(adapter_for(handler))
        assert report.fetches_ok == 1 and report.candidates_promoted == 0, name
        assert report.coverage_checks_recorded == 1, name
        (check,) = stores.events.checks()
        assert (check.canonical_game_id, check.source, check.checked_at) == (GAME, SOURCE_ID, NOW)
        assert stores.events.observations() == (), name
        # Checked, nothing relevant: available with a heat of exactly 0. Not unavailable.
        intelligence = stores.heat(NOW)
        assert intelligence.availability is Availability.AVAILABLE, name
        assert intelligence.event_heat == 0 and intelligence.contributions == (), name
        assert intelligence.coverage_sources == (SOURCE_ID,)
    # A game nobody asked about stays unavailable: no number at all.
    assert stores.heat(NOW, OTHER).availability is Availability.UNAVAILABLE
    assert stores.heat(NOW, OTHER).event_heat is None


def test_a_failed_check_is_not_coverage(tmp_path: Path) -> None:
    failures: dict[str, Any] = {
        "http error": (503, {}),
        "refused": (403, {}),
        "bad envelope": {"nothing": "useful"},
        "network": httpx.ConnectError("down"),
    }
    for index, (name, payload) in enumerate(failures.items()):
        stores = Stores(tmp_path / str(index))
        handler, _ = serving({APP: payload})
        report = stores.ingest(adapter_for(handler))
        assert (report.fetches_ok, report.fetches_failed) == (0, 1), name
        assert report.coverage_checks_recorded == 0 and stores.events.checks() == (), name
        assert report.targets[0].coverage_recorded is False
        # The failure is on record in the source store; event intelligence knows nothing.
        (record,) = stores.sources.fetches()
        assert record.status is FetchStatus.FAILED and record.error_code is not None
        intelligence = stores.heat(NOW)
        assert intelligence.availability is Availability.UNAVAILABLE, name
        assert intelligence.event_heat is None  # unavailable, never zero
    # One game failing does not cost another its coverage, or lend it any.
    stores = Stores(tmp_path / "mixed")
    handler, _ = serving({APP: (500, {}), OTHER_APP: envelope([], app=570)})
    stores.ingest(adapter_for(handler), (APP, OTHER_APP))
    assert [check.canonical_game_id for check in stores.events.checks()] == [OTHER]


# --- what reaches Event Heat ----------------------------------------------------------


def test_only_promoted_candidates_reach_event_heat(tmp_path: Path) -> None:
    refused_only = envelope(
        [
            post("1", "Season 5 coming soon"),
            post("2", "Release Notes", tags=["patchnotes"]),
            post("3", "Season 9 is now live", feed="synthetic_press"),
            post("4", "Major Update 3.0 Hotfix", tags=["patchnotes"]),
            post("5", "Free weekend until October 20, 2026"),
        ]
    )
    stores = Stores(tmp_path / "refused")
    handler, _ = serving({APP: refused_only})
    report = stores.ingest(adapter_for(handler))
    assert report.candidates_created == 5 and report.candidates_promoted == 0
    assert report.candidates_classified == 2
    assert stores.events.observations() == ()
    assert stores.heat(NOW).event_heat == 0 and stores.heat(NOW).contributions == ()
    # However many refused candidates there are, the heat does not move.
    assert stores.heat(NOW + HOUR).event_heat == 0

    # The same posts plus one that passes the gate: heat appears, from that one alone.
    promoted = stores_with = Stores(tmp_path / "promoted")
    handler, _ = serving(
        {APP: envelope([*refused_only["appnews"]["newsitems"], post("6", "Season 5 is now live")])}
    )
    report = stores_with.ingest(adapter_for(handler))
    assert (report.candidates_created, report.candidates_promoted) == (6, 1)
    assert report.promoted_event_types == {"season": 1}
    intelligence = promoted.heat(NOW)
    assert intelligence.event_heat is not None and intelligence.event_heat > 0
    counted = [c for c in intelligence.contributions if c.counted]
    assert len(counted) == 1
    (event,) = stores_with.events.events()
    assert counted[0].evaluation.event_id == event.event_id
    assert event.source_event_ids == ("6",) and event.sources == (SOURCE_ID,)
    # The event store, which is all Event Heat reads, holds nothing of the other five.
    stored = " ".join(o.model_dump_json() for o in stores_with.events.observations())
    for title in ("coming soon", "Release Notes", "Season 9", "Major Update", "Free weekend"):
        assert title not in stored
    # And the event engine does not import the source store or the candidates.
    for module in (REPO / "src/metaspike/events").glob("*.py"):
        assert "event_sources" not in module.read_text(), module.name


def test_the_event_packages_are_kept_apart_and_heat_rules_are_untouched() -> None:
    rules = (REPO / "src/metaspike/events/rules.py").read_bytes()
    assert hashlib.sha256(rules).hexdigest().startswith("7358ea6563ddcad9")
    pinned = json.dumps(EVENT_HEAT_RULES_V1.model_dump(mode="json"), sort_keys=True).encode()
    assert hashlib.sha256(pinned).hexdigest() == (
        "0f3a11ee44d64ba48e4906b70886d3cb6738da4192d47e0a9f84223575c9717e"
    )
    for package in ("scoring", "features"):
        for module in (REPO / "src/metaspike" / package).glob("*.py"):
            text = module.read_text()
            assert "event_sources" not in text and "metaspike.events" not in text, module.name
    # No adapter classifies, scores or resolves a canonical game.
    adapter = Path(steam_news_module.__file__).read_text()
    for forbidden in ("EventType", "classify", "canonical_game_id", "heat", "Catalogue"):
        assert forbidden not in adapter.split('"""', 2)[2], forbidden


# --- history and point in time --------------------------------------------------------


def test_a_bounded_backfill_walks_back_with_the_documented_parameter(tmp_path: Path) -> None:
    oldest_first_page = datetime(2026, 9, 24, 9, tzinfo=UTC)
    oldest_second_page = datetime(2026, 9, 5, 18, tzinfo=UTC)
    requests: list[httpx.Request] = []
    replay = replay_news_client(FIXTURES)
    inner = replay._transport

    def recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return inner.handle_request(request)

    pool = LimiterPool(clock=Clock(sleeper=lambda _: None))
    client = httpx.Client(transport=httpx.MockTransport(recording))
    adapter = build_steam_news(client, pool, max_requests=5, now=Ticking())
    pages = list(adapter.backfill(APP, max_requests=5))
    # Three requests, then it stops by itself: the third page is empty.
    assert [len(page.items) for page in pages] == [4, 3, 0]
    assert [request.url.params.get("enddate") for request in requests] == [
        None,
        str(stamp(oldest_first_page)),
        str(stamp(oldest_second_page)),
    ]
    assert all(page.succeeded for page in pages)
    # The allowance is a ceiling on requests, not a target.
    requests.clear()
    assert len(list(adapter.backfill(APP, max_requests=1))) == 1 and len(requests) == 1
    with pytest.raises(ValueError, match="at least 1"):
        list(adapter.backfill(APP, max_requests=0))
    # A page that makes no progress ends the walk instead of looping.
    stuck, seen = serving({APP: envelope([post("1", "Same page forever")])})
    looping = list(adapter_for(stuck).backfill(APP, max_requests=10))
    assert len(looping) == 2 and len(seen) == 2
    # A failure ends it too, and is reported as a failure.
    broken, _ = serving({APP: (500, {})})
    assert [p.status for p in adapter_for(broken).backfill(APP, max_requests=4)] == [
        FetchStatus.FAILED
    ]

    # Ingested, a backfill keeps every post with BOTH times: when it was published and
    # when it was actually seen. Seeing it now does not make it known then.
    stores = Stores(tmp_path)
    report = stores.ingest(
        build_steam_news(client, pool, max_requests=5, now=Ticking()), backfill_requests=5
    )
    assert (report.requests, report.posts_observed, report.candidates_created) == (3, 7, 7)
    assert report.candidates_promoted == 4
    assert report.promoted_event_types == {"major_patch": 1, "minor_patch": 1, "season": 2}
    assert report.refusals == {
        "not_an_official_feed": 1,
        "patch_size_not_established": 1,
        "title_not_about_an_occurrence": 1,
    }
    assert all(item.observed_at == NOW for item in stores.sources.items())
    assert min(item.published_at for item in stores.sources.items()) == oldest_second_page
    before_it_was_seen = datetime(2026, 10, 5, tzinfo=UTC)
    assert stores.events.events(before_it_was_seen) == ()
    assert stores.heat(before_it_was_seen).availability is Availability.UNAVAILABLE


def test_a_later_candidate_or_revision_cannot_rewrite_an_earlier_event_snapshot(
    tmp_path: Path,
) -> None:
    stores = Stores(tmp_path)
    clock = Ticking(NOW)
    feed: dict[str, Any] = {APP: envelope([post("10", "Season 5 starts October 14, 2026")])}
    handler, _ = serving(feed)
    adapter = adapter_for(handler, clock)
    stores.ingest(adapter)
    at_first = NOW + HOUR
    snapshot = stores.heat(at_first)
    assert snapshot.event_heat is not None and snapshot.event_heat > 0
    frozen = snapshot.model_dump_json()

    # Later: the post is revised, a second event appears, and a third post is refused.
    clock.moment = NOW + 2 * DAY
    feed[APP] = envelope(
        [
            post("10", "Season 5 starts October 16, 2026"),
            post("11", "Major Update 4.0", tags=["patchnotes"], published=NOW + DAY),
            post("12", "Expansion coming soon", published=NOW + DAY),
        ]
    )
    later = stores.ingest(adapter)
    assert (later.candidates_created, later.candidates_promoted) == (3, 2)
    # Asked again as of the earlier moment, the answer is byte-for-byte what it was.
    assert stores.heat(at_first).model_dump_json() == frozen
    assert len(stores.events.events(at_first)) == 1
    assert stores.events.events(at_first)[0].start_day == date(2026, 10, 14)
    # Asked as of now, it reflects the revision and the new event.
    now_known = stores.events.events(clock.moment)
    assert sorted(event.event_type.value for event in now_known) == ["major_patch", "season"]
    assert stores.heat(clock.moment).model_dump_json() != frozen
    summary_then = summarise(stores.sources, STEAM_NEWS_POLICY, RULES_VERSION, at_first)
    assert (summary_then.posts_observed, summary_then.candidates_promoted) == (1, 1)
    assert summary_then.fetches_after_as_of == 1


def test_the_backtest_can_read_an_ingested_event_store_and_score_v0_does_not_change(
    tmp_path: Path,
) -> None:
    from tests.conftest import steady
    from tests.test_historical_validation import WHEN, ZERO, recording
    from tests.test_historical_validation import day as history_day

    catalogue, report = recording({"alpha": steady(80), "beta": [v * 2 for v in steady(80) if v]})
    alpha = catalogue.games[0].canonical_game_id
    resolver = steam_news_resolver({APP: GameBinding(canonical_game_id=alpha)})
    events = EventStore(tmp_path / "events")
    seen_at = datetime.combine(history_day(50), datetime.min.time(), tzinfo=UTC) + 6 * HOUR
    handler, _ = serving(
        {APP: envelope([post("1", "Season 2 is now live", published=seen_at - HOUR)])}
    )
    ingest(
        adapter_for(handler, Ticking(seen_at)),
        STEAM_NEWS_POLICY,
        [APP],
        resolver,
        EventSourceStore(tmp_path / "sources"),
        events,
    )
    from metaspike.validation.backtest import run_backtest
    from metaspike.validation.metrics import prediction_day

    plain = run_backtest(catalogue, report, history_day(40), history_day(60), WHEN, ZERO)
    eventful = run_backtest(
        catalogue, report, history_day(40), history_day(60), WHEN, ZERO, event_store=events
    )
    by_day = {(prediction_day(s), s.slug): s for s in eventful.snapshots}
    # Before it was seen: unavailable. From the first prediction after: available, with heat.
    assert by_day[(history_day(50), "alpha")].events.availability == "unavailable"
    after = by_day[(history_day(51), "alpha")].events
    assert (
        after.availability == "available" and after.event_heat is not None and after.event_heat > 0
    )
    assert by_day[(history_day(51), "beta")].events.availability == "unavailable"
    # Score v0, rank and confidence are identical with and without the event store.
    for without, with_events in zip(plain.snapshots, eventful.snapshots, strict=True):
        assert without.score == with_events.score and without.rank == with_events.rank
        assert without.confidence == with_events.confidence
        assert without.features == with_events.features
    # A handful of records proves the wiring and nothing else.
    assert eventful.report.evidence.status.value == "framework_only"
    assert eventful.report.events.real_event_history is True
    assert eventful.report.events.snapshots_with_event_intelligence > 0


# --- summaries, artifacts and the command --------------------------------------------


def test_a_summary_counts_announcements_and_events_separately(tmp_path: Path) -> None:
    stores = Stores(tmp_path)
    empty = summarise(stores.sources, STEAM_NEWS_POLICY, RULES_VERSION, NOW)
    assert empty.collected is False and empty.games == () and empty.posts_observed == 0
    handler, _ = serving(
        {
            APP: envelope(
                [
                    post("1", "Hotfix 1.0", tags=["patchnotes"]),
                    post("2", "Season 5 coming soon"),
                    post("3", "Thanks everyone"),
                    post("4", "Hotfix 0.1", tags=["patchnotes"], app=424242),
                ]
            ),
            OTHER_APP: (503, {}),
        }
    )
    stores.ingest(adapter_for(handler), (APP, OTHER_APP))
    summary = summarise(stores.sources, STEAM_NEWS_POLICY, RULES_VERSION, NOW)
    assert summary.collected is True
    assert (summary.fetches_ok, summary.fetches_failed, summary.fetches_not_sent) == (1, 1, 0)
    assert summary.games_checked == 1 and summary.last_success_at == NOW
    assert (summary.posts_observed, summary.candidates) == (4, 4)
    assert (summary.candidates_classified, summary.candidates_promoted) == (3, 1)
    assert summary.candidates_not_promoted == 3 and summary.candidates_unresolved == 1
    assert summary.refusals == {
        "no_type_evidence": 1,
        "title_says_not_yet": 1,
        "unresolved_game": 1,
    }
    assert summary.promoted_event_types == {"minor_patch": 1}
    (row,) = summary.games
    assert (row.canonical_game_id, row.source_game_ids, row.posts) == (GAME, ("730",), 3)
    assert (row.candidates_promoted, row.candidates_not_promoted) == (1, 2)
    # Counts only: no title or reference leaves the store through a summary.
    dumped = summary.model_dump_json()
    for text in ("Hotfix", "Season", "Thanks", "example.invalid"):
        assert text not in dumped
    assert (
        summarise(stores.sources, STEAM_NEWS_POLICY, RULES_VERSION, NOW - HOUR).collected is False
    )
    assert (
        summarise(stores.sources, STEAM_NEWS_POLICY, RULES_VERSION, NOW - HOUR).fetches_after_as_of
        == 2
    )


def test_event_data_enters_a_bundle_only_at_a_cleared_stage_and_marks_it_private(
    tmp_path: Path,
) -> None:
    from metaspike.catalogue.seed import load_seed_catalogue
    from metaspike.domain.source_policy import DeploymentStage as Stage
    from metaspike.pipeline.assess import assess
    from metaspike.pipeline.collection import collect, window_ending
    from metaspike.publishing.build import build_bundle
    from metaspike.publishing.publisher import publish
    from metaspike.publishing.validation import ArtifactValidationError, validate_published
    from metaspike.sources.registry import default_registry
    from metaspike.sources.replay import replay_client

    catalogue = load_seed_catalogue()
    counter_strike = next(g for g in catalogue.games if g.slug == "counter-strike-2")
    resolver = steam_news_resolver(
        {APP: GameBinding(canonical_game_id=counter_strike.canonical_game_id)}
    )
    seen_at = datetime(2026, 10, 3, 12, tzinfo=UTC)
    stores = Stores(tmp_path)
    handler, _ = serving(
        {
            APP: envelope(
                [
                    post("1", "Season 4 is now live", published=seen_at - HOUR),
                    post("2", "Season 5 coming soon", published=seen_at - HOUR),
                ]
            )
        }
    )
    ingest(
        adapter_for(handler, Ticking(seen_at)), STEAM_NEWS_POLICY, [APP], resolver,
        stores.sources, stores.events,
    )  # fmt: skip
    generated = datetime(2026, 10, 4, 6, tzinfo=UTC)
    as_of = datetime(2026, 10, 4, tzinfo=UTC)

    def bundle(stage: Stage, inputs: EventInputs | None) -> ArtifactBundle:
        with replay_client(REPO / "tests/fixtures/wikimedia") as client:
            registry = default_registry(client)
            registry.stage = stage
            report = collect(
                catalogue, registry, window_ending(date(2026, 10, 3)),
                observed_at=generated, clock=lambda: generated,
            )  # fmt: skip
            return build_bundle(
                catalogue, report, assess(catalogue, report), registry, generated, inputs
            )

    inputs = EventInputs(
        records=stores.events.events(as_of),
        checks=stores.events.checks(as_of),
        as_of=as_of,
        source_policies=EVENT_SOURCE_POLICIES,
        source_summaries=(summarise(stores.sources, STEAM_NEWS_POLICY, RULES_VERSION, as_of),),
    )
    private = bundle(Stage.PRIVATE_DEVELOPMENT, inputs)
    assert private.manifest.distribution == "private_development_only"
    (restricted,) = private.manifest.restricted_sources
    assert restricted.source == SOURCE_ID and restricted.purpose == "event_intelligence"
    assert restricted.cleared_stages == ("private_development",)
    assert "not a permission" in restricted.reason
    (view,) = private.source_health.event_sources
    assert (view.source, view.status, view.purpose) == (
        SOURCE_ID,
        "collected",
        "event_intelligence",
    )
    assert (view.posts_observed, view.candidates, view.candidates_promoted) == (2, 2, 1)
    assert view.candidates_not_promoted == 1 and view.refusals == {"title_says_not_yet": 1}
    assert [(g.slug, g.candidates_promoted) for g in view.games] == [("counter-strike-2", 1)]
    assert [s.cleared for s in view.stage_clearance] == [True, False, False, False]
    # The game carries the promoted event; the refused candidate is nowhere in the bundle.
    game = private.games["counter-strike-2"]
    assert game.event_intelligence.availability == "available"
    assert game.event_intelligence.event_heat is not None and game.event_intelligence.event_heat > 0
    assert [event.title for event in game.events] == ["Season 4 is now live"]
    everything = json.dumps(
        [private.source_health.model_dump(mode="json"), private.radar.model_dump(mode="json")]
        + [g.model_dump(mode="json") for g in private.games.values()]
    )
    assert "coming soon" not in everything and "synthetic body" not in everything
    # The radar holds only real promoted events; a candidate can never be on it.
    assert all(item.slug == "counter-strike-2" for item in private.radar.items)
    # Score v0 is what it is without any of this.
    plain = bundle(Stage.PRIVATE_DEVELOPMENT, None)
    assert plain.manifest.distribution == "internal" and plain.manifest.restricted_sources == ()
    assert plain.source_health.event_sources == ()
    for slug, artifact in plain.games.items():
        assert artifact.score == private.games[slug].score, slug
        assert (
            artifact.rank == private.games[slug].rank
            and artifact.confidence == private.games[slug].confidence
        )
    assert [e.slug for e in plain.current.entries] == [e.slug for e in private.current.entries]

    # At any stage the policy does not clear, the same inputs stop the build.
    for stage in (Stage.PRIVATE_COLLECTION, Stage.PUBLIC_LAUNCH, Stage.COMMERCIAL_PRODUCTION):
        with pytest.raises(SourcePolicyError, match=SOURCE_ID):
            bundle(stage, inputs)
    # A source with no policy is refused outright, and so is a summary alone.
    with pytest.raises(SourcePolicyError, match="no source policy"):
        bundle(Stage.PRIVATE_DEVELOPMENT, EventInputs(
            records=inputs.records, checks=inputs.checks, as_of=as_of, source_policies={}
        ))  # fmt: skip
    with pytest.raises(SourcePolicyError):
        bundle(Stage.PUBLIC_LAUNCH, EventInputs(
            as_of=as_of,
            source_policies=EVENT_SOURCE_POLICIES,
            source_summaries=inputs.source_summaries,
        ))  # fmt: skip

    # Published, the validator holds the same lines.
    root = tmp_path / "artifacts"
    publish(private, root)
    manifest = validate_published(root)
    assert manifest.schema_version == "1.4.0"
    version = root / manifest.version_path

    def tampered(name: str, change: Callable[[dict[str, Any]], None], message: str) -> None:
        targets = [root / name, version / name] if name != "manifest.json" else [root / name]
        originals = [path.read_text() for path in targets]
        for path, original in zip(targets, originals, strict=True):
            document = json.loads(original)
            change(document)
            path.write_text(json.dumps(document, indent=2) + "\n")
        with pytest.raises(ArtifactValidationError, match=message):
            validate_published(root)
        for path, original in zip(targets, originals, strict=True):
            path.write_text(original)

    def unmark(document: dict[str, Any]) -> None:
        document["distribution"] = "internal"

    def drop_restriction(document: dict[str, Any]) -> None:
        document["distribution"], document["restricted_sources"] = "internal", []

    def count_a_post_as_an_event(document: dict[str, Any]) -> None:
        document["event_sources"][0]["candidates_promoted"] = 2

    def promote_without_classifying(document: dict[str, Any]) -> None:
        document["event_sources"][0]["candidates_classified"] = 0

    def claim_uncollected(document: dict[str, Any]) -> None:
        document["event_sources"][0]["status"] = "not_collected"

    tampered("manifest.json", unmark, "distribution disagrees")
    tampered("manifest.json", drop_restriction, "does not mark the bundle as private")
    tampered("source-health.json", count_a_post_as_an_event, "do not add up")
    tampered(
        "source-health.json",
        promote_without_classifying,
        "more candidates promoted than classified",
    )
    tampered("source-health.json", claim_uncollected, "status disagrees")
    validate_published(root)


def test_the_command_ingests_offline_and_prints_counts_never_posts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_network(*_: object, **__: object) -> None:
        raise AssertionError("event ingestion tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(cli, "build_client", no_network)
    base = [
        "ingest-events",
        "--event-sources-dir", str(tmp_path / "sources"),
        "--events-dir", str(tmp_path / "events"),
        "--quota-dir", str(tmp_path / "quota"),
        "--observed-at", "2026-10-03T12:00:00+00:00",
    ]  # fmt: skip
    offline = [*base, "--replay", str(FIXTURES)]
    # 730 has posts, 570 answers with none, 424242 is bound to nothing, 252490 answers 503.
    assert cli.main([*offline, "--apps", "730", "570", "424242"]) == cli.EXIT_OK
    output = capsys.readouterr().out
    assert "Event ingestion (offline replay): steam_news, event_classification_rules_v1" in output
    assert "requests sent: 3 of a budget of 3; fetches ok 3, failed 0, not sent 0" in output
    assert "Posts are announcements, not events:" in output
    for line in (
        "posts observed            5",
        "candidates created        5",
        "candidates classified     3",
        "candidates PROMOTED       2",
        "candidates not promoted   3",
        "event observations added  2",
        "coverage checks recorded  2",
    ):
        assert line in output, line
    assert "'minor_patch': 1, 'season': 1" in output
    # Feed names and tags are the response's structure, and are shown; posts are not.
    assert "'steam_community_announcements': 4" in output and "'patchnotes': 3" in output
    assert "not bound to a catalogue game" in output
    # Nothing of any post is printed: no title, id, URL, author or body.
    for text in (
        "Hotfix 1.4.2",
        "Season 4",
        "Release Notes",
        "Synthetic Outlet",
        "example.invalid",
        "9001",
        "synthetic body",
        "synthetic-author",
    ):
        assert text not in output, text
    events = EventStore(tmp_path / "events")
    assert len(events.observations()) == 2
    slugs = {
        game.canonical_game_id: game.slug
        for game in generate_catalogue(
            cli._recorded(cli.DEFAULT_RECORDED_DIRECTORIES), as_of=NOW
        ).catalogue.games
    }
    # Coverage for the two bound apps, the one with no posts included; none for the unbound.
    assert sorted(slugs[check.canonical_game_id] for check in events.checks()) == [
        "counter-strike-2",
        "dota-2",
    ]
    assert {slugs[o.canonical_game_id] for o in events.observations()} == {"counter-strike-2"}
    sources = EventSourceStore(tmp_path / "sources")
    assert {c.source_game.value for c in sources.candidates() if c.canonical_game_id is None} == {
        "424242"
    }
    assert "synthetic body" not in " ".join(i.model_dump_json() for i in sources.items())
    # Again: nothing new.
    assert cli.main([*offline, "--apps", "730", "570", "424242"]) == cli.EXIT_OK
    assert "new post versions kept    0" in capsys.readouterr().out
    # A failed fetch is reported as one, with no coverage.
    assert cli.main([*offline, "--apps", "252490"]) == cli.EXIT_SOURCE_UNAVAILABLE
    failed = capsys.readouterr().out
    assert (
        "fetches ok 0, failed 1" in failed and "coverage none" in failed and "http_error" in failed
    )
    assert "No candidate could safely become an event in this pass." in failed
    # A bounded backfill, offline.
    assert (
        cli.main([*base, "--replay", str(FIXTURES), "--apps", "730", "--backfill-requests", "5"])
        == cli.EXIT_OK
    )
    assert "requests sent: 3 of a budget of 5" in capsys.readouterr().out


def test_a_live_probe_is_bounded_before_anything_is_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_network(*_: object, **__: object) -> None:
        raise AssertionError("a refused probe tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(cli, "build_client", no_network)
    base = [
        "ingest-events", "--live",
        "--event-sources-dir", str(tmp_path / "sources"),
        "--events-dir", str(tmp_path / "events"),
        "--quota-dir", str(tmp_path / "quota"),
    ]  # fmt: skip
    assert cli.LIVE_EVENT_PROBE_MAX_APPS == 3
    assert cli.main([*base, "--apps", "730", "570", "252490", "553850"]) == cli.EXIT_SOURCE_POLICY
    assert "at most 3 apps" in capsys.readouterr().err
    assert cli.main([*base, "--apps", "730", "424242"]) == cli.EXIT_SOURCE_POLICY
    assert "not bound to a catalogue game" in capsys.readouterr().err
    assert cli.main([*base, "--apps", "730", "--backfill-requests", "2"]) == cli.EXIT_SOURCE_POLICY
    assert "live backfill is not approved" in capsys.readouterr().err
    with pytest.raises(SystemExit):  # one mode, and only one
        cli.main([*base, "--replay", str(FIXTURES), "--apps", "730"])
    with pytest.raises(SystemExit):
        cli.main(["ingest-events", "--apps", "730"])
    assert not (tmp_path / "sources").exists() and not (tmp_path / "events").exists()
    # Repeating an app does not buy a second request for it.
    handler, seen = serving({APP: envelope([])})
    monkeypatch.setattr(
        cli, "build_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(cli, "FileQuotaLedger", lambda _: None)
    monkeypatch.setattr(
        cli, "LimiterPool", lambda ledger=None: LimiterPool(clock=Clock(sleeper=lambda _: None))
    )
    assert cli.main([*base, "--apps", "730", "730", "730"]) == cli.EXIT_OK
    output = capsys.readouterr().out
    assert len(seen) == 1 and "Event ingestion (LIVE probe)" in output
    assert "requests sent: 1 of a budget of 1" in output


def test_the_fixtures_are_synthetic_and_say_so() -> None:
    files = sorted(FIXTURES.glob("news_*.json"))
    assert len(files) == 6
    for path in files:
        document = json.loads(path.read_text())
        assert document["note"].startswith("SYNTHETIC."), path.name
        assert "example.invalid" in path.read_text() or not document["body"].get("appnews", {}).get(
            "newsitems"
        )
    assert "synthetic" in (FIXTURES / "README.md").read_text().lower()
