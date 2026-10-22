# Live API validation and parallel development — 22 October 2026

**Decision:** MetaSpike's core offline architecture, fixtures, deterministic tests, safety gates, and developer UI are established enough to move into **real API experiments and real-data integration**. From now on, **live source validation and product development proceed in parallel**. Do not spend another milestone on purely theoretical API integration, additional speculative source frameworks, or extended observation periods. Offline and synthetic tests **remain regression tools**, not substitutes for live source evidence.

**Delivery target:** finish core multi-source creator-opportunity product development by **5 November 2026**. This is a development target, not authorization to launch or proof that external data rights, quotas, or the production release gates are cleared.

## Product outcome: begin with the creator decision

An end-to-end result is a dated, attributable game event or meaningful attention change, resolved to the correct canonical game UUID, compared with time-correct demand/coverage evidence, and converted into an actionable **why now / video angle / creator fit / caveats** brief. The result must show its sources and uncertainty, and may say **watch** or **abstain**. API count, fetched rows and test totals are not the product.

## We do real source tests now

For each source, work in this order:

1. Read its current policy in the repository and the provider's current documented interface/terms. Distinguish permission for a **bounded private read** from permission for unattended collection, long retention, derived scores, redistribution and commercial display. Don't assume unknown public reuse rights prohibit every permitted private experiment; don't assume a working API key grants public reuse rights.
2. Define a **small, named, owner-reviewed live probe**: source, purpose, exact canonical/game-native ids or event query, endpoints, request/page limits, expected budget, region/storage, retention, stop/refusal conditions, and credential method. Securely use existing keys; don't put values in a prompt, log, Git commit or PR.
3. Execute only within the source policy and any required **per-probe owner authorization**. Capture actual status, response shape, retrieval and publication times, incomplete pages, source-side counts, headers needed to understand quota/cache, and real request usage. Stop on refusal, quota failure or unexpected scope. Do not turn source errors into zeros.
4. Record the exact response and `acquisition_record_v1` when permitted; inspect, redact, label **UNVERIFIED** until independently reviewed/pinned; enforce retention and source-specific data restrictions. Raw, sampled, derived, and interpreted facts remain distinct.
5. Test source-native IDs against the existing versioned canonical UUID resolver. No title-only merge; quarantine ambiguous matches. Use difficult examples (editions, DLC, shared names, remakes, renamed titles, platform-specific IDs).
6. Report: **actual request count/quota consumption; verified response schema and oddities; match/ambiguity results; what creator decision this source improved; operational cost; permission/retention constraints; next action**. If incremental value is poor, reduce or drop the source.

These are **live integration probes, not permission to turn on production schedulers**. Sensitive operations retain their review gates in `CLAUDE.md`: owner approval for production source activation, secrets/versions, IAM/WIF, Terraform apply/destruction, and public deployment. No repeated polling or uncontrolled backfill. Respect upstream ToS and actual project quotas.

## Parallel development lanes — small, non-overlapping PRs

| Lane | Start | Work now | Acceptance |
| --- | --- | --- | --- |
| **A — Live API evidence** | Immediate | Run/review narrowly scoped live tests: the existing supervised Steam players recorder (8 eligible apps, one 15-minute slot, 8 attempts maximum); implement a similarly bounded Steam News recorder; run suitable IGDB identity probes and a small YouTube event-query feasibility probe only after verifying the project's actual quota and permitted development use. Investigate Twitch and official developer feeds as candidates. | An inspected actual response per available source, exact consumption, acquisition/provenance record, permissions/retention state, no fabricated completeness or public-use claim. |
| **B — Cross-source identity** | Immediate | Challenge canonical UUID mappings with 10–20 real multi-platform cases and hard negatives. Connect app/category IDs, articles, event references and video-game associations through distinct source-specific rules and binding times. | Correct matches and correct quarantines, documented ambiguous cases, reproducible regression set, no false title-only joins. |
| **C — Evidence and intelligence** | Immediate | Reuse real Wikimedia warehouse observations with bounded point-in-time reads and source provenance; ingest verified event records; build a new versioned event-first opportunity rule if needed (do not alter frozen v1). Produce game/event/topic, why-now, angle, audience fit, supporting and contradicting evidence, and unknowns. | At least one actual event/game + genuine evidence yields an explained candidate/watch/abstain report; missing Steam/YouTube never masquerades as zero or blocks all processing; no unverified data counted as verified. |
| **D — Creator brief + UI** | Immediate, using contract fixtures | Agree on the private brief schema early; implement public-safe artifact mapping and UI screens independently of source timing. Show game/event, source state and freshness, content angle, creator fit, caveats and explicit abstentions. | Contract/schema/TypeScript checks and end-to-end rendering pass; private or rights-restricted raw evidence cannot leak into public artifacts. Public activation is separately approved. |
| **E — GCP and quality operations** | Ongoing, independent | Keep Wikimedia's existing 12:00 UTC collection and 13:00 UTC warehouse/dbt run; inspect failures with bounded GCP log and BigQuery reads. Investigate specific data failures, not more soaks. Evaluate whether one volume-test failure should prevent diagnostic mart builds without weakening missing-period checks. Prepare any needed infra plan for owner review. | Scheduled outcomes correctly classified, `period_due_day_status` and missing/late semantics intact, current spend measured, no silent change to scheduler/control record. |

Avoid editing the same files across concurrent branches. Agree the brief/evidence contract first, then parallelize source probes, identity regression, data reader and UI. Merge small green CI routine code/tests/docs PRs as already authorized, but never infer CI alone approves sensitive production operations.

## Order and gates

**22–24 October:** Merge this decision; launch lanes A–E without waiting for Wikimedia; complete small live-probe approval packets and commence permitted tests; establish the brief interface, real-data reader and identity test corpus. Do not duplicate existing offline adapters.

**25–29 October:** Deliver the first genuine event → canonical UUID → time-correct evidence → explainable creator brief → private UI integration. Record observed quotas, payloads, data gaps and actual source value. Fail closed on unverified observations and invalid source bindings.

**30 October–5 November:** Expand genuine event/game test cases, implement creator-fit improvements and current-source-aware discovery, harden performance/cost, close the public-safe artifact and product UI path, and run release acceptance checks. Publish/activate only after the owner reviews launch and source-use gates; list external rights blockers instead of inventing clearance.

## Wikimedia is not a blocker

Wikimedia is historical public-curiosity evidence, not the sole truth about a creator opportunity. Its existing 12:00 UTC collector and 13:00 UTC warehouse remain active. On 22 October, the collector correctly skipped the already-complete 20 October slot; the warehouse loaded the 16–20 October repair and failed the daily observed-row volume check due to the prior schedule transition. The 16 October recovery must retain `late` status once the mart builds. This should be checked at/after 13:30 UTC on **23 October**, but unrelated API integration and product work **do not wait**.

If the scheduled check fails again: determine whether it failed at **source response, collector/work order, ingestion, dbt dependency, or alerting**; establish the exact observed data before changing code; preserve missing vs zero and late vs on-time; and continue other lanes. A failed check does not authorize suppressing the check, rewriting history, backfilling by hand, or restarting prolonged C2-F soaking.

## Handoff contract for coding agents

Read the [README product charter](../README.md#product-charter--why-metaspike-exists), `CLAUDE.md`, and the latest *dated* handoff entry. Prefer the actual current `main` and live status over stale historical notes. At each meaningful milestone, record **which real endpoints were called, exact source-native IDs/queries, returned facts, quotas, acquisition standing, tests, PRs, approvals, and what remains blocked**. Do not claim an offline adapter is a running collector or that an unverified recording is trusted evidence.

**Non-negotiable:** real experimentation is now the priority; permission to call, permission to store, and permission to publish are separate; preserve low-cost bounded execution, no secret exposure, source isolation, evidence provenance and explicit owner-controlled deployment.
