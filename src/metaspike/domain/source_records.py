"""Source-native catalogue records: what a source says about one of ITS products.

A source record is identified only by the source's own id. It is not a canonical
entity and carries no `canonical_game_id`; whether it becomes one, joins one, or
stays unresolved is decided later by resolution and recorded as a versioned binding.

A field the source did not supply is absent (None or empty), never guessed.
"""

import hashlib
import json
from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, model_validator

from metaspike.domain.games import NonEmptyStr
from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.model import FrozenModel


class ProductType(StrEnum):
    MAIN_GAME = "main_game"
    STANDALONE_EXPANSION = "standalone_expansion"
    REMAKE = "remake"
    REMASTER = "remaster"
    EXPANDED_GAME = "expanded_game"
    PORT = "port"
    FORK = "fork"
    MOD = "mod"
    DLC = "dlc"
    EXPANSION = "expansion"
    BUNDLE = "bundle"
    PACK = "pack"
    EPISODE = "episode"
    SEASON = "season"
    UPDATE = "update"
    DEMO = "demo"
    BETA = "beta"
    PLAYTEST = "playtest"
    SOUNDTRACK = "soundtrack"
    SOFTWARE = "software"
    VIDEO = "video"
    HARDWARE = "hardware"
    UNKNOWN = "unknown"


class Admissibility(StrEnum):
    # May become a canonical entity if resolution finds it is a new game.
    ENTITY_ELIGIBLE = "entity_eligible"
    # May become an entity only through an explicit, modelled relationship
    # (a live-service mode or a mod ecosystem); never automatically.
    EXPLICIT_MODEL_ONLY = "explicit_model_only"
    # A packaging or platform variant of another game: it can only ever attach to
    # that game's entity, and never creates one of its own.
    VARIANT_OF_PARENT = "variant_of_parent"
    # A product related to a game (DLC, demo, soundtrack, bundle): never an entity.
    RELATED_PRODUCT = "related_product"
    # Not a game product at all.
    NOT_A_GAME = "not_a_game"
    # The source did not say what this is. It cannot become an entity on its own.
    UNDETERMINED = "undetermined"


_ADMISSIBILITY: dict[ProductType, Admissibility] = {
    ProductType.MAIN_GAME: Admissibility.ENTITY_ELIGIBLE,
    ProductType.STANDALONE_EXPANSION: Admissibility.ENTITY_ELIGIBLE,
    ProductType.REMAKE: Admissibility.ENTITY_ELIGIBLE,
    ProductType.REMASTER: Admissibility.ENTITY_ELIGIBLE,
    ProductType.MOD: Admissibility.EXPLICIT_MODEL_ONLY,
    ProductType.FORK: Admissibility.EXPLICIT_MODEL_ONLY,
    ProductType.EXPANDED_GAME: Admissibility.VARIANT_OF_PARENT,
    ProductType.PORT: Admissibility.VARIANT_OF_PARENT,
    ProductType.DLC: Admissibility.RELATED_PRODUCT,
    ProductType.EXPANSION: Admissibility.RELATED_PRODUCT,
    ProductType.BUNDLE: Admissibility.RELATED_PRODUCT,
    ProductType.PACK: Admissibility.RELATED_PRODUCT,
    ProductType.EPISODE: Admissibility.RELATED_PRODUCT,
    ProductType.SEASON: Admissibility.RELATED_PRODUCT,
    ProductType.UPDATE: Admissibility.RELATED_PRODUCT,
    ProductType.DEMO: Admissibility.RELATED_PRODUCT,
    ProductType.BETA: Admissibility.RELATED_PRODUCT,
    ProductType.PLAYTEST: Admissibility.RELATED_PRODUCT,
    ProductType.SOUNDTRACK: Admissibility.RELATED_PRODUCT,
    ProductType.SOFTWARE: Admissibility.NOT_A_GAME,
    ProductType.VIDEO: Admissibility.NOT_A_GAME,
    ProductType.HARDWARE: Admissibility.NOT_A_GAME,
    ProductType.UNKNOWN: Admissibility.UNDETERMINED,
}


def admissibility_of(product_type: ProductType) -> Admissibility:
    return _ADMISSIBILITY[product_type]


class DatePrecision(StrEnum):
    DAY = "day"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"
    TBD = "tbd"


class ReleaseDate(FrozenModel):
    """One release the source reports. `starts_on` is the first day of the stated period."""

    starts_on: date | None = None
    precision: DatePrecision
    platform: str | None = None
    region: str | None = None
    # The source's own wording for the kind of release, e.g. "Early Access".
    status: str | None = None

    @model_validator(mode="after")
    def _date_matches_precision(self) -> Self:
        if (self.starts_on is None) != (self.precision is DatePrecision.TBD):
            raise ValueError("a release has a date exactly when its precision is not 'tbd'")
        return self


class ExternalId(FrozenModel):
    """Another catalogue's id for this product, as asserted by this record's source."""

    source: NonEmptyStr
    value: NonEmptyStr


class SourceRecord(FrozenModel):
    key: SourceRecordKey
    title: NonEmptyStr
    alternative_titles: tuple[NonEmptyStr, ...] = ()
    product_type: ProductType
    # The source's own word for what this is, kept verbatim alongside our reading of it.
    source_category: str | None = None
    # The source's own status word, e.g. "early_access" or "cancelled".
    status: str | None = None
    release_dates: tuple[ReleaseDate, ...] = ()
    platforms: tuple[NonEmptyStr, ...] = ()
    developers: tuple[NonEmptyStr, ...] = ()
    publishers: tuple[NonEmptyStr, ...] = ()
    franchises: tuple[NonEmptyStr, ...] = ()
    external_ids: tuple[ExternalId, ...] = ()
    websites: tuple[NonEmptyStr, ...] = ()
    # The game this product belongs to (a DLC's base game, a remaster's original).
    parent: SourceRecordKey | None = None
    # The game this is an edition of (a Game of the Year edition's base game).
    version_parent: SourceRecordKey | None = None
    source_updated_at: AwareDatetime | None = None
    fetched_at: AwareDatetime

    @model_validator(mode="after")
    def _references_stay_in_source(self) -> Self:
        for reference in (self.parent, self.version_parent):
            if reference is not None and reference.source != self.key.source:
                raise ValueError("a parent reference must point into the record's own source")
            if reference == self.key:
                raise ValueError("a record cannot be its own parent")
        return self

    @property
    def admissibility(self) -> Admissibility:
        return admissibility_of(self.product_type)

    @property
    def content_hash(self) -> str:
        """Changes exactly when the source's statement about the product changes.

        Fetch time is excluded, so re-fetching an unchanged record is not a change.
        A new hash is what triggers re-evaluation of anything resolved from the record.
        """
        payload = self.model_dump(mode="json", exclude={"fetched_at"})
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ContentHashMemo:
    """The content hashes of the records one run handles, each computed once.

    `SourceRecord.content_hash` serialises the whole record and hashes it every time it
    is read. A run that fingerprints quarantined records reads the same records' hashes
    over and over. This keeps each answer for the life of the run.

    It adds no way of computing a hash: `of(record)` is `record.content_hash`, read
    once. It is keyed by the record OBJECT, not by anything about it:

    - A record is immutable, so one object has one hash for as long as it exists.
    - Any change to a record is a new object (`model_copy`, a re-parse, a re-fetch), so
      it is looked up afresh. Nothing a record says, and not its source id, is part of
      the key, so changed content under the same source id can never be answered from
      an older record's entry.
    - Each entry keeps a reference to its record. An object's identity cannot be
      reused while the object is alive, so an entry can never be read for a different
      record that happens to land at the same address.

    A memo belongs to one run and is discarded with it. It is not shared between runs,
    not global and not stored, so there is no second place a hash could go stale.
    """

    def __init__(self) -> None:
        self._held: dict[int, tuple[SourceRecord, str]] = {}
        # How often a hash was asked for, and how often it actually had to be computed.
        self.requested = 0
        self.computed = 0

    def of(self, record: SourceRecord) -> str:
        self.requested += 1
        held = self._held.get(id(record))
        if held is not None and held[0] is record:
            return held[1]
        value = record.content_hash
        self.computed += 1
        self._held[id(record)] = (record, value)
        return value

    def __len__(self) -> int:
        return len(self._held)
