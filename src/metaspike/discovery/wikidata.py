"""The universe of games: every Wikidata video game with an English Wikipedia article.

Wikidata (CC0) carries the cross-source ids: IGDB game id (P5794, a slug) and Steam
application id (P1733). Identity therefore comes from ids recorded in Wikidata, never
from a title match. One SPARQL query returns the whole list in seconds (~30,000 games).
"""

import csv
import io
from collections.abc import Iterable
from datetime import date
from typing import Final

import httpx

from metaspike.domain.model import FrozenModel
from metaspike.domain.signals import SourceErrorCode
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
from metaspike.sources.base import SourceError

SOURCE_ID: Final = "wikidata"
SPARQL_URL: Final = "https://query.wikidata.org/sparql"
# Instance of "video game" (Q7889) with an English Wikipedia article.
GAMES_QUERY: Final = """
SELECT ?item ?title ?igdb ?steam WHERE {
  ?item wdt:P31 wd:Q7889 .
  ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> ; schema:name ?title .
  OPTIONAL { ?item wdt:P5794 ?igdb }
  OPTIONAL { ?item wdt:P1733 ?steam }
}
"""

WIKIDATA_POLICY = SourcePolicy(
    source=SOURCE_ID,
    raw_storage_allowed=Permission.ALLOWED,
    retention_days=None,
    historical_storage_allowed=Permission.ALLOWED,
    derived_metrics_allowed=Permission.ALLOWED,
    commercial_use_allowed=Permission.ALLOWED,
    redistribution_allowed=Permission.ALLOWED,
    attribution_required=Requirement.NOT_REQUIRED,
    attribution_text="Game identifiers from Wikidata (CC0 1.0).",
    approval_required=Requirement.NOT_REQUIRED,
    approval_status=ApprovalStatus.NOT_REQUIRED,
    commercial_approval_required=Requirement.NOT_REQUIRED,
    commercial_approval_status=ApprovalStatus.NOT_REQUIRED,
    stage_clearance=tuple(
        StageClearance(stage=stage, cleared=True, reason="Structured data is CC0 1.0.")
        for stage in DeploymentStage
    ),
    operations=SourceOperations(
        budget_group="wikidata",
        rate_limit=RateLimit(
            max_requests=1,
            per_seconds=60,
            max_concurrency=1,
            basis=LimitBasis.SELF_IMPOSED,
            note="One list query a run; the service allows 60 s of query time per minute.",
        ),
        quota=None,
        cache_max_age_seconds=7 * 86_400,
        expected_publication_lag_seconds=0,
        publication_lag_basis=LimitBasis.SELF_IMPOSED,
    ),
    display=DisplayRules(
        link_back_required=Requirement.NOT_REQUIRED,
        branding_required=Requirement.NOT_REQUIRED,
        note="CC0 data carries no display condition.",
    ),
    reviewed_at=date(2026, 10, 23),
    references=(
        "https://www.wikidata.org/wiki/Wikidata:Licensing",
        "https://www.mediawiki.org/wiki/Wikidata_Query_Service/User_Manual#Query_limits",
        "https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy",
    ),
)


class WikidataGame(FrozenModel):
    """One game as Wikidata lists it. The article title is the dump's spelling (underscores)."""

    qid: str
    article: str
    igdb: tuple[str, ...] = ()
    steam: tuple[str, ...] = ()


def parse_games(rows: Iterable[dict[str, str]]) -> list[WikidataGame]:
    """Group the query's rows (one per id combination) into one entry per item."""
    found: dict[str, tuple[str, set[str], set[str]]] = {}
    for row in rows:
        qid = row.get("item", "").rsplit("/", 1)[-1]
        title = row.get("title", "").strip()
        if not qid.startswith("Q") or not title:
            continue
        _, igdb, steam = found.setdefault(qid, (title.replace(" ", "_"), set(), set()))
        if row.get("igdb"):
            igdb.add(row["igdb"].strip())
        if row.get("steam", "").strip().isdigit():
            steam.add(row["steam"].strip())
    return sorted(
        (
            WikidataGame(qid=q, article=a, igdb=tuple(sorted(i)), steam=tuple(sorted(s)))
            for q, (a, i, s) in found.items()
        ),
        key=lambda g: g.article,
    )


def fetch_games(client: httpx.Client) -> list[WikidataGame]:
    try:
        response = client.get(
            SPARQL_URL,
            params={"query": GAMES_QUERY},
            headers={"Accept": "text/csv"},
            timeout=httpx.Timeout(90.0, connect=10.0),
        )
    except httpx.HTTPError as error:
        raise SourceError(
            SourceErrorCode.NETWORK_ERROR, f"wikidata: {type(error).__name__}"
        ) from None
    if response.status_code != httpx.codes.OK:
        raise SourceError(SourceErrorCode.HTTP_ERROR, f"wikidata: HTTP {response.status_code}")
    games = parse_games(csv.DictReader(io.StringIO(response.text)))
    if not games:
        raise SourceError(
            SourceErrorCode.INVALID_RESPONSE, "wikidata: the game list came back empty"
        )
    return games
