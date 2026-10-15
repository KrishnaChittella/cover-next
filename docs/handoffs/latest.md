# Handoff: latest checkpoint (product branch)

## PRODUCT BUILD U6.8: EVIDENCE-BACKED CREATOR DECISION BRIEFS — CHATGPT REVIEW REQUIRED

This checkpoint was worked on branch `product/local-ui` only:

- private, local UI work, built and tested offline;
- no source call, live recording or pin;
- no cloud access or deployment;
- no pull request, merge or publication.

**No later checkpoint has been started.**

## 1. Starting SHA

`a61035fc4a5e1157e909bd8c5fe0d59a1d761974`. Verified at the start: on
`product/local-ui`, at that commit, tree clean.

## 2. Implementation SHA

`4539b22ed1650241e5a4d84053d2455acaa9ddb1`: `feat: add evidence-backed creator decision
briefs`.

## 3. Exact files changed

| File | Change |
|---|---|
| `apps/dev-ui/src/private/opportunities/brief.ts` | New. Server-side brief builder: `buildBrief`, the plain-text serializer `briefText`, `scrub` for paths. |
| `apps/dev-ui/src/components/copy-text.tsx` | New. A tiny client copy button that receives finished text only. |
| `apps/dev-ui/src/components/opportunities.tsx` | `CreatorBrief` component; the brief added as the first section of the existing detail page. Nothing else changed. |
| `apps/dev-ui/src/components/creator-brief.test.tsx` | New: 20 tests. |
| `apps/dev-ui/README.md` | How to preview the brief. |
| `docs/handoffs/latest.md` | This handoff (separate commit). |

**No Python changed,** so the backend gates were not needed. The report schema,
`report.schema.json`, the validator, the loader, its enablement rules, the engine and every
frozen contract are untouched. `opportunities:schema:check` confirms the schema still
matches the engine.

## 4. UI behaviour and navigation

`/opportunities/<slug>`, and `?view=synthetic`, now open with a **"Creator decision
brief"** section. Every existing section follows it, unchanged (tested). There is no new
page, route, API or dashboard.

The brief shows:

- readiness, with the as-of moment and the engine's position;
- a decision sentence and its explanation;
- the rubric's four conditions, as met or not met (the engine's own `facts` booleans);
- what changed, with values and observed-through dates;
- why now;
- what points somewhere but is not counted;
- the video angle, or why there is none;
- creator supply and community, with what they cannot mean;
- counterevidence and caveats;
- what to confirm before acting, and the unvalidated-thresholds notice.

It ends with **Copy brief as text** and a collapsible plain-text view.

## 5. Copyable brief format

The brief is plain text, in this order:

1. The header: game, as-of time and rules version, basis, readiness (with verified
   readiness when simulated) and the engine's position.
2. Notes.
3. DECISION.
4. RUBRIC CONDITIONS.
5. WHAT CHANGED.
6. WHY NOW.
7. POINTING SOMEWHERE, BUT NOT COUNTED (when present).
8. VIDEO ANGLE.
9. CREATOR SUPPLY.
10. COMMUNITY.
11. COUNTEREVIDENCE AND CAVEATS.
12. FRESHNESS.
13. NOT KNOWN.
14. CONFIRM BEFORE ACTING.
15. UNCERTAINTY.
16. SOURCES REFERENCED.
17. "Private development only. Not published."

Every item whose standing is not verified is prefixed `[UNVERIFIED]`, `[SYNTHETIC]` or
`[NO INPUT]`, with its source references. What is copied is exactly what the plain-text
view shows (tested).

## 6. Abstention behaviour

The engine's readiness is used as given. Nothing is computed in TypeScript, and nothing is
reordered.

- **`evidence_supported_candidate`:** "The rubric supports further creator consideration of
  this game. It is not a guarantee that a video will perform." It names its limits:
  unvalidated thresholds, a sample that is only a lower bound, and nothing that measures
  video performance.
- **`provisional_watchlist`:** "Watchlist — not a recommendation." It lists the unmet
  conditions: "a reason to look again, not to publish".
- **`insufficient_evidence`:** "No recommendation. MetaSpike abstains: nothing counted says
  "now" for this game." It adds that missing evidence is not negative evidence.

## 7. Synthetic separation

The default verified view's brief uses verified readiness and verified why-now only.
Synthetic or unverified statements and events appear under "Pointing somewhere, but not
counted", labelled.

In `?view=synthetic` the brief's basis is **simulated**:

- **On screen:** "SYNTHETIC DEMONSTRATION — NOT A REAL RECOMMENDATION" in a `role="alert"`
  line, a "SIMULATED:" readiness badge, and a decision prefixed "SIMULATED —". The verified
  readiness is shown beside it.
- **In copied text:** the warning is the first and the last line, and each invented item
  is labelled `[SYNTHETIC]`.

No synthetic item is ever shown as verified.

## 8. Missingness and provenance handling

- **No creator input:** "That is not low competition: competition is simply unknown."
- **Incomplete or uncounted sample:** "cannot describe competition".
- **Every sample:** "Even a complete sample is a lower bound: total creator competition is
  unknown." Tested: no brief text asserts low competition.
- **No community input:** "That is not low interest."
- **Absent events** produce nothing. **Unverified or synthetic events** are listed as "not
  counted as a fact".
- **No angle:** said plainly. Candidate angles on uncounted evidence are "leads only".
- **Down movements and disagreement** appear as counterevidence. The engine's caveats are
  kept.
- **Kept as given:** freshness per source (state, latest observation, standing), the
  engine's `missing` list and the source references.
- **Local paths:** any filesystem path that appears in a string is replaced by `[local path
  removed]` (tested with POSIX and Windows paths).
- **Rendering:** text renders as React text, never HTML. Tested: a caveat containing
  `<script>`, `<b>` and `<img onerror>` renders as literal text, with no element.

## 9. Tests and exact quality gates

The new tests in `creator-brief.test.tsx` (20) cover:

- verified candidate wording;
- watchlist wording;
- abstention;
- the recorded report with zero candidates, on the page and in every brief;
- the engine's position, with no score field and no score text except the engine's own,
  verbatim;
- the synthetic warning on screen;
- the synthetic warning first and last in copied text, through a mocked clipboard;
- synthetic data never counted in the verified view;
- unverified labelling;
- empty and incomplete creator samples;
- no creator or community input;
- unverified or absent events;
- a missing angle;
- contradictory movements;
- the as-of moment, freshness and references;
- no local paths;
- markup rendered as text;
- private gates (disabled in production, off without the flag, an invalid report refused);
- the existing detail sections preserved.

Test inputs that need a verified candidate or watchlist are copies of fixture assessments,
shaped the way the engine would emit them. The fixtures themselves are unchanged.

**Frontend gates** (`apps/dev-ui`):

| Gate | Result |
|---|---|
| `npm run contract:check` | exit 0 |
| `npm run opportunities:schema:check` | exit 0 |
| `npm run typecheck` | exit 0 |
| `npm run lint` | exit 0 |
| `npm test` | exit 0, **217 passed** (14 files; previously 197) |
| `npm run build` | exit 0 |

**Backend gates:** not run, because no Python, schema or engine file changed (`git diff`
shows nothing under `src/`, `tests/` or `schemas/`).

## 10. Recorded and synthetic examples

These were generated offline with `npm run opportunities:recorded` and `npm run
opportunities:synthetic`. They are written to the git-ignored `.metaspike/private/` and
were not committed.

- **Recorded** (as of 2026-10-05 12:00 UTC, 15 games): every verified readiness is
  `insufficient_evidence`, and every brief says "No recommendation. MetaSpike abstains…".
  No genuine candidate exists, and none was invented.

  Minecraft's brief, for example:
  - pageviews verified and flat (log lift 0.157, robust z 0.151), observed through
    2026-10-04;
  - rubric conditions not met: rising attention, angle, coverage;
  - "No creator sample was taken. That is not low competition";
  - "No discussion was sampled. That is not low interest";
  - the engine's seven "not known" lines kept;
  - sources igdb, steam_catalogue and wikimedia_pageviews.
- **Synthetic** (as of 2026-10-20 12:00 UTC, 6 games): simulated Dota 2 is a candidate
  and Rust a watchlist entry; every verified readiness stays `insufficient_evidence`. The
  Dota 2 brief opens and closes with the warning, prefixes its decision "SIMULATED —",
  shows "verified readiness: Insufficient evidence", and labels every movement,
  statement, angle and sample `[SYNTHETIC]`.
- **Each brief matches its assessment.** Every brief in both reports was checked against
  its assessment's readiness, after the report passed `checkReport`.
- **Server render.** The local dev server (127.0.0.1, `METASPIKE_PRIVATE_OPPORTUNITIES=1`)
  rendered `/opportunities/minecraft` with a verified basis, the abstention and no
  synthetic warning. It rendered `/opportunities/dota-2?view=synthetic` with a simulated
  basis and the warning. Neither page contained a local path. The server was then stopped.

**Preview commands** (also in `apps/dev-ui/README.md`):

```
cd apps/dev-ui
npm run opportunities:recorded && npm run opportunities:synthetic
METASPIKE_PRIVATE_OPPORTUNITIES=1 npm run dev
# http://127.0.0.1:3000/opportunities/minecraft
# http://127.0.0.1:3000/opportunities/dota-2?view=synthetic
```

## 11. Privacy and publication isolation

- **Server-side.** The brief is built server-side, from the report the existing loader
  validated.
- **The copy button** is the only client code added. It receives the finished text and
  imports nothing private; the existing test that client components import nothing private
  still passes.
- **No new endpoints.** There is no API route, no publication endpoint, and no
  browser-controlled path.
- **Enablement unchanged:** disabled in production, and off without the flag.
- **Reports stay git-ignored.** Reports stay in the ignored `.metaspike/private/`, outside
  `public/` and `artifacts/`.
- **No artifact or schema change.** No public artifact or contract changed.

## 12. Frozen-rule parity

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

Also unchanged:

- source policies, provenance checks and the U6.7 recorder;
- the private report schema;
- public artifact contract 1.5.0.

## 13. Git-only C2-F isolation

C2-F isolation: Git-only verification. No GCP access, no production action.

Verified with Git:

- Only `product/local-ui` changed.
- `main` is untouched: `origin/main` and the main worktree are both at
  `cb33198e2510965e41dff0f5fe7e75cacb7ee2ca`.
- No changes under `infra/`, `.github/`, `artifacts/`, `schemas/`, `src/` or `tests/`.
- `BROAD_DISCOVERY_ENABLED = False` is unchanged.
- No deployment, source request, source activation or pin.

## 14. No later checkpoint started

Nothing beyond U6.8 has been started:

- no U6.9;
- no post-soak acceptance;
- no live collection;
- no deployment, merge or publication.

Awaiting independent ChatGPT review.
