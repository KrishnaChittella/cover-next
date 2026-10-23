"""creator_opportunity_rules_v2: a reaction window and the event-first candidate.

v1 is frozen and stays the default; these tests only add to it.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from metaspike import cli
from metaspike.domain.families import SourceFamily
from metaspike.events.heat import Phase
from metaspike.evidence.model import Standing
from metaspike.opportunity.rules import (
    OPPORTUNITY_RULES_VERSION,
    OPPORTUNITY_RULES_VERSION_V2,
    REACTION_WINDOW,
    Direction,
    Facts,
    MovementNote,
    Readiness,
    _timely_phase,
    readiness,
    readiness_v2,
)
from tests.test_event_classification_v2 import _helldivers_store
from tests.test_event_sources import PUBLISHED

AS_OF = datetime(2026, 10, 22, 22, tzinfo=UTC)


def counted(standing: Standing) -> bool:
    return standing is Standing.VERIFIED


def movement(direction: Direction, standing: Standing = Standing.VERIFIED) -> MovementNote:
    return MovementNote(
        family=SourceFamily.WIKIMEDIA_READERSHIP,
        source="wikimedia_pageviews",
        standing=standing,
        direction=direction,
    )


EVENT_AND_ANGLE = Facts(timely_event=True, angle=True)


def test_a_shipped_event_is_timely_only_in_v2_and_only_within_the_window() -> None:
    started = AS_OF - timedelta(hours=10)
    assert not _timely_phase(Phase.DECAYING, started, AS_OF, OPPORTUNITY_RULES_VERSION)
    assert _timely_phase(Phase.DECAYING, started, AS_OF, OPPORTUNITY_RULES_VERSION_V2)
    late = AS_OF - REACTION_WINDOW - timedelta(minutes=1)
    assert not _timely_phase(Phase.DECAYING, late, AS_OF, OPPORTUNITY_RULES_VERSION_V2)
    assert not _timely_phase(Phase.EXPIRED, started, AS_OF, OPPORTUNITY_RULES_VERSION_V2)
    for phase in (Phase.UPCOMING, Phase.ACTIVE):  # as in v1
        assert _timely_phase(phase, started, AS_OF, OPPORTUNITY_RULES_VERSION)


@pytest.mark.parametrize(
    ("facts", "movements", "expected"),
    [
        # Event-first: a timely event, its angle, demand measured and not down.
        (EVENT_AND_ANGLE, [movement(Direction.FLAT)], Readiness.CANDIDATE),
        (EVENT_AND_ANGLE, [movement(Direction.UP)], Readiness.CANDIDATE),
        # Demand down, unknown or not counted: no candidate, only the event's watchlist.
        (EVENT_AND_ANGLE, [movement(Direction.DOWN)], Readiness.WATCHLIST),
        (EVENT_AND_ANGLE, [movement(Direction.UNKNOWN)], Readiness.WATCHLIST),
        (EVENT_AND_ANGLE, [movement(Direction.FLAT, Standing.UNVERIFIED)], Readiness.WATCHLIST),
        (EVENT_AND_ANGLE, [], Readiness.WATCHLIST),
        # Disagreement blocks it.
        (
            Facts(timely_event=True, angle=True, disagreement=True),
            [movement(Direction.FLAT)],
            Readiness.WATCHLIST,
        ),
        # No event: v2 adds nothing.
        (Facts(), [movement(Direction.FLAT)], Readiness.INSUFFICIENT),
    ],
)
def test_the_event_first_candidate(
    facts: Facts, movements: list[MovementNote], expected: Readiness
) -> None:
    assert readiness_v2(facts, movements, counted) is expected


def test_v2_never_lowers_what_v1_says() -> None:
    v1_candidate = Facts(
        moving_up=(SourceFamily.WIKIMEDIA_READERSHIP,), angle=True, coverage_known=True
    )
    assert readiness(v1_candidate) is Readiness.CANDIDATE
    assert readiness_v2(v1_candidate, [movement(Direction.UP)], counted) is Readiness.CANDIDATE
    assert readiness(EVENT_AND_ANGLE) is Readiness.WATCHLIST  # v1: no candidate without coverage


def _opportunities(root: Path, version: str, capsys: pytest.CaptureFixture[str]) -> Any:
    sources_dir, events_dir = _helldivers_store(root)
    capsys.readouterr()
    code = cli.main(
        [
            "evidence",
            "--as-of",
            (PUBLISHED + timedelta(hours=10)).isoformat(),
            "--game",
            "helldivers-2",
            "--events-dir",
            str(events_dir),
            "--event-sources-dir",
            str(sources_dir),
            "--event-rules",
            "event_classification_rules_v2",
            "--opportunities",
            "--opportunity-rules",
            version,
            "--json",
        ]
    )
    assert code == 0
    (assessment,) = json.loads(capsys.readouterr().out)["assessments"]
    return assessment


def test_a_shipped_patch_explains_now_in_v2_and_counts_only_if_verified(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The Helldivers 2 shape: a patch shipped hours ago, from an unverified local store."""
    v1 = _opportunities(tmp_path / "v1", OPPORTUNITY_RULES_VERSION, capsys)
    v2 = _opportunities(tmp_path / "v2", OPPORTUNITY_RULES_VERSION_V2, capsys)
    assert v1["rules_version"] == OPPORTUNITY_RULES_VERSION and v1["events"] == []
    assert v1["simulated_readiness"] == Readiness.INSUFFICIENT.value
    assert v2["rules_version"] == OPPORTUNITY_RULES_VERSION_V2
    (event,) = v2["events"]
    assert event["event_type"] == "minor_patch" and event["phase"] == "decaying"
    (angle,) = v2["angles"]
    assert angle["addresses"].startswith("reaction to the minor patch")
    assert "reaction window" in angle["timely_reason"] and angle["confirmed"] is False
    # Unverified: shown, labelled, and counted only in the simulation. Never a real "now".
    assert v2["readiness"] == Readiness.INSUFFICIENT.value
    assert v2["simulated_readiness"] == Readiness.WATCHLIST.value
    assert v2["why_now"] == [] and v2["unconfirmed"]
    assert "shipped" in v2["unconfirmed"][0]["text"]


def test_the_recorded_v1_output_is_unchanged_by_v2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Asked for nothing new, the command writes a v1 report exactly as before."""
    default = _opportunities(tmp_path / "a", OPPORTUNITY_RULES_VERSION, capsys)
    assert default["rules_version"] == "creator_opportunity_rules_v1"
    assert not any("reaction" in json.dumps(a) for a in default["angles"])
