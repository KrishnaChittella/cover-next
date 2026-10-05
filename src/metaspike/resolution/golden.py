"""The golden evaluation set for entity resolution: its shape and its integrity rules.

The set is an independent test target. It is drafted from recorded source records,
reviewed by a person, and frozen BEFORE any resolver threshold exists, so thresholds
can never be tuned into it.

Ground truth must come from the recorded evidence itself. Every case therefore cites
the fields that justify its label, and `GoldenCase` verifies each citation against
the recorded record it names. A label that the recorded fields cannot support has no
place here: such a case is labelled QUARANTINE or left out.
"""

import hashlib
import json
from collections.abc import Mapping
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from metaspike.domain.games import NonEmptyStr
from metaspike.domain.identity import SourceRecordKey
from metaspike.domain.model import FrozenModel
from metaspike.domain.source_records import Admissibility, SourceRecord


class GoldenGroup(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    QUARANTINE = "quarantine"
    NEW_ENTITY = "new_entity"


class ExpectedDecision(StrEnum):
    AUTO_MATCH = "auto_match"
    NEW_ENTITY = "new_entity"
    QUARANTINE = "quarantine"
    # Filtered out before resolution: DLC, demos, betas, bundles, soundtracks.
    NOT_AN_ENTITY = "not_an_entity"


# The approved distribution: category -> (group, target number of cases).
CATEGORY_TARGETS: dict[str, tuple[GoldenGroup, int]] = {
    "exact_cross_source": (GoldenGroup.POSITIVE, 14),
    "punctuation_casing": (GoldenGroup.POSITIVE, 6),
    "alternate_names": (GoldenGroup.POSITIVE, 6),
    "roman_numerals": (GoldenGroup.POSITIVE, 5),
    "subtitles": (GoldenGroup.POSITIVE, 5),
    "renamed_games": (GoldenGroup.POSITIVE, 5),
    "ports_platform_variants": (GoldenGroup.POSITIVE, 8),
    "early_access": (GoldenGroup.POSITIVE, 5),
    "editions_with_id_link": (GoldenGroup.POSITIVE, 8),
    "numbered_sequels": (GoldenGroup.NEGATIVE, 9),
    "remasters": (GoldenGroup.NEGATIVE, 6),
    "remakes": (GoldenGroup.NEGATIVE, 6),
    "same_name_different_years": (GoldenGroup.NEGATIVE, 10),
    "short_ambiguous_names": (GoldenGroup.NEGATIVE, 7),
    "franchise_collisions": (GoldenGroup.NEGATIVE, 6),
    "call_of_duty_family": (GoldenGroup.NEGATIVE, 6),
    "counter_strike_lineage": (GoldenGroup.NEGATIVE, 5),
    "gta_family": (GoldenGroup.NEGATIVE, 5),
    "standalone_expansions": (GoldenGroup.NEGATIVE, 4),
    "dlc": (GoldenGroup.NEGATIVE, 6),
    "demos": (GoldenGroup.NEGATIVE, 5),
    "betas_playtests": (GoldenGroup.NEGATIVE, 4),
    "bundles": (GoldenGroup.NEGATIVE, 3),
    "soundtracks": (GoldenGroup.NEGATIVE, 3),
    # Proposed substitutes (target 0 until approved): non-entity products that IGDB
    # types authoritatively. No recorded field identifies a demo, beta or soundtrack.
    "packs_addons": (GoldenGroup.NEGATIVE, 0),
    "episodes_seasons": (GoldenGroup.NEGATIVE, 0),
    "updates": (GoldenGroup.NEGATIVE, 0),
    "title_only_missing_companies": (GoldenGroup.QUARANTINE, 8),
    "conflicting_companies": (GoldenGroup.QUARANTINE, 5),
    "conflicting_release_dates": (GoldenGroup.QUARANTINE, 5),
    "id_link_incompatible_title": (GoldenGroup.QUARANTINE, 5),
    "edition_without_id_link": (GoldenGroup.QUARANTINE, 4),
    "regional_titles": (GoldenGroup.QUARANTINE, 3),
    "csgo_cs2_single_app": (GoldenGroup.QUARANTINE, 2),
    "early_access_rename_thin_evidence": (GoldenGroup.QUARANTINE, 1),
    "no_candidates": (GoldenGroup.NEW_ENTITY, 12),
    "candidates_hard_contradicted": (GoldenGroup.NEW_ENTITY, 8),
}

GROUP_TARGETS: dict[GoldenGroup, int] = {
    group: sum(count for owner, count in CATEGORY_TARGETS.values() if owner is group)
    for group in GoldenGroup
}


class EvidenceCitation(FrozenModel):
    """'This recorded field of this recorded record contains this value.'"""

    record: SourceRecordKey
    field: NonEmptyStr
    value: NonEmptyStr


class GoldenEntity(FrozenModel):
    """An entity that already exists when the incoming record arrives."""

    label: NonEmptyStr
    records: tuple[SourceRecord, ...] = Field(min_length=1)


class GoldenCase(FrozenModel):
    case_id: NonEmptyStr
    category: NonEmptyStr
    incoming: SourceRecord
    existing: tuple[GoldenEntity, ...] = ()
    # Recorded records that bear on the case without being entities, e.g. a bundle
    # record that claims the incoming record's id. Evidence may cite them.
    context: tuple[SourceRecord, ...] = ()
    expected: ExpectedDecision
    # The label of the entity an AUTO_MATCH must select.
    target: str | None = None
    # Labels the incoming record must never be merged into.
    must_not_match: tuple[str, ...] = ()
    rationale: NonEmptyStr
    evidence: tuple[EvidenceCitation, ...] = Field(min_length=1)
    # 'difficult' marks a label that took judgement and deserves a second look.
    truth: Literal["established", "difficult"] = "established"

    @property
    def group(self) -> GoldenGroup:
        return CATEGORY_TARGETS[self.category][0]

    @model_validator(mode="after")
    def _check_case(self) -> Self:
        if self.category not in CATEGORY_TARGETS:
            raise ValueError(f"{self.case_id}: unknown category '{self.category}'")
        labels = [entity.label for entity in self.existing]
        if len(labels) != len(set(labels)):
            raise ValueError(f"{self.case_id}: two existing entities share a label")
        if (self.expected is ExpectedDecision.AUTO_MATCH) != (self.target is not None):
            raise ValueError(f"{self.case_id}: a target is given exactly for AUTO_MATCH")
        if self.target is not None and self.target not in labels:
            raise ValueError(f"{self.case_id}: target is not one of the existing entities")
        if any(label not in labels for label in self.must_not_match):
            raise ValueError(f"{self.case_id}: must_not_match names an unknown entity")
        if self.target in self.must_not_match:
            raise ValueError(f"{self.case_id}: target is also listed in must_not_match")
        self._check_group()
        self._check_evidence()
        return self

    def _check_group(self) -> None:
        group, expected = self.group, self.expected
        if group is GoldenGroup.POSITIVE and expected is not ExpectedDecision.AUTO_MATCH:
            raise ValueError(f"{self.case_id}: a positive case expects AUTO_MATCH")
        if group is GoldenGroup.QUARANTINE and expected is not ExpectedDecision.QUARANTINE:
            raise ValueError(f"{self.case_id}: a quarantine case expects QUARANTINE")
        if group is GoldenGroup.NEW_ENTITY and expected is not ExpectedDecision.NEW_ENTITY:
            raise ValueError(f"{self.case_id}: a new-entity case expects NEW_ENTITY")
        if group is GoldenGroup.NEGATIVE:
            if expected is ExpectedDecision.QUARANTINE:
                raise ValueError(f"{self.case_id}: a negative case must name its right outcome")
            if not self.must_not_match:
                raise ValueError(f"{self.case_id}: a negative case names what it must not match")
        eligible = self.incoming.admissibility is Admissibility.ENTITY_ELIGIBLE
        if expected is ExpectedDecision.NOT_AN_ENTITY and eligible:
            raise ValueError(f"{self.case_id}: an entity-eligible record cannot be NOT_AN_ENTITY")
        if expected is ExpectedDecision.NEW_ENTITY and not eligible:
            raise ValueError(f"{self.case_id}: only an entity-eligible record can be NEW_ENTITY")

    def _check_evidence(self) -> None:
        recorded = {self.incoming.key: self.incoming}
        recorded.update({record.key: record for record in self.context})
        for entity in self.existing:
            for record in entity.records:
                recorded[record.key] = record
        for citation in self.evidence:
            cited = recorded.get(citation.record)
            if cited is None:
                raise ValueError(f"{self.case_id}: evidence cites a record that is not in the case")
            fields = cited.model_dump(mode="json")
            if citation.field not in fields:
                raise ValueError(f"{self.case_id}: evidence cites unknown field '{citation.field}'")
            stated = json.dumps(fields[citation.field], ensure_ascii=False)
            if citation.value not in stated:
                raise ValueError(
                    f"{self.case_id}: recorded {citation.field} does not contain "
                    f"the cited value '{citation.value}'"
                )


class GoldenEntitySpec(FrozenModel):
    label: NonEmptyStr
    records: tuple[SourceRecordKey, ...] = Field(min_length=1)


class GoldenCaseSpec(FrozenModel):
    """A case as stored on disk: it names recorded records instead of copying them."""

    case_id: NonEmptyStr
    category: NonEmptyStr
    incoming: SourceRecordKey
    existing: tuple[GoldenEntitySpec, ...] = ()
    context: tuple[SourceRecordKey, ...] = ()
    expected: ExpectedDecision
    target: str | None = None
    must_not_match: tuple[str, ...] = ()
    rationale: NonEmptyStr
    evidence: tuple[EvidenceCitation, ...] = Field(min_length=1)
    truth: Literal["established", "difficult"] = "established"

    def resolve(self, recorded: Mapping[SourceRecordKey, SourceRecord]) -> GoldenCase:
        def find(key: SourceRecordKey) -> SourceRecord:
            try:
                return recorded[key]
            except KeyError:
                raise ValueError(
                    f"{self.case_id}: {key.source}:{key.source_id} is not in the recorded fixtures"
                ) from None

        return GoldenCase(
            case_id=self.case_id,
            category=self.category,
            incoming=find(self.incoming),
            existing=tuple(
                GoldenEntity(label=entity.label, records=tuple(find(k) for k in entity.records))
                for entity in self.existing
            ),
            context=tuple(find(key) for key in self.context),
            expected=self.expected,
            target=self.target,
            must_not_match=self.must_not_match,
            rationale=self.rationale,
            evidence=self.evidence,
            truth=self.truth,
        )


class DroppedCase(FrozenModel):
    """A case considered and left out because recorded evidence could not settle it."""

    description: NonEmptyStr
    category: NonEmptyStr
    reason: NonEmptyStr


class GoldenSet(FrozenModel):
    version: int = Field(ge=1)
    status: Literal["draft", "frozen"]
    created_on: date
    sources_recorded_on: date
    cases: tuple[GoldenCase, ...]
    dropped: tuple[DroppedCase, ...] = ()

    @model_validator(mode="after")
    def _unique_cases(self) -> Self:
        identifiers = [case.case_id for case in self.cases]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("two golden cases share a case_id")
        return self

    def count_by_category(self) -> dict[str, int]:
        counts = dict.fromkeys(CATEGORY_TARGETS, 0)
        for case in self.cases:
            counts[case.category] += 1
        return counts

    def count_by_group(self) -> dict[GoldenGroup, int]:
        counts = dict.fromkeys(GoldenGroup, 0)
        for case in self.cases:
            counts[case.group] += 1
        return counts

    def distribution_gaps(self) -> dict[str, tuple[int, int]]:
        """Categories whose case count differs from the approved target: (have, target)."""
        have = self.count_by_category()
        return {
            category: (have[category], target)
            for category, (_, target) in CATEGORY_TARGETS.items()
            if have[category] != target
        }

    @property
    def content_hash(self) -> str:
        """Fingerprint of the cases. A frozen set is pinned to this value by a test."""
        payload = [case.model_dump(mode="json") for case in self.cases]
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class GoldenSetFile(FrozenModel):
    """The on-disk form of the golden set."""

    version: int = Field(ge=1)
    status: Literal["draft", "frozen"]
    created_on: date
    sources_recorded_on: date
    notes: tuple[str, ...] = ()
    cases: tuple[GoldenCaseSpec, ...]
    dropped: tuple[DroppedCase, ...] = ()


def load_golden_set(path: Path, recorded: Mapping[SourceRecordKey, SourceRecord]) -> GoldenSet:
    """Load the golden set, resolving and verifying every case against recorded records."""
    stored = GoldenSetFile.model_validate_json(path.read_bytes())
    return GoldenSet(
        version=stored.version,
        status=stored.status,
        created_on=stored.created_on,
        sources_recorded_on=stored.sources_recorded_on,
        cases=tuple(spec.resolve(recorded) for spec in stored.cases),
        dropped=stored.dropped,
    )
