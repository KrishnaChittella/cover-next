# Handoff: latest checkpoint (product branch)

## PRODUCT BUILD U6.7: SUPERVISED WIKIMEDIA RECORDING — CHATGPT REVIEW REQUIRED

This checkpoint was worked on branch `product/local-ui` only, entirely offline:

- no live source request was ever sent;
- no cloud access, deployment or source activation;
- no pull request, merge or publication.

**The live recorder has not been executed. No later checkpoint has been started.**

## Things to review first

1. **The live path exists, but it cannot run by accident.** `record-pageviews` only plans
   by default. Sending requires both `--live` and `--confirm <plan id>`, where the id is
   the one the preview printed. A changed scope, window, destination or policy changes
   the id, and the run is refused before any transport is built. The single function that
   builds a network transport, `cli._live_transport`, was replaced in every test, and
   sockets were blocked.
2. **Rate limiting reuses the existing limiter unchanged.** That limiter enforces the
   policy's documented window (200 requests in 60 seconds), not a fixed gap, so a run of
   at most 15 requests never waits. What is enforced: strictly sequential requests,
   concurrency 1, no retry (`max_attempts=1`), and an attempt budget equal to the number
   of planned requests. The F test checks exactly this. It does not claim any pacing
   between requests.
3. **The digest covers the bodies only, as for the existing pinned recording.**
   Acquisition records and the manifest live in `acquisition/`, outside the digest, and
   are checked against the bodies by `--inspect`.
4. **Bindings carry `bound_from = MIGRATED_AT` (2026-10-04).** That is the bootstrap
   catalogue's binding origin. The seed catalogue holds no later binding time.

## 1. Approved starting SHA

`bca28c3adfd0c061c93d45ca89cf9670d186e659`. Verified at the start: on
`product/local-ui`, at that commit, tree clean.

## 2. Implementation SHA

`824f7f6478a461232dfcf4dbaa58a8c198438fa9`: `feat: prepare supervised Wikimedia recording
offline`.

## 3. Exact files changed

| File | Change |
|---|---|
| `src/metaspike/opportunity/history.py` | `material_content` replaces `_demand_values`; duplicates refused |
| `src/metaspike/pageview_recorder.py` | New: plan, record, inspect, render |
| `src/metaspike/cli.py` | `record-pageviews` command; `_live_transport`; `_recorder_sleep` |
| `src/metaspike/source_readiness.py` | Wikimedia `next_step` text names the new command |
| `tests/test_pageview_recorder.py` | New: 20 tests, mock transports only |
| `tests/test_opportunity_history.py` | 2 hardening regressions |
| `tests/test_evidence.py` | Allow-list names `pageview_recorder.py` |
| `docs/wikimedia-recording-runbook.md` | New: the owner runbook |
| `docs/source-readiness-and-real-data-activation.md` | Points at the runbook |
| `docs/handoffs/latest.md` | This handoff (separate commit) |

No frontend file or contract changed, so the frontend gates were not run.

## 4. Historical comparison hardening

For verified demand, `evaluate_history` now compares `material_content` of the supplied
recording with that of the replay of its pinned directory.

**What is compared:**

- the requested window, and the set of sources run;
- per signal: run source, game, signal key (source, metric and granularity), collection
  status, error code and message;
- per series: identity, granularity, window, `latest_due`;
- per point: game, source, metric, granularity, period, status, value, reference and
  source timestamp.

Entries are compared in a canonical sorted order, so failed and unmapped signals count. A
missing failure differs.

**Duplicates.** A second entry for the same source, game and key is refused ("more than
one entry"). It is never collapsed by a mapping.

**Excluded on purpose:** replay-time stamps (report and run times, each point's
`observed_at`, and the freshness lag derived from it). The replay is run at the
recording's own `observed_at`, so `latest_due` is still compared and nothing is backdated.

**Regressions.** All values were left unchanged in each case.

| Change | Result |
|---|---|
| A failure's error code changed | Refused |
| A failed signal dropped | Refused |
| A failure turned into "not mapped" | Refused |
| `latest_due` moved a day earlier | Refused |
| A point's article reference changed | Refused |
| A signal duplicated | Refused ("more than one entry") |
| Only the stamps changed (runs re-stamped, points' `observed_at` moved) | Accepted, same fingerprint |

**The U6.5 result is unchanged.** Fingerprint `ophist_b1d2af287f618f8ab1dbc9634b5b1292`,
546 assessments: 0 candidate, 35 watchlist, 511 insufficient (tested through the CLI).

## 5. Recorder CLI contract

```
metaspike record-pageviews (--game SLUG ... | --all-bootstrap) --out DIR [--through DAY] [--json]
metaspike record-pageviews ... --live --confirm PLAN_ID      # owner only; NOT EXECUTED
metaspike record-pageviews --inspect DIR [--json]
```

**Planning**

- Only explicit targets: bootstrap games with a bound article. That is 14, because FiveM
  has none. The maximum is 15.
- No discovery and no default selection.
- Duplicates are refused, and non-bootstrap games are refused.

**Window**

- One window of exactly 90 days, ending on `--through` or on the latest due day.
- The latest due day is `latest_due_period(DAILY, now, 24h)`, which is the day before
  yesterday.
- A day that is not yet due is refused.

**Destination**

- It must not exist, and neither may its `.incomplete` twin.
- It is refused under `artifacts/`, `public/`, `fixtures/`, `apps/`, `infra/` or
  `.github/`.

**Exit codes**

| Code | Meaning |
|---|---|
| 0 | Plan printed, or recording complete |
| 3 | Recording partial or failed |
| 4 | Inspection found a problem |
| 5 | Refused |

## 6. Replay and acquisition format

Each 200 response that the adapter parsed is kept, **byte for byte**, as `<article>.json`
at the top of the directory. That is exactly what `replay_client` serves and
`recording_digest` hashes.

**`acquisition/records.jsonl`** holds one `acquisition_record_v1` per kept body:

- source, purpose and stage;
- the policy's review date and digest;
- `GET <path>`;
- requested and received times;
- SHA-256 and length;
- the article, and its binding to the canonical game.

**`acquisition/manifest.json`** holds:

- the plan;
- run status (`complete`, `partial` or `failed`), and why it stopped;
- per target: status (`recorded`, `no_data`, `failed` or `not_attempted`), error code,
  and the exchange (method, path, query, times, HTTP status, kept response headers, body
  length);
- requests sent;
- the bodies' digest, marked NOT pinned;
- standing `unverified`, with the trust statement.

**Never written:** request headers (so no User-Agent contact), cookies, authorization,
response headers outside an allowlist (content-type, content-length, date, etag,
last-modified, cache-control, age, retry-after), environment values or credentials.

## 7. Source and rate-limit safeguards

- **Policy.** `registration_refusal(demand_signals, private_development)` runs at plan
  time and again at record time. If the policy digest changed since planning, the run is
  refused.
- **Requests.** The existing `WikimediaPageviewsAdapter` and its limiter are used, with
  no retry and a one-attempt-per-target budget.
- **Request identity.** Each request sent must equal the planned method and path, with no
  query, or the run stops.
- **When the run stops.** It stops at the first error other than 404: a 429, 401, 403 or
  5xx, an invalid body, or a network failure or timeout. Later targets become
  `not_attempted`. A 404 is `no_data`, and the run continues. Nothing is invented for any
  failure.
- **Output.** It is written to `.incomplete` and renamed at the end.

## 8. Origin verification boundaries

- **A new recording is UNVERIFIED.** `recording_provenance` and `--inspect` both say so.
- **What an acquisition record proves.** It proves integrity and permitted collection.
  It does not prove that Wikimedia sent the bytes, and it gives no permission for public
  use.
- **The only route to verified.** Inspection, then owner review, then owner approval,
  then the digest pinned in `PINNED_RECORDINGS` in a reviewed commit.
- **No shortcuts.** There is no auto-pin, no override flag, and no change to
  `PINNED_RECORDINGS` in U6.7.

## 9. Offline end-to-end evidence

`test_a_reviewed_and_pinned_mock_recording_reaches_the_opportunity_engine` walks the whole
path:

- **Recording.** An invented Wikimedia response goes through the recorder over a mock
  transport. The raw bodies, the 14 acquisition records and the manifest are written.
- **Before the pin.** Inspection passes, with standing `unverified`. Read by `metaspike
  evidence --opportunities`, the pageviews are `unverified` and every entry is
  `insufficient_evidence`.
- **Test-only pin.** `provenance.PINNED_RECORDINGS` is monkeypatched in that test only;
  the real table is untouched.
- **After the pin.** The pageviews read as `verified`. The invented Minecraft rise
  reaches `provisional_watchlist` through the unchanged rubric, in a private,
  development-only report. Nothing goes under `artifacts/`.

The values are invented. Nothing is claimed about any real game.

## 10. Tests and quality gates (actual results)

**Recorder tests A to W**

| Test | What it covers |
|---|---|
| A | A plan sends and writes nothing |
| B and C | Targets and bounds |
| D and E | The 90-day window and the publication lag |
| F | Sequential requests under the policy limit, with no retry |
| G, H, I and R | A byte-exact, replayable recording with its records, unverified |
| J | No cookie, secret header, User-Agent or contact written |
| K | Deterministic records |
| L, M and N | No data, and a 503, 429, 403 or two invalid bodies (parametrised) |
| O and P | Destinations refused |
| Q | Policy checked, and a change refused |
| S | Altered or extra content found by inspection |
| T | A transport failure stops the run, with no network fallback |
| — | The CLI live path needs the matching plan id (mock transport) |
| U | End to end |
| W | No cloud or production path is reachable |

V is the U6.5 fingerprint parity check, covered in `tests/test_opportunity_history.py`.

**Backend gates**

| Gate | Result |
|---|---|
| `uv run ruff format --check` | exit 0 |
| `uv run ruff check` | exit 0 |
| `uv run mypy` | exit 0, no issues in 246 files |
| `uv run metaspike export-schemas --check` | exit 0, up to date |
| `uv run metaspike evaluate-resolver` | exit 0; golden v3 `c2b3aef7b372`; false AUTO_MATCH merges 0; precision 100.0% (52/52); real-evidence recall 100.0%; NEW_ENTITY 100.0% |
| Focused suites (see below) | 226 passed |
| `uv run pytest` | **1,662 passed, 8 deselected**, exit 0 (previously 1,640) |

The focused suites were recorder, readiness, history, historical validation, evidence,
wikimedia, opportunity and v0 parity.

**Frontend gates:** not run, because no frontend code or contract changed. No live API
test exists or was run.

## 11. Future live command readiness

The command for an authorised owner session is:

```
uv run metaspike record-pageviews --all-bootstrap \
  --out .metaspike/recordings/wikimedia-YYYYMMDD --live --confirm PLAN_ID
```

It sends 14 requests, one at a time, with no retry. The output is reviewed with
`--inspect`. The step-by-step runbook is `docs/wikimedia-recording-runbook.md`: preview,
record, stop conditions, inspection, digest, pinning approval, private evidence, UI
refresh, stopping and rollback.

## 12. What was explicitly NOT executed

- Any request to Wikimedia or any other source.
- The live recorder: `cli._live_transport` was never called outside tests, where it was
  replaced.
- A real recording. `.metaspike/recordings` does not exist.
- Any pin.
- The C2-F acceptance review.
- Any GCP or production access.

## 13. Owner steps after C2-F acceptance

1. Complete the separate C2-F acceptance review and authorise a live recording session.
2. Set `METASPIKE_CONTACT`.
3. Preview the plan, then run it with `--live --confirm`.
4. Inspect the recording, and review the manifest.
5. Approve pinning the exact digest. Claude adds it to `PINNED_RECORDINGS` in a reviewed
   commit.
6. Generate the private evidence and the opportunity report with `--wikimedia-replay`.

## 14. Frozen-rule parity

These are unchanged:

- resolver_v2;
- baseline_rules_v1;
- signal_semantics_v2;
- metaspike_score_v0;
- event_heat_rules_v1;
- event_classification_rules_v1;
- validation_protocol_v1;
- outcome_rules_v1;
- recommendation_snapshot_v2;
- creator_opportunity_rules_v1.

No file changed under `sources/`, `domain/`, `validation/` or `evidence/provenance.py`,
nor in `opportunity/rules.py`. So the collectors, source policies and `PINNED_RECORDINGS`
are unchanged, and so is the public artifact contract 1.5.0.

Steam, YouTube, Reddit and event intelligence remain on the roadmap, untouched.

## 15. Git-only C2-F isolation

C2-F isolation: Git-only verification. No GCP access, no production action.

Verified with Git:

- Only `product/local-ui` changed.
- `main` is untouched: `origin/main` and the main worktree are both at
  `cb33198e2510965e41dff0f5fe7e75cacb7ee2ca`.
- No changes under `infra/`, `.github/`, `artifacts/`, `schemas/`, `apps/` or
  `tests/fixtures/`.
- `BROAD_DISCOVERY_ENABLED = False` is unchanged.
- No deployment, source API contact, source activation or publication.

## 16. No later checkpoint started

Nothing beyond U6.7 has been started:

- no live recording;
- no post-soak acceptance;
- no pin;
- no deployment or merge;
- no next checkpoint.

Awaiting independent ChatGPT review and explicit owner authorisation.
