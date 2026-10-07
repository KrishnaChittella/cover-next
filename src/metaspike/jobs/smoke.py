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

SUITES. `smoke_suite` runs every probe set of one runtime identity in a single
process, so one Cloud Run execution covers that identity's whole storage matrix.
Every object it touches is named from an explicit run identifier, it stops at the
first unexpected verdict, and it returns one result document. In a suite, listing is
required to be as expected.
"""

import re
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


def _observe(action: Callable[[], Any]) -> tuple[str, str | None]:
    """What the service did, and its own short account of it.

    Returns ('allowed' | 'denied' | 'absent' | 'error', detail). The detail is the
    status our storage port reports, such as "HTTP 403". It never holds a token.
    """
    try:
        action()
    except AccessDenied as error:
        return "denied", _status(error)
    except ObjectNotFound:
        return "absent", "HTTP 404"
    except ObjectStoreError as error:
        return "error", _status(error)
    return "allowed", None


def _status(error: Exception) -> str | None:
    found = re.search(r"HTTP \d{3}", str(error))
    return found.group(0) if found else type(error).__name__


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
    inside_object: str | None = None,
    outside_object: str | None = None,
    list_prefix: str | None = None,
    stop_at_first_unexpected: bool = False,
    raise_on_failure: bool = True,
) -> Mapping[str, Any]:
    """Probe one bucket as this identity. Raises SmokeFailed on any unexpected outcome.

    `expect_read` and `expect_list` override the shape where ANOTHER reviewed grant
    independently gives the identity that ability on the same bucket (the dispatcher,
    for example, may read the whole operations bucket while writing only its prefix).

    Read is probed on an object that EXISTS: the one this run created, or, for an
    identity that cannot create, `read_object`, which an administrator places
    beforehand. A missing object proves nothing, because the service answers
    "forbidden" rather than "not found" to an identity that may not list.

    With `stop_at_first_unexpected` no further probe is made after an unexpected
    verdict; whatever was created is left and reported for an administrator.
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
    inside = inside_object or f"{allowed_prefix}_smoke/{runtime.run_id}.txt"
    outside = None
    if shape.bounded and outside_prefix:
        outside = outside_object or f"{outside_prefix}_smoke/{runtime.run_id}.txt"
    body = b"metaspike-storage-smoke\n"
    read_allowed = shape.read if expect_read is None else expect_read
    list_allowed = shape.list if expect_list is None else expect_list
    probes: dict[str, dict[str, Any]] = {}
    state: dict[str, Any] = {"exists": False, "generation": None, "stopped": False}

    def probe(
        name: str,
        expected_allowed: bool,
        action: Callable[[], Any],
        target: str | None,
        required: bool = True,
    ) -> str:
        observed, detail = _observe(action)
        verdict = _verdict(expected_allowed, observed)
        if observed == "absent":
            # Not a permission answer at all: the object we were to read is missing.
            observed = "fixture absent"
        probes[name] = {
            "operation": name,
            "expected": "allowed" if expected_allowed else "denied",
            "observed": observed,
            "verdict": verdict,
            "status": detail,
            "object": target,
            "required": required,
        }
        if required and verdict in (UNEXPECTED_FAILURE, UNEXPECTED_ALLOW):
            state["stopped"] = stop_at_first_unexpected
        return verdict

    def create_inside() -> None:
        def create() -> None:
            state["generation"] = store.create(inside, body, "text/plain")

        verdict = probe("create_inside", shape.create, create, inside)
        state["exists"] = verdict in (ALLOWED, UNEXPECTED_ALLOW)

    def create_outside() -> None:
        if outside is None:
            return
        target = outside
        leaked = probe(
            "create_outside", False, lambda: store.create(target, body, "text/plain"), target
        )
        if leaked == UNEXPECTED_ALLOW:
            state["leaked"] = target
            if not stop_at_first_unexpected:
                # It must not exist. Remove it if we can; the check fails either way.
                probes["create_outside"]["removed"] = _observe(lambda: store.delete(target))[0]

    def read() -> None:
        readable = inside if state["exists"] else read_object
        if readable is None:
            probes["read"] = {
                "operation": "read",
                "expected": "allowed" if read_allowed else "denied",
                "observed": "not probed: no existing object to read",
                "verdict": NOT_APPLICABLE,
                "status": None,
                "object": None,
                "required": False,
            }
            return
        probe("read", read_allowed, lambda: store.get(readable), readable)
        fixture = readable == read_object and not state["exists"]
        if fixture and probes["read"]["observed"] == "denied" and not list_allowed:
            # An identity that may not list is told "forbidden" for a missing object
            # too. From inside, the two cannot be told apart; say so.
            probes["read"]["note"] = (
                "denied, or the fixture is absent: this identity cannot distinguish them. "
                "An administrator must confirm the fixture exists."
            )

    def listing() -> None:
        # Permission to list is a property of the bucket, so a narrow prefix asks the
        # same question as a wide one and returns far less.
        under = allowed_prefix if list_prefix is None else list_prefix
        probe("list", list_allowed, lambda: store.list(under), None, required=strict_list)

    def overwrite() -> None:
        if state["exists"] and shape.overwrite is not None:
            # A real overwrite: replace the object we just created, at its generation.
            probe(
                "overwrite",
                shape.overwrite,
                lambda: store.replace(inside, body, state["generation"], "text/plain"),
                inside,
            )

    def delete() -> None:
        if state["exists"]:
            verdict = probe("delete", bool(shape.delete), lambda: store.delete(inside), inside)
            state["exists"] = verdict not in (ALLOWED, UNEXPECTED_ALLOW)
            state["delete_tried"] = True

    for step in (create_inside, create_outside, read, listing, overwrite, delete):
        if state["stopped"]:
            break
        step()

    # What is left behind, and who removes it.
    left = []
    if state["exists"]:
        left.append(inside)
    if state.get("leaked") and probes["create_outside"].get("removed") != "allowed":
        left.append(state["leaked"])
    if left:
        cleanup: dict[str, Any] = {
            "actor": ADMINISTRATOR,
            "object": left[0],
            "objects": left,
            "done": False,
        }
    elif state.get("delete_tried"):
        cleanup = {"actor": RUNTIME, "object": inside, "objects": [], "done": True}
    else:
        cleanup = {"actor": NOBODY, "object": None, "objects": []}

    unexpected = [
        name
        for name, outcome in probes.items()
        if outcome["required"] and outcome["verdict"] in (UNEXPECTED_FAILURE, UNEXPECTED_ALLOW)
    ]
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
        "unexpected": sorted(unexpected),
        "first_unexpected": unexpected[0] if unexpected else None,
        "stopped_early": bool(state["stopped"]),
        "for_review": advisory,
        "passed": not unexpected,
    }
    runtime.log("smoke_result", severity="INFO" if not unexpected else "ERROR", **result)
    if unexpected and raise_on_failure:
        detail = "; ".join(
            f"{name}: expected {probes[name]['expected']}, observed {probes[name]['observed']}"
            for name in sorted(unexpected)
        )
        raise SmokeFailed(detail, result)
    return result


# --- suites: one runtime identity's whole storage matrix ------------------------------------

# A prefix no identity holds anything under, for the "write outside must be denied" probe.
OUTSIDE = "_outside/"
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,62}$")


@dataclass(frozen=True)
class ProbeSet:
    bucket: str
    prefix: str  # "" for the whole bucket
    mode: str
    # Where ANOTHER reviewed grant gives the identity this ability on the same bucket.
    expect_read: bool | None = None
    expect_list: bool | None = None

    @property
    def needs_fixture(self) -> bool:
        return not SHAPES[self.mode].create


def _sets(*entries: tuple[str, str, str]) -> tuple[ProbeSet, ...]:
    return tuple(ProbeSet(bucket=b, prefix=p, mode=m) for b, p, m in entries)


# The accepted storage matrix: every bucket grant each runtime identity holds.
PROFILES: dict[str, tuple[ProbeSet, ...]] = {
    "ms-dispatch": (
        # It also reads the whole operations bucket, so read and list are allowed
        # there. Writing outside dispatch/ must still be denied.
        ProbeSet("ops", "dispatch/", "user", expect_read=True, expect_list=True),
        ProbeSet("landing", "dispatch/", "creator"),
    ),
    "ms-steam-players": _sets(
        ("ops", "steam_current_players/", "user"),
        ("ops", "quota/steam_web_api/", "user"),
        ("landing", "steam_current_players/", "creator"),
        ("state", "catalogue/", "viewer"),
    ),
    "ms-wikimedia": _sets(
        ("ops", "wikimedia_pageviews/", "user"),
        ("landing", "wikimedia_pageviews/", "creator"),
        ("raw", "wikimedia_pageviews/", "creator"),
        ("state", "catalogue/", "viewer"),
    ),
    "ms-catalogue": _sets(
        ("landing", "igdb/", "creator"),
        ("landing", "steam_catalogue/", "creator"),
        ("landing", "catalogue/", "creator"),
        ("raw", "igdb/", "creator"),
        ("ops", "catalogue/", "user"),
        ("ops", "quota/steam_web_api/", "user"),
        ("state", "catalogue/", "user"),
        ("tokens", "", "bucket_user"),
    ),
    "ms-warehouse": _sets(
        ("landing", "", "bucket_viewer"),
        ("raw", "", "bucket_viewer"),
        ("ops", "warehouse/", "user"),
    ),
    "ms-features": _sets(
        ("state", "baseline/", "user"),
        ("ops", "features/", "user"),
    ),
    "ms-parity": _sets(
        ("state", "baseline/", "viewer"),
        ("ops", "parity/", "user"),
    ),
}


def checked_run_id(run_id: str) -> str:
    """A run identifier that is safe as one path component, or ValueError."""
    if not _SAFE_RUN_ID.fullmatch(run_id) or ".." in run_id:
        raise ValueError(
            "a run id is 3 to 63 letters, digits, '.', '_' or '-', starting with a letter or digit"
        )
    return run_id


def _base(run_id: str, prefix: str) -> str:
    return f"{prefix}_smoke/{checked_run_id(run_id)}/"


def probe_object(run_id: str, profile: str, probe_set: ProbeSet) -> str:
    """The object a suite creates inside an allowed prefix."""
    return f"{_base(run_id, probe_set.prefix)}{profile}-probe.txt"


def outside_object(run_id: str, profile: str, probe_set: ProbeSet) -> str:
    """The object a suite tries, and must fail, to create outside its prefix."""
    inside = probe_set.prefix.rstrip("/").replace("/", "-")
    return f"{_base(run_id, OUTSIDE)}{profile}-{inside}-outside.txt"


def fixture_object(run_id: str, profile: str, probe_set: ProbeSet) -> str:
    """The object an administrator places for a read-only probe to read."""
    return f"{_base(run_id, probe_set.prefix)}{profile}-fixture.txt"


def fixtures_for(run_id: str, profile: str) -> tuple[tuple[str, str], ...]:
    """Every (bucket, object) an administrator must place before this profile runs."""
    return tuple(
        (probe_set.bucket, fixture_object(run_id, profile, probe_set))
        for probe_set in PROFILES[profile]
        if probe_set.needs_fixture
    )


def smoke_suite(runtime: Runtime, profile: str, run_id: str) -> Mapping[str, Any]:
    """Run every probe set of one runtime identity, stopping at the first surprise.

    Returns one result document whether the suite passed or not; `overall` says
    which. It never raises for a permission outcome.
    """
    if profile not in PROFILES:
        raise ValueError(f"profile must be one of {sorted(PROFILES)}")
    checked_run_id(run_id)
    rows: list[dict[str, Any]] = []
    cleanup: list[dict[str, Any]] = []
    first: dict[str, Any] | None = None
    completed = 0
    for probe_set in PROFILES[profile]:
        shape = SHAPES[probe_set.mode]
        fixture = fixture_object(run_id, profile, probe_set) if probe_set.needs_fixture else None
        outcome = smoke(
            runtime,
            probe_set.bucket,
            probe_set.prefix,
            OUTSIDE if shape.bounded else None,
            probe_set.mode,
            read_object=fixture,
            expect_read=probe_set.expect_read,
            expect_list=probe_set.expect_list,
            strict_list=True,
            inside_object=probe_object(run_id, profile, probe_set),
            outside_object=outside_object(run_id, profile, probe_set),
            list_prefix=_base(run_id, probe_set.prefix),
            stop_at_first_unexpected=True,
            raise_on_failure=False,
        )
        for result in outcome["probes"].values():
            rows.append(
                {
                    "bucket": probe_set.bucket,
                    "prefix": probe_set.prefix,
                    "shape": probe_set.mode,
                    **{k: result.get(k) for k in ("operation", "expected", "status", "object")},
                    "actual": result["observed"],
                    "verdict": result["verdict"],
                    **({"note": result["note"]} if "note" in result else {}),
                }
            )
        for name in outcome["cleanup"].get("objects", []):
            cleanup.append(
                {
                    "bucket": probe_set.bucket,
                    "object": name,
                    "cleanup_required": True,
                    "cleanup_actor": "administrator",
                }
            )
        if fixture is not None:
            cleanup.append(
                {
                    "bucket": probe_set.bucket,
                    "object": fixture,
                    "cleanup_required": True,
                    "cleanup_actor": "administrator",
                    "fixture": True,
                }
            )
        if not outcome["passed"]:
            name = outcome["first_unexpected"]
            first = {
                "bucket": probe_set.bucket,
                "prefix": probe_set.prefix,
                "shape": probe_set.mode,
                **{k: v for k, v in outcome["probes"][name].items() if k != "required"},
            }
            break
        completed += 1
    counts = {
        verdict: sum(1 for row in rows if row["verdict"] == verdict)
        for verdict in (
            ALLOWED,
            EXPECTED_DENIAL,
            UNEXPECTED_FAILURE,
            UNEXPECTED_ALLOW,
            NOT_APPLICABLE,
        )
    }
    document: dict[str, Any] = {
        "run_id": run_id,
        "profile": profile,
        "job": runtime.environ.get("CLOUD_RUN_JOB"),
        "execution": runtime.environ.get("CLOUD_RUN_EXECUTION"),
        "overall": "PASS" if first is None else "FAIL",
        "probe_sets_planned": len(PROFILES[profile]),
        "probe_sets_completed": completed,
        "first_unexpected": first,
        "counts": counts,
        "probes": rows,
        "cleanup": cleanup,
    }
    runtime.log("smoke_suite_result", severity="INFO" if first is None else "ERROR", **document)
    return document
