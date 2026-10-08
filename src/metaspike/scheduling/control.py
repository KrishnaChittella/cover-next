"""The dispatcher control record: the switch a tick reads before anything else.

One small object, written by the owner. A tick starts nothing unless the record is
present, well formed, enabled and not past `armed_until`, and it plans a source only
if the record names that source as enabled. Every other case is a refusal: the
record fails closed.

`armed_until` is what bounds a soak. When it passes, collection stops whether or not
anyone pauses the scheduler.

Pins are a temporary, private testing mechanism: an explicit tier for one target,
with an expiry, a reason and a name. An expired pin does nothing.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, Field, ValidationError

from metaspike.domain.model import FrozenModel
from metaspike.domain.timeseries import Granularity
from metaspike.scheduling.tiers import Tier
from metaspike.sources.base import SourceMode
from metaspike.warehouse.objects import ObjectNotFound, ObjectStore, ObjectStoreError

CONTROL_OBJECT = "dispatch/control.json"


class Pin(FrozenModel):
    source: str
    metric: str
    granularity: Granularity
    mode: SourceMode
    source_native_id: str
    tier: Tier
    expires_at: AwareDatetime
    reason: str = Field(min_length=1)
    created_by: str = Field(min_length=1)


class SourceSwitch(FrozenModel):
    enabled: bool = False
    # May lower the configured ceilings. Never raises them.
    max_requests_per_day: int | None = Field(default=None, ge=0)
    max_targets_per_execution: int | None = Field(default=None, ge=0)


class ControlRecord(FrozenModel):
    version: Literal[1]
    enabled: bool
    armed_until: AwareDatetime
    max_job_starts_per_tick: int | None = Field(default=None, ge=0)
    max_executions_per_day: int | None = Field(default=None, ge=0)
    sources: Mapping[str, SourceSwitch] = Field(default_factory=dict)
    pins: tuple[Pin, ...] = ()

    def source_enabled(self, source: str) -> bool:
        switch = self.sources.get(source)
        return switch is not None and switch.enabled

    def active_pins(self, now: datetime, limit: int) -> tuple[Pin, ...]:
        live = [pin for pin in self.pins if pin.expires_at > now and pin.tier < Tier.CATALOGUED]
        live.sort(key=lambda pin: (pin.source, pin.source_native_id, pin.expires_at))
        return tuple(live[:limit])


class Control(FrozenModel):
    """What a tick may do: a record, or the reason it may do nothing."""

    record: ControlRecord | None = None
    refusal: str | None = None


def load_control(store: ObjectStore, now: datetime) -> Control:
    try:
        data = store.get(CONTROL_OBJECT).data
    except ObjectNotFound:
        return Control(refusal="control_record_absent")
    except ObjectStoreError:
        return Control(refusal="control_record_unreadable")
    try:
        record = ControlRecord.model_validate_json(data)
    except ValidationError:
        return Control(refusal="control_record_malformed")
    if not record.enabled:
        return Control(refusal="control_disabled")
    if record.armed_until <= now:
        return Control(refusal="control_expired")
    return Control(record=record)
