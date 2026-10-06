"""Steam concurrent players: a SAMPLE_NOW source.

Docs: https://partner.steamgames.com/doc/webapi/ISteamUserStats  (reviewed 2026-10-05)

    GET ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid=<uint32>

"Gets the total number of players currently active in the specified app on Steam.
Note that this will not return players that are playing while not connected to Steam."

WHAT IS AND IS NOT DOCUMENTED
Documented: the interface, method, version and the single `appid` parameter. The
method lists no key parameter. NOT documented: the response body, error behaviour,
what a zero means, any short-window limit, and whether the public host serves this
method. The documentation's example uses the partner host, which needs a publisher
key and answers 403 without one; 403s "incur strict rate limits for the connecting
IP". This adapter therefore uses the public host only and never the partner host.

The user Web API key is sent in the documented `x-webapi-key` header, so it is never
part of a URL. Whether the method needs it is ambiguous in the documentation;
sending it is our chosen posture and keeps the call attributable to our key.

WHAT ONE LIVE CALL SHOWED (2026-10-06, app 730, public host, user key in the header)
HTTP 200 with the body `{"response": {"player_count": <int>, "result": 1}}`. The
request was accepted with a normal user key; whether the key was needed was not
tested. The response carried `Cache-Control: public, max-age=436` and an `Expires`
about seven minutes after `Date`, and no `Age` header: Steam lets the answer be cached
for minutes, so a count may be a few minutes older than the moment it was observed,
and sampling much faster than that would mostly re-read the same number. One success
shows nothing about failures or zero. So: a count is accepted only in exactly the
observed shape; a zero in that shape is recorded as a zero and flagged; and the
handling of every other response below is a stated rule of ours, not an observation.

A reading is one answer about now. There is no window to ask for and nothing to
backfill, and a failure is never a value.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx

from metaspike.domain.metrics import CONCURRENT_PLAYERS
from metaspike.domain.samples import SampleReading, SampleStatus
from metaspike.domain.signals import SignalSpec, SourceErrorCode
from metaspike.domain.source_policy import (
    ApprovalStatus,
    DeploymentStage,
    DisplayRules,
    LimitBasis,
    Permission,
    Requirement,
    SourceOperations,
    SourcePolicy,
    StageClearance,
)
from metaspike.domain.timeseries import Granularity
from metaspike.sources.base import SourceError, SourceMode
from metaspike.sources.credentials import Secret
from metaspike.sources.http import LimitedHttpClient, RetryConfig
from metaspike.sources.steam_catalogue import STEAM_BUDGET_GROUP, STEAM_CATALOGUE_POLICY

SOURCE_ID = "steam_current_players"
PUBLIC_HOST = "api.steampowered.com"
FORBIDDEN_HOST = "partner.steam-api.com"
CURRENT_PLAYERS_URL = f"https://{PUBLIC_HOST}/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
KEY_HEADER = "x-webapi-key"
SAMPLED_PLAYERS = SignalSpec(
    metric=CONCURRENT_PLAYERS.metric_id, granularity=Granularity.FIFTEEN_MINUTES
)
# One transmission per attempt: a retry is a new, separately recorded attempt, so the
# store always shows exactly what was sent.
SAMPLING_RETRY = RetryConfig(max_attempts=1)

# Response headers that describe caching or timing and can never carry a credential.
REPORTABLE_HEADERS = (
    "age",
    "cache-control",
    "content-type",
    "date",
    "expires",
    "last-modified",
    "pragma",
    "vary",
)

STEAM_CURRENT_PLAYERS_POLICY = SourcePolicy(
    source=SOURCE_ID,
    # Storage is conditional, not free: stored Steam Data must be disclosed to end users
    # and kept in a country named in a privacy policy.
    raw_storage_allowed=Permission.RESTRICTED,
    retention_days=None,
    historical_storage_allowed=Permission.RESTRICTED,
    # The terms do not address metrics derived from Steam Data. Unanswered is not
    # allowed: nothing computed from this source may be published until it is.
    derived_metrics_allowed=Permission.UNKNOWN,
    commercial_use_allowed=Permission.UNKNOWN,
    redistribution_allowed=Permission.RESTRICTED,
    attribution_required=Requirement.REQUIRED,
    attribution_text="Steam data provided by Valve via the Steam Web API.",
    approval_required=Requirement.NOT_REQUIRED,
    approval_status=ApprovalStatus.NOT_REQUIRED,
    commercial_approval_required=Requirement.UNKNOWN,
    commercial_approval_status=ApprovalStatus.PENDING,
    stage_clearance=(
        StageClearance(
            stage=DeploymentStage.PRIVATE_DEVELOPMENT,
            cleared=True,
            reason=(
                "The Web API Terms license access through an application the key holder "
                "owns and operates. Building the adapter, one-shot sampling and recorded "
                "fixtures stay private and publish nothing."
            ),
        ),
        StageClearance(
            stage=DeploymentStage.PRIVATE_COLLECTION,
            cleared=False,
            reason=(
                "Not cleared until ALL of these hold: a privacy/data notice with a Steam "
                "Data section is public at https://krishnachittella.dev/privacy; persistent "
                "Steam Data is stored only in the United States (us-central1); the API key "
                "registration identifies the MetaSpike / krishnachittella.dev application; "
                "calls stay far below 100,000 a day; nothing Steam-derived is published; "
                "and no Steam-derived commercial feature exists."
            ),
        ),
        StageClearance(
            stage=DeploymentStage.PUBLIC_LAUNCH,
            cleared=False,
            reason=(
                "Derived metrics are not addressed by the terms. Display also needs "
                "disclosure of stored Steam Data, 'as is' disclaimers, the Valve name, "
                "logo and followable links, and no implied affiliation."
            ),
        ),
        StageClearance(
            stage=DeploymentStage.COMMERCIAL_PRODUCTION,
            cleared=False,
            reason=(
                "Neither commercial use nor derived metrics are addressed by the Steam "
                "Web API Terms of Use (July 2010). Both stay unknown until Valve answers."
            ),
        ),
    ),
    operations=SourceOperations(
        budget_group=STEAM_BUDGET_GROUP,
        # The same allowance as every other Steam endpoint: one key, one budget.
        rate_limit=STEAM_CATALOGUE_POLICY.operations.rate_limit,
        quota=STEAM_CATALOGUE_POLICY.operations.quota,
        # A reading answers one slot and is never reused for another.
        cache_max_age_seconds=0,
        # The count is "current", but one observed response was cacheable for about
        # seven minutes, so a reading may be that much older than its observed_at.
        expected_publication_lag_seconds=450,
        publication_lag_basis=LimitBasis.OBSERVED,
    ),
    display=DisplayRules(
        link_back_required=Requirement.REQUIRED,
        branding_required=Requirement.REQUIRED,
        note=(
            "Valve name, logo and links on any page incorporating Steam Data, without "
            "'nofollow'. Must not appear endorsed by or affiliated with Valve. Data is "
            "provided 'as is' with the terms' disclaimers. The value is concurrent "
            "players sampled over time, never a count of daily players."
        ),
    ),
    reviewed_at=date(2026, 10, 6),
    references=(
        "https://partner.steamgames.com/doc/webapi/ISteamUserStats",
        "https://partner.steamgames.com/doc/webapi_overview",
        "https://partner.steamgames.com/doc/webapi_overview/auth",
        "https://steamcommunity.com/dev/apiterms",
        "https://store.steampowered.com/subscriber_agreement/",
    ),
    notes=(
        "Licence (Steam Web API Terms of Use, July 2010): you may access the Steam Web "
        "API, implement it in an Application you own and operate, and distribute Steam "
        "Data to end users for their personal use, as is. Limit: 100,000 calls per day "
        "across the Web API. The key is confidential and the licence is specific to the "
        "Application named when the key was registered. Stored Steam Data must be "
        "disclosed to end users and kept in a country named in a privacy policy. Two "
        "facts are recorded together and neither cancels the other. First, the API Terms "
        "expressly license implementing the Web API in an Application, which is "
        "necessarily automated. Second, the Steam Subscriber Agreement (revised "
        "2026-09-10), section 4.C, says: 'You may not use any form of scripts, bots, "
        'macros, or other non-human-controlled systems ("Automation") to interact with '
        "Content and Services on Steam in any manner', followed by examples; it is not "
        "limited to those examples, and the API Terms forbid using the API in any way "
        "that violates the Subscriber Agreement. Operational interpretation (owner "
        "decision, 2026-10-06): low-volume, private, licensed API collection is permitted "
        "under the API-specific terms once the private-collection conditions are met. "
        "This is a residual policy ambiguity, not a settled permission; a clarification "
        "request to Valve is to be sent before any public use. Commercial use and "
        "derived metrics are not addressed anywhere reviewed and remain unknown."
    ),
)


@dataclass(frozen=True)
class PlayerCountProbe:
    """Exactly what one live call returned, minus anything that could be a credential."""

    app_id: int
    http_status: int
    body: Any  # the parsed JSON body, or None if it was not JSON
    headers: dict[str, str]  # only REPORTABLE_HEADERS
    other_header_names: tuple[str, ...]  # names only, never values
    reading: SampleReading


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SteamCurrentPlayersAdapter:
    def __init__(
        self,
        http: LimitedHttpClient,
        api_key: Secret,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._http = http
        self._api_key = api_key
        self._now = now

    @property
    def source(self) -> str:
        return SOURCE_ID

    @property
    def mode(self) -> SourceMode:
        return SourceMode.SAMPLE_NOW

    @property
    def signal(self) -> SignalSpec:
        return SAMPLED_PLAYERS

    @property
    def description(self) -> str:
        return (
            "Concurrent players of a Steam app at the moment it was sampled, from the "
            "Steam Web API. Excludes players not connected to Steam."
        )

    def sample(self, source_native_id: str) -> SampleReading:
        response = self._send(source_native_id)
        if isinstance(response, SampleReading):
            return response
        return interpret(response.status_code, _json(response), response.headers, self._now())

    def probe(self, app_id: int) -> PlayerCountProbe:
        """One call, reported raw. Raises SourceError if no response was received."""
        response = self._send(str(app_id), raise_errors=True)
        assert isinstance(response, httpx.Response)
        body = _json(response)
        names = sorted(name.lower() for name in response.headers)
        headers = {name: response.headers[name] for name in REPORTABLE_HEADERS if name in names}
        # A response has no reason to contain the key. If one ever echoed it, nothing
        # of that response is reported.
        secret = self._api_key.reveal()
        if secret in response.text or any(secret in value for value in headers.values()):
            body, headers = "[withheld: the response echoed a credential]", {}
        return PlayerCountProbe(
            app_id=app_id,
            http_status=response.status_code,
            body=body,
            headers=headers,
            other_header_names=tuple(n for n in names if n not in REPORTABLE_HEADERS),
            reading=interpret(response.status_code, body, response.headers, self._now()),
        )

    def _send(
        self, source_native_id: str, raise_errors: bool = False
    ) -> httpx.Response | SampleReading:
        app_id = _app_id(source_native_id)
        try:
            return self._http.request(
                "GET",
                CURRENT_PLAYERS_URL,
                label=f"steam current players (app {app_id})",
                headers={KEY_HEADER: self._api_key.reveal()},
                params={"appid": app_id},
            )
        except SourceError as error:
            if raise_errors:
                raise
            refused = error.code is SourceErrorCode.QUOTA_EXHAUSTED
            return SampleReading(
                status=SampleStatus.REFUSED if refused else SampleStatus.FAILED,
                observed_at=self._now(),
                error_code=error.code,
                halt_run=error.code
                in (SourceErrorCode.QUOTA_EXHAUSTED, SourceErrorCode.RATE_LIMITED),
            )


def interpret(
    status: int, body: Any, headers: httpx.Headers, observed_at: datetime
) -> SampleReading:
    """Turn one response into a reading. A failure is never a value.

        Observed: HTTP 200, `response.player_count` an integer, `response.result` equal to 1.
    Rules of ours for everything not observed:
      401, 403     the key was refused: failed, and the run stops sending
      429          the rate was refused: failed, and the run stops sending
      404          the source answered that it has nothing for this app: unavailable
      other codes  failed
      200 without a `response` object, or with a count that is not a non-negative
                   integer, or with a `result` other than 1: failed (invalid response)
      200 with a `response` object that has no `player_count`: unavailable
    """

    def failed(code: SourceErrorCode, halt: bool = False) -> SampleReading:
        return SampleReading(
            status=SampleStatus.FAILED, observed_at=observed_at, error_code=code, halt_run=halt
        )

    if status in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
        return failed(SourceErrorCode.UNAUTHORIZED, halt=True)
    if status == httpx.codes.TOO_MANY_REQUESTS:
        return failed(SourceErrorCode.RATE_LIMITED, halt=True)
    if status == httpx.codes.NOT_FOUND:
        return SampleReading(
            status=SampleStatus.UNAVAILABLE,
            observed_at=observed_at,
            error_code=SourceErrorCode.NOT_FOUND,
        )
    if status != httpx.codes.OK:
        return failed(SourceErrorCode.HTTP_ERROR)
    envelope = body.get("response") if isinstance(body, dict) else None
    if not isinstance(envelope, dict):
        return failed(SourceErrorCode.INVALID_RESPONSE)
    if "player_count" not in envelope:
        return SampleReading(
            status=SampleStatus.UNAVAILABLE,
            observed_at=observed_at,
            error_code=SourceErrorCode.NOT_FOUND,
        )
    count = envelope["player_count"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return failed(SourceErrorCode.INVALID_RESPONSE)
    result = envelope.get("result")
    if result is not None and result != SUCCESS_RESULT:
        return failed(SourceErrorCode.INVALID_RESPONSE)
    return SampleReading(
        status=SampleStatus.OBSERVED,
        value=count,
        observed_at=observed_at,
        cache_age_seconds=_age(headers),
        cache_ttl_seconds=_max_age(headers),
    )


# The `result` value that accompanied a successful count.
SUCCESS_RESULT = 1
_MAX_AGE = re.compile(r"(?i)\bmax-age=(\d{1,9})\b")


def _age(headers: httpx.Headers) -> int | None:
    age = headers.get("age", "")
    return int(age) if age.isdigit() else None


def _max_age(headers: httpx.Headers) -> int | None:
    found = _MAX_AGE.search(headers.get("cache-control", ""))
    return int(found.group(1)) if found else None


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _app_id(source_native_id: str) -> int:
    if not source_native_id.isdigit() or source_native_id.startswith("0"):
        raise ValueError(f"not a Steam app id: {source_native_id!r}")
    return int(source_native_id)
