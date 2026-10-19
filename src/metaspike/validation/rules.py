"""`outcome_rules_v1` and `validation_protocol_v1`: every number validation uses.

Two versioned rule sets, each a frozen value with a reason beside every constant.

`outcome_rules_v1` says what "what happened afterwards" means. It was fixed before any
backtest was run and is not to be tuned to make a score look good: changing a value is
a new version, and results under different versions are not comparable.

`validation_protocol_v1` says how predictions are compared with outcomes and how much
evidence a result is. Under v1 no result can be called validated.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from metaspike.domain.model import FrozenModel

OUTCOME_RULES_VERSION = "outcome_rules_v1"
PROTOCOL_VERSION = "validation_protocol_v1"


class OutcomeRules(FrozenModel):
    """Forward outcomes for a DAILY demand signal. All windows are whole UTC days."""

    version: str
    # Primary: the days after the snapshot whose mean is compared with the baseline.
    primary_days: int = Field(ge=1)
    primary_rationale: str
    # Secondary: the next day against the recent level known at the snapshot.
    movement_days: int = Field(ge=1)
    movement_rationale: str
    # Secondary: the share of these forward days that stay above the baseline.
    persistence_days: int = Field(ge=1)
    persistence_rationale: str
    # Secondary: how far ahead a local peak is looked for. A peak on day k needs day
    # k + 1 to confirm it, so this is one less than the days of future read.
    peak_search_days: int = Field(ge=1)
    peak_rationale: str
    # An uplift at or above this is counted as a hit.
    hit_threshold: float = Field(gt=0)
    hit_rationale: str
    # An uplift at or below this is counted as a downside.
    downside_threshold: float = Field(lt=0)
    downside_rationale: str

    @model_validator(mode="after")
    def _windows_are_ordered(self) -> Self:
        if not self.movement_days <= self.primary_days <= self.persistence_days:
            raise ValueError("windows must nest: movement within primary within persistence")
        if self.peak_search_days >= self.persistence_days + 1:
            raise ValueError("a peak needs the following day inside the future that is read")
        return self

    @property
    def future_days(self) -> int:
        """How many days after the snapshot the outcome side reads at most."""
        return max(self.persistence_days, self.peak_search_days + 1)


OUTCOME_RULES_V1 = OutcomeRules(
    version=OUTCOME_RULES_VERSION,
    primary_days=3,
    primary_rationale=(
        "Seventy-two hours is long enough to make and publish something and short enough "
        "that the recommendation is still the reason. For a daily signal that is the next "
        "three complete days."
    ),
    movement_days=1,
    movement_rationale=(
        "The very next day, against the three-day level the snapshot already knew: did "
        "attention keep moving or stall at once?"
    ),
    persistence_days=7,
    persistence_rationale=(
        "A week covers one whole weekly cycle, so a weekday effect cannot pass for persistence."
    ),
    peak_search_days=6,
    peak_rationale=(
        "The furthest day inside the week whose following day is also inside it, so a "
        "peak is always confirmed by a decline and never assumed at the edge."
    ),
    hit_threshold=0.10,
    hit_rationale=(
        "Ten percent above the game's own normal level is the smallest uplift treated as "
        "more than ordinary day-to-day variation. A stated prior, not a fitted value."
    ),
    downside_threshold=-0.10,
    downside_rationale="The same distance below normal. Symmetric on purpose.",
)


class EvidenceStatus(StrEnum):
    # The sample can show that the machinery works, and nothing about the score.
    FRAMEWORK_ONLY = "framework_only"
    # Enough to compute every metric; far too little to read anything into them.
    INSUFFICIENT_SAMPLE = "insufficient_sample"
    # Enough to look for patterns. Still in-sample, still one protocol, no holdout.
    EXPLORATORY = "exploratory"
    # Confirmed on data the rules were not chosen with. Unreachable under protocol v1.
    VALIDATED = "validated"


class EvidenceThreshold(FrozenModel):
    """The least a sample must hold to be called this status."""

    snapshot_dates: int = Field(ge=0)
    games: int = Field(ge=0)
    evaluable_outcomes: int = Field(ge=0)
    independent_sources: int = Field(ge=0)
    rationale: str


class ValidationProtocol(FrozenModel):
    version: str
    # The sizes of the leading groups whose outcomes are reported.
    top_k: tuple[int, ...] = Field(min_length=1)
    top_k_rationale: str
    # Upper edges of the score bins; the first bin is a score of exactly 0.
    score_bin_edges: tuple[float, ...] = Field(min_length=1)
    score_bin_rationale: str
    # A day needs at least this many games with an outcome for a rank correlation.
    min_games_for_association: int = Field(ge=3)
    association_rationale: str
    # How many of the most recent snapshot days are kept for inspection in the report.
    inspection_days: int = Field(ge=0)
    inspection_rationale: str
    thresholds: Mapping[EvidenceStatus, EvidenceThreshold]
    # Protocol v1 has no held-out period, so it can never report VALIDATED.
    supports_holdout: bool
    holdout_rationale: str

    @model_validator(mode="after")
    def _thresholds_are_complete_and_ordered(self) -> Self:
        needed = (
            EvidenceStatus.INSUFFICIENT_SAMPLE,
            EvidenceStatus.EXPLORATORY,
            EvidenceStatus.VALIDATED,
        )
        if tuple(self.thresholds) != needed:
            raise ValueError("thresholds are given for each status above framework_only, in order")
        previous: EvidenceThreshold | None = None
        for status in needed:
            current = self.thresholds[status]
            if previous is not None and not (
                current.snapshot_dates >= previous.snapshot_dates
                and current.games >= previous.games
                and current.evaluable_outcomes >= previous.evaluable_outcomes
                and current.independent_sources >= previous.independent_sources
            ):
                raise ValueError("each status must ask for at least as much as the one before")
            previous = current
        if list(self.top_k) != sorted(set(self.top_k)) or self.top_k[0] < 1:
            raise ValueError("top_k must be positive and strictly increasing")
        if list(self.score_bin_edges) != sorted(set(self.score_bin_edges)):
            raise ValueError("score bin edges must be strictly increasing")
        return self


VALIDATION_PROTOCOL_V1 = ValidationProtocol(
    version=PROTOCOL_VERSION,
    top_k=(1, 3, 5),
    top_k_rationale=(
        "The question a creator asks is about the head of the list: the first pick, the "
        "first few, the first screen."
    ),
    score_bin_edges=(10.0, 25.0, 50.0, 100.0),
    score_bin_rationale=(
        "Fixed edges, not quantiles: score v0 is exactly 0 for every game whose attention "
        "is at or below its baseline, so quantiles would split identical scores."
    ),
    min_games_for_association=3,
    association_rationale="A rank correlation over fewer than three games says nothing.",
    inspection_days=60,
    inspection_rationale=(
        "Enough recent days to audit by eye without the report growing with the whole "
        "history. Every snapshot remains in the accountability store."
    ),
    thresholds={
        EvidenceStatus.INSUFFICIENT_SAMPLE: EvidenceThreshold(
            snapshot_dates=30,
            games=20,
            evaluable_outcomes=500,
            independent_sources=1,
            rationale=(
                "Below a month of days or twenty games a result describes those games in "
                "that month and nothing else."
            ),
        ),
        EvidenceStatus.EXPLORATORY: EvidenceThreshold(
            snapshot_dates=90,
            games=100,
            evaluable_outcomes=5000,
            independent_sources=1,
            rationale=(
                "A season of days across a hundred games is enough to look for patterns "
                "worth testing, not to conclude from."
            ),
        ),
        EvidenceStatus.VALIDATED: EvidenceThreshold(
            snapshot_dates=180,
            games=500,
            evaluable_outcomes=50000,
            independent_sources=2,
            rationale=(
                "Half a year, several hundred games and a second, independent demand "
                "source, and in addition a held-out period the rules were not chosen on."
            ),
        ),
    },
    supports_holdout=False,
    holdout_rationale=(
        "This protocol evaluates one period in-sample. It has no held-out period, so it "
        "cannot report validated however large the sample is."
    ),
)
