"""How the baseline_rules_v1 reproduction pin is computed. Shared by the test and by
the one-off script that wrote the fixture.

Floats are compared at 12 significant digits. That is far tighter than any feature
needs and loose enough that a different C maths library (macOS and Linux do not always
agree on the last bit of log1p) does not raise a false alarm.
"""

import gzip
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from metaspike.domain.timeseries import Granularity
from metaspike.features.config import SignalFeatureConfig, feature_config
from metaspike.features.engine import (
    BaselineState,
    BucketEvaluation,
    contiguous,
    initial_state,
    run,
)

PIN_DIRECTORY = Path(__file__).parent / "fixtures" / "baseline_rules_v1"
PIN_FILE = PIN_DIRECTORY / "pin.json"
STATE_FILE = PIN_DIRECTORY / "state.json"
EVALUATIONS_FILE = PIN_DIRECTORY / "evaluations.jsonl.gz"
SIGNIFICANT_DIGITS = 12
SUBJECT = "v1-reproduction-pin"


def steam_v1() -> SignalFeatureConfig:
    return feature_config(
        "steam_current_players", "concurrent_players", Granularity.HOURLY, "baseline_rules_v1"
    )


def canonical(value: Any) -> Any:
    """The same structure with every float rounded to the pinned precision."""
    if isinstance(value, bool) or value is None or isinstance(value, int | str):
        return value
    if isinstance(value, float):
        return float(f"{value:.{SIGNIFICANT_DIGITS}g}")
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [canonical(item) for item in value]
    raise TypeError(f"unexpected {type(value).__name__} in a pinned record")


def dumps(value: Any) -> str:
    return json.dumps(canonical(value), sort_keys=True, separators=(",", ":"))


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def reproduce(
    start: datetime, values: list[float | None]
) -> tuple[BaselineState, tuple[BucketEvaluation, ...]]:
    config = steam_v1()
    return run(config, initial_state(config, SUBJECT), contiguous(start, values, config))


def state_text(state: BaselineState) -> str:
    return dumps(state.model_dump(mode="json"))


def evaluation_lines(evaluations: tuple[BucketEvaluation, ...]) -> list[str]:
    return [dumps(evaluation.model_dump(mode="json")) for evaluation in evaluations]


def write_evaluations(lines: list[str]) -> None:
    with EVALUATIONS_FILE.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as out:
        out.write(("\n".join(lines) + "\n").encode("utf-8"))


def read_evaluations() -> list[str]:
    with gzip.open(EVALUATIONS_FILE, "rt", encoding="utf-8") as stream:
        return stream.read().splitlines()
