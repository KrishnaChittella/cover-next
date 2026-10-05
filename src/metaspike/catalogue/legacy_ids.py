"""The one-time migration from the retired `msg_NNNN` ids to UUIDv4 canonical ids.

THIS TABLE IS FROZEN. It was generated once, on 2026-10-04, and is the permanent
record of which canonical entity each legacy id became. Never regenerate it, never
edit a value, and never add a row: new entities get a fresh UUID and have no legacy
id. `verify_legacy_mapping` fails if the table is altered in any way.
"""

import hashlib
import json
import re
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Final

from metaspike.domain.games import CANONICAL_ID_PATTERN
from metaspike.domain.identity import AliasKind, IdentityAlias, IdentityLedger

MIGRATED_AT: Final = datetime(2026, 10, 4, tzinfo=UTC)
_LEGACY_ID = re.compile(r"^msg_[0-9]{4}$")
_EXPECTED_COUNT = 15
_EXPECTED_SHA256 = "bc254cf7d70e417b1bbfa2e0aa74146f3783376bf61bec14778ba6d3f160f551"

LEGACY_ID_TO_CANONICAL: Final = MappingProxyType(
    {
        "msg_0001": "780b4850-6248-4a81-8fe0-7f0762376cfc",
        "msg_0002": "9abc9607-45ea-4441-9da2-3eb00af9d4a3",
        "msg_0003": "bdea9df5-8fdd-4bcb-8acb-9e9fe2a2d8d2",
        "msg_0004": "204f2af0-0dab-438f-b3b3-024fd4d2be1c",
        "msg_0005": "367ff339-1e0f-4c81-aa18-ee9e8715c74f",
        "msg_0006": "657b1573-3156-408d-b997-941071d42c56",
        "msg_0007": "3c37a62c-1718-49ce-bc4a-6b3be70faf02",
        "msg_0008": "cc600051-5a1b-458f-8cd0-cf1324bc44df",
        "msg_0009": "0ace3a7a-a923-4f5b-9e9a-fc8a88e8c1c9",
        "msg_0010": "f2f36b97-9664-4d59-a749-d2bba4f57e05",
        "msg_0011": "d1c9712e-1691-49c0-be37-a3ea0dd06760",
        "msg_0012": "86d32c9a-afbe-4220-94c3-52dbf2489f04",
        "msg_0013": "30288c54-5c98-45ed-975a-8addc8c776c3",
        "msg_0014": "05773a27-941a-4175-8243-3a5641b255d3",
        "msg_0015": "2128bf4d-7afc-4d2b-9803-3afae37bb7f3",
    }
)


class LegacyMappingError(Exception):
    """The frozen legacy-id table has been altered."""


def mapping_digest() -> str:
    canonical = json.dumps(dict(LEGACY_ID_TO_CANONICAL), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_legacy_mapping() -> None:
    mapping = LEGACY_ID_TO_CANONICAL
    if len(mapping) != _EXPECTED_COUNT:
        raise LegacyMappingError(f"expected {_EXPECTED_COUNT} legacy ids, found {len(mapping)}")
    if any(not _LEGACY_ID.fullmatch(legacy) for legacy in mapping):
        raise LegacyMappingError("a legacy id is not in the msg_NNNN format")
    if any(not re.fullmatch(CANONICAL_ID_PATTERN, canonical) for canonical in mapping.values()):
        raise LegacyMappingError("a canonical id is not a lowercase UUIDv4")
    if len(set(mapping.values())) != len(mapping):
        raise LegacyMappingError("two legacy ids map to the same canonical id")
    if mapping_digest() != _EXPECTED_SHA256:
        raise LegacyMappingError("the frozen legacy-id table has been modified")


def canonical_id_for_legacy(legacy_id: str) -> str:
    try:
        return LEGACY_ID_TO_CANONICAL[legacy_id]
    except KeyError:
        raise LegacyMappingError(f"unknown legacy id: {legacy_id}") from None


def legacy_identity_ledger() -> IdentityLedger:
    """A ledger in which every legacy id resolves to its canonical id."""
    verify_legacy_mapping()
    return IdentityLedger(
        aliases=tuple(
            IdentityAlias(
                alias=legacy,
                canonical_game_id=canonical,
                kind=AliasKind.LEGACY,
                valid_from=MIGRATED_AT,
                reason="msg_NNNN id format retired in favour of UUIDv4",
                decision_ref="migration:legacy-ids-2026-10-04",
            )
            for legacy, canonical in LEGACY_ID_TO_CANONICAL.items()
        )
    )
