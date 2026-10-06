"""The resolver against the frozen golden set, and the quarantine store."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from metaspike import cli
from metaspike.catalogue.overrides import NO_OVERRIDES, PRODUCTION_OVERRIDES, OverrideSet
from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.source_records import ExternalId, ProductType, SourceRecord
from metaspike.resolution.evaluate import evaluate
from metaspike.resolution.golden import ExpectedDecision, GoldenGroup, GoldenSet, load_golden_set
from metaspike.resolution.models import (
    DecisionKind,
    DecisionLog,
    EntityView,
    QuarantineReason,
    ResolutionDecision,
    ResolverInput,
)
from metaspike.resolution.quarantine import (
    LocalQuarantineStore,
    Quarantine,
    evaluation_fingerprint,
)
from metaspike.resolution.recorded import load_recorded_catalogue
from metaspike.resolution.resolver import RESOLVER_VERSION, resolve

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 5, tzinfo=UTC)


@pytest.fixture(scope="module")
def golden() -> GoldenSet:
    return load_golden_set(
        FIXTURES / "resolution_golden.json", load_recorded_catalogue(FIXTURES / "catalogue")
    )


# --- the acceptance gate --------------------------------------------------------------


def test_the_resolver_makes_zero_false_automatic_merges(golden: GoldenSet) -> None:
    report = evaluate(golden)
    assert report.overall.false_auto_merges == 0
    assert report.passes_safety_gate
    assert report.overall.precision == 1.0
    assert all(metrics.false_auto_merges == 0 for metrics in report.by_category.values())
    for result in report.results:
        if result.decided is DecisionKind.AUTO_MATCH:
            assert result.expected is ExpectedDecision.AUTO_MATCH and result.correct


def test_no_case_is_ever_merged_into_an_entity_it_must_not_match(golden: GoldenSet) -> None:
    report = evaluate(golden)
    by_id = {case.case_id: case for case in golden.cases}
    for result in report.results:
        if result.decided_target is not None:
            assert result.decided_target not in by_id[result.case_id].must_not_match


def test_observed_resolver_quality_on_the_frozen_set(golden: GoldenSet) -> None:
    """A record of where resolver_v1 stands. A change here is a change in behaviour."""
    report = evaluate(golden)
    overall = report.overall
    assert (
        report.golden_version == 3 and report.resolver_version == RESOLVER_VERSION == "resolver_v2"
    )
    assert (overall.cases, overall.correct) == (200, 178)
    assert (overall.correct_auto_matches, overall.auto_matches) == (52, 52)
    assert (overall.correct_auto_matches_on_positives, overall.positives) == (44, 62)
    assert overall.recall == pytest.approx(44 / 62)
    assert (overall.quarantined, overall.avoidable_quarantines) == (55, 22)
    assert overall.quarantine_rate == pytest.approx(0.275)
    assert overall.avoidable_quarantine_rate == pytest.approx(22 / 167)
    assert (overall.correct_new_entity_decisions, overall.new_entity_decisions) == (70, 70)
    assert overall.new_entity_accuracy == 1.0


def test_a_quarantined_positive_is_a_recall_miss_not_a_false_merge(golden: GoldenSet) -> None:
    report = evaluate(golden)
    masked = report.masked
    assert (masked.cases, masked.positives) == (18, 18)
    # Steam's list has no dates or companies, so with the id link withheld nothing
    # corroborates the title: every masked positive is safely quarantined.
    assert (masked.auto_matches, masked.quarantined, masked.false_auto_merges) == (0, 18, 0)
    assert masked.recall == 0.0
    misses = [r for r in report.results if r.masked]
    assert all(not r.correct and not r.false_auto_merge for r in misses)
    assert {r.quarantine_reason for r in misses} == {"title_only"}

    plain = report.unmasked_positives
    # Every positive decided on real, visible evidence is matched.
    assert (plain.correct_auto_matches_on_positives, plain.positives) == (44, 44)
    assert plain.recall == 1.0


def test_every_miss_is_on_the_safe_side(golden: GoldenSet) -> None:
    failures = evaluate(golden).failures()
    assert len(failures) == 22
    assert {failure.decided for failure in failures} == {DecisionKind.QUARANTINE}
    assert sum(f.group is GoldenGroup.POSITIVE for f in failures) == 18
    assert all(f.masked for f in failures if f.group is GoldenGroup.POSITIVE)
    assert sum(f.category == "same_name_different_years" for f in failures) == 4


def test_hard_families_are_resolved_as_labelled(golden: GoldenSet) -> None:
    report = evaluate(golden)
    for category in (
        "gta_family",
        "counter_strike_lineage",
        "call_of_duty_family",
        "short_ambiguous_names",
        "remasters",
        "remakes",
        "bundles",
        "dlc",
        "soundtracks",
        "numbered_sequels",
    ):
        metrics = report.by_category[category]
        assert metrics.correct == metrics.cases, category
    results = {result.case_id: result for result in report.results}
    fivem = next(c for c in golden.cases if c.incoming.title == "FiveM")
    assert results[fivem.case_id].decided is DecisionKind.MANUAL_OVERRIDE
    assert evaluate(golden, overrides=NO_OVERRIDES).by_category["gta_family"].correct == 4


def test_the_resolver_is_given_only_a_resolver_input(golden: GoldenSet) -> None:
    seen: list[object] = []

    def spy(view: ResolverInput, overrides: OverrideSet) -> ResolutionDecision:
        seen.append((type(view), type(overrides)))
        assert type(view) is ResolverInput
        assert not hasattr(view, "expected") and not hasattr(view, "ground_truth_evidence")
        return resolve(view, overrides)

    evaluate(golden, resolver=spy)
    assert len(seen) == 200 and set(seen) == {(ResolverInput, OverrideSet)}


def test_the_evaluator_catches_a_resolver_that_merges_wrongly(golden: GoldenSet) -> None:
    def reckless(view: ResolverInput, overrides: OverrideSet) -> ResolutionDecision:
        if not view.entities:
            return resolve(view, overrides)
        return ResolutionDecision(
            kind=DecisionKind.AUTO_MATCH,
            target_ref=view.entities[-1].ref,
            path="B",
            explanation="always merge into the last entity",
            resolver_version="reckless",
        )

    report = evaluate(golden, resolver=reckless)
    assert report.overall.false_auto_merges > 100 and not report.passes_safety_gate
    assert report.overall.precision is not None and report.overall.precision < 0.5
    assert report.overall.recall is not None and report.overall.recall > 0.9


def test_cli_evaluation_prints_the_metrics_and_fails_on_a_false_merge(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments = [
        "evaluate-resolver",
        "--golden",
        str(FIXTURES / "resolution_golden.json"),
        "--recorded",
        str(FIXTURES / "catalogue"),
        "--failures",
    ]
    assert cli.main(arguments) == cli.EXIT_OK
    output = capsys.readouterr().out
    for line in (
        "FALSE AUTO_MATCH MERGES    0",
        "AUTO_MATCH PRECISION       100.0%  (52/52)",
        "REAL-EVIDENCE RECALL       100.0%  (44/44)",
        "MASKED STRESS-TEST RECALL    0.0%  (0/18)",
        "ALL-POSITIVES RECALL        71.0%  (44/62)",
        "QUARANTINE RATE             27.5%  (55/200)",
        "AVOIDABLE QUARANTINE RATE   13.2%  (22/167)",
        "NEW_ENTITY ACCURACY        100.0%  (70/70)",
    ):
        assert line in output
    assert "gta_family" in output and "[masked]" in output

    def merge_everything(golden_set: GoldenSet) -> object:
        real = evaluate(golden_set)
        return real.model_copy(
            update={"overall": real.overall.model_copy(update={"false_auto_merges": 1})}
        )

    monkeypatch.setattr(cli, "evaluate", merge_everything)
    assert cli.main(arguments) == cli.EXIT_INVALID_OUTPUT
    assert "false automatic merge" in capsys.readouterr().err


# --- quarantine -----------------------------------------------------------------------


def record(source: str, source_id: str, title: str, **fields: object) -> SourceRecord:
    return SourceRecord.model_validate(
        {
            "key": {"source": source, "source_id": source_id},
            "title": title,
            "product_type": fields.pop("product_type", ProductType.UNKNOWN),
            "fetched_at": NOW,
            **fields,
        }
    )


STEAM_APP = record("steam_catalogue", "100", "Synthetic Quest")
IGDB_GAME = record("igdb", "1", "Synthetic Quest", product_type=ProductType.MAIN_GAME)


def view_of(incoming: SourceRecord, *existing: SourceRecord, **fields: object) -> ResolverInput:
    entities = tuple(
        EntityView(ref=f"e{number}", records=(r,)) for number, r in enumerate(existing)
    )
    return ResolverInput.model_validate({"incoming": incoming, "entities": entities, **fields})


def quarantined(view: ResolverInput, at: datetime = NOW) -> tuple[Quarantine, ResolutionDecision]:
    decision = resolve(view)
    assert decision.kind is DecisionKind.QUARANTINE
    _, logged = DecisionLog().append(view.incoming, decision, at)
    fingerprint = evaluation_fingerprint(view, NO_OVERRIDES)
    return Quarantine().put(view.incoming, decision, at, fingerprint, logged.ref), decision


def test_a_quarantined_record_keeps_everything_known_and_has_no_canonical_id() -> None:
    quarantine, decision = quarantined(view_of(STEAM_APP, IGDB_GAME))
    entry = quarantine.get(STEAM_APP.key)
    assert entry is not None

    assert entry.record == STEAM_APP and entry.reason is QuarantineReason.TITLE_ONLY
    assert entry.candidates == decision.candidates and entry.candidates[0].entity_ref == "e0"
    assert entry.first_seen == entry.last_evaluated == NOW and entry.evaluations == 1
    assert entry.decision_history == ("decision:1",) and entry.explanation
    assert "canonical_game_id" not in type(entry).model_fields
    assert "canonical_game_id" not in entry.model_dump_json()


def test_re_evaluation_keeps_first_seen_and_extends_the_history() -> None:
    view = view_of(STEAM_APP, IGDB_GAME)
    quarantine, decision = quarantined(view)
    later = NOW + timedelta(days=9)
    quarantine = quarantine.put(STEAM_APP, decision, later, "new-fingerprint", "decision:7")
    entry = quarantine.get(STEAM_APP.key)
    assert entry is not None and len(quarantine.entries) == 1
    assert (entry.first_seen, entry.last_evaluated, entry.evaluations) == (NOW, later, 2)
    assert entry.decision_history == ("decision:1", "decision:7")
    assert entry.age_days(later) == 9


def test_only_a_quarantine_decision_can_be_stored() -> None:
    linked = IGDB_GAME.model_copy(
        update={"external_ids": (ExternalId(source="steam", value="100"),)}
    )
    matched = resolve(view_of(STEAM_APP, linked))
    assert matched.kind is DecisionKind.AUTO_MATCH
    with pytest.raises(ValueError, match="only a QUARANTINE decision"):
        Quarantine().put(STEAM_APP, matched, NOW, "f", "decision:1")


def test_release_removes_the_record_once_it_is_resolved() -> None:
    quarantine, _ = quarantined(view_of(STEAM_APP, IGDB_GAME))
    assert quarantine.release(STEAM_APP.key).entries == ()
    assert quarantine.release(IGDB_GAME.key) == quarantine  # releasing a stranger is harmless


def test_metrics_count_by_source_reason_and_age() -> None:
    quarantine, _ = quarantined(view_of(STEAM_APP, IGDB_GAME), NOW - timedelta(days=45))
    orphan = record("steam_catalogue", "200", "Nothing Like It")
    decision = resolve(view_of(orphan, IGDB_GAME))
    quarantine = quarantine.put(orphan, decision, NOW - timedelta(days=3), "f", "decision:2")
    conflicted = record("igdb", "9", "Another", product_type=ProductType.PORT)
    quarantine = quarantine.put(conflicted, resolve(view_of(conflicted)), NOW, "g", "decision:3")

    metrics = quarantine.metrics(NOW)
    assert metrics.total == 3
    assert metrics.by_source == {"igdb": 1, "steam_catalogue": 2}
    assert metrics.by_reason == {
        "title_only": 1,
        "undetermined_product_type": 1,
        "variant_without_parent": 1,
    }
    assert (metrics.oldest_age_days, metrics.median_age_days) == (45.0, 3.0)
    assert metrics.older_than_30_days == 1
    assert Quarantine().metrics(NOW).oldest_age_days is None


def test_a_record_is_due_again_only_when_its_evidence_changes() -> None:
    view = view_of(STEAM_APP, IGDB_GAME)
    quarantine, _ = quarantined(view)
    unchanged = evaluation_fingerprint(view, NO_OVERRIDES)
    assert not quarantine.is_due(STEAM_APP.key, unchanged)
    assert quarantine.is_due(IGDB_GAME.key, unchanged)  # never seen before

    refetched = STEAM_APP.model_copy(update={"fetched_at": NOW + timedelta(days=2)})
    assert evaluation_fingerprint(view_of(refetched, IGDB_GAME), NO_OVERRIDES) == unchanged

    # 1. the source record itself changes
    renamed = STEAM_APP.model_copy(update={"title": "Synthetic Quest Remastered"})
    # 2. a candidate entity changes
    enriched = IGDB_GAME.model_copy(update={"developers": ("Dev Co",)})
    # 3. linking evidence arrives from another source
    now_linked = IGDB_GAME.model_copy(
        update={"external_ids": (ExternalId(source="steam", value="100"),)}
    )
    # 4. another unresolved record starts pointing at this one
    pointer = record("igdb", "55", "Bundle", external_ids=[{"source": "steam", "value": "100"}])
    # 5. a new candidate appears
    namesake = record("igdb", "2", "Synthetic Quest", product_type=ProductType.MAIN_GAME)
    changed = [
        view_of(renamed, IGDB_GAME),
        view_of(STEAM_APP, enriched),
        view_of(STEAM_APP, now_linked),
        view_of(STEAM_APP, IGDB_GAME, context=(pointer,)),
        view_of(STEAM_APP, IGDB_GAME, namesake),
    ]
    fingerprints = {evaluation_fingerprint(v, NO_OVERRIDES) for v in changed}
    assert len(fingerprints) == 5 and unchanged not in fingerprints
    assert all(quarantine.is_due(STEAM_APP.key, f) for f in fingerprints)
    assert resolve(changed[2]).kind is DecisionKind.AUTO_MATCH  # the arriving link resolves it


def test_an_unrelated_change_does_not_trigger_re_evaluation() -> None:
    view = view_of(STEAM_APP, IGDB_GAME)
    stranger = record("igdb", "77", "Unrelated Game", product_type=ProductType.MAIN_GAME)
    bystander = record("steam_catalogue", "999", "Another App")
    with_noise = view_of(STEAM_APP, IGDB_GAME, stranger, context=(bystander,))
    assert evaluation_fingerprint(with_noise, NO_OVERRIDES) == evaluation_fingerprint(
        view, NO_OVERRIDES
    )


def test_an_override_change_triggers_re_evaluation_only_for_its_own_record() -> None:
    fivem = record(
        "igdb",
        "146553",
        "FiveM",
        product_type=ProductType.MOD,
        parent={"source": "igdb", "source_id": "1"},
    )
    assert evaluation_fingerprint(view_of(fivem, IGDB_GAME), NO_OVERRIDES) != (
        evaluation_fingerprint(view_of(fivem, IGDB_GAME), PRODUCTION_OVERRIDES)
    )
    view = view_of(STEAM_APP, IGDB_GAME)
    assert evaluation_fingerprint(view, NO_OVERRIDES) == evaluation_fingerprint(
        view, PRODUCTION_OVERRIDES
    )


def test_quarantine_survives_a_restart(tmp_path: Path) -> None:
    quarantine, _ = quarantined(view_of(STEAM_APP, IGDB_GAME))
    store = LocalQuarantineStore(tmp_path / "state" / "quarantine.json")
    assert store.load() == Quarantine()
    store.save(quarantine)
    assert LocalQuarantineStore(tmp_path / "state" / "quarantine.json").load() == quarantine
    assert quarantine.get(SourceRecordKey(source="igdb", source_id="404")) is None
