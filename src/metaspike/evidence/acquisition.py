"""Acquisition records: what was fetched, when, under which policy, with which bytes.

The contract a future real collection must write beside everything it stores, so that
private evidence can later be traced to a legitimate acquisition. It is OFFLINE: nothing
here sends a request, reads a credential or contacts a source.

An acquisition record keeps, for one raw response:

    source and purpose     what was asked of which source, and why
    stage                  the deployment stage the collection ran at
    policy                 the policy's review date and a digest of its full content,
                           so a later policy change is visible, never silently applied
    requested_at,          when the request left and when the answer arrived; nothing
      received_at          in it is usable at a moment before `received_at`
    request                the method and path, which must carry no credential
    content digest         SHA-256 of the exact raw bytes, and their length
    native ids, bindings   the source's own ids, and which canonical game each was bound
                           to and from when

A record can be made only when the policy permits the purpose at the stage. Making one
for a refused source fails, so a record is itself evidence that collection was allowed
when it happened.

**Integrity is not origin.** `check_content` proves the bytes are the ones the record
was made over: nothing has changed since. It does NOT prove a source sent them. Anyone
can compute a SHA-256 of any bytes, so a digest alone authenticates nothing, and this
module adds no signature, attestation or key. Origin is established only by the
existing route: a reviewed recording whose digest is pinned in `provenance.py`.

So `acquisition_standing` never returns VERIFIED. A record over bytes that match is
UNVERIFIED: intact, permitted, of unestablished origin. A trusted live acquisition step
that could raise this (a supervised collection whose output is reviewed and pinned)
does not exist yet: TRUSTED_ACQUISITION_STEP says so, and it is NOT EXECUTED.

There is no field or argument through which a caller declares a record verified.
"""

import hashlib
import json
from datetime import date, datetime
from typing import Final, Literal

from pydantic import AwareDatetime, Field, model_validator

from metaspike.domain.model import FrozenModel
from metaspike.domain.source_policy import DeploymentStage, SourcePolicy, SourcePurpose
from metaspike.evidence.model import Standing

ACQUISITION_RECORD_FORMAT: Final = "acquisition_record_v1"
TRUSTED_ACQUISITION_STEP: Final = (
    "NOT EXECUTED: no supervised live acquisition has been run and reviewed, so no "
    "acquisition record can raise an input above unverified"
)
# Query and header names that carry a credential. A request text containing one is
# refused, because a record is kept and may be read by anyone with the store.
_CREDENTIAL_MARKERS: Final = (
    "key=",
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "authorization",
    "client_id=",
)


class AcquisitionRefused(Exception):
    """A record that must not be made: refused policy, bad times, or a credential."""


class ContentMismatch(Exception):
    """The bytes offered are not the bytes the record was made over."""


class NativeBinding(FrozenModel):
    native_id: str = Field(min_length=1)
    canonical_game_id: str = Field(min_length=1)
    # The binding as it stood at acquisition; a later rebinding is a later record.
    bound_from: AwareDatetime


class AcquisitionRecord(FrozenModel):
    record_id: str = Field(pattern=r"^acq_[0-9a-f]{32}$")
    format: Literal["acquisition_record_v1"] = ACQUISITION_RECORD_FORMAT
    source: str = Field(min_length=1)
    purpose: SourcePurpose
    stage: DeploymentStage
    policy_reviewed_at: date
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_at: AwareDatetime
    received_at: AwareDatetime
    request: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_bytes: int = Field(ge=0)
    native_ids: tuple[str, ...] = Field(min_length=1)
    bindings: tuple[NativeBinding, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> "AcquisitionRecord":
        if self.received_at < self.requested_at:
            raise ValueError("an answer cannot arrive before its request was sent")
        unknown = {b.native_id for b in self.bindings} - set(self.native_ids)
        if unknown:
            raise ValueError(f"bindings name ids the response did not hold: {sorted(unknown)}")
        if any(b.bound_from > self.received_at for b in self.bindings):
            raise ValueError("a binding that began after the acquisition is a later record")
        return self


def policy_digest(policy: SourcePolicy) -> str:
    """A digest of the policy's full content, as it stood."""
    canonical = json.dumps(policy.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _record_id(fields: dict[str, object]) -> str:
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str)
    return "acq_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def record_acquisition(
    policy: SourcePolicy,
    purpose: SourcePurpose,
    stage: DeploymentStage,
    request: str,
    raw: bytes,
    requested_at: datetime,
    received_at: datetime,
    native_ids: tuple[str, ...],
    bindings: tuple[NativeBinding, ...] = (),
) -> AcquisitionRecord:
    """A record of one response, made only if the policy allows the purpose at the stage."""
    refusal = policy.registration_refusal(purpose, stage)
    if refusal is not None:
        raise AcquisitionRefused(f"{policy.source} at {stage.value}: {refusal}")
    lowered = request.lower()
    if any(marker in lowered for marker in _CREDENTIAL_MARKERS):
        raise AcquisitionRefused("the request text carries a credential; record it without")
    fields: dict[str, object] = {
        "source": policy.source,
        "purpose": purpose,
        "stage": stage,
        "policy_reviewed_at": policy.reviewed_at,
        "policy_digest": policy_digest(policy),
        "requested_at": requested_at,
        "received_at": received_at,
        "request": request,
        "content_sha256": hashlib.sha256(raw).hexdigest(),
        "content_bytes": len(raw),
        "native_ids": native_ids,
        "bindings": bindings,
    }
    try:
        draft = AcquisitionRecord.model_validate({**fields, "record_id": "acq_" + "0" * 32})
    except ValueError as error:
        raise AcquisitionRefused(str(error)) from None
    payload = draft.model_dump(mode="json", exclude={"record_id"})
    return draft.model_copy(update={"record_id": _record_id(payload)})


def check_content(record: AcquisitionRecord, raw: bytes) -> None:
    """Refuse bytes that are not the ones the record was made over. Integrity only."""
    if len(raw) != record.content_bytes or hashlib.sha256(raw).hexdigest() != (
        record.content_sha256
    ):
        raise ContentMismatch(f"{record.record_id}: the content offered is not what was acquired")


def policy_changed(record: AcquisitionRecord, policy: SourcePolicy) -> bool:
    """Whether the source's policy is no longer the one the acquisition ran under."""
    return policy.source != record.source or policy_digest(policy) != record.policy_digest


def usable_at(record: AcquisitionRecord, moment: datetime) -> bool:
    """Whether a reading at `moment` may use this acquisition: only after it arrived."""
    return record.received_at <= moment


def acquisition_standing(record: AcquisitionRecord, raw: bytes) -> Standing:
    """UNVERIFIED for intact content. Never VERIFIED: integrity does not establish origin."""
    check_content(record, raw)
    return Standing.UNVERIFIED
