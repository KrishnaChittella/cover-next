"""A SYNTHETIC scenario for exercising the evidence path. Nothing here is an observation.

The recorded inputs MetaSpike holds do not overlap: there are real pageviews for six
games and no recorded player history for any. So the integration of several sources is
demonstrated on invented data, and every piece of it is labelled as invented wherever
it appears. A report built from this scenario describes no real game's state.

What is invented, relative to the moment asked about:

    players   15-minute samples for four of the eligible apps: one current, one that
              stopped three hours ago, one reporting zero players, and one whose last
              hour and last day are incomplete. The other eligible apps are never sampled.
    news      announcements for three of those apps, written in the shape the Steam News
              adapter reads, then passed through the real adapter, classifier and
              promotion gate: an upcoming season with a date, a hotfix, a post that is
              only ever a candidate, and a post whose timing is not established.

Identity is not invented: the apps and the games they belong to come from the catalogue
state it is given. Samples start no earlier than the binding that ties an app to its
game, because a sample belongs to the entity its id was bound to when it was observed.
"""

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from metaspike.catalogue.eligibility import (
    SamplingTarget,
    bootstrap_scope,
    sampling_targets,
    steam_eligibility,
)
from metaspike.catalogue.generate import CatalogueState
from metaspike.domain.samples import SampleAttempt, SampleFlag, SampleStatus
from metaspike.domain.source_policy import DeploymentStage
from metaspike.domain.source_records import SourceRecord
from metaspike.domain.timeseries import Granularity
from metaspike.event_sources.ingest import ingest
from metaspike.event_sources.registry import build_steam_news, replay_news_client
from metaspike.event_sources.resolve import steam_app_bindings, steam_news_resolver
from metaspike.event_sources.steam_news import STEAM_NEWS_POLICY
from metaspike.event_sources.store import EventSourceStore
from metaspike.events.store import EventStore
from metaspike.pipeline.signal_features import STEAM_METRIC, STEAM_SOURCE
from metaspike.sources.limiter import LimiterPool
from metaspike.sources.steam_players import STEAM_CURRENT_PLAYERS_POLICY
from metaspike.storage.samples import LocalSampleStore

SCENARIO_NAME = "synthetic-overlap-v1"
HISTORY_DAYS = 21
_SLOT = Granularity.FIFTEEN_MINUTES.step
_HOUR = timedelta(hours=1)
_DAY = timedelta(days=1)
_RUN_ID = "synthetic-scenario"
_NOTE = (
    "SYNTHETIC. Written by MetaSpike's synthetic evidence scenario. Not a recording of "
    "any Steam response; every title, id and URL here is invented."
)


# How each game's invented pageviews end: a sharp rise, a sharp fall, or nothing.
PAGEVIEW_PATTERNS: dict[str, str] = {
    "dota-2": "rising",
    "rust": "rising",
    "counter-strike-2": "falling",
    "no-mans-sky": "flat",
    "elden-ring": "flat",
}


def write_synthetic_pageviews(
    directory: Path, articles: dict[str, str], through: date
) -> list[str]:
    """Invented daily pageviews in the shape the Wikimedia replay reads. SYNTHETIC.

    `articles` maps an article title to a pattern in `PAGEVIEW_PATTERNS`. Ninety days
    ending on `through`, steady with a weekly rhythm, then the pattern over the last
    three days. Nothing here is a recording of Wikimedia.
    """
    directory.mkdir(parents=True, exist_ok=True)
    lines = []
    for article, pattern in sorted(articles.items()):
        items = []
        for offset in range(90):
            day = through - timedelta(days=89 - offset)
            base = 1000 + 60 * (day.weekday() % 3)
            if offset >= 87 and pattern == "rising":
                base *= 4
            elif offset >= 87 and pattern == "falling":
                base //= 5
            items.append(
                {
                    "project": "en.wikipedia",
                    "article": article,
                    "granularity": "daily",
                    "timestamp": f"{day:%Y%m%d}00",
                    "access": "all-access",
                    "agent": "user",
                    "views": base,
                }
            )
        (directory / f"{article}.json").write_text(json.dumps({"items": items}), encoding="utf-8")
        lines.append(f"{article}: synthetic pageviews ({pattern})")
    return lines


@dataclass(frozen=True)
class SyntheticScenario:
    samples: LocalSampleStore
    events: EventStore
    event_sources: EventSourceStore
    # What was invented for which app, in words, for the report's header.
    description: tuple[str, ...]


def _attempt(target: SamplingTarget, slot: datetime, value: int) -> SampleAttempt:
    observed = slot + timedelta(seconds=20)
    return SampleAttempt(
        source=STEAM_SOURCE,
        metric=STEAM_METRIC,
        source_native_id=target.source_native_id,
        value=value,
        granularity=Granularity.FIFTEEN_MINUTES,
        scheduled_for=slot,
        slot_start=slot,
        observed_at=observed,
        received_at=observed,
        canonical_game_id=target.canonical_game_id,
        mapping_version=target.mapping_version,
        binding_ref=target.binding_ref,
        collection_run_id=_RUN_ID,
        attempt_id=f"synthetic-{target.source_native_id}-{slot:%Y%m%dT%H%M}",
        status=SampleStatus.OBSERVED,
        quality_flags=(SampleFlag.ZERO_REPORTED,) if value == 0 else (),
    )


def _level(base: int, slot: datetime, last: datetime, rising: bool) -> int:
    """An invented daily rhythm, and optionally a climb over the final day and a half."""
    rhythm = 1 + 0.25 * math.sin(2 * math.pi * (slot.hour + slot.minute / 60) / 24)
    hours_left = (last - slot) / _HOUR
    climb = 1 + 0.6 * max(0.0, 1 - hours_left / 36) if rising else 1.0
    return round(base * rhythm * climb)


def _write_samples(
    store: LocalSampleStore,
    state: CatalogueState,
    targets: tuple[SamplingTarget, ...],
    as_of: datetime,
) -> list[str]:
    # The newest slot that had wholly ended by the moment asked about.
    latest = Granularity.FIFTEEN_MINUTES.floor(as_of - _SLOT)
    hour = Granularity.HOURLY.floor(as_of) - _HOUR
    yesterday = Granularity.DAILY.floor(as_of) - _DAY
    roles = ("current", "stopped", "zero", "incomplete")
    lines = []
    for index, (target, role) in enumerate(zip(targets, roles, strict=False)):
        bound_from = next(
            binding.valid_from
            for binding in state.mapping.bindings
            if binding.key.source_id == target.source_native_id
            and binding.mapping_version == target.mapping_version
        )
        first = Granularity.FIFTEEN_MINUTES.floor(max(bound_from, as_of - HISTORY_DAYS * _DAY))
        first += _SLOT
        last = latest - 3 * _HOUR if role == "stopped" else latest
        slot = first
        while slot <= last:
            value = _level(800 * (index + 1), slot, last, rising=role == "current")
            if role == "zero" and slot >= latest - 2 * _HOUR:
                value = 0
            skip = role == "incomplete" and (
                # two of the last whole hour's four samples, and eight hours of yesterday
                (hour <= slot < hour + _HOUR and slot.minute < 30)
                or yesterday <= slot < yesterday + 8 * _HOUR
            )
            if not skip:
                store.append(_attempt(target, slot, value))
            slot += _SLOT
        lines.append(f"app {target.source_native_id}: synthetic player samples ({role})")
    lines.extend(
        f"app {target.source_native_id}: eligible, deliberately never sampled"
        for target in targets[len(roles) :]
    )
    return lines


def _post(
    gid: int, title: str, published: datetime, app: str, tags: bool = False
) -> dict[str, object]:
    post: dict[str, object] = {
        "gid": str(gid),
        "title": title,
        "url": f"https://example.invalid/synthetic/{gid}",
        "is_external_url": False,
        "author": "synthetic-author",
        "contents": "synthetic body that must never be stored",
        "feedlabel": "Community Announcements",
        "date": int(published.timestamp()),
        "feedname": "steam_community_announcements",
        "feed_type": 1,
        "appid": int(app),
    }
    if tags:
        post["tags"] = ["patchnotes"]
    return post


def _write_news(directory: Path, apps: tuple[str, ...], as_of: datetime) -> list[str]:
    """Invented announcements for up to three apps, as files the replay client serves."""
    directory.mkdir(parents=True, exist_ok=True)
    starts = (as_of + 3 * _DAY).date()
    dated = f"Season 5 starts {starts:%B} {starts.day}, {starts.year}"
    feeds: list[list[dict[str, object]]] = [
        [
            _post(7101, "Hotfix 2.0.1", as_of - _DAY, apps[0], tags=True),
            _post(7102, dated, as_of - 2 * _DAY, apps[0]),
            _post(7103, "Developer diary: what comes next", as_of - 4 * _DAY, apps[0]),
        ],
        [_post(7201, "Season 2 is coming soon", as_of - _DAY, apps[1])] if len(apps) > 1 else [],
        [],
    ]
    lines = []
    for app, posts in zip(apps, feeds, strict=False):
        body = {"appnews": {"appid": int(app), "newsitems": posts, "count": len(posts)}}
        (directory / f"news_{app}.json").write_text(
            json.dumps({"note": _NOTE, "status": 200, "body": body}, indent=1), encoding="utf-8"
        )
        lines.append(f"app {app}: {len(posts)} synthetic announcement(s)")
    return lines


def build_synthetic_scenario(
    root: Path, state: CatalogueState, records: list[SourceRecord], as_of: datetime
) -> SyntheticScenario:
    """Write the scenario's stores under `root` and return them. Deterministic."""
    eligibility = steam_eligibility(state, records, as_of)
    targets = sampling_targets(state, eligibility, bootstrap_scope(), as_of)
    samples = LocalSampleStore(root / "samples", lambda _: STEAM_CURRENT_PLAYERS_POLICY)
    description = _write_samples(samples, state, targets, as_of)

    apps = tuple(target.source_native_id for target in targets[:3])
    description += _write_news(root / "news", apps, as_of)
    events = EventStore(root / "events")
    event_sources = EventSourceStore(root / "event-sources")
    observed_at = as_of - _HOUR
    with replay_news_client(root / "news") as client:
        adapter = build_steam_news(
            client, LimiterPool(), max_requests=len(apps), now=lambda: observed_at
        )
        ingest(
            adapter,
            STEAM_NEWS_POLICY,
            apps,
            steam_news_resolver(steam_app_bindings(state, records, as_of)),
            event_sources,
            events,
            stage=DeploymentStage.PRIVATE_DEVELOPMENT,
        )
    return SyntheticScenario(
        samples=samples,
        events=events,
        event_sources=event_sources,
        description=tuple(description),
    )
