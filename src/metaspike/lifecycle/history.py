"""Lifecycle history: append-only, point-in-time, and tied to the rule version in force."""

from datetime import date
from typing import Self

from pydantic import model_validator

from metaspike.domain.games import GameId, LifecycleState, NonEmptyStr
from metaspike.domain.model import FrozenModel
from metaspike.lifecycle.rules import (
    HOLD_DAYS,
    RULE_VERSION,
    ActivityEvidence,
    Availability,
    LifecycleFlag,
    derive_state,
)

# A change among these must be sustained for HOLD_DAYS, so a state cannot flap.
_HELD_STATES = frozenset(
    {
        LifecycleState.RELEASED,
        LifecycleState.LIVE_SERVICE,
        LifecycleState.EVERGREEN,
        LifecycleState.DORMANT,
    }
)
# Only these may be asserted by a labelled override, and only while the evidence
# that would decide them does not exist yet.
OVERRIDABLE_STATES = frozenset(
    {LifecycleState.LIVE_SERVICE, LifecycleState.EVERGREEN, LifecycleState.DORMANT}
)


class LifecycleOverride(FrozenModel):
    canonical_game_id: GameId
    state: LifecycleState
    reason: NonEmptyStr

    @model_validator(mode="after")
    def _only_states_awaiting_evidence(self) -> Self:
        if self.state not in OVERRIDABLE_STATES:
            raise ValueError("only LIVE_SERVICE, EVERGREEN and DORMANT may be overridden")
        return self


class LifecycleRecord(FrozenModel):
    state: LifecycleState
    valid_from: date
    rule_id: NonEmptyStr
    rule_version: NonEmptyStr
    flags: tuple[LifecycleFlag, ...] = ()
    first_available: date | None = None


class LifecycleHistory(FrozenModel):
    canonical_game_id: GameId
    records: tuple[LifecycleRecord, ...] = ()
    # A change that has been derived but not yet sustained for HOLD_DAYS.
    pending_state: LifecycleState | None = None
    pending_since: date | None = None

    @property
    def current(self) -> LifecycleRecord | None:
        return self.records[-1] if self.records else None

    def state_at(self, as_of: date) -> LifecycleState | None:
        """The state that was in force on a past date."""
        known = [record for record in self.records if record.valid_from <= as_of]
        return known[-1].state if known else None

    def started(self, state: LifecycleState, on: date, reason: str) -> Self:
        """The first record of an entity whose state is given, not yet derived."""
        return self._appended(
            LifecycleRecord(
                state=state,
                valid_from=on,
                rule_id=reason,
                rule_version=RULE_VERSION,
                flags=(LifecycleFlag.BOOTSTRAP,),
            )
        )

    def advance(
        self,
        availability: Availability,
        activity: ActivityEvidence,
        as_of: date,
        override: LifecycleOverride | None = None,
    ) -> Self:
        """Re-derive the state as of a date and append a record only if something changed."""
        derived = derive_state(availability, activity, as_of)
        previous = self.current
        state, rule_id, flags = derived.state, derived.rule_id, derived.flags

        if state is None:
            # Conflicting or missing evidence: the previous valid state stands.
            if previous is None:
                fallback = LifecycleState.RELEASED
                if availability.first_available is None or availability.first_available > as_of:
                    fallback = LifecycleState.PRE_RELEASE
                state = fallback
            else:
                state = previous.state
        awaiting_evidence = (
            activity.content_events_last_180d is None
            or activity.active_day_fraction_last_180d is None
        )
        if (
            override is not None
            and awaiting_evidence
            and state in (LifecycleState.RELEASED, *OVERRIDABLE_STATES)
        ):
            state, rule_id = override.state, "labelled_override"
            flags = (*flags, LifecycleFlag.LABELLED_OVERRIDE)

        candidate = LifecycleRecord(
            state=state,
            valid_from=as_of,
            rule_id=rule_id,
            rule_version=RULE_VERSION,
            flags=flags,
            first_available=availability.first_available,
        )
        if previous is None:
            return self._appended(candidate)
        if state == previous.state:
            unchanged = (previous.flags, previous.rule_version, previous.first_available) == (
                flags,
                RULE_VERSION,
                availability.first_available,
            )
            cleared = self.model_copy(update={"pending_state": None, "pending_since": None})
            return cleared if unchanged else cleared._appended(candidate)
        if state in _HELD_STATES and previous.state in _HELD_STATES:
            if self.pending_state is not state or self.pending_since is None:
                return self.model_copy(update={"pending_state": state, "pending_since": as_of})
            if (as_of - self.pending_since).days < HOLD_DAYS:
                return self
        return self._appended(candidate)

    def _appended(self, record: LifecycleRecord) -> Self:
        return type(self)(canonical_game_id=self.canonical_game_id, records=(*self.records, record))
