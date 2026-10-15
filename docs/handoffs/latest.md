# Handoff: latest checkpoint (product branch)

## PRODUCT BUILD U6.9: CREATOR OPPORTUNITY COMPARISON WORKSPACE — CHATGPT REVIEW REQUIRED

This checkpoint was worked on branch `product/local-ui` only:

- private, local UI work, built and tested offline;
- no source call, live recording or pin;
- no cloud access or deployment;
- no pull request, merge or publication.

**No later checkpoint has been started.**

## 1. Starting SHA

`b6ccbff79284fd27a06f869c64828799543973f1`. Verified at the start: on
`product/local-ui`, at that commit, tree clean.

## 2. Implementation SHA

`d819b9546a16f664edd6d2e7e68baff7dac697c4`: `feat: compare creator opportunities
privately`.

## 3. Exact changed files

| File | Change |
|---|---|
| `apps/dev-ui/src/private/opportunities/compare.ts` | New. `parseSelection`, `buildComparison` (engine order, positions, tie groups), the plain-text serializer `comparisonText`. |
| `apps/dev-ui/src/app/opportunities/compare/page.tsx` | New. A server page, `force-dynamic`, behind the existing loader. |
| `apps/dev-ui/src/components/opportunities.tsx` | Adds `CompareSelector` (a plain GET form, added to the shortlist page) and `OpportunityComparison`. Existing components are unchanged. |
| `apps/dev-ui/src/private/opportunities/brief.ts` | Path hygiene (`safeText`, `safeReference`) replaces U6.8's `scrub`. Adds an `events` field for comparisons; exports `labelled` and `STANDING_LABEL`. |
| `apps/dev-ui/src/components/compare.test.tsx` | New: 18 tests. |
| `apps/dev-ui/src/components/creator-brief.test.tsx` | One U6.8 test updated (see §8). |
| `apps/dev-ui/src/private/opportunities/loader.test.ts` | The compare page is added to the private-page guard (it must be `force-dynamic` and never `use client`). |
| `apps/dev-ui/README.md` | Comparison usage and the path policy. |
| `docs/handoffs/latest.md` | This handoff (separate commit). |

No Python, schema, engine or report-contract file changed.

## 4. Comparison interaction

1. The shortlist page (`/opportunities`, or `?view=synthetic`) has a **Compare games**
   section. It is a plain `<form method="get" action="/opportunities/compare">` with one
   checkbox per game in the engine's verified order, plus a hidden `view=synthetic` in the
   synthetic view. It needs no client script.
2. `/opportunities/compare?game=a&game=b[&game=c]`, where comma-separated values are also
   accepted:
   - The selection is validated on the server against the loaded, validated report.
   - Plain refusals:
     - "Choose 2 or 3 games to compare."
     - "Choose at most 3 games to compare (N were chosen)."
     - "“x” is chosen more than once."
     - "“x” is not a game in this report."
   - A value that is not slug-shaped, such as a path or markup, is never echoed: the
     message says "A selection is not a game in this report."
3. The comparison shows:
   - **Header and notices:** the as-of moment and rules version; the basis; the order
     note; any ties; the abstention line, when it applies.
   - **A table:** one column per game, headed by its engine position and linked to its
     detail page. The rows are readiness and decision; why now (counted evidence); leads,
     not counted; what changed; video angle; creator coverage; community; events;
     counterevidence; not known; freshness; and confirm before acting.
   - **Footer:** the combined questions to answer before choosing, and the
     unvalidated-thresholds notice.
   - **Copy controls:** **Copy comparison as text**, and a plain-text view.

The comparison is disabled whenever private opportunities are disabled: it uses the
existing loader unchanged.

## 5. Engine-order preservation

- **Engine order.** Columns are in the engine's order for the basis: `entries` for
  verified, `simulated_entries` for simulated. Selection order is ignored. Positions are
  the engine's.
- **Ties.** Tie groups are read from the engine's `ahead_of_next` ("indistinguishable on
  the evidence"). Chosen games in one group are stated as tied, "ordered by id only, which
  means nothing", on screen and in the text.
- **Nothing computed.** There is no score, rank, winner or "best" (tested), and the text
  says "A higher position is not a predicted video outcome". Readiness is the engine's
  (tested per column).

## 6. Abstention and missingness

- **All insufficient.** When every chosen game's verified readiness is
  `insufficient_evidence`, the comparison says: "No real creator opportunity is established
  for these games by the verified evidence." It still lists the unanswered questions.
- **Per-game wording** is the U6.8 brief's:
  - a watchlist entry is "Watchlist — not a recommendation";
  - a verified candidate is "The rubric supports further creator consideration … not a
    guarantee";
  - an insufficient game says "MetaSpike abstains".
- **Coverage is never read as a negative.**
  - Missing creator coverage: "That is not low competition: competition is simply
    unknown."
  - Even a complete sample: "a lower bound".
  - Missing community: "That is not low interest".
- **Labelled leads.** Unverified or synthetic events are listed with their provenance and
  marked "lead, not counted".
- **Kept as given:** counterevidence includes down movements and disagreement; the
  engine's "not known" lines, freshness and references are kept as they are.
- **Not used:** audience size, market share, or a ranking by missingness.

## 7. Synthetic separation

The synthetic view opts in through `?view=synthetic` on the shortlist and its form.

- **The comparison basis is simulated.**
- **The warning.** "SYNTHETIC DEMONSTRATION — NOT A REAL RECOMMENDATION" is shown on
  screen (`role="alert"`) and is the first and last line of the copied text.
- **Readiness labels.** Every simulated readiness is "SIMULATED …", with "(verified
  readiness: …)" beside it.
- **The abstention line** still states the verified fact: no real opportunity is
  established. The simulated claims are never merged into it.

The verified view of a synthetic report counts nothing invented (tested).

## 8. Copy and path hygiene

Copied text is plain text and React escaping is unchanged. **Path hygiene is a
conservative policy, not a universal detector:**

- **`safeText` (free text).** Every whitespace-separated token is checked. A token is
  withheld as `[local path removed]` if it contains `/` or `\`, starts with `~`, or names
  a drive (`C:`).
  - **Exceptions:** http(s) URLs with a host are kept whole, whatever words their path
    holds (`…/r/private/…`, `…/Users/…`). A lone `/` between words is kept, as are plain
    fractions like `34/34`.
  - **The cost:** ordinary words with a slash ("and/or") are also withheld. That
    over-removal is accepted.
- **`safeReference` (references).** A reference is kept only if it is a plain identifier
  (`[A-Za-z0-9_.:#@-]`) or an http(s) URL. Anything else is replaced by `[reference
  withheld]`, including `file:` and `javascript:`.

Tested paths include:

- POSIX: `/Users/…`, `/home/…`;
- Windows: drive paths and UNC paths;
- home paths: `~/…`;
- project-private relative paths: `.metaspike/private/…`, `./tests/…`, `../…`,
  `apps/dev-ui/…`;
- a workspace path: `/workspace/…`.

Each was planted in names, coverage, community, missing, caveats, uncertainty, statements
and references. None reached a brief or a comparison, and the legitimate references
survived.

**U6.8 parity.** On every fixture assessment, in both views (14 briefs), the U6.8 brief
text is byte-identical under the new policy. This was checked against the U6.8 `brief.ts`
taken from Git, in a temporary test.

**One U6.8 test updated.** Its markup payload contained `</script>`, whose `/` the new
policy withholds. The test now asserts both behaviours: slash-free markup is shown
literally as text, and markup with `/` is withheld. Neither creates an element.

**Not page text.** In development mode, Next.js's own router payload repeats the URL the
browser itself requested, for example a `game=../../etc/passwd` query. That is the
client's own URL in framework state, not page text. No file is read, and our messages
never echo it.

## 9. Regression tests

`compare.test.tsx` adds 18 tests:

- **Selection:** 2 and 3 games; an invalid game; a duplicate; too many (array and comma
  forms); too few; hostile values never echoed.
- **Selector:** the form lists the report's games, and carries `view` in the synthetic
  view.
- **Order:** engine order whatever the selection order; positions; ties stated; no score,
  rank or winner.
- **Readiness and abstention:** all-insufficient abstention; watchlist and verified
  candidate semantics.
- **Coverage and community:** missing creator coverage; an empty sample as a lower bound;
  missing community.
- **Events and conflicts:** unverified events as leads; contradictory demand.
- **Provenance:** as-of, freshness and sources.
- **Copying:** copy equals the view (mocked clipboard).
- **Synthetic:** the warning on screen and first and last in the text; the verified view
  of a synthetic report.
- **Paths:** POSIX, Windows, home, relative and workspace paths withheld; HTTPS links and
  identifiers preserved; planted paths absent from copied briefs and comparisons.
- **Private boundary:** disabled in production; an invalid report refused.

Existing U6.8 creator-brief tests all pass, with one updated as described in §8. The
loader's private-page guard now covers the compare page.

## 10. Quality-gate results (`apps/dev-ui`)

| Gate | Result |
|---|---|
| `npm run contract:check` | exit 0 |
| `npm run opportunities:schema:check` | exit 0 |
| `npm run typecheck` | exit 0 |
| `npm run lint` | exit 0 |
| `npm test` | exit 0, **236 passed** (15 files; previously 217) |
| `npm run build` | exit 0; `/opportunities/compare` built as dynamic (ƒ), never prerendered |

**Backend gates:** not run, because no Python, schema or engine file changed.

## 11. Offline demonstration

The private reports were generated earlier by `npm run opportunities:recorded` and
`npm run opportunities:synthetic`, all offline. They sit in the git-ignored
`.metaspike/private/` and are not committed.

- **Recorded:** Minecraft, GTA V and Dota 2.
  - The columns are in engine order at positions 4, 9 and 14.
  - Dota 2 and GTA V are in one engine tie group, and stated as tied.
  - "No real creator opportunity is established for these games by the verified
    evidence."
  - Every game abstains; coverage and community are unknown, never low; the engine's
    "not known" lines are kept.
- **Synthetic:** Dota 2 and Rust.
  - The basis is simulated, with the warning on screen and first and last in the text.
  - Dota 2 is a SIMULATED candidate and Rust a SIMULATED watchlist entry, each with
    "(verified readiness: Insufficient evidence)".
- **Server render.** The local dev server (127.0.0.1, `METASPIKE_PRIVATE_OPPORTUNITIES=1`)
  was run, then stopped. It served:
  - the shortlist, with the selector and 15 checkboxes;
  - both comparisons, as above;
  - a duplicate selection, refused with a message;
  - four games, refused with a message;
  - a `../../etc/passwd` selection, refused as "A selection is not a game in this report".

  No rendered page or comparison text contained a local path. The ranking and readiness
  were the engine's throughout.

## 12. Privacy and frozen-contract parity

- **Loader and gates unchanged.** The existing loader and its enablement are unchanged:
  disabled in production, off without the flag, and limited to recognised views
  (`verified`, `synthetic`).
- **No new surfaces.** There is no API route, publication endpoint, browser-controlled
  path, storage, telemetry or analytics. The copy button receives finished text only.
- **Unchanged contracts.** The report schema, the validator and the public artifact
  contract 1.5.0 are unchanged.

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

Source policies, provenance rules and the Wikimedia recorder are also unchanged.

**Known limitation:** a static `compare` route takes precedence over `[slug]`, so a game
whose slug was literally `compare` would not reach its detail page. No such game exists.

## 13. Git-only C2-F isolation

C2-F isolation: Git-only verification. No GCP access, no production action.

Verified with Git:

- Only `product/local-ui` changed.
- `main` is at `cb33198e2510965e41dff0f5fe7e75cacb7ee2ca`, on both `origin/main` and the
  main worktree.
- No changes under `infra/`, `.github/`, `artifacts/`, `schemas/`, `src/` or `tests/`.
- `BROAD_DISCOVERY_ENABLED = False` is unchanged.
- No deployment, source request, activation, pin or publication.

## 14. No later checkpoint started

Nothing beyond U6.9 has been started:

- no U6.10;
- no post-soak acceptance;
- no live collection;
- no deployment, merge or publication.

Awaiting independent ChatGPT review.
