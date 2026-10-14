"""A game evidence report as text a developer can read. No source text is printed.

Counts, states, dates and the project's own names only: an announcement's title and a
source record's title stay in the stores they are kept in.
"""

from metaspike.events.heat import Availability as EventAvailability
from metaspike.evidence.model import (
    EvidenceKind,
    EvidenceReport,
    EvidenceState,
    GameEvidence,
    InputProvenance,
    Origin,
    SourceEvidence,
    Standing,
)

_RULE = "=" * 78


def _origin(provenance: InputProvenance) -> str:
    if provenance.origin is Origin.NONE:
        return "not given"
    label = {
        Standing.SYNTHETIC: "SYNTHETIC",
        Standing.UNVERIFIED: f"UNVERIFIED ORIGIN ({provenance.origin.value})",
        Standing.VERIFIED: f"verified {provenance.origin.value}",
    }[provenance.standing]
    return f"{label}  {provenance.reference}" + (
        f"  ({provenance.note})" if provenance.note else ""
    )


def _when(item: SourceEvidence) -> str:
    return "-" if item.latest_observed_at is None else f"{item.latest_observed_at:%Y-%m-%d %H:%MZ}"


def _source_line(item: SourceEvidence) -> str:
    marks = []
    if item.provenance.synthetic:
        marks.append("SYNTHETIC")
    if item.provenance.unverified:
        marks.append("UNVERIFIED ORIGIN")
    if item.restricted:
        marks.append("private only")
    ids = ", ".join(binding.source_native_id for binding in item.bindings) or "-"
    line = (
        f"  {item.source:<22}{item.kind.value:<19}{item.state.value:<15}"
        f"latest {_when(item):<18}ids {ids}"
    )
    if marks:
        line += f"   [{', '.join(marks)}]"
    described_below = (
        item.kind in (EvidenceKind.CREATOR_SUPPLY, EvidenceKind.COMMUNITY_INTEREST)
        and item.policy is not None
    )
    if item.reason and item.state is not EvidenceState.AVAILABLE and not described_below:
        line += f"\n{'':24}{item.reason}"
    return line


def _game(game: GameEvidence) -> list[str]:
    identity = game.identity
    lines = [
        "",
        _RULE,
        f"{identity.name}  ({identity.slug})",
        f"  canonical id {identity.canonical_game_id}  [{identity.id_persistence}]  "
        f"lifecycle {identity.lifecycle_state.value}",
    ]
    lines.append("  Identity as bound at this moment (source ids, never titles):")
    for record in identity.bound_records:
        if not record.content_known:
            lines.append(
                f"    {record.source}:{record.source_id:<10} bound (mapping "
                f"v{record.mapping_version}, from {record.valid_from:%Y-%m-%d %H:%M}Z); its "
                "content was NOT held at this moment"
            )
            continue
        dates = "; ".join(record.release_dates) or "no release date"
        lines.append(
            f"    {record.source}:{record.source_id:<10} {record.product_type or '?':<14}"
            f"{dates}  (mapping v{record.mapping_version}, "
            f"from {record.valid_from:%Y-%m-%d %H:%M}Z)"
        )
    if not identity.bound_records:
        lines.append("    no source record was bound to this game at this moment")
    lines.extend(
        f"    quarantined, NOT bound: {record.source}:{record.source_id} ({record.reason})"
        for record in identity.unresolved_records
    )
    lines.extend(f"    relationship: {relationship}" for relationship in identity.relationships)

    lines.append("  Sources:")
    lines.extend(_source_line(item) for item in game.sources)

    lines.append("  Demand (each family against its own history; never added or averaged):")
    breadth = game.demand.evidence_breadth
    lines.append(
        f"    families that could observe this game: {breadth.applicable_count}; "
        f"with current VERIFIED observations: {game.independent_demand_families}"
    )
    if game.unconfirmed_demand_families:
        lines.append(
            "    current but NOT verified, so not counted: "
            + ", ".join(family.value for family in game.unconfirmed_demand_families)
        )
    for movement in game.movements:
        tag = {
            Standing.SYNTHETIC: "  [SYNTHETIC]",
            Standing.UNVERIFIED: "  [UNVERIFIED ORIGIN]",
        }.get(movement.standing, "")
        relative = ("log_lift", "robust_z", "velocity", "acceleration")
        # A restricted source's own values are not printed, as `sample` never prints them.
        shown = (
            ("current_level", "expected_level", *relative)
            if movement.levels_showable or movement.synthetic
            else relative
        )
        values = ", ".join(
            f"{name} {movement.features[name]}" for name in shown if name in movement.features
        )
        lines.append(
            f"    {movement.family.value} ({movement.metric}, maturity {movement.maturity}, "
            f"bucket coverage {movement.coverage:.2f}){tag}"
        )
        lines.append(f"      {values}")
        if movement.withheld:
            lines.append(
                f"      not usable for scoring (shadow-only): {', '.join(movement.withheld)}"
            )
    if not game.movements:
        lines.append("    no family has a movement to report")
    for sample in game.sample_completeness:
        hour, day = sample.last_hour, sample.last_day
        level = (
            "no sample"
            if sample.current_level is None
            else ("a reported ZERO" if sample.zero_reported else "a reported value above zero")
        )
        lines.append(
            f"    app {sample.source_native_id}: latest sample is {level}; last whole hour "
            f"{hour.valid_count}/{hour.expected_count} samples "
            f"({'complete enough' if hour.valid else 'INCOMPLETE'}); last whole day "
            f"{day.valid_samples}/{day.expected_samples} samples, {day.valid_hours}/24 valid "
            f"hours ({'valid' if day.valid else 'INCOMPLETE'})"
        )

    score = game.score_v0
    if score.value is None:
        reason = "-" if score.unscored_reason is None else score.unscored_reason.value
        lines.append(f"  Score v0 (pageviews only, unchanged): unscored ({reason})")
    else:
        confidence = game.confidence_v0
        detail = (
            ""
            if confidence is None
            else f", confidence {confidence.value:.0f} ({confidence.band.value})"
        )
        stale = game.source("wikimedia_pageviews").state is EvidenceState.STALE
        lines.append(
            f"  Score v0 (pageviews only, unchanged): {score.value:.1f}{detail}"
            + ("   [its pageviews are STALE at this moment]" if stale else "")
        )

    events = game.events
    if events.availability is EventAvailability.AVAILABLE:
        standing = game.source("steam_news").provenance.standing
        if not game.events_verified:
            lines.append(
                f"  Events below come from {standing.value.upper()} announcements: a "
                "diagnostic, NOT verified real-world events."
            )
        lines.append(
            f"  Events: coverage current (last look {events.coverage_checked_at:%Y-%m-%d %H:%MZ}); "
            f"event heat {events.event_heat:.3f}; {len(events.contributions)} relevant event(s)"
        )
    else:
        reasons = ", ".join(reason.value for reason in events.unavailable_reasons)
        lines.append(f"  Events: UNAVAILABLE ({reasons}). That is not the same as no events.")
    for item in game.upcoming:
        lines.append(
            f"    upcoming: {item.event_type.value} starts {item.starts_at:%Y-%m-%d %H:%MZ} "
            f"({item.time_precision.value} precision), in {item.hours_until_start:.0f} h"
        )
    counts = game.candidates
    if counts.total:
        lines.append(
            f"    announcements read: {counts.total}; promoted to events {counts.promoted}; "
            f"candidates only {counts.not_promoted} (timing not established: "
            f"{counts.timing_not_established}); refusals {counts.refusals}"
        )

    supply = game.creator_supply
    if supply is not None:
        row = game.source("youtube_data")
        mark = {
            Standing.SYNTHETIC: "  [SYNTHETIC]",
            Standing.UNVERIFIED: "  [UNVERIFIED ORIGIN]",
        }.get(row.provenance.standing, "")
        lines.append(
            f"  Creator supply: a SAMPLE, not the market ({supply.state.value}){mark}. "
            "Total competition: UNKNOWN."
        )
        lines.append(f"    {supply.summary}")
        for asked in supply.queries:
            detail = (
                ""
                if asked.error_code is None
                else f" ({asked.error_code.value}"
                + (f": {asked.api_error_reason}" if asked.api_error_reason else "")
                + ")"
            )
            lines.append(
                f"    query sample {asked.query_id[:12]}: {asked.completeness.value}{detail}, "
                f"{asked.pages_fetched} page(s), {asked.videos_returned} result(s), "
                f"retrieved {asked.retrieved_at:%Y-%m-%d %H:%MZ}"
            )
        if supply.candidate_videos:
            lines.append(
                f"    sampled videos {supply.candidate_videos} (returned twice: "
                f"{supply.duplicate_results}); supported {supply.supported_videos} from "
                f"{supply.supported_channels} channel(s), most from one channel "
                f"{supply.most_videos_from_one_channel}; published in the last 24 h "
                f"{supply.supported_published_last_24h}, last 7 d "
                f"{supply.supported_published_last_7d}; view count held for "
                f"{supply.supported_with_view_count}, absent for "
                f"{supply.supported_without_view_count}"
            )
        for video in supply.videos:
            lines.append(
                f"      {video.video_id}  {video.association.value:<10}"
                f"{', '.join(video.reasons)}  published {video.published_at:%Y-%m-%d %H:%MZ}"
            )
    discussion = game.community_interest
    if discussion is not None:
        row = game.source("reddit_data")
        mark = {
            Standing.SYNTHETIC: "  [SYNTHETIC]",
            Standing.UNVERIFIED: "  [UNVERIFIED ORIGIN]",
        }.get(row.provenance.standing, "")
        lines.append(
            f"  Community interest: a SAMPLE of discussion, not the community "
            f"({discussion.state.value}){mark}. Total discussion: UNKNOWN."
        )
        lines.append(f"    {discussion.summary}")
        for searched in discussion.queries:
            where = "all of Reddit" if searched.subreddit is None else f"r/{searched.subreddit}"
            failure = "" if searched.error_code is None else f" ({searched.error_code.value})"
            lines.append(
                f"    query {searched.query_id[:12]} in {where} [{searched.scope.value}]: "
                f"{searched.completeness.value}{failure}, {searched.pages_fetched} page(s), "
                f"{searched.posts_returned} post(s), "
                f"retrieved {searched.retrieved_at:%Y-%m-%d %H:%MZ}"
            )
        if discussion.candidate_posts:
            lines.append(
                f"    posts {discussion.candidate_posts} (returned twice: "
                f"{discussion.duplicate_results}); supported {discussion.supported_posts} in "
                f"{discussion.supported_subreddits} communit(ies); created in the last 24 h "
                f"{discussion.supported_created_last_24h}, last 7 d "
                f"{discussion.supported_created_last_7d}; score held for "
                f"{discussion.supported_with_score}, absent for "
                f"{discussion.supported_without_score}; comment count held for "
                f"{discussion.supported_with_comment_count}, absent for "
                f"{discussion.supported_without_comment_count}"
            )
        for post in discussion.discussions:
            themes = ", ".join(theme.value for theme in post.themes) or "unclassified"
            lines.append(
                f"      {post.post_id}  r/{post.subreddit:<16}{post.association.value:<10}"
                f"{', '.join(post.reasons)}  themes: {themes}"
            )
        for angle in discussion.angles:
            posts = ", ".join(p.post_id for p in angle.supporting_posts)
            lines.append(
                f"    CANDIDATE ANGLE (not validated): {' / '.join(angle.kinds)} — about "
                f"{', '.join(angle.about)}; {len(angle.supporting_posts)} supporting posts "
                f"({posts})"
            )
        if discussion.observed_once:
            lines.append(
                "    themes seen in one post only (not angles): "
                + ", ".join(discussion.observed_once)
            )
    lines.append(f"  Usable now: {', '.join(game.usable_sources) or 'nothing'}")
    if game.restricted_sources:
        lines.append(f"  Private development only: {', '.join(game.restricted_sources)}")
    if game.unverified_sources:
        lines.append(f"  UNVERIFIED ORIGIN, not counted: {', '.join(game.unverified_sources)}")
    if game.synthetic_sources:
        lines.append(f"  SYNTHETIC, not observations: {', '.join(game.synthetic_sources)}")
    lines.append("  Still missing before a creator-opportunity recommendation could be justified:")
    lines.extend(f"    - {gap.text}" for gap in game.gaps)
    return lines


def render(report: EvidenceReport, scenario: tuple[str, ...] = ()) -> str:
    lines = [
        "MetaSpike game evidence: a LOCAL, PRIVATE reading. Not an artifact; never published.",
        f"  as of {report.as_of.isoformat()}   stage {report.stage.value}   "
        f"{report.report_version}   distribution {report.distribution}",
        "  Offline: no source was called, no credential was read, nothing was stored.",
    ]
    if report.contains_synthetic:
        lines += [
            "",
            "  *** THIS REPORT CONTAINS SYNTHETIC EVIDENCE. ***",
            "  What is marked SYNTHETIC was invented to exercise the integration. It is not",
            "  an observation of any game and supports no conclusion about one.",
        ]
        lines.extend(f"    {line}" for line in scenario)
    if report.contains_unverified:
        lines += [
            "",
            "  This report reads inputs of UNVERIFIED ORIGIN. They are shown for inspection",
            "  and are never counted as confirmed observations of a game.",
        ]
    lines.append("\n  Inputs:")
    lines.extend(f"    {name:<24}{_origin(item)}" for name, item in report.inputs.items())
    lines.append("\n  Source policies (as the repository states them):")
    for policy in report.policies:
        cleared = ", ".join(stage.value for stage in policy.cleared_stages) or "none"
        lines.append(
            f"    {policy.source:<22}{policy.purpose.value:<20}cleared: {cleared}; "
            f"publishable: {'yes' if policy.publishable else 'NO'}"
        )
        if policy.public_launch_refusal:
            lines.append(f"{'':26}public launch: {policy.public_launch_refusal}")
    absent = f"{EvidenceState.NOT_INTEGRATED.value}: no source, no policy"
    if not any(policy.purpose.value == "creator_supply" for policy in report.policies):
        lines.append(f"    {'creator supply':<22}{'-':<20}{absent}")
    if not any(policy.purpose.value == "community_interest" for policy in report.policies):
        lines.append(f"    {'community interest':<22}{'-':<20}{absent}")
    for game in report.games:
        lines.extend(_game(game))
    kinds = ", ".join(kind.value for kind in EvidenceKind)
    lines += ["", _RULE, f"Evidence kinds are kept apart and never blended: {kinds}."]
    return "\n".join(lines) + "\n"
