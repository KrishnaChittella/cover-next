"""The published contract must not move when internal models change."""

import ast
import importlib
import json
import pkgutil
from enum import StrEnum
from pathlib import Path
from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError

import metaspike
from metaspike.domain.events import EventType
from metaspike.domain.games import LifecycleState, RelationshipType
from metaspike.domain.model import FrozenModel
from metaspike.domain.scores import ConfidenceBand, ScoreResult, ScoreStatus, UnscoredReason
from metaspike.features.attention import AttentionFeatures, compute_attention_features
from metaspike.publishing import artifacts, mapping
from metaspike.publishing.artifacts import (
    ConfidenceBandView,
    ContractModel,
    EventTypeView,
    LifecycleStateView,
    RelationshipTypeView,
    ScoreStatusView,
    TermView,
    UnscoredReasonView,
)
from metaspike.publishing.schemas import ARTIFACT_MODELS, schema_text
from metaspike.scoring.v0 import score_v0
from metaspike.sources.wikimedia import WIKIMEDIA_PAGEVIEWS_POLICY
from tests.conftest import run_fixture_pipeline, series_of, spiking


def all_subclasses(base: type) -> set[type]:
    found: set[type] = set()
    subclass: type
    for subclass in base.__subclasses__():
        found |= {subclass} | all_subclasses(subclass)
    return found


def import_everything() -> None:
    for module in pkgutil.walk_packages(metaspike.__path__, "metaspike."):
        importlib.import_module(module.name)


def test_the_contract_module_imports_nothing_from_metaspike() -> None:
    tree = ast.parse(Path(artifacts.__file__).read_text())
    imported = [
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    ]
    assert imported and not [name for name in imported if name and name.startswith("metaspike")]


def test_no_contract_model_is_or_contains_a_domain_model() -> None:
    import_everything()
    domain_models = all_subclasses(FrozenModel)
    contract_models = all_subclasses(ContractModel)
    assert len(domain_models) > 15 and len(contract_models) > 30
    assert not domain_models & contract_models
    assert not any(issubclass(model, FrozenModel) for model in contract_models)

    domain_names = {model.__name__ for model in domain_models}
    for name, model in ARTIFACT_MODELS.items():
        definitions = set(json.loads(schema_text(model)).get("$defs", {}))
        assert not definitions & domain_names, f"{name} schema exposes a domain model"


def test_a_field_added_to_a_domain_model_is_not_published() -> None:
    class ExperimentalFeatures(AttentionFeatures):
        internal_debug_ratio: float = 0.123
        experimental_signal: str = "do not publish"

    class ExperimentalScore(ScoreResult):
        calibration_bucket: int = 7

    features = compute_attention_features(series_of(spiking()))
    extended = ExperimentalFeatures(**features.model_dump())
    assert mapping.features_view(extended) == mapping.features_view(features)
    assert "internal_debug_ratio" not in mapping.features_view(extended).model_dump()

    score = score_v0(features)
    extended_score = ExperimentalScore(**score.model_dump())
    assert mapping.score_view(extended_score) == mapping.score_view(score)
    assert "calibration_bucket" not in mapping.score_view(extended_score).model_dump()


def test_a_field_the_contract_needs_cannot_silently_disappear() -> None:
    class TermWithoutRationale(BaseModel):
        name: str = "significance"
        formula: str = "x / 7"

    with pytest.raises(ValidationError, match="rationale"):
        mapping._project(TermWithoutRationale(), TermView)


def test_views_carry_the_same_values_as_the_models_they_are_built_from() -> None:
    features = compute_attention_features(series_of(spiking()))
    score = score_v0(features)
    assert mapping.score_view(score).model_dump(mode="json") == score.model_dump(mode="json")

    published = mapping.features_view(features).model_dump()
    assert published["latest_views"] == 400 and published["lift_ratio"] == 3.0
    assert published["robust_z_score"] == round(features.robust_z_score or 0, 4)
    assert published["flags"] == ()

    policy = mapping.policy_view(WIKIMEDIA_PAGEVIEWS_POLICY)
    assert policy.model_dump(mode="json") == WIKIMEDIA_PAGEVIEWS_POLICY.model_dump(mode="json")


@pytest.mark.parametrize(
    ("domain_enum", "contract_values"),
    [
        (LifecycleState, LifecycleStateView),
        (RelationshipType, RelationshipTypeView),
        (ScoreStatus, ScoreStatusView),
        (UnscoredReason, UnscoredReasonView),
        (ConfidenceBand, ConfidenceBandView),
        (EventType, EventTypeView),
    ],
)
def test_every_domain_value_has_an_explicit_place_in_the_contract(
    domain_enum: type[StrEnum], contract_values: object
) -> None:
    """A new domain value forces a deliberate contract decision instead of leaking out."""
    assert {member.value for member in domain_enum} == set(get_args(contract_values))


def test_every_artifact_and_schema_is_marked_internal(artifacts_dir: Path) -> None:
    run_fixture_pipeline(artifacts_dir)
    documents = [
        json.loads(path.read_text())
        for path in artifacts_dir.rglob("*.json")
        if "versions" not in path.relative_to(artifacts_dir).parts
    ]
    # manifest, current, health, methodology, radar, validation; then the games
    assert len(documents) == 6 + 15
    assert {document["contract_status"] for document in documents} == {"internal"}

    for name, model in ARTIFACT_MODELS.items():
        schema = json.loads(schema_text(model))
        assert "contract_status" in schema["required"], name
        assert schema["properties"]["contract_status"]["const"] == "internal"
        assert "INTERNAL" in schema["properties"]["contract_status"]["description"]
    assert "INTERNAL / NON-BINDING" in (artifacts.__doc__ or "")
    assert artifacts.SCHEMA_VERSION == "1.2.0"  # 1.1: events and radar; 1.2: validation
