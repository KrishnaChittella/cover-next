"""Small, deterministic robust statistics. Standard library only."""

import statistics
from collections.abc import Sequence

from metaspike.domain.baseline_rules import MAD_TO_SIGMA


def median_and_mad(values: Sequence[float]) -> tuple[float, float]:
    """The median, and the median absolute deviation around it."""
    centre = statistics.median(values)
    return centre, statistics.median(abs(value - centre) for value in values)


def robust_scale(mad: float, floor: float) -> float:
    """A scale that estimates a standard deviation and can never be zero.

    A flat history has a MAD of zero. Without a floor every later movement, however
    small, would be an infinite anomaly.
    """
    return max(MAD_TO_SIGMA * mad, floor)


def mid_rank_percentile(value: float, history: Sequence[float]) -> float:
    """Where `value` falls among `history`, 0 to 100. `history` must exclude `value`."""
    below = sum(1 for other in history if other < value)
    equal = sum(1 for other in history if other == value)
    return 100 * (below + 0.5 * equal) / len(history)


def theil_sen_slope(points: Sequence[tuple[float, float]]) -> float:
    """The median of the slopes between every pair of points.

    Unlike a least-squares line, one wild point cannot drag it: up to about 29% of
    the points can be arbitrary without moving the answer far. Points that share an
    x contribute no slope. Deterministic: the pairs are taken in order and the median
    of an even count is the mean of the two middle slopes.
    """
    slopes = [
        (later_y - earlier_y) / (later_x - earlier_x)
        for index, (earlier_x, earlier_y) in enumerate(points)
        for later_x, later_y in points[index + 1 :]
        if later_x != earlier_x
    ]
    if not slopes:
        raise ValueError("a slope needs two points at different times")
    return statistics.median(slopes)


def clip(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))
