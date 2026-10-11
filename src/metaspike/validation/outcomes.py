"""Outcome labels: what happened after a snapshot, measured against what it knew.

The OUTCOME side. A label is made from a frozen snapshot and the days after its
cutoff, and from nothing else. The reference it compares with is the one inside the
snapshot, so no baseline is ever computed from the future.

`outcome_rules_v1`, for a daily demand signal. Let t be the snapshot's cutoff day,
x[d] the value on day d, b the 28-day baseline median the snapshot recorded and a the
3-day average it recorded.

    realized_uplift_72h = mean(x[t+1], x[t+2], x[t+3]) / b - 1
        Primary. Positive: the next three days ran above the game's known normal level.
        Exactly 0: they averaged exactly that level. Negative: below it. Not clipped.
        It is the snapshot's own lift measure, moved forward three days.

    movement_24h = x[t+1] / a - 1
        The next day against the recent level the snapshot already knew.

    persistence_7d = count(x[t+k] > b for k in 1..7) / 7
        The share of the following week spent above the known normal level.

    lead_time_days = the smallest k in 1..6 with
        x[t+k] > x[t+k-1]  and  x[t+k] >= x[t+k+1]  and  x[t+k] > b
        The first confirmed local peak above normal. None in the window is a finding.

Each measure needs every day of its own window. A day that is missing, or that the
recording does not reach, makes the measure UNAVAILABLE. It is never filled with zero
and never averaged over what happens to be there.
"""

from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from metaspike.domain.model import FrozenModel
from metaspike.validation.history import FutureValues
from metaspike.validation.rules import OUTCOME_RULES_V1, OutcomeRules
from metaspike.validation.snapshot import RecommendationSnapshot, content_id


class OutcomeAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class OutcomeUnavailable(StrEnum):
    # The snapshot had no score, so it made no recommendation to hold to account.
    NOT_SCORED_AT_SNAPSHOT = "not_scored_at_snapshot"
    # The snapshot recorded no usable reference level (baseline missing or zero).
    NO_REFERENCE = "no_reference"
    # A day of the forward window is missing or lies beyond the data that exists.
    FUTURE_INCOMPLETE = "future_incomplete"


class PeakStatus(StrEnum):
    FOUND = "found"
    NONE_IN_WINDOW = "none_in_window"
    UNAVAILABLE = "unavailable"


class ForwardDay(FrozenModel):
    day: date
    value: float | None = Field(default=None, ge=0)


class OutcomeLabel(FrozenModel):
    label_id: str = Field(pattern=r"^label_[0-9a-f]{32}$")
    snapshot_id: str
    canonical_game_id: str
    outcome_rules_version: str
    # The last day of data that existed when the label was made.
    data_through: date
    # The reference levels, copied from the snapshot. Never recomputed.
    reference_baseline: float | None = None
    reference_avg_3d: float | None = None
    # The forward days read, in order, each observed or not.
    forward: tuple[ForwardDay, ...]
    primary_window_start: date
    primary_window_end: date
    availability: OutcomeAvailability
    unavailable_reason: OutcomeUnavailable | None = None
    realized_uplift_72h: float | None = None
    movement_24h: float | None = None
    persistence_7d: float | None = Field(default=None, ge=0, le=1)
    peak_status: PeakStatus
    lead_time_days: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _availability_is_consistent(self) -> Self:
        available = self.availability is OutcomeAvailability.AVAILABLE
        if available != (self.realized_uplift_72h is not None):
            raise ValueError("the primary outcome is a number exactly when it is available")
        if available == (self.unavailable_reason is not None):
            raise ValueError("unavailable_reason is given exactly when unavailable")
        if (self.peak_status is PeakStatus.FOUND) != (self.lead_time_days is not None):
            raise ValueError("lead_time_days is given exactly when a peak was found")
        return self


def _complete(values: list[float | None]) -> list[float] | None:
    """The values, if every one is present. A single gap makes the whole window unusable."""
    present = [value for value in values if value is not None]
    return present if len(present) == len(values) and values else None


def label(
    snapshot: RecommendationSnapshot,
    future: FutureValues,
    rules: OutcomeRules = OUTCOME_RULES_V1,
) -> OutcomeLabel:
    """The outcome of one snapshot. Reads the snapshot and the days after its cutoff."""
    cutoff = snapshot.data_cutoff_day
    features = snapshot.features
    baseline = None if features is None else features.baseline_median_28d
    recent = None if features is None else features.avg_3d
    forward = (
        future.after(snapshot.canonical_game_id, snapshot.scored_signal, cutoff, rules.future_days)
        if snapshot.scored_signal is not None
        else ()
    )
    values = [value for _, value in forward]
    current = None if features is None else features.latest_views

    reason: OutcomeUnavailable | None = None
    uplift: float | None = None
    if snapshot.rank is None or snapshot.score.value is None:
        reason = OutcomeUnavailable.NOT_SCORED_AT_SNAPSHOT
    elif baseline is None or baseline <= 0:
        reason = OutcomeUnavailable.NO_REFERENCE
    else:
        primary = _complete(values[: rules.primary_days])
        if primary is None:
            reason = OutcomeUnavailable.FUTURE_INCOMPLETE
        else:
            uplift = sum(primary) / len(primary) / baseline - 1

    movement: float | None = None
    persistence: float | None = None
    peak, lead = PeakStatus.UNAVAILABLE, None
    if reason is not OutcomeUnavailable.NOT_SCORED_AT_SNAPSHOT:
        next_days = _complete(values[: rules.movement_days])
        if next_days is not None and recent is not None and recent > 0:
            movement = sum(next_days) / len(next_days) / recent - 1
        week = _complete(values[: rules.persistence_days])
        if week is not None and baseline is not None and baseline > 0:
            persistence = sum(1 for value in week if value > baseline) / len(week)
        path = _complete([current, *values[: rules.peak_search_days + 1]])
        if path is not None and baseline is not None and baseline > 0:
            peak = PeakStatus.NONE_IN_WINDOW
            for k in range(1, rules.peak_search_days + 1):
                if path[k] > path[k - 1] and path[k] >= path[k + 1] and path[k] > baseline:
                    peak, lead = PeakStatus.FOUND, k
                    break

    fields: dict[str, object] = {
        "snapshot_id": snapshot.snapshot_id,
        "canonical_game_id": snapshot.canonical_game_id,
        "outcome_rules_version": rules.version,
        "data_through": future.data_through,
        "reference_baseline": baseline,
        "reference_avg_3d": recent,
        "forward": tuple(ForwardDay(day=day, value=value) for day, value in forward),
        "primary_window_start": date.fromordinal(cutoff.toordinal() + 1),
        "primary_window_end": date.fromordinal(cutoff.toordinal() + rules.primary_days),
        "availability": (
            OutcomeAvailability.UNAVAILABLE if reason is not None else OutcomeAvailability.AVAILABLE
        ),
        "unavailable_reason": reason,
        "realized_uplift_72h": uplift,
        "movement_24h": movement,
        "persistence_7d": persistence,
        "peak_status": peak,
        "lead_time_days": lead,
    }
    draft = OutcomeLabel.model_validate({**fields, "label_id": "label_" + "0" * 32})
    payload = draft.model_dump(mode="json", exclude={"label_id"})
    return draft.model_copy(update={"label_id": content_id("label", payload)})
