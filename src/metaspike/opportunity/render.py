"""An opportunity shortlist as text. Private. No source text is printed."""

from metaspike.evidence.model import Standing
from metaspike.opportunity.rules import (
    NOTEWORTHY_ROBUST_Z,
    OpportunityAssessment,
    OpportunityShortlist,
    Readiness,
)

_RULE = "=" * 78
_LABEL = {
    Readiness.CANDIDATE: "EVIDENCE-SUPPORTED CANDIDATE",
    Readiness.WATCHLIST: "PROVISIONAL / WATCHLIST",
    Readiness.INSUFFICIENT: "INSUFFICIENT EVIDENCE",
}


def _tag(standing: Standing) -> str:
    return {
        Standing.VERIFIED: "verified",
        Standing.SYNTHETIC: "SYNTHETIC",
        Standing.UNVERIFIED: "UNVERIFIED",
        Standing.NONE: "-",
    }[standing]


def _game(item: OpportunityAssessment, position: int) -> list[str]:
    lines = [
        "",
        _RULE,
        f"{position}. {item.name}  ({item.slug})",
        f"  Readiness (verified evidence only): {_LABEL[item.readiness]}",
        f"  Simulated readiness (synthetic and unverified counted as if real; NOT a "
        f"recommendation): {_LABEL[item.simulated_readiness]}",
    ]
    if item.why_now:
        lines.append("  Why now (verified):")
        lines.extend(f"    - {s.text}" for s in item.why_now)
    else:
        lines.append("  Why now: nothing verified says 'now'.")
    if item.unconfirmed:
        lines.append("  Pointing somewhere, but not counted:")
        lines.extend(f"    - [{_tag(s.standing)}] {s.text}" for s in item.unconfirmed)
    lines.append(f"  Video angle (verified evidence): {item.angle_status.replace('_', ' ')}")
    for angle in item.angles:
        about = f" — about {', '.join(angle.about)}" if angle.about else ""
        lines.append(
            f"    - [{_tag(angle.standing)}] {angle.addresses}{about}; supported by "
            f"{', '.join(angle.references)}; {'timely' if angle.timely else 'not timely'} "
            f"({angle.timely_reason}); confirmed: {'yes' if angle.confirmed else 'no'}"
        )
        lines.append(f"      unverified assumptions: {'; '.join(angle.assumptions)}")
    for movement in item.movements:
        lift = "-" if movement.log_lift is None else f"{movement.log_lift:.3g}"
        z = "-" if movement.robust_z is None else f"{movement.robust_z:.3g}"
        lines.append(
            f"  Movement [{_tag(movement.standing)}]: {movement.family.value} "
            f"{movement.direction.value} (log lift {lift}, robust z {z})"
        )
    lines.append(f"  Creator coverage: {item.creator_coverage}")
    lines.append(f"  Community: {item.community}")
    fresh = [
        f"{s.source} {s.state.value}"
        + ("" if s.latest_observed_at is None else f" @ {s.latest_observed_at:%Y-%m-%d %H:%MZ}")
        + f" [{_tag(s.standing)}]"
        for s in item.sources
        if s.state.value not in ("not_integrated", "not_collected")
    ]
    lines.append(f"  Freshness and provenance: {'; '.join(fresh) or 'nothing collected'}")
    if item.caveats:
        lines.append("  Caveats:")
        lines.extend(f"    - {c}" for c in item.caveats)
    lines.append("  Missing:")
    lines.extend(f"    - {m}" for m in item.missing[:6])
    lines.append(f"  Uncertainty: {item.uncertainty}")
    return lines


def render_shortlist(shortlist: OpportunityShortlist, scenario: tuple[str, ...] = ()) -> str:
    by_id = {a.canonical_game_id: a for a in shortlist.assessments}
    lines = [
        "MetaSpike creator opportunities: a LOCAL, PRIVATE reading. Not a recommendation "
        "service; never published.",
        f"  as of {shortlist.as_of.isoformat()}   {shortlist.rules_version}   "
        f"distribution {shortlist.distribution}",
        f"  Thresholds are UNVALIDATED (robust z {NOTEWORTHY_ROBUST_Z:g}, 7-day discussion "
        "window). No historical evaluation has calibrated them.",
    ]
    if shortlist.contains_synthetic:
        lines += [
            "",
            "  *** THIS SHORTLIST IS BUILT FROM SYNTHETIC EVIDENCE. ***",
            "  Simulated readiness shows the rubric working on invented data. It is not a",
            "  real current recommendation for any game.",
        ]
        lines.extend(f"    {line}" for line in scenario)
    if shortlist.contains_unverified:
        lines.append("  Some inputs are of UNVERIFIED origin and count for nothing.")
    lines += ["", f"  {shortlist.summary}", "", "  Real order (verified evidence only):"]
    for entry in shortlist.entries:
        lines.append(
            f"    {entry.position:>2}. {entry.slug:<22} {_LABEL[entry.readiness]:<30} "
            f"ahead of next: {entry.ahead_of_next}"
        )
    lines += ["", "  SIMULATED order (synthetic counted as if real; NOT a recommendation):"]
    for entry in shortlist.simulated_entries:
        lines.append(
            f"    {entry.position:>2}. {entry.slug:<22} {_LABEL[entry.readiness]:<30} "
            f"ahead of next: {entry.ahead_of_next}"
        )
    for entry in shortlist.simulated_entries:
        lines.extend(_game(by_id[entry.canonical_game_id], entry.position))
    return "\n".join(lines) + "\n"
