# Handoff: latest checkpoint (product branch)

## PRODUCT BUILD U6.0.1: EVIDENCE PROVENANCE AND HISTORICAL METADATA CLOSEOUT — CHATGPT REVIEW REQUIRED

Branch `product/local-ui` only. Offline. No production action, no live source call, no
credential, no cloud resource, no deployment, no pull request, no merge to `main`. This
file is the product-branch handoff; `main`'s handoff was not touched. U6.0 was not
reverted and the evidence system was not redesigned. **U6.1 has not been started.**

- **Starting baseline:** `f98e20e05fc2e840575d09d0af142771de217c64` (U6.0 handoff; U6.0
  implementation `18b72bc`). Verified at the start: on `product/local-ui`, at that
  commit, tree clean.
- **Implementation SHA:** `a3abea802d4dcdbc712c2f45578bb9b0a9b26e7a` —
  `fix: distinguish unverified evidence and historical metadata`.
- **Production `main`:** `cb33198e2510965e41dff0f5fe7e75cacb7ee2ca`, unchanged.

## Exact files changed

| File | Change |
|---|---|
| `src/metaspike/evidence/provenance.py` | New. Pinned recording digests, the verified / unverified / synthetic rule, detection of the scenario's own data. |
| `src/metaspike/evidence/model.py` | `Origin.UNVERIFIED_FILES`; `Standing`; `InputProvenance.standing` and `.digests`; `usable` now needs a verified origin; `BoundRecord.content_known`; `Movement.standing`; `GameEvidence.verified_demand_families`, `unconfirmed_demand_families`, `events_verified`, `unverified_sources`; `EvidenceReport.contains_unverified`. |
| `src/metaspike/evidence/assemble.py` | Metadata state from held content, not from the binding; per-game provenance corrected by what is stored; honesty check on every input; new gaps. |
| `src/metaspike/evidence/render.py` | Standing on inputs, sources, movements and events; bound records whose content was not held. |
| `src/metaspike/cli.py` | The `evidence` command derives provenance from content instead of declaring it (22 lines added, 34 removed). No other command touched. |
| `tests/test_evidence.py` | 5 new tests; 2 existing output expectations updated for the new wording. |
| `docs/multi-source-evidence.md` | Origin, historical metadata, freshness verification, limits. |

Exactly the files the brief expected. Nothing under `infra/`, `.github/`, `apps/`,
`schemas/`, `tests/fixtures/`, and no resolver, feature, scoring, event, validation,
publishing, source, store or policy module. The changes to the evidence model are local
to the private evidence report; no store format, sample, artifact schema or public
contract changed.

## Root causes

**A. Unverified local evidence.** The command labelled whatever `--sample-store`,
`--events-dir` and `--event-sources-dir` pointed at as `local_store`, and
`SourceEvidence.usable` rejected only `synthetic`. Readable was treated as real. The
same hole existed one level up: the command declared `--recorded` and
`--wikimedia-replay` directories to be `recorded_response` by fiat, whatever was in
them. The review named the stores; I closed both, since they are the same mistake.

**B. Historical metadata.** `catalogue_source` set `available` whenever a binding
existed. `identity()` already withheld a record's content when its only held version was
fetched later, so the report showed a source as available while showing none of what it
said.

## Chosen provenance handling

Three standings, decided by one rule in `provenance.py`:

- **verified**: the input's content matches a digest pinned in the code. Three
  recordings are pinned (the six pageview responses, the two catalogue fixture
  directories). Bytes only: a renamed copy is still verified; one changed byte is not.
- **synthetic**: declared by the command's own scenario, or **detected in what is
  stored** — a scenario sample carries the run id `synthetic-scenario`; a test
  announcement points at the reserved `.invalid` domain.
- **unverified**: everything else, including every local store.

No option declares an input real. An input claiming `recorded_response` without a
pinned digest is refused before anything is read. Store formats, samples, the feature
engine and source policies are untouched; no global state; no filename guessing.

Unverified and synthetic evidence stays visible with its numbers exactly as the engine
gives them. It is never `usable`, never a verified demand family, never a verified event.

## Synthetic re-import regression (`test_synthetic_stores_read_as_local_stores_are_never_confirmed_evidence`)

The scenario's stores read (a) declared synthetic, (b) as ordinary local stores, through
the library and through the command:

- Inputs on path (b) read `unverified`; Dota 2's samples and announcements are then
  detected as synthetic from their content, with the reason stated.
- `independent_demand_families`, `verified_demand_families` and `usable_sources` are
  identical on both paths and identical to a reading with no stores at all, for all
  fifteen games. Neither path gains a confirmed family.
- States, reasons, bindings, latest-observed times, freshness, the engine's demand
  reading, sample completeness, event intelligence, upcoming events, candidate counts,
  movement features, score v0 and its confidence are equal on both paths.
- Where the store gives no sign (a never-sampled app; a game looked at with no posts),
  the standing stays `unverified`.
- Text and JSON show all three standings; both reports are `private_development_only`;
  the publication refusal names synthetic and unestablished origin.

## Real demand-family counting

`independent_demand_families` = demand sources that are `available` **and** verified.
On the recorded inputs it is unchanged from U6.0 (six games with one, none with two).
`demand.evidence_breadth` is the frozen engine's output and still counts a synthetic or
unverified family as observed (asserted: `observed_count` 1 while the confirmed count is
0). The engine was not modified; the model, the doc and the text now say that the
engine's breadth is not the confirmed count, and current-but-unverified families are
listed separately as `unconfirmed_demand_families`.

A copy of the pageview recording with one view count changed reads `available` with the
altered value shown, `unverified`, and counts as zero confirmed families.

## Event provenance

Announcements from a synthetic or unverified store still run through classification,
promotion and Event Heat, and the result is shown. `events_verified` is false; the text
prints "Events below come from UNVERIFIED (or SYNTHETIC) announcements: a diagnostic,
NOT verified real-world events"; a gap `events_not_verified` says the same.
`test_local_data_of_unestablished_origin_is_shown_and_not_counted` covers plausible
local samples and an announcement with nothing synthetic about them.

## Historical metadata regression (`test_metadata_is_available_only_once_its_content_was_held`)

A binding valid from 5 October whose only held record was fetched on 10 October:

- **Before the fetch:** the binding is listed with its mapping version and start;
  `content_known` is false; no product type, date or parent is shown; the source is
  `missing` with the reason "bound to the game at this moment, but no version of the
  record fetched by then is held…"; not usable; the Steam app it asserts is not used, so
  Steam's standing is `unknown`; the later release date appears nowhere in the report.
- **From the fetch on:** `available`, content shown, latest-observed is the fetch time.
- Canonical id, binding and mapping history identical throughout. The other source's
  row for the same game, fetched in time, is unaffected. On the recorded fixtures as
  they are, every bound record's content was held and no source reads `missing`.

The existing rebinding tests pass unchanged.

## Pageview freshness: verified and kept

Checked on the recorded Minecraft series at ten moments (table in the doc). The reading
is `available` exactly when score v0's `staleness_days` is 0 and v0's freshness factor
is 1.0; stale with 1, 4 and 15 missing due days; identical at four times on one day;
equal to `RecordedHistory.inputs_at` wherever the backtest can stand; and with 3 October
removed from the recording, available on the 4th and stale on the 5th. With every due
day held, the engine handed the wall clock returns `applicable_but_stale` and withholds
every feature; handed the due boundary it reports the same buckets. It does not change
freshness semantics or hide missing data, so the implementation is unchanged.

## Quality gates (2026-10-14, on the committed tree)

- `uv run ruff format --check` — 231 files already formatted
- `uv run ruff check` — All checks passed
- `uv run mypy` — no issues in 217 source files
- `uv run pytest` — **1,540 passed, 8 deselected in 363.99s** (1,535 before, plus 5)
- `uv run metaspike export-schemas --check` — Schemas are up to date
- `uv run metaspike evaluate-resolver` — exit 0; golden set v3, 200 cases, 0 false auto merges
- `uv run pytest tests/test_evidence.py` — 32 passed in about 55 s

Two existing expectations were changed, both for wording the fix changed on purpose (the
input line now reads "verified recorded_response"; the input JSON gained `standing` and
`digests`). Two of my new tests first failed on my own wrong expectations and were
corrected: an "altered" copy I had not actually altered, and an assumption that score v0
drops confidence to zero on one missing day (it lowers its freshness factor per day).
Neither was a product fault. No unrelated test failed.

Not run: `live` tests (not permitted); the four `slow` catalogue proofs (no catalogue or
resolver code changed; last run in U5.3, 4 passed). Frontend not touched and not re-run
(last verified in U5.3: all five gates passed, 179 tests).

## Frozen rules and source policies

No change to `resolver_v2`, `baseline_rules_v1`, `signal_semantics_v2`,
`metaspike_score_v0`, `event_heat_rules_v1`, `event_classification_rules_v1`,
`validation_protocol_v1`, `outcome_rules_v1`, any source policy, the artifact contract
(1.5.0), the golden labels, or the U5.2 and U5.3 catalogue code. Score v0 parity against
the frozen golden file and against the backtest's inputs still passes. Policy refusals
at public stages still pass.

## Production isolation (read-only, 2026-10-14 02:22 UTC)

- `main` = `cb33198`, level with `origin/main`, clean. No pull request. Last workflow
  run still 38104941975 (11 October, `main`). `.github/` and `infra/` not modified.
- Terraform state generation `1791685217422653` and control record generation
  `1791684706340016`: unchanged. Scheduler configuration, jobs, IAM, dataset schemas,
  secrets and alert policies identical to the previous snapshot. 0 secret versions; 0
  user-managed keys. Repository variables unchanged.
- `BROAD_DISCOVERY_ENABLED` is still `False`. No source was called. Nothing was
  published. The portfolio repository was not touched.

## C2-F observation (read-only, 2026-10-14 02:22 UTC; separate; not altered; not judged)

55 executions since the window opened, all succeeded, none running: 12 October 24
dispatcher, 1 Wikimedia collector, 1 warehouse; 13 October the same; 14 October so far 3
dispatcher. Warehouse rows: `collection_runs` 120, `expected_targets` 56,
`period_observations` 1,624. This is not a statement that C2-F has passed. Nothing was
triggered, restarted, paused, cancelled or changed.

## Remaining known limitations

1. **Genuine local data cannot be verified yet.** Every local store is unverified,
   including the real Steam News probe of 11 October and any real samples collected
   later. Verifying them needs an origin recorded at collection time: a change to the
   collection contract, for its own review.
2. **Detection is a safety net, not proof.** It recognises the scenario's run id and
   the `.invalid` domain. Synthetic data without those marks reads as unverified, which
   still counts for nothing; it is never mistaken for verified.
3. **A pinned digest vouches for bytes, not for truth.** The three pins assert that the
   committed fixtures are the recordings the repository documents them to be.
4. **A record altered in memory is not caught.** The library takes records as objects;
   the digest is checked on the directory the command loads them from. A caller that
   changes records after loading keeps the verified label (tests do this deliberately).
5. **Metadata history is not kept.** Only the latest version of a source record is
   held, so content before its latest fetch is `missing`, never reconstructed.
6. Unchanged from U6.0: no game has two verified demand families; no creator-supply or
   community-interest source; no opportunity score; no multi-app combination rule.

## Commits

`a3abea8` fix: distinguish unverified evidence and historical metadata; then this
handoff. Pushed to `origin/product/local-ui` only. No PR, no merge to `main`, no
deployment.

## Exact next approval required

Independent ChatGPT review of U6.0.1, then explicit user authorisation. U6.1 and every
other workstream are not started.

STOP.
