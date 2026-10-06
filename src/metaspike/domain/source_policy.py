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


class DeploymentStage(StrEnum):
    """How far a source's data is allowed to travel. Each stage needs its own clearance.

    Being able to build against a source says nothing about being able to store its
    data in production, show it publicly, or earn money beside it.
    """

    PRIVATE_DEVELOPMENT = "private_development"  # local build and test
    PRIVATE_COLLECTION = "private_collection"  # unattended internal collection and storage
    PUBLIC_LAUNCH = "public_launch"  # shown to the public, not monetized
    COMMERCIAL_PRODUCTION = "commercial_production"  # shown publicly and monetized


# Stages at which a source's data, or anything computed from it, is shown to someone else.
PUBLIC_STAGES = frozenset({DeploymentStage.PUBLIC_LAUNCH, DeploymentStage.COMMERCIAL_PRODUCTION})


class SourcePurpose(StrEnum):
    """What a source is used for, which decides which permissions it must hold."""

    DEMAND_SIGNALS = "demand_signals"  # metrics that features and scores derive from
    CATALOGUE_METADATA = "catalogue_metadata"  # identity, dates and relationships


class StageClearance(FrozenModel):
    stage: DeploymentStage
    cleared: bool
    reason: NonEmptyStr


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

    # Sources that share one upstream allowance (for example one API key) share a
    # budget group, and therefore one rate limiter and one quota ledger.
    budget_group: NonEmptyStr
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
    # Approval needed merely to collect from the source.
    approval_required: Requirement
    approval_status: ApprovalStatus
    # Approval needed before the source may sit beside monetization. Separate, because
    # a source can be free to build against yet need a partnership to commercialise.
    commercial_approval_required: Requirement
    commercial_approval_status: ApprovalStatus
    # An explicit, reviewed statement per stage. Never computed, never assumed.
    stage_clearance: tuple[StageClearance, ...]
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
        stages = [clearance.stage for clearance in self.stage_clearance]
        if sorted(stages) != sorted(DeploymentStage):
            raise ValueError("stage_clearance must state every deployment stage exactly once")
        needs_commercial = self.commercial_approval_required is not Requirement.NOT_REQUIRED
        if needs_commercial and self.commercial_approval_status is ApprovalStatus.NOT_REQUIRED:
            raise ValueError(
                "commercial_approval_status cannot be 'not_required' when approval may be needed"
            )
        if self.cleared_for(DeploymentStage.COMMERCIAL_PRODUCTION) and not (
            self.commercial_use_allowed.permits and self._commercial_approval_settled
        ):
            raise ValueError(
                "commercial production cannot be cleared without an explicit commercial "
                "permission and settled commercial approval"
            )
        return self

    def clearance(self, stage: DeploymentStage) -> StageClearance:
        return next(c for c in self.stage_clearance if c.stage is stage)

    def cleared_for(self, stage: DeploymentStage) -> bool:
        return self.clearance(stage).cleared

    @property
    def _approval_settled(self) -> bool:
        return self.approval_status in (ApprovalStatus.NOT_REQUIRED, ApprovalStatus.APPROVED)

    @property
    def _commercial_approval_settled(self) -> bool:
        return self.commercial_approval_status in (
            ApprovalStatus.NOT_REQUIRED,
            ApprovalStatus.APPROVED,
        )

    def registration_refusal(self, purpose: SourcePurpose, stage: DeploymentStage) -> str | None:
        """Why this source may not be used for `purpose` at `stage`, or None if it may.

        What a source must hold depends on how far its data travels. Privately, a
        demand source may be built against and studied while its derived-metrics
        permission is still unanswered, because nothing derived leaves the building.
        From public launch onward the permission must be an explicit ALLOWED. An
        unanswered permission is never read as granted; it only stops mattering
        where nothing it governs happens.
        """
        if not self._approval_settled:
            return "policy does not permit collection (required approval has not been granted)"
        if purpose is SourcePurpose.DEMAND_SIGNALS:
            if self.derived_metrics_allowed is Permission.PROHIBITED:
                return "policy does not permit collection (derived metrics are prohibited)"
            if stage in PUBLIC_STAGES and not self.derived_output_permitted:
                return (
                    f"derived metrics are not explicitly allowed "
                    f"({self.derived_metrics_allowed.value}), so the source may not be "
                    f"part of a {stage.value} configuration"
                )
            if self.historical_storage_allowed in (Permission.UNKNOWN, Permission.PROHIBITED):
                return "policy does not permit storing the source's history"
        if purpose is SourcePurpose.CATALOGUE_METADATA and self.historical_storage_allowed in (
            Permission.UNKNOWN,
            Permission.PROHIBITED,
        ):
            return "policy does not permit storing the source's metadata"
        clearance = self.clearance(stage)
        if not clearance.cleared:
            return f"not cleared for {stage.value}: {clearance.reason}"
        return None

    @property
    def collection_permitted(self) -> bool:
        """We may collect and score only if derived metrics are allowed and approval is settled."""
        approved = self.approval_status in (ApprovalStatus.NOT_REQUIRED, ApprovalStatus.APPROVED)
        return self.derived_metrics_allowed.permits and approved

    @property
    def derived_output_permitted(self) -> bool:
        """Whether anything computed from this source may appear in a published artifact."""
        return self.derived_metrics_allowed.permits

    def publication_refusal(self) -> str | None:
        """Why this source's data or anything derived from it may not be published."""
        if not self._approval_settled:
            return "required approval has not been granted"
        if not self.derived_output_permitted:
            return (
                f"derived metrics are not explicitly allowed "
                f"({self.derived_metrics_allowed.value}); nothing computed from this "
                "source may enter a published artifact"
            )
        return None

    @property
    def commercial_production_permitted(self) -> bool:
        """Whether the source may be part of a monetized production configuration."""
        return (
            self.cleared_for(DeploymentStage.COMMERCIAL_PRODUCTION)
            and self.commercial_use_allowed.permits
            and self._approval_settled
            and self._commercial_approval_settled
        )

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
