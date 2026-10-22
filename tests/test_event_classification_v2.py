"""event_classification_rules_v2: v1 kept whole, plus size-unstated official patches.

The shapes below are those of real official Steam News posts read on 22 Oct 2026 that
v1 refused (titles here are synthetic): "<Game> Update" filed as patch notes, a
three-part version number filed as patch notes, and an untagged "Patch 1.2.0.0".
"""

import hashlib
from typing import Any

import pytest

from metaspike import cli
from metaspike.domain.events import EventType
from metaspike.event_sources.candidates import (
    EVENT_CLASSIFICATION_RULES_V1,
    EVENT_CLASSIFICATION_RULES_V2,
    EventCandidate,
    PromotionStatus,
    Refusal,
    TimingBasis,
    classify,
)
from tests.test_event_sources import (
    APP,
    BINDINGS,
    PINNED_RULES_FINGERPRINT,
    item_of,
)

V1 = EVENT_CLASSIFICATION_RULES_V1
V2 = EVENT_CLASSIFICATION_RULES_V2


def read(title: str, rules: Any = V2, **fields: Any) -> EventCandidate:
    return classify(item_of(title, **fields), BINDINGS[APP], source_cleared=True, rules=rules)


def test_v1_is_unchanged_and_v2_extends_it() -> None:
    assert hashlib.sha256(V1.model_dump_json().encode()).hexdigest() == PINNED_RULES_FINGERPRINT
    assert V2.version == "event_classification_rules_v2"
    assert V2.rules[: len(V1.rules)] == V1.rules
    assert [rule.rule_id for rule in V2.rules[len(V1.rules) :]] == [
        "patch_notes_size_unstated",
        "official_patch_number",
    ]
    for name in type(V1).model_fields:
        if name not in {"version", "rules"}:
            assert getattr(V2, name) == getattr(V1, name), name


@pytest.mark.parametrize(
    ("title", "tags"),
    [
        ("Example Game Update", ["patchnotes"]),
        ("Operation Something: 7.2.1", ["patchnotes"]),
        ("Patch 1.2.0.0", None),
        ("Hotfix 3.1", None),
    ],
)
def test_an_official_shipped_patch_of_unstated_size_is_a_minor_patch_in_v2(
    title: str, tags: list[str] | None
) -> None:
    before = read(title, V1, tags=tags)
    assert before.status is PromotionStatus.NOT_PROMOTED
    assert set(before.refusals) <= {Refusal.PATCH_SIZE_NOT_ESTABLISHED, Refusal.NO_TYPE_EVIDENCE}
    after = read(title, tags=tags)
    assert after.status is PromotionStatus.PROMOTED, after.explanation
    assert after.proposed_event_type is EventType.MINOR_PATCH
    assert after.timing_basis is TimingBasis.PUBLICATION_TIME
    assert after.rules_version == "event_classification_rules_v2"
    assert any("size not stated" in e or "version number" in e for e in after.evidence)


def test_a_stated_size_wins_over_the_size_unstated_rule() -> None:
    major = read("Major Update 2.0", tags=["patchnotes"])
    assert major.proposed_event_type is EventType.MAJOR_PATCH
    assert major.classification_rule == "patch_notes_major_update"
    hotfix = read("Hotfix update", tags=["patchnotes"])
    assert hotfix.classification_rule == "patch_notes_hotfix"


@pytest.mark.parametrize(
    ("title", "feed", "tags", "refusal"),
    [
        # A press feed reporting on a patch is still not the publisher speaking.
        (
            "Example Game Update",
            "Rock, Paper, Shotgun",
            ["patchnotes"],
            Refusal.NOT_AN_OFFICIAL_FEED,
        ),
        # Promotion and surveys are not occurrences, whatever else the title says.
        (
            "Watch and get Twitch Drops in Patch 2.1",
            None,
            None,
            Refusal.TITLE_NOT_ABOUT_AN_OCCURRENCE,
        ),
        ("New in-game survey", None, None, Refusal.TITLE_NOT_ABOUT_AN_OCCURRENCE),
        # Untagged and no numbered patch: nothing establishes a type.
        ("Operation Something: 7.2.0", None, None, Refusal.NO_TYPE_EVIDENCE),
        # A tagged post with a date-like run of numbers but no patch word, untagged.
        ("Community event 10 22 2026", None, None, Refusal.NO_TYPE_EVIDENCE),
    ],
)
def test_v2_still_refuses_what_v1_refused_for_other_reasons(
    title: str, feed: str | None, tags: list[str] | None, refusal: Refusal
) -> None:
    fields: dict[str, Any] = {"tags": tags}
    if feed is not None:
        fields["feed"] = feed
    candidate = read(title, **fields)
    assert candidate.status is PromotionStatus.NOT_PROMOTED
    assert refusal in candidate.refusals


def test_a_patch_not_yet_shipped_is_not_timed_by_its_post() -> None:
    upcoming = read("Patch 1.3 coming next week", tags=["patchnotes"])
    assert upcoming.status is PromotionStatus.NOT_PROMOTED
    assert Refusal.TITLE_SAYS_NOT_YET in upcoming.refusals


def test_v1_candidates_are_identical_to_before() -> None:
    """The same item under v1 gives the same candidate id whether or not v2 exists."""
    for title, tags in (("Example Game Update", ["patchnotes"]), ("Patch 1.2.0.0", None)):
        assert (
            read(title, V1, tags=tags).candidate_id
            == classify(item_of(title, tags=tags), BINDINGS[APP], source_cleared=True).candidate_id
        )


def test_the_cli_offers_both_versions_and_defaults_to_v1() -> None:
    assert set(cli.EVENT_RULES) == {
        "event_classification_rules_v1",
        "event_classification_rules_v2",
    }
    args = cli._parser().parse_args(["ingest-events", "--replay", "x", "--apps", "1"])
    assert args.rules == "event_classification_rules_v1"
