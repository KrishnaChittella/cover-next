# Creator-Opportunity Intelligence v1 (U6.3)

**Offline, private, on the product branch only.** This is the first layer that reads
all of MetaSpike's evidence together and asks whether a game is worth a video now. It
is a transparent rubric, not a score, and on today's real data it recommends nothing.

## Candidate, not recommendation

An **assessment** applies `creator_opportunity_rules_v1` to one game's evidence reading
(U6.0–U6.2) at a moment. It reaches one of three readinesses:

| Readiness | Rule (counted evidence only) |
|---|---|
| `evidence_supported_candidate` | a demand family moving up, AND a confirmed timely angle, AND a complete creator-coverage sample, AND no disagreement between demand families |
| `provisional_watchlist` | a demand family moving up, OR a timely event, without everything above |
| `insufficient_evidence` | nothing counted says "now" |

A candidate is the strongest thing the rubric can say, and it is still a candidate: no
threshold in it has been validated against outcomes. A watchlist entry is a reason to
look. Nothing here is a recommendation service.

**What counts.** Only **verified** evidence: a recording whose content matches a pinned
digest (U6.0.1). Synthetic and unverified evidence is shown, labelled, and kept out.
Beside every real readiness is a **simulated** one, computed by the same rubric with
synthetic and unverified evidence counted as if real. It exists to show the rubric
working on invented data and is never a recommendation.

## The rubric's questions

| Question | Answer, from counted evidence |
|---|---|
| Moving up | A demand family available and current, robust z ≥ 3.5 and log lift > 0 against its own history. 3.5 is score v0's own outlier threshold, reused, **UNVALIDATED** here. |
| Moving down | The same, downward. A caveat, never a reason. |
| Disagreement | One family up while another is down. Blocks a candidate. |
| Timely event | Event intelligence available, an event upcoming within the radar's horizon or active. |
| Angle | A timely community candidate angle (two or more supported posts within **7 days**, UNVALIDATED), or a timely event. |
| Coverage known | A creator-supply sample that ran to its end. Total competition stays unknown. |

Raw units are never combined: each family is read against its own history by the
frozen feature engine, and the rubric only asks which way each moved. Families count
once each, and a stale family says nothing about now.

## Order

Lexicographic, every step stated, no numbers mixed across sources:

1. readiness; 2. more families moving up; 3. no disagreement before disagreement;
4. a timely event; 5. a timely angle; 6. a complete coverage sample; 7. more sources
answering; 8. canonical id.

Each entry says, in words, why it is above the next. When two games differ only in
their id, the explanation says "indistinguishable on the evidence; ordered by canonical
id only, which means nothing". No numerical margin is invented.

## Why now, angles, caveats

**Why now** lists only statements backed by counted evidence: a family moving up (with
its lift and z), an upcoming or active event (type, date, precision, never its
publisher's title), recent discussion. Everything else that points somewhere is under
**not counted**, tagged SYNTHETIC or UNVERIFIED. An unverified event never explains now.

**Angles** come from the community reading's candidate angles (U6.2) and from timely
events. Each says what it would address, what supports it (post or event ids, kept for
review), whether it is timely and why, whether it is confirmed, and the assumptions it
rests on — including that a video would be watched (nothing measures that) and that
existing videos do not already cover it (total competition is unknown). No title is
written, nothing is copied from posts or announcements. With none, the output says
"angle not yet established".

**Caveats** name downward movements, disagreement, staleness, incomplete samples, that
creator competition is unknown, and every synthetic or unverified source. **Missing**
repeats the evidence reading's gaps. **Uncertainty** says how many verified sources
answered and that nothing is calibrated; there is no probability.

Missing creator supply is never low competition; an empty video sample is never low
competition; a quiet community is never low interest. These are tested.

## Time and provenance

Everything is read from the evidence reading at `as_of`, so it inherits its guarantees:
publication lag, bindings valid then, samples and pages retrieved by then, posts and
videos created by then, events observed by then, and no later counter. Tested: an
earlier assessment of the same stores has no later events, climb, searches or posts; a
later rebinding does not change an earlier assessment; repeated runs are identical.

## The Reddit collection guard (closing U6.2's finding)

U6.2's collector did not itself check Reddit's policy; the evidence reader did. Now the
adapter takes one of two things: `RedditReplay`, which answers from recorded files and
holds no HTTP client or transport, so it cannot reach a network; or a network-capable
`httpx.Client`, which it accepts **only** if Reddit's source policy clears collection at
a stated stage — checked when the adapter is built and again before every request.
Approval is pending, so a network-capable client is refused at every stage before any
request. No label, file name or flag can lift it; only the policy can, and it was not
changed. Against the U6.2 adapter, the same ordinary client was accepted and its request
sent; that is the regression the new test pins.

## Demonstration

Synthetic (every source invented except identity, all labelled):

```
uv run metaspike evidence --as-of 2026-10-20T12:00:00+00:00 --synthetic-scenario \
    --opportunities --game rust --game dota-2 --game counter-strike-2 \
    --game elden-ring --game no-mans-sky --game minecraft
```

With `--synthetic-scenario` and no `--wikimedia-replay`, the scenario also invents
pageviews for five games (rising, falling or flat). Real order: all
`insufficient_evidence` ("No real recommendation can be justified"). Simulated order:

| Game | Simulated | Shows |
|---|---|---|
| Dota 2 | candidate | B two families up; C an upcoming season; E a complete empty video sample, stated not to be low competition |
| Rust | watchlist | H readership up, players down: disagreement; D bounded, incomplete coverage; F two community angles; G a theme seen once, not an angle |
| Counter-Strike 2 | insufficient | readership down; I stale player samples; quota-limited coverage |
| No Man's Sky, Elden Ring | insufficient | flat; ambiguous coverage |
| Minecraft | insufficient | J nothing at all |

K: posts and videos retrieved after the moment never appear. L: the same stores read two
days earlier have no event, no climb and no searches (tested).

Recorded only:

```
uv run metaspike evidence --as-of 2026-10-05T12:00:00+00:00 --opportunities \
    --wikimedia-replay tests/fixtures/wikimedia --recorded-through 2026-10-03
```

All fifteen games `insufficient_evidence`. Six verified pageview series, every one
`flat` (largest |robust z| 1.86, for GTA V, downward). No other verified source exists.
**This is the real state of MetaSpike's evidence today: it cannot justify a single
recommendation.**

`--json` prints the structured shortlist; `--out` refuses the artifacts directory.

## Not shown, and why

Case A, verified single-source attention, cannot be shown on real data because no
recorded series moves past the threshold. It is tested on a copy of real verified
evidence with one family's movement replaced, in memory only: a watchlist entry, never a
candidate, with creator supply and community stated unknown.

## In the developer UI (U6.4)

`/opportunities` in `apps/dev-ui` renders a generated private report, verified view by
default, with a separate labelled synthetic demonstration. It is off by default, never on
in a production build, reads one of two fixed local files, and validates the report against
a schema generated from this model. See `apps/dev-ui/README.md`.

## Limitations

- No threshold here is validated: 3.5 is borrowed from score v0, 7 days is a choice.
- The rubric is binary per question; it does not grade strength.
- No verified YouTube, Reddit, Steam players or Steam News evidence exists, so no real
  game can pass beyond `insufficient_evidence` except by a verified pageview movement.
- Event angles assume the event happens as announced.
- Simulated readiness counts unverified data as if real: it is for inspecting the rule.

## Calibration still needed

Historical evaluation of each question against outcomes (validation_protocol_v1 and
outcome_rules_v1 exist for pageviews only), thresholds chosen from that evidence, and a
reviewed rule version. Synthetic tests prove the rubric does what it says, not that it
predicts anything.

U6.5 adds the offline historical evaluation
([creator-opportunity-historical-evaluation.md](creator-opportunity-historical-evaluation.md)).
On the recorded data no moment reaches a candidate, so candidate accuracy is NOT ESTIMABLE,
and creator-video success cannot yet be validated from the recorded data.

## Before public launch

Verified sources beyond pageviews; their policies cleared for public use (YouTube and
Reddit derived output, Steam derived metrics); calibration; a reviewed public contract.
None is started. The artifact contract is unchanged at 1.5.0.
