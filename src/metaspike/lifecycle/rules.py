"""Lifecycle rules, version 1.

Lifecycle is a pure function of catalogue metadata, delivered content events, raw
activity and the date. The Opportunity Score is never an input.

    PRE_RELEASE     no public availability date in the past
    LAUNCH_WINDOW   the first 30 days after first public availability on any
                    platform. Early access counts as public availability.
    LIVE_SERVICE    at least 3 delivered content events in the trailing 180 days
    DORMANT         no content event for 365 days and almost no activity for 180
    EVERGREEN       first available at least 3 years ago, with sustained activity
    RELEASED        the default once the launch window has passed

A full release after early access, and a later release on another platform, are
events. Neither restarts the launch window.

The numeric thresholds for LIVE_SERVICE, EVERGREEN and DORMANT are VERSIONED INITIAL
CLASSIFICATION RULES, chosen before any activity or event data existed. They are not
domain truth. Recalibrating them means a new RULE_VERSION; history written under an
older version keeps that version and is never rewritten.
"""

import calendar
from datetime import date, timedelta
from enum import StrEnum

from metaspike.domain.games import LifecycleState
from metaspike.domain.model import FrozenModel
from metaspike.domain.source_records import DatePrecision, ReleaseDate, SourceRecord

RULE_VERSION = "lifecycle_rules_v1"

LAUNCH_WINDOW_DAYS = 30
LIVE_SERVICE_MIN_EVENTS = 3
CONTENT_EVENT_WINDOW_DAYS = 180
EVERGREEN_MIN_AGE_DAYS = 3 * 365
EVERGREEN_MIN_ACTIVE_FRACTION = 0.8
DORMANT_NO_EVENT_DAYS = 365
DORMANT_MAX_ACTIVE_FRACTION = 0.05
# States among which a change must be sustained before it is accepted.
HOLD_DAYS = 14
# Two records of one game disagreeing by more than this about its first day conflict.
DATE_CONFLICT_DAYS = 7

_UNRELEASED_STATUSES = frozenset({"rumored"})
_CANCELLED_STATUSES = frozenset({"cancelled"})


class LifecycleFlag(StrEnum):
    LIFECYCLE_CONFLICT = "lifecycle_conflict"
    EVIDENCE_MISSING = "lifecycle_evidence_missing"
    LABELLED_OVERRIDE = "labelled_override"
    BOOTSTRAP = "bootstrap"


class Availability(FrozenModel):
    """When a game first became publicly playable, as far as its records say."""

    first_available: date | None = None
    # A release is recorded but none has a date yet.
    announced_without_date: bool = False
    said_to_be_unreleased: bool = False
    cancelled: bool = False
    conflict: str | None = None


class ActivityEvidence(FrozenModel):
    """Evidence that only later phases can supply. None means 'not available yet'."""

    content_events_last_180d: int | None = None
    days_since_last_content_event: int | None = None
    active_day_fraction_last_180d: float | None = None
    service_ended: bool = False


class DerivedState(FrozenModel):
    # None when the evidence cannot support any state; the previous state then stands.
    state: LifecycleState | None
    rule_id: str
    flags: tuple[LifecycleFlag, ...] = ()


def effective_date(release: ReleaseDate) -> date | None:
    """The day a release counts from. An imprecise date counts as the END of its period."""
    start = release.starts_on
    if start is None:
        return None
    if release.precision is DatePrecision.DAY:
        return start
    if release.precision is DatePrecision.MONTH:
        return start.replace(day=calendar.monthrange(start.year, start.month)[1])
    if release.precision is DatePrecision.QUARTER:
        last_month = ((start.month - 1) // 3) * 3 + 3
        return date(start.year, last_month, calendar.monthrange(start.year, last_month)[1])
    return date(start.year, 12, 31)


def first_public_availability(records: tuple[SourceRecord, ...]) -> Availability:
    """Earliest release across every platform and region, early access included."""
    per_record: list[date] = []
    undated = False
    for record in records:
        dates = [d for d in (effective_date(r) for r in record.release_dates) if d is not None]
        if dates:
            per_record.append(min(dates))
        elif record.release_dates:
            undated = True
    statuses = {record.status for record in records if record.status}
    conflict = None
    if len(per_record) > 1 and (max(per_record) - min(per_record)).days > DATE_CONFLICT_DAYS:
        conflict = (
            f"records disagree about the first release by "
            f"{(max(per_record) - min(per_record)).days} days"
        )
    return Availability(
        first_available=min(per_record) if per_record else None,
        announced_without_date=undated and not per_record,
        said_to_be_unreleased=bool(statuses & _UNRELEASED_STATUSES),
        cancelled=bool(statuses & _CANCELLED_STATUSES),
        conflict=conflict,
    )


def derive_state(
    availability: Availability, activity: ActivityEvidence, as_of: date
) -> DerivedState:
    first = availability.first_available
    if availability.conflict is not None:
        return DerivedState(
            state=None, rule_id="conflicting_dates", flags=(LifecycleFlag.LIFECYCLE_CONFLICT,)
        )
    if first is not None and first <= as_of and availability.said_to_be_unreleased:
        return DerivedState(
            state=None,
            rule_id="status_contradicts_date",
            flags=(LifecycleFlag.LIFECYCLE_CONFLICT,),
        )
    if first is None:
        if availability.announced_without_date or availability.said_to_be_unreleased:
            return DerivedState(state=LifecycleState.PRE_RELEASE, rule_id="no_release_date_yet")
        return DerivedState(
            state=None, rule_id="no_release_evidence", flags=(LifecycleFlag.EVIDENCE_MISSING,)
        )
    if first > as_of:
        return DerivedState(state=LifecycleState.PRE_RELEASE, rule_id="first_release_in_future")
    if as_of < first + timedelta(days=LAUNCH_WINDOW_DAYS):
        return DerivedState(state=LifecycleState.LAUNCH_WINDOW, rule_id="within_launch_window")

    events = activity.content_events_last_180d
    active = activity.active_day_fraction_last_180d
    if events is not None and events >= LIVE_SERVICE_MIN_EVENTS and not activity.service_ended:
        return DerivedState(state=LifecycleState.LIVE_SERVICE, rule_id="recurring_content_events")
    since = activity.days_since_last_content_event
    no_recent_event = events == 0 and (since is None or since >= DORMANT_NO_EVENT_DAYS)
    if no_recent_event and active is not None and active <= DORMANT_MAX_ACTIVE_FRACTION:
        return DerivedState(state=LifecycleState.DORMANT, rule_id="no_events_and_no_activity")
    old_enough = (as_of - first).days >= EVERGREEN_MIN_AGE_DAYS
    if old_enough and active is not None and active >= EVERGREEN_MIN_ACTIVE_FRACTION:
        return DerivedState(state=LifecycleState.EVERGREEN, rule_id="old_and_still_active")
    return DerivedState(state=LifecycleState.RELEASED, rule_id="released_default")
