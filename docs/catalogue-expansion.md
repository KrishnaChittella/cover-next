# Catalogue expansion: the offline foundation

**Status: INTERNAL. Offline evaluation only.** Broad catalogue discovery is **off** in
production and this checkpoint does not turn it on. Nothing here calls a source, stores
a catalogue, moves the catalogue pointer, schedules collection or changes a score.

Code: `src/metaspike/catalogue/evaluation.py`, `src/metaspike/catalogue/coverage.py`.
Command: `metaspike evaluate-catalogue`.

## Four stages that must not be confused

```
LARGE CATALOGUE  ->  SOURCE ELIGIBILITY + COVERAGE  ->  SELECTIVE MONITORING  ->  MULTI-SOURCE INTELLIGENCE
   catalogued            coverable                         monitored                 (not built)
```

- **Catalogued**: the entity exists, with a canonical identity. That is all it means.
- **Coverable**: a source has a trustworthy identifier for it, so it *could* be asked.
- **Monitored**: it is actually collected, on some schedule, under a cleared policy.

A game can be catalogued and not coverable. It can be coverable by Wikimedia and not by
Steam. A source that cannot be asked about a game reports **nothing** about it: not
zero players, not zero attention. A game without enough source data gets no score.

This checkpoint is about the first two stages, offline.

## What already existed, and was reused

Nothing here is a second catalogue system. The existing pieces do the work:

| Piece | Role | Used how |
|---|---|---|
| `resolve` (`resolver_v2`) | Every identity decision | Unchanged. Not called directly by the new code |
| `generate_catalogue` | Reference, all-pairs generation | Unchanged; used in tests as the reference |
| `generate_catalogue_blocked` | The same catalogue with blocking and incrementality | The engine the evaluation runs |
| `advance_catalogue` + `CatalogueRepository` | Production: scope, resolve, commit, move `CURRENT` | **Not used.** The evaluation has no repository |
| `CatalogueScope` / `in_scope` / `BROAD_DISCOVERY_ENABLED` | The production scope gate | **Untouched and closed.** `BROAD` still raises |
| `directly_reachable` | What production's scope sees | Used to compute the comparison |
| `steam_eligibility` | Whether a Steam app is trustworthily bound | The Steam half of coverage, as is |
| `tests/catalogue_samples.py` | Stratified synthetic records | The scale inputs |

## The evaluation path

`evaluate_broad_catalogue(records, policies=..., stage=..., as_of=...)` answers one
question about records already in hand: *resolved by the existing rules, what would a
broad catalogue hold?*

It builds two catalogues with the one shared engine and keeps them apart:

- **Narrow scope**: what production's scope would build. The bootstrap entities and
  every record an explicit link (a structured external id, a recognised source URL, a
  parent or version-parent reference) ties to them. A title is never a link.
- **Broad scope**: what a broad pass would build from every supplied record.

### Scope difference and incremental change are different questions

| | Question | Fields | On an identical repeat |
|---|---|---|---|
| **Scope difference** | How do the narrow and broad catalogues differ *now*? | `entities_in_production_scope` (narrow), `canonical_entities` (broad), `entities_only_in_broad_scope`, and the bound / quarantined / related counts | unchanged |
| **Since previous** | What did *this* evaluation add to each catalogue? | `narrow_entities_admitted_since_previous`, `broad_entities_admitted_since_previous`, `records_new_since_previous`, `quarantines_released_since_previous` | all zero |

On a first evaluation the "since previous" fields are **absent** (`null`), not zero:
there is no previous evaluation to count from. `continues_previous` says which it is.

**Each catalogue has its own history.** The first evaluation builds the narrow
catalogue and then the broad one on top of it. A later evaluation is given the previous
*evaluation* (`previous: BroadEvaluation`), and continues the narrow catalogue from the
previous narrow one and the broad from the previous broad one. A bare `CatalogueState`
is refused with a `TypeError`: a broad catalogue holds entities the narrow scope never
admitted, so it is not a valid history for the narrow pass.

`entities_only_in_broad_scope` is counted **by records, not by comparing ids**: a broad
entity is within the narrow scope if it is a bootstrap entity or holds a record the
narrow scope reaches. That makes it a property of the current state.

One consequence, stated plainly: an entity that *both* catalogues come to create after
the first evaluation (a new record that an explicit link ties to a bootstrap entity)
gets a different canonical id in each, because each history issues its own. Every
existing identity is stable within its catalogue, the bootstrap ids are the same in
both, and neither is the production catalogue. Nothing consumes an id across the two.

#### What was wrong before (corrected in U5.1)

The first version took a single previous `CatalogueState`, in practice the earlier
broad one, and gave it to the narrow pass as well. On an identical repeat of the 394
recorded records it reported 234 entities "in production's scope" and 0 "admitted by
broad", for a catalogue that had not changed: one field had quietly become a count of
change since last time. Now the same repeat reports 15, 234 and 219 again, and zero
under "since previous". `entities_admitted_by_broad` was renamed
`entities_only_in_broad_scope` to say what it is.

### Time: what `as_of` is and is not

`as_of` is the **time of the evaluation**. It stamps the decisions and bindings made in
it (`decided_at`, `valid_from`). It is **not** a historical cutoff: it does not hide
records, the counts do not depend on it, and this module cannot reconstruct what was
known at an earlier date. No such reconstruction is claimed.

What it must not do is date a binding before its evidence existed, because the mapping
history is read point-in-time and such a binding would later read as something known
earlier than it was. So:

- a record with `fetched_at` after `as_of` is **refused**;
- an `as_of` earlier than the evaluation being continued is **refused**;
- a later evaluation appends: an earlier point-in-time read of the mapping still shows
  what was bound then, and nothing else.

`fetched_at` is the record's own fetch time as the fixture recorded it. Nothing invents
a collection time. The recorded fixtures carry one fetch time for the whole file
(2026-10-05 02:00 UTC), so they cannot tell apart records fetched at different moments.

Effect on the recorded runs: the first U5 run was stamped 2026-10-05 00:00 UTC, two
hours before the fixtures' fetch time. That stamp is now refused. The same run stamped
2026-10-06 gives **exactly the same counts**; only the timestamps on the decisions
differ. The command below is shown with the corrected date.

What keeps it from being a back door to broad discovery:

- **It takes records.** No adapter, no HTTP client, no way to fetch.
- **It has no storage.** No repository, object store or pointer is imported; the result
  is a value in memory. A test asserts none of those names appear in the module.
- **It does not go through the production gate.** `in_scope(..., BROAD)` still raises
  `BroadDiscoveryDisabled`, and `advance_catalogue` still defaults to the narrow scope.
- **It holds no identity rule.** It calls `generate_catalogue_blocked` and counts.
- **It states its scope.** Every summary says `offline_broad_evaluation` and the stage.

### Source policy: fail closed

Every source a record comes from must have a `SourcePolicy`, handed in by the caller
under that source's own name, that clears the **stated** stage for
`CATALOGUE_METADATA`. Neither `policies` nor `stage` has a default. Refused:

- no mapping, an empty one, or one missing that source;
- a policy filed under another source's name;
- no stage stated;
- a stage the policy does not clear;
- a source with no policy at all, whatever it is called;
- a record posing as bootstrap evidence;
- a policy whose storage permission is unknown or prohibited.

The real policies decide, and were not changed:

| Source | Private development | Private collection | Public launch | Commercial |
|---|---|---|---|---|
| IGDB | cleared | cleared | not cleared | not cleared |
| Steam catalogue | cleared | **not cleared** | not cleared | not cleared |

So the recorded fixtures (both sources) can be evaluated at private development and at
no further stage. IGDB's records alone can be evaluated at private collection.

The summary is **counts only**: no title, id or text of any record. The command prints
the same. Nothing from a restricted source reaches an artifact, because nothing here
writes an artifact.

## What the recorded inputs come to

`uv run metaspike evaluate-catalogue --stage private_development --recorded
tests/fixtures/catalogue --as-of 2026-10-06`

Input: the existing recorded fixture, rows as the sources returned them on 2026-10-05:
**266 IGDB games and 128 Steam apps, 394 records.** No new record was fetched.

| | |
|---|---|
| Source records | 394 |
| In production's scope | 17 |
| Seen only by a broad evaluation | 377 |
| **Bound to an entity** | **327** |
| – admitted as a new entity | 219 |
| – resolved to an existing entity | 108 |
| **Quarantined** (no identity) | **44** |
| **Related products** (not entities) | **23** |
| Canonical entities, narrow scope | 15 (the bootstrap entities) |
| Canonical entities, broad scope | 234 |
| Only in the broad scope | 219 |

Every record is in exactly one of the three bold states: 327 + 44 + 23 = 394.

Quarantine, by reason: `title_only` 21, `conflicting_evidence` 12,
`id_link_title_mismatch` 8, `undetermined_product_type` 2, `multiple_candidates` 1.
Related products, by type: DLC 8, bundle 5, pack 3, update 3, episode 2, expansion 1,
season 1.

This is 394 records. It shows the path works on real recorded rows. It is not a
measurement of how a catalogue of thousands of real games would resolve.

## Identity safety

Nothing about identity changed. The tests pin what must hold:

- The 15 bootstrap entities keep their canonical ids, slugs, names, source identifiers,
  typed parent relationships (GTA Online → GTA V as `ONLINE_COMPONENT`; FiveM as
  `MOD_ECOSYSTEM`) and lifecycle histories.
- Proven identity joins and never duplicates: Counter-Strike 2's IGDB record and the
  Steam app it names resolve to the one bootstrap entity.
- A genuinely new, eligible game becomes `NEW_ENTITY`; every one of the 219 came from
  an entity-eligible record.
- A shared title is never identity. 21 recorded records wait as `title_only`.
- DLC, bundles, packs and the like are related products. No related product, non-game,
  undetermined record or packaging variant was admitted as an entity.
- A remaster is its own entity with a typed `REMASTER` relationship to its original;
  a sequel is a different entity (synthetic case).
- Repeating the same inputs changes nothing: no binding added, no decision repeated,
  no entity created, zero resolver calls.
- New evidence releases a quarantine without rewriting anything: the earlier
  quarantine decision and every earlier binding are still there, the release is a new
  decision and a new binding, and a point-in-time read before the evidence still shows
  the record unbound (synthetic case).
- No source identifier is inferred from a title. Entities admitted by the broad pass
  have **no** source identifiers.
- Asked every question both ways (`--cross-check`), the blocked engine never differs
  from all-pairs on the recorded data.
- The frozen golden set is byte-identical (file hash pinned) and gives the same
  result: version 3, 200 cases, 178 correct, **0 false automatic merges**.

## Coverage and monitoring readiness

`catalogue/coverage.py` says, per entity, whether each monitoring source *could* be
asked. It reuses the one existing vocabulary, `SourceEligibility`, and adds no rule:

| State | Meaning |
|---|---|
| `eligible` | A trustworthy identifier exists for this source |
| `ineligible` | Evidence says the source cannot observe the entity: inapplicable |
| `unknown` | Nothing settles it |

- **Steam** is exactly `steam_eligibility`: a bound app that a bound record also lists
  as a structured external id.
- **Wikimedia** is the catalogue's own exact article mapping. An entity without one is
  `unknown`, never `ineligible`: a missing mapping is not evidence that no article
  exists. No title is ever turned into an article name.

For the 234 entities of the recorded evaluation:

| Source | Eligible | Inapplicable | Unknown |
|---|---|---|---|
| Wikimedia pageviews | 14 | 0 | 220 |
| Steam concurrent players | 90 | 69 | 75 |

- Ready for both: 5. With a confirmed Steam app: 90 entities, 91 apps.
- **Without any eligible source: 135.** These are catalogued and cannot be monitored.
  Nothing is known about their demand. That is absence of data, not low demand.

None of these is a measurement. No field in the summary can hold players, views,
demand or a score.

**Readiness is not permission.** Ninety entities have a confirmed Steam app, and Steam
player counts are cleared for private development only. Nothing here enables sampling,
tiering or scheduling.

How this lines up with the existing signal vocabulary, without merging the two:
`eligible` is what `FamilyApplicability` calls applicable (available or missing,
depending on whether data exists); `ineligible` is `INAPPLICABLE`; `unknown` is
`UNKNOWN`. Coverage is about identifiers and comes from the catalogue; applicability
and availability are about data and come from `signal_semantics_v2`.

## Scale

**Synthetic records only.** `tests/catalogue_samples.py` builds invented games in fixed
proportions, one family per situation the resolver has a rule for. It is shaped like
IGDB and Steam records and deliberately heavy in hard cases, so its quarantine rate
(about 40%) says nothing about real data. It measures the cost of resolving.

Measured on one laptop, one process per size
(`uv run python -m tests.catalogue_scale 1000 5000 10000 20000`):

| Records | Entities | Quarantined | Related | Resolver calls | Entities examined | All-pairs would examine | Share | Full scans | Resolving | Coverage | Total | Peak memory |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1,000 | 432 | 402 | 80 | 1,030 | 25,176 | 675,566 | 3.7% | 60 | 0.47 s | 0.04 s | 0.51 s | 54 MB |
| 5,000 | 2,070 | 2,021 | 395 | 5,167 | 578,428 | 16,138,916 | 3.6% | 312 | 10.3 s | 1.0 s | 11.4 s | 108 MB |
| 10,000 | 4,117 | 4,043 | 790 | 10,343 | 2,289,686 | 64,147,993 | 3.6% | 628 | 44.7 s | 4.5 s | 49.2 s | 169 MB |
| 20,000 | 8,210 | 8,094 | 1,575 | 20,690 | 9,108,927 | 255,861,875 | 3.6% | 1,260 | 191.9 s | 22.2 s | 214.2 s | 306 MB |

Size on disk, measured on the synthetic catalogues by serialising the state: about
2.1 KB of snapshot and 2.3 KB of first-generation events per source record (2.1 MB
and 2.3 MB at 1,000; 10.6 MB and 11.6 MB at 5,000).

No larger size was run. Nothing is claimed for 50,000.

### What the measurements show

1. **Blocking works: about 3.6% of all-pairs work, at every size.** A record with
   usable blocking keys is offered at most 8 entities.
2. **Resolving is nonetheless quadratic.** Each doubling multiplies the time by about
   4.3. The cause is specific: a record whose title blocking cannot key (too many
   numbers in it) falls back to a **full scan** of every entity. About 6% of the
   synthetic records do. Since no other call is offered more than 8 entities, full
   scans account for at least 98% of the entities examined at 20,000 (1,260 scans
   against at most 8 × 20,690 for everything else). Quarantine fingerprints are
   recomputed on the same views.
   On the 394 recorded records there were **0** full scans in 380 calls, so the share
   in real data is unknown and may be much smaller. It has not been measured on real
   data at scale because no such data exists here.
3. **The coverage summary is also quadratic** (22 s at 20,000): `steam_eligibility`
   scans every binding once per entity.
4. **Memory grows linearly**, about 15 MB per thousand records.
5. **A snapshot is the whole state, every generation.** At about 2.1 KB per record a
   20,000-record catalogue is roughly 42 MB per stored generation (extrapolated from
   the two measured sizes, not measured at 20,000).

None of these was fixed here. The first is in the resolver's blocking and the second in
existing eligibility code; changing either is its own reviewed change with its own
equivalence proof. They are stated so they are dealt with before broad collection, not
discovered during it.

A consequence worth noting now: the production catalogue job is configured with a
one-hour timeout and 2 GiB. A first broad build of 20,000 records took 192 s of
resolving on a laptop. On one Cloud Run vCPU it would be slower by a factor that has
not been measured, and at quadratic growth a first build of 50,000 would take roughly
six times as long as 20,000. Whether that fits the job has to be measured, not assumed.

### Tests and CI

The 1,000-record evaluation runs in ordinary CI with exact pinned counts (about half a
second in U5; less since U5.2). The 5,000, 10,000 and 20,000 cases are marked `slow` and
excluded by default.
The pre-existing 10,000-record blocked-versus-reference proof is unchanged and also
`slow`.

## Scaling optimisation (U5.2)

**Offline, on the product branch only.** This section describes two changes to how the
catalogue is *processed*. It changes no identity decision and no eligibility rule, it
has not run in production, and it does not move the rollout gate: broad discovery is
still off and adopting any of this in production is still after C2-F.

The measurements in "Scale" above are U5's and stay as they were recorded. They
describe the code before this section. The measurements below are new.

### The two bottlenecks

Both were measured in U5 at 20,000 synthetic records: 191.9 s resolving, almost all of
it in 1,260 full scans, and 22.2 s in the coverage summary.

### A. Candidate blocking: no more full scans

The resolver calls an entity a packaging variant when

```
incoming core − rework words − numbers equal to one of THE OTHER record's release years
        ==  the other record's core − rework words
```

Which numbers are dropped depends on the record being compared with. The old index
handled that by trying every way of dropping the incoming title's numbers, which is
exponential in them, and above ten distinct numbers it gave up and had every entity
assessed. That was correct, and quadratic.

The index now does it in two steps:

1. **A necessary condition as the key.** Dropping numbers can only make two cores equal
   if they already have the same *words* in the same order. So every record is filed
   under its core with all numbers removed, and an incoming title is looked up under
   its own. A record filed anywhere else cannot be a packaging variant, whatever its
   release years. "A number" is tested exactly as the resolver tests it.
2. **The resolver's own comparison, on that bucket.** Each record in the bucket keeps
   its core and its release years. The lookup evaluates the expression above with
   them by calling the resolver's `_without` and `_release_years`, the same two
   functions the resolver calls.

So for this rule the index returns **exactly** the entities the resolver would relate
by packaging: nothing is missed, and nothing is guessed. The work is linear in the
records that share a title's words, however many numbers the title carries.

What it is not: no threshold was lowered, no top-K, no similarity, no probabilistic
filter, no assumption that a number is a year or that a title is unique. Every other
key (ids, URLs, parent links, exact and alternative titles) is unchanged. Overrides and
existing bindings are still added by reference. The resolver is untouched
(`resolution/resolver.py` has no diff) and is still the only thing that decides.

**Remaining full-scan conditions: none.** No input makes the index ask for every
entity. The `None` return that means "assess everything" is kept in `wanted_refs`'
contract, unused, so a future case the index cannot key has a safe, already-handled way
out instead of a reason to prune.

The cost that remains for this rule is a bucket scan: titles made of the same words in
the same order (a long numbered series; titles that are nothing but numbers) share a
bucket, and each lookup compares against every distinct core in it. Those are tuple
comparisons, not resolver calls.

### Proof of equivalence

- **Per question.** `resolve(blocked_view(view)) == resolve(view)`, the whole decision:
  kind, target, assessments, vetoes, explanation, relationships; and the quarantine
  fingerprint. Held on every golden case, on hand-built adversarial views and on 120
  seeded random questions against 300 held records.
- **Per build.** `generate_catalogue_blocked(...) == generate_catalogue(...)`, as whole
  states and as serialised JSON: every binding, logged decision in order, quarantine
  entry and fingerprint, related product, lifecycle history and id. Held on both
  recorded fixtures, a 700-record stratified sample, three input orderings, the seeded
  random catalogue, and the pre-existing **10,000-record** proof, re-run in this
  checkpoint.
- **Cross-check mode**, unchanged, re-asks every question the all-pairs way and raises
  on any difference. It ran clean on all of the above.
- **Golden set**: byte-identical file; version 3, 200 cases, 178 correct, 0 false
  automatic merges, as before.
- **Why it cannot miss**, tested directly: for 2,000 seeded token lists, removing any
  set of numbers never changes the words, so two cores made equal by dropping numbers
  always share a key.

Adversarial cases, all synthetic, each asserting the offered set equals the resolver's
own candidate set: a title with 37 distinct numbers; twelve numbers of which exactly
one is the other record's release year; several year-like tokens with different
subsets released; a sequel numeral that is not a year, and the same numeral when it
really is one; a number written two ways (`2016`, `02016`); titles that are only
numbers; a repeated year; exact and alternative titles in both directions; one
normalised title on three distinct games (all offered; the resolver quarantines);
series siblings in Arabic and Roman numerals; remasters, HD and packaging editions; a
title that is only packaging words; id links with unrelated titles in both directions;
a store URL alone, corroborated, and claimed by two games; parent and version-parent
links; conflicting evidence; an override target with no evidence; an existing binding;
no key at all; an empty index.

### B. Steam eligibility: grouped once per call

`steam_eligibility` asked the mapping history for one entity's records, and the
quarantine for one entity's candidates, once per entity: every binding and every
quarantine entry was read again for each of them.

It now builds a lookup once per call, from the same state, records and `as_of`: games
by id; per entity, the records bound to it at `as_of`, in binding order; per entity,
the Steam apps currently in quarantine that name it; records by key. Each entity is
then answered from the lookup. `sampling_targets` groups each Steam record's binding
history once in the same way.

The lookup is a local value thrown away at the end of the call. Nothing is stored and
nothing new can disagree with the mapping history.

**The rule did not change.** From the first line that decides anything to the end of
the function, the code is character for character what it was; a test compares the two
texts. Signatures are unchanged.

Parity is checked against `tests/eligibility_reference.py`, the previous implementation
kept verbatim under `tests/`. Every comparison is output for output: classification,
confirmed app ids, asserted-unconfirmed apps, quarantined apps, the reason sentence and
the order, and the sampling targets with their binding references. It holds on the 394
recorded rows (and both fixture sets together), on every bootstrap entity, on the
reference engine's state, at moments before, at and after binding, on a binding closed
and replaced and on one closed without replacement, with records withheld, on the empty
catalogue, on a state with no bindings or quarantine, on one synthetic entity per branch
of the rule, and on a 3,000-record synthetic catalogue.

Preserved exactly:

- Bindings are point-in-time: a later binding does not appear earlier and a closed one
  stops counting from the moment it closed.
- Quarantine is the **current** quarantine at every `as_of`. It was not turned into a
  historical reconstruction.
- An asserted app that Steam's list does not confirm is not eligible. A quarantined app
  is not a binding. A bound app nothing asserts is not confirmed. A title is not an
  identifier. Several eligible apps stay several ids; no primary is chosen.

Recorded coverage is unchanged: 90 eligible, 69 inapplicable, 75 unknown; 91 apps.

### Before and after

Same machine, same day, same synthetic datasets, same deterministic id factory, one
process per size, nothing else running. Apple M3, 8 cores, 8 GB, macOS 26.6.2, Python
3.12.15. One run per size per side; laptop timings, not production metrics.

- **Before**: commit `c89ea4b` (U5.1.2), run from an untouched export of that commit.
- **After**: this checkpoint's working tree, committed as the U5.2 commit.

`uv run python -m tests.catalogue_scale 1000 5000 10000 20000`

| Records | | Resolving | Coverage | Total | Entities examined | Full scans | Resolver calls | Peak memory |
|---|---|---|---|---|---|---|---|---|
| 1,000 | before | 0.46 s | 0.05 s | 0.51 s | 25,176 | 60 | 1,030 | 54 MB |
| | after | 0.14 s | 0.01 s | 0.15 s | 2,233 | 0 | 1,030 | 54 MB |
| 5,000 | before | 10.38 s | 0.96 s | 11.33 s | 578,428 | 312 | 5,167 | 105 MB |
| | after | 1.09 s | 0.08 s | 1.18 s | 11,858 | 0 | 5,167 | 109 MB |
| 10,000 | before | 44.73 s | 4.00 s | 48.74 s | 2,289,686 | 628 | 10,343 | 170 MB |
| | after | 3.22 s | 0.20 s | 3.41 s | 23,887 | 0 | 10,343 | 177 MB |
| 20,000 | before | 191.41 s | 21.87 s | 213.28 s | 9,108,927 | 1,260 | 20,690 | 305 MB |
| | after | 10.70 s | 0.25 s | 10.95 s | 47,982 | 0 | 20,690 | 308 MB |

At every size the outcome is identical before and after: the same number of entities
(432 / 2,070 / 4,117 / 8,210), quarantined records, related products, resolver calls and
fingerprint checks. Only how many entities were looked at changed.

The "before" column also reproduces U5's original measurements to within a second
(213.3 s against 214.2 s at 20,000), on a different day.

What the numbers show:

- **Entities examined is now linear**: about 2.3 per resolver call at every size, down
  from 440 per call at 20,000.
- **Coverage went from 21.9 s to 0.25 s** at 20,000.
- **Memory is unchanged** within a few megabytes. The new index adds one small entry per
  distinct title core.
- **Resolving is still superlinear**, though far less so: each doubling now multiplies
  it by about 3 (it was 4.3). See below.

### What is still superlinear

Profiled at 5,000, 10,000 and 20,000. The remaining growth is in the **quarantine
fingerprint**, not in blocking: each time a quarantined record is re-examined,
`evaluation_fingerprint` hashes every record of every candidate entity, and
`SourceRecord.content_hash` is recomputed on each call rather than kept (59,515 calls
at 5,000; 204,525 at 10,000; 747,955 at 20,000).

It grows faster than the data because a few synthetic entities accumulate records in
proportion to the sample: the largest holds 8 records at 5,000, 16 at 10,000 and 32 at
20,000, while the mean stays at 1.26. Every record that is a candidate for such an
entity re-hashes all of it. Whether real catalogues contain entities like that is not
known.

This was **not** changed in U5.2. It is in the quarantine and source-record code,
outside the two operations that checkpoint was for, and the fingerprint decides when a
quarantined record is re-resolved, so it deserved its own equivalence argument. It was
addressed in U5.3; see the next section.

Also not addressed: a whole-state snapshot per generation; and `directly_reachable`,
`_Links` and the evaluation summary are linear passes that were not examined closely.

### A latent fault found, not fixed

The resolver's `_without` tests a token with `str.isdigit()` and then calls `int()` on
it. A few characters satisfy the first and fail the second (Ethiopic digits, for
example), so a title containing one raises `ValueError` inside the resolver. This
predates the checkpoint, is the same before and after, and was left alone because the
resolver is frozen here. The new index uses only the `isdigit` test for its key and
calls `_without` for the comparison, so it neither adds nor removes the fault.

### Limits of what this shows

- Everything at scale is synthetic. The sample is built to be hard; about 6% of its
  records carried a dozen numbers in the title precisely to exercise the old fallback.
  On the 394 recorded rows there were **0** full scans before this change, so on data
  like that the blocker's gain is small and the eligibility gain is what remains.
- One laptop, one run per size per side. No variance was measured.
- No 50,000-record run was made. Nothing is claimed for it.
- Nothing was run on Cloud Run. Whether a first broad build fits the production job's
  one-hour, 2 GiB limits is still unmeasured, and is a post-soak question.

### What this changes about cost

Nothing is activated and no cost is incurred. The one estimate it affects is the
compute for a first broad build: measured resolving for 20,000 synthetic records fell
from 192 s to 11 s on a laptop, so under the same assumptions as the Cost section
(2–4× slower on one Cloud Run vCPU, about $0.0000185 a vCPU-second) that build is a
fraction of a cent, not one to two cents. It was already negligible; it is more so.
Collection cadence remains the cost that matters, and those figures are unchanged.

### Still to be verified after the soak

- A measurement on real records at scale, which do not exist here.
- A first broad build on the production job's actual vCPU and memory.
- The fingerprint cost above, on real data.
- Adoption itself: the blocked engine is what production's `advance_catalogue` already
  calls, so this code would be picked up by a production image built from it. No such
  image has been built, and none may be until this branch is reviewed and merged after
  C2-F.

## Quarantine fingerprint hashing (U5.3)

**Offline, on the product branch only.** One more processing change. It alters how
often a hash is computed and nothing about what is hashed, what a fingerprint is, or
when a quarantined record is looked at again. Production adoption is still after C2-F.

The U5.2 measurements above stay as recorded. The measurements here are new, taken
against the U5.2 commit as the baseline.

### Why it was costly

A quarantined record is re-examined only when its *evaluation fingerprint* changes. The
fingerprint is a SHA-256 over a canonical payload of: the record's content hash; for
each candidate entity, its id and the sorted content hashes of every record it holds;
the content hashes of unresolved records that link to it; the entity it is already
bound to; and the override that applies. All of that is needed: each part is something
that could change the decision.

A record's content hash is itself a serialisation of the whole record and a SHA-256.
`SourceRecord.content_hash` is a property that does that work every time it is read.
So each fingerprint re-serialised and re-hashed every record of every candidate entity,
on every pass, though no record had changed. At 20,000 synthetic records the engine
read 747,955 content hashes of 20,000 records.

### What was done

`ContentHashMemo` (`domain/source_records.py`): the content hashes of the records one
run handles, each computed once. `generate_catalogue_blocked` makes one at the start of
a run and passes it to `evaluation_fingerprint`, which reads hashes through it.

That is the whole change. `memo.of(record)` returns `record.content_hash`, read once
and kept. The content hash, the fingerprint payload, its ordering and its SHA-256 are
untouched; no algorithm or payload version changed.

The other approaches considered (maintaining per-entity summaries as records attach)
were not needed to get a clear gain and would have added state that has to be kept in
step with the entities. This one adds none.

### Why a kept hash cannot hide new evidence

The memo is keyed by the record **object**, and holds a reference to it:

- A record is immutable. One object has one content hash for as long as it exists.
- Any change to a record is a new object: `model_copy(update=...)`, a re-parse, a
  re-fetch. A new object is looked up afresh. Nothing a record says, and not its source
  id, is part of the key, so changed content under the same source id is never answered
  from an older record's entry.
- Because the memo holds each record, the record stays alive, and an address cannot be
  reused for a different record while its entry exists.
- The memo is created inside a run and dropped when the run returns. It is not
  module-level, not shared between runs and not stored. A continuation is a new run
  with a new memo, given the records as they are now.

What changes a fingerprint is unchanged, because the function still reads the same
things: a candidate entity gaining or losing a record changes the list of hashes; a
rebinding changes `bound_to`; linking context and overrides are read as before.

The reference engine, `generate_catalogue`, was not given a memo. Neither was the
cross-check's all-pairs side. So every comparison between the engines is also a
comparison between hashing with and without one.

### Proof of equivalence

- **A hash.** `memo.of(record) == record.content_hash` for unchanged, re-fetched and
  changed records (external id, title, release date, parent, version parent,
  developer), for equal-but-distinct objects, and across 3,000 short-lived records with
  garbage collection between them.
- **A fingerprint.** The original function is kept verbatim in
  `tests/fingerprint_reference.py`. On hand-built views, the current function with a
  memo, the current function without one, and the original give the same string, and
  that string moves for each of sixteen distinct changes of evidence and stays put for
  the changes that are not evidence (a re-fetch, a non-candidate entity, input order,
  unrelated context, an override for another record).
- **Every fingerprint a real build computes.** With the engine's fingerprint call
  wrapped, each of more than 2,500 fingerprints computed while building the recorded
  fixtures and a 1,500-record synthetic sample, first builds and continuations, was
  re-derived by the original function and matched.
- **A build.** The blocked engine and the reference engine return equal states, and
  equal JSON, on the recorded fixtures and on synthetic samples, for a first build and
  for each continuation, with cross-check on. The pre-existing blocked-versus-reference
  tests pass unchanged, including the 10,394-record one, re-run here.

### Quarantine behaviour, unchanged

Each step below is run through both engines and must give the same state:

- unchanged evidence: nothing is decided again, and the entry (evaluations, dates,
  fingerprint, history) is exactly what it was, including after a re-fetch;
- a bound candidate record changes: the quarantine that leans on it reopens, is
  examined again (`evaluations` 2, `first_seen` kept, `last_evaluated` moved), and,
  the evidence still being ambiguous, stays; an unrelated quarantine is not touched;
- conflicting evidence examined again is still a conflict;
- corroborating id evidence releases a title-only quarantine: one appended decision
  (path A), one appended binding, earlier history intact, and a point-in-time read
  before the evidence still shows the record unbound;
- an unresolved linking record arriving later reopens the record it links to;
- an override added for a quarantined record resolves it;
- no record is dropped: every supplied record is either bound or in quarantine.

No threshold moved. A title is still never an identity.

### Before and after

Same machine, same day, same synthetic datasets, same deterministic id factory, one
process per size, nothing else running. Apple M3, 8 cores, 8 GB, macOS 26.6.2, Python
3.12.15. One run per size per side for the times. Laptop timings, not production
metrics.

- **Before**: commit `55531e0` (U5.2), run from an untouched export of that commit with
  only the measuring harness replaced.
- **After**: this checkpoint's commit.

Content-hash computations are **counted, not inferred**: the harness wraps the
`content_hash` property on the class in a second, separate run of the same evaluation,
so the figure is every real computation by anyone and is comparable across the two
sides. Times are from the first, uninstrumented run. "Requested" is the engine's own
count of how often it asked.

`uv run python -m tests.catalogue_scale 1000 5000 10000 20000`

| Records | | Resolving | Coverage | Total | Content hashes computed | Requested | Fingerprint checks | Resolver calls | Entities examined | Peak memory |
|---|---|---|---|---|---|---|---|---|---|---|
| 1,000 | before | 0.14 s | 0.01 s | 0.15 s | 5,184 | – | 804 | 1,030 | 2,233 | 56 MB |
| | after | 0.10 s | 0.01 s | 0.11 s | 1,000 | 5,184 | 804 | 1,030 | 2,233 | 55 MB |
| 5,000 | before | 1.08 s | 0.08 s | 1.16 s | 59,515 | – | 4,042 | 5,167 | 11,858 | 108 MB |
| | after | 0.71 s | 0.03 s | 0.75 s | 5,000 | 59,515 | 4,042 | 5,167 | 11,858 | 111 MB |
| 10,000 | before | 3.33 s | 0.24 s | 3.57 s | 204,525 | – | 8,086 | 10,343 | 23,887 | 171 MB |
| | after | 1.75 s | 0.19 s | 1.94 s | 10,000 | 204,525 | 8,086 | 10,343 | 23,887 | 179 MB |
| 20,000 | before | 10.49 s | 0.29 s | 10.78 s | 747,955 | – | 16,188 | 20,690 | 47,982 | 307 MB |
| | after | 5.41 s | 0.29 s | 5.69 s | 20,000 | 747,955 | 16,188 | 20,690 | 47,982 | 311 MB |
| 50,000 | before | 78.03 s | 1.00 s | 79.03 s | 4,401,151 | – | 41,532 | 51,729 | 121,421 | 707 MB |
| | after | 36.48 s | 0.98 s | 37.47 s | 50,000 | 4,401,151 | 41,532 | 51,729 | 121,421 | 719 MB |

Entities (432 / 2,070 / 4,117 / 8,210), quarantined records, related products,
resolver calls, fingerprint checks and entities examined are identical on both sides at
every size. The baseline's hash counts are exactly the figures U5.2 profiled.

- **Hashes computed: one per record.** 747,955 became 20,000 at 20,000 records. The
  engine still *asks* 747,955 times; it computes 20,000.
- **Time roughly halved** at 10,000 and 20,000.
- **Memory**: a few megabytes more at the larger sizes, within run-to-run noise for a
  single run. The memo holds one 64-character string per record.
- **50,000 was run once on each side**, as a single larger size beyond the usual four.
  It gave 20,230 entities and 20,766 quarantined records on both sides. It is the first
  measurement at that size and the only one; it used about 0.7 GB.

### What is still superlinear

Resolving still grows faster than the data: about ×2.5 from 5,000 to 10,000, ×3.1 from
10,000 to 20,000, and ×6.7 from 20,000 to 50,000, which is close to quadratic over that
last step.

Profiled at 10,000 and 20,000 after this change, what remains is the **resolver's own
assessment of candidates**: `_assess`, `_has_id_link`, `_names` and `_title_relation`
walk every record of a candidate entity, and the fingerprint calls `candidate_refs`,
which assesses each offered entity. The number of assessments is linear (33,644 at
10,000; 67,634 at 20,000). The work per assessment is not, because a few synthetic
entities hold records in proportion to the sample (the largest: 8, 16, 32 records at
5,000, 10,000, 20,000; the mean stays 1.26). The hash *requests* for those entities are
quadratic too, but each is now a dictionary lookup.

That is inside the frozen resolver and was not touched. Whether real catalogues contain
entities that accumulate records like this is not known; it is a property of one shape
in the synthetic sample.

Also still unaddressed: a whole-state snapshot per generation.

### The numeric-Unicode fault: a separate known item

Unchanged, and not caused by U5.2 or U5.3. The resolver's `_without` tests a title
token with `str.isdigit()` and then calls `int()` on it. Some characters pass the first
and fail the second (Ethiopic digits such as `፩`), so a title containing one raises
`ValueError` inside the resolver. NFKC normalisation converts most such forms (a
superscript `²` becomes `2`) but not all. No test added in this checkpoint exercises
it. It needs its own reviewed decision, because fixing it means changing how the
resolver reads titles, which is identity behaviour.

### Limits of what this shows

- Synthetic records only at scale; one laptop; one run per size per side.
- The gain depends on how often quarantined records are re-examined against entities
  with several records. On the 394 recorded rows the engine asked for 749 hashes and
  computed 397, so there the saving is small in absolute terms.
- Nothing ran on Cloud Run.

### Where this leaves the catalogue work

Three measured costs are gone: full scans, per-entity eligibility scans, and repeated
content hashing. What remains is inside the resolver and in snapshot storage, and both
are separate reviewed decisions.

Processing speed is no longer what limits breadth. The limits are the ones listed under
"What multi-source demand signals still need": entities beyond the bootstrap 14 have no
Wikimedia identifier; the sources are not cleared for broader collection; there is no
agreement rule across source families; and there is no creator-opportunity score. Those
are product-intelligence gaps, and they are the more useful next work.

Still to be verified after the soak, as before: behaviour on real records at scale; a
first broad build on the production job's vCPU and memory; and adoption itself, which
needs this branch reviewed and merged after C2-F.

## Multi-source foundation: what exists, and what was not added

Reviewed, not rebuilt:

| Exists | Where |
|---|---|
| Source families, one voice per independent observation process: Wikimedia readership and Steam players active; news, search, official events, creator supply as vocabulary | `domain/families.py` |
| Applicability per family: available, missing, inapplicable, unknown | `domain/families.py` |
| Six availability states and two kinds of freshness, versioned | `features/semantics.py` (`signal_semantics_v2`) |
| Signals normalised against their own history (lift, robust z, percentile, velocity) | `features/normalise.py`, `baseline_rules_v1` |
| A gate that refuses shadow-only and internal features to operational consumers | `features/access.py` |
| Steam eligibility from bindings, point in time | `catalogue/eligibility.py` |
| Official event evidence, candidates, promotion, coverage, policy gates | `event_sources/`, `events/` |
| Point-in-time validation with publication lag and event-source provenance | `validation/` |
| Per-purpose, per-stage source policy | `domain/source_policy.py` |

Added in this checkpoint: one thing, the coverage summary above, which reuses
`SourceEligibility` and adds no rule.

Deliberately **not** added:

- No aggregation of metrics into a score. No weights.
- No count of "independent sources". Two adapters are not two independent observations;
  the family model already says so, and nothing here counts sources as agreement.
- No Steam metric and no Event Heat in score v0.
- No rule for combining an entity's several Steam apps. It stays unresolved, as
  `baseline_rules_v1` says.

What multi-source demand signals still need, in order:

1. **A Wikimedia identifier for entities beyond the bootstrap 14.** Today a new entity
   has no source identifier at all, and a title may not be turned into an article name.
   This is the largest gap: 220 of 234 entities are `unknown` for the only source
   cleared for unattended collection.
2. **Permission.** Steam player counts are private-development only. Derived metrics
   and commercial use are unanswered for both Steam and IGDB.
3. **A combination rule** for entities with several eligible apps.
4. **An agreement rule across families**, defined before it is fitted and validated
   under the existing protocol. Not started.

## Cost

Principle unchanged: **breadth first, frequency selective.** A catalogue's size is not
its collection cost; what is *due* is. Request budgets belong to sources, not to the
catalogue.

Nothing in this checkpoint costs anything: it runs locally and activates nothing.

What broad expansion would add, separating measured from estimated:

| Item | Basis | Estimate |
|---|---|---|
| **Catalogue discovery, requests** | Documented page limits: IGDB 500 records a request, Steam's app list up to 50,000 a page | 20,000 IGDB games is 40 requests; 100,000 is 200. API price $0. Well inside both sources' stated limits |
| **Catalogue discovery, compute** | **Measured:** 192 s to resolve 20,000 synthetic records on a laptop. **Assumed:** 2–4× slower on one Cloud Run vCPU; the approved rate of about $0.0000185 a vCPU-second | A first 20,000-record build: about 400–800 vCPU-seconds, **one to two cents, once.** Later runs resolve only what is new |
| **Metadata refresh** | **Assumed:** one small execution a day, billed at the one-minute minimum, like the approved Wikimedia collector row | about **$0.03 a month** |
| **Daily Wikimedia coverage** | **Approved planning figures** (C2 plan §28), simulated, hourly tick, one build a day, including a stand-in sampled source that is not approved | 1,000 catalogued $4.28; 5,000 $4.69; 10,000 $5.76; 50,000 $11.55 a month |
| **Selective sampled monitoring** | C2 plan §4, an estimate: about 24,200 requests a day at the tier caps | about $9 a month of compute at one request a second, or inside the free allowance at four concurrent. **Blocked by policy**, not by cost |
| **Storage growth** | **Measured:** about 2.1 KB of snapshot per record. **Assumed:** list price of about $0.02 a GB-month; one stored generation a day when something changed | 20,000 records is about 42 MB a generation; a month of daily generations about 1.3 GB, **about $0.03 a month, accumulating** until old snapshots are pruned |

Against the boundaries (preferred $5–10, scrutiny $10–15, hard $20): catalogue
discovery, refresh and storage together are cents. The cost that matters is collection
cadence, and the approved figures already put 10,000 catalogued inside the preferred
range and 50,000 inside scrutiny. Those figures are simulations with one real source
measured; they are planning numbers, not commitments.

The real limits on breadth today are not money. They are: no identifier source for new
entities, source policy, and the quadratic resolving cost above.

## Local catalogue state: how each command is governed

Several local commands read recorded external source records. Two of them write
derived catalogue state to local disk. None of them touches production: no bucket, no
generation, no `CURRENT` pointer.

| Command | Reads recorded records | Writes catalogue state | Governed by |
|---|---|---|---|
| `evaluate-catalogue` | yes | **no** (in memory) | `--stage` required; every source must clear it for `CATALOGUE_METADATA` |
| `generate-catalogue` | yes | **yes**, a local state file | `--stage` required, no default; the same gate, applied before anything is resolved or written; `--as-of` may not precede the records' fetch time |
| `sample` (Steam player counts) | yes | **yes**, the same local state file if none exists | Private development, stated in the handler, the only stage its own policy clears; the same gate, whether the state is read or generated; a reused state needs its provenance and every record it was made from |
| `audit-bootstrap-bindings` | yes | **no** | **Not stage-gated.** A read-only identity audit: it resolves in memory and prints the bootstrap entities' bindings to the terminal. It persists nothing |
| `ingest-events` | yes, in memory, to resolve Steam app ids | **no** catalogue state | Gated by the Steam News policy for what it stores. Its in-memory use of catalogue records is not stage-gated |
| `probe-sources steam-players` | yes, in memory, to check an app is an eligible target | **no** | Gated by the Steam player-count policy for the call it makes. Its in-memory use of catalogue records is not stage-gated |

Before U5.1, `generate-catalogue` and `sample` wrote that state with no policy check at
all. That was never a production mutation; it was local state that looked the same as
authorised state.

Both now go through `require_catalogue_sources`, the gate the evaluation uses, with the
existing `SourcePolicy` definitions. No stage is defaulted, no source name is exempt and
nothing is looked up behind the caller: the command hands in the mapping.

A written state file now has a companion, `<name>.provenance.json`, saying what it is:
`kind: local_offline_catalogue_state`, `is_production_catalogue: false`, the stage it
was generated for, and each source with its record count and cleared stages. A local
state is **not scoped** as production is: it resolves every recorded record, which is
exactly why it must not be mistaken for the production catalogue.

The rows marked "not stage-gated" are stated as they are, not as they might be
wished. They write no catalogue state. Whether a read-only use of recorded records
should also name a stage is open.

## Source policy follows the state, not the latest batch (U5.1.1)

The first policy gate looked at the records supplied to a call. That is not enough once
a state is continued: what earlier sources put into a catalogue is still in it, whether
or not those sources appear in the next batch. A state made with Steam's catalogue
records at private development could be continued at private collection, which Steam
does not clear, simply by supplying only IGDB records the second time.

Four things are now kept apart, in both the evaluation and the local state files:

| | What it is |
|---|---|
| Records supplied now | The batch given to this call |
| Records processed before | Every record the state being continued was made from |
| Sources retained | Every source whose record left a binding, a decision, a quarantine entry or a related product in that state |
| Sources authorised | Those whose policy clears the stated stage |

**Retained sources are read from the state itself** (`retained_sources`), from its
bindings (closed ones included), decision log, quarantine and related products. The
bootstrap entities are not an external source. This is the same reading production's
repository uses before it commits a generation.

Two rules follow, and the first is checked first:

1. **Every retained source must clear the stated stage**, exactly as every supplied
   source must. A missing policy, a policy under another name, or a stage not cleared
   is a refusal that names the source and says it is retained. Being left out of the
   current batch excuses nothing.
2. **A continuation must be given every earlier record again**, plus what is new
   (`require_complete`). The engine reads its records as all the evidence there is, so
   a record left out is evidence withdrawn: quarantined records that leaned on it would
   be decided again with less to go on. A partial or empty batch is refused.

A stage change is authorised by the policies and nothing else: a state made only from
IGDB records may move from private development to private collection, because IGDB
clears both; a state that retains Steam's catalogue may not.

### Continuing a broad evaluation

`evaluate_broad_catalogue(records, previous=earlier_evaluation, ...)`:

- `stage` and `policies` must cover the sources supplied **and** the sources retained in
  both of `previous`'s catalogues;
- `records` must include every key in `previous.record_keys`;
- `as_of` may not be earlier than `previous`'s, nor than any record's `fetched_at`.

With no external source supplied and none retained there is nothing to authorise: the
result is the bootstrap catalogue, and it can be continued at any stage. The stage must
still be stated.

`EvaluationSummary.sources` lists each source in use, the records of it supplied to the
call, and `retained_from_previous`.

### Reusing a local state file

A local state file is reused only if what it rests on can be established. Beside
`state.json` is `state.provenance.json`, which now carries:

- `state_sha256`: the digest of the state file it describes;
- `retained_sources`: every source retained in that state, read from the state;
- per retained source, the records supplied in the run that wrote it, and that source's
  cleared stages;
- `kind`, `is_production_catalogue: false`, the stage, and when it was generated.

On reuse (`generate-catalogue` with an existing `--state`, and the `sample` command):

| Check | Refused when |
|---|---|
| Provenance exists | there is no provenance file |
| Provenance is readable and is a local state's | unreadable, not an object, wrong `kind`, or `is_production_catalogue` is not `false` |
| It describes *this* file | `state_sha256` is not the state file's digest: the state was edited, or the file belongs to another state |
| It agrees with the state | its `retained_sources` are not the sources the state actually retains |
| Policy | any retained or supplied source does not clear the stated stage |
| Completeness | the supplied records omit a record in the state's decision log |
| Time | `as_of` is earlier than the state's `generated_at`, or than a record's `fetched_at` |

Both paths that reuse a local state enforce every row of that table, the full-record
requirement included:

- **`generate-catalogue`** continuing an existing `--state`;
- **`sample`**, which reuses the state to decide which Steam apps to sample (enforced
  from U5.1.2; before that it checked provenance and policy but not completeness).

Completeness matters to the sampler for a specific reason. Steam sampling eligibility is
read from two things together: the state's bindings, and the supplied records' own
assertions (a bound record must list the app as a structured external id). Bindings
made from the complete records, read beside a smaller set, give an eligibility that
matches neither. So the sampler refuses incomplete evidence **before** it chooses a
target, opens a client, sends a request or writes a sample, with an ordinary error
that says how many records are missing and from which source. Nothing on disk changes.

Both use the same two functions, `known_record_keys` and `require_complete`; there is
one definition of completeness.

**The provenance file is a description, never a grant.** Its declared stage and cleared
stages are not read when deciding anything. A file edited to claim "public_launch",
with a matching digest, changes nothing: the retained sources are read from the state
and held to the source policies. The digest is not a signature and is not meant to
resist a determined forger with access to both files; it ties a description to the
state it describes and catches a stale or mismatched pair. Nobody can widen a
permission by editing either file, because no permission is taken from them.

A refused state is **left exactly as it is**: not repaired, not rewritten, not deleted.

### Old local states

A local state written before provenance existed (or by U5.1, whose provenance has no
digest) is **refused**. It does not inherit permission from whatever records are
supplied today.

What to do with one: leave it where it is or move it aside, and generate a new state
into a new path from recorded inputs that clear the stage:

```
uv run metaspike generate-catalogue --stage private_development --state <a new path>
```

The new state will have new canonical ids for entities beyond the bootstrap ones and
new binding dates. There is no automatic migration, because nothing in an old state
says under what policy it was made, and guessing would be the thing this closes.

## What was not activated

- Broad discovery in production. The constant is `False`; a test pins it.
- Any live call to Wikimedia, Steam or IGDB.
- Any stored catalogue, generation or pointer move.
- Any new source in production, any sampling, any tier, any schedule.
- Any score, score component or weight.
- Any change to the developer interface or the artifact contract (still 1.5.0).

## What remains for a broad rollout

1. ~~A fix for the full-scan fallback in blocking~~ and 2. ~~an indexed
   `steam_eligibility`~~: done offline in U5.2, with the equivalence proofs re-run.
   Still needed: review, a measurement on real records at scale, and the remaining
   fingerprint cost.
3. A snapshot retention or delta policy.
4. A measured first broad build on the real job's vCPU and memory.
5. A source of Wikimedia identifiers that is not a title match.
6. Policy: Steam's catalogue is not cleared for unattended collection; neither Steam
   nor IGDB is cleared for anything public.
7. A decision to turn `BROAD_DISCOVERY_ENABLED` on, which is a reviewed change by
   itself and is not proposed here.

All of it is after C2-F.

## Limitations

- 394 real recorded records. Everything larger is synthetic and measures cost only.
- Timings are from one laptop and one run per size.
- The share of real records that need a full scan is unknown.
- Local catalogue commands are governed as described under "Local catalogue state"
  above. None of them is the production catalogue.
- Coverage counts identifiers. It says nothing about whether a source's data is good.
