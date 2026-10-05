"""Source compliance model. A permission that is not known to be granted is not granted.

MetaSpike is engineered to be commercial-capable. `commercial_use_allowed` must be
ALLOWED before a source may be part of a commercial production configuration;
anything unclear stays UNKNOWN or RESTRICTED until permission is obtained.
"""

from datetime import date, timedelta
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


class LimitBasis(StrEnum):
    """Where a number came from, so an assumption is never mistaken for a guarantee."""

    DOCUMENTED = "documented"
    SELF_IMPOSED = "self_imposed"
    OBSERVED = "observed"


class RateLimit(FrozenModel):
    """Short-window request ceiling: at most `max_requests` every `per_seconds`."""

    max_requests: int = Field(ge=1)
    per_seconds: int = Field(ge=1)
    max_concurrency: int = Field(ge=1)
    basis: LimitBasis
    note: str = ""

    @property
    def min_interval_seconds(self) -> float:
        """Smallest gap between requests that can never exceed the limit."""
        return self.per_seconds / self.max_requests


class QuotaPeriod(StrEnum):
    DAY = "day"
    MONTH = "month"


class QuotaBudget(FrozenModel):
    """Long-window allowance, in the source's own unit (requests, cost units, ...)."""

    limit: int = Field(ge=1)
    unit: NonEmptyStr
    period: QuotaPeriod
    basis: LimitBasis
    note: str = ""


class SourceOperations(FrozenModel):
    """How the source may be called and how soon its data appears.

    These are constraints the scheduler must obey, kept beside the legal permissions
    so no collector can be configured without them.
    """

    rate_limit: RateLimit
    # None means the source documents no long-window quota.
    quota: QuotaBudget | None = None
    # Longest a fetched response may be reused before re-fetching. None means the
    # source states no limit; it never overrides raw-storage or retention rules.
    cache_max_age_seconds: int | None = Field(default=None, ge=0)
    # How long after a period ends its data is expected to be published.
    expected_publication_lag_seconds: int = Field(ge=0)
    publication_lag_basis: LimitBasis

    @property
    def expected_publication_lag(self) -> timedelta:
        return timedelta(seconds=self.expected_publication_lag_seconds)


class FieldRule(FrozenModel):
    """Permissions for one field of a source's data, where they differ from the source's.

    `field` is a metric id for observation values, or a source field name otherwise.
    """

    field: NonEmptyStr
    storage_allowed: Permission
    display_allowed: Permission
    note: str = ""


class DisplayRules(FrozenModel):
    """What the source requires wherever its data is shown to a user."""

    link_back_required: Requirement
    branding_required: Requirement
    note: str = ""


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
    operations: SourceOperations
    display: DisplayRules
    field_rules: tuple[FieldRule, ...] = ()
    reviewed_at: date
    references: tuple[NonEmptyStr, ...] = Field(min_length=1)
    notes: str = ""

    @model_validator(mode="after")
    def _check_policy(self) -> Self:
        needs_approval = self.approval_required is not Requirement.NOT_REQUIRED
        if needs_approval and self.approval_status is ApprovalStatus.NOT_REQUIRED:
            raise ValueError("approval_status cannot be 'not_required' when approval may be needed")
        fields = [rule.field for rule in self.field_rules]
        if len(fields) != len(set(fields)):
            raise ValueError("more than one rule for the same field")
        return self

    @property
    def collection_permitted(self) -> bool:
        """We may collect and score only if derived metrics are allowed and approval is settled."""
        approved = self.approval_status in (ApprovalStatus.NOT_REQUIRED, ApprovalStatus.APPROVED)
        return self.derived_metrics_allowed.permits and approved

    @property
    def commercial_production_permitted(self) -> bool:
        """Whether the source may be part of a monetized production configuration."""
        return self.collection_permitted and self.commercial_use_allowed.permits

    @property
    def public_history_permitted(self) -> bool:
        """Publishing a source's own values is redistribution of stored history."""
        return self.redistribution_allowed.permits and self.historical_storage_allowed.permits

    def field_storage_permitted(self, field: str) -> bool:
        """A field rule can only narrow what the source-level policy allows."""
        rule = self._rule(field)
        allowed = self.historical_storage_allowed.permits
        return allowed and (rule is None or rule.storage_allowed.permits)

    def field_display_permitted(self, field: str) -> bool:
        rule = self._rule(field)
        return self.public_history_permitted and (rule is None or rule.display_allowed.permits)

    def _rule(self, field: str) -> FieldRule | None:
        for rule in self.field_rules:
            if rule.field == field:
                return rule
        return None
