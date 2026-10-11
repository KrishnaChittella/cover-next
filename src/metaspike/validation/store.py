"""The accountability store: snapshots, labels and reports, local and append-only.

    {root}/snapshots/{cutoff day}/{content hash}.ndjson
    {root}/labels/{data through}/{content hash}.ndjson
    {root}/reports/{report id}.json

Every record is identified by a hash of its content and written once. Writing it again
adds nothing; nothing is rewritten or deleted. A label made later, with more future
available, is a new record beside the earlier one: the earlier "unavailable" stays as
evidence of what could be said then.
"""

import hashlib
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from metaspike.storage.base import StorageCorruptionError
from metaspike.validation.outcomes import OutcomeLabel
from metaspike.validation.report import ValidationReport
from metaspike.validation.snapshot import RecommendationSnapshot


class AccountabilityStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _append(self, area: str, by_day: dict[str, list[str]]) -> None:
        for day, lines in sorted(by_day.items()):
            body = ("\n".join(lines) + "\n").encode("utf-8")
            path = self._root / area / day / f"{hashlib.sha256(body).hexdigest()[:32]}.ndjson"
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("xb") as handle:  # "x": never overwrite
                    handle.write(body)
            except FileExistsError:
                continue

    def _lines(self, area: str) -> list[str]:
        directory = self._root / area
        if not directory.is_dir():
            return []
        return [
            line
            for path in sorted(directory.glob("*/*.ndjson"))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]

    def add_snapshots(self, snapshots: Iterable[RecommendationSnapshot]) -> int:
        """Add snapshots. Returns how many were new."""
        known = {item.snapshot_id for item in self.snapshots()}
        by_day: dict[str, list[str]] = {}
        added = 0
        for item in sorted(snapshots, key=lambda s: (s.data_cutoff_day, s.snapshot_id)):
            if item.snapshot_id in known:
                continue
            known.add(item.snapshot_id)
            by_day.setdefault(item.data_cutoff_day.isoformat(), []).append(item.model_dump_json())
            added += 1
        self._append("snapshots", by_day)
        return added

    def add_labels(self, labels: Iterable[OutcomeLabel]) -> int:
        known = {item.label_id for item in self.labels()}
        by_day: dict[str, list[str]] = {}
        added = 0
        for item in sorted(labels, key=lambda label: (label.data_through, label.label_id)):
            if item.label_id in known:
                continue
            known.add(item.label_id)
            by_day.setdefault(item.data_through.isoformat(), []).append(item.model_dump_json())
            added += 1
        self._append("labels", by_day)
        return added

    def add_report(self, report: ValidationReport) -> bool:
        """Keep a report. False when a report with this id is already there."""
        path = self._root / "reports" / f"{report.report_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(report.model_dump_json(indent=2) + "\n")
        except FileExistsError:
            return False
        return True

    def snapshots(self, cutoff_day: date | None = None) -> tuple[RecommendationSnapshot, ...]:
        try:
            loaded = [
                RecommendationSnapshot.model_validate_json(line)
                for line in self._lines("snapshots")
            ]
        except ValidationError as error:
            raise StorageCorruptionError(f"stored snapshots are unreadable: {error}") from None
        kept = [item for item in loaded if cutoff_day is None or item.data_cutoff_day == cutoff_day]
        return tuple(sorted(kept, key=lambda s: (s.data_cutoff_day, s.rank or 10**9, s.slug)))

    def labels(self) -> tuple[OutcomeLabel, ...]:
        try:
            loaded = [OutcomeLabel.model_validate_json(line) for line in self._lines("labels")]
        except ValidationError as error:
            raise StorageCorruptionError(f"stored labels are unreadable: {error}") from None
        return tuple(sorted(loaded, key=lambda label: (label.data_through, label.label_id)))

    def label_for(self, snapshot_id: str) -> OutcomeLabel | None:
        """The label made with the most future available, for one snapshot."""
        candidates = [item for item in self.labels() if item.snapshot_id == snapshot_id]
        return candidates[-1] if candidates else None

    def reports(self) -> tuple[ValidationReport, ...]:
        directory = self._root / "reports"
        if not directory.is_dir():
            return ()
        try:
            loaded = [
                ValidationReport.model_validate_json(path.read_bytes())
                for path in sorted(directory.glob("report_*.json"))
            ]
        except ValidationError as error:
            raise StorageCorruptionError(f"a stored report is unreadable: {error}") from None
        return tuple(sorted(loaded, key=lambda report: (report.created_at, report.report_id)))

    def latest_report(self) -> ValidationReport | None:
        reports = self.reports()
        return reports[-1] if reports else None
