"""REFERENCE ONLY: the quarantine fingerprint exactly as it was before content hashes
were kept for the length of a run.

`evaluation_fingerprint` and `_links` below are copied verbatim from
`src/metaspike/resolution/quarantine.py` at commit 55531e0 (U5.2). Every record's
content hash is read from the record each time, with nothing kept. Nothing in `src/`
imports this. It exists so the current function can be compared with it string for
string.

Do not edit the logic below.
"""

import hashlib
import json

from metaspike.catalogue.overrides import OverrideSet
from metaspike.domain.source_records import SourceRecord
from metaspike.resolution.models import ResolverInput
from metaspike.resolution.resolver import candidate_refs


def evaluation_fingerprint(view: ResolverInput, overrides: OverrideSet) -> str:
    """Changes exactly when something that could change the decision changes:

    - the record itself (its content hash)
    - any candidate entity, or the set of candidates
    - linking evidence from another unresolved record
    - the override that applies to this record
    """
    record = view.incoming
    candidates = set(candidate_refs(view))
    override = overrides.for_record(record.key)
    payload = {
        "record": record.content_hash,
        "candidates": sorted(
            [entity.ref, sorted(r.content_hash for r in entity.records)]
            for entity in view.entities
            if entity.ref in candidates
        ),
        "linking": sorted(other.content_hash for other in view.context if _links(other, record)),
        "bound_to": view.bound_to,
        "override": None if override is None else [override.ref, override.added_in_version],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _links(other: SourceRecord, record: SourceRecord) -> bool:
    """Whether an unresolved record points at this one, or is pointed at by it."""
    mentions = {link.value for link in other.external_ids} | {
        reference.source_id for reference in (other.parent, other.version_parent) if reference
    }
    mentioned = {link.value for link in record.external_ids} | {
        reference.source_id for reference in (record.parent, record.version_parent) if reference
    }
    return record.key.source_id in mentions or other.key.source_id in mentioned
