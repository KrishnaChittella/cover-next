"""A safe check of what the job's own identity can and cannot do in one bucket.

It calls no source and touches no landed data. It probes one bucket with tiny
objects of its own and compares what happened with what least privilege requires
for the SHAPE of grant the identity holds there:

    shape          grant                          create read overwrite delete list
    user           objectUser on a prefix          yes    yes  yes       yes    no
    creator        objectCreator on a prefix       yes    no   no        no     no
    viewer         objectViewer on a prefix        no     yes  -         -      no
    bucket_viewer  objectViewer on the bucket      no     yes  -         -      yes
    bucket_user    objectUser on the bucket        yes    yes  yes       yes    yes

Every prefix shape also requires a create OUTSIDE the prefix to be denied.

A denial is not a failure when it is what the grant is meant to do. Each probe is
therefore given one of four verdicts, and only the last two fail the check:

    ALLOWED             permitted, and meant to be
    EXPECTED_DENIAL     refused, and meant to be
    UNEXPECTED_FAILURE  refused or broken, and meant to be permitted
    UNEXPECTED_ALLOW    permitted, and meant to be refused

A create-only identity cannot delete what it wrote: that is the immutability of
landed data, and granting delete to make the check tidy would remove the very
property being checked. So its refused delete is an EXPECTED_DENIAL, the object is
left where it is, and the result names it for an already-authorized administrator
to remove afterwards. An identity that legitimately holds delete cleans up itself.

Listing is reported and judged, but a prefix-conditional identity being able to list
is an observation for review rather than a breach of the write boundary, so by
default it does not fail the check. `strict_list` makes it fail.

It is meant to be run by Cloud Run as the job's own service account. It needs, and
grants, no impersonation.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from metaspike.jobs.runtime import Runtime
from metaspike.warehouse.objects import (
    AccessDenied,
    ObjectNotFound,
    ObjectStore,
    ObjectStoreError,
)

ALLOWED = "ALLOWED"
EXPECTED_DENIAL = "EXPECTED_DENIAL"
UNEXPECTED_FAILURE = "UNEXPECTED_FAILURE"
UNEXPECTED_ALLOW = "UNEXPECTED_ALLOW"
NOT_APPLICABLE = "NOT_APPLICABLE"

RUNTIME = "runtime identity"
ADMINISTRATOR = "administrator, after the run"
NOBODY = "nothing to clean up"


@dataclass(frozen=True)
class Shape:
    """What one kind of grant must permit. None means the probe does not apply."""

    create: bool
    read: bool
    overwrite: bool | None
    delete: bool | None
    list: bool
    # Whether a write outside the prefix can be probed (it cannot for a whole bucket).
    bounded: bool


SHAPES: dict[str, Shape] = {
    "user": Shape(create=True, read=True, overwrite=True, delete=True, list=False, bounded=True),
    "creator": Shape(
        create=True, read=False, overwrite=False, delete=False, list=False, bounded=True
    ),
    "viewer": Shape(create=False, read=True, overwrite=None, delete=None, list=False, bounded=True),
    "bucket_viewer": Shape(
        create=False, read=True, overwrite=None, delete=None, list=True, bounded=False
    ),
    "bucket_user": Shape(
        create=True, read=True, overwrite=True, delete=True, list=True, bounded=False
    ),
}
MODES = tuple(SHAPES)


class SmokeFailed(Exception):
    """The identity's permissions are not what the design requires."""

    def __init__(self, message: str, result: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.result = result


def _observe(action: Callable[[], Any]) -> str:
    """What the service did: 'allowed', 'denied', or an error that is neither."""
    try:
        action()
    except AccessDenied:
        return "denied"
    except ObjectNotFound:
        return "error: not found"
    except ObjectStoreError as error:
        return f"error: {error}"
    return "allowed"


def _verdict(expected_allowed: bool, observed: str) -> str:
    if observed == "allowed":
        return ALLOWED if expected_allowed else UNEXPECTED_ALLOW
    if observed == "denied":
        return UNEXPECTED_FAILURE if expected_allowed else EXPECTED_DENIAL
    return UNEXPECTED_FAILURE  # neither an answer nor a refusal: something is wrong


def smoke(
    runtime: Runtime,
    bucket: str,
    allowed_prefix: str,
    outside_prefix: str | None = None,
    mode: str = "user",
    *,
    read_object: str | None = None,
    expect_read: bool | None = None,
    expect_list: bool | None = None,
    strict_list: bool = False,
) -> Mapping[str, Any]:
    """Probe one bucket as this identity. Raises SmokeFailed on any unexpected outcome.

    `expect_read` and `expect_list` override the shape where ANOTHER reviewed grant
    independently gives the identity that ability on the same bucket (the dispatcher,
    for example, may read the whole operations bucket while writing only its prefix).

    Read is probed on an object that EXISTS: the one this run created, or, for an
    identity that cannot create, `read_object`, which an administrator places
    beforehand. A missing object proves nothing, because the service answers
    "forbidden" rather than "not found" to an identity that may not list.
    """
    if mode not in SHAPES:
        raise ValueError(f"mode must be one of {MODES}")
    shape = SHAPES[mode]
    if bucket not in runtime.buckets:
        raise ValueError(f"unknown bucket: {bucket}")
    if allowed_prefix and not allowed_prefix.endswith("/"):
        raise ValueError("a prefix ends with '/'")
    if shape.bounded:
        if not allowed_prefix:
            raise ValueError(f"mode '{mode}' checks a prefix; give one")
        if not outside_prefix or not outside_prefix.endswith("/"):
            raise ValueError(f"mode '{mode}' needs an outside prefix ending with '/'")
        if outside_prefix.startswith(allowed_prefix) or allowed_prefix.startswith(outside_prefix):
            raise ValueError("the outside prefix must not overlap the allowed one")
    elif outside_prefix:
        raise ValueError(f"mode '{mode}' covers the whole bucket; there is no outside")

    store: ObjectStore = runtime.buckets[bucket]
    inside = f"{allowed_prefix}_smoke/{runtime.run_id}.txt"
    body = b"metaspike smoke: a harmless object, safe to delete\n"
    read_allowed = shape.read if expect_read is None else expect_read
    list_allowed = shape.list if expect_list is None else expect_list
    probes: dict[str, dict[str, Any]] = {}

    def probe(name: str, expected_allowed: bool, observed: str, required: bool = True) -> str:
        verdict = _verdict(expected_allowed, observed)
        probes[name] = {
            "expected": "allowed" if expected_allowed else "denied",
            "observed": observed,
            "verdict": verdict,
            "required": required,
        }
        return verdict

    generation: list[int] = []
    created = probe(
        "create_inside",
        shape.create,
        _observe(lambda: generation.append(store.create(inside, body, "text/plain"))),
    )
    exists = created in (ALLOWED, UNEXPECTED_ALLOW)

    outside = None
    if shape.bounded and outside_prefix:
        outside = f"{outside_prefix}_smoke/{runtime.run_id}.txt"
        leaked = probe(
            "create_outside", False, _observe(lambda: store.create(outside, body, "text/plain"))
        )
        if leaked == UNEXPECTED_ALLOW:
            # It must not exist. Remove it if we can; the check fails either way.
            probes["create_outside"]["removed"] = _observe(lambda: store.delete(outside))

    readable = inside if exists else read_object
    if readable is not None:
        probe("read", read_allowed, _observe(lambda: store.get(readable)))
    else:
        probes["read"] = {
            "expected": "allowed" if read_allowed else "denied",
            "observed": "not probed: no existing object to read",
            "verdict": NOT_APPLICABLE,
            "required": False,
        }
    probe("list", list_allowed, _observe(lambda: store.list(allowed_prefix)), required=strict_list)

    cleanup: dict[str, Any] = {"actor": NOBODY, "object": None}
    if exists:
        if shape.overwrite is not None:
            # A real overwrite: replace the object we just created, at its generation.
            probe(
                "overwrite",
                shape.overwrite,
                _observe(lambda: store.replace(inside, body, generation[0], "text/plain")),
            )
        deleted = probe("delete", bool(shape.delete), _observe(lambda: store.delete(inside)))
        if deleted in (ALLOWED, UNEXPECTED_ALLOW):
            cleanup = {"actor": RUNTIME, "object": inside, "done": True}
        else:
            # Still there. For a create-only identity this is the design working.
            cleanup = {"actor": ADMINISTRATOR, "object": inside, "done": False}

    unexpected = sorted(
        name
        for name, outcome in probes.items()
        if outcome["required"] and outcome["verdict"] in (UNEXPECTED_FAILURE, UNEXPECTED_ALLOW)
    )
    advisory = sorted(
        name
        for name, outcome in probes.items()
        if not outcome["required"] and outcome["verdict"] in (UNEXPECTED_FAILURE, UNEXPECTED_ALLOW)
    )
    result: dict[str, Any] = {
        "bucket": bucket,
        "mode": mode,
        "allowed_prefix": allowed_prefix,
        "outside_prefix": outside_prefix,
        "probes": probes,
        "cleanup": cleanup,
        "unexpected": unexpected,
        "for_review": advisory,
        "passed": not unexpected,
    }
    runtime.log("smoke_result", severity="INFO" if not unexpected else "ERROR", **result)
    if unexpected:
        detail = "; ".join(
            f"{name}: expected {probes[name]['expected']}, observed {probes[name]['observed']}"
            for name in unexpected
        )
        raise SmokeFailed(detail, result)
    return result
