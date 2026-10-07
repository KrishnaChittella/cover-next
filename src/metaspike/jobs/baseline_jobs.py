"""The feature and parity jobs for sampled signals.

`features` keeps one `BaselineState` per sampled target, continuing it from where it
stopped. `parity` rebuilds each state from stored history and compares. Both read
samples through the warehouse store and call the one Python engine; neither computes
anything of its own, and nothing here is publishable.

Both act only on a sampled source whose policy clears the stage. Today the only
sampled source is Steam's, which is not cleared, so both find nothing to do and say
so. Writing per-bucket evaluations to the warehouse belongs to Part D.
"""

import hashlib
from collections.abc import Mapping
from typing import Any

from pydantic import AwareDatetime, ValidationError

from metaspike.domain.model import FrozenModel
from metaspike.domain.source_policy import SourcePurpose
from metaspike.domain.timeseries import Granularity, TimeWindow
from metaspike.features.engine import BaselineState
from metaspike.jobs.runtime import Runtime
from metaspike.pipeline.signal_features import (
    STEAM_SLOTS,
    features_from_slots,
    steam_app_features,
    steam_config,
)
from metaspike.storage.base import StorageCorruptionError
from metaspike.warehouse.objects import ObjectNotFound, PreconditionFailed
from metaspike.warehouse.stores import WarehouseSampleStore

SAMPLED_SOURCE = STEAM_SLOTS.source


class StoredBaseline(FrozenModel):
    # The moment the state was computed as of. Parity rebuilds as of the same moment.
    as_of: AwareDatetime
    state: BaselineState


def state_name(rule_version: str, source: str, source_native_id: str) -> str:
    target = hashlib.sha256(source_native_id.encode("utf-8")).hexdigest()[:32]
    return f"baseline/{rule_version}/{source}/{target}.json"


def _cleared(runtime: Runtime) -> bool:
    refusal = runtime.policy_for(SAMPLED_SOURCE).registration_refusal(
        SourcePurpose.DEMAND_SIGNALS, runtime.stage
    )
    if refusal is not None:
        runtime.log("source_not_cleared", source=SAMPLED_SOURCE, reason=refusal)
    return refusal is None


def _store(runtime: Runtime) -> WarehouseSampleStore:
    return WarehouseSampleStore(
        runtime.writer, runtime.warehouse, runtime.policy_for, now=runtime.now, stage=runtime.stage
    )


def _load(runtime: Runtime, name: str) -> tuple[StoredBaseline | None, int | None]:
    try:
        stored = runtime.state.get(name)
    except ObjectNotFound:
        return None, None
    try:
        return StoredBaseline.model_validate_json(stored.data), stored.generation
    except ValidationError:
        raise StorageCorruptionError(f"{name} is not a stored baseline") from None


def features(runtime: Runtime) -> Mapping[str, Any]:
    if not _cleared(runtime):
        return {"outcome": "nothing_to_do", "reason": f"{SAMPLED_SOURCE} is not cleared"}
    store, config, as_of = _store(runtime), steam_config(), runtime.now()
    updated = 0
    for native_id in store.native_ids(STEAM_SLOTS):
        name = state_name(config.rule_version, SAMPLED_SOURCE, native_id)
        earlier, generation = _load(runtime, name)
        if earlier is None or earlier.state.valid_through is None:
            _, state = steam_app_features(store, native_id, as_of)
        else:
            reading = TimeWindow(
                start=earlier.state.valid_through,
                end=max(earlier.state.valid_through, Granularity.FIFTEEN_MINUTES.floor(as_of)),
            )
            slots = store.read(STEAM_SLOTS, native_id, reading, as_of)
            _, state = features_from_slots(config, native_id, slots, as_of, earlier.state)
        body = StoredBaseline(as_of=as_of, state=state).model_dump_json().encode("utf-8")
        try:
            if generation is None:
                runtime.state.create(name, body)
            else:
                runtime.state.replace(name, body, generation)
        except PreconditionFailed:
            raise RuntimeError(f"{name} was changed by another run") from None
        updated += 1
    return {"targets": updated, "rule_version": config.rule_version}


def parity(runtime: Runtime) -> Mapping[str, Any]:
    if not _cleared(runtime):
        return {"outcome": "nothing_to_do", "reason": f"{SAMPLED_SOURCE} is not cleared"}
    store, config = _store(runtime), steam_config()
    checked = mismatched = missing = 0
    for native_id in store.native_ids(STEAM_SLOTS):
        stored, _ = _load(runtime, state_name(config.rule_version, SAMPLED_SOURCE, native_id))
        if stored is None:
            missing += 1
            continue
        _, rebuilt = steam_app_features(store, native_id, stored.as_of)
        checked += 1
        if rebuilt != stored.state:
            mismatched += 1
            runtime.log(
                "parity_mismatch",
                severity="ERROR",
                source=SAMPLED_SOURCE,
                target=native_id,
                as_of=stored.as_of.isoformat(),
            )
    if mismatched:
        raise RuntimeError(f"{mismatched} of {checked} baseline states differ from a rebuild")
    return {"checked": checked, "without_state": missing, "rule_version": config.rule_version}
