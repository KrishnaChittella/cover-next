"""A SYNTHETIC, TEST-ONLY source policy for invented event data.

Artifact and contract tests need events to exist. Those events are made up in the test
that uses them, and the artifact build refuses any event source without a policy. This
module is how a test supplies one, so that invented data goes through exactly the same
fail-closed gate as real data.

It lives under `tests/`, is imported by nothing in `src/`, appears in no runtime
registry, and describes no real source: it needs no network and has no endpoint. It
clears private development and nothing else, so a bundle built from it is marked
private-development-only, as any bundle of data not cleared for the public is.
"""

from datetime import date

from metaspike.domain.games import Catalogue, SourceIdentifier
from metaspike.domain.source_policy import (
    ApprovalStatus,
    DeploymentStage,
    DisplayRules,
    LimitBasis,
    Permission,
    RateLimit,
    Requirement,
    SourceOperations,
    SourcePolicy,
    StageClearance,
)

SYNTHETIC_EVENT_SOURCE = "synthetic_events"
_NAMESPACE = "synthetic_test"
_PREFIX = "synthetic_"
_NOT_REAL = "Synthetic test data. It is not real and must never leave a test."


def synthetic_event_policy(source: str = SYNTHETIC_EVENT_SOURCE) -> SourcePolicy:
    """A policy for invented events. Refuses any name that could be a real source's."""
    if not source.startswith(_PREFIX):
        raise ValueError(f"a synthetic test source must be named '{_PREFIX}...', not {source!r}")
    return SourcePolicy(
        source=source,
        raw_storage_allowed=Permission.ALLOWED,
        historical_storage_allowed=Permission.ALLOWED,
        derived_metrics_allowed=Permission.ALLOWED,
        commercial_use_allowed=Permission.PROHIBITED,
        redistribution_allowed=Permission.PROHIBITED,
        attribution_required=Requirement.NOT_REQUIRED,
        approval_required=Requirement.NOT_REQUIRED,
        approval_status=ApprovalStatus.NOT_REQUIRED,
        commercial_approval_required=Requirement.NOT_REQUIRED,
        commercial_approval_status=ApprovalStatus.NOT_REQUIRED,
        stage_clearance=tuple(
            StageClearance(
                stage=stage,
                cleared=stage is DeploymentStage.PRIVATE_DEVELOPMENT,
                reason=(
                    "Synthetic test data, usable in a test and nowhere else."
                    if stage is DeploymentStage.PRIVATE_DEVELOPMENT
                    else _NOT_REAL
                ),
            )
            for stage in DeploymentStage
        ),
        operations=SourceOperations(
            budget_group="synthetic_test_no_network",
            rate_limit=RateLimit(
                max_requests=1,
                per_seconds=1,
                max_concurrency=1,
                basis=LimitBasis.SELF_IMPOSED,
                note="Never used: a synthetic source is never called.",
            ),
            expected_publication_lag_seconds=0,
            publication_lag_basis=LimitBasis.SELF_IMPOSED,
        ),
        display=DisplayRules(
            link_back_required=Requirement.NOT_REQUIRED,
            branding_required=Requirement.NOT_REQUIRED,
            note=_NOT_REAL,
        ),
        reviewed_at=date(2026, 10, 11),
        references=("tests/synthetic_policy.py",),
        notes=_NOT_REAL,
    )


def synthetic_event_policies(*sources: str) -> dict[str, SourcePolicy]:
    """A policy mapping for the given synthetic sources (the default one if none)."""
    return {name: synthetic_event_policy(name) for name in sources or (SYNTHETIC_EVENT_SOURCE,)}


def with_synthetic_identifiers(
    catalogue: Catalogue, source: str = SYNTHETIC_EVENT_SOURCE
) -> Catalogue:
    """The same games, each named only by a synthetic source id.

    Lets a test report an invented event for a real catalogue game without attributing
    it to a real source.
    """
    if not source.startswith(_PREFIX):
        raise ValueError(f"a synthetic test source must be named '{_PREFIX}...', not {source!r}")
    return Catalogue(
        games=tuple(
            game.model_copy(
                update={
                    "source_identifiers": (
                        SourceIdentifier(source=source, namespace=_NAMESPACE, value=game.slug),
                    )
                }
            )
            for game in catalogue.games
        )
    )
