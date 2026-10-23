# Cover Next

**https://cover-next.krishnachittella.dev**

A free site for gaming YouTubers that answers one question: which game is worth a video
right now?

Every day it checks the same list of games and shows, side by side:

- **Wikipedia views** for the last 7 days against the 7 before. A sudden jump usually
  means an update, a launch or some news, and it tends to come before a wave of videos.
- **The most-watched YouTube videos** about each game from the past week, each with its
  own view count and a link.

Games are labelled *rising*, *steady* or *cooling* from the Wikipedia change. You can
search, sort and star games into a watchlist. The watchlist stays in your browser.

It is an early preview. The numbers are readings, not advice.

## Where the data comes from

| Source | Shown on the site | Notes |
|---|---|---|
| [Wikimedia page views](https://wikimedia.org/api/rest_v1/) | Yes | Public domain (CC0) |
| [YouTube Data API](https://developers.google.com/youtube/v3) | Yes, video by video | Never added up or averaged across channels, as YouTube's rules require |
| [IGDB](https://www.igdb.com) | Game identity only | Game metadata from IGDB.com |
| Steam players and news | Not yet | Collected privately while Valve's terms are confirmed |
| Twitch viewers | Not yet | Kept 24 hours at most; written permission has been requested |

PlayStation and Xbox have no public source for player numbers, so they are left out.
A missing number is shown as "not measured", never as zero. The recorded IGDB rows under
`tests/fixtures/` hold identity fields only; they are the catalogue's source records, not
a dataset for reuse.

## How it updates

A GitHub Actions workflow ([`.github/workflows/site.yml`](.github/workflows/site.yml))
runs once a day: it asks YouTube one question per game, reads Wikipedia's page views,
builds the site and publishes it to GitHub Pages. Nothing it collects is committed to
this repository.

## Running it yourself

You need [uv](https://docs.astral.sh/uv/) and Node 20 or newer.

```sh
uv sync
cd apps/site && npm install

# Optional: put METASPIKE_YOUTUBE_API_KEY=... in .env first, then
#   set -a; source ../../.env; set +a
#   (cd ../.. && uv run metaspike collect-youtube)

npm run data      # writes apps/site/data/site.json
npm run dev       # http://127.0.0.1:3001
```

Without a YouTube key the site still builds; YouTube shows as "not measured".

## What's in here

```
apps/site/      the public site (Next.js, static export)
src/metaspike/  the data pipeline in Python: sources, game identity, readings
tests/          offline tests; recorded or invented responses only
warehouse/      dbt models for the private Google Cloud warehouse
infra/          Terraform for the private Google Cloud project
docs/           design notes
```

The code still uses the project's working name, `metaspike`. The full engineering
overview (rules, invariants, warehouse, cloud) is in
[docs/engineering.md](docs/engineering.md).

## Privacy

No accounts, no cookies, no analytics. See the
[privacy page](https://cover-next.krishnachittella.dev/privacy/).

## Contact

Open an [issue](https://github.com/KrishnaChittella/cover-next/issues), or find Krishna
Chittella at [krishnachittella.dev](https://krishnachittella.dev).
