"""Historical validation as published: the report turned into contract views.

A boundary, like `mapping.py` and `event_views.py`. The report's models never reach an
artifact directly. Values are published exactly as computed; nothing is rounded here.
"""

from collections.abc import Mapping

from metaspike.publishing.artifacts import (
    VALIDATION_SCOPE_NOTE,
    OutcomeDefinitionView,
    ParameterView,
    TermView,
    ValidationArtifact,
    ValidationReportView,
)
from metaspike.validation.report import ValidationReport
from metaspike.validation.rules import OutcomeRules, ValidationProtocol


def outcome_definition(rules: OutcomeRules) -> OutcomeDefinitionView:
    return OutcomeDefinitionView(
        version=rules.version,
        terms=(
            TermView(
                name="realized_uplift_72h (primary)",
                formula=f"mean(x[t+1] .. x[t+{rules.primary_days}]) / baseline_median_28d[t] - 1",
                rationale=(
                    "Did demand after the recommendation exceed the game's normal level as "
                    "it was known when the recommendation was made? Positive: above it. "
                    "Exactly 0: equal to it. Negative: below it. Never clipped. "
                    + rules.primary_rationale
                ),
            ),
            TermView(
                name="movement_24h",
                formula=f"mean(x[t+1] .. x[t+{rules.movement_days}]) / avg_3d[t] - 1",
                rationale=rules.movement_rationale,
            ),
            TermView(
                name="persistence_7d",
                formula=(
                    f"count(x[t+k] > baseline_median_28d[t] for k in 1..{rules.persistence_days})"
                    f" / {rules.persistence_days}"
                ),
                rationale=rules.persistence_rationale,
            ),
            TermView(
                name="lead_time_days",
                formula=(
                    f"smallest k in 1..{rules.peak_search_days} with x[t+k] > x[t+k-1] and "
                    "x[t+k] >= x[t+k+1] and x[t+k] > baseline_median_28d[t]"
                ),
                rationale=rules.peak_rationale,
            ),
        ),
        parameters=(
            ParameterView(
                name="hit_threshold",
                value=f"{rules.hit_threshold:g}",
                rationale=rules.hit_rationale,
            ),
            ParameterView(
                name="downside_threshold",
                value=f"{rules.downside_threshold:g}",
                rationale=rules.downside_rationale,
            ),
        ),
        unavailable=(
            TermView(
                name="future_incomplete",
                formula="any day of the measure's forward window is missing or not yet recorded",
                rationale="A missing day is never filled with zero and never averaged around.",
            ),
            TermView(
                name="no_reference",
                formula="the snapshot recorded no baseline, or a baseline of 0",
                rationale="There is nothing to compare the future with.",
            ),
            TermView(
                name="not_scored_at_snapshot",
                formula="the game had no score at the snapshot",
                rationale="No recommendation was made, so there is none to hold to account.",
            ),
        ),
    )


def validation_artifact(
    report: ValidationReport | None,
    rules: OutcomeRules,
    protocol: ValidationProtocol,
    header: Mapping[str, object],
) -> ValidationArtifact:
    """The artifact for a run. With no report it says so, and carries no number."""
    view = (
        None
        if report is None
        else ValidationReportView.model_validate(report.model_dump(mode="json"))
    )
    return ValidationArtifact.model_validate(
        {
            **header,
            "scope_note": VALIDATION_SCOPE_NOTE,
            "availability": "not_run" if view is None else "available",
            "protocol_version": protocol.version,
            "outcome_definition": outcome_definition(rules),
            "report": view,
        }
    )
