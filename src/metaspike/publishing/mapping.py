"""The only place internal domain models are turned into published views.

Each mapper copies exactly the fields the view declares. A field added to a domain
model is therefore NOT published until a view opts in, while a field the view needs
that the domain no longer provides fails loudly here instead of at a consumer.
"""

from typing import Any

from pydantic import BaseModel

from metaspike.domain.events import GameEvent
from metaspike.domain.metrics import MetricDefinition
from metaspike.domain.scores import ConfidenceResult, Explanation, ScoreResult
from metaspike.domain.source_policy import SourcePolicy
from metaspike.features.attention import AttentionFeatures, FeatureDefinition
from metaspike.publishing.artifacts import (
    ConfidenceFactorsView,
    ConfidenceView,
    ContractModel,
    DisplayRulesView,
    EventView,
    ExplanationView,
    FeatureDefinitionView,
    FeaturesView,
    FieldRuleView,
    MetricView,
    QuotaView,
    RateLimitView,
    ReasonView,
    ScoreComponentsView,
    ScoreView,
    SourceOperationsView,
    SourcePolicyView,
    StageClearanceView,
    TermView,
)
from metaspike.scoring.v0 import TermDefinition

FEATURE_DECIMALS = 4


def _project[ViewT: ContractModel](source: BaseModel, view: type[ViewT], **overrides: Any) -> ViewT:
    """Build `view` from the same-named fields of `source`, ignoring any others."""
    wanted = set(view.model_fields) - set(overrides)
    return view.model_validate({**source.model_dump(mode="json", include=wanted), **overrides})


def score_view(score: ScoreResult) -> ScoreView:
    components = score.components
    return _project(
        score,
        ScoreView,
        components=None if components is None else _project(components, ScoreComponentsView),
    )


def confidence_view(confidence: ConfidenceResult) -> ConfidenceView:
    return _project(
        confidence, ConfidenceView, factors=_project(confidence.factors, ConfidenceFactorsView)
    )


def features_view(features: AttentionFeatures) -> FeaturesView:
    """Published features, rounded to the documented precision."""
    view = _project(features, FeaturesView)
    rounded = {
        name: round(value, FEATURE_DECIMALS) for name, value in view if isinstance(value, float)
    }
    return view.model_copy(update=rounded)


def explanation_view(explanation: Explanation) -> ExplanationView:
    return _project(
        explanation,
        ExplanationView,
        reasons=tuple(_project(reason, ReasonView) for reason in explanation.reasons),
    )


def event_view(event: GameEvent) -> EventView:
    return _project(event, EventView)


def term_view(term: TermDefinition) -> TermView:
    return _project(term, TermView)


def feature_definition_view(definition: FeatureDefinition) -> FeatureDefinitionView:
    return _project(definition, FeatureDefinitionView)


def metric_view(metric: MetricDefinition) -> MetricView:
    return _project(metric, MetricView)


def policy_view(policy: SourcePolicy) -> SourcePolicyView:
    operations = policy.operations
    return _project(
        policy,
        SourcePolicyView,
        operations=_project(
            operations,
            SourceOperationsView,
            rate_limit=_project(operations.rate_limit, RateLimitView),
            quota=None if operations.quota is None else _project(operations.quota, QuotaView),
        ),
        stage_clearance=tuple(
            _project(clearance, StageClearanceView) for clearance in policy.stage_clearance
        ),
        display=_project(policy.display, DisplayRulesView),
        field_rules=tuple(_project(rule, FieldRuleView) for rule in policy.field_rules),
    )
