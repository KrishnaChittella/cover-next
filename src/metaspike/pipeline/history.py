"""Entity history for sampled signals, resolved point-in-time.

Samples are stored under the source's own id. This module answers "what is this
ENTITY's history?" by asking, for every slot, which entity the source id belonged to
when that slot was observed. Stored samples are never touched.

Two moments are involved and kept apart:

    as_of        what was known: only attempts stored by then, and only bindings that
                 had been made by then. A replay before a rebinding cannot see it.
    observed_at  what was true: a sample belongs to the entity its source id was
                 bound to at the moment it was observed.

The binding stamped on a sample at collection time is an audit record of what was
believed. It is not consulted here: the mapping history is the authority.
"""

from datetime import datetime

from metaspike.domain.baseline_rules import MULTI_APP_UNRESOLVED_REASON
from metaspike.domain.games import GameId
from metaspike.domain.identity import MappingHistory, SourceRecordKey
from metaspike.domain.model import FrozenModel
from metaspike.domain.samples import SlotResolution
from metaspike.domain.signals import SignalKey
from metaspike.domain.timeseries import TimeWindow
from metaspike.storage.samples import SampleStore

NO_BINDING_REASON = "no_source_binding_in_window"


class NativeSampleHistory(FrozenModel):
    """The slots of one source-native id that belonged to the entity."""

    source_native_id: str
    slots: tuple[SlotResolution, ...]


class EntitySampleHistory(FrozenModel):
    canonical_game_id: GameId
    key: SignalKey
    window: TimeWindow
    as_of: datetime
    # One history per source-native id, each kept whole and separate.
    by_native_id: tuple[NativeSampleHistory, ...] = ()
    # The entity's single series, when exactly one source id contributes.
    combined: tuple[SlotResolution, ...] | None = None
    # Why there is no single series. Never a statement that data is missing.
    unavailable_reason: str | None = None


def mapping_as_known(mapping: MappingHistory, as_of: datetime) -> MappingHistory:
    """The mapping history as it stood at `as_of`: later bindings and closures removed."""
    return MappingHistory(
        bindings=tuple(
            binding
            if binding.valid_to is None or binding.valid_to <= as_of
            else binding.model_copy(update={"valid_to": None})
            for binding in mapping.bindings
            if binding.valid_from <= as_of
        )
    )


def read_entity_history(
    store: SampleStore,
    mapping: MappingHistory,
    canonical_game_id: str,
    key: SignalKey,
    binding_source: str,
    window: TimeWindow,
    as_of: datetime,
) -> EntitySampleHistory:
    known = mapping_as_known(mapping, as_of)
    step = key.granularity.step
    native_ids = sorted(
        {
            binding.key.source_id
            for binding in known.bindings
            if binding.key.source == binding_source
            and binding.canonical_game_id == canonical_game_id
            and binding.valid_from < window.end + step
            and (binding.valid_to is None or binding.valid_to > window.start)
        },
        key=lambda value: (len(value), value),
    )
    histories = []
    for native_id in native_ids:
        record = SourceRecordKey(source=binding_source, source_id=native_id)
        slots = tuple(
            slot
            for slot in store.read(key, native_id, window, as_of)
            if known.canonical_for(record, _moment(slot)) == canonical_game_id
        )
        histories.append(NativeSampleHistory(source_native_id=native_id, slots=slots))

    combined = None
    reason = None
    if not histories:
        reason = NO_BINDING_REASON
    elif len(histories) > 1:
        reason = MULTI_APP_UNRESOLVED_REASON
    else:
        combined = histories[0].slots
    return EntitySampleHistory(
        canonical_game_id=canonical_game_id,
        key=key,
        window=window,
        as_of=as_of,
        by_native_id=tuple(histories),
        combined=combined,
        unavailable_reason=reason,
    )


def _moment(slot: SlotResolution) -> datetime:
    """When the slot's content was true: the observation if there is one, else the slot."""
    return slot.key.slot_start if slot.selected is None else slot.selected.observed_at
