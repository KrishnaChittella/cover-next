from typing import Any

import httpx
import pytest

from metaspike.domain.source_policy import (
    ApprovalStatus,
    DeploymentStage,
    Permission,
    Requirement,
    SourcePolicy,
)
from metaspike.sources.registry import SourcePolicyError, SourceRegistry, default_registry
from metaspike.sources.wikimedia import (
    SOURCE_ID,
    WIKIMEDIA_PAGEVIEWS_POLICY,
    WikimediaPageviewsAdapter,
)


@pytest.fixture
def adapter() -> WikimediaPageviewsAdapter:
    return WikimediaPageviewsAdapter(httpx.Client())


def policy(**changes: Any) -> SourcePolicy:
    return WIKIMEDIA_PAGEVIEWS_POLICY.model_copy(update=changes)


def test_adapter_cannot_be_registered_without_a_policy(
    adapter: WikimediaPageviewsAdapter,
) -> None:
    registry = SourceRegistry()
    with pytest.raises(TypeError):
        registry.register(adapter)  # type: ignore[call-arg]
    with pytest.raises(SourcePolicyError, match="SourcePolicy is required"):
        registry.register(adapter, None)  # type: ignore[arg-type]
    assert registry.entries() == ()


def test_policy_must_belong_to_the_adapter(adapter: WikimediaPageviewsAdapter) -> None:
    with pytest.raises(SourcePolicyError, match="policy is for"):
        SourceRegistry().register(adapter, policy(source="some_other_source"))


@pytest.mark.parametrize(
    "changes",
    [
        {"derived_metrics_allowed": Permission.PROHIBITED},
        {"approval_required": Requirement.REQUIRED, "approval_status": ApprovalStatus.PENDING},
        {"approval_required": Requirement.REQUIRED, "approval_status": ApprovalStatus.DENIED},
    ],
)
def test_prohibited_or_unapproved_sources_are_refused(
    adapter: WikimediaPageviewsAdapter, changes: dict[str, Any]
) -> None:
    with pytest.raises(SourcePolicyError, match="does not permit collection"):
        SourceRegistry().register(adapter, policy(**changes))


@pytest.mark.parametrize("uncertain", [Permission.UNKNOWN, Permission.RESTRICTED])
def test_an_uncertain_derived_metrics_permission_is_private_only(
    adapter: WikimediaPageviewsAdapter, uncertain: Permission
) -> None:
    unclear = policy(derived_metrics_allowed=uncertain)
    private = SourceRegistry()
    private.register(adapter, unclear)  # may be built against and studied privately
    assert private.publication_refusals() != {}  # and published nowhere
    for stage in (DeploymentStage.PUBLIC_LAUNCH, DeploymentStage.COMMERCIAL_PRODUCTION):
        with pytest.raises(SourcePolicyError, match="not explicitly allowed"):
            SourceRegistry(stage=stage).register(adapter, unclear)


def test_a_source_cannot_be_registered_twice(adapter: WikimediaPageviewsAdapter) -> None:
    registry = SourceRegistry()
    registry.register(adapter, WIKIMEDIA_PAGEVIEWS_POLICY)
    with pytest.raises(SourcePolicyError, match="already registered"):
        registry.register(adapter, WIKIMEDIA_PAGEVIEWS_POLICY)


def test_only_an_explicit_allowed_permits() -> None:
    assert Permission.ALLOWED.permits
    assert not any(
        p.permits for p in (Permission.UNKNOWN, Permission.RESTRICTED, Permission.PROHIBITED)
    )


def test_unknown_redistribution_blocks_public_history() -> None:
    assert WIKIMEDIA_PAGEVIEWS_POLICY.public_history_permitted
    assert not policy(redistribution_allowed=Permission.UNKNOWN).public_history_permitted


def test_approval_fields_must_be_consistent() -> None:
    with pytest.raises(ValueError, match="approval_status"):
        SourcePolicy.model_validate(
            WIKIMEDIA_PAGEVIEWS_POLICY.model_dump() | {"approval_required": Requirement.UNKNOWN}
        )


def test_wikimedia_policy_is_documented_and_dated() -> None:
    registered = default_registry(httpx.Client()).entries()
    assert [entry.policy.source for entry in registered] == [SOURCE_ID]
    wikimedia = registered[0].policy
    assert wikimedia.reviewed_at.year >= 2026
    assert all(reference.startswith("https://") for reference in wikimedia.references)
