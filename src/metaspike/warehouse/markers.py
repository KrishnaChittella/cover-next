"""Completion markers: "this target's slot already has a durable result".

A collector cannot list or read the landing bucket, and the warehouse sees a batch
only after the next load, so neither can tell a retried run that a slot is done. A
marker can. It is a small object at a name derived from the source, metric, target
and slot, read by exact name under the collector's own operations prefix.

The order is what makes it safe:

    1. read the marker by its exact name
    2. a valid marker for this exact target and slot: skip the source request
    3. otherwise make the attempt
    4. land the result durably
    5. commit the landing manifest
    6. only then create the marker, create-only

A marker is created from a committed batch manifest and from nothing else, so it
cannot exist before the result it stands for is durable. If a run dies between
steps 5 and 6 there is a durable result and no marker, and the retry asks the source
again: at-least-once, never a skipped sample. This is NOT exactly-once execution.
The guarantee is only this: once a result is durable and marked, a normal retry does
not call the source again for that slot.

A marker holds operational metadata and no source data: which run and which landed
object completed the slot, and how the attempt ended. Never a value.
"""

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import AwareDatetime, ValidationError

from metaspike.domain.model import FrozenModel
from metaspike.warehouse.landing import BatchManifest
from metaspike.warehouse.objects import ObjectNotFound, ObjectStore, PreconditionFailed

MARKER_FORMAT = "slot_marker_v1"


class Completion(StrEnum):
    """Outcomes that settle a slot. A failure or a refusal never does."""

    OBSERVED = "observed"  # the source answered with a value
    UNAVAILABLE = "unavailable"  # the source answered that it has none
    COLLECTED = "collected"  # a period source returned the window


class SlotMarker(FrozenModel):
    format: str = MARKER_FORMAT
    source: str
    metric: str
    source_native_id: str
    slot_start: AwareDatetime
    completion: Completion
    collection_run_id: str
    # The landed batch that holds the durable result, and its hash.
    data_object: str
    content_sha256: str
    completed_at: AwareDatetime


def marker_name(source: str, metric: str, source_native_id: str, slot_start: datetime) -> str:
    """A fixed name for one target and slot. The id is hashed: it may be any text."""
    slot = slot_start.astimezone(UTC)
    target = hashlib.sha256(source_native_id.encode("utf-8")).hexdigest()[:32]
    return f"{source}/slots/{metric}/dt={slot:%Y-%m-%d}/{slot:%H%M}/{target}.json"


class SlotMarkers:
    def __init__(self, store: ObjectStore, now: Callable[[], datetime]) -> None:
        self._store = store
        self._now = now

    def completed(
        self, source: str, metric: str, source_native_id: str, slot_start: datetime
    ) -> SlotMarker | None:
        """The marker, if a valid one exists for exactly this target and slot.

        Anything else (absent, unreadable, or about a different target) is treated as
        no marker, so a damaged marker costs a repeated request and never a sample.
        """
        try:
            data = self._store.get(marker_name(source, metric, source_native_id, slot_start)).data
            marker = SlotMarker.model_validate_json(data)
        except (ObjectNotFound, ValidationError):
            return None
        same = (
            marker.format == MARKER_FORMAT
            and (marker.source, marker.metric, marker.source_native_id)
            == (source, metric, source_native_id)
            and marker.slot_start == slot_start
        )
        return marker if same else None

    def complete(
        self,
        manifest: BatchManifest,
        metric: str,
        source_native_id: str,
        slot_start: datetime,
        completion: Completion,
    ) -> SlotMarker:
        """Mark a slot done. `manifest` is the committed batch that holds its result."""
        marker = SlotMarker(
            source=manifest.source,
            metric=metric,
            source_native_id=source_native_id,
            slot_start=slot_start,
            completion=completion,
            collection_run_id=manifest.run_id,
            data_object=manifest.data_object,
            content_sha256=manifest.content_sha256,
            completed_at=self._now(),
        )
        name = marker_name(manifest.source, metric, source_native_id, slot_start)
        try:
            self._store.create(name, marker.model_dump_json().encode("utf-8"))
        except PreconditionFailed:
            # Another run marked the slot first. Its marker stands; nothing is replaced.
            existing = self.completed(manifest.source, metric, source_native_id, slot_start)
            if existing is not None:
                return existing
        return marker
