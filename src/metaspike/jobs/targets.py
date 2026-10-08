"""Which targets are to be collected: the eligibility and schedule a run plans from.

The catalogue job publishes one targets document per catalogue generation. Until it
has run there is no generation, and the plan falls back to the bootstrap entities'
Wikimedia articles, which are in the code and need no source to be asked.

A target is always for one source. A target for a source whose policy does not clear
the stage is never planned, so nothing about an uncleared source is recorded as
intent either.
"""

from collections.abc import Callable
from datetime import datetime

from pydantic import AwareDatetime, ValidationError

from metaspike.catalogue.generate import CatalogueState
from metaspike.catalogue.seed import bootstrap_catalogue
from metaspike.domain.games import Catalogue
from metaspike.domain.model import FrozenModel
from metaspike.domain.source_policy import DeploymentStage, SourcePolicy, SourcePurpose
from metaspike.domain.timeseries import Granularity
from metaspike.sources.base import SourceMode
from metaspike.sources.wikimedia import DAILY_PAGEVIEWS
from metaspike.sources.wikimedia import SOURCE_ID as WIKIMEDIA
from metaspike.storage.base import StorageCorruptionError
from metaspike.warehouse.landing import timestamp
from metaspike.warehouse.objects import ObjectNotFound, ObjectStore, PreconditionFailed

TARGETS_OBJECT = "catalogue/targets.json"
BOOTSTRAP_VERSION = "bootstrap"


class Target(FrozenModel):
    source: str
    metric: str
    granularity: Granularity
    mode: SourceMode
    source_native_id: str
    canonical_game_id: str | None = None
    mapping_version: int | None = None
    binding_ref: str | None = None


class TargetsDocument(FrozenModel):
    # The catalogue generation the targets were derived from, or "bootstrap".
    version: str
    generated_at: AwareDatetime
    targets: tuple[Target, ...] = ()
    # Every entity in the catalogue this was derived from, monitored or not.
    catalogued: int | None = None

    def for_source(self, source: str) -> tuple[Target, ...]:
        return tuple(target for target in self.targets if target.source == source)


def wikimedia_targets(catalogue: Catalogue) -> tuple[Target, ...]:
    """One target per entity that has an article of its own."""
    targets = []
    for game in catalogue.games:
        identifier = game.identifier_for(WIKIMEDIA)
        if identifier is not None:
            targets.append(
                Target(
                    source=WIKIMEDIA,
                    metric=DAILY_PAGEVIEWS.metric,
                    granularity=DAILY_PAGEVIEWS.granularity,
                    mode=SourceMode.FETCH_HISTORY,
                    source_native_id=f"{identifier.namespace}/{identifier.value}",
                    canonical_game_id=game.canonical_game_id,
                )
            )
    return tuple(targets)


def bootstrap_targets(at: datetime) -> TargetsDocument:
    return TargetsDocument(
        version=BOOTSTRAP_VERSION,
        generated_at=at,
        targets=wikimedia_targets(bootstrap_catalogue()),
    )


def targets_for(state: CatalogueState, version: str) -> TargetsDocument:
    """The targets of a catalogue generation.

    Wikimedia articles come from the entities. Steam player targets are not derived
    here yet: they need Steam catalogue records, and that source is not cleared.
    """
    return TargetsDocument(
        version=version,
        generated_at=state.generated_at,
        targets=wikimedia_targets(state.catalogue),
        catalogued=len(state.catalogue.games),
    )


def load_targets(store: ObjectStore, at: datetime) -> TargetsDocument:
    try:
        data = store.get(TARGETS_OBJECT).data
    except ObjectNotFound:
        return bootstrap_targets(at)
    try:
        return TargetsDocument.model_validate_json(data)
    except ValidationError:
        raise StorageCorruptionError(f"{TARGETS_OBJECT} is not a targets document") from None


def publish_targets(store: ObjectStore, document: TargetsDocument) -> None:
    """Replace the targets document. The only writer is the catalogue job."""
    body = document.model_dump_json().encode("utf-8")
    try:
        current = store.get(TARGETS_OBJECT)
    except ObjectNotFound:
        try:
            store.create(TARGETS_OBJECT, body)
            return
        except PreconditionFailed:
            current = store.get(TARGETS_OBJECT)
    store.replace(TARGETS_OBJECT, body, current.generation)


def cleared_targets(
    document: TargetsDocument,
    policy_for: Callable[[str], SourcePolicy],
    stage: DeploymentStage,
) -> tuple[Target, ...]:
    """The targets whose source may be collected at this stage. The rest are not planned."""
    return tuple(
        target
        for target in document.targets
        if policy_for(target.source).registration_refusal(SourcePurpose.DEMAND_SIGNALS, stage)
        is None
    )


def expected_target_rows(
    targets: tuple[Target, ...],
    slot_of: Callable[[Target], datetime],
    schedule_version: str,
    dispatch_run_id: str,
    recorded_at: datetime,
) -> list[dict[str, object]]:
    """Ledger rows: this target was intended to be collected in this slot."""
    return [
        {
            "source": target.source,
            "metric": target.metric,
            "granularity": target.granularity.value,
            "mode": target.mode.value,
            "source_native_id": target.source_native_id,
            "slot_start": timestamp(slot_of(target)),
            "schedule_version": schedule_version,
            "dispatch_run_id": dispatch_run_id,
            "recorded_at": timestamp(recorded_at),
            "tier": None,
            "due_at": None,
            "due_reason": None,
            "policy_status": None,
            "disposition": None,
            "work_order_id": None,
        }
        for target in targets
    ]
