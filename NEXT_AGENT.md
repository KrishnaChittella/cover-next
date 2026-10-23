# Cover Next: start here (written 2026-10-23 for the next agent)

Read this file, then `CLAUDE.md`. Do not read `docs/` by default. This replaces the
earlier NEXT_AGENT.md; the full engineering overview is `docs/engineering.md`.

## 1. What this is

**Cover Next** (code name `metaspike`) answers one question for gaming YouTubers: *which
game is worth a video right now, and why?* Live at **https://cover-next.krishnachittella.dev**
(GitHub Pages, custom subdomain, DNS at Cloudflare). Repo: `KrishnaChittella/cover-next`,
**public**, MIT licence (code only; data keeps its providers' terms). Target: full
development by **5 November 2026**.

The owner is learning; explain in plain language, give one-line terminal commands
(`! <command>`), and name where to look in a console. No accounts, logins or cookies on
the site, ever. Every number must come from data; "not measured" is shown instead of
inventing one. Copy must not read as AI-written (`/no-ai-slop`).

## 2. How it works today (all merged on `main`)

Daily GitHub Actions workflow `.github/workflows/site.yml` (13:30 UTC, also manual):

1. `metaspike discover-games` (`src/metaspike/discovery/`): Wikidata lists every video game
   with an English Wikipedia article (~30,639, with IGDB P5794 and Steam P1733 ids). The
   day's Wikimedia pageview dump (~750 MB, ~3 min) gives every game's views; history is
   backfilled through the existing Wikimedia adapter for games with ≥150 views (~2,200,
   ~12 min). Each game is compared with **its own** normal: 3-day mean against the median
   of the 28 days before (`risers.py`). Output `risers-<day>.json`.
2. `metaspike collect-youtube --stage private_collection`: one `search.list` per game for
   the top 60 risers then the 18 pinned games (`discovery/catalogue.py::PINNED`, includes
   Wardogs, Apex Legends, Valorant, Hollow Knight). Query `"<name> game"`, relevance order,
   last 7 days, Gaming category.
3. `metaspike site-data`: `publishing/site.py` builds one JSON for the site: top 100 risers
   + up to 20 new + pinned (119 today). Game id = UUID derived from the Wikidata QID
   (`catalogue.game_id`), never a title.
4. `apps/site` (Next.js 16 static export) builds `/`, `/games/`, `/games/<slug>/`, privacy
   and how-it-works, then deploys to Pages. Nothing collected is committed.

Design: owner's Stitch three-column mock (dark `#0a0b12` / cards `#121422` / borders
`#20243b`, sunset `#f97316`, sky `#38bdf8`, violet `#a855f7`; Plus Jakarta Sans +
JetBrains Mono) with a derived light theme. The mock HTML was never saved to disk; the
owner should compare and correct.

## 3. Rules from the providers (do not work around them)

| Source | Public site | Why |
|---|---|---|
| Wikimedia, Wikidata | Yes | CC0 |
| YouTube | **Individual videos only** (title, own views, link) | Developer Policies III.E.4.h (no derived metrics) and III.E.2 (no combining across channels). No medians, totals, counts, ratios |
| IGDB | Identity, attribution in footer | Recorded identity rows in `tests/fixtures/` are the real catalogue; keep them (owner decision) |
| Steam players/news | **Not yet** | Steam policy needs Valve's answer on derived figures + privacy notice. Valve email not sent yet |
| Twitch | **Not yet**, 24 h cache only | Developer Agreement storage clause. Owner emailed legal@twitch.tv on 2026-10-23; awaiting reply |

The owner once asked to disguise restricted data as "model output"; it was declined and he
agreed. Keep it that way.

A YouTube video is shown only if `publishing/site.py::_about_game` passes: association
rule SUPPORTED, the game's name in the **title**, and for one-word names a YouTube
game-**genre** topic (not `*_culture`). Added after the live site showed music videos and
an esports song for "Anthem".

## 4. Owner decisions already made (do not re-ask)

Hosting GitHub Pages at the subdomain; daily refresh; deterministic angles first, LLM
later (ask before adding one); IGDB rows stay with attribution; MIT; pinned games above;
light + dark; home = top 15 + "See all"; `/games/` 25 at a time; per-game pages.

## 5. Working rules

- Routine code/docs PRs: merge yourself after CI is green. **Read the run's `conclusion`
  (`gh run view <id> --json conclusion`), never a wait command's exit code** (PR #19 was
  merged red that way). A PR has two CI runs (push + pull_request); check both.
- Ask first: IAM/WIF, secrets, enabling a source in production, Terraform destroy,
  anything newly public. The permission system blocks editing `infra/bootstrap` and
  live calls that use secret keys; hand those to the owner as `!` commands.
- zsh does not split `$ids` in `for r in $ids`; use `${=ids}`.
- YouTube quota: 100 `search.list` calls per Pacific day **shared** by local runs and CI.
  A daily CI run spends ~78. Failed searches still spend quota.

## 6. Open work, in suggested order

1. **Studio / news stories (owner's latest ask).** Today's top risers are 21 BioWare games
   because EA shut BioWare down; the owner wants one story, not 20 dead games. Plan in §7.
2. Forecast line on game pages from Wikipedia history (honest projection with a band).
3. Small-channel breakouts: `channels.list` subscriber counts, show the two raw numbers
   side by side (no computed ratio on the page).
4. Cache the discovery store between Actions runs (`actions/cache`, pin the SHA and add it
   to the allowlist in `tests/test_cloud_foundation.py`) so reruns take ~3 min, not ~20.
5. Draft the Valve email (players, trends, patch-note dates) like the Twitch one.
6. WIF fix (owner applies): `infra/bootstrap/main.tf` visibility `'private'`→`'public'`,
   tfvars `github_repository = "KrishnaChittella/cover-next"`. Only needed before cloud
   deploys; production Wikimedia jobs keep running meanwhile.
7. `metaspike refresh` for the owner's private all-source reading (Steam, Twitch).
8. Twitch: add to `source_readiness.py`; when legal replies, update `sources/twitch.py`.

## 7. Plan for studio stories

- Wikidata gives developer (P178) and publisher (P123) per game; 21 of the top 40 risers
  on 22 Oct share BioWare.
- When ≥3 risers share a developer or publisher, show **one story card** ("BioWare: 21 of
  its games spiking together") with the studio's own Wikipedia views, the member games
  collapsed inside, and individual YouTube videos that name the studio. Remove members
  from the main list (keep the strongest one, marked "part of the BioWare story").
- "Why" text: start deterministic from data (studio article spike + video titles); an LLM
  one-line summary of headlines is the owner's call (cost/accuracy; not approved yet).
- Shut-down or discontinued games: still a story for retrospectives, but never flood the
  list; Wikidata P2669 (discontinued date) can mark them.

## 8. Handy commands

```
uv run metaspike discover-games --show Wardogs Hollow_Knight   # private ranking, ~15 min first run
set -a; source .env; set +a; uv run metaspike collect-youtube --max-searches 20
uv run metaspike site-data --out apps/site/data/site.json
cd apps/site && npm run dev            # http://127.0.0.1:3001
gh workflow run site.yml               # publish (about 20 min)
```
