"""Studio stories: when several risers share a developer or publisher, one story, not a flood.

On 22 Oct 2026 21 of the top 40 risers were BioWare games after EA shut the studio. The
list should say that once. Developer (P178) and publisher (P123) come from Wikidata (CC0)
for the selected games only. A studio with at least MIN_MEMBERS rising games becomes a
story; each game joins at most one story, the biggest. The strongest member stays in the
list; the others fold into the story card.

The headline is a one-line AI summary of the studio's English Wikipedia lead (CC BY-SA
4.0), written only when ANTHROPIC_API_KEY is set. Without it, or when the call fails, the
headline is None and the site shows the deterministic line built from the counts.
"""

import os
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import quote, unquote

import httpx

from metaspike.discovery.catalogue import display_name, slug_of
from metaspike.discovery.wikidata import SPARQL_URL, WikidataGame
from metaspike.domain.model import FrozenModel

if TYPE_CHECKING:  # the `site` dependency group; the job image does not carry it
    import anthropic

MIN_MEMBERS: Final = 3
# A story is about games rising together; a steady game does not join one.
RISING_RATIO: Final = 1.25
SUMMARY_URL: Final = "https://en.wikipedia.org/api/rest_v1/page/summary/"
MODEL: Final = "claude-opus-5-5"
_ROLES: Final = {"P178": "developer", "P123": "publisher"}


class Studio(FrozenModel):
    qid: str
    name: str
    role: str  # "developer" or "publisher" of the game it was read for
    article: str | None  # English Wikipedia title with underscores, when there is one


def studios_query(qids: Sequence[str]) -> str:
    values = " ".join(f"wd:{q}" for q in qids)
    return f"""
SELECT ?item ?prop ?studio ?studioLabel ?article WHERE {{
  VALUES ?item {{ {values} }}
  VALUES ?prop {{ wdt:P178 wdt:P123 }}
  ?item ?prop ?studio .
  OPTIONAL {{ ?article schema:about ?studio ; schema:isPartOf <https://en.wikipedia.org/> }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
"""


def parse_studios(bindings: Sequence[Mapping[str, Any]]) -> dict[str, list[Studio]]:
    """SPARQL JSON bindings -> each game's studios, developers first."""
    found: dict[str, dict[str, Studio]] = {}
    for row in bindings:
        game = row["item"]["value"].rsplit("/", 1)[-1]
        studio = row["studio"]["value"].rsplit("/", 1)[-1]
        role = _ROLES.get(row["prop"]["value"].rsplit("/", 1)[-1])
        name = row.get("studioLabel", {}).get("value", "")
        if role is None or not name or name == studio:  # no English label
            continue
        url = row.get("article", {}).get("value")
        article = unquote(url.rsplit("/wiki/", 1)[-1]) if url else None
        kept = found.setdefault(game, {})
        if studio not in kept or role == "developer":
            kept[studio] = Studio(qid=studio, name=name, role=role, article=article)
    return {
        g: sorted(s.values(), key=lambda s: (s.role != "developer", s.qid))
        for g, s in found.items()
    }


def fetch_studios(client: httpx.Client, qids: Sequence[str]) -> dict[str, list[Studio]]:
    """Each game's developers and publishers. An empty answer on failure: no stories today."""
    if not qids:
        return {}
    try:
        response = client.get(
            SPARQL_URL,
            params={"query": studios_query(qids)},
            headers={"Accept": "application/sparql-results+json"},
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
        response.raise_for_status()
        return parse_studios(response.json()["results"]["bindings"])
    except (httpx.HTTPError, ValueError, KeyError):
        return {}


def group(
    selection: Sequence[tuple[WikidataGame, Mapping[str, Any] | None, bool]],
    studios: Mapping[str, Sequence[Studio]],
) -> list[dict[str, Any]]:
    """Stories, biggest first. `selection` is ranked, so a story's first member is its lead."""
    rising = [
        game
        for game, riser, _ in selection
        if riser is not None and (riser.get("ratio") or 0) >= RISING_RATIO
    ]
    members: dict[str, list[WikidataGame]] = {}
    by_qid: dict[str, Studio] = {}
    for game in rising:
        for studio in studios.get(game.qid, ()):
            members.setdefault(studio.qid, []).append(game)
            by_qid.setdefault(studio.qid, studio)
    # Biggest first; a developer beats a publisher of the same size (BioWare before EA).
    order = sorted(
        members,
        key=lambda s: (-len(members[s]), by_qid[s].role != "developer", by_qid[s].name),
    )
    taken: set[str] = set()
    stories: list[dict[str, Any]] = []
    for qid in order:
        games = [g for g in members[qid] if g.qid not in taken]
        if len(games) < MIN_MEMBERS:
            continue
        taken.update(g.qid for g in games)
        studio = by_qid[qid]
        stories.append(
            {
                "slug": f"studio-{qid.lower()}",
                "qid": qid,
                "name": _name(studio),
                "role": studio.role,
                "article": studio.article,
                "members": [slug_of(g) for g in games],
                "headline": None,
            }
        )
    return stories


def _name(studio: Studio) -> str:
    # Wikidata labels are sometimes lower-cased ("Bioware"); the article title is not.
    if studio.article is None:
        return studio.name
    return re.sub(r"\s*\([^()]*\)$", "", studio.article.replace("_", " ")) or studio.name


def wikipedia_lead(client: httpx.Client, article: str) -> str | None:
    try:
        response = client.get(SUMMARY_URL + quote(article, safe=""), timeout=20.0)
        response.raise_for_status()
        text = response.json().get("extract")
    except (httpx.HTTPError, ValueError):
        return None
    return text if isinstance(text, str) and text.strip() else None


PROMPT: Final = """\
Several games by {studio} are getting far more Wikipedia readers than usual at the same \
time: {games}.

Here is the current lead of Wikipedia's article on {studio}:
<article>
{lead}
</article>

Write one headline, at most 14 words, for gaming YouTubers that says why these games are \
drawing attention now. Use only what the article says. If it gives no recent reason, \
describe the studio plainly and do not guess at one. Plain words, no hype, no quotation \
marks, no trailing full stop. Reply with the headline only."""


def headline(
    client: "anthropic.Anthropic", studio: str, games: Sequence[str], lead: str
) -> str | None:
    import anthropic

    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=2000,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[
                {
                    "role": "user",
                    "content": PROMPT.format(studio=studio, games=", ".join(games), lead=lead),
                }
            ],
        )
    except anthropic.APIError:
        return None
    if response.stop_reason != "end_turn":
        return None
    text = " ".join(b.text for b in response.content if b.type == "text").strip()
    line = text.splitlines()[0].strip().strip('"').rstrip(".") if text else ""
    return line or None


def add_headlines(
    stories: list[dict[str, Any]],
    names: Mapping[str, str],
    client: httpx.Client,
) -> int:
    """Fill each story's headline in place when a key is set. Returns how many were written."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return 0
    import anthropic

    llm = anthropic.Anthropic()
    written = 0
    for story in stories:
        lead = wikipedia_lead(client, story["article"]) if story["article"] else None
        if lead is None:
            continue
        games = [names[s] for s in story["members"][:12]]
        story["headline"] = headline(llm, story["name"], games, lead)
        written += story["headline"] is not None
    return written


def names_of(selection: Sequence[tuple[WikidataGame, Any, bool]]) -> dict[str, str]:
    return {slug_of(g): display_name(g.article) for g, _, _ in selection}
