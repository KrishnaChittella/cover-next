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
| Source requests per execution | targets × 1.5, hard stop at 1,000 |
| Source requests per day, Wikimedia | 10,000 |
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
| Source freshness | **New, ALERT** | No successful `ms-wikimedia` execution in 36 hours |
| Executions per day | **New, ALERT** | Above the daily budget in section 6 |
| Landing bucket size | **New, ALERT** | Above 5 GB |

Every alert auto-closes when its condition clears. That is seven active policies and
one channel. None should fire in normal operation.

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
| Terraform | One applied plan: `scheduler_paused = false`. A second, prepared in advance, sets it back |
| Source contact | Wikimedia only |
| Executions | About 6 dispatcher, 1 Wikimedia, 1 or 2 warehouse. Hard ceiling 15 |
| Cost | Negligible; measured |
| Security | The scheduler identity starts the dispatcher for the first time |
| Tests | None new |
| Live proof | Every tick dispatched exactly once; at most one collection; no alert fired; collection stops at `armed_until` with the scheduler still running, proving the bound; then the scheduler is paused again |
| STOP | Report after re-pausing |
| Rollback | Pause the scheduler (one command), then the prepared plan |

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
3. The prepared re-pause plan exists and has been reviewed.
4. Scheduler never attempted; secret versions 0; keys 0; IAM as last audited.
5. The measured projection for the soak is inside the cost policy.
6. Every policy in section 8 exists and the channel is the one proven in S4.
7. Only cleared sources have targets in the schedule.

For C2-F, add: C2-D and C2-E accepted. For C2-G, add: C2-F accepted.

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

