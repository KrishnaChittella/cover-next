"""Evaluate a resolver against the frozen golden set.

The resolver is handed `ResolverInput` objects and nothing else. Labels, expected
decisions and ground-truth evidence stay on this side of the call.

    false auto-merge   an AUTO_MATCH whose target is not the ground-truth entity
                       (including any AUTO_MATCH where no match is expected)
    precision          correct AUTO_MATCH / all AUTO_MATCH
    recall             correct AUTO_MATCH on positive cases / positive cases
    quarantine rate    QUARANTINE decisions / all cases
    avoidable          QUARANTINE decisions on cases that expected something else
      quarantine rate    / cases that expected something else
    new-entity         correct NEW_ENTITY / all NEW_ENTITY decisions
      accuracy

A positive case that is quarantined is safe, is not a false merge, and counts as a
missed match.
"""

from collections.abc import Callable

from metaspike.catalogue.overrides import PRODUCTION_OVERRIDES, OverrideSet
from metaspike.domain.model import FrozenModel
from metaspike.resolution.golden import ExpectedDecision, GoldenGroup, GoldenSet
from metaspike.resolution.models import DecisionKind, ResolutionDecision, ResolverInput
from metaspike.resolution.resolver import resolve

Resolver = Callable[[ResolverInput, OverrideSet], ResolutionDecision]

_EXPECTED_KIND = {
    ExpectedDecision.AUTO_MATCH: DecisionKind.AUTO_MATCH,
    ExpectedDecision.NEW_ENTITY: DecisionKind.NEW_ENTITY,
    ExpectedDecision.QUARANTINE: DecisionKind.QUARANTINE,
    ExpectedDecision.NOT_AN_ENTITY: DecisionKind.NOT_AN_ENTITY,
    ExpectedDecision.MANUAL_OVERRIDE: DecisionKind.MANUAL_OVERRIDE,
}


class CaseResult(FrozenModel):
    case_id: str
    category: str
    group: GoldenGroup
    masked: bool
    expected: ExpectedDecision
    decided: DecisionKind
    decided_target: str | None
    path: str | None
    quarantine_reason: str | None
    correct: bool
    false_auto_merge: bool
    explanation: str


class Metrics(FrozenModel):
    cases: int = 0
    correct: int = 0
    positives: int = 0
    auto_matches: int = 0
    correct_auto_matches: int = 0
    correct_auto_matches_on_positives: int = 0
    false_auto_merges: int = 0
    quarantined: int = 0
    not_expecting_quarantine: int = 0
    avoidable_quarantines: int = 0
    new_entity_decisions: int = 0
    correct_new_entity_decisions: int = 0

    @property
    def precision(self) -> float | None:
        return _ratio(self.correct_auto_matches, self.auto_matches)

    @property
    def recall(self) -> float | None:
        return _ratio(self.correct_auto_matches_on_positives, self.positives)

    @property
    def quarantine_rate(self) -> float | None:
        return _ratio(self.quarantined, self.cases)

    @property
    def avoidable_quarantine_rate(self) -> float | None:
        return _ratio(self.avoidable_quarantines, self.not_expecting_quarantine)

    @property
    def new_entity_accuracy(self) -> float | None:
        return _ratio(self.correct_new_entity_decisions, self.new_entity_decisions)


class EvaluationReport(FrozenModel):
    golden_version: int
    golden_hash: str
    resolver_version: str
    overall: Metrics
    masked: Metrics
    unmasked_positives: Metrics
    by_category: dict[str, Metrics]
    results: tuple[CaseResult, ...]

    @property
    def passes_safety_gate(self) -> bool:
        return self.overall.false_auto_merges == 0

    def failures(self) -> tuple[CaseResult, ...]:
        return tuple(result for result in self.results if not result.correct)


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def evaluate(
    golden: GoldenSet,
    resolver: Resolver = resolve,
    overrides: OverrideSet = PRODUCTION_OVERRIDES,
) -> EvaluationReport:
    results: list[CaseResult] = []
    version = "unknown"
    for case in golden.cases:
        view, labels = case.resolver_view()
        decision = resolver(view, overrides)
        version = decision.resolver_version
        target = None if decision.target_ref is None else labels.get(decision.target_ref)
        matched = decision.kind is DecisionKind.AUTO_MATCH
        correct = decision.kind is _EXPECTED_KIND[case.expected] and (
            not matched or target == case.target
        )
        results.append(
            CaseResult(
                case_id=case.case_id,
                category=case.category,
                group=case.group,
                masked=bool(case.masked_evidence),
                expected=case.expected,
                decided=decision.kind,
                decided_target=target,
                path=decision.path,
                quarantine_reason=(
                    None if decision.quarantine_reason is None else decision.quarantine_reason.value
                ),
                correct=correct,
                false_auto_merge=matched and not correct,
                explanation=decision.explanation,
            )
        )
    categories = sorted({result.category for result in results})
    return EvaluationReport(
        golden_version=golden.version,
        golden_hash=golden.content_hash,
        resolver_version=version,
        overall=_metrics(results),
        masked=_metrics([r for r in results if r.masked]),
        unmasked_positives=_metrics(
            [r for r in results if r.group is GoldenGroup.POSITIVE and not r.masked]
        ),
        by_category={c: _metrics([r for r in results if r.category == c]) for c in categories},
        results=tuple(results),
    )


def _metrics(results: list[CaseResult]) -> Metrics:
    auto = [r for r in results if r.decided is DecisionKind.AUTO_MATCH]
    new = [r for r in results if r.decided is DecisionKind.NEW_ENTITY]
    quarantined = [r for r in results if r.decided is DecisionKind.QUARANTINE]
    other_expected = [r for r in results if r.expected is not ExpectedDecision.QUARANTINE]
    return Metrics(
        cases=len(results),
        correct=sum(r.correct for r in results),
        positives=sum(r.group is GoldenGroup.POSITIVE for r in results),
        auto_matches=len(auto),
        correct_auto_matches=sum(r.correct for r in auto),
        correct_auto_matches_on_positives=sum(
            r.correct for r in auto if r.group is GoldenGroup.POSITIVE
        ),
        false_auto_merges=sum(r.false_auto_merge for r in results),
        quarantined=len(quarantined),
        not_expecting_quarantine=len(other_expected),
        avoidable_quarantines=sum(
            r.expected is not ExpectedDecision.QUARANTINE for r in quarantined
        ),
        new_entity_decisions=len(new),
        correct_new_entity_decisions=sum(r.correct for r in new),
    )
