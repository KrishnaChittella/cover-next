"""Where an evidence input came from, and whether that can be checked.

Being able to read an input says nothing about what it is. Three questions are kept
apart, because they have different answers:

    is there data?            the source's state for a game (available, stale, ...)
    is it a real observation? only when its origin is VERIFIED
    is it invented?           when its origin is SYNTHETIC, declared or detected

    verified     A recording whose content matches a digest pinned below. The pinned
                 recordings are the response fixtures committed to this repository and
                 documented as recordings of the real source. The check is on content:
                 a directory's name or location proves nothing, and a copy with one byte
                 changed is not verified.
    synthetic    Declared by the command's own scenario, or detected from what is
                 stored: a sample written by the scenario carries its run id, and an
                 announcement written for a test points at the reserved `.invalid`
                 domain, which no real source uses, and a synthetic YouTube-shaped
                 response carries an etag that begins with `SYNTHETIC`.
    unverified   Everything else. A local store is read as it is found. Nothing in it
                 establishes who wrote it, so nothing here vouches for it.

There is no option that declares an input real. An input that claims to be verified
without a pinned digest is refused. Until samples are stamped with a checkable origin
when they are collected, locally stored samples and announcements stay unverified,
including genuine ones.

Unverified and synthetic evidence stays visible in the private reading, with its
numbers and dates exactly as the engine gives them. It is never counted as a confirmed
observation of a game.
"""

import hashlib
from collections.abc import Iterable, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Final
from urllib.parse import urlsplit

from metaspike.evidence.model import InputProvenance, Origin

# Written by the synthetic scenario on every sample it invents.
SYNTHETIC_RUN_ID: Final = "synthetic-scenario"
# Every response the synthetic creator-supply scenario writes has an etag starting so.
# A real etag is an opaque token of the source's; it never reads like this.
SYNTHETIC_ETAG_PREFIX: Final = "SYNTHETIC"
# RFC 2606 reserves this top-level domain: it can never resolve, so no real post has it.
_RESERVED_SUFFIX: Final = ".invalid"

# Content digests of the recordings this repository vouches for. A digest is over the
# sorted names and bytes of a directory's .json files. Changing a recording changes its
# digest, and a changed recording is not verified until its new digest is reviewed in.
PINNED_RECORDINGS: Final = MappingProxyType(
    {
        "260d787eda9046d7a07fbc9765309ebc1e2a95041ce8a5a51f0524c0c593b098": (
            "Wikimedia pageviews, six articles, through 2026-10-03"
        ),
        "a23a6d22394b66674fb4ef943eaadbda581434690d4144ad8b85155cadbe6533": (
            "IGDB games and Steam app list, recorded 2026-10-05"
        ),
        "f17e132457d4e1877a7b7d04cd82202d1cf83a9322ca6488e5c962b300acd775": (
            "IGDB games and Steam app list for the bootstrap entities, recorded 2026-10-05"
        ),
    }
)


def recording_digest(directory: Path) -> str:
    """The content digest of a directory of recordings. Names and bytes, nothing else."""
    digest = hashlib.sha256()
    files = sorted(p for p in directory.iterdir() if p.is_file() and p.suffix == ".json")
    for path in files:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def recording_provenance(directories: Sequence[Path], note: str = "") -> InputProvenance:
    """Verified only if EVERY directory's content matches a pinned recording."""
    reference = ", ".join(str(directory) for directory in directories)
    digests = [
        recording_digest(directory) if directory.is_dir() else "" for directory in directories
    ]
    if digests and all(digest in PINNED_RECORDINGS for digest in digests):
        return InputProvenance(
            origin=Origin.RECORDED_RESPONSE,
            reference=reference,
            note=note,
            digests=tuple(digests),
        )
    unmatched = sum(digest not in PINNED_RECORDINGS for digest in digests)
    return InputProvenance(
        origin=Origin.UNVERIFIED_FILES,
        reference=reference,
        note="; ".join(
            part
            for part in (note, f"{unmatched} of {len(digests)} match no pinned recording")
            if part
        ),
    )


def local_store_provenance(reference: str) -> InputProvenance:
    return InputProvenance(
        origin=Origin.LOCAL_STORE,
        reference=reference,
        note="read as found; nothing in a local store establishes who wrote it",
    )


def require_honest(provenance: InputProvenance) -> None:
    """Refuse an input that claims to be verified and is not."""
    if provenance.origin is not Origin.RECORDED_RESPONSE:
        return
    if not provenance.digests or any(d not in PINNED_RECORDINGS for d in provenance.digests):
        raise ValueError(
            f"{provenance.reference}: an input is verified only with the digest of a "
            "pinned recording; this one has none"
        )


def detected_synthetic(provenance: InputProvenance, why: str) -> InputProvenance:
    """The same input, known from its own content to be invented."""
    if provenance.synthetic:
        return provenance
    return InputProvenance(
        origin=Origin.SYNTHETIC,
        reference=provenance.reference,
        note=f"detected in the store: {why}",
    )


def has_synthetic_run(run_ids: Iterable[str]) -> bool:
    return any(run_id == SYNTHETIC_RUN_ID for run_id in run_ids)


def has_synthetic_etag(etags: Iterable[str | None]) -> bool:
    return any(etag is not None and etag.startswith(SYNTHETIC_ETAG_PREFIX) for etag in etags)


def is_reserved_reference(reference: str | None) -> bool:
    """Whether a reference points at the reserved `.invalid` domain."""
    if not reference:
        return False
    host = urlsplit(reference).hostname or ""
    return host == _RESERVED_SUFFIX[1:] or host.endswith(_RESERVED_SUFFIX)
