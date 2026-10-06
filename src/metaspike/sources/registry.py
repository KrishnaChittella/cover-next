"""Registry that refuses any adapter without a usable policy and known metrics."""

from dataclasses import dataclass

import httpx

from metaspike.domain.metrics import MetricRegistry, default_metrics
from metaspike.domain.source_policy import DeploymentStage, SourcePolicy, SourcePurpose
from metaspike.sources.base import CatalogueAdapter, SamplingAdapter, SourceAdapter, SourceMode
from metaspike.sources.http import LimitedHttpClient
from metaspike.sources.limiter import LimiterPool
from metaspike.sources.wikimedia import WIKIMEDIA_PAGEVIEWS_POLICY, WikimediaPageviewsAdapter


class SourceRegistrationError(Exception):
    """An adapter could not be registered."""


class SourcePolicyError(SourceRegistrationError):
    """An adapter was registered without a usable source policy."""


@dataclass(frozen=True)
class RegisteredSource:
    adapter: SourceAdapter
    policy: SourcePolicy


@dataclass(frozen=True)
class RegisteredSampler:
    adapter: SamplingAdapter
    policy: SourcePolicy


@dataclass(frozen=True)
class RegisteredCatalogueSource:
    adapter: CatalogueAdapter
    policy: SourcePolicy


class SourceRegistry:
    """Sources usable at one deployment stage.

    Registration is where policy is enforced: a source is refused unless its policy
    grants what its purpose needs and clears the stage this registry serves.
    """

    def __init__(
        self,
        metrics: MetricRegistry | None = None,
        stage: DeploymentStage = DeploymentStage.PRIVATE_DEVELOPMENT,
    ) -> None:
        self.metrics = metrics or default_metrics()
        self.stage = stage
        self._entries: dict[str, RegisteredSource] = {}
        self._catalogue: dict[str, RegisteredCatalogueSource] = {}
        self._samplers: dict[str, RegisteredSampler] = {}

    def register(self, adapter: SourceAdapter, policy: SourcePolicy) -> None:
        """Register a FETCH_HISTORY source of demand signals."""
        if adapter.mode is not SourceMode.FETCH_HISTORY:
            raise SourceRegistrationError(
                f"{adapter.source}: a sample-now source cannot be registered as a history source"
            )
        self._check_policy(adapter.source, policy, SourcePurpose.DEMAND_SIGNALS)
        self._check_signals(adapter)
        self._entries[adapter.source] = RegisteredSource(adapter=adapter, policy=policy)

    def register_sampler(self, adapter: SamplingAdapter, policy: SourcePolicy) -> None:
        """Register a SAMPLE_NOW source of demand signals."""
        if adapter.mode is not SourceMode.SAMPLE_NOW:
            raise SourceRegistrationError(
                f"{adapter.source}: only a sample-now source can be registered as a sampler"
            )
        if hasattr(adapter, "fetch"):
            raise SourceRegistrationError(
                f"{adapter.source}: a sample-now source cannot also fetch history"
            )
        self._check_policy(adapter.source, policy, SourcePurpose.DEMAND_SIGNALS)
        signal = adapter.signal
        if signal.metric not in self.metrics:
            raise SourceRegistrationError(
                f"{adapter.source}: metric '{signal.metric}' is not in the metric registry"
            )
        if not self.metrics.get(signal.metric).supports(signal.granularity):
            raise SourceRegistrationError(
                f"{adapter.source}: metric '{signal.metric}' is not defined at "
                f"{signal.granularity.value} granularity"
            )
        self._samplers[adapter.source] = RegisteredSampler(adapter=adapter, policy=policy)

    def register_catalogue(self, adapter: CatalogueAdapter, policy: SourcePolicy) -> None:
        """Register a source of catalogue metadata."""
        self._check_policy(adapter.source, policy, SourcePurpose.CATALOGUE_METADATA)
        self._catalogue[adapter.source] = RegisteredCatalogueSource(adapter=adapter, policy=policy)

    def _check_policy(self, source: str, policy: SourcePolicy, purpose: SourcePurpose) -> None:
        if not isinstance(policy, SourcePolicy):
            raise SourcePolicyError(f"{source}: a SourcePolicy is required to register")
        if policy.source != source:
            raise SourcePolicyError(
                f"policy is for '{policy.source}' but the adapter is '{source}'"
            )
        refusal = policy.registration_refusal(purpose, self.stage)
        if refusal is not None:
            raise SourcePolicyError(f"{source}: {refusal}")
        if source in self._entries or source in self._catalogue or source in self._samplers:
            raise SourcePolicyError(f"{source}: already registered")

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

    def samplers(self) -> tuple[RegisteredSampler, ...]:
        return tuple(self._samplers[source] for source in sorted(self._samplers))

    def publication_refusals(self) -> dict[str, str]:
        """Every demand source whose data may not appear in a published artifact, and why."""
        policies = [entry.policy for entry in self.entries()]
        policies += [sampler.policy for sampler in self.samplers()]
        return {
            policy.source: refusal
            for policy in policies
            if (refusal := policy.publication_refusal()) is not None
        }

    def catalogue_entries(self) -> tuple[RegisteredCatalogueSource, ...]:
        return tuple(self._catalogue[source] for source in sorted(self._catalogue))

    def policy(self, source: str) -> SourcePolicy:
        if source in self._catalogue:
            return self._catalogue[source].policy
        if source in self._samplers:
            return self._samplers[source].policy
        return self._entries[source].policy


def default_registry(client: httpx.Client, pool: LimiterPool | None = None) -> SourceRegistry:
    """The demand-signal sources, each behind the limiter for its budget group."""
    limiters = pool or LimiterPool()
    registry = SourceRegistry()
    http = LimitedHttpClient(client, limiters.for_policy(WIKIMEDIA_PAGEVIEWS_POLICY))
    registry.register(WikimediaPageviewsAdapter(http), WIKIMEDIA_PAGEVIEWS_POLICY)
    return registry
