"""Score, confidence and explanation results."""

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from metaspike.domain.model import FrozenModel

UnitInterval = float


class ScoreStatus(StrEnum):
    SCORED = "scored"
    UNSCORED = "unscored"


class UnscoredReason(StrEnum):
    NO_OBSERVATIONS = "no_observations"
    INSUFFICIENT_HISTORY = "insufficient_history"
    INSUFFICIENT_RECENT_DATA = "insufficient_recent_data"
    ZERO_BASELINE = "zero_baseline"
    ZERO_DISPERSION = "zero_dispersion"


class ScoreComponents(FrozenModel):
    significance: UnitInterval = Field(ge=0, le=1)
    magnitude: UnitInterval = Field(ge=0, le=1)
    anomaly: UnitInterval = Field(ge=0, le=1)
    momentum: UnitInterval = Field(ge=0, le=1)


class ScoreResult(FrozenModel):
    score_version: str
    status: ScoreStatus
    value: float | None = Field(default=None, ge=0, le=100)
    components: ScoreComponents | None = None
    unscored_reason: UnscoredReason | None = None

    @model_validator(mode="after")
    def _status_is_consistent(self) -> Self:
        scored = self.status is ScoreStatus.SCORED
        if scored != (self.value is not None) or scored != (self.components is not None):
            raise ValueError(
                "a scored result needs a value and components; an unscored one neither"
            )
        if scored == (self.unscored_reason is not None):
            raise ValueError("unscored_reason must be set exactly when the result is unscored")
        return self


class ConfidenceBand(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ConfidenceFactors(FrozenModel):
    source_ceiling: UnitInterval = Field(ge=0, le=1)
    freshness: UnitInterval = Field(ge=0, le=1)
    history: UnitInterval = Field(ge=0, le=1)
    completeness: UnitInterval = Field(ge=0, le=1)
    samples: UnitInterval = Field(ge=0, le=1)


class ConfidenceResult(FrozenModel):
    value: float = Field(ge=0, le=100)
    band: ConfidenceBand
    ceiling: float = Field(ge=0, le=100)
    independent_sources: int = Field(ge=0)
    factors: ConfidenceFactors


class Reason(FrozenModel):
    code: str
    text: str


class Explanation(FrozenModel):
    summary: str
    reasons: tuple[Reason, ...]
    top_reasons: tuple[str, ...]
