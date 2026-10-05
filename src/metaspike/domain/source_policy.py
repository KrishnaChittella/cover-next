"""Source compliance model. A permission that is not known to be granted is not granted."""

from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from metaspike.domain.games import NonEmptyStr
from metaspike.domain.model import FrozenModel


class Permission(StrEnum):
    ALLOWED = "allowed"
    RESTRICTED = "restricted"
    PROHIBITED = "prohibited"
    UNKNOWN = "unknown"

    @property
    def permits(self) -> bool:
        return self is Permission.ALLOWED


class Requirement(StrEnum):
    REQUIRED = "required"
    NOT_REQUIRED = "not_required"
    UNKNOWN = "unknown"


class ApprovalStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class SourcePolicy(FrozenModel):
    source: NonEmptyStr
    raw_storage_allowed: Permission
    # Maximum days raw payloads may be kept. None means the source states no limit;
    # it is only meaningful when raw_storage_allowed permits storage at all.
    retention_days: int | None = Field(default=None, ge=0)
    historical_storage_allowed: Permission
    derived_metrics_allowed: Permission
    commercial_use_allowed: Permission
    redistribution_allowed: Permission
    attribution_required: Requirement
    attribution_text: str | None = None
    approval_required: Requirement
    approval_status: ApprovalStatus
    reviewed_at: date
    references: tuple[NonEmptyStr, ...] = Field(min_length=1)
    notes: str = ""

    @model_validator(mode="after")
    def _approval_is_consistent(self) -> Self:
        needs_approval = self.approval_required is not Requirement.NOT_REQUIRED
        if needs_approval and self.approval_status is ApprovalStatus.NOT_REQUIRED:
            raise ValueError("approval_status cannot be 'not_required' when approval may be needed")
        return self

    @property
    def collection_permitted(self) -> bool:
        """We may collect and score only if derived metrics are allowed and approval is settled."""
        approved = self.approval_status in (ApprovalStatus.NOT_REQUIRED, ApprovalStatus.APPROVED)
        return self.derived_metrics_allowed.permits and approved

    @property
    def public_history_permitted(self) -> bool:
        """Publishing a source's own daily values is redistribution of stored history."""
        return self.redistribution_allowed.permits and self.historical_storage_allowed.permits
