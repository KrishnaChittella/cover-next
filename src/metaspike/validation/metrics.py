"""Accountability metrics: predictions joined to outcomes, and summarised.

Everything here is arithmetic over frozen snapshots and their labels. Nothing is
resampled and nothing is random: the same inputs give the same report. A summary of no
values has no mean; it is reported as empty, never as zero.

Strategies are ways of ordering the SAME games on the SAME day, each judged by the
same primary outcome:

    metaspike_score_v0   the published rank
    raw_popularity       latest observed daily value, highest first
    simple_lift          the 3-day average over the 28-day median, highest first
    simple_velocity      the 3-day average over the preceding 3 days, highest first

Every strategy orders the games that score v0 ranked that day, so they are compared on
one universe. Each key is a value the snapshot already held. A missing key sorts last;
ties are broken by slug.
"""

import statistics
from collections.abc import Callable, Sequence
from datetime import UTC, date

from pydantic import Field

from metaspike.domain.model import FrozenModel
from metaspike.validation.outcomes import OutcomeLabel, PeakStatus
from metaspike.validation.rules import OutcomeRules, ValidationProtocol
from metaspike.validation.snapshot import RecommendationSnapshot

Pair = tuple[RecommendationSnapshot, OutcomeLabel]

STRATEGIES: dict[str, tuple[str, str]] = {
    "metaspike_score_v0": (
        "MetaSpike score v0",
        "The published rank: attention anomaly and momentum, then confidence, then slug.",
    ),
    "raw_popularity": (
        "Raw popularity",
        "Latest observed daily value, highest first. What a list of big games would say.",
    ),
    "simple_lift": (
        "Simple lift",
        "The 3-day average over the 28-day baseline median, highest first.",
    ),
    "simple_velocity": (
        "Simple velocity",
        "The 3-day average over the preceding 3 days, highest first.",
    ),
}


def _key(strategy: str) -> Callable[[RecommendationSnapshot], float | None]:
    def popularity(snapshot: RecommendationSnapshot) -> float | None:
        return None if snapshot.features is None else snapshot.features.latest_views

    def lift(snapshot: RecommendationSnapshot) -> float | None:
        return None if snapshot.features is None else snapshot.features.lift_ratio

    def velocity(snapshot: RecommendationSnapshot) -> float | None:
        return None if snapshot.features is None else snapshot.features.velocity_ratio

    return {"raw_popularity": popularity, "simple_lift": lift, "simple_velocity": velocity}[
        strategy
    ]


def order(strategy: str, ranked: Sequence[RecommendationSnapshot]) -> list[RecommendationSnapshot]:
    """One day's ranked games in a strategy's order, best first."""
    if strategy == "metaspike_score_v0":
        return sorted(ranked, key=lambda item: item.rank or 0)
    key = _key(strategy)

    def sort_key(item: RecommendationSnapshot) -> tuple[int, float, str]:
        value = key(item)
        return (1, 0.0, item.slug) if value is None else (0, -value, item.slug)

    return sorted(ranked, key=sort_key)


class Summary(FrozenModel):
    """A set of primary outcomes. With no values every statistic is absent."""

    count: int = Field(ge=0)
    mean: float | None = None
    median: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    hit_rate: float | None = Field(default=None, ge=0, le=1)
    downside_rate: float | None = Field(default=None, ge=0, le=1)


def summarise(values: Sequence[float], rules: OutcomeRules) -> Summary:
    if not values:
        return Summary(count=0)
    return Summary(
        count=len(values),
        mean=statistics.fmean(values),
        median=statistics.median(values),
        minimum=min(values),
        maximum=max(values),
        hit_rate=sum(1 for value in values if value >= rules.hit_threshold) / len(values),
        downside_rate=sum(1 for value in values if value <= rules.downside_threshold) / len(values),
    )


def _average_ranks(values: Sequence[float]) -> list[float]:
    """Ranks from 1, with tied values sharing the mean of the ranks they span."""
    indexed = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(indexed):
        end = start
        while end + 1 < len(indexed) and values[indexed[end + 1]] == values[indexed[start]]:
            end += 1
        shared = (start + end) / 2 + 1
        for position in range(start, end + 1):
            ranks[indexed[position]] = shared
        start = end + 1
    return ranks


def spearman(first: Sequence[float], second: Sequence[float]) -> float | None:
    """Spearman's rank correlation. None when either side has no variation."""
    if len(first) != len(second) or len(first) < 2:
        return None
    a, b = _average_ranks(first), _average_ranks(second)
    mean_a, mean_b = statistics.fmean(a), statistics.fmean(b)
    spread_a = sum((value - mean_a) ** 2 for value in a)
    spread_b = sum((value - mean_b) ** 2 for value in b)
    if spread_a == 0 or spread_b == 0:
        return None
    covariance = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b, strict=True))
    return float(covariance / (spread_a * spread_b) ** 0.5)


class TopK(FrozenModel):
    k: int
    # Days on which at least k games were ranked, so a full top k existed.
    days_with_full_group: int
    outcomes: Summary


class Association(FrozenModel):
    """Does a better position go with a better outcome? Per day, then averaged.

    `mean_rho` is the mean over days of Spearman's correlation between position
    (best = highest) and the primary outcome. Positive means better-placed games did
    better afterwards. A day with fewer games than the protocol's minimum, or with no
    variation on either side, is left out and counted.
    """

    statistic: str = "spearman_position_vs_outcome"
    days_used: int
    days_skipped: int
    mean_rho: float | None = None
    median_rho: float | None = None
    positive_days: int = 0
    negative_days: int = 0


class StrategyResult(FrozenModel):
    strategy: str
    label: str
    description: str
    top_k: tuple[TopK, ...]
    association: Association


class Stratum(FrozenModel):
    label: str
    outcomes: Summary


class LeadTime(FrozenModel):
    found: int
    none_in_window: int
    unavailable: int
    median_days: float | None = None
    mean_days: float | None = None


def prediction_day(snapshot: RecommendationSnapshot) -> date:
    """The UTC day the recommendation was made on. What a backtest's days are counted in."""
    return snapshot.as_of.astimezone(UTC).date()


def by_day(pairs: Sequence[Pair]) -> dict[date, list[Pair]]:
    days: dict[date, list[Pair]] = {}
    for pair in pairs:
        days.setdefault(prediction_day(pair[0]), []).append(pair)
    return dict(sorted(days.items()))


def evaluate_strategy(
    strategy: str, pairs: Sequence[Pair], rules: OutcomeRules, protocol: ValidationProtocol
) -> StrategyResult:
    label, description = STRATEGIES[strategy]
    groups: dict[int, list[float]] = {k: [] for k in protocol.top_k}
    full: dict[int, int] = dict.fromkeys(protocol.top_k, 0)
    rhos: list[float] = []
    skipped = 0
    for day_pairs in by_day(pairs).values():
        labels = {snapshot.snapshot_id: outcome for snapshot, outcome in day_pairs}
        ranked = [snapshot for snapshot, _ in day_pairs if snapshot.rank is not None]
        ordered = order(strategy, ranked)
        for k in protocol.top_k:
            if len(ordered) >= k:
                full[k] += 1
            for snapshot in ordered[:k]:
                uplift = labels[snapshot.snapshot_id].realized_uplift_72h
                if uplift is not None:
                    groups[k].append(uplift)
        scored = [
            (len(ordered) - position, labels[snapshot.snapshot_id].realized_uplift_72h)
            for position, snapshot in enumerate(ordered)
        ]
        usable = [(place, uplift) for place, uplift in scored if uplift is not None]
        rho = (
            spearman([p for p, _ in usable], [u for _, u in usable])
            if len(usable) >= protocol.min_games_for_association
            else None
        )
        if rho is None:
            skipped += 1
        else:
            rhos.append(rho)
    return StrategyResult(
        strategy=strategy,
        label=label,
        description=description,
        top_k=tuple(
            TopK(k=k, days_with_full_group=full[k], outcomes=summarise(groups[k], rules))
            for k in protocol.top_k
        ),
        association=Association(
            days_used=len(rhos),
            days_skipped=skipped,
            mean_rho=statistics.fmean(rhos) if rhos else None,
            median_rho=statistics.median(rhos) if rhos else None,
            positive_days=sum(1 for rho in rhos if rho > 0),
            negative_days=sum(1 for rho in rhos if rho < 0),
        ),
    )


def score_bins(
    pairs: Sequence[Pair], rules: OutcomeRules, protocol: ValidationProtocol
) -> tuple[Stratum, ...]:
    """Primary outcomes by score. The first bin is a score of exactly 0."""
    edges = protocol.score_bin_edges
    names = ["0"] + [
        f"{'0' if index == 0 else f'{edges[index - 1]:g}'} to {edge:g}"
        for index, edge in enumerate(edges)
    ]
    values: list[list[float]] = [[] for _ in names]
    for snapshot, outcome in pairs:
        score, uplift = snapshot.score.value, outcome.realized_uplift_72h
        if score is None or uplift is None:
            continue
        index = 0 if score == 0 else 1 + next(i for i, edge in enumerate(edges) if score <= edge)
        values[index].append(uplift)
    return tuple(
        Stratum(label=name, outcomes=summarise(bucket, rules))
        for name, bucket in zip(names, values, strict=True)
    )


def confidence_bands(pairs: Sequence[Pair], rules: OutcomeRules) -> tuple[Stratum, ...]:
    values: dict[str, list[float]] = {"low": [], "medium": [], "high": []}
    for snapshot, outcome in pairs:
        if snapshot.confidence is not None and outcome.realized_uplift_72h is not None:
            values[snapshot.confidence.band.value].append(outcome.realized_uplift_72h)
    return tuple(
        Stratum(label=band, outcomes=summarise(bucket, rules)) for band, bucket in values.items()
    )


def lead_time(pairs: Sequence[Pair]) -> LeadTime:
    """Lead time to the next local peak, over the games that were ranked."""
    ranked = [outcome for snapshot, outcome in pairs if snapshot.rank is not None]
    found = [outcome.lead_time_days for outcome in ranked if outcome.lead_time_days is not None]
    return LeadTime(
        found=len(found),
        none_in_window=sum(1 for o in ranked if o.peak_status is PeakStatus.NONE_IN_WINDOW),
        unavailable=sum(1 for o in ranked if o.peak_status is PeakStatus.UNAVAILABLE),
        median_days=statistics.median(found) if found else None,
        mean_days=statistics.fmean(found) if found else None,
    )
