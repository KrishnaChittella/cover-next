"""Source readiness: what each source can contribute today, and what stops it. OFFLINE.

For every source family MetaSpike has built against, this reads, without calling the
source, reading a credential or touching the cloud:

    the declared policy      every permission, approval and per-stage clearance, exactly
                             as `SourcePolicy` states it; the refusal for each stage is
                             the policy's own `registration_refusal`, not a summary
    the code                 whether the adapter, offline replay and local store exist,
                             checked by importing them, and which environment variables
                             a live run would need (by NAME only: no value is read)
    the evidence             the committed fixtures and the default local stores, and
                             what standing each has: verified only for a pinned recording
                             (checked by digest), synthetic when the fixture is invented,
                             unverified otherwise, including a genuine recording that has
                             not been reviewed and pinned
    the way forward          the owner's actions and the next technical step, written in
                             this file beside each source, and labelled as such

Nothing here grants a permission. Unknown stays UNKNOWN and pending stays PENDING. A
working parser is not a permission, and a public endpoint is not a licence.
"""

import importlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal

from metaspike.community_sources.reddit import REDDIT_DATA_POLICY
from metaspike.creator_sources.youtube import YOUTUBE_DATA_POLICY
from metaspike.domain.model import FrozenModel
from metaspike.domain.source_policy import DeploymentStage, SourcePolicy, SourcePurpose
from metaspike.event_sources.steam_news import STEAM_NEWS_POLICY
from metaspike.evidence.acquisition import TRUSTED_ACQUISITION_STEP
from metaspike.evidence.provenance import PINNED_RECORDINGS, recording_digest
from metaspike.sources.credentials import (
    IGDB_CLIENT_ID_ENV,
    IGDB_CLIENT_SECRET_ENV,
    STEAM_WEB_API_KEY_ENV,
)
from metaspike.sources.igdb import IGDB_POLICY
from metaspike.sources.steam_catalogue import STEAM_CATALOGUE_POLICY
from metaspike.sources.steam_players import STEAM_CURRENT_PLAYERS_POLICY
from metaspike.sources.wikimedia import WIKIMEDIA_PAGEVIEWS_POLICY

READINESS_FORMAT: Final = "source_readiness_v1"
STAGE_ORDER: Final = (
    DeploymentStage.PRIVATE_DEVELOPMENT,
    DeploymentStage.PRIVATE_COLLECTION,
    DeploymentStage.PUBLIC_LAUNCH,
    DeploymentStage.COMMERCIAL_PRODUCTION,
)


class FixtureKind(StrEnum):
    # Committed as a recording of the real source's responses.
    RECORDING = "recording_of_the_source"
    # Written by hand or by code; never a response of the source.
    SYNTHETIC = "synthetic"


class EvidenceStanding(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    SYNTHETIC = "synthetic"
    ABSENT = "absent"


@dataclass(frozen=True)
class _Fixture:
    path: str
    kind: FixtureKind
    description: str


@dataclass(frozen=True)
class _Spec:
    family: str
    policy: SourcePolicy
    purpose: SourcePurpose
    role: str
    adapter: str
    replay: str | None
    store: str | None
    credentials: tuple[str, ...]
    fixtures: tuple[_Fixture, ...]
    local_stores: tuple[str, ...]
    # Written here from the policy's own clearance reasons and the project's docs.
    owner_actions: tuple[str, ...]
    third_party_answers: tuple[str, ...]
    next_step: str
    integration: str


SPECS: Final = (
    _Spec(
        family="wikimedia_pageviews",
        policy=WIKIMEDIA_PAGEVIEWS_POLICY,
        purpose=SourcePurpose.DEMAND_SIGNALS,
        role="demand: daily Wikipedia readership per game",
        adapter="metaspike.sources.wikimedia:WikimediaPageviewsAdapter",
        replay="metaspike.sources.replay:replay_client",
        store=None,
        credentials=(),
        fixtures=(
            _Fixture(
                "tests/fixtures/wikimedia",
                FixtureKind.RECORDING,
                "six articles, 2026-07-06 to 2026-10-03",
            ),
        ),
        local_stores=(),
        owner_actions=(
            "After the C2-F acceptance review, authorise a private development read of "
            "fresh pageviews (a new recording, reviewed and pinned).",
        ),
        third_party_answers=(),
        next_step="Record fresh pageviews for the bootstrap games under the existing "
        "adapter, review the recording and pin its digest; nothing else changes.",
        integration="Already integrated: EvidenceInputs.wikimedia -> demand family -> "
        "creator_opportunity_rules_v1 movement.",
    ),
    _Spec(
        family="igdb_catalogue",
        policy=IGDB_POLICY,
        purpose=SourcePurpose.CATALOGUE_METADATA,
        role="identity metadata only: names, dates, relationships; never demand",
        adapter="metaspike.sources.igdb:IgdbCatalogueAdapter",
        replay="metaspike.sources.replay:replay_client",
        store=None,
        credentials=(IGDB_CLIENT_ID_ENV, IGDB_CLIENT_SECRET_ENV),
        fixtures=(
            _Fixture(
                "tests/fixtures/catalogue",
                FixtureKind.RECORDING,
                "IGDB games and Steam app list, recorded 2026-10-05",
            ),
            _Fixture(
                "tests/fixtures/bootstrap_catalogue",
                FixtureKind.RECORDING,
                "the bootstrap entities, recorded 2026-10-05",
            ),
        ),
        local_stores=(),
        owner_actions=("For public launch: static, user-visible IGDB attribution in the product.",),
        third_party_answers=(
            "IGDB: whether an unmonetized but commercial-capable launch needs the "
            "partnership (per the policy's public-launch reason).",
        ),
        next_step="None needed for evidence: identity is already resolved from the "
        "pinned recordings. A refresh is a catalogue task, not a demand task.",
        integration="Already integrated: canonical identities and source bindings.",
    ),
    _Spec(
        family="steam_app_catalogue",
        policy=STEAM_CATALOGUE_POLICY,
        purpose=SourcePurpose.CATALOGUE_METADATA,
        role="identity metadata only: Steam app ids and names; never demand",
        adapter="metaspike.sources.steam_catalogue:SteamCatalogueAdapter",
        replay="metaspike.sources.replay:replay_client",
        store=None,
        credentials=(STEAM_WEB_API_KEY_ENV,),
        fixtures=(
            _Fixture(
                "tests/fixtures/catalogue",
                FixtureKind.RECORDING,
                "Steam app list with IGDB games, recorded 2026-10-05",
            ),
        ),
        local_stores=(),
        owner_actions=(
            "Choose the storage region and publish a privacy policy naming it (per the "
            "policy's private-collection reason).",
        ),
        third_party_answers=("Valve: commercial use is not addressed by the Steam Web API Terms.",),
        next_step="None needed for evidence: app bindings come from the pinned recording.",
        integration="Already integrated: Steam app bindings for eligibility.",
    ),
    _Spec(
        family="steam_concurrent_players",
        policy=STEAM_CURRENT_PLAYERS_POLICY,
        purpose=SourcePurpose.DEMAND_SIGNALS,
        role="demand: sampled concurrent players per Steam app",
        adapter="metaspike.sources.steam_players:SteamCurrentPlayersAdapter",
        replay="metaspike.sources.sampling_sources:replay_sampling_client",
        store="metaspike.storage.samples:LocalSampleStore",
        credentials=(STEAM_WEB_API_KEY_ENV,),
        fixtures=(
            _Fixture(
                "tests/fixtures/steam_players",
                FixtureKind.RECORDING,
                "one response for app 730, recorded 2026-10-06T01:25Z, key not recorded",
            ),
        ),
        local_stores=(".metaspike/samples",),
        owner_actions=(
            "Publish a privacy/data notice with a Steam Data section at "
            "https://krishnachittella.dev/privacy.",
            "Keep persistent Steam Data only in the United States (us-central1).",
            "Register the Web API key to the MetaSpike / krishnachittella.dev application.",
            "Authorise one supervised sampling session whose store is then reviewed.",
        ),
        third_party_answers=(
            "Valve: derived metrics and commercial use are not addressed by the terms, so "
            "nothing Steam-derived may be published.",
        ),
        next_step="Run `metaspike sample` in a supervised private session at a "
        "post-recording moment, write an acquisition record per response, and keep the "
        "store private; it stays UNVERIFIED until a reviewed recording is pinned.",
        integration="Defined: LocalSampleStore -> EvidenceInputs.samples -> players "
        "family. Counted only once verified.",
    ),
    _Spec(
        family="steam_news",
        policy=STEAM_NEWS_POLICY,
        purpose=SourcePurpose.EVENT_INTELLIGENCE,
        role="events: announcements read as dated events, never a demand figure",
        adapter="metaspike.event_sources.steam_news:SteamNewsAdapter",
        replay="metaspike.event_sources.registry:replay_news_client",
        store="metaspike.event_sources.store:EventSourceStore",
        credentials=(),
        fixtures=(
            _Fixture(
                "tests/fixtures/steam_news",
                FixtureKind.SYNTHETIC,
                "hand-written in the response shape; no real response is kept",
            ),
        ),
        local_stores=(".metaspike/event-sources",),
        owner_actions=(
            "The same Steam conditions as player sampling (privacy notice, storage "
            "region, far below 100,000 calls a day).",
            "Decide whether publisher-authored titles may be kept at all.",
            "Approve any historical backfill budget (none is approved).",
        ),
        third_party_answers=(
            "Valve: derived events and showing publisher text are not addressed by the terms.",
        ),
        next_step="Keep the existing supervised probe private; record acquisition "
        "records for its responses; promote events only through the existing gate.",
        integration="Defined: EventSourceStore -> ingest/promotion -> EventStore -> "
        "event intelligence -> timely_event. Counted only once verified.",
    ),
    _Spec(
        family="youtube_creator_supply",
        policy=YOUTUBE_DATA_POLICY,
        purpose=SourcePurpose.CREATOR_SUPPLY,
        role="creator supply: a bounded sample of existing videos; a lower bound only",
        adapter="metaspike.creator_sources.youtube:YouTubeAdapter",
        replay="metaspike.creator_sources.youtube:replay_youtube_client",
        store="metaspike.creator_sources.store:CreatorSupplyStore",
        credentials=(),
        fixtures=(),
        local_stores=(),
        owner_actions=(
            "Create one API project for this client and accept the YouTube API Services "
            "Terms (per the policy's private-collection reason).",
            "Provide deletion or refresh within 30 calendar days, and deletion on request.",
        ),
        third_party_answers=(
            "YouTube: the aggregation and derived-metrics clauses for counting videos per game.",
        ),
        next_step="Nothing until the owner creates the project and the clauses are "
        "answered. The adapter, quota and store are ready offline.",
        integration="Defined: CreatorSupplyStore -> creator-supply reading -> "
        "coverage_known. Counted only once verified.",
    ),
    _Spec(
        family="reddit_community_interest",
        policy=REDDIT_DATA_POLICY,
        purpose=SourcePurpose.COMMUNITY_INTEREST,
        role="community interest: sampled posts and themes; never a measure of demand",
        adapter="metaspike.community_sources.reddit:RedditAdapter",
        replay="metaspike.community_sources.reddit:RedditReplay",
        store="metaspike.community_sources.store:CommunityStore",
        credentials=(),
        fixtures=(),
        local_stores=(),
        owner_actions=(
            "Apply under Reddit's Responsible Builder Policy (reported; NOT VERIFIED from "
            "Reddit's own pages).",
            "A deletion process for content removed on Reddit, and short retention.",
        ),
        third_party_answers=(
            "Reddit: approval of the application (PENDING) and of any derived output.",
        ),
        next_step="Nothing until Reddit approves. The adapter refuses to be built with a "
        "network client while approval is pending.",
        integration="Defined: CommunityStore -> community reading -> candidate angles. "
        "Counted only once verified.",
    ),
)


class StageStatus(FrozenModel):
    stage: DeploymentStage
    # The policy's own reviewed statement for the stage, as written.
    declared_cleared: bool
    # Whether the source may be USED for its purpose at the stage: the policy's
    # registration_refusal, which also weighs approval and permissions.
    permitted: bool
    refusal: str | None = None


class PermissionRow(FrozenModel):
    name: str
    value: str


class Capability(FrozenModel):
    name: Literal["adapter", "offline_replay", "local_store"]
    reference: str | None
    present: bool


class EvidenceStatus(FrozenModel):
    path: str
    kind: str
    description: str
    present: bool
    files: int
    standing: EvidenceStanding
    why: str


class SourceReadiness(FrozenModel):
    family: str
    source: str
    purpose: SourcePurpose
    role: str
    stages: tuple[StageStatus, ...]
    # The furthest stage reached with every earlier stage also permitted.
    furthest_permitted_stage: DeploymentStage | None
    publication: str
    permissions: tuple[PermissionRow, ...]
    attribution_text: str | None
    policy_reviewed_at: str
    capabilities: tuple[Capability, ...]
    credentials_required: tuple[str, ...]
    evidence: tuple[EvidenceStatus, ...]
    verified_evidence_today: bool
    blockers: tuple[str, ...]
    owner_actions: tuple[str, ...]
    third_party_answers: tuple[str, ...]
    next_step: str
    integration: str


class ReadinessReport(FrozenModel):
    format: Literal["source_readiness_v1"] = READINESS_FORMAT
    distribution: Literal["private_development_only"] = "private_development_only"
    sources: tuple[SourceReadiness, ...]
    first_source: str
    statements: tuple[str, ...]


def _present(reference: str | None) -> bool:
    if reference is None:
        return False
    module, _, name = reference.partition(":")
    try:
        return hasattr(importlib.import_module(module), name)
    except ImportError:
        return False


def _evidence(root: Path, fixture: _Fixture) -> EvidenceStatus:
    directory = root / fixture.path
    files = sorted(p for p in directory.glob("*.json")) if directory.is_dir() else []
    if not files:
        standing, why = EvidenceStanding.ABSENT, "no file here"
    elif fixture.kind is FixtureKind.SYNTHETIC:
        standing, why = EvidenceStanding.SYNTHETIC, "invented in the source's shape"
    elif recording_digest(directory) in PINNED_RECORDINGS:
        standing, why = EvidenceStanding.VERIFIED, "content matches a pinned recording digest"
    else:
        standing, why = (
            EvidenceStanding.UNVERIFIED,
            "a recording, but its content matches no pinned digest: it has not been "
            "reviewed in, so it counts as unverified",
        )
    return EvidenceStatus(
        path=fixture.path,
        kind=fixture.kind.value,
        description=fixture.description,
        present=bool(files),
        files=len(files),
        standing=standing,
        why=why,
    )


def _local(root: Path, path: str) -> EvidenceStatus:
    directory = root / path
    files = [p for p in directory.rglob("*") if p.is_file()] if directory.is_dir() else []
    return EvidenceStatus(
        path=path,
        kind="local_store",
        description="the command's default local store",
        present=bool(files),
        files=len(files),
        standing=EvidenceStanding.UNVERIFIED if files else EvidenceStanding.ABSENT,
        why="a local store is read as found; nothing in it establishes who wrote it"
        if files
        else "empty or absent",
    )


def _permissions(policy: SourcePolicy) -> tuple[PermissionRow, ...]:
    return tuple(
        PermissionRow(name=name, value=value)
        for name, value in (
            ("approval", f"{policy.approval_required.value} / {policy.approval_status.value}"),
            ("raw_storage", policy.raw_storage_allowed.value),
            (
                "retention_days",
                "not stated" if policy.retention_days is None else str(policy.retention_days),
            ),
            ("historical_storage", policy.historical_storage_allowed.value),
            ("derived_metrics", policy.derived_metrics_allowed.value),
            ("redistribution", policy.redistribution_allowed.value),
            ("attribution", policy.attribution_required.value),
            ("commercial_use", policy.commercial_use_allowed.value),
            (
                "commercial_approval",
                f"{policy.commercial_approval_required.value} / "
                f"{policy.commercial_approval_status.value}",
            ),
            ("link_back", policy.display.link_back_required.value),
            ("branding", policy.display.branding_required.value),
        )
    )


def assess_source(spec: _Spec, root: Path) -> SourceReadiness:
    policy = spec.policy
    stages = []
    for stage in STAGE_ORDER:
        refusal = policy.registration_refusal(spec.purpose, stage)
        stages.append(
            StageStatus(
                stage=stage,
                declared_cleared=policy.cleared_for(stage),
                permitted=refusal is None,
                refusal=refusal,
            )
        )
    furthest: DeploymentStage | None = None
    for status in stages:
        if not status.permitted:
            break
        furthest = status.stage
    evidence = tuple(_evidence(root, f) for f in spec.fixtures) + tuple(
        _local(root, p) for p in spec.local_stores
    )
    verified = any(e.standing is EvidenceStanding.VERIFIED for e in evidence)
    blockers: list[str] = []
    if not verified:
        recorded = [e for e in evidence if e.kind == FixtureKind.RECORDING.value and e.present]
        blockers.append(
            "no verified observation: a recording exists but is not pinned"
            if recorded
            else "no verified observation: no recording of a real response exists"
        )
        blockers.append(f"origin verification: {TRUSTED_ACQUISITION_STEP}")
    blockers += [
        f"{status.stage.value}: {status.refusal}" for status in stages if not status.permitted
    ]
    unknown = [
        row.name
        for row in _permissions(policy)
        if row.value == "unknown" or row.value.endswith("/ pending")
    ]
    if unknown:
        blockers.append("UNKNOWN or PENDING in the policy: " + ", ".join(unknown))
    publication = policy.publication_refusal()
    return SourceReadiness(
        family=spec.family,
        source=policy.source,
        purpose=spec.purpose,
        role=spec.role,
        stages=tuple(stages),
        furthest_permitted_stage=furthest,
        publication="derived output may be published"
        if publication is None
        else f"not publishable: {publication}",
        permissions=_permissions(policy),
        attribution_text=policy.attribution_text,
        policy_reviewed_at=policy.reviewed_at.isoformat(),
        capabilities=(
            Capability(name="adapter", reference=spec.adapter, present=_present(spec.adapter)),
            Capability(name="offline_replay", reference=spec.replay, present=_present(spec.replay)),
            Capability(name="local_store", reference=spec.store, present=_present(spec.store)),
        ),
        credentials_required=spec.credentials,
        evidence=evidence,
        verified_evidence_today=verified,
        blockers=tuple(blockers),
        owner_actions=spec.owner_actions,
        third_party_answers=spec.third_party_answers,
        next_step=spec.next_step,
        integration=spec.integration,
    )


def first_source(sources: tuple[SourceReadiness, ...]) -> str:
    """The demand source that can contribute genuine evidence soonest under its policy.

    A demand source permitted for private collection, with a verified recording, comes
    first; then one permitted privately with a real (unpinned) recording; catalogue
    metadata is never demand and never chosen.
    """
    demand = [s for s in sources if s.purpose is SourcePurpose.DEMAND_SIGNALS]

    def rank(s: SourceReadiness) -> tuple[int, int, str]:
        reach = (
            -1
            if s.furthest_permitted_stage is None
            else STAGE_ORDER.index(s.furthest_permitted_stage)
        )
        return (-reach, -int(s.verified_evidence_today), s.family)

    return sorted(demand, key=rank)[0].family


def assess_readiness(root: Path) -> ReadinessReport:
    """Every source, read offline from its policy, its code and the files under `root`."""
    sources = tuple(assess_source(spec, root) for spec in SPECS)
    return ReadinessReport(
        sources=sources,
        first_source=first_source(sources),
        statements=(
            "Offline: no source was contacted, no credential was read, no cloud was "
            "queried. Credential requirements are listed by variable NAME only.",
            "Stage results are each policy's own registration_refusal for the source's "
            "purpose. Nothing here grants or revises a permission.",
            "Verified means a recording whose content matches a pinned digest. A local "
            "store, an unpinned recording and an acquisition record are unverified.",
            f"Trusted live acquisition: {TRUSTED_ACQUISITION_STEP}.",
            "Owner actions and next steps are written in source_readiness.py from each "
            "policy's clearance reasons; statements the policy marks as reported are "
            "NOT VERIFIED.",
        ),
    )


def render_readiness(report: ReadinessReport) -> str:
    lines = [
        "SOURCE READINESS (offline; private development only)",
        "",
    ]
    lines += [f"  {s}" for s in report.statements]
    for s in report.sources:
        furthest = "none" if s.furthest_permitted_stage is None else s.furthest_permitted_stage
        lines += [
            "",
            f"{s.family}  [{s.source}, {s.purpose.value}]",
            f"  role: {s.role}",
            f"  furthest permitted stage: {furthest}",
        ]
        for st in s.stages:
            declared = "declared cleared" if st.declared_cleared else "declared not cleared"
            lines.append(
                f"    {st.stage.value:<22} "
                + ("use permitted" if st.permitted else f"use BLOCKED: {st.refusal}")
                + ("" if st.permitted == st.declared_cleared else f" ({declared})")
            )
        lines.append(f"  publication: {s.publication}")
        lines.append(
            "  policy: "
            + ", ".join(f"{p.name}={p.value}" for p in s.permissions)
            + f" (reviewed {s.policy_reviewed_at})"
        )
        lines.append(
            "  code: "
            + ", ".join(f"{c.name} {'present' if c.present else 'absent'}" for c in s.capabilities)
        )
        lines.append(
            "  credentials needed for a live run (names only, not checked): "
            + (", ".join(s.credentials_required) or "none")
        )
        for e in s.evidence:
            lines.append(
                f"  evidence {e.path}: {e.standing.value.upper()} ({e.files} file(s)); {e.why}"
            )
        lines.append(f"  verified evidence today: {'YES' if s.verified_evidence_today else 'NO'}")
        lines += [f"  blocker: {b}" for b in s.blockers]
        lines += [f"  owner action: {a}" for a in s.owner_actions]
        lines += [f"  needs an answer from: {a}" for a in s.third_party_answers]
        lines.append(f"  next step: {s.next_step}")
        lines.append(f"  integration: {s.integration}")
    lines += ["", f"FIRST SOURCE FOR GENUINE DEMAND EVIDENCE: {report.first_source}", ""]
    return "\n".join(lines)
