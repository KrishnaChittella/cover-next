"""Work orders: how the dispatcher tells a collector exactly what to collect.

A job's command is fixed and the dispatcher may only start a job, so an order cannot
be passed as an argument. A collector cannot list a bucket either. So:

    dispatch/orders/{source}/{order_id}.json   the order; created once, never changed
    dispatch/orders/{source}/CURRENT           a small pointer to the latest order,
                                               replaced on its generation
    {source}/orders/{order_id}.json            the collector's receipt, in its own
                                               prefix; created once

Everything is read by exact name. The order id is a hash of the source, the tick and
the schedule version, so issuing the same order twice is the same order.

A collector acts on an order only if the pointer is readable, the order it names
exists and matches the pointer's hash, it is for this source and policy version, it
has not expired, and no receipt exists for it. Anything else is a refusal and the
collector asks the source nothing. An order cannot run after its expiry, so an old
order is never run indefinitely.

Collection stays at-least-once. A crash before the receipt lets the same order be
claimed again inside its lifetime, and the completion markers stop it asking again
for what it already landed.

The minimum access this needs beyond what exists: read on the operations bucket under
`dispatch/orders/{source}/` for that source's collector. Nothing else changes.
"""

import hashlib
from datetime import datetime

from pydantic import AwareDatetime, Field, ValidationError

from metaspike.domain.model import FrozenModel
from metaspike.domain.timeseries import Granularity
from metaspike.scheduling.health import Outcome
from metaspike.scheduling.tiers import Tier
from metaspike.sources.base import SourceMode
from metaspike.storage.base import StorageCorruptionError
from metaspike.warehouse.objects import (
    ObjectNotFound,
    ObjectStore,
    ObjectStoreError,
    PreconditionFailed,
)

FORMAT = "work_order_v1"


class OrderTarget(FrozenModel):
    source_native_id: str
    canonical_game_id: str | None = None
    tier: Tier
    reason: str


class WorkOrder(FrozenModel):
    format: str = FORMAT
    order_id: str
    source: str
    metric: str
    granularity: Granularity
    mode: SourceMode
    # The slot or period every target is collected for.
    slot: AwareDatetime
    # Period sources: how many days ending at the slot to ask for. None for a sample.
    window_days: int | None = None
    tick: AwareDatetime
    created_at: AwareDatetime
    expires_at: AwareDatetime
    schedule_version: str
    policy_version: str
    targets: tuple[OrderTarget, ...] = Field(min_length=1)
    max_targets: int = Field(ge=1)
    max_requests: int = Field(ge=1)
    probe: bool = False


class OrderPointer(FrozenModel):
    order_id: str
    object: str
    content_sha256: str
    tick: AwareDatetime
    expires_at: AwareDatetime


class OrderReceipt(FrozenModel):
    order_id: str
    source: str
    outcome: Outcome
    requests_sent: int = Field(ge=0)
    targets_collected: int = Field(ge=0)
    targets_failed: int = Field(ge=0)
    completed_at: AwareDatetime
    run_id: str


class ClaimRefused(Exception):
    """There is no order this collector may act on. It must ask the source nothing."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def order_id(source: str, tick: datetime, schedule_version: str) -> str:
    text = f"{source}|{tick.isoformat()}|{schedule_version}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def order_object(source: str, identifier: str) -> str:
    return f"dispatch/orders/{source}/{identifier}.json"


def pointer_object(source: str) -> str:
    return f"dispatch/orders/{source}/CURRENT"


def receipt_object(source: str, identifier: str) -> str:
    return f"{source}/orders/{identifier}.json"


def issue(store: ObjectStore, order: WorkOrder) -> OrderPointer:
    """Write an order and point at it. Safe to repeat for the same order."""
    if len(order.targets) > order.max_targets or len(order.targets) > order.max_requests:
        raise ValueError("an order may not name more targets than its own limits")
    if order.order_id != order_id(order.source, order.tick, order.schedule_version):
        raise ValueError("the order id does not match what the order says")
    name = order_object(order.source, order.order_id)
    body = order.model_dump_json().encode("utf-8")
    try:
        store.create(name, body)
    except PreconditionFailed:
        if store.get(name).data != body:
            raise StorageCorruptionError(f"{name} exists with other content") from None
    pointer = OrderPointer(
        order_id=order.order_id,
        object=name,
        content_sha256=hashlib.sha256(body).hexdigest(),
        tick=order.tick,
        expires_at=order.expires_at,
    )
    target = pointer_object(order.source)
    data = pointer.model_dump_json().encode("utf-8")
    for _ in range(5):
        try:
            current = store.get(target)
        except ObjectNotFound:
            try:
                store.create(target, data)
                return pointer
            except PreconditionFailed:
                continue
        try:
            held = OrderPointer.model_validate_json(current.data)
        except ValidationError:
            held = None
        if held is not None and held.tick > order.tick:
            return held  # a later tick already moved the pointer on; never move it back
        if current.data == data:
            return pointer
        try:
            store.replace(target, data, current.generation)
            return pointer
        except PreconditionFailed:
            continue
    raise RuntimeError(f"the order pointer for {order.source} could not be moved")


def read_receipt(store: ObjectStore, source: str, identifier: str) -> OrderReceipt | None:
    try:
        data = store.get(receipt_object(source, identifier)).data
    except ObjectNotFound:
        return None
    try:
        return OrderReceipt.model_validate_json(data)
    except ValidationError:
        raise StorageCorruptionError(f"the receipt for order {identifier} is unreadable") from None


def claim(store: ObjectStore, source: str, now: datetime, policy_version: str) -> WorkOrder:
    """The order this collector should act on now, or ClaimRefused. Exact names only."""
    try:
        data = store.get(pointer_object(source)).data
    except ObjectNotFound:
        raise ClaimRefused("no_order_pointer") from None
    except ObjectStoreError:
        raise ClaimRefused("order_pointer_unreadable") from None
    try:
        pointer = OrderPointer.model_validate_json(data)
    except ValidationError:
        raise ClaimRefused("order_pointer_malformed") from None
    if pointer.expires_at <= now:
        raise ClaimRefused("order_expired")
    if pointer.object != order_object(source, pointer.order_id):
        raise ClaimRefused("order_pointer_names_another_prefix")
    try:
        body = store.get(pointer.object).data
    except ObjectStoreError:
        raise ClaimRefused("order_missing") from None
    if hashlib.sha256(body).hexdigest() != pointer.content_sha256:
        raise ClaimRefused("order_hash_mismatch")
    try:
        order = WorkOrder.model_validate_json(body)
    except ValidationError:
        raise ClaimRefused("order_malformed") from None
    if order.format != FORMAT or order.source != source or order.order_id != pointer.order_id:
        raise ClaimRefused("order_not_for_this_collector")
    if order.expires_at <= now:
        raise ClaimRefused("order_expired")
    if order.policy_version != policy_version:
        raise ClaimRefused("order_policy_version_changed")
    try:
        if read_receipt(store, source, order.order_id) is not None:
            raise ClaimRefused("order_already_completed")
    except StorageCorruptionError:
        raise ClaimRefused("order_receipt_unreadable") from None
    return order


def complete(store: ObjectStore, receipt: OrderReceipt) -> None:
    """Record an order's outcome, once. A second receipt for an order is ignored."""
    name = receipt_object(receipt.source, receipt.order_id)
    try:
        store.create(name, receipt.model_dump_json().encode("utf-8"))
    except PreconditionFailed:
        return
