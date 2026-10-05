import logging
import pickle
import random
import threading
import time
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from metaspike.domain.signals import SourceErrorCode
from metaspike.domain.source_policy import (
    ApprovalStatus,
    DeploymentStage,
    LimitBasis,
    Permission,
    QuotaBudget,
    QuotaPeriod,
    RateLimit,
    Requirement,
    SourceOperations,
    SourcePolicy,
    SourcePurpose,
    StageClearance,
)
from metaspike.domain.timeseries import TimeWindow
from metaspike.publishing.mapping import policy_view
from metaspike.sources.base import SourceError
from metaspike.sources.credentials import Secret, secret_from_env
from metaspike.sources.http import LimitedHttpClient, RetryConfig, install_log_redaction, redact
from metaspike.sources.igdb import IGDB_POLICY
from metaspike.sources.igdb_auth import TWITCH_OAUTH_OPERATIONS
from metaspike.sources.limiter import (
    QUOTA_SAFETY_FRACTION,
    Clock,
    FileQuotaLedger,
    InMemoryQuotaLedger,
    LimiterPool,
    QuotaExhaustedError,
    RateLimiter,
)
from metaspike.sources.registry import SourcePolicyError, SourceRegistry
from metaspike.sources.steam_catalogue import STEAM_BUDGET_GROUP, STEAM_CATALOGUE_POLICY
from metaspike.sources.wikimedia import WIKIMEDIA_PAGEVIEWS_POLICY, WikimediaPageviewsAdapter
from tests.conftest import GAME, RUN_TIME, WIKI_IDENTIFIER, limited_client, mock_client

OK = httpx.Response(200, json={})


def operations(
    max_requests: int = 1000,
    per_seconds: int = 1,
    max_concurrency: int = 1,
    quota: int | None = None,
    group: str = "test_group",
) -> SourceOperations:
    return SourceOperations(
        budget_group=group,
        rate_limit=RateLimit(
            max_requests=max_requests,
            per_seconds=per_seconds,
            max_concurrency=max_concurrency,
            basis=LimitBasis.SELF_IMPOSED,
        ),
        quota=(
            None
            if quota is None
            else QuotaBudget(
                limit=quota, unit="calls", period=QuotaPeriod.DAY, basis=LimitBasis.DOCUMENTED
            )
        ),
        expected_publication_lag_seconds=0,
        publication_lag_basis=LimitBasis.SELF_IMPOSED,
    )


class _CatalogueStub:
    def __init__(self, source: str) -> None:
        self.source = source
        self.description = "stub"


# --- stages: build / collect privately / launch / monetize ------------------------


def test_wikimedia_is_cleared_for_every_stage() -> None:
    for stage in DeploymentStage:
        assert WIKIMEDIA_PAGEVIEWS_POLICY.cleared_for(stage)
        registry = SourceRegistry(stage=stage)
        registry.register(WikimediaPageviewsAdapter(httpx.Client()), WIKIMEDIA_PAGEVIEWS_POLICY)
    assert WIKIMEDIA_PAGEVIEWS_POLICY.commercial_production_permitted
    assert WIKIMEDIA_PAGEVIEWS_POLICY.operations.budget_group == "wikimedia"


@pytest.mark.parametrize("policy", [IGDB_POLICY, STEAM_CATALOGUE_POLICY])
def test_igdb_and_steam_can_be_built_against_but_nothing_more_yet(policy: SourcePolicy) -> None:
    development = SourceRegistry(stage=DeploymentStage.PRIVATE_DEVELOPMENT)
    development.register_catalogue(_CatalogueStub(policy.source), policy)
    assert [entry.policy.source for entry in development.catalogue_entries()] == [policy.source]
    assert development.entries() == ()  # catalogue sources are not demand sources
    assert development.policy(policy.source) is policy

    for stage in (
        DeploymentStage.PRIVATE_COLLECTION,
        DeploymentStage.PUBLIC_LAUNCH,
        DeploymentStage.COMMERCIAL_PRODUCTION,
    ):
        assert not policy.cleared_for(stage)
        with pytest.raises(SourcePolicyError, match=f"not cleared for {stage.value}"):
            SourceRegistry(stage=stage).register_catalogue(_CatalogueStub(policy.source), policy)
    assert not policy.commercial_production_permitted


def test_reviewed_policy_values_are_recorded_exactly() -> None:
    assert IGDB_POLICY.commercial_use_allowed is Permission.RESTRICTED
    assert IGDB_POLICY.commercial_approval_required is Requirement.REQUIRED
    assert IGDB_POLICY.commercial_approval_status is ApprovalStatus.PENDING
    assert IGDB_POLICY.approval_required is Requirement.NOT_REQUIRED  # free to build against
    assert IGDB_POLICY.attribution_required is Requirement.REQUIRED
    assert IGDB_POLICY.derived_metrics_allowed is Permission.UNKNOWN
    limit = IGDB_POLICY.operations.rate_limit
    assert (limit.max_requests, limit.per_seconds, limit.max_concurrency) == (4, 1, 8)

    assert STEAM_CATALOGUE_POLICY.commercial_use_allowed is Permission.UNKNOWN
    assert STEAM_CATALOGUE_POLICY.historical_storage_allowed is Permission.RESTRICTED
    assert STEAM_CATALOGUE_POLICY.display.branding_required is Requirement.REQUIRED
    quota = STEAM_CATALOGUE_POLICY.operations.quota
    assert quota is not None and (quota.limit, quota.period) == (100_000, QuotaPeriod.DAY)
    assert STEAM_CATALOGUE_POLICY.operations.budget_group == STEAM_BUDGET_GROUP == "steam_web_api"

    for policy in (IGDB_POLICY, STEAM_CATALOGUE_POLICY):
        assert policy.reviewed_at == date(2026, 10, 4)
        assert all(reference.startswith("https://") for reference in policy.references)


def test_the_permission_checked_depends_on_what_the_source_is_used_for() -> None:
    development = DeploymentStage.PRIVATE_DEVELOPMENT
    # IGDB does not address derived metrics, so it may supply metadata but not signals.
    assert IGDB_POLICY.registration_refusal(SourcePurpose.CATALOGUE_METADATA, development) is None
    refusal = IGDB_POLICY.registration_refusal(SourcePurpose.DEMAND_SIGNALS, development)
    assert refusal is not None and "does not permit collection" in refusal

    no_storage = STEAM_CATALOGUE_POLICY.model_copy(
        update={"historical_storage_allowed": Permission.UNKNOWN}
    )
    refusal = no_storage.registration_refusal(SourcePurpose.CATALOGUE_METADATA, development)
    assert refusal == "policy does not permit storing the source's metadata"

    unapproved = IGDB_POLICY.model_copy(
        update={
            "approval_required": Requirement.REQUIRED,
            "approval_status": ApprovalStatus.PENDING,
        }
    )
    refusal = unapproved.registration_refusal(SourcePurpose.CATALOGUE_METADATA, development)
    assert refusal is not None and "approval has not been granted" in refusal


def test_commercial_clearance_cannot_be_claimed_without_the_permission_behind_it() -> None:
    cleared = tuple(
        StageClearance(stage=stage, cleared=True, reason="claimed") for stage in DeploymentStage
    )
    with pytest.raises(ValueError, match="cannot be cleared without"):
        SourcePolicy.model_validate(
            STEAM_CATALOGUE_POLICY.model_dump() | {"stage_clearance": cleared}
        )
    with pytest.raises(ValueError, match="cannot be cleared without"):  # permission, no approval
        SourcePolicy.model_validate(
            IGDB_POLICY.model_dump()
            | {"stage_clearance": cleared, "commercial_use_allowed": Permission.ALLOWED}
        )
    approved = SourcePolicy.model_validate(
        IGDB_POLICY.model_dump()
        | {
            "stage_clearance": cleared,
            "commercial_use_allowed": Permission.ALLOWED,
            "commercial_approval_status": ApprovalStatus.APPROVED,
        }
    )
    assert approved.commercial_production_permitted


def test_every_stage_must_be_stated_exactly_once() -> None:
    with pytest.raises(ValueError, match="every deployment stage exactly once"):
        SourcePolicy.model_validate(
            IGDB_POLICY.model_dump() | {"stage_clearance": IGDB_POLICY.stage_clearance[:3]}
        )
    with pytest.raises(ValueError, match="commercial_approval_status"):
        SourcePolicy.model_validate(
            STEAM_CATALOGUE_POLICY.model_dump()
            | {"commercial_approval_status": ApprovalStatus.NOT_REQUIRED}
        )


def test_new_policy_fields_are_published_through_the_contract_view() -> None:
    view = policy_view(IGDB_POLICY).model_dump(mode="json")
    assert view == IGDB_POLICY.model_dump(mode="json")
    assert view["operations"]["budget_group"] == "igdb"
    assert {c["stage"]: c["cleared"] for c in view["stage_clearance"]} == {
        "private_development": True,
        "private_collection": False,
        "public_launch": False,
        "commercial_production": False,
    }


# --- requests per interval ---------------------------------------------------------


def test_requests_beyond_the_interval_limit_wait_for_tokens() -> None:
    sleeps: list[float] = []
    limiter = RateLimiter(operations(max_requests=4, per_seconds=1), clock=Clock(sleeps.append))
    for _ in range(10):
        with limiter.slot():
            pass
    assert limiter.transmitted == 10
    assert len(sleeps) == 6  # the first four are free, each later one waits for a token
    assert sum(sleeps) == pytest.approx(1.5, abs=0.01)  # six more tokens at four per second
    assert max(sleeps) == pytest.approx(0.25, abs=0.01)


def test_a_slow_caller_is_never_made_to_wait() -> None:
    sleeps: list[float] = []
    clock = Clock(sleeps.append)
    limiter = RateLimiter(operations(max_requests=1, per_seconds=1), clock=clock)
    for _ in range(3):
        with limiter.slot():
            pass
        clock.sleep(2.0)  # the caller's own work takes longer than the interval
    assert sleeps == [2.0, 2.0, 2.0]


# --- concurrency -------------------------------------------------------------------


def test_no_more_than_the_allowed_number_of_requests_are_in_flight() -> None:
    limiter = RateLimiter(operations(max_concurrency=3))
    in_flight = 0
    peak = 0
    lock = threading.Lock()

    def work() -> None:
        nonlocal in_flight, peak
        with limiter.slot():
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            time.sleep(0.01)
            with lock:
                in_flight -= 1

    threads = [threading.Thread(target=work) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert peak == 3 and limiter.transmitted == 12


# --- quota -------------------------------------------------------------------------


def test_quota_refuses_before_transmission_and_never_waits_for_the_reset() -> None:
    sleeps: list[float] = []
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return OK

    http = limited_client(handler, operations(quota=10), sleeps)
    effective = int(10 * QUOTA_SAFETY_FRACTION)
    for _ in range(effective):
        http.request("GET", "https://example.test/", label="probe")

    with pytest.raises(SourceError) as refused:
        http.request("GET", "https://example.test/", label="probe")
    assert refused.value.code is SourceErrorCode.QUOTA_EXHAUSTED
    assert "quota exhausted" in refused.value.message
    assert len(sent) == effective == 9  # the refused request never left
    assert sleeps == [] and http.limiter.quota_remaining() == 0


def test_every_transmitted_attempt_including_a_retry_consumes_quota() -> None:
    ledger = InMemoryQuotaLedger()
    answers = [httpx.Response(503), httpx.Response(429), OK]
    http = limited_client(lambda request: answers.pop(0), operations(quota=100), ledger=ledger)

    http.request("GET", "https://example.test/", label="probe")
    today = datetime.now(UTC).date().isoformat()
    assert ledger.used("test_group", today) == 3 == http.limiter.transmitted


def test_a_retry_is_refused_once_the_quota_runs_out_mid_request() -> None:
    sent = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        return httpx.Response(503)

    http = limited_client(handler, operations(quota=3))  # two calls after the safety margin
    with pytest.raises(SourceError) as refused:
        http.request("GET", "https://example.test/", label="probe")
    assert refused.value.code is SourceErrorCode.QUOTA_EXHAUSTED and sent == 2


def test_budget_groups_are_independent() -> None:
    pool = LimiterPool()
    steam = pool.for_operations(operations(quota=2, group="steam_web_api"))
    igdb = pool.for_operations(operations(group="igdb"))
    with steam.slot():
        pass
    with pytest.raises(QuotaExhaustedError), steam.slot():
        pass
    for _ in range(50):  # an exhausted neighbour does not slow or stop this group
        with igdb.slot():
            pass
    assert igdb.transmitted == 50 and igdb.quota_remaining() is None


def test_sources_in_one_budget_group_share_one_limiter() -> None:
    pool = LimiterPool()
    catalogue = pool.for_policy(STEAM_CATALOGUE_POLICY)
    assert pool.for_operations(STEAM_CATALOGUE_POLICY.operations) is catalogue
    assert pool.for_policy(IGDB_POLICY) is not catalogue
    assert pool.for_operations(TWITCH_OAUTH_OPERATIONS) is not pool.for_policy(IGDB_POLICY)
    with pytest.raises(ValueError, match="two different limits"):
        pool.for_operations(operations(quota=5, group=STEAM_BUDGET_GROUP))


def test_file_ledger_shares_a_days_budget_across_runs_and_resets_next_day(tmp_path: Path) -> None:
    day = [datetime(2026, 10, 4, 23, 0, tzinfo=UTC)]

    def limiter() -> RateLimiter:
        return RateLimiter(
            operations(quota=10), ledger=FileQuotaLedger(tmp_path), wall_clock=lambda: day[0]
        )

    first_run = limiter()
    for _ in range(5):
        with first_run.slot():
            pass
    second_run = limiter()  # a new process the same day
    assert second_run.quota_remaining() == 4
    for _ in range(4):
        with second_run.slot():
            pass
    with pytest.raises(QuotaExhaustedError), second_run.slot():
        pass

    day[0] = datetime(2026, 10, 5, 0, 5, tzinfo=UTC)
    assert limiter().quota_remaining() == 9
    with pytest.raises(ValueError, match="unsafe budget group"):
        FileQuotaLedger(tmp_path).used("../escape", "2026-10-05")


# --- Retry-After, backoff, jitter ---------------------------------------------------


def test_retry_after_is_honoured_and_pauses_the_whole_group() -> None:
    sleeps: list[float] = []
    answers = [httpx.Response(429, headers={"Retry-After": "3"}), OK, OK]
    http = limited_client(lambda request: answers.pop(0), operations(), sleeps)

    http.request("GET", "https://example.test/", label="probe")
    assert sleeps == [3.0]
    http.request("GET", "https://example.test/", label="probe")
    assert sleeps == [3.0]  # the pause has passed; nobody waits twice


def test_another_caller_in_the_group_waits_out_a_backoff() -> None:
    sleeps: list[float] = []
    limiter = RateLimiter(operations(), clock=Clock(sleeps.append))
    limiter._not_before = limiter._clock.now() + 5.0  # someone else was told to back off
    with limiter.slot():
        pass
    assert sleeps == [pytest.approx(5.0, abs=0.01)]


def test_backoff_is_exponential_bounded_and_jittered_within_limits() -> None:
    plain = RetryConfig()
    assert [plain.delay(attempt) for attempt in range(1, 7)] == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0]
    assert plain.delay(1, retry_after=10_000) == plain.max_delay_seconds

    jittered = RetryConfig(jitter=0.25)
    rng = random.Random(7)
    for attempt in range(1, 7):
        ceiling = min(8.0, 0.5 * 2 ** (attempt - 1))
        samples = [jittered.delay(attempt, rng=rng) for _ in range(200)]
        assert all(0.75 * ceiling <= value <= ceiling for value in samples)
        assert len(set(samples)) > 1 or attempt >= 6
    assert jittered.delay(2, retry_after=6.0, rng=rng) == 6.0


def test_attempts_are_bounded() -> None:
    sent = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        raise httpx.ConnectError("down")

    sleeps: list[float] = []
    http = limited_client(handler, operations(), sleeps, retry=RetryConfig(max_attempts=3))
    with pytest.raises(SourceError) as failed:
        http.request("GET", "https://example.test/", label="probe")
    assert failed.value.code is SourceErrorCode.NETWORK_ERROR
    assert sent == 3 and sleeps == [0.5, 1.0]


def test_non_transient_statuses_are_returned_to_the_adapter_without_retry() -> None:
    sent = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        return httpx.Response(404)

    http = limited_client(handler, operations())
    assert http.request("GET", "https://example.test/", label="probe").status_code == 404
    assert sent == 1


def test_wikimedia_requests_all_pass_through_its_limiter() -> None:
    seen = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen
        seen += 1
        return httpx.Response(404)

    limiter = RateLimiter(WIKIMEDIA_PAGEVIEWS_POLICY.operations)
    adapter = WikimediaPageviewsAdapter(LimitedHttpClient(mock_client(handler), limiter))
    window = TimeWindow.of_days(date(2026, 10, 1), date(2026, 10, 3))
    for _ in range(3):
        with pytest.raises(SourceError):
            adapter.fetch(GAME, WIKI_IDENTIFIER, adapter.signals[0], window, RUN_TIME)
    assert seen == 3 == limiter.transmitted
    assert not hasattr(adapter, "_client")  # no bare transport is kept to bypass with


# --- secrets -----------------------------------------------------------------------

STEAM_KEY = "0123456789ABCDEF0123456789ABCDEF"
TWITCH_SECRET = "abcdefghij0123456789abcdefghij"


def test_a_secret_never_appears_in_text() -> None:
    secret = Secret(STEAM_KEY)
    rendered = [repr(secret), str(secret), f"{secret}", f"{secret!r}", f"{secret}"]
    assert all(STEAM_KEY not in text for text in rendered)
    assert secret.reveal() == STEAM_KEY and secret == Secret(STEAM_KEY)
    with pytest.raises(TypeError):
        pickle.dumps(secret)
    with pytest.raises(ValueError):
        Secret("")


def test_a_missing_credential_is_named_by_variable_only() -> None:
    with pytest.raises(SourceError) as missing:
        secret_from_env("METASPIKE_STEAM_WEB_API_KEY", {})
    assert missing.value.code is SourceErrorCode.MISSING_CREDENTIALS
    assert missing.value.message == "environment variable METASPIKE_STEAM_WEB_API_KEY is not set"
    found = secret_from_env("METASPIKE_STEAM_WEB_API_KEY", {"METASPIKE_STEAM_WEB_API_KEY": "x"})
    assert found.reveal() == "x"


@pytest.mark.parametrize(
    "text",
    [
        f"GET https://api.steampowered.com/IStoreService/GetAppList/v1/?key={STEAM_KEY}&max_results=5",
        f"client_id=abc123&client_secret={TWITCH_SECRET}&grant_type=client_credentials",
        f"Authorization: Bearer {TWITCH_SECRET}",
        f"'Authorization': 'Bearer {TWITCH_SECRET}'",
        f'{{"access_token":"{TWITCH_SECRET}","expires_in":5000}}',
    ],
)
def test_credentials_are_redacted_from_text(text: str) -> None:
    cleaned = redact(text)
    assert STEAM_KEY not in cleaned and TWITCH_SECRET not in cleaned
    assert "[redacted]" in cleaned
    assert redact("max_results=5&include_games=true") == "max_results=5&include_games=true"


def test_http_client_logs_cannot_leak_a_key(caplog: pytest.LogCaptureFixture) -> None:
    install_log_redaction()
    http_logger = logging.getLogger("httpx")
    assert http_logger.level >= logging.WARNING  # request-line logging is off by default
    with caplog.at_level(logging.DEBUG, logger="httpx"):
        http_logger.setLevel(logging.DEBUG)  # even if someone turns it back on
        http_logger.info('HTTP Request: GET https://x.test/?key=%s "HTTP/1.1 200 OK"', STEAM_KEY)
        http_logger.setLevel(logging.WARNING)
    assert caplog.records and all(STEAM_KEY not in r.getMessage() for r in caplog.records)
    assert "[redacted]" in caplog.records[0].getMessage()


def test_errors_are_built_from_labels_never_from_urls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    http = limited_client(handler, operations(), retry=RetryConfig(max_attempts=1))
    with pytest.raises(SourceError) as failed:
        http.request(
            "GET", "https://example.test/", label="steam app list", params={"key": STEAM_KEY}
        )
    assert failed.value.message == "steam app list: ConnectError"
    assert STEAM_KEY not in str(failed.value) and failed.value.__cause__ is None
