"""Monitoring tiers, and the rule that proposes one.

A tier says how often a sampled source is asked about a game. Period sources ignore
it: a daily series is daily whatever the game's tier.

The rule here is SHADOW ONLY. It proposes a tier and the proposal is recorded beside
the schedule; nothing in production samples a game faster because of it. The only way
a game leaves the long tail in production is an explicit, expiring pin in the control
record. `baseline_rules_v1` is not read here at all: its change-point output is shadow
only and may not promote a tier.

A missing value is never treated as zero and never escalates: without enough history
the proposal is the long tail, with the reason recorded.
"""

from collections.abc import Mapping, Sequence
from enum import IntEnum

from metaspike.domain.model import FrozenModel

RULES_VERSION = "tiering_rules_v0"


class Tier(IntEnum):
    SURGE = 0
    HOT = 1
    ACTIVE = 2
    WARM = 3
    LONG_TAIL = 4
    CATALOGUED = 5  # known, not sampled


# How often a sampled source is asked, per tier. The catalogued tier is never asked.
INTERVAL_MINUTES: Mapping[Tier, int] = {
    Tier.SURGE: 15,
    Tier.HOT: 30,
    Tier.ACTIVE: 60,
    Tier.WARM: 180,
    Tier.LONG_TAIL: 1440,
}
# Simulation defaults. They are capacities, NOT approved request budgets: a source's
# own caps always override what a tier could hold.
DEFAULT_CAPS: Mapping[Tier, int] = {
    Tier.SURGE: 25,
    Tier.HOT: 75,
    Tier.ACTIVE: 300,
    Tier.WARM: 1000,
}


class Attention(FrozenModel):
    """What the rule may look at for one game. Absent means unknown, never zero."""

    latest: float | None = None
    trailing_median: float | None = None
    days_since_event: int | None = None


class Proposal(FrozenModel):
    tier: Tier
    # Orders games inside a tier when it is over capacity. Higher keeps its place.
    score: float
    reason: str


def propose(attention: Attention | None) -> Proposal:
    """The tier this rule would give one monitored game. Deterministic."""
    if attention is None:
        return Proposal(tier=Tier.LONG_TAIL, score=0.0, reason="no_attention_data")
    if attention.days_since_event is not None and attention.days_since_event <= 0:
        return Proposal(tier=Tier.SURGE, score=1000.0, reason="event_today")
    if attention.days_since_event is not None and attention.days_since_event <= 3:
        return Proposal(tier=Tier.HOT, score=500.0, reason="event_within_3_days")
    latest, median = attention.latest, attention.trailing_median
    if latest is None or median is None or median <= 0:
        return Proposal(tier=Tier.LONG_TAIL, score=0.0, reason="insufficient_history")
    ratio = latest / median
    if ratio >= 5 and latest >= 2000:
        return Proposal(tier=Tier.SURGE, score=ratio, reason="attention_5x")
    if ratio >= 3 and latest >= 1000:
        return Proposal(tier=Tier.HOT, score=ratio, reason="attention_3x")
    if ratio >= 2 and latest >= 500:
        return Proposal(tier=Tier.ACTIVE, score=ratio, reason="attention_2x")
    if ratio >= 1.3 or latest >= 5000:
        return Proposal(tier=Tier.WARM, score=ratio, reason="attention_elevated")
    return Proposal(tier=Tier.LONG_TAIL, score=ratio, reason="quiet")


def fit_to_capacity(
    proposals: Mapping[str, Proposal], caps: Mapping[Tier, int] = DEFAULT_CAPS
) -> dict[str, Tier]:
    """Give each game its proposed tier, moving the weakest down when a tier is full.

    A tier never holds more than its cap. What does not fit moves down exactly one
    tier and competes there with what was proposed for that tier. The order inside a
    tier is the score, then the id, so the result does not depend on input order.
    """
    assigned: dict[str, Tier] = {}
    carried: Sequence[tuple[str, Proposal]] = ()
    for tier in (Tier.SURGE, Tier.HOT, Tier.ACTIVE, Tier.WARM):
        here = [*carried, *((k, p) for k, p in proposals.items() if p.tier is tier)]
        here.sort(key=lambda item: (-item[1].score, item[0]))
        cap = caps.get(tier, 0)
        for key, _ in here[:cap]:
            assigned[key] = tier
        carried = here[cap:]
    for key, _ in carried:
        assigned[key] = Tier.LONG_TAIL
    for key, proposal in proposals.items():
        if proposal.tier >= Tier.LONG_TAIL:
            assigned[key] = Tier.LONG_TAIL
    return assigned
