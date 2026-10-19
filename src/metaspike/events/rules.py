"""`event_heat_rules_v1`: every number Event Heat uses, in one place, with its reason.

STATUS: INTERNAL / UNCALIBRATED. These are engineering priors chosen so the engine
behaves sensibly while there is no data to fit them to. They are not learned, not
backtested and not a statement about what creators should do. A change to any value is
a new rules version.

No function in this package carries a constant of its own. If a number affects heat it
is a field here.
"""

from collections.abc import Mapping
from typing import Self

from pydantic import Field, model_validator

from metaspike.domain.events import EventType
from metaspike.domain.model import FrozenModel

RULES_VERSION = "event_heat_rules_v1"
RULES_STATUS = "internal_uncalibrated"


class TypeRule(FrozenModel):
    """How one type of event behaves over time. All durations are in days."""

    # The most one event of this type can contribute, on a scale of 0 to 1. This is the
    # type's importance and its ceiling at once.
    importance: float = Field(gt=0, le=1)
    # How long before the start anticipation builds. 0: no pre-event heat at all.
    ramp_days: float = Field(ge=0)
    # The share of full strength reached the instant before the start.
    pre_event_peak: float = Field(ge=0, le=1)
    # How long the event is taken to last when the source gives no end. 0: instantaneous.
    default_duration_days: float = Field(ge=0)
    # Days for strength to halve, counted from the start while the event runs and from
    # the end once it is over.
    half_life_days: float = Field(gt=0)
    # Strength never falls below this while the event is still running.
    active_floor: float = Field(ge=0, le=1)
    # Days after the end at which the event stops counting altogether.
    decay_window_days: float = Field(ge=0)
    rationale: str = Field(min_length=1)


class EventHeatRules(FrozenModel):
    version: str
    status: str
    # Heat is reported on 0 to `scale`.
    scale: float = Field(gt=0)
    scale_rationale: str
    # Only the strongest few events of a game count; the rest are listed, not added.
    max_contributing_events: int = Field(ge=1)
    max_contributing_events_rationale: str
    # A game's event coverage is trusted for this long after a source last looked.
    coverage_max_age_days: float = Field(gt=0)
    coverage_max_age_rationale: str
    # How far ahead the radar and a game's event list look.
    horizon_days: float = Field(gt=0)
    horizon_rationale: str
    # The radar's windows, as hours ahead, shortest first. The last one is the horizon.
    radar_windows_hours: tuple[int, ...] = Field(min_length=1)
    radar_windows_rationale: str
    aggregation: str
    types: Mapping[EventType, TypeRule]

    @model_validator(mode="after")
    def _every_type_has_a_rule(self) -> Self:
        missing = [event_type.value for event_type in EventType if event_type not in self.types]
        if missing:
            raise ValueError(f"no rule for event type(s): {', '.join(missing)}")
        windows = self.radar_windows_hours
        if list(windows) != sorted(set(windows)) or windows[0] <= 0:
            raise ValueError("radar windows must be positive and strictly increasing")
        if windows[-1] != self.horizon_days * 24:
            raise ValueError("the last radar window must be the horizon")
        longest = max(rule.ramp_days for rule in self.types.values())
        if longest > self.horizon_days:
            raise ValueError("no ramp may be longer than the horizon")
        return self

    def rule(self, event_type: EventType) -> TypeRule:
        return self.types[event_type]


def _rule(
    importance: float,
    ramp: float,
    peak: float,
    duration: float,
    half_life: float,
    floor: float,
    window: float,
    rationale: str,
) -> TypeRule:
    return TypeRule(
        importance=importance,
        ramp_days=ramp,
        pre_event_peak=peak,
        default_duration_days=duration,
        half_life_days=half_life,
        active_floor=floor,
        decay_window_days=window,
        rationale=rationale,
    )


EVENT_HEAT_RULES_V1 = EventHeatRules(
    version=RULES_VERSION,
    status=RULES_STATUS,
    scale=100.0,
    scale_rationale=(
        "The same 0 to 100 scale as the score and confidence, so the three read alike. It "
        "is a different quantity and is not added to either."
    ),
    max_contributing_events=3,
    max_contributing_events_rationale=(
        "A game is rarely about more than a few things at once. Counting only the three "
        "strongest stops a pile of small events from looking like one large one."
    ),
    coverage_max_age_days=14.0,
    coverage_max_age_rationale=(
        "Dates move. If no source has looked at a game's events for two weeks, what is "
        "known may be out of date, and heat is reported as unavailable, never as zero."
    ),
    horizon_days=30.0,
    horizon_rationale=(
        "A month is as far ahead as a dated announcement is useful for planning content, "
        "and the longest ramp any type has."
    ),
    radar_windows_hours=(24, 168, 720),
    radar_windows_rationale=(
        "The next day, the next week and the next month: what can still be prepared for "
        "today, this week, and with planning."
    ),
    aggregation=(
        "Each event contributes importance x strength, between 0 and 1. Events of the same "
        "type starting on the same UTC day count once, at the larger contribution, so an "
        "unmerged duplicate cannot count twice. The max_contributing_events largest "
        "contributions c are combined as heat = scale x (1 - product(1 - c)). The result "
        "is never below the largest single contribution, never reaches the scale unless "
        "one contribution is 1, and adding an event never lowers it."
    ),
    types={
        EventType.RELEASE: _rule(
            1.0,
            30,
            0.6,
            0,
            7,
            0,
            30,
            "The largest moment a game has. Anticipation builds for a month, the day "
            "itself is the peak, and interest halves about weekly for a month after.",
        ),
        EventType.EXPANSION: _rule(
            0.85,
            21,
            0.55,
            0,
            6,
            0,
            21,
            "A second launch for an existing audience: nearly as large as a release, "
            "with a shorter build-up and tail.",
        ),
        EventType.PLATFORM_RELEASE: _rule(
            0.6,
            14,
            0.5,
            0,
            5,
            0,
            14,
            "A launch for a new audience only. Real, but smaller than the original.",
        ),
        EventType.SEASON: _rule(
            0.7,
            7,
            0.5,
            60,
            5,
            0.15,
            3,
            "A season's start is the event; the season then runs for weeks. Strength "
            "halves every five days and holds at a low floor until the season ends.",
        ),
        EventType.WIPE: _rule(
            0.65,
            5,
            0.6,
            14,
            4,
            0.1,
            2,
            "A reset everyone plays through at once. Sharp, anticipated a few days "
            "ahead, and largely over within two weeks.",
        ),
        EventType.MAJOR_PATCH: _rule(
            0.6,
            5,
            0.4,
            0,
            4,
            0,
            10,
            "Changes what there is to say about a game. Usually dated a few days ahead; "
            "interest fades within about ten days.",
        ),
        EventType.DLC: _rule(
            0.5,
            10,
            0.45,
            0,
            4,
            0,
            10,
            "New content smaller than an expansion.",
        ),
        EventType.EXPEDITION: _rule(
            0.45,
            5,
            0.4,
            42,
            5,
            0.1,
            2,
            "A time-limited community event that runs for weeks, strongest at its start.",
        ),
        EventType.MAJOR_IN_GAME_EVENT: _rule(
            0.55,
            5,
            0.5,
            3,
            3,
            0.25,
            3,
            "A live moment inside the game. Short, so it stays near full strength while "
            "it runs and fades within days.",
        ),
        EventType.BETA: _rule(
            0.5,
            7,
            0.45,
            7,
            3,
            0.2,
            4,
            "First hands-on access for a wide audience, for a limited time.",
        ),
        EventType.PLAYTEST: _rule(
            0.35,
            5,
            0.4,
            4,
            3,
            0.2,
            3,
            "Limited access, smaller than a beta.",
        ),
        EventType.FREE_WEEKEND: _rule(
            0.4,
            3,
            0.4,
            3,
            3,
            0.4,
            2,
            "A few days in which anyone can play. It matters while it lasts and little after.",
        ),
        EventType.SHOWCASE: _rule(
            0.45,
            7,
            0.4,
            1,
            2,
            0.5,
            4,
            "A dated presentation. Speculation builds for a week and the reaction is "
            "spent within days.",
        ),
        EventType.TRAILER: _rule(
            0.4,
            2,
            0.3,
            0,
            2,
            0,
            5,
            "Often a surprise, so there is little build-up. Reaction content has a short life.",
        ),
        EventType.MINOR_PATCH: _rule(
            0.15,
            1,
            0.2,
            0,
            2,
            0,
            3,
            "Routine maintenance. Recorded, and deliberately worth little: three of them "
            "together stay well under one major patch.",
        ),
    },
)
