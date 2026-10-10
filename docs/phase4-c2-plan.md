# Phase 4 Part C2: plan for safe, low-cost, unattended collection

**Status: decisions made (section 20); C2-A is built offline (section 24); nothing is
deployed, applied or run.** The scheduler is paused and stays paused until the
activation gate in section 19.

Part C1 proved the private pipeline works when a person runs each step
([phase4-c1-implementation-report.md](phase4-c1-implementation-report.md)). Part C2
turns it into a system that runs on its own, safely and cheaply.

C2 is not a public launch, not website work, and does not authorise any source that is
not already cleared. MetaSpike still has one public release, and it is not near.

## 1. Principles

**Breadth first, frequency selective.** MetaSpike should eventually catalogue tens of
thousands of games, monitor several thousand, and sample only a small, changing subset
at high frequency. A niche game must be promotable quickly when attention accelerates
or an event lands; a quiet game must stay cheap. Nothing in this plan assumes a
ceiling of a few hundred games.

**Cost.** Preferred $5 to $10 a month; $10 to $15 acceptable with scrutiny; nothing
projected above $20 may be enabled without explicit approval. $20 is a boundary, not a
target. The model that gates the scheduler must be built from measured executions.

**A missing value is never zero.** A slot that was not sampled, a source that is
unavailable and a source that is disabled are states, recorded as states.

**Scoring stays in Python.** Nothing here moves feature or scoring semantics into SQL
to save a query.

## 2. What C2 starts from

Honest about how small the real workload is today:

- One source is cleared for unattended private collection: Wikimedia pageviews.
- The catalogue is the 14-entity bootstrap set. Broad discovery is off.
- Steam and IGDB are not cleared, and no secret has a value.
- The baseline rule's change-point output is shadow only and may not promote a tier.

So the real unattended workload C2 can switch on is 14 daily Wikimedia series, and it
stays 14 throughout C2 (section 20, decision 1). The tiering, caps and scale design below
are built and proven in simulation and exercised for real only as far as cleared
sources allow. That is a deliberate limit, not an oversight: the machinery must exist
and be trusted before a high-frequency source is ever connected to it.

## 3. Steady-state Wikimedia

The S3 run fetched 90 days because it was a first run. Routine runs must not.

| | Decision |
|---|---|
| History fill | 90 days, once per article, the first time it is seen. Tracked by a marker under the collector's own `ops` prefix. At most 200 new articles are filled per execution, so a large addition drains over several runs |
| Steady-state window | 4 days ending at the latest due day |
| Cadence | Once a day per article, in the first dispatcher tick after 03:00 UTC |
| Why 4 days | Wikimedia publishes a day about one day late and occasionally revises recent days. Four days absorbs a missed day or two without a history fill |
| Weekly reconciliation | One run a week uses a 14-day window to pick up later revisions. This goes through the existing late-data path |
| dbt lookback | The period models' routine lookback rises from 3 to 7 days, so a 4-day window is ordinary work and not "late data". Sample models keep 3 days |
| Idempotency | Unchanged: completion marker per article and day, create-only batch, deterministic load id. A repeated run asks the source nothing for an article already marked |
| Failure | Per-article failures are recorded and retried at the next eligible tick, up to 3 attempts a day. The platform retry stays at 1. Whatever is missing the next day is inside the window |
| Faster cadence | None. The source is daily. A game is promoted by sampling a faster source, not by asking Wikimedia more often |

Overlap means the raw table receives up to four rows per article and day. Staging
already resolves one. At 5,000 articles that is about 20,000 small rows a day, a few
megabytes.

Requests are one per article per run whatever the window:

| Articles | Requests a day | Executions a day (600 targets each) |
|---|---|---|
| 14 | 14 | 1 |
| 1,000 | 1,000 | 2 |
| 5,000 | 5,000 | 9 |
| 10,000 | 10,000 | 17 |

At 5,000 and above, serial requests at about one a second cost real compute (roughly
5,000 vCPU-seconds a day). Bounded concurrency inside the collector, kept well inside
Wikimedia's published limits and identified by a descriptive user agent, is the lever.
It is measured before the target set grows, not assumed.

## 4. Adaptive scheduling

Tiers govern sample-now sources. Period sources such as Wikimedia are daily for every
monitored game whatever its tier.

| Tier | Interval | Initial cap | Meaning |
|---|---|---|---|
| T0 surge | 15 min | 25 | An event or a confirmed acceleration, for a bounded time |
| T1 hot | 30 min | 75 | Elevated and still moving |
| T2 active | 1 h | 300 | Steady interest worth hourly resolution |
| T3 warm | 3 h | 1,000 | Background monitoring |
| T4 long tail | 24 h | the rest of the monitored set | Cheap presence |
| T5 catalogued | none | unlimited | Known, not sampled; period sources only if eligible |

The caps are part of the policy, not advice: a tier that is full does not grow. A
promotion into a full tier displaces the entry with the weakest claim, which moves
down one tier.

The tiers and caps above are simulation defaults. They are **not approved request
budgets**: a source's own request caps always override what a tier could hold, and no
uncleared source gets any work.

**Escalation in C2 is shadow only** (section 20, decision 2). The rule
`tiering_rules_v0` proposes a tier from a game's latest value against its trailing
median, with an absolute floor, and from a recent event when one is known. Missing
history proposes the long tail: unknown is never treated as zero and never escalates.
The proposal is recorded beside the schedule entry and changes nothing. The baseline
change-point output is not read by it at all.

The one thing that moves a target in C2 is a **pin** in the control record, for
supervised testing only: explicit, with an expiry, a reason and a name, and inert
after its expiry. Pins are not the product's design.

Later, when it is approved, escalation would move one tier per evaluation on the
rule's proposal, an owner pin, or a lifecycle event from a cleared catalogue source.

**De-escalation:** a game holds a tier for a minimum dwell time (T0 6 h, T1 24 h, T2
3 days, T3 7 days), then drops one tier per evaluation when its reason has lapsed.
Hysteresis: the threshold to leave a tier is lower than the one to enter it.

**Stale and error behaviour:** a target whose source has failed repeatedly is not
escalated on missing data, and is not sampled faster to compensate. It keeps its tier
and its slots are recorded as expected and not observed.

Tier evaluation runs once a day and after any catalogue event, never per tick. Its
output is a versioned schedule. The dispatcher only reads it.

Until a sample-now source is cleared, T0 to T3 are empty in production and every
monitored game is effectively daily.

### What the tiers would cost on a sampled source

Not activated; shown because it decides the caps. With the caps above and a 3,000-game
tail: 25×96 + 75×48 + 300×24 + 1,000×8 + 3,000 ≈ 24,200 requests a day.

- Against Steam's limit of 100,000 calls a day that is about a quarter, which is the
  intended ceiling.
- At one request a second it is about 726,000 vCPU-seconds a month, roughly $9 beyond
  the free allowance on compute alone. With four concurrent requests it is about
  180,000 and inside the allowance.

So these caps fit the budget only with bounded concurrency, or they must be smaller.
That is an estimate; it is measured before any sampled source is switched on.

## 5. Catalogue scale

Five sets, kept separate:

| Set | What it is | Size it must tolerate |
|---|---|---|
| Catalogued | Every canonical entity | tens of thousands |
| Monitored | Has a tier T0 to T4 | several thousand |
| Eligible for a source | Monitored, has that source's identifier, and the source's policy clears the stage | per source |
| Due | Eligible and its interval has elapsed at this tick | bounded by the caps |
| High frequency | T0 to T2 | 400 at the initial caps |

The dispatcher must do work proportional to what is due, not to the catalogue:

- **A schedule, not the catalogue.** The daily tier evaluation writes a versioned
  schedule partitioned by source and tier. The catalogue is not opened by the
  dispatcher.
- **Read only what can be due.** T0 is read every tick, T1 every second tick, T2 once
  an hour, T3 every third hour.
- **Cohorts for the long tail.** T4 is split into 24 cohorts by a hash of the native
  id. One cohort is due each hour, so the daily tail is spread evenly and one tick
  reads one twenty-fourth of it.
- **Ledger rows only for what is due.** `ms_ops.expected_targets` grows with work
  planned, not with catalogue size.
- **No listing.** Collectors still cannot list. A collector derives its work from the
  schedule version and the tick it was started for, both named in a work order the
  dispatcher writes before starting it.

Job templates are fixed and the dispatcher may only start a job, not pass it
arguments. The work order is therefore an object, not a parameter. Where it lives and
which prefix grant lets a collector read it is settled in C2-A and goes through a
reviewed plan like any other IAM change.

Proof at scale is by simulation in C2-A: synthetic schedules of 1,000, 5,000, 10,000
and 50,000 entities, asserting that the objects read and the rows written per tick
depend on what is due and not on the total.

## 6. Dispatch safety

Initial limits, all enforced in code and all configuration, so a reviewed commit
changes them:

| Limit | Initial value |
|---|---|
| Job starts per tick | 6 |
| Executions of one collector per tick | 1 |
| Targets per collector execution | 600 |
| Outbound attempts per execution (physical requests, retries included) | targets × 1.5 where the day's remainder allows, hard stop at 1,000 |
| Outbound attempts per day, Wikimedia | 10,000, reserved order by order (Part C2-C.1) |
| In-run retries per request | 2, with backoff, honouring `Retry-After` |
| Platform retries | 1 for collectors and warehouse, 0 for the dispatcher (as deployed) |
| Task timeout | As deployed: dispatcher 300 s, Wikimedia 900 s, warehouse 1,800 s |
| Cloud Run executions per day, all jobs | 120 at an hourly tick |
| Concurrent executions of one job | 1; a tick whose collector is still running does not start another |

**Backlog.** There is no catch-up loop. A tick dispatches the current tick only.

- A missed sample-now slot is gone. It is recorded as expected and not sampled.
- A missed period day is recovered by the window, with no extra request.
- Work beyond a cap is deferred, oldest due first, and recorded as deferred in the
  ledger. A backlog larger than the caps drains at the capped rate, however long that
  takes.

**Circuit breaker, per source.** Three consecutive failed executions, or any HTTP
429, opens the breaker. While it is open the dispatcher plans nothing for that source
and records the reason. It half-opens after a cool-down that doubles from one hour to
a maximum of 24, with a single small probe execution.

**Rate limiting.** A 429 stops the execution's remaining requests at once. The
collector lands what it has, records the limit in its health record and exits
successfully. It does not retry into a rate limit.

**Stale targets.** A target with no success for seven days is flagged in health and
left at its tier. One with none for 30 days drops to T5 and is reported.

## 7. Cost guardrails

What actually stops spend, and what only tells someone:

| Guardrail | Kind |
|---|---|
| Scheduler pause | Hard. The one-action stop |
| Dispatcher control record (section 16): enabled flag, armed-until time, per-source switches | Hard, in code |
| Per-tick, per-execution and per-day caps (section 6) | Hard, in code |
| Daily execution budget kept by the dispatcher | Hard, in code |
| Cloud Run task timeout, retry count and task count | Hard, platform |
| `maximum_bytes_billed`, 1 GiB per query, in dbt and the adapters | Hard, per query |
| BigQuery custom quota on query usage per day for the project | Hard, platform. 10 GiB a day (section 20, decision 5) |
| `require_partition_filter` on every raw table | Hard, per query |
| Full refresh needs an explicit flag; the routine command never passes it | Hard, in code, with a test |
| History fill only for an article with no fill marker, capped per run | Hard, in code |
| Alert: executions per day above budget | Alert |
| Alert: landing bucket above 5 GB | Alert |
| Google Cloud budget at 25, 50, 75, 100 percent and forecast | Alert |
| Monthly cost projection reviewed before each widening of scope | Workflow |

**The Google Cloud budget is not a spending cap.** It sends email. It stops nothing.

No automatic billing-driven stop is built in C2 (section 20, decision 4). The limits
above are what bound spend.

**The BigQuery daily quota, for C2-B.** A custom quota of 10 GiB a day on query usage
for the project. It is a real stop, and four things about it are stated plainly:

- it applies to the whole project, so it also stops the owner's own ad hoc queries for
  the rest of that day once reached;
- it is a temporary C2 ceiling; raising it needs a measured reason;
- it is not a monthly billing guarantee: 10 GiB a day is about 300 GiB a month, far
  below the 1 TiB allowance, but it bounds one day, not a bill;
- measured need is small: the one warehouse build a day bills about 0.13 GiB.

One awkward point to settle in C2-B, not improvised here: the Terraform resource that
sets a quota override (`google_service_usage_consumer_quota_override`) exists only in
the `google-beta` provider at the pinned version, not in `google`. The options are to
add the beta provider for that one resource, or to set the quota once with the
owner's login and assert it with a read-only check. Either way a test or an audit
step must fail if the quota is missing or larger than 10 GiB.

## 8. Monitoring

| Metric or policy | Decision | Threshold and reason |
|---|---|---|
| `ms_warehouse_failed` | **ALERT** | Any occurrence in an hour. The warehouse runs a few times a day and a failure stops history from advancing |
| `ms_source_rate_limited` | **ALERT** | Three or more in an hour. One is handled by the breaker and logged; three means the caps are wrong |
| `ms_slot_not_observed` | **Dashboard and log only** | Per-slot events would be noise. The useful signal is the coverage ratio in `sample_coverage_daily`, which gets an alert when a sampled source exists |
| Steam refused the key | Keep | Dormant until Steam is cleared. Correct as written |
| Repeated job failures | Keep | More than one failed execution of a job in an hour |
| Parity mismatch | Keep | Dormant until parity runs on real state (Part D) |
| Dispatcher silence | **Change** | Threshold becomes three tick intervals (three hours at an hourly tick), enabled only while the scheduler is running |
| Source freshness | **Deferred** | "No success in 36 hours" cannot be expressed reliably with built-in metrics (25-hour window limit); it needs a metric of our own. Covered indirectly meanwhile |
| Executions per day | **New, ALERT** | More than 60 in 24 hours over every job; the plan is about 26 and the code's ceiling is 120 |
| Landing bucket size | **New, ALERT** | Above 5 GB |

Every alert auto-closes when its condition clears. That is eight policies (seven
active while the scheduler is paused) and one channel. None should fire in normal operation.

## 9. Source health

One health record per source, `dispatch/health/{source}.json`, written only by the
dispatcher. The collector reports an outcome by filing a receipt for its work order in
its own `ops` prefix; at the start of the next tick the dispatcher folds that receipt
into the health record, or counts the order as a failure if it expired with no
receipt. One writer, and no new access.

It holds: last attempt, last success, consecutive failures, breaker state and when it
reopens, cool-down, last rate-limit time and the order still awaiting an outcome. The
day's request count is kept beside it in the dispatcher's daily counters. From these
one of the following states is derived:

| State | Meaning |
|---|---|
| `healthy` | Last attempt succeeded |
| `degraded` | Recent failures, breaker closed |
| `rate_limited` | Breaker open after a 429 |
| `unavailable` | Breaker open after repeated failures |
| `stale` | No success within the source's freshness limit |
| `disabled_by_policy` | The source's policy does not clear this stage |
| `disabled_no_credential` | Cleared, but no secret version is supplied |
| `disabled_by_control` | Switched off in the dispatcher control record |

Downstream, the same states travel with the data: a source that is not `healthy`
contributes a state, never a number. `source_health_daily` remains the historical
view; the record is the current one.

## 10. The expected-target ledger in unattended use

`ms_ops.expected_targets` gains six nullable columns, appended like
`source_timestamp` was: `tier`, `due_at`, `due_reason`, `policy_status`,
`disposition` and `work_order_id`.

`disposition` is one of `planned`, `deferred_cap`, `deferred_collector_busy`,
`skipped_breaker_open`, `skipped_disabled_by_control`, `expired` and
`expired_backlog_overflow`. A row is written when something changes for a target: when
it becomes due, and again when deferred work is finally planned or given up. A target
that is simply still waiting gets no further row.

With the schedule version already on each row, the ledger answers at any point in
time: which target, source and metric was expected, when, at what tier and why;
whether it was sampled; if not, whether that was a cap, a breaker, a switch or a
failed attempt; and when it was next due. `NOT_SAMPLED` stays derived in the timeline
view and is never stored.

## 11. Warehouse and dbt cost

A dbt build bills about 136 MB whatever arrives, because each of its roughly 40
statements is billed a 10 MB minimum. In dollars that is small (about $0.0008 a run
at list price) but it is the kind of cost that grows silently with frequency.

- **Run when there is something to load.** The dispatcher starts the warehouse on the
  tick after a collector was started, and once a day regardless as a safety net. With
  only Wikimedia that is one or two runs a day instead of 24.
- **Group batches.** One warehouse run loads everything that has landed since the
  last one.
- **Build what changed.** A run that loaded only period observations builds the
  period models. The sample models and their tests are built only when sample
  batches were loaded. The job already tracks which groups are pending.
- **Hourly against daily.** The hourly selector exists for sample data. It is not
  used until a sampled source is active.
- **Full refresh is never routine.**

Projected at one to two runs a day: 4 to 8 GB billed a month, against a free
allowance of 1 TB.

## 12. Backpressure and recovery

| Event | Behaviour |
|---|---|
| Scheduler outage | Ticks are simply missing. The next tick dispatches itself only |
| Dispatcher failure | Alert on repeated failure; silence alert after three intervals. The tick lease is created only after the ledger is written, so a rerun of the same tick is safe |
| Collector failure | Platform retry once, then the next eligible tick, up to the daily attempt limit. Markers prevent repeated requests |
| BigQuery outage | Landed batches wait in Cloud Storage. The next warehouse run loads them all. Collectors are unaffected |
| Source rate limit | Section 6: stop, record, open the breaker, back off |
| Hours of accumulated work | No catch-up for samples; period days recovered by the window; deferred work drains at the capped rate |
| Huge event day | Tier caps hold. Promotions displace within a tier; nothing raises the request budget |
| First run after a deployment | Treated as an ordinary tick. Markers and receipts make it a repeat of work already done or the first tick of new work |
| Armed-until time passes | The dispatcher refuses to start jobs and says so, whatever the scheduler does |

## 13. Idempotency

Unattended retries are safe because every step is safe to repeat:

- **Tick lease.** One create-only object per tick. A second dispatcher run for the
  same tick stops.
- **Ledger batch.** Named for the tick; landing it twice is refused and treated as
  already done.
- **Collector.** Marker lookup before each request; create-only batch; markers
  written only from a committed manifest. Worst case is one repeated request.
- **Loader.** Deterministic job id, receipt, recovery by job lookup. A batch loads
  once.
- **Late data.** Pending-rebuild record survives a failed dbt run.
- **Catalogue.** One pointer, replaced only on a matching generation.
- **Models.** Partition replacement over a lookback is the same result however many
  times it runs.

New in C2 and held to the same standard: the work order, the health record, the
daily counters and the control record. Counters are advisory upper bounds written
with a conditional replace; a lost update can only make the system stricter.

## 14. Source and credential gates

| Source | In C2 |
|---|---|
| Wikimedia pageviews | Active. Already cleared and proven |
| Steam (players, catalogue) | **Not activated.** Requires all seven conditions in the runbook, a replacement key if the earlier one is treated as exposed, registration identifying the application, the public privacy notice, United States-only storage, the Valve clarification request, and the secret-IAM review. Public and commercial use stay blocked separately |
| IGDB | **Not activated.** Requires a credential handling plan, the licensing gate for the fields used, and the secret-IAM review |
| Twitch, YouTube creator supply | Permission gated. Not in C2 |
| Reddit, TikTok, RAWG | Not used |

Code existing for a source is not permission to schedule it. A source is planned by
the dispatcher only when its reviewed policy clears the stage, and that is changed by
a reviewed commit.

## 15. Secret-IAM review

A separate, documentation-only checkpoint (C2-S), required before the first secret
version exists and not required for the Wikimedia-only activation. It must answer:

- who may add a version (today: the owner only; the deploy identity cannot);
- who may read a version (today: `ms-catalogue` for three, `ms-steam-players` for one,
  through bindings that exist with nothing behind them);
- how a job template names a version (an explicit number, never `latest`, already
  enforced by a Terraform test);
- the deploy identity's indirect paths (it can set IAM on a container and deploy a
  job as a reader), and whether a deny policy or an alert on secret IAM changes
  should cover them;
- rotation and revocation, step by step, including what running executions do;
- the guarantee that no value reaches a log, a run record, a landed batch or state.

No secret is added during planning or during this review.

## 16. Kill switches

In order of reach:

1. **Pause the scheduler.** One command, already documented. Nothing else starts
   work.
2. **Dispatcher control record.** One object under the dispatcher's `ops` prefix:
   `enabled`, `armed_until`, and per-source switches. The dispatcher reads it first
   and refuses to start anything when disabled, expired or unreadable. It fails
   closed.
3. **Deploy with `scheduler_paused = true`.** The variable's default.
4. **Source policy.** A reviewed commit that withdraws a source's clearance.

`armed_until` is what makes a soak bounded for real. A soak is armed for its length;
when the time passes, collection stops whether or not anyone remembers to pause.

## 17. Hardening

**Image.**

- *setuid `mount` and `umount`:* remove the setuid bits in the image build **before
  activation**. It is a two-line change, C2 needs a new image anyway, and it
  neutralises 32 of the 34 MONITOR rows.
- *Debian 13 base:* evaluate **after the first soak**, as its own reviewed image
  change. It removes both CRITICAL findings but changes every OS package, so it
  should not be mixed with the first automation proof.
- *Debian 12 OpenSSL fix:* nothing to schedule. When Debian publishes it the CI gate
  fails until the image is rebuilt, which is the intended behaviour.

**Deploy identity.** Narrow `roles/monitoring.editor` to alert-policy editing plus
read access to the channel, and `roles/logging.configWriter` to log-metric
permissions, in one owner-run bootstrap plan with the same lockout proof as before.
Recommended in C2-B alongside the infrastructure change. The BigQuery and storage
custom roles are not touched.

## 18. Start-up and image size

Known: both S3 executions waited over 90 seconds before their first log line, and the
image is about 1.1 GB uncompressed. Unknown: whether that wait is billed.

Measured in C2-C and C2-D, without spending executions only to measure:

1. For every execution: creation time, start time, first log line, exit, and
   `billable_instance_time`.
2. After the first soak: the Cloud Run lines on the billing export or invoice,
   compared with the sum of both.
3. Whether the wait shrinks when executions are frequent (a cached image).

Decision rule: if the wait is billed, or it makes a tick overrun, reduce the image,
most likely by giving collectors a build target without the dbt environment. If it is
not billed and ticks are hourly, leave it. Nothing is optimised before step 2.

## 19. Activation sequence

Dependencies run strictly downward. Nothing starts until the one above it is accepted
in writing.

```
C2-0 decisions ─► C2-A offline build ─► C2-B plan and apply (paused)
                                             │
                                             ▼
                              C2-C one supervised tick
                                             │
                                             ▼
                              C2-D six-hour armed soak
                                             │
                                             ▼
                              C2-E cost and reliability review
                                             │
                                             ▼
                              C2-F seven-day armed soak
                                             │
                                             ▼
                              C2-G private automation acceptance

C2-S secret-IAM review (documents only)  ── independent; gates any secret version
C2-H Debian 13 evaluation                ── after C2-D; its own plan
```

### C2-0. Decisions

| | |
|---|---|
| Objective | Settled: the six decisions are recorded in section 20 |
| Components | This document |
| Terraform | None |
| Source contact | None |
| Executions | 0 |
| Cost | None |
| Security | None |
| Tests | None |
| Live proof | None |
| STOP | Done |
| Rollback | Not applicable |

### C2-A. Offline build

| | |
|---|---|
| Objective | Build steady-state Wikimedia, the schedule and tiers, due selection, caps, the breaker, health records, the control record, the ledger columns and on-demand warehouse runs, all offline |
| Components | `jobs/dispatch.py`, `jobs/collect.py`, `jobs/targets.py`, `jobs/warehouse_job.py`, a new scheduling module, `warehouse/` models and selectors, the Dockerfile (setuid removal), `infra/prod` (drafted, not applied) |
| Terraform | None applied or planned. One schema file is changed in the repository (six ledger columns), because the loader and its tests read it; it reaches BigQuery only through a reviewed C2-B plan |
| Source contact | None |
| Executions | 0 |
| Cost | None |
| Security | Any new prefix grant is listed for review. No role is widened |
| Tests | Simulated ticks over synthetic schedules of 1,000 to 50,000 entities; work proportional to due; caps and deferral; breaker transitions; no catch-up after a gap; fail-closed control record; history fill only once; no full refresh from the routine command; every existing baseline, parity and resolver lock unchanged |
| Live proof | None |
| STOP | Report with test results and the drafted plan summary |
| Rollback | Revert the commits |

### C2-B. Plan and apply, still paused

| | |
|---|---|
| Objective | Deploy the C2 image and infrastructure with the scheduler paused; narrow the two deploy-identity roles |
| Components | Deploy workflow (plan, then apply of that plan); owner bootstrap plan for the role narrowing |
| Terraform | Prod: 7 jobs changed (image; `METASPIKE_WORK_ORDERS=required` on the collector), scheduler changed (hourly, still paused), 1 table changed in place (six columns appended), alert policies changed and added, 1 prefix grant added (the collector's read of its own orders), 1 quota added. Bootstrap: 2 custom roles added, 2 bindings added, 2 removed. No destroy of data. Also once, by the owner: fill markers for the 14 articles collected in C1 |
| Source contact | None |
| Executions | 0. S1-style smoke for any new prefix grant is a separate, approved step |
| Cost | A new image in the registry |
| Security | Lockout proof by one deploy-workflow plan after the bootstrap apply. Image scan must show 0 fixable HIGH or CRITICAL and no setuid `mount` |
| Tests | CI on the pushed commit; Terraform tests for the new assertions |
| Live proof | Post-apply audit: scheduler paused, never attempted; templates on the new digest; data unchanged |
| STOP | After the plan, for approval of its hash; again after the apply |
| Rollback | Re-apply the previous image digest through a reviewed plan; owner re-apply of the previous bootstrap configuration |

### C2-C. One supervised tick

| | |
|---|---|
| Objective | Run the unattended path once, by hand, end to end |
| Components | No change |
| Terraform | None |
| Source contact | **Yes: Wikimedia only**, 14 requests at most, 4-day window |
| Executions | 3 or 4: `ms-dispatch` once, `ms-wikimedia` started by it, `ms-dispatch` once more on the following tick, `ms-warehouse` started by it. Control record armed for two hours |
| Cost | Negligible; measured |
| Security | First real use of dispatcher-started jobs. Confirms the dispatcher's invoker bindings and nothing broader |
| Tests | None new |
| Live proof | Ledger rows with tier and state; one steady-state batch of about 56 rows; no 90-day refetch; warehouse run not flagged as late; dbt passes; a second dispatcher run for the same tick does nothing; start-up measurements recorded |
| STOP | Report, including every measurement in section 18 |
| Rollback | Control record disabled. The scheduler was never unpaused |

### C2-D. Six-hour armed soak

| | |
|---|---|
| Objective | First unattended operation, bounded in time by the system itself |
| Components | No change |
| Terraform | One applied plan: `scheduler_paused = false`. Pausing again is not a saved plan (see "Pausing again" below): it is the one-action `gcloud` pause, reconciled by a later reviewed plan |
| Source contact | Wikimedia only |
| Executions | About 6 dispatcher, 1 Wikimedia, 1 or 2 warehouse. Hard ceiling 15 |
| Cost | Negligible; measured |
| Security | The scheduler identity starts the dispatcher for the first time |
| Tests | None new |
| Live proof | Every tick dispatched exactly once; at most one collection; no alert fired; collection stops at `armed_until` with the scheduler still running, proving the bound; then the scheduler is paused again |
| STOP | Report after re-pausing |
| Rollback | Pause the scheduler (one command), at once. Then disable the control record. Terraform is brought back in line afterwards through the normal reviewed plan |

### C2-E. Cost and reliability review

| | |
|---|---|
| Objective | Replace every estimate in this plan with a measurement and decide whether to continue |
| Components | Documents only |
| Terraform | None |
| Source contact | None |
| Executions | 0 |
| Cost | The gate itself: a monthly projection for the seven-day configuration and for 1,000, 5,000 and 10,000 monitored games, from measured billable time, billed bytes, storage growth and billing lines |
| Security | None |
| Tests | None |
| Live proof | The measured model, the start-up finding and its decision |
| STOP | Explicit approval of the projection. Anything above $20 a month stops here |
| Rollback | Not applicable |

### C2-F. Seven-day armed soak

| | |
|---|---|
| Objective | A week of unattended operation including a weekly reconciliation run |
| Components | Only what C2-E decided |
| Terraform | The unpause plan again; any change from C2-E through its own reviewed plan first |
| Source contact | Wikimedia only |
| Executions | About 170 dispatcher, 7 to 10 Wikimedia, 8 to 14 warehouse. Ceiling 250 |
| Cost | Projected in C2-E; compared daily with the budget |
| Security | Daily read-only audit: secret versions 0, keys 0, IAM unchanged |
| Tests | None new |
| Live proof | Seven consecutive days present for all 14 articles; no duplicate logical rows in staging; no unexplained alert; no failed execution without a recorded recovery; cost within the projection |
| STOP | Report at `armed_until`, with the scheduler paused again |
| Rollback | Pause the scheduler; the control record expires on its own |

### C2-G. Private automation acceptance

| | |
|---|---|
| Objective | Decide whether the scheduler may run without an expiry |
| Components | Runbook and README updated to the accepted state |
| Terraform | The unpause plan, if accepted |
| Source contact | Wikimedia only |
| Executions | The accepted steady state |
| Cost | A monthly review against the measured model, and before any widening of scope |
| Security | The gates in sections 14 and 15 remain for every other source |
| Tests | None new |
| Live proof | The C2-F evidence |
| STOP | Written acceptance. C2 ends here |
| Rollback | Pause the scheduler |

### The scheduler-unpause gate, exactly

The scheduler is unpaused only by applying a reviewed plan whose sole change is
`paused: true -> false`, and only when all of these are true:

1. C2-A, C2-B and C2-C are accepted in writing.
2. The control record is armed with an expiry no later than the approved soak length.
3. The one-action pause command is written down, with its read-back, and the operator
   has confirmed it can be run. No saved re-pause plan is relied on.
4. Scheduler never attempted; secret versions 0; keys 0; IAM as last audited.
5. The measured projection for the soak is inside the cost policy.
6. Every policy in section 8 exists and the channel is the one proven in S4.
7. Only cleared sources have targets in the schedule.

For C2-F, add: C2-D and C2-E accepted. For C2-G, add: C2-F accepted.

### Pausing again

An earlier version of this plan called for a re-pause plan saved in advance. That
cannot work. A saved Terraform plan is tied to the state it was made against: one made
while the scheduler is still paused contains no change at all, and once the unpause is
applied the state has moved on and Terraform refuses the older plan as stale ("Saved
plan is stale"). Confirmed locally with Terraform 1.16.4.

So the stop is the command, which acts on the scheduler directly and depends on no
plan:

```
gcloud scheduler jobs pause ms-dispatch-15m --location=us-central1 --project=<PROJECT_ID>
gcloud scheduler jobs describe ms-dispatch-15m --location=us-central1 --project=<PROJECT_ID> --format='value(state)'
```

It is run at the end of a soak and at once on any anomaly, and the read-back must say
`PAUSED`. The control record is then disabled as a second barrier. Afterwards Terraform
still believes the scheduler is running; the next normal plan, made with
`scheduler_paused = true`, shows that one difference and is reviewed and applied like
any other. Until then the repository variable stays `true`, so no plan can unpause by
accident.

Unpausing also turns the dispatcher-silence alert on: it is defined as enabled exactly
while the scheduler is not paused, so an unpause plan changes two resources, the
scheduler and that one alert policy, and nothing else.

## 20. Decisions (C2-0, final)

1. **Wikimedia targets.** The 14 proven articles throughout C2. No curated production
   list is made to imitate scale; scale is proven offline at 1,000, 5,000, 10,000 and
   50,000 catalogued entities. Any larger real set needs its own reviewed, supervised
   experiment, and real expansion belongs to later catalogue work.
2. **Tiering.** The rule is built and is shadow only. It proposes T0 to T5 and causes
   no production escalation in C2. Temporary pins are allowed for supervised
   validation: explicit, expiring, auditable, and inert after expiry. The baseline
   change-point output stays shadow only.
3. **Tick.** Hourly while Wikimedia is the only source. The interval is configuration,
   so a later cleared source can justify 15 minutes.
4. **Billing stop.** None is built. The scheduler pause, the expiring control record,
   the per-source, per-tick and per-day caps, the Cloud Run timeout, the query cap and
   the alerts are the controls. The budget alert is an alert.
5. **BigQuery daily quota.** 10 GiB a day, as a temporary C2 ceiling (section 7).
6. **Branch protection.** The current GitHub and federation trust model is accepted
   for private C2 with Wikimedia only and no secret version. It is **not** approved
   for a secret-backed source or for public deployment: before the first Steam, IGDB
   or other secret-backed unattended source, or the final public deployment, the
   repository and deploy protection decision is reopened.

## 21. Projected cost for the Wikimedia-only steady state

An estimate from the two S3 executions and the published prices in the runbook. It is
replaced by C2-E.

| Item | Monthly volume | Against the free allowance |
|---|---|---|
| Dispatcher, hourly, billed one minute each | 720 executions, about 43,000 vCPU-seconds | 18% of 240,000 |
| Wikimedia collector | about 35 executions, 2,100 vCPU-seconds | under 1% |
| Warehouse | 30 to 60 executions, 2,000 to 4,000 vCPU-seconds | under 2% |
| Memory | about 25,000 GiB-seconds | 6% of 450,000 |
| BigQuery queries | 4 to 8 GB billed | under 1% of 1 TB |
| BigQuery and Cloud Storage data | megabytes | inside the allowances |
| Image registry | about 1.3 GB | about 0.8 GB over; around ten cents |
| Scheduler, logging, monitoring | one job; small volumes | inside the allowances |

Expected: well under $1 a month. Free allowances belong to the billing account, not
the project, and the one-minute minimum is assumed, not confirmed.

## 22. Excluded from C2

No public website, route, export or artifact. No change to `krishnachittella.dev` or
its Labs page. No public data. No source beyond those cleared in section 14. No claim
of release.

## 23. After C2

C2 delivers a private pipeline that keeps itself running for one source. Most of the
product is still ahead:

- broader source and catalogue ingestion, each behind its own policy gate;
- baseline state, parity and soak on real samples (Part D);
- event and lifecycle intelligence;
- adaptive prioritisation proven at scale on real sampled sources;
- opportunity scoring, confidence, and source agreement;
- "why now" explanations;
- creator-supply gap, where permitted;
- point-in-time recommendation snapshots, backtesting and accountability;
- an upcoming-opportunity radar;
- public artifact generation;
- the frontend: explorer, search and game pages;
- methodology, transparency and privacy pages;
- integration with the website, and a final launch soak.

There is one public release, at the end of that list.

## 24. C2-A: what was built and measured offline

Built in `src/metaspike/scheduling/` and wired into the dispatcher, the Wikimedia
collector and the warehouse job. Nothing is deployed; no cloud resource, source or
scheduler was touched.

| Module | What it is |
|---|---|
| `config` | Per-source and per-tick limits, in one place |
| `tiers` | The tiers, their intervals and caps, and the shadow rule `tiering_rules_v0` |
| `schedule` | The versioned, partitioned schedule and which partitions a tick may read |
| `control` | The control record and its fail-closed reading |
| `health` | Source health and the circuit breaker, as pure transitions |
| `workorder` | Immutable orders, the pointer, claiming and receipts |
| `planner` | Bounded planning for one source on one tick |
| `tick` | One tick, end to end, against the operations bucket |
| `projection` | The cost arithmetic, with measured and assumed inputs kept apart |
| `simulation` | A day of real ticks over a synthetic catalogue |

### Layout in the operations bucket

All under the dispatcher's own prefix, all read by exact name:

```
dispatch/control.json                         the owner's switch
dispatch/schedule/CURRENT                     pointer to the schedule in force
dispatch/schedule/{version}/manifest.json     counts and the partition index
dispatch/schedule/{version}/{partition}.json  the entries of one partition
dispatch/plans/{tick}.json                    the tick's plan, written once
dispatch/ticks/{tick}.json                    the tick's lease
dispatch/orders/{source}/{order_id}.json      a work order, written once
dispatch/orders/{source}/CURRENT              pointer to the latest order
dispatch/health/{source}.json                 health and breaker state
dispatch/backlog/{source}.json                deferred work, bounded
dispatch/counters/{date}.json                 requests and executions today
dispatch/warehouse.json                       whether a warehouse run is owed
{source}/orders/{order_id}.json               the collector's receipt
{source}/bootstrap/{hash}.json                history filled for one target
```

The one access this adds, for C2-B: read for a collector under
`dispatch/orders/{its source}/`. The dispatcher already holds everything it needs.

### Scale simulation

The simulation runs the real tick for a simulated day at an hourly tick. Two sources
exist only in it: `sim_sampled`, a stand-in sampled source that exercises the tiers
(its request ceiling is a simulation value, not an approved budget), and
`sim_uncleared`, which is never cleared. A typical catalogue has 30 percent of its
entities monitored, of which 80 percent have an article and 60 percent a sampled id.

Quiet day:

| Catalogued | Monitored | Eligible targets | High frequency | Entries read, worst tick | Objects read, worst tick | Ledger rows | Executions | Requests | Peak backlog | Slowest tick | Peak memory |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1,000 | 300 | 420 | 8 | 273 | 27 | 765 | 48 | 765 | 0 | 0.02 s | 1 MB |
| 5,000 | 1,500 | 2,100 | 39 | 779 | 28 | 3,827 | 50 | 3,823 | 4 | 0.12 s | 5 MB |
| 10,000 | 3,000 | 4,200 | 72 | 941 | 29 | 7,347 | 52 | 7,291 | 23 | 0.20 s | 10 MB |
| 50,000 | 15,000 | 21,000 | 347 | 2,311 | 29 | 44,507 | 64 | 24,400 | 5,000 | 1.09 s | 56 MB |
| 50,000, small eligible subset | 500 | 700 | 9 | 445 | 28 | 1,117 | 48 | 1,117 | 0 | 0.03 s | 2 MB |

What the scenarios showed, at every size:

- **Breadth is not cost.** From 1,000 to 50,000 catalogued the objects a tick reads go
  from 27 to 29, and the schedule entries it reads grow about eightfold, not fiftyfold.
  A 50,000 catalogue with 700 eligible targets behaves exactly like a 1,000 catalogue
  with the same 700: same requests, executions, ledger rows and projected cost.
- **Limits hold.** No tick started more than 3 jobs (the cap is 6). Requests stopped at
  the sources' ceilings: 10,000 for Wikimedia, 600 per execution for the stand-in.
- **Many events at once** fill the tiers to exactly 25, 75, 300 and 1,000 and start not
  one more job than a quiet day.
- **An outage** opens the breaker after three failed executions; work is recorded as
  skipped and no backlog builds. **A rate limit** opens it at once, and a single probe
  closes it after the cool-down.
- **Six hours offline** is followed by one ordinary tick: at 50,000 it started 3 jobs
  and planned 1,193 targets, two executions' worth. Nothing was made up for the gap.
- **Nothing was ever listed**, and the uncleared source got no ledger row, no order and
  no job.
- **At 50,000 the limits bind.** With these default tiers and one execution per source
  per tick, about 6,600 pieces of due work a day expired unsampled and the backlog sat
  at its bound of 5,000. That is the design doing what it should, cost stays bounded
  and the shortfall is recorded, but it also says these caps and cohort sizes are not
  yet a tuned policy for that scale. Tuning them is later work, on measured data.

### Projected cost

From `projection`, using list prices. Measured inputs: the warehouse run's duration and
billed bytes, and row sizes, from S3. Assumed inputs, to be replaced in C2-E: a
one-minute minimum bill per execution, half a second per source request (S3 measured
about a tenth of that), and the size of a period-only or sample-only build.

| Configuration | Without allowances | With allowances | Verdict |
|---|---|---|---|
| **C2 as planned: 14 articles, hourly tick, one build a day** | **$1.06** | **$0.09** | within the preferred range |
| 1,000 catalogued, quiet, with the stand-in sampled source | $3.22 | $0.09 | within the preferred range |
| 5,000 | $3.56 | $0.09 | within the preferred range |
| 10,000 | $4.61 | $0.09 | within the preferred range |
| 50,000 | $10.01 | $4.37 | acceptable with scrutiny |
| 50,000 at a 15-minute tick (illustrative only) | $13.77 | $7.38 | acceptable with scrutiny |

The figure without allowances is the one to decide on: allowances belong to the
billing account. Every row with the stand-in source describes a configuration that is
not approved and cannot be switched on; it is there to show the shape of the cost, and
that cost follows what is due and what the limits allow, not the size of the catalogue.
Any configuration projected above $20 is reported as not activation-ready.

In the planned configuration the dispatcher is most of the compute: 720 one-minute
executions a month, if the one-minute minimum applies. BigQuery is about 4 GB billed a
month.

### Two things to carry into C2-B

- **Fill markers for the 14 articles.** The C1 collection filled their history before
  fill markers existed. Unless the owner writes those 14 markers once, the first C2
  tick fetches 90 days again. The function that writes them exists and is tested;
  running it against the real bucket is a C2-B step.
- **The quota resource** is beta-only in Terraform (section 7).

## 25. C2-C.1: request budgets are outbound attempts

Found in review after the supervised tick, and fixed before any unattended soak. The
control record capped the day at 14 requests; the order issued under it allowed 22;
the day's counter advanced by the 14 targets; and retries were sent below the layer
that counted. With a transient failure the attempts on the wire could have passed the
cap unnoticed. In C2-C itself none did: 14 were sent, with no retry.

Now every budget counts physical attempts, enforced where they are sent; an order's
budget is capped by what is left of the day and reserved in full when issued; and
running out is recorded as our own backpressure, never as the source refusing. The
details are in the runbook under "A request is an outbound attempt".

One effect on the scale simulation in section 24: because an order now reserves its
retry room, a source at its daily ceiling is asked for fewer targets than the ceiling.
At 50,000 catalogued entities the simulated Wikimedia day reserves all 10,000 attempts
and asks for about 6,700 targets. The bound holds more strictly, not less; the other
figures in that section are unchanged in kind and were not re-derived.

This gates C2-D. The soak's own request cap, and so whether it carries retry room, is
chosen when its control record is reviewed.

## 26. C2-D closeout: functional pass, execution ceiling failed

The first unattended soak ran on 2026-10-09. The ruling on it is two results, and they
are kept as two. It is not a clean pass and must not be described later as one.

| | Result |
|---|---|
| Source safety, fail-closed expiry, collection, warehouse, request budget, security, unattended behaviour | **PASS** |
| Execution-count ceiling | **FAIL, exceeded by five** |

**What happened.** The scheduler fired by itself every hour from 03:00 UTC. The 03:00
tick collected 14 articles with 14 outbound attempts of a budget of 22 and no retry,
landing 56 rows. The 04:00 tick started the warehouse, which loaded 8 batches (76 rows)
and passed dbt with 28 of 28. The 05:00 to 08:00 ticks had nothing due. The control
record expired at 08:30.

**The ceiling.** The checkpoint allowed 15 new executions on a baseline of 14, a total
of 29. The actual total is 34. Cleanup was recommended by about 13:00 UTC and happened
at 20:43, roughly eight hours late, and the hourly scheduler went on starting the
dispatcher after the control record had expired. The five executions beyond the ceiling
are the dispatchers of 16:00, 17:00, 18:00, 19:00 and 20:00.

**Impact.** None on any source or child job. Twelve consecutive dispatchers, 09:00
through 20:00, refused with `control_expired`: no child started, no source request, no
work order, no ledger row, no movement in the day's budget. Each did land its own
run-log record, which is how the refusals are known.

**The lesson.** A control record's expiry stops source work. It does not stop the
scheduler. An unattended soak therefore keeps spending one dispatcher execution an hour
until a person pauses it.

**Hard gate before C2-F.** A longer unattended soak may not start until there is a
reviewed, bounded way to prevent indefinite hourly dispatcher executions after the
control record expires. No solution is chosen here. Whatever is chosen must keep source
safety fail-closed, must not depend on the owner's machine, must leave no indefinite
scheduler execution after the finite soak window, must be least-privilege (no broad
Cloud Scheduler administration), deterministic, auditable and cheap.

C2-E, the review of what the soak measured, may proceed once Terraform is reconciled
with the paused scheduler. C2-F is blocked on the gate above.

**Left pending on purpose.** The run logs of the sixteen dispatchers from 05:00 to
20:00 (32 rows) are landed and not yet loaded. They are durable operational history.
They are not to be deleted or rewritten, and no warehouse run is made just for them:
the next approved warehouse run loads them by the existing mechanism (its routine
window covers three days of landing folders; a later run needs the wider window the
job already supports).


## 27. C2-E: measured cost and reliability review

Read-only review made on 2026-10-10 from the execution records, logs, Scheduler logs,
BigQuery job statistics and bucket contents left by C1, C2-C and C2-D. Nothing was
run, changed, unpaused or armed, and no source was called. It replaces the estimates in
sections 21 and 24; those sections are left as they were written.

C2-D's ruling is unchanged by anything below: functional pass, execution ceiling failed
by five (34 against 29).

### Where the start-up delay is

Every execution has four clocks: created, execution start, container (task) start, and
the application's first log line. C2-D, seconds:

| Dispatcher, 18 executions | min | median | p90 | max |
|---|---|---|---|---|
| On the hour to created (Scheduler delivery) | 1.1 | 6.7 | 42 | 52.3 |
| Created to execution start | 9.5 | 16.9 | 20.9 | 51.9 |
| **Created to container start** | **37.0** | **128.2** | **185.2** | **244.3** |
| Container start to first log line | 2.1 | 4.0 | 9.9 | 15.0 |
| First to last log line (the work) | 0.3 | 0.4 | 1.3 | 2.4 |
| Container start to exit | 5.5 | 8.9 | 12.1 | 18.2 |
| Created to completed (lifetime) | 77.1 | 140.0 | 194.8 | 264.8 |

| One execution each | Created to container start | Start to first log | Work | Container | Lifetime |
|---|---|---|---|---|---|
| Wikimedia collector | 129.3 | 3.1 | 4.5 | 9.5 | 141.4 |
| Warehouse | 179.0 | 3.9 | 109.9 | 118.9 | 301.2 |

Across all 34 executions since C1 the created-to-container-start time is 37 to 245
seconds, median 134, p90 208. All 20 soak executions succeeded on the first attempt
with no task retry. With one collector and one warehouse execution there is no
distribution to report for those two.

**Finding.** The one to four and a half minutes is spent before the container starts.
It is not application initialisation (two to fifteen seconds from container start to
first log, which includes starting Python), not logging delay (log entries were
received a median 0.18 s and at most 0.34 s after they were written), and not the
recorded image import (1 to 35 s, finished long before). It is Cloud Run's own
provisioning between accepting the execution and starting the container. Whether that
is instance scheduling or fetching the 226 MB image into the second-generation sandbox
**cannot be distinguished with current telemetry**: the platform reports one "started"
time and nothing in between. No runtime setting was changed.

The practical effect: a tick's work begins two to four minutes after the hour, and a
child started by it another two to three minutes later. That is harmless for daily
period data at an hourly tick. It would matter for a 15-minute tick.

### What C2-D proved about reliability

| | Evidence |
|---|---|
| Tick delivery | 18 of 18 hourly ticks delivered, 03:00 to 20:00 UTC, none missed, none doubled |
| Scheduler retries | 0. 18 attempts started, 18 finished with HTTP 200 |
| Dispatcher | 18 of 18 succeeded; 6 ran a tick, 12 refused with `control_expired` |
| Child starts | 2 of 2 (collector at 03:00, warehouse at 04:00); 0 started after expiry |
| Work order | 1 written, 14 targets, budget 22; honoured by the collector; none after expiry |
| Receipt folding | Folded by the next tick: health shows success, no pending order, empty backlog |
| Completion markers | 14 of 14 targets completed; nothing refetched by the later ticks |
| Request budget | 22 reserved, 14 physical attempts; the day counter holds the reservation |
| Retries | 0 |
| Breaker and health | Stayed closed and healthy; never exercised by a real failure |
| Control expiry | Refusals from the first tick after 08:30; 12 consecutive, fail-closed |
| Warehouse idempotence | 8 batches loaded, 11 recognised as already loaded, 0 incomplete |
| dbt | PASS=28, WARN 0, ERROR 0 |
| BigQuery loads | 10 of 10 load jobs succeeded; 0 failed jobs on the day |
| Source availability | Wikimedia answered 14 of 14 |
| Monitoring | See below |

Wikimedia, counted: targets planned 14, attempted 14, collected 14; physical attempts
14; retries 0; HTTP or source errors 0; 429s 0; missing targets 0; duplicates 0;
unexpected refetches 0.

What it did **not** prove, because it never happened: a failed execution and its
recovery, a breaker opening on a real source, a missed or late tick, a rate limit, a
weekly reconciliation run, more than one collection day in a row. One collection and
one build are a demonstration, not a rate.

Monitoring behaved as built and showed two weaknesses. The silence alert fired at
launch (it was enabled before the first tick could succeed) and again after the pause;
both were artifacts of a finite soak, not faults. The execution-count alert has a
threshold of 60 a day, so it could not see a ceiling of 29 being passed; the ceiling
was a checkpoint rule with nothing enforcing it. Both are entry gates for C2-F below.

### What C2-D cost

There is no billing export, so no invoice line can be read. Durations, bytes and counts
are measured; dollars are list prices applied to them.

| Item | Measured | Cost at list price | After allowances |
|---|---|---|---|
| Cloud Run, 20 executions | 303 s of container time; 3,109 s of execution lifetime | $0.006 if container time is billed; $0.02 with a one-minute minimum; $0.06 if the whole lifetime is billed | $0 |
| Cloud Scheduler | 1 job, 18 attempts | $0.003 for the day (one job is $0.10 a month) | $0 |
| BigQuery queries | 220 MiB billed by the warehouse, 140 MiB by the owner's audit queries | $0.002 | $0 |
| BigQuery storage | 0.5 MB | negligible | $0 |
| Cloud Storage | 0.7 MB landed, 0.07 MB operational, a few hundred operations | negligible | $0 |
| Artifact Registry | 11 images, at most 2.1 GB before shared layers | at most $0.005 for the day | same |
| Logging | 1.3 MB ingested on the day | negligible | $0 |
| Monitoring | 8 alert policies, built-in and log-based metrics | not measured; see below | unknown |

C2-D cost at most about seven cents at list price, and most likely nothing. The five
executions beyond the ceiling cost between a tenth of a cent and two cents.

**Which billing rule applies is still not settled.** The billable-time metric shows
181 s for the 18 dispatchers, which matches container time (175 s) and neither a
one-minute minimum nor lifetime billing. But the same metric shows 0.1 s for a
collector that ran 9.5 s and 72 s for a warehouse run of 119 s, so it is incomplete for
short jobs and cannot be treated as authoritative. Only invoice lines can settle it.

Against the earlier model:

| | Earlier model | Measured |
|---|---|---|
| Dispatcher billed time | 60 s assumed | 9 s container, 140 s lifetime |
| Warehouse run | 65 s | 119 s container (63, 80, 110 over three runs), 301 s lifetime |
| Daily build, billed | 136 MB (130 MiB) | 220 MiB, the same on both C2 days |
| Seconds per source request | 0.5 assumed | about 0.3 (14 requests inside 4.5 s) |
| Registry | 1.4 GB | at most 2.1 GB |

The build bills more because C2 added the ledger models: 22 billing minimums against
13. The warehouse run has grown with each run for reasons three samples cannot
explain. Neither moves the total by a cent. The one discrepancy that matters is that
an execution exists for about two minutes longer than it works, and it is not known
whether that time is billed.

One item was not checked: Google announced per-condition charges for alert policies.
If they are in force, eight single-condition policies are of the order of a dollar a
month. This needs reading on the pricing page before C2-F, not assuming.

### Projections

Three billing readings are carried, because the evidence does not choose between them:
**low**, container time only; **middle**, a one-minute minimum; **conservative**, the
whole execution lifetime, taken as the work plus 208 s (the p90 start-up over all 34
executions). Decisions are made on the conservative figure without allowances. All
include $0.16 a month for the registry and exclude the unverified alerting charge.

| Configuration | Low | Middle | Conservative | Conservative, after allowances | Verdict on the conservative figure |
|---|---|---|---|---|---|
| A. 14 articles, hourly tick, one build a day, per month | $0.45 | $1.13 | **$3.54** | $0.16 | below the preferred range |
| B. Seven-day C2-F soak (182 executions) | $0.07 | $0.23 | **$0.79** | $0 | negligible |
| C. 1,000 catalogued, 300 monitored | $3.09 | $4.28 | **$11.77** | $5.92 | acceptable with scrutiny |
| C. 5,000 catalogued, 1,500 monitored | $4.03 | $4.69 | **$12.95** | $6.98 | acceptable with scrutiny |
| C. 10,000 catalogued, 3,000 monitored | $5.10 | $5.76 | **$14.26** | $8.16 | acceptable with scrutiny |
| C. 50,000 catalogued, 15,000 monitored | $10.90 | $11.55 | **$21.47** | $14.53 | **ABOVE THE $20 REVIEW BOUNDARY. NOT APPROVED** |

The C rows are the quiet-day simulations of section 24: the adaptive tiers decide what
is due, not every entity is fetched every hour, and they include the stand-in sampled
source, which is not approved and cannot be switched on. They show the shape of the
cost, not a configuration anyone may enable.

What the measurement changes: under the conservative reading cost follows the **number
of executions**, not the number of requests or the size of the catalogue. From 1,000 to
10,000 catalogued the requests grow tenfold and the bill by a fifth. The simulated
configuration starts 72 to 88 executions a day, 23 of them warehouse builds, and each
carries about three and a half minutes of start-up. If that reading is the true one,
the levers are fewer builds a day and fewer, larger collector executions; nothing is
decided here. If the low reading is true, every row but the last is in or below the
preferred range and the last is at its edge.

The 50,000 row is not approved by this document under any reading, and no row with a
sampled source is.

### BigQuery

Verified from the job records: on 2026-10-09 the warehouse identity ran 70 jobs, 46
top-level (36 queries, 10 loads) and 24 children, billed 220 MiB and used 59,775
slot-milliseconds. The day before was identical in count and bytes.

- It processed 0.2 MiB. The 220 MiB is 13 statements billed at the 10 MiB minimum for
  each table they read, 22 minimums in all. Billed bytes follow the **number of
  statements**, not the data, and will until a table passes 10 MiB (the largest is
  0.5 MiB at 1,372 rows).
- The three incremental staging models account for 120 MiB (a 30 MiB script and a
  10 MiB statement each), the two daily marts for 40 MiB, five tests for 60 MiB.
- Current cadence: 220 MiB a day, about 1.5 GiB over seven days, 6.4 GiB a month:
  $0.04 at list price, under one percent of the free terabyte.
- The 10 GiB daily quota is about 2 percent used by the warehouse, 3.5 percent with
  the owner's audit queries. It is still appropriately conservative and is unchanged.
- Scans are not a scaling concern at any size simulated: models read a three- or
  seven-day window of partitions. Frequency is. Twenty-three builds a day would bill
  about 4 GiB a day, 40 percent of the quota, though still about 120 GiB a month and
  inside the free terabyte. Build frequency is the thing to review before any sampled
  source, for the quota and for the compute above alike.

### The 32 pending run-log rows

Still there: 36 dispatcher run rows landed for 2026-10-09, 4 loaded at 04:04 that day,
32 not loaded (the dispatchers of 05:00 to 20:00). No warehouse run was made for them.

The loader walks the landing folders for today and the two days before. The rows sit
in the folder for 2026-10-09, so a normal warehouse run on 9, 10 or 11 October UTC
loads them without anyone doing anything. From 12 October the ordinary window no
longer reaches that folder, and the run needs `METASPIKE_LOAD_DAYS` set wide enough to
reach back to it. That is an execution-time setting on a supervised run; a
dispatcher-started run uses three. The late-data record then rebuilds the affected
models from that day. Since C2-F cannot start before its hardening is built and
reviewed, the wider window is the likely path, and the first warehouse run of C2-F's
preparation should be planned with it.

### The expiry stop (C2-F hard gate): options

The problem: the control record's expiry stops work, and Cloud Scheduler goes on
starting the dispatcher every hour for ever.

**Option 1. A finite schedule.** The scheduler's cron expression names the soak's days,
for example `0 * 12-18 10 *`, set by the same reviewed Terraform plan that unpauses it.
After the last hour of the last day there is no next run until the same dates a year
later.

- Least privilege: no new permission, identity or code.
- Determinism: complete. The number of ticks is fixed when the plan is reviewed.
- Owner's machine: not needed to stop. Needed only for the later tidy-up.
- Drift: none. Terraform owns the schedule and nothing else writes it.
- Re-arm: a new reviewed plan with new dates, and a new control record.
- Failure modes: the window must sit inside one calendar month, or the expression
  matches unwanted days in the other month; a window left in place repeats a year
  later, by which time the control record has expired and every tick refuses. During
  an API failure nothing has to happen for the stop to hold.
- Risk of leaving production disabled: none, it is a soak mechanism. Steady state
  returns to `0 * * * *` by its own reviewed plan.
- Audit: the plan, its hash and the Scheduler attempt log.
- Cost and complexity: none; two variables and a Terraform test.
- Weakness: it is a second clock beside `armed_until`. If the two disagree the result
  is still fail-closed, and refusals are bounded by the window's end.

**Option 2. The dispatcher pauses its own trigger.** On `control_expired` the
dispatcher calls the Scheduler API to pause the job.

- Least privilege: a custom role with `cloudscheduler.jobs.pause` alone, but Cloud
  Scheduler has no per-job IAM, so it is granted on the project. Misuse can only pause.
- Determinism: one refusal, then stopped. If the call fails the next tick tries again.
- Drift: yes. Terraform says "not paused" and the live job is paused; the next
  unrelated apply would start it again unless the plan is read carefully. This is the
  serious cost of the option.
- Re-arm: reviewed unpause plan, as now.
- Risk of leaving production disabled: real once steady state exists; an expired
  record silently stops collection until someone notices.
- Complexity: new code on the control path, a new permission, a new failure branch.

**Option 3. A separate one-shot stopper.** A second Scheduler job, timed at the expiry,
calls the Scheduler API to pause the first, as a dedicated identity holding only the
pause permission.

- Independent of the dispatcher's health, and no application code.
- Same project-wide pause grant and the same Terraform drift as option 2, plus a new
  service account, a second job and a second schedule to keep in step with the first.
- If its one call fails beyond its retries, the scheduler runs on.

**Option 4. A self-renewing chain.** No recurring schedule: each tick enqueues the next
only while the control record is valid.

- Stops by construction, but any single failure ends collection for good, it needs a
  queue and an enqueue permission, and it replaces the proven trigger. Rejected.

**Recommendation: option 1.** It is the only one that adds no permission, no identity,
no code and no drift, and the only one whose stop needs nothing to succeed at the
moment of stopping. It meets every condition of the gate in section 26. Its limits
(one calendar month, day granularity, a second clock) are acceptable for a seven-day
soak and are checkable in a Terraform test. Options 2 and 3 need a project-wide
Scheduler permission and make Terraform disagree with the live system by design;
neither is justified while option 1 exists. What stops an indefinite, steady-state
schedule when a control record lapses is a different question and belongs to C2-G.

Nothing is implemented.

### The Docker Hub dependency: options

The problem: every apply rebuilds the image in verification, the build pulls the pinned
Python base anonymously from Docker Hub, and a rate limit there twice blocked a
one-line change that had nothing to do with the image.

| Option | Security | Reproducibility | CI complexity | External dependency | Cost | Weakens verification |
|---|---|---|---|---|---|---|
| A. Skip the image rebuild for an infrastructure-only apply | The scan no longer runs at apply time | Unaffected | Low | Removed for those applies only | None | **Yes**: a gate is skipped |
| B. Take the same digest from another registry, Docker Hub as fallback | Unchanged: the digest in the Dockerfile is the trust anchor, the registry is transport | Unchanged, byte-identical | Low: a build argument for the registry and a fallback | Google's public mirror first, Docker Hub second | None | No |
| C. A private copy of the base in Artifact Registry | Unchanged, and the copy is under our retention | Unchanged | Higher: the verification job has no cloud identity by design, so the base must be handed to it by a job that has | None at build time | A few cents | No, but it brings cloud credentials nearer to the test job |
| D. Authenticated Docker Hub pulls | Adds a long-lived secret to a repository that has none | Unchanged | Low | Still Docker Hub | None | No, but breaks the zero-secret position |
| E. Retry with backoff | Unchanged | Unchanged | Trivial | Unchanged | None | No; does not remove the dependency |

Checked on 2026-10-10 without credentials: Google's public mirror serves the pinned
base digest (`mirror.gcr.io/library/python@sha256:54c85f3c…8cb3`, HTTP 200).

**Recommendation: option B**, with the mirror tried first and Docker Hub kept as the
fallback. It needs no secret, no permission and no new resource, weakens no gate, and
helps ordinary push CI as well as applies. Its residual risk is that the mirror is a
cache and may one day not hold the digest, in which case the build is where it is
today. If that proves insufficient, option C is the stronger second step. Option A is
not recommended while a fix exists that skips nothing; option D is rejected. The build
also depends on the uv image, Debian's package servers, the Python package index and
the scanner's database; none has failed, and all remain.

Nothing is implemented.

### C2-F entry gates

All must be true, with evidence, before a seven-day soak is launched.

1. The expiry stop is implemented, reviewed and tested: with the scheduler unpaused,
   the planned schedule is finite, and a test fails if it is not.
2. The unpause plan shows a number of ticks that is known in advance, and the total
   execution ceiling for the soak is derived from it, not chosen separately.
3. After the window no dispatcher execution can occur without a new reviewed plan:
   zero executions after expiry, not "few".
4. Source fail-closed behaviour is unchanged: the control record still gates every
   tick, and its tests still pass.
5. Request budgets are unchanged: outbound attempts are counted and reserved as in
   C2-C.1.
6. The sampled collector stays inactive; it is not wired to a request budget and no
   sampled source is enabled.
7. The Scheduler configuration (schedule, time zone, retry settings, target, identity)
   is reviewed in the plan output.
8. Monitoring fits a finite soak: the silence alert does not fire before the first
   tick or after the planned end, and the execution-count alert's threshold is
   consistent with the soak's ceiling.
9. The alerting charge is read from the pricing page and the projection updated; the
   conservative projection for the soak stays under $1 and the monthly figure under
   $10.
10. No saved or pending Terraform plan contains a destroy, and state matches live.
11. Security is clean: 0 secret versions, 0 user-managed keys, no IAM or WIF change
    other than what a reviewed plan made, the deployed image approved.
12. The deployment path no longer fails on a Docker Hub rate limit, or the owner
    accepts that risk for the soak explicitly.
13. The 32 pending run-log rows are accounted for in the plan for the first warehouse
    run.
14. Wikimedia remains the only source, 14 articles, tiering shadow only.

**C2-F: BLOCKED** pending the expiry-stop implementation (gates 1 to 3) and the
monitoring adjustment (gate 8). No launch time is chosen.
