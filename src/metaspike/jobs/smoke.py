"""A safe check of what the job's own identity can and cannot do in one bucket.

It calls no source and touches no landed data. It writes one tiny object of its own
under an allowed prefix, proves that the same write is refused outside that prefix,
reports whether the identity can list the bucket, and cleans up after itself.

    user     the identity may create and delete under the prefix (operations prefixes)
    creator  the identity may only create under the prefix (landing, raw). It cannot
             delete what it wrote, by design, so the object is left for the owner
             and its name is reported

It is meant to be run by Cloud Run as the job's own service account. It needs, and
grants, no impersonation.
"""

from collections.abc import Mapping
from typing import Any

from metaspike.jobs.runtime import Runtime
from metaspike.warehouse.objects import AccessDenied, ObjectStore, ObjectStoreError

MODES = ("user", "creator")


class SmokeFailed(Exception):
    """The identity's permissions are not what the design requires."""


def _try(action: Any) -> str:
    try:
        action()
    except AccessDenied:
        return "denied"
    except ObjectStoreError as error:
        return f"error: {error}"
    return "allowed"


def smoke(
    runtime: Runtime, bucket: str, allowed_prefix: str, outside_prefix: str, mode: str = "user"
) -> Mapping[str, Any]:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if bucket not in runtime.buckets:
        raise ValueError(f"unknown bucket: {bucket}")
    if not allowed_prefix.endswith("/") or not outside_prefix.endswith("/"):
        raise ValueError("prefixes end with '/'")
    if outside_prefix.startswith(allowed_prefix) or allowed_prefix.startswith(outside_prefix):
        raise ValueError("the outside prefix must not overlap the allowed one")
    store: ObjectStore = runtime.buckets[bucket]
    inside = f"{allowed_prefix}_smoke/{runtime.run_id}.txt"
    outside = f"{outside_prefix}_smoke/{runtime.run_id}.txt"
    body = b"metaspike smoke: a harmless object, safe to delete\n"

    result: dict[str, Any] = {
        "bucket": bucket,
        "mode": mode,
        "allowed_object": inside,
        "outside_object": outside,
        "allowed_write": _try(lambda: store.create(inside, body, "text/plain")),
        "outside_write": _try(lambda: store.create(outside, body, "text/plain")),
        "list": _try(lambda: store.list(allowed_prefix)),
    }
    if result["outside_write"] == "allowed":
        # It must not exist. Remove it if we can, and fail either way.
        result["outside_cleanup"] = _try(lambda: store.delete(outside))
    cleanup = _try(lambda: store.delete(inside)) if result["allowed_write"] == "allowed" else "n/a"
    result["cleanup"] = cleanup
    if mode == "creator":
        # A create-only identity must NOT be able to delete: that is what keeps landed
        # data immutable. The object stays, and the owner removes it.
        result["left_for_owner"] = inside if cleanup == "denied" else None

    problems = []
    if result["allowed_write"] != "allowed":
        problems.append("the write inside the allowed prefix did not succeed")
    if result["outside_write"] != "denied":
        problems.append("the write outside the allowed prefix was not denied")
    if mode == "user" and cleanup != "allowed":
        problems.append("the allowed object could not be cleaned up")
    if mode == "creator" and cleanup != "denied":
        problems.append("a create-only identity was able to delete")
    result["passed"] = not problems
    runtime.log("smoke_result", severity="INFO" if not problems else "ERROR", **result)
    if problems:
        raise SmokeFailed("; ".join(problems))
    return result
