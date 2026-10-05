"""Registry that refuses any adapter without a policy permitting its use."""

from dataclasses import dataclass

import httpx

from metaspike.domain.source_policy import SourcePolicy
from metaspike.sources.base import SourceAdapter
from metaspike.sources.wikimedia import WIKIMEDIA_PAGEVIEWS_POLICY, WikimediaPageviewsAdapter


class SourcePolicyError(Exception):
    """An adapter was registered without a usable source policy."""


@dataclass(frozen=True)
class RegisteredSource:
    adapter: SourceAdapter
    policy: SourcePolicy


class SourceRegistry:
    def __init__(self) -> None:
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
        self._entries[adapter.source] = RegisteredSource(adapter=adapter, policy=policy)

    def entries(self) -> tuple[RegisteredSource, ...]:
        return tuple(self._entries[source] for source in sorted(self._entries))


def default_registry(client: httpx.Client) -> SourceRegistry:
    registry = SourceRegistry()
    registry.register(WikimediaPageviewsAdapter(client), WIKIMEDIA_PAGEVIEWS_POLICY)
    return registry
