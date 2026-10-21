"""Part C2-C.1: a request budget means outbound HTTP attempts, retries included.

"Outbound attempts" here are counted by the transport itself: every request that
reaches the mock transport is one, whatever the code above it believes it sent.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from metaspike.domain.signals import SourceErrorCode
from metaspike.jobs.collect import (
    BUDGET_EXHAUSTED,
    WORK_ORDERS_ENV,
    collect_wikimedia,
    collect_wikimedia_job,
    seed_bootstrap_markers,
)
from metaspike.jobs.dispatch import dispatch
from metaspike.jobs.targets import TargetsDocument, bootstrap_targets, publish_targets
from metaspike.scheduling.config import PRODUCTION, DispatchConfig, SourceCaps
from metaspike.scheduling.control import SourceSwitch
from metaspike.scheduling.health import Breaker, Outcome, SourceHealth
from metaspike.scheduling.tick import Counters
from metaspike.scheduling.workorder import (
    OrderPointer,
    OrderReceipt,
    WorkOrder,
    claim,
    complete,
    pointer_object,
    read_receipt,
    receipt_object,
)
from metaspike.sources.base import SourceError
from metaspike.sources.http import (
    AttemptBudget,
    LimitedHttpClient,
    RequestBudgetExhausted,
    RetryConfig,
)
from metaspike.sources.limiter import Clock, RateLimiter
from metaspike.sources.wikimedia import SOURCE_ID as WIKIMEDIA
from metaspike.sources.wikimedia import WIKIMEDIA_PAGEVIEWS_POLICY, WikimediaPageviewsAdapter
from metaspike.warehouse.landing import policy_version
from metaspike.warehouse.objects import MemoryObjectStore
from tests.test_c2_scheduling import Clock as At
from tests.test_c2_scheduling import Starter, arm, article
from tests.warehouse_support import World, real_policy, runtime_for, world

TUESDAY = datetime(2026, 10, 6, tzinfo=UTC)
PERIOD_HOUR = PRODUCTION.period_hour
RETRY = RetryConfig(max_attempts=4, base_delay_seconds=0.0, max_delay_seconds=0.0)
IDS = [t.source_native_id for t in bootstrap_targets(TUESDAY).targets]
VERSION = policy_version(real_policy(WIKIMEDIA))


class Wire:
    """A transport that counts every request it is handed and answers from a script.

    `faults` maps an article to the responses its first requests get: a status code,
    or "timeout". After the script runs out the article is answered normally.
    """

    def __init__(self, faults: dict[str, list[Any]] | None = None) -> None:
        self.sends: list[str] = []
        self.faults = {k: list(v) for k, v in (faults or {}).items()}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        parts = request.url.raw_path.decode().split("/")
        article_name = httpx.URL("http://x/" + parts[-4]).path.lstrip("/")
        self.sends.append(article_name)
        script = self.faults.get(article_name) or self.faults.get("*")
        if script:
            fault = script.pop(0)
            if fault == "timeout":
                raise httpx.ReadTimeout("timed out", request=request)
            return httpx.Response(fault, headers={"Retry-After": "0"} if fault == 429 else {})
        start = datetime.strptime(parts[-2], "%Y%m%d")
        end = datetime.strptime(parts[-1], "%Y%m%d")
        items = [
            {
                "timestamp": f"{start + timedelta(days=n):%Y%m%d}00",
                "granularity": "daily",
                "views": 100 + n,
            }
            for n in range((end - start).days + 1)
        ]
        return httpx.Response(200, content=json.dumps({"items": items}).encode())

    def http(
        self, budget: AttemptBudget | None = None, retry: RetryConfig = RETRY
    ) -> LimitedHttpClient:
        limiter = RateLimiter(WIKIMEDIA_PAGEVIEWS_POLICY.operations, clock=Clock(lambda _: None))
        client = httpx.Client(transport=httpx.MockTransport(self))
        return LimitedHttpClient(client, limiter, retry, budget=budget)

    def adapter(self, retry: RetryConfig = RETRY) -> WikimediaPageviewsAdapter:
        return WikimediaPageviewsAdapter(self.http(retry=retry))


def name_of(native_id: str) -> str:
    return native_id.split("/", 1)[1]


def asked_in_order() -> list[str]:
    """The articles in the order a run asks for them."""
    wire = Wire()
    supervised(wire, 1000)
    return list(wire.sends)


def filled(w: World) -> None:
    """Every article's history already filled, so a run asks for the routine window."""
    seed_bootstrap_markers(
        w.operations, WIKIMEDIA, IDS, days=90, data_object="x", content_sha256="a" * 64,
        collection_run_id="first", completed_at=TUESDAY,
    )  # fmt: skip


def supervised(
    wire: Wire, limit: int, retry: RetryConfig = RETRY
) -> tuple[Any, list[dict[str, Any]], World]:
    clock = At(TUESDAY.replace(hour=PERIOD_HOUR, minute=5))
    w = world(clock, policy_for=real_policy)
    filled(w)
    runtime, log = runtime_for(w, "ms-wikimedia")
    caps = PRODUCTION.sources[WIKIMEDIA].model_copy(update={"max_requests_per_execution": limit})
    return collect_wikimedia(runtime, wire.adapter(retry), None, caps), log, w


# --- the primitive ---------------------------------------------------------------------------


def test_a_budget_counts_attempts_refuses_when_spent_and_can_only_be_lowered() -> None:
    budget = AttemptBudget(2)
    budget.take()
    assert (budget.used, budget.remaining, budget.exhausted) == (1, 1, False)
    budget.take()
    assert budget.exhausted and budget.remaining == 0
    with pytest.raises(RequestBudgetExhausted) as spent:
        budget.take("Minecraft")
    assert budget.used == 2 and spent.value.limit == 2 and spent.value.after is None
    budget.restrict(10)
    assert budget.limit == 2  # never raised
    budget.restrict(1)
    assert budget.limit == 1 and budget.exhausted
    counting = AttemptBudget()
    for _ in range(50):
        counting.take()
    assert counting.used == 50 and counting.limit is None and not counting.exhausted
    assert counting.remaining is None
    with pytest.raises(ValueError):
        AttemptBudget(-1)
    # Running out of our own budget is not a source error and has no source error code.
    assert not issubclass(RequestBudgetExhausted, SourceError)
    assert BUDGET_EXHAUSTED not in {code.value for code in SourceErrorCode}


def test_every_attempt_is_taken_just_before_it_is_sent_and_a_retry_is_an_attempt() -> None:
    wire = Wire({"A": [503, 503]})
    http = wire.http(AttemptBudget(5))
    assert (
        http.request("GET", "https://x/a/A/daily/20261003/20261006", label="A").status_code == 200
    )
    assert len(wire.sends) == 3 == http.budget.used  # one fetch, two retries, three attempts
    # With no budget given the client still counts, and limits nothing.
    free = Wire({"A": [503]})
    plain = free.http()
    plain.request("GET", "https://x/a/A/daily/20261003/20261006", label="A")
    assert plain.budget.used == 2 == len(free.sends) and plain.budget.limit is None


@pytest.mark.parametrize("fault", [503, "timeout", 429])
def test_a_retry_is_not_sent_when_no_attempt_is_left(fault: Any) -> None:
    wire = Wire({"A": [fault, fault, fault]})
    http = wire.http(AttemptBudget(1))
    with pytest.raises(RequestBudgetExhausted) as spent:
        http.request("GET", "https://x/a/A/daily/20261003/20261006", label="A")
    assert len(wire.sends) == 1 == http.budget.used  # the retry never left the process
    # What the source did on the attempt that was sent is kept, as what it was.
    expected = {503: "http_error", "timeout": "timeout", 429: "rate_limited"}[fault]
    assert spent.value.after is not None and spent.value.after.code.value == expected
    # And a request with nothing left at all sends nothing and has no source error.
    with pytest.raises(RequestBudgetExhausted) as nothing:
        http.request("GET", "https://x/a/B/daily/20261003/20261006", label="B")
    assert len(wire.sends) == 1 and nothing.value.after is None


def test_retries_stop_at_the_retry_limit_or_the_budget_whichever_comes_first() -> None:
    always = {"A": [503] * 20}
    for budget, attempts, sent, error in (
        (10, 4, 4, SourceError),  # the retry policy ends it: a real source error
        (3, 4, 3, RequestBudgetExhausted),  # the budget ends it first
        (4, 4, 4, SourceError),  # both at once: the attempts were all sent and all failed
        (10, 2, 2, SourceError),
        (1, 1, 1, SourceError),
    ):
        wire = Wire(always)
        http = wire.http(AttemptBudget(budget), RetryConfig(attempts, 0.0, 0.0))
        with pytest.raises(error):
            http.request("GET", "https://x/a/A/daily/20261003/20261006", label="A")
        assert len(wire.sends) == sent == http.budget.used, (budget, attempts)


def test_no_backoff_is_waited_out_for_a_retry_that_cannot_be_sent() -> None:
    sleeps: list[float] = []
    wire = Wire({"A": [503]})
    limiter = RateLimiter(WIKIMEDIA_PAGEVIEWS_POLICY.operations, clock=Clock(sleeps.append))
    http = LimitedHttpClient(
        httpx.Client(transport=httpx.MockTransport(wire)),
        limiter,
        RetryConfig(max_attempts=4, base_delay_seconds=30.0, max_delay_seconds=30.0),
        budget=AttemptBudget(1),
    )
    with pytest.raises(RequestBudgetExhausted):
        http.request("GET", "https://x/a/A/daily/20261003/20261006", label="A")
    assert sum(sleeps) < 30.0  # it did not sit through a backoff with nothing to send


# --- the collector ---------------------------------------------------------------------------


def test_fourteen_targets_and_fourteen_attempts_with_no_failure_is_fourteen_sends() -> None:
    wire = Wire()
    summary, log, _ = supervised(wire, 14)
    assert len(wire.sends) == 14 == summary["outbound_attempts"] == summary["requests_sent"]
    assert summary["retry_attempts"] == 0 and summary["request_budget"] == 14
    assert summary["targets_intended"] == 14 == summary["targets_attempted"]
    assert summary["targets_collected"] == 14 and summary["targets_failed"] == 0
    assert summary["targets_budget_deferred"] == 0 and summary["halted_by"] is None
    assert summary["outcome"] == "succeeded" and summary["rows_landed"] == 56
    assert not [e for e in log if e["event"] == BUDGET_EXHAUSTED]


def test_one_retry_spends_a_unit_and_the_last_target_is_deferred_not_failed() -> None:
    first = name_of(sorted(IDS)[0])
    wire = Wire({first: [503]})
    summary, log, w = supervised(wire, 14)
    assert len(wire.sends) == 14 == summary["outbound_attempts"]  # never fifteen
    assert wire.sends.count(first) == 2 and summary["retry_attempts"] == 1
    assert summary["targets_attempted"] == 13 == summary["targets_collected"]
    assert summary["targets_failed"] == 0 and summary["targets_budget_deferred"] == 1
    assert summary["halted_by"] == BUDGET_EXHAUSTED and summary["outcome"] == "partial"
    # The one that was not asked has no row, no marker and no invented value.
    asked = set(wire.sends)
    missed = [i for i in IDS if name_of(i) not in asked]
    assert len(missed) == 1 and summary["rows_landed"] == 13 * 4
    assert len(w.operations.list(f"{WIKIMEDIA}/slots/pageviews/")) == 13
    # It is said in our own words, and never as the source refusing or rate limiting.
    events = [e["event"] for e in log]
    assert events.count(BUDGET_EXHAUSTED) == 1
    assert "source_refused" not in events and "source_failed" not in events
    assert "rate_limited" not in json.dumps(log)


def test_spare_attempts_may_be_used_for_retries_and_never_exceeded() -> None:
    faults = {name_of(i): [503, 503] for i in sorted(IDS)[:4]}  # eight extra attempts wanted
    wire = Wire(faults)
    summary, _, _ = supervised(wire, 22)
    assert len(wire.sends) == 22 == summary["outbound_attempts"]
    assert summary["retry_attempts"] == 8 and summary["targets_collected"] == 14
    assert summary["targets_budget_deferred"] == 0 and summary["outcome"] == "succeeded"
    # One more retry than the budget holds: it is the budget that gives way, not the cap.
    greedy = Wire({name_of(i): [503, 503] for i in sorted(IDS)[:5]})
    capped, _, _ = supervised(greedy, 22)
    assert len(greedy.sends) == 22 == capped["outbound_attempts"]
    assert capped["targets_collected"] + capped["targets_budget_deferred"] == 14
    assert capped["targets_budget_deferred"] == 2 and capped["targets_failed"] == 0


@pytest.mark.parametrize("limit", range(0, 40))
def test_sends_never_exceed_the_budget_whatever_fails(limit: int) -> None:
    wire = Wire({"*": [503, "timeout", 503, 429, "timeout", 503, 503, 503, "timeout", 503] * 3})
    summary, _, _ = supervised(wire, max(1, limit))
    assert len(wire.sends) <= max(1, limit)
    assert summary["outbound_attempts"] == len(wire.sends)
    accounted = (
        summary["targets_collected"] + summary["targets_failed"]
        + summary["targets_budget_deferred"]
    )  # fmt: skip
    assert accounted == 14  # every target is in exactly one place


def test_a_failure_whose_retry_could_not_be_sent_is_recorded_as_that_failure() -> None:
    last = asked_in_order()[-1]
    wire = Wire({last: ["timeout", "timeout"]})
    summary, log, _ = supervised(wire, 14)
    assert len(wire.sends) == 14 and summary["targets_collected"] == 13
    assert summary["targets_failed"] == 1 and summary["targets_budget_deferred"] == 0
    assert summary["halted_by"] == BUDGET_EXHAUSTED
    failed = [e for e in log if e["event"] == "source_failed"]
    assert len(failed) == 1 and failed[0]["error_code"] == "timeout"
    assert failed[0]["retry_not_sent"] == BUDGET_EXHAUSTED
    assert not [e for e in log if e["event"] == "source_refused"]


def test_a_real_rate_limit_is_still_a_rate_limit() -> None:
    first = asked_in_order()[0]
    # Budget to spare: the source answers 429 until the retry policy gives up.
    wire = Wire({first: [429, 429, 429, 429]})
    summary, log, _ = supervised(wire, 100)
    assert len(wire.sends) == 4 and summary["halted_by"] == "rate_limited"
    refused = [e for e in log if e["event"] == "source_refused"]
    assert len(refused) == 1 and refused[0]["error_code"] == "rate_limited"
    assert summary["targets_budget_deferred"] == 0 and summary["targets_failed"] == 14
    assert not [e for e in log if e["event"] == BUDGET_EXHAUSTED]
    # A 429 on the last attempt the budget had: the source did refuse, and that is said.
    tight = Wire({first: [429, 429]})
    summary, log, _ = supervised(tight, 1)
    assert len(tight.sends) == 1 and summary["halted_by"] == "rate_limited"
    assert [e["error_code"] for e in log if e["event"] == "source_refused"] == ["rate_limited"]


def test_a_transient_failure_is_still_retried_while_attempts_remain() -> None:
    names = [name_of(i) for i in sorted(IDS)]
    wire = Wire({names[2]: [503], names[7]: ["timeout", 503]})
    summary, _, _ = supervised(wire, 1000)
    assert len(wire.sends) == 17 == summary["outbound_attempts"]
    assert summary["retry_attempts"] == 3 and summary["targets_collected"] == 14
    assert summary["outcome"] == "succeeded" and summary["halted_by"] is None


def test_a_stand_in_with_no_transport_is_charged_one_attempt_for_each_fetch() -> None:
    from tests.test_c2_scheduling import Pageviews

    clock = At(TUESDAY.replace(hour=PERIOD_HOUR, minute=5))
    w = world(clock, policy_for=real_policy)
    filled(w)
    runtime, _ = runtime_for(w, "ms-wikimedia")
    caps = PRODUCTION.sources[WIKIMEDIA].model_copy(update={"max_requests_per_execution": 5})
    adapter = Pageviews()
    summary = collect_wikimedia(runtime, adapter, None, caps)  # type: ignore[arg-type]
    assert len(adapter.windows) == 5 == summary["outbound_attempts"]
    assert summary["targets_budget_deferred"] == 9 and summary["targets_failed"] == 0


# --- the dispatcher's reservation ------------------------------------------------------------


def dispatched(w: World, clock: At, hour: int, config: DispatchConfig = PRODUCTION) -> Starter:
    clock.moment = clock.moment.replace(hour=hour, minute=0, second=9)
    w.now = clock
    runtime, _ = runtime_for(w, "ms-dispatch")
    starter = Starter()
    dispatch(runtime, starter, config)
    return starter


def current_order(w: World) -> WorkOrder:
    pointer = OrderPointer.model_validate_json(w.operations.get(pointer_object(WIKIMEDIA)).data)
    return WorkOrder.model_validate_json(w.operations.get(pointer.object).data)


def counters(w: World, day: datetime) -> Counters:
    data = w.operations.get(f"dispatch/counters/{day:%Y-%m-%d}.json").data
    return Counters.model_validate_json(data)


def test_a_control_cap_of_fourteen_cannot_produce_a_twenty_two_attempt_order() -> None:
    clock = At(TUESDAY)
    w = world(clock, policy_for=real_policy)
    arm(
        w.operations,
        TUESDAY + timedelta(days=1),
        sources={WIKIMEDIA: SourceSwitch(enabled=True, max_requests_per_day=14)},
    )
    dispatched(w, clock, PERIOD_HOUR)
    order = current_order(w)
    assert len(order.targets) == 14 and order.max_requests == 14  # not 22
    assert counters(w, TUESDAY).requests == {WIKIMEDIA: 14}
    # Without the lower cap the same order carries retry room, and reserves all of it.
    free = world(clock, policy_for=real_policy)
    arm(free.operations, TUESDAY + timedelta(days=1))
    dispatched(free, clock, PERIOD_HOUR)
    assert current_order(free).max_requests == 22
    assert counters(free, TUESDAY).requests == {WIKIMEDIA: 22}


@pytest.mark.parametrize("cap", [1, 5, 13, 14, 15, 18, 21, 22, 23, 100])
def test_an_order_never_carries_more_attempts_than_the_day_has_left(cap: int) -> None:
    clock = At(TUESDAY)
    w = world(clock, policy_for=real_policy)
    arm(
        w.operations,
        TUESDAY + timedelta(days=1),
        sources={WIKIMEDIA: SourceSwitch(enabled=True, max_requests_per_day=cap)},
    )
    dispatched(w, clock, PERIOD_HOUR)
    order = current_order(w)
    assert order.max_requests <= cap and len(order.targets) <= order.max_requests
    assert len(order.targets) == min(14, cap) and order.max_requests == min(22, cap)
    assert counters(w, TUESDAY).requests[WIKIMEDIA] == order.max_requests


def test_reservations_over_several_executions_never_exceed_the_days_ceiling() -> None:
    # Forty articles in four hourly cohorts, ten targets and fifteen attempts an
    # execution, thirty-five attempts a day.
    config = DispatchConfig(
        sources={
            WIKIMEDIA: SourceCaps(
                collector_job="ms-wikimedia",
                max_targets_per_execution=10,
                max_requests_per_execution=15,
                max_requests_per_day=35,
            )
        }
    )
    clock = At(TUESDAY)
    w = world(clock, policy_for=real_policy)
    publish_targets(
        w.operations,
        TargetsDocument(
            version="forty", generated_at=TUESDAY, targets=tuple(article(i) for i in range(40))
        ),
    )
    arm(w.operations, TUESDAY + timedelta(days=1))
    reserved: list[int] = []
    issued: list[WorkOrder] = []
    for hour in range(PERIOD_HOUR, PERIOD_HOUR + 9):
        started = dispatched(w, clock, hour, config).started
        total = counters(w, TUESDAY).requests.get(WIKIMEDIA, 0)
        reserved.append(total)
        assert total <= 35
        if "ms-wikimedia" in started:
            order = current_order(w)
            issued.append(order)
            done = clock.moment + timedelta(minutes=1)
            claim(w.operations, WIKIMEDIA, done, VERSION)
            complete(
                w.operations,
                OrderReceipt(
                    order_id=order.order_id,
                    source=WIKIMEDIA,
                    outcome=Outcome.SUCCEEDED,
                    requests_sent=len(order.targets),
                    targets_collected=len(order.targets),
                    targets_failed=0,
                    completed_at=done,
                    run_id="r",
                ),
            )
    # Each order holds retry room while there is room, and the last takes what is left.
    assert len(issued) == 3 and sum(o.max_requests for o in issued) == 35 == reserved[-1]
    for order in issued[:-1]:
        assert order.max_requests == min(15, int(len(order.targets) * 1.5) + 1)
    assert issued[-1].max_requests == len(issued[-1].targets)  # no retry room remains
    assert all(len(o.targets) <= o.max_requests for o in issued)
    assert reserved == sorted(reserved)  # a reservation is never handed back the same day


def test_an_order_issued_with_no_spare_attempts_still_names_no_more_targets_than_attempts() -> None:
    clock = At(TUESDAY)
    w = world(clock, policy_for=real_policy)
    arm(
        w.operations,
        TUESDAY + timedelta(days=1),
        sources={WIKIMEDIA: SourceSwitch(enabled=True, max_requests_per_day=14)},
    )
    dispatched(w, clock, PERIOD_HOUR)
    order = current_order(w)
    with pytest.raises(ValueError):
        WorkOrder.model_validate({**order.model_dump(), "max_requests": 0})
    from metaspike.scheduling.workorder import issue

    with pytest.raises(ValueError):
        issue(MemoryObjectStore(), order.model_copy(update={"max_requests": 13}))


# --- the order, the receipt and the breaker --------------------------------------------------


def ordered(w: World, clock: At, wire: Wire, run: str = "run-1") -> dict[str, Any]:
    clock.moment += timedelta(minutes=2)
    w.now = clock
    runtime, log = runtime_for(w, "ms-wikimedia", run_id=run, environ={WORK_ORDERS_ENV: "required"})
    adapter: Callable[[], Any] = wire.adapter
    summary = dict(collect_wikimedia_job(runtime, adapter))
    summary["log"] = log
    return summary


def test_an_order_for_fourteen_attempts_sends_fourteen_and_says_what_it_did_not_reach() -> None:
    clock = At(TUESDAY)
    w = world(clock, policy_for=real_policy)
    filled(w)
    arm(
        w.operations,
        TUESDAY + timedelta(days=2),
        sources={WIKIMEDIA: SourceSwitch(enabled=True, max_requests_per_day=14)},
    )
    dispatched(w, clock, PERIOD_HOUR)
    order = current_order(w)
    wire = Wire({name_of(sorted(IDS)[0]): [503], name_of(sorted(IDS)[1]): ["timeout"]})
    summary = ordered(w, clock, wire)
    assert len(wire.sends) == 14 == order.max_requests  # the order's ceiling, on the wire
    assert summary["outbound_attempts"] == 14 and summary["retry_attempts"] == 2
    assert summary["targets_collected"] == 12 and summary["targets_budget_deferred"] == 2
    assert summary["targets_failed"] == 0 and summary["halted_by"] == BUDGET_EXHAUSTED
    receipt = read_receipt(w.operations, WIKIMEDIA, order.order_id)
    assert receipt is not None
    assert receipt.outcome is Outcome.PARTIAL  # not FAILED, and never RATE_LIMITED
    assert receipt.requests_sent == 14 and receipt.request_budget == 14
    assert receipt.targets_attempted == 12 and receipt.retry_attempts == 2
    assert receipt.targets_budget_deferred == 2 and receipt.targets_failed == 0
    # The next tick folds it: the breaker stays closed and the source stays healthy.
    clock.moment = TUESDAY.replace(hour=4, second=9)
    w.now = clock
    runtime, _ = runtime_for(w, "ms-dispatch")
    states = dispatch(runtime, Starter())["source_states"]
    health = SourceHealth.model_validate_json(
        w.operations.get(f"dispatch/health/{WIKIMEDIA}.json").data
    )
    assert health.breaker is Breaker.CLOSED and health.consecutive_failures == 0
    assert health.last_outcome is Outcome.PARTIAL and health.last_rate_limited_at is None
    assert states == {WIKIMEDIA: "healthy"}
    # The log metric for rate limits reads `source_refused` with `rate_limited`. Neither appears.
    assert not [e for e in summary["log"] if e["event"] == "source_refused"]


def test_a_real_rate_limit_under_an_order_still_opens_the_breaker() -> None:
    clock = At(TUESDAY)
    w = world(clock, policy_for=real_policy)
    filled(w)
    arm(w.operations, TUESDAY + timedelta(days=2))
    dispatched(w, clock, PERIOD_HOUR)
    order = current_order(w)
    summary = ordered(w, clock, Wire({"*": [429] * 40}))
    assert summary["halted_by"] == "rate_limited" and summary["outbound_attempts"] == 4
    receipt = read_receipt(w.operations, WIKIMEDIA, order.order_id)
    assert receipt is not None and receipt.outcome is Outcome.RATE_LIMITED
    clock.moment = TUESDAY.replace(hour=4, second=9)
    w.now = clock
    runtime, _ = runtime_for(w, "ms-dispatch")
    dispatch(runtime, Starter())
    health = SourceHealth.model_validate_json(
        w.operations.get(f"dispatch/health/{WIKIMEDIA}.json").data
    )
    assert health.breaker is Breaker.OPEN and health.last_outcome is Outcome.RATE_LIMITED


# The receipt written by the supervised C2-C run, byte for byte.
C2C_RECEIPT = (
    b'{"order_id":"34e4f8be49cf8a61e3709cc9c36cc478","source":"wikimedia_pageviews",'
    b'"outcome":"succeeded","requests_sent":14,"targets_collected":14,"targets_failed":0,'
    b'"completed_at":"2026-10-08T03:19:59.670791Z","run_id":"20261008T031955Z-ms-wikimedia-mld5x"}'
)


def test_a_receipt_written_before_attempts_were_counted_still_reads_and_means_the_same() -> None:
    old = OrderReceipt.model_validate_json(C2C_RECEIPT)
    assert old.outcome is Outcome.SUCCEEDED and old.requests_sent == 14
    assert old.targets_collected == 14 and old.targets_failed == 0
    # What it could not have recorded is unknown, not zero and not invented.
    assert old.request_budget is None and old.targets_attempted is None
    assert old.retry_attempts is None and old.targets_budget_deferred == 0
    store = MemoryObjectStore()
    store.create(receipt_object(WIKIMEDIA, old.order_id), C2C_RECEIPT)
    assert read_receipt(store, WIKIMEDIA, old.order_id) == old
    # Folding it gives the health the production record already holds.
    from metaspike.scheduling.health import apply_outcome

    health = apply_outcome(
        SourceHealth(source=WIKIMEDIA, pending_order_id=old.order_id),
        old.outcome,
        old.completed_at,
        PRODUCTION.sources[WIKIMEDIA],
    )
    assert health.breaker is Breaker.CLOSED and health.last_success == old.completed_at
    # A new receipt carries the new fields and round-trips.
    new = old.model_copy(
        update={"request_budget": 14, "targets_attempted": 14, "retry_attempts": 0}
    )
    assert OrderReceipt.model_validate_json(new.model_dump_json()) == new


def test_the_sampled_collector_is_not_switched_on_by_any_of_this() -> None:
    assert set(PRODUCTION.sources) == {WIKIMEDIA}
    from pathlib import Path

    collect = (
        Path(__file__).parent.parent / "src" / "metaspike" / "jobs" / "collect.py"
    ).read_text()
    # The budget lives in the shared HTTP client, so any HTTP source can be given one.
    http = (Path(__file__).parent.parent / "src" / "metaspike" / "sources" / "http.py").read_text()
    assert "class AttemptBudget" in http and "wikimedia" not in http.lower()
    assert "self._budget.take(label)" in http
    assert http.index("self._budget.take(label)") < http.index("response = self._send(")
    assert "BUDGET_EXHAUSTED" in collect
