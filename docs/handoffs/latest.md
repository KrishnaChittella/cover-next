# Handoff: latest checkpoint (product branch)

## PRODUCT BUILD U6.0: OFFLINE MULTI-SOURCE EVIDENCE INTEGRATION — CHATGPT REVIEW REQUIRED

Branch `product/local-ui` only. Offline. No production action, no live source call, no
credential, no cloud resource, no deployment, no pull request, no merge to `main`. This
file is the product-branch handoff; `main`'s handoff was not touched. **No further
checkpoint has been started.**

- **Approved starting SHA:** `a009ea3315f8b0df389d1e039b99dbc387040522` (U5.3 handoff).
  Verified at the start: on `product/local-ui`, at that commit, tree clean.
- **Implementation SHA:** `18b72bc38f869039515b45e0f327a10c58423d3b` —
  `feat: integrate offline multi-source game evidence`.
- **Production `main`:** `cb33198e2510965e41dff0f5fe7e75cacb7ee2ca`, unchanged.

**In one line:** a local, private command now reads what is held about a canonical game
at a moment, source by source, using the existing components unchanged; and the honest
result on real data is that **no game has two real demand signals at the same moment**,
so multi-source integration is demonstrated only on clearly labelled synthetic data.

## Three things to review first

1. **Pageview freshness is read at the source's due boundary, not the wall clock.** The
   frozen engine's daily freshness counts whole days from the last bucket to the moment
   it is read and knows nothing of publication lag. Read at the wall clock, daily
   pageviews are `stale` even when every due day is held (seen on the recorded data at
   2026-10-05 12:00). The evidence layer hands the engine the end of the latest period
   the source was due to have published. Pageviews are then stale exactly when a due day
   is missing, as score v0 treats it. **No engine rule or constant changed**
   (`pageviews_read_at` in `evidence/assemble.py`). This is a judgement call of mine.
2. **Metadata history is not kept.** Only a source record's latest version is held. A
   reading before a record's latest fetch does not use it, and what leaned on it reads
   as `unknown`. It never borrows the later version. This is a limit, not a fix.
3. **Synthetic origin is declared, not detected.** Stores carry no origin. The command
   labels its own scenario synthetic and refuses to mix it with stored evidence, but a
   store someone filled by replaying the synthetic Steam News fixtures and then passed
   as `--events-dir` would be labelled `local_store`. The fixtures' README and the
   doc say so; nothing enforces it.

## Exact changed files

| File | Change |
|---|---|
| `src/metaspike/evidence/__init__.py` | New package. |
| `src/metaspike/evidence/model.py` | New. The typed reading and its eight states. |
| `src/metaspike/evidence/assemble.py` | New. `assess_evidence`, the policy gate, the pageview cut. |
| `src/metaspike/evidence/render.py` | New. Text output; no source text printed. |
| `src/metaspike/evidence/synthetic.py` | New. The labelled synthetic scenario. |
| `src/metaspike/cli.py` | New `evidence` command and its helpers. No existing command changed. |
| `tests/test_evidence.py` | New. 27 tests. |
| `docs/multi-source-evidence.md` | New. |
| `README.md` | One paragraph pointing to the command and the document. |

Nothing else. No change under `infra/`, `.github/`, `apps/`, `schemas/`,
`tests/fixtures/`, or to any resolver, feature, scoring, event, validation, publishing,
source or policy module. No artifact schema changed (contract still 1.5.0;
`export-schemas --check` is clean). No new persisted format: the report is printed, or
written only where `--out` says.

## Integrated sources

| Source | Kind | How it is read |
|---|---|---|
| IGDB | catalogue metadata | Recorded rows, resolved by `resolver_v2`; bindings at `as_of` |
| Steam app list | catalogue metadata | The same |
| Wikimedia pageviews | demand signal | Recorded responses, cut to what was published by `as_of` |
| Steam concurrent players | demand signal | The sample store, point in time, through the binding valid at observation |
| Steam News | event evidence | The event store and the source store, as of `as_of` |
| Creator supply | — | `not_integrated` |
| Community interest | — | `not_integrated` |

## Reused components (none modified)

`MappingHistory` · `steam_eligibility`, `sampling_targets`, `active_binding` ·
`build_time_series`, `latest_due_period` · `steam_family_v2`, `wikimedia_family_v2`,
`read_entity_history` · `hourly_aggregates`, `daily_aggregates`, `current_level` ·
`usable_features` (as `Consumer.OPPORTUNITY_SCORE`, so the shadow-only change point is
withheld) · `entity_evidence_v2` · `assess_entity` (score v0) · `assess_events`,
`upcoming_radar`, `coverage_of` · `EventStore`, `EventSourceStore.latest_candidates` ·
`require_catalogue_sources`, `retained_sources`, `require_event_source`,
`SourcePolicy.registration_refusal` · and, in the scenario, the real `SteamNewsAdapter`,
`ingest`, classifier and promotion gate through `replay_news_client`.

## Real versus synthetic

**Real and recorded, in the repository:** 402 catalogue rows; daily pageviews for six
games through 3 October 2026.

**Real, local to this machine only:** the fifteen announcements of the one supervised
Steam News probe of 11 October (three apps). Not in the repository; no new request made.

**Not held anywhere:** player-sample history. No sample store has ever been filled.

**Synthetic:** everything the scenario writes (player samples for four apps,
announcements for three), and one rebinding built inside two tests. Labelled in every
place it surfaces; never `usable`; never counted as a demand family observing a game.

Tests on recorded data: identity, metadata, quarantine, pageviews, the publication-lag
cut, score v0 parity, policy. Tests on synthetic data only: everything about player
samples, promoted and upcoming events, candidates.

## Working demonstration command

```
uv run metaspike evidence --as-of 2026-10-05T12:00:00+00:00 \
    --wikimedia-replay tests/fixtures/wikimedia --recorded-through 2026-10-03

uv run metaspike evidence --as-of 2026-10-20T12:00:00+00:00 \
    --wikimedia-replay tests/fixtures/wikimedia --recorded-through 2026-10-03 \
    --synthetic-scenario
```

Both were run. Offline, read-only, byte-identical on a second run (tested).

## End-to-end examples (actual output, abridged)

**Real, 2026-10-05 12:00 UTC — Minecraft:**

```
Minecraft  (minecraft)
  canonical id d1c9712e-1691-49c0-be37-a3ea0dd06760  [frozen_bootstrap]  lifecycle EVERGREEN
    no source record was bound to this game at this moment
    quarantined, NOT bound: igdb:121 (title_only)
  igdb                  catalogue_metadata unresolved
  wikimedia_pageviews   demand_signal      available      latest 2026-10-04 00:00Z ids Minecraft
  steam_current_players demand_signal      unknown        No source record is bound to this entity.
  steam_news            event_evidence     not_collected
  creator_supply        creator_supply     not_integrated
    families that could observe this game: 1; with current REAL observations: 1
    wikimedia_readership: current_level 6013, expected_level 5138, log_lift 0.1573, robust_z 0.1506
      not usable for scoring (shadow-only): change_point
  Score v0 (pageviews only, unchanged): 12.2, confidence 59 (medium)
  Events: UNAVAILABLE (no_event_coverage). That is not the same as no events.
  Usable now: wikimedia_pageviews
```

**Real, the fifteen original games at that moment:** pageviews available for 6, missing
for 8 (no recording held), unknown for 1; Steam players eligible but not collected for 8,
inapplicable for 5, unknown for 2. **Games with two demand families: 0.**

**Real, 2026-10-12 00:00 UTC with this machine's local event stores:** the six pageview
series are `stale`; Counter-Strike 2, Dota 2 and Baldur's Gate 3 have current Steam News
coverage, five announcements read each, none promoted, event heat 0.

**SYNTHETIC, 2026-10-20 12:00 UTC — Dota 2 (invented; not a finding):**

```
  steam_current_players demand_signal      available      latest 2026-10-20 11:45Z ids 570   [SYNTHETIC, private only]
  steam_news            event_evidence     available      latest 2026-10-20 11:00Z ids 570   [SYNTHETIC, private only]
    families that could observe this game: 2; with current REAL observations: 0
    steam_players (concurrent_players, maturity partial_baseline)  [SYNTHETIC]
  Events: coverage current; event heat 30.720; 2 relevant event(s)
    upcoming: season starts 2026-10-23 00:00Z (day precision), in 60 h
    announcements read: 3; promoted to events 2; candidates only 1
  SYNTHETIC, not observations: steam_current_players, steam_news
```

## Point-in-time correctness

- Bindings at `as_of`; a later rebinding leaves an earlier reading identical to one made
  from the state that never had it; samples before and after a rebinding stay with the
  entity bound at the time; stored samples byte-identical afterwards.
- Samples, announcements, events and checks stored after `as_of` are not read; the same
  stores read two days apart show the announcements and the synthetic climb only later.
- Pageviews: the cut equals `RecordedHistory.inputs_at` at four moments; on 4 October at
  noon the 3rd is recorded and not yet published, and is not used.
- A moment before the catalogue state existed is refused, as is a naive time.
- Quarantine entries first seen later are not listed. Metadata: see item 2 above.

## Acceptance tests A–T

| | Covered by |
|---|---|
| A, B | `test_pageviews_alone…`, `test_metadata_gives_identity…` (recorded) |
| C–I | samples association, inapplicable, never sampled, reported zero, stale/fresh, incomplete hour and day, several apps (synthetic samples) |
| J | `test_a_quarantined_record_is_never_treated_as_bound` (recorded: `igdb:121`) |
| K | `test_a_later_rebinding_changes_nothing_before_it` |
| L–O | candidate only, promoted event, upcoming with timing, timing not established |
| P | `test_a_reading_uses_nothing_later…`, `test_pageviews_are_cut…` |
| Q | `test_restricted_evidence_cannot_be_read_for_a_public_stage_or_published` |
| R | `test_synthetic_evidence_is_never_presented_as_an_observation` |
| S | `test_creator_supply_and_community_interest_are_absent_not_zero` |
| T | `test_score_v0_is_what_it_has_always_been`, `test_other_evidence_does_not_move_score_v0` |

Plus the command's own tests (offline, deterministic, refusals) and one asserting the
layer defines no rule of its own.

## Source-policy gate results

- Every input's source must have a policy clearing the stated stage for its purpose
  before anything is read. Missing policy, or a policy under another source's name: refused.
- At `public_launch`: the recorded catalogue is refused (IGDB not cleared); with only
  pageviews and the bootstrap entities it is allowed; adding synthetic samples, the event
  store or the source store is refused, each by its own policy. `private_collection` and
  `commercial_production` with the scenario: refused.
- The report is `private_development_only` always. Nothing in `publishing/` imports it;
  `schemas/` has no entry for it; `--out` refuses the artifacts directory.
- No source policy was changed and no unknown permission was read as granted.

What stands between each source and a noncommercial public launch, from the policies:
Wikimedia nothing; IGDB attribution in the product and IGDB's answer on the partnership
question; Steam app list branding, links, disclaimers and a privacy policy (the branding
guidance has not been located); Steam players and Steam News an explicit answer on
derived metrics and events (both `unknown`). Detail in `docs/multi-source-evidence.md`.

## Existing score parity

With the publication lag set to zero, the reading at midnight after the recording
reproduces the frozen `score_v0_replay_golden.json` score and confidence for all fifteen
games (six scored). Under the real one-day lag it equals `assess_entity` on the
backtest's own inputs. Adding samples and events changes no score or confidence.

## Golden resolver parity

`uv run metaspike evaluate-resolver`: golden set v3, 200 cases, hash `c2b3aef7b372`,
**0 false automatic merges**, exit 0. No resolver file changed.

## Quality gates

Backend, on the committed tree (2026-10-14):

- `uv run ruff format --check` — 229 files already formatted (230 after the handoff's
  doc-only follow-up check; clean both times)
- `uv run ruff check` — All checks passed
- `uv run mypy` — no issues in 216 source files
- `uv run pytest` — **1,535 passed, 8 deselected in 347.17s** (1,508 before, plus the 27
  new tests). The three test files that read the docs and README were re-run after the
  documentation was written: 146 passed.
- `uv run metaspike export-schemas --check` — Schemas are up to date
- `uv run metaspike evaluate-resolver` — exit 0, 0 false auto merges
- `uv run pytest tests/test_evidence.py` — 27 passed in about 40 s

Not run: `live` tests (no live call is permitted) and the four `slow` catalogue proofs
(no catalogue or resolver code changed; last run in U5.3, 4 passed).

Frontend: **not touched** (no file under `apps/` changed, no artifact contract changed).
Not re-run here; last verified in U5.3 on 2026-10-13: `contract:check`, `typecheck`,
`lint`, `test` (179 passed), `build`, all passed.

## Measured local cost

Apple M3, Python 3.12.15, one run each: fifteen games on recorded inputs 1.4 s, 72 MB;
all 234 entities as JSON 1.5 s, 87 MB; fifteen games with the synthetic scenario 7.3 s,
95 MB; all 234 entities with a synthetic sample store about 35 s in process. Laptop
figures only. No cloud resource was used.

## Production isolation (read-only, 2026-10-14 01:51 UTC)

- `main` = `cb33198`, level with `origin/main`, clean. No pull request. Last workflow
  run still 38104941975 (11 October, `main`).
- Terraform state generation `1791685217422653`: unchanged. No Terraform was run.
- Control record generation `1791684706340016`: unchanged.
- Scheduler ENABLED, configuration unchanged; only `lastAttemptTime` differs from the
  previous snapshot. Jobs, IAM, dataset schemas, secrets and alert policies identical.
  0 secret versions; 0 user-managed keys across 10 service accounts.
- Repository variables unchanged (`SCHEDULER_PAUSED=false`, `SOAK_MONTH=10`,
  `SOAK_START_DAY=12`, `SOAK_END_DAY=18`, `SOAK_DAILY_EXECUTION_CEILING=31`).
- `BROAD_DISCOVERY_ENABLED` is still `False`. No collector was activated. No Wikimedia,
  Steam, IGDB, YouTube or Reddit request was made. The portfolio repository was not touched.

## C2-F observation (read-only, 2026-10-14 01:51 UTC; separate; not altered; not judged)

| | |
|---|---|
| Executions since the window opened | 54; all 54 succeeded; none running |
| 12 October | 24 dispatcher, 1 Wikimedia collector, 1 warehouse |
| 13 October | 24 dispatcher, 1 Wikimedia collector, 1 warehouse |
| 14 October so far | 2 dispatcher (00:00, 01:00) |
| Warehouse rows | `collection_runs` 120, `expected_targets` 56, `period_observations` 1,624 |

Two days into a seven-day window. This is not a statement that C2-F has passed. Nothing
was triggered, restarted, paused, cancelled or changed.

## Unfinished source integrations

- **Creator supply (YouTube):** no adapter, no policy, no fixtures. Reported as
  `not_integrated`.
- **Community interest (Reddit):** the same.
- **Steam players:** the adapter, store and features exist; no real history is held.
- **Steam News:** real coverage exists for three apps on one day, locally only.
- **Wikimedia:** six of fifteen games recorded; 14 of 234 entities have an identifier.

## Concrete blockers for final intelligence

1. No game has two real demand families at one moment, so nothing about agreement
   between sources can be measured yet.
2. No creator-supply or community-interest source, and no policy for either.
3. No opportunity score is defined: no rule for combining families or weighing demand
   against supply. Score v0 reads pageviews alone.
4. Steam players and Steam News clear private development only; their derived-output
   permission is `unknown`, so nothing from them can be published as things stand.
5. No combination rule for games with several Steam apps.
6. Metadata history is not kept (item 2 at the top).
7. Wikimedia identifiers exist only for the original fifteen games.

## Commits

`18b72bc` feat: integrate offline multi-source game evidence; then this handoff. Pushed
to `origin/product/local-ui` only. No PR, no merge to `main`, no deployment.

## Exact next approval required

Independent ChatGPT review of U6.0, then explicit user authorisation for whatever comes
next. The YouTube, Reddit, final scoring, deployment and public-launch checkpoints have
not been started.

STOP.
