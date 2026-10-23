"""Wiring for the catalogue sources: credentials, limiters, adapters, registration.

Everything a catalogue adapter needs is assembled here, and each adapter is handed a
limited client for its own budget group. Registration goes through `SourceRegistry`,
so a source whose policy does not clear the registry's stage is refused before any
request can be made.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx

from metaspike.domain.source_policy import SourcePolicy
from metaspike.sources.credentials import (
    IGDB_CLIENT_ID_ENV,
    IGDB_CLIENT_SECRET_ENV,
    STEAM_WEB_API_KEY_ENV,
    secret_from_env,
)
from metaspike.sources.http import LimitedHttpClient, RetryConfig
from metaspike.sources.igdb import IGDB_POLICY, IgdbCatalogueAdapter
from metaspike.sources.igdb_auth import (
    DEFAULT_TOKEN_CACHE,
    TWITCH_OAUTH_OPERATIONS,
    IgdbAuth,
    TokenCache,
)
from metaspike.sources.limiter import FileQuotaLedger, LimiterPool
from metaspike.sources.registry import SourceRegistry
from metaspike.sources.steam_catalogue import STEAM_CATALOGUE_POLICY, SteamCatalogueAdapter
from metaspike.sources.twitch import TWITCH_HELIX_POLICY, TwitchHelixAdapter

DEFAULT_QUOTA_DIR = Path(".metaspike/quota")
# The catalogue-metadata sources and the policy each runs under. A caller that uses
# catalogue records hands this in explicitly; nothing looks it up on its own.
CATALOGUE_SOURCE_POLICIES: Mapping[str, SourcePolicy] = {
    IGDB_POLICY.source: IGDB_POLICY,
    STEAM_CATALOGUE_POLICY.source: STEAM_CATALOGUE_POLICY,
}
# Catalogue calls are not time-critical, so retries are spread out with jitter.
CATALOGUE_RETRY = RetryConfig(jitter=0.25)


@dataclass(frozen=True)
class CatalogueSources:
    registry: SourceRegistry
    igdb: IgdbCatalogueAdapter | None
    steam: SteamCatalogueAdapter | None


def default_limiter_pool(quota_dir: Path = DEFAULT_QUOTA_DIR) -> LimiterPool:
    """Limiters whose quota usage persists across runs in a local file."""
    return LimiterPool(ledger=FileQuotaLedger(quota_dir))


def build_igdb(
    client: httpx.Client,
    pool: LimiterPool,
    environ: Mapping[str, str] | None = None,
    token_cache: Path = DEFAULT_TOKEN_CACHE,
) -> IgdbCatalogueAdapter:
    """Raises SourceError(missing_credentials) naming the variable that is not set."""
    auth = IgdbAuth(
        LimitedHttpClient(client, pool.for_operations(TWITCH_OAUTH_OPERATIONS), CATALOGUE_RETRY),
        secret_from_env(IGDB_CLIENT_ID_ENV, environ),
        secret_from_env(IGDB_CLIENT_SECRET_ENV, environ),
        TokenCache(token_cache),
    )
    http = LimitedHttpClient(client, pool.for_policy(IGDB_POLICY), CATALOGUE_RETRY)
    return IgdbCatalogueAdapter(http, auth)


def build_twitch(
    client: httpx.Client,
    pool: LimiterPool,
    environ: Mapping[str, str] | None = None,
    token_cache: Path = DEFAULT_TOKEN_CACHE,
) -> TwitchHelixAdapter:
    """Helix with the IGDB application's credentials: one Twitch application, one token."""
    auth = IgdbAuth(
        LimitedHttpClient(client, pool.for_operations(TWITCH_OAUTH_OPERATIONS), CATALOGUE_RETRY),
        secret_from_env(IGDB_CLIENT_ID_ENV, environ),
        secret_from_env(IGDB_CLIENT_SECRET_ENV, environ),
        TokenCache(token_cache),
    )
    http = LimitedHttpClient(client, pool.for_policy(TWITCH_HELIX_POLICY), CATALOGUE_RETRY)
    return TwitchHelixAdapter(http, auth)


def build_steam(
    client: httpx.Client, pool: LimiterPool, environ: Mapping[str, str] | None = None
) -> SteamCatalogueAdapter:
    http = LimitedHttpClient(client, pool.for_policy(STEAM_CATALOGUE_POLICY), CATALOGUE_RETRY)
    return SteamCatalogueAdapter(http, secret_from_env(STEAM_WEB_API_KEY_ENV, environ))


def register_catalogue_sources(
    registry: SourceRegistry,
    igdb: IgdbCatalogueAdapter | None = None,
    steam: SteamCatalogueAdapter | None = None,
) -> CatalogueSources:
    if igdb is not None:
        registry.register_catalogue(igdb, IGDB_POLICY)
    if steam is not None:
        registry.register_catalogue(steam, STEAM_CATALOGUE_POLICY)
    return CatalogueSources(registry=registry, igdb=igdb, steam=steam)
