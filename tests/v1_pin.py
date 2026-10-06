"""The baseline_rules_v1 reproduction contract. Shared by the tests that enforce it.

WHAT IS PINNED
A fixed synthetic history is folded through the engine. Its 720 evaluations and its
final BaselineState were recorded once (on macOS) and are stored as the reference.
Three things are pinned, and each catches a different kind of drift:

1. The reference itself: SHA-256 of the stored evaluations and of the stored state.
   The reference cannot be regenerated or edited without the test noticing.
2. The skeleton of the live output: SHA-256 of the output with every float replaced
   by a marker. Everything that is not a float is in it exactly: strings, enums,
   booleans, nulls, integer counts, timestamps, reason codes, phases, availability,
   ordering and structure. This hash is bit-identical on every platform.
3. The floats of the live output: each one compared with its stored reference value,
   within the tolerance below.

WHY FLOATS ARE NOT HASHED
The first version of this pin hashed floats rounded to 12 significant digits and
claimed that would be stable across platforms. It was not. C maths libraries differ
in the last bit of log1p and expm1, and subtraction of nearly equal numbers amplifies
that: a robust z close to zero differed between macOS and Linux by 5e-11 relative.
More fundamentally, ANY rounding has boundaries, and a difference of one bit can
carry a value across one. No rounded-float hash of live output is safe, at any
precision. So floats are compared as numbers, and the hash pins what is exact.

MEASURED, NOT GUESSED (macOS arm64 libm against Linux glibc 2.36, arm64 and x86-64)
- Linux arm64 and Linux x86-64 produced bit-identical output.
- macOS against Linux: 461 of 720 evaluations differ, in floats only. 0 differences
  in any string, enum, boolean, null, integer, timestamp, key or list length.
- Largest absolute difference 2.5e-11 (on levels near 14,000; 1.8e-15 relative).
- Largest relative difference 5.3e-11 (a robust z of -0.00067; 3.6e-14 absolute).
- The stored reference adds its own rounding of up to 5e-12 relative.

THE TOLERANCE
    |live - reference| <= 1e-12 + 1e-9 * max(|live|, |reference|)
Against every float in every measured pairing (71,613 comparisons) the largest
difference was 2.1% of this tolerance: 47 times headroom. It is in turn 1,000 times
tighter than a one-part-per-million change in any feature, which no feature needs
resolved. A real change to baseline_rules_v1 moves values by far more than 1e-9
relative, and most also change the skeleton.
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
SIGNIFICANT_DIGITS = 12  # precision the stored reference was written at
SUBJECT = "v1-reproduction-pin"

FLOAT_REL_TOLERANCE = 1e-9
FLOAT_ABS_TOLERANCE = 1e-12
# What was measured between macOS and Linux when the tolerance was chosen.
MEASURED_MAX_RELATIVE_DELTA = 5.4e-11
MEASURED_MAX_ABSOLUTE_DELTA = 2.6e-11
MEASURED_WORST_FRACTION_OF_TOLERANCE = 0.0213
# A relative change this large in any float must always fail the gate.
MATERIAL_RELATIVE_CHANGE = 1e-6
FLOAT_MARKER = "<float>"


def steam_v1() -> SignalFeatureConfig:
    return feature_config(
        "steam_current_players", "concurrent_players", Granularity.HOURLY, "baseline_rules_v1"
    )


def canonical(value: Any) -> Any:
    """How the stored reference was written: floats at 12 significant digits.

    Used only to check the reference's own hash. Live output is never rounded and
    hashed; see the module docstring for why that cannot be made portable.
    """
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


def read_evaluation_records() -> list[dict[str, Any]]:
    return [json.loads(line) for line in read_evaluations()]


def read_state_record() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return loaded


def live_records(evaluations: tuple[BucketEvaluation, ...]) -> list[dict[str, Any]]:
    """The live evaluations as plain JSON values, at full precision."""
    return [evaluation.model_dump(mode="json") for evaluation in evaluations]


def skeleton(value: Any) -> Any:
    """The value with every float replaced by a marker. Everything else is kept exactly."""
    if isinstance(value, float):
        return FLOAT_MARKER
    if isinstance(value, dict):
        return {key: skeleton(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [skeleton(item) for item in value]
    return value


def skeleton_digest(value: Any) -> str:
    return digest(json.dumps(skeleton(value), sort_keys=True, separators=(",", ":")))


def floats_agree(reference: float, live: float) -> bool:
    limit = FLOAT_ABS_TOLERANCE + FLOAT_REL_TOLERANCE * max(abs(reference), abs(live))
    return abs(live - reference) <= limit


def differences(reference: Any, live: Any, path: str = "") -> list[str]:
    """Every way `live` departs from `reference`. An empty list means it reproduces.

    Floats may differ within the tolerance. Nothing else may differ at all: not a
    type, a key, a length, an integer, a string, a boolean or a null.
    """
    here = path or "<root>"
    if isinstance(reference, float) or isinstance(live, float):
        if type(reference) is not float or type(live) is not float:
            return [f"{here}: {reference!r} became {live!r} (a float is no longer a float)"]
        if not floats_agree(reference, live):
            return [f"{here}: {reference!r} became {live!r} (delta {abs(live - reference):.3e})"]
        return []
    if type(reference) is not type(live):
        return [f"{here}: {reference!r} became {live!r}"]
    if isinstance(reference, dict):
        found = []
        if reference.keys() != live.keys():
            found.append(f"{here}: keys {sorted(reference)} became {sorted(live)}")
        for key in reference.keys() & live.keys():
            found += differences(reference[key], live[key], f"{path}.{key}" if path else key)
        return found
    if isinstance(reference, list):
        if len(reference) != len(live):
            return [f"{here}: {len(reference)} items became {len(live)}"]
        found = []
        for index, (old, new) in enumerate(zip(reference, live, strict=True)):
            found += differences(old, new, f"{path}[{index}]")
        return found
    return [] if reference == live else [f"{here}: {reference!r} became {live!r}"]
