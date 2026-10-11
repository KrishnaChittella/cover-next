"""The one gate every use of event-source data passes through.

Event intelligence derived from a source is a derived output of that source. Wherever it
is about to be used, whether in an artifact bundle or in a historical backtest, the
source must have a policy that clears the stage of that use for event intelligence.

Fail closed. There is no default stage, no default policy, no way for a caller to vouch
for a source, and no source whose name exempts it. The policies are always handed in by
the caller, so nothing here consults a registry a test could not control.
"""

from collections.abc import Mapping

from metaspike.domain.source_policy import DeploymentStage, SourcePolicy, SourcePurpose
from metaspike.sources.registry import SourcePolicyError


def require_event_source(
    source: str,
    policies: Mapping[str, SourcePolicy] | None,
    stage: DeploymentStage | None,
    use: str,
) -> SourcePolicy:
    """The policy that lets `source`'s events be used for `use` at `stage`, or a refusal."""
    if stage is None:
        raise SourcePolicyError(
            f"{source}: event data was about to be used for {use}, but no deployment "
            "stage was stated"
        )
    policy = (policies or {}).get(source)
    if policy is None:
        raise SourcePolicyError(
            f"{source}: no source policy for this event source, so it cannot be used for {use}"
        )
    if policy.source != source:
        raise SourcePolicyError(
            f"{source}: the policy given for this event source is for '{policy.source}'"
        )
    refusal = policy.registration_refusal(SourcePurpose.EVENT_INTELLIGENCE, stage)
    if refusal is not None:
        raise SourcePolicyError(f"{source}: {refusal}")
    return policy
