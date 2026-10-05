"""Registry that refuses any adapter without a usable policy and known metrics."""

from dataclasses import dataclass

import httpx

from metaspike.domain.metrics import MetricRegistry, default_metrics
from metaspike.domain.source_policy import SourcePolicy
from metaspike.sources.base import SourceAdapter
from metaspike.sources.wikimedia import WIKIMEDIA_PAGEVIEWS_POLICY, WikimediaPageviewsAdapter


class SourceRegistrationError(Exception):
    """An adapter could not be registered."""


class SourcePolicyError(SourceRegistrationError):
    """An adapter was registered without a usable source policy."""


@dataclass(frozen=True)
class RegisteredSource:
    adapter: SourceAdapter
    policy: SourcePolicy


class SourceRegistry:
    def __init__(self, metrics: MetricRegistry | None = None) -> None:
        self.metrics = metrics or default_metrics()
        self._entries: dict[str, RegisteredSource] = {}

    def register(self, adapter: SourceAdapter, policy: SourcePolicy) -> None:
        if not isinstance(policy, SourcePolicy):
            raise SourcePolicyError(f"{adapter.source}: a SourcePolicy is required to register")
        if policy.source != adapter.source:
            raise SourcePolicyError(
                f"policy is for '{policy.source}' but the adapter is '{adapter.source}'"
            )
        if not policy.collection_permitted:
            raise SourcePolicyError(
                f"{adapter.source}: policy does not permit collection "
                "(derived metrics must be allowed and any required approval granted)"
            )
        if adapter.source in self._entries:
            raise SourcePolicyError(f"{adapter.source}: already registered")
        self._check_signals(adapter)
        self._entries[adapter.source] = RegisteredSource(adapter=adapter, policy=policy)

    def _check_signals(self, adapter: SourceAdapter) -> None:
        signals = adapter.signals
        if not signals:
            raise SourceRegistrationError(f"{adapter.source}: adapter declares no signals")
        if len(signals) != len(set(signals)):
            raise SourceRegistrationError(f"{adapter.source}: adapter declares a signal twice")
        for signal in signals:
            if signal.metric not in self.metrics:
                raise SourceRegistrationError(
                    f"{adapter.source}: metric '{signal.metric}' is not in the metric registry"
                )
            if not self.metrics.get(signal.metric).supports(signal.granularity):
                raise SourceRegistrationError(
                    f"{adapter.source}: metric '{signal.metric}' is not defined at "
                    f"{signal.granularity.value} granularity"
                )

    def entries(self) -> tuple[RegisteredSource, ...]:
        return tuple(self._entries[source] for source in sorted(self._entries))

    def policy(self, source: str) -> SourcePolicy:
        return self._entries[source].policy


def default_registry(client: httpx.Client) -> SourceRegistry:
    registry = SourceRegistry()
    registry.register(WikimediaPageviewsAdapter(client), WIKIMEDIA_PAGEVIEWS_POLICY)
    return registry
