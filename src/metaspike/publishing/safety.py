"""The last check before anything computed from a source could be published.

Internal features may be computed from a source whose derived-metrics permission is
unanswered. They may not be published. This is where that line is held for anything
that is not already stopped by the registry and the artifact builder.
"""

from collections.abc import Callable, Iterable

from metaspike.domain.source_policy import SourcePolicy
from metaspike.features.normalise import EntityEvidence, NormalisedSignal
from metaspike.sources.registry import SourcePolicyError


def refuse_unpublishable(
    signals: Iterable[NormalisedSignal], policy_for: Callable[[str], SourcePolicy]
) -> None:
    """Raise unless every signal's source explicitly permits publishing derived metrics."""
    for signal in signals:
        refusal = policy_for(signal.source).publication_refusal()
        if refusal is not None:
            raise SourcePolicyError(f"{signal.source}: {refusal}")


def refuse_unpublishable_evidence(
    evidence: EntityEvidence, policy_for: Callable[[str], SourcePolicy]
) -> None:
    for reading in evidence.families:
        refuse_unpublishable((*reading.signals, *reading.per_native), policy_for)
