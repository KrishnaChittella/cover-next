"""Which games are rising: recent curiosity against each game's own normal level.

A game is compared with itself, not with big games: an indie going from 300 to 3,000
views a day ranks above a blockbuster's steady 8,000. Recent is the mean of the last 3
days; normal is the median of the 28 days before them. Every known day is used and an
unknown day is skipped, never read as zero.
"""

from collections.abc import Sequence
from statistics import median

from metaspike.domain.model import FrozenModel

RECENT_DAYS = 3
BASELINE_DAYS = 28
WINDOW_DAYS = RECENT_DAYS + BASELINE_DAYS
# Below this many daily views a jump is noise, not news.
MIN_RECENT = 300
# Fewer known baseline days than this and there is no normal level to compare with.
MIN_BASELINE_DAYS = 14
# Damps ratios for tiny baselines (10 views becoming 40 is not a 4x story).
_SMOOTHING = 50


class Riser(FrozenModel):
    qid: str
    article: str
    recent: float
    baseline: float | None
    ratio: float | None
    new: bool  # no normal level yet: very new, or newly noticed


def assess(qid: str, article: str, series: Sequence[int | None]) -> Riser | None:
    """`series` is WINDOW_DAYS days, oldest first. None when the game is not worth showing."""
    if len(series) != WINDOW_DAYS:
        raise ValueError(f"expected {WINDOW_DAYS} days, got {len(series)}")
    recent_known = [v for v in series[-RECENT_DAYS:] if v is not None]
    if len(recent_known) < 2:
        return None
    recent = sum(recent_known) / len(recent_known)
    if recent < MIN_RECENT:
        return None
    base_known = [v for v in series[:BASELINE_DAYS] if v is not None]
    if len(base_known) < MIN_BASELINE_DAYS:
        return Riser(qid=qid, article=article, recent=recent, baseline=None, ratio=None, new=True)
    baseline = float(median(base_known))
    ratio = (recent + _SMOOTHING) / (baseline + _SMOOTHING)
    return Riser(qid=qid, article=article, recent=recent, baseline=baseline, ratio=ratio, new=False)


def rank(risers: Sequence[Riser]) -> list[Riser]:
    """Biggest jump against its own normal first; games with no normal yet by volume after."""
    known = sorted((r for r in risers if r.ratio is not None), key=lambda r: -(r.ratio or 0))
    new = sorted((r for r in risers if r.ratio is None), key=lambda r: -r.recent)
    return known + new
