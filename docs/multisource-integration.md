# Multisource integration: recorded sources to the product UI

Branch `product/multisource`, cut from `product/local-ui` at `ec679aa`. Offline and
private. No source was called, no credential was read, nothing was deployed.

## What runs

```
cd apps/dev-ui
npm run opportunities:integrated      # every genuine recorded source, read together
npm run opportunities:recorded        # pinned recordings only (the verified view)
METASPIKE_PRIVATE_OPPORTUNITIES=1 npm run dev
# http://127.0.0.1:3000/opportunities?view=integrated
# http://127.0.0.1:3000/opportunities/counter-strike-2?view=integrated
```

`opportunities:integrated` runs two existing commands. Nothing new sits in between.

1. `metaspike sample --replay tests/fixtures/steam_players`. This replays the one recorded
   Steam response into a fresh private store, `.metaspike/private/integrated/`, which is
   git-ignored.
2. `metaspike evidence --as-of 2026-10-06T01:30:00+00:00 --opportunities`. This reads the
   recorded catalogue, the pinned pageviews and that store. It writes
   `.metaspike/private/opportunities/integrated.json`.

The same inputs give the same bytes. `tests/test_integrated_recorded.py` runs both
commands with every socket refused and the wall clock moved to 2027. It requires the
exact bytes of the UI's test fixture, so the path from source to game to UI is held
end to end.

## What the UI shows

- **Board** (`/opportunities?view=integrated`):
  - a notice: recorded sources, not live data. Unverified inputs count for nothing.
  - the unverified sources, listed by name.
  - for each game, every demand and event source, with its state and its origin.
- **Game page:** the source table shows source, kind (identity, demand or event), state,
  latest observation, freshness and origin.
- **Unchanged:** readiness, order and the brief. They are the engine's own and are
  verified-only.

## Inputs, by standing

| Input | Standing | Reaches |
|---|---|---|
| IGDB and Steam app-list catalogue records (`tests/fixtures/catalogue`, `bootstrap_catalogue`) | **verified** (pinned) | identity only, through `resolver_v2`. Never a demand signal |
| Wikimedia pageviews, six articles, 6 July to 3 October 2026 (`tests/fixtures/wikimedia`) | **verified** (pinned) | the six games with an article recording. At 6 October 01:30 they are `stale`, because 4 October had fallen due and is not held |
| Wikimedia pageviews, the other eight bootstrap articles | **absent** | `missing`. Nothing is filled in |
| Steam concurrent players, one recorded response (app 730, 2026-10-06 01:25:26 UTC) | **genuinely recorded, unverified** (not pinned) | Counter-Strike 2: `available`, `fresh`. Shown and never counted. It is one observation, with no history (`maturity no_history`) |
| Steam concurrent players, the other seven eligible apps | **absent** | `missing`. No attempt is invented |
| Steam News | fixtures **synthetic**; the 11 October probe store is not on this machine | `not_collected` |
| YouTube creator supply | **absent** (no source, no collection) | `not_integrated` |
| Reddit community interest | **absent** (approval PENDING) | `not_integrated` |

**Result:** every game abstains (`insufficient_evidence`). No game has a current,
verified demand observation at a moment when another source also observes it. That is
the honest state of the recorded data, not a defect.

## Defects found and fixed here

1. **The collector marked a slot done without its due period.** This is the C2-F
   coverage gap.
   - Before: `collect_wikimedia` marked a target complete whenever the answer held any
     row.
   - Now:
     - an answer without the due period is still landed;
     - it counts as `targets_due_period_missing` and logs a `due_period_missing`
       WARNING;
     - it leaves no marker, so a later run asks again;
     - the run and the work-order receipt are partial.
   - Regression tests in `tests/test_jobs.py` reproduce the 18 October answer.
2. **A replayed Steam response was dated by the wall clock.**
   - Before: `metaspike sample --replay` stamped a 6 October answer with the time it was
     replayed, so it read as a current observation, and it invented failed attempts for
     apps with no recording.
   - Now:
     - a replay uses a clock that starts at the recording's `recorded_at`;
     - it asks only apps that have a recording;
     - it refuses a `--scheduled-for` later than the recording.
   - Tests updated in `tests/test_steam_players.py` to the stricter behaviour.

No frozen rule, score, schema or source policy was changed. The public artifact
contract stays 1.5.0.

## Steam: what remains before the first supervised real sample

The policy permits `private_development` use today. A supervised live sample also needs
the following, in order:

1. **Owner authorisation** of one supervised session, with its scope stated: the eight
   eligible bootstrap apps, one slot, eight requests. Separate from any other task.
2. **The key.** `METASPIKE_STEAM_WEB_API_KEY` is set in the owner's shell. It is never
   printed or committed. The registration should identify the MetaSpike /
   krishnachittella.dev application, which the policy names as a collection
   condition.
3. **A supervised Steam recorder: built, offline-tested, never run live.**
   `metaspike record-steam-players` (`src/metaspike/steam_players_recorder.py`):
   - plans by default, for the current 15-minute slot; the plan id covers the slot;
   - at most 8 eligible bootstrap apps, 8 requests, one attempt each;
   - `--live --confirm PLAN_ID`, inside the same slot, with the key in
     `METASPIKE_STEAM_WEB_API_KEY`;
   - writes `current_players_<app>.json` in the replay envelope, plus `body_text`;
   - writes one `acquisition_record_v1` per kept response;
   - `--inspect` checks the files, replays them and reports the digest.

   An answer that arrives after the slot ends is not kept.
4. **Review, then pin.** The owner reviews the recording and approves pinning its digest.
   It is then added to `PINNED_RECORDINGS` in a reviewed commit. Only then is a Steam
   observation verified.
5. **History before it means anything.** One sample has no baseline. The feature engine
   reports `no_history` until enough hours and days are held. Scheduled sampling is
   `private_collection`, which the policy does not clear. That needs:
   - the privacy notice;
   - US-only storage;
   - the key registration;
   - far fewer than 100,000 calls a day.

## Steam: what remains before anything Steam-derived could be public

- **Valve's answer** on derived metrics and commercial use. Both are UNKNOWN in the
  policy. Until then nothing computed from Steam may enter a published artifact. The
  publishing path enforces this, and nothing here bypasses it.
- **The privacy and data notice** with a Steam Data section, at
  `krishnachittella.dev/privacy`. It is an owner action, and the portfolio site is not
  touched here.
- **Valve attribution, branding and links**, plus "as is" disclaimers, on any page
  showing Steam data.
- **A reviewed change to the public artifact contract.** Version 1.5.0 carries no Steam
  field.

## Other sources, within their actual permissions

| Source | Permitted today | Next step that needs no new permission | Blocked on |
|---|---|---|---|
| IGDB | private collection, identity only | none needed for the integration: its records already bind games | public: attribution, and IGDB's partnership answer |
| Steam News | private development | an acquisition-record recorder for the supervised probe, like the Steam one above | Steam conditions, the titles decision, a backfill budget; public: Valve |
| YouTube | private development, offline parsing only | none: there is no API project | an owner API project and terms acceptance; YouTube's answer on derived metrics |
| Reddit | none | none | Reddit's approval (PENDING) |
