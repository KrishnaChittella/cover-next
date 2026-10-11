"""JSON Schema export for the public artifact contract, generated from the typed models."""

import json
from pathlib import Path

from metaspike.publishing.artifacts import (
    ContractModel,
    CurrentArtifact,
    GameArtifact,
    Manifest,
    MethodologyArtifact,
    RadarArtifact,
    SourceHealthArtifact,
)

ARTIFACT_MODELS: dict[str, type[ContractModel]] = {
    "manifest": Manifest,
    "current": CurrentArtifact,
    "game": GameArtifact,
    "source-health": SourceHealthArtifact,
    "methodology": MethodologyArtifact,
    "radar": RadarArtifact,
}


def schema_text(model: type[ContractModel]) -> str:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **model.model_json_schema(mode="serialization"),
    }
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def schema_path(directory: Path, name: str) -> Path:
    return directory / f"{name}.schema.json"


def export_schemas(directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, model in ARTIFACT_MODELS.items():
        path = schema_path(directory, name)
        path.write_text(schema_text(model), encoding="utf-8")
        written.append(path)
    return written


def stale_schemas(directory: Path) -> list[str]:
    """Names of checked-in schemas that no longer match the models."""
    return [
        name
        for name, model in ARTIFACT_MODELS.items()
        if not schema_path(directory, name).is_file()
        or schema_path(directory, name).read_text(encoding="utf-8") != schema_text(model)
    ]
