"""baseline_rules_v1 must reproduce, forever, on every platform.

A fixed synthetic history is folded through the engine and compared with the output
recorded when v1 was accepted. If this fails, v1 has drifted. The remedy is to fix the
code, never to regenerate the reference: a different calculation is a new rule version.

How the comparison works, and why floats are compared as numbers instead of hashed,
is in tests/v1_pin.py.
"""

import copy
import json
import math
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest

from metaspike.domain import baseline_rules as rules
from metaspike.features.config import feature_config, register_config
from metaspike.features.engine import BaselineState, ChangePhase, contiguous, initial_state, run
from tests import v1_pin as pin

# The stored reference. Changing the reference changes these, and that is the point.
PINNED_EVALUATIONS = "0267aa1bd812d9ed4e463c4482e18b1acba4453c9a9339581a5f7e6e859757f4"
PINNED_STATE = "dd54c938d939ed10c047301ee752822d1558ab88b8e5c233b4b1743ff51c96f3"
# Everything in the LIVE output that is not a float. Identical on every platform.
PINNED_EVALUATIONS_SKELETON = "177a89c374b646cd237b5011235c4b26dd530ba5dbe39bb2824318a427eb44d3"
PINNED_STATE_SKELETON = "52fe9f9d4f4ca0acdbe0760c69916b4896ed6354886ed7977bcfd836dc824c35"
PINNED_FINGERPRINT = "f67603b40400a626"

Live = tuple[dict[str, Any], list[dict[str, Any]], BaselineState]


@pytest.fixture(scope="module")
def recorded() -> dict[str, object]:
    loaded: dict[str, object] = json.loads(pin.PIN_FILE.read_text(encoding="utf-8"))
    return loaded


@pytest.fixture(scope="module")
def live(recorded: dict[str, object]) -> Live:
    """This platform's own reproduction, at full precision."""
    values = recorded["values"]
    assert isinstance(values, list) and isinstance(recorded["start"], str)
    state, evaluations = pin.reproduce(datetime.fromisoformat(recorded["start"]), values)
    return state.model_dump(mode="json"), pin.live_records(evaluations), state


@pytest.fixture(scope="module")
def reference() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return pin.read_state_record(), pin.read_evaluation_records()


# --- the reference is the one that was accepted ------------------------------------------


def test_the_stored_reference_is_the_one_that_was_accepted(recorded: dict[str, object]) -> None:
    # The hashes live in this file as well as in the fixture, so that changing one
    # without the other is itself a failure.
    assert recorded["evaluations_sha256"] == PINNED_EVALUATIONS
    assert recorded["state_sha256"] == PINNED_STATE
    assert recorded["config_fingerprint"] == PINNED_FINGERPRINT
    assert recorded["rule_version"] == rules.BASELINE_RULES_VERSION == "baseline_rules_v1"
    assert (recorded["buckets"], recorded["significant_digits"]) == (720, 12)
    assert pin.digest("\n".join(pin.read_evaluations())) == PINNED_EVALUATIONS
    assert pin.digest(pin.STATE_FILE.read_text(encoding="utf-8").rstrip("\n")) == PINNED_STATE
    assert len(pin.read_evaluations()) == 720  # all of them, none dropped


def test_the_v1_configuration_has_not_moved(recorded: dict[str, object]) -> None:
    assert pin.steam_v1().fingerprint == PINNED_FINGERPRINT
    constants = recorded["constants"]
    assert isinstance(constants, dict) and len(constants) == 45
    for name, value in constants.items():
        current = getattr(rules, name)
        current = list(current) if isinstance(current, tuple) else current
        assert current == value, f"baseline_rules.{name} changed"
    assert {name for name in dir(rules) if name.isupper()} == set(constants)
    with pytest.raises(ValueError, match="needs a new rule version"):
        register_config(pin.steam_v1().model_copy(update={"scale_floor": 0.06}))
    assert (
        feature_config(
            "steam_current_players", "concurrent_players", pin.steam_v1().granularity
        ).scale_floor
        == 0.05
    )


# --- the live output reproduces ----------------------------------------------------------


def test_everything_that_is_not_a_float_reproduces_exactly(live: Live) -> None:
    """Strings, enums, booleans, nulls, counts, timestamps, reasons, phases, order."""
    state, evaluations, _ = live
    assert pin.skeleton_digest(evaluations) == PINNED_EVALUATIONS_SKELETON
    assert pin.skeleton_digest(state) == PINNED_STATE_SKELETON


def test_the_reference_has_the_same_skeleton_as_the_live_output(
    reference: tuple[dict[str, Any], list[dict[str, Any]]],
) -> None:
    state, evaluations = reference
    assert pin.skeleton_digest(evaluations) == PINNED_EVALUATIONS_SKELETON
    assert pin.skeleton_digest(state) == PINNED_STATE_SKELETON


def test_every_v1_evaluation_reproduces(
    live: Live, reference: tuple[dict[str, Any], list[dict[str, Any]]]
) -> None:
    _, evaluations, _ = live
    _, expected = reference
    assert len(evaluations) == len(expected) == 720
    drift = pin.differences(expected, evaluations)
    assert drift == [], f"{len(drift)} value(s) no longer reproduce under v1; first: {drift[:5]}"


def test_the_v1_baseline_state_reproduces(
    live: Live, reference: tuple[dict[str, Any], list[dict[str, Any]]]
) -> None:
    state, _, model = live
    expected, _ = reference
    drift = pin.differences(expected, state)
    assert drift == [], f"{len(drift)} state value(s) no longer reproduce; first: {drift[:5]}"
    # The stored state is a real, loadable BaselineState, not only a hash.
    restored = BaselineState.model_validate_json(pin.STATE_FILE.read_text(encoding="utf-8"))
    assert restored.rule_version == "baseline_rules_v1"
    assert restored.config_fingerprint == PINNED_FINGERPRINT
    assert len(restored.window) == 720 and restored.subject == pin.SUBJECT
    assert model.config_fingerprint == PINNED_FINGERPRINT


def test_the_pinned_history_exercises_what_it_claims_to(
    recorded: dict[str, object], live: Live
) -> None:
    """A pin over a featureless series would prove little; this one is not featureless."""
    _, evaluations, state = live
    values = recorded["values"]
    assert isinstance(values, list) and values.count(None) == 6  # a gap and a lone hole
    assert recorded["confirmed_change_points"] == ["2026-06-26T02:00:00+00:00"]
    (change,) = state.cusum.history
    assert change.started_at.isoformat() == "2026-06-26T00:00:00+00:00"
    assert state.cusum.phase is ChangePhase.ACTIVE
    last = evaluations[-1]
    assert last["maturity"] == "mature" and last["seasonality_basis"] == "hour_of_week"
    for feature in ("log_lift", "robust_z", "velocity", "acceleration", "trend_long"):
        assert last[feature]["value"] is not None, feature
    assert {e["seasonality_basis"] for e in evaluations} == {
        None,
        "trailing_level",
        "hour_of_day",
        "hour_of_week",
    }
    assert sum(entry.excluded for entry in state.window) > 100  # the surge freeze is in it


# --- the tolerance is justified, and tight -----------------------------------------------


def test_the_float_tolerance_sits_between_platform_noise_and_meaningful_change() -> None:
    # Loose enough: at least 10 times the largest relative difference ever measured
    # between macOS and Linux, and the worst measured case used about 2% of it.
    assert pin.FLOAT_REL_TOLERANCE >= 10 * pin.MEASURED_MAX_RELATIVE_DELTA
    assert pin.MEASURED_WORST_FRACTION_OF_TOLERANCE < 0.05
    # Tight enough: 1,000 times smaller than a one-part-per-million change.
    assert pin.MATERIAL_RELATIVE_CHANGE / pin.FLOAT_REL_TOLERANCE >= 999
    assert (pin.FLOAT_REL_TOLERANCE, pin.FLOAT_ABS_TOLERANCE) == (1e-9, 1e-12)
    # The comparison is not a blanket approximation: it is per value, and exact for zero.
    assert pin.floats_agree(0.0, 0.0) and pin.floats_agree(14000.0, 14000.0 + 2.6e-11)
    assert pin.floats_agree(-0.000668709267071, -0.0006687092670354389)  # the worst real case
    assert not pin.floats_agree(0.0, 1e-9) and not pin.floats_agree(1.0, 1.0 + 1e-8)


def test_the_difference_that_failed_on_linux_is_within_tolerance_and_shows_why_hashing_broke() -> (
    None
):
    """Bucket 179 on the Linux runner: the same number, printed with one digit fewer."""
    macos, linux = 0.766472606589, 0.76647260659
    assert pin.floats_agree(macos, linux)
    assert pin.dumps(macos) != pin.dumps(linux)  # a rounded-and-hashed float cannot see that
    # One bit can carry a value across a rounding boundary, at any precision: walk up
    # from a value one representable float at a time until its rounded form changes.
    below = 0.12345678901249
    while pin.dumps(math.nextafter(below, 1.0)) == pin.dumps(below):
        below = math.nextafter(below, 1.0)
    above = math.nextafter(below, 1.0)
    assert above - below == math.ulp(below)  # adjacent floats: the smallest possible difference
    assert pin.dumps(below) != pin.dumps(above)  # and a rounded hash calls them different
    assert pin.floats_agree(below, above)  # while the numeric comparison does not


# --- meaningful drift still fails --------------------------------------------------------


def perturbed(records: list[dict[str, Any]], index: int, edit: Any) -> list[dict[str, Any]]:
    changed = copy.deepcopy(records)
    edit(changed[index])
    return changed


def scale(feature: str, factor: float) -> Any:
    def edit(record: dict[str, Any]) -> None:
        record[feature]["value"] *= factor

    return edit


def put(path: tuple[str, ...], value: Any) -> Any:
    def edit(record: dict[str, Any]) -> None:
        target = record
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return edit


@pytest.mark.parametrize(
    ("feature", "relative_change"),
    [
        ("log_lift", 1e-6),
        ("robust_z", 1e-6),
        ("velocity", 1e-6),
        ("acceleration", 1e-6),
        ("expected_level", 1e-6),
        ("anomaly_strength", 1e-6),
        ("trend_long", 1e-6),
        ("lift_fast", 1e-7),
        ("lift_slow", 1e-8),  # ten times the tolerance is already a failure
    ],
)
def test_a_material_change_to_one_float_fails_the_gate(
    live: Live,
    reference: tuple[dict[str, Any], list[dict[str, Any]]],
    feature: str,
    relative_change: float,
) -> None:
    _, evaluations, _ = live
    _, expected = reference
    drifted = perturbed(evaluations, 719, scale(feature, 1 + relative_change))
    found = pin.differences(expected, drifted)
    assert len(found) == 1 and found[0].startswith(f"[719].{feature}.value")
    # The skeleton cannot see a float change, which is exactly why floats are compared too.
    assert pin.skeleton_digest(drifted) == PINNED_EVALUATIONS_SKELETON


def test_a_change_at_the_scale_of_platform_noise_does_not_fail(
    live: Live, reference: tuple[dict[str, Any], list[dict[str, Any]]]
) -> None:
    _, evaluations, _ = live
    _, expected = reference
    noise = 1 + pin.MEASURED_MAX_RELATIVE_DELTA  # the largest difference seen across platforms
    for feature in ("log_lift", "robust_z", "velocity", "expected_level"):
        assert pin.differences(expected, perturbed(evaluations, 719, scale(feature, noise))) == []


@pytest.mark.parametrize(
    ("what", "index", "path", "value"),
    [
        ("an enum", 719, ("maturity",), "partial_baseline"),
        ("a seasonality basis", 719, ("seasonality_basis",), "hour_of_day"),
        ("a reason code", 5, ("log_lift", "reason"), "gap"),
        ("feature availability", 719, ("velocity", "value"), None),
        ("a state-machine phase", 719, ("change_point", "phase"), "none"),
        ("a change-point decision", 602, ("change_point", "confirmed_now"), False),
        (
            "a change-point start time",
            719,
            ("change_point", "current", "started_at"),
            "2026-06-26T01:00:00Z",
        ),
        ("a sample count", 719, ("samples",), 3),
        ("a percentile sample count", 719, ("percentile_samples",), 671),
        ("a timestamp", 719, ("bucket_start",), "2026-06-30T22:00:00Z"),
        ("the rule version", 0, ("rule_version",), "baseline_rules_v2"),
        ("an integer where a float belongs", 719, ("coverage",), 1),
    ],
)
def test_any_change_to_a_non_float_value_fails_the_gate(
    live: Live,
    reference: tuple[dict[str, Any], list[dict[str, Any]]],
    what: str,
    index: int,
    path: tuple[str, ...],
    value: Any,
) -> None:
    _, evaluations, _ = live
    _, expected = reference
    drifted = perturbed(evaluations, index, put(path, value))
    assert pin.differences(expected, drifted) != [], what
    assert pin.skeleton_digest(drifted) != PINNED_EVALUATIONS_SKELETON, what  # caught twice


def test_a_missing_reordered_or_extra_evaluation_fails_the_gate(
    live: Live, reference: tuple[dict[str, Any], list[dict[str, Any]]]
) -> None:
    _, evaluations, _ = live
    _, expected = reference
    for drifted in (
        evaluations[:-1],
        [*evaluations, evaluations[-1]],
        [evaluations[1], evaluations[0], *evaluations[2:]],
    ):
        assert pin.differences(expected, drifted) != []
        assert pin.skeleton_digest(drifted) != PINNED_EVALUATIONS_SKELETON


def test_drift_in_the_baseline_state_fails_the_gate(
    live: Live, reference: tuple[dict[str, Any], list[dict[str, Any]]]
) -> None:
    state, _, _ = live
    expected, _ = reference
    edits: list[Callable[[dict[str, Any]], Any]] = [
        lambda s: s["ewma"].update(fast=s["ewma"]["fast"] * (1 + 1e-6)),
        lambda s: s["cusum"].update(phase="none"),
        lambda s: s["window"][700].update(excluded=False),
        lambda s: s.update(gap_run=1),
        lambda s: s["ewma"].update(updates=s["ewma"]["updates"] + 1),
        lambda s: s["window"].pop(),
    ]
    for edit in edits:
        drifted = copy.deepcopy(state)
        edit(drifted)
        assert pin.differences(expected, drifted) != []


@pytest.mark.parametrize(
    "change",
    [
        {"scale_floor": 0.0501},
        {"residual_window_buckets": 167},
        {"min_residuals": 19},
        {"velocity_span_buckets": 4},
        {"anomaly_z_saturation": 7.001},
    ],
)
def test_a_real_change_to_a_v1_constant_fails_the_gate(
    recorded: dict[str, object],
    reference: tuple[dict[str, Any], list[dict[str, Any]]],
    change: dict[str, float],
) -> None:
    """The gate's actual purpose: a small edit to one rule constant must be caught."""
    _, expected = reference
    edited = pin.steam_v1().model_copy(update=change)
    values, start = recorded["values"], recorded["start"]
    assert isinstance(values, list) and isinstance(start, str)
    _, evaluations = run(
        edited,
        initial_state(edited, pin.SUBJECT),
        contiguous(datetime.fromisoformat(start), values, edited),
    )
    assert pin.differences(expected, pin.live_records(evaluations)) != []
    assert edited.fingerprint != PINNED_FINGERPRINT
