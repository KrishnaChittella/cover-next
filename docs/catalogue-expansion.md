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
second). The 5,000, 10,000 and 20,000 cases are marked `slow` and excluded by default.
The pre-existing 10,000-record blocked-versus-reference proof is unchanged and also
`slow`.

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

1. A reviewed fix for the full-scan fallback in blocking, with the equivalence proof
   re-run, and a measurement on real records at scale.
2. An indexed `steam_eligibility`.
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
