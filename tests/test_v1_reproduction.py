"""baseline_rules_v1 must reproduce, forever.

A fixed synthetic history is folded through the engine and compared with the output
that was recorded when v1 was accepted. If this test fails, v1 has drifted. The
remedy is to fix the code, never to regenerate the pin: a different calculation is a
new rule version.
"""

import json
from datetime import datetime

import pytest

from metaspike.domain import baseline_rules as rules
from metaspike.features.config import feature_config, register_config
from metaspike.features.engine import BaselineState, ChangePhase
from tests import v1_pin as pin

PINNED_EVALUATIONS = "0267aa1bd812d9ed4e463c4482e18b1acba4453c9a9339581a5f7e6e859757f4"
PINNED_STATE = "dd54c938d939ed10c047301ee752822d1558ab88b8e5c233b4b1743ff51c96f3"
PINNED_FINGERPRINT = "f67603b40400a626"


@pytest.fixture(scope="module")
def recorded() -> dict[str, object]:
    loaded: dict[str, object] = json.loads(pin.PIN_FILE.read_text(encoding="utf-8"))
    return loaded


@pytest.fixture(scope="module")
def reproduced(recorded: dict[str, object]) -> tuple[BaselineState, list[str]]:
    values = recorded["values"]
    assert isinstance(values, list) and isinstance(recorded["start"], str)
    state, evaluations = pin.reproduce(datetime.fromisoformat(recorded["start"]), values)
    return state, pin.evaluation_lines(evaluations)


def test_the_pin_is_the_one_that_was_accepted(recorded: dict[str, object]) -> None:
    # The hashes live in this file as well as in the fixture, so that changing one
    # without the other is itself a failure.
    assert recorded["evaluations_sha256"] == PINNED_EVALUATIONS
    assert recorded["state_sha256"] == PINNED_STATE
    assert recorded["config_fingerprint"] == PINNED_FINGERPRINT
    assert recorded["rule_version"] == rules.BASELINE_RULES_VERSION == "baseline_rules_v1"
    assert (recorded["buckets"], recorded["significant_digits"]) == (720, 12)
    assert pin.digest("\n".join(pin.read_evaluations())) == PINNED_EVALUATIONS
    assert pin.digest(pin.STATE_FILE.read_text(encoding="utf-8").rstrip("\n")) == PINNED_STATE


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


def test_every_v1_evaluation_reproduces(reproduced: tuple[BaselineState, list[str]]) -> None:
    _, lines = reproduced
    expected = pin.read_evaluations()
    assert len(lines) == len(expected) == 720
    for index, (now, then) in enumerate(zip(lines, expected, strict=True)):
        assert now == then, f"evaluation of bucket {index} no longer reproduces under v1"
    assert pin.digest("\n".join(lines)) == PINNED_EVALUATIONS


def test_the_v1_baseline_state_reproduces(reproduced: tuple[BaselineState, list[str]]) -> None:
    state, _ = reproduced
    text = pin.state_text(state)
    assert text == pin.STATE_FILE.read_text(encoding="utf-8").rstrip("\n")
    assert pin.digest(text) == PINNED_STATE
    # The stored state is a real, loadable BaselineState, not just a hash.
    restored = BaselineState.model_validate_json(pin.STATE_FILE.read_text(encoding="utf-8"))
    assert restored.rule_version == "baseline_rules_v1"
    assert restored.config_fingerprint == PINNED_FINGERPRINT
    assert len(restored.window) == 720 and restored.subject == pin.SUBJECT


def test_the_pinned_history_exercises_what_it_claims_to(
    recorded: dict[str, object], reproduced: tuple[BaselineState, list[str]]
) -> None:
    """A pin over a featureless series would prove little; this one is not featureless."""
    state, lines = reproduced
    values = recorded["values"]
    assert isinstance(values, list) and values.count(None) == 6  # a gap and a lone hole
    assert recorded["confirmed_change_points"] == ["2026-06-26T02:00:00+00:00"]
    (change,) = state.cusum.history
    assert change.started_at.isoformat() == "2026-06-26T00:00:00+00:00"
    assert state.cusum.phase is ChangePhase.ACTIVE
    last = json.loads(lines[-1])
    assert last["maturity"] == "mature" and last["seasonality_basis"] == "hour_of_week"
    for feature in ("log_lift", "robust_z", "velocity", "acceleration", "trend_long"):
        assert last[feature]["value"] is not None, feature
    bases = {json.loads(line)["seasonality_basis"] for line in lines}
    assert bases == {None, "trailing_level", "hour_of_day", "hour_of_week"}
    assert sum(entry.excluded for entry in state.window) > 100  # the surge freeze is in it
