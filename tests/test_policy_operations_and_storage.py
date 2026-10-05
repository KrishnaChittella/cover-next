import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from metaspike import cli
from metaspike.catalogue.legacy_ids import canonical_id_for_legacy
from metaspike.catalogue.seed import load_seed_catalogue
from metaspike.domain.signals import SignalKey
from metaspike.domain.source_policy import (
    ApprovalStatus,
    FieldRule,
    LimitBasis,
    Permission,
    QuotaBudget,
    QuotaPeriod,
    RateLimit,
    Requirement,
    SourcePolicy,
)
from metaspike.domain.timeseries import Granularity, TimeWindow, start_of_day
from metaspike.features.attention import compute_attention_features
from metaspike.pipeline.collection import collect, window_ending
from metaspike.pipeline.normalize import latest_due_period
from metaspike.pipeline.run import run_pipeline
from metaspike.scoring.v0 import V0_INPUT_SIGNAL, confidence_v0
from metaspike.sources.registry import SourceRegistry
from metaspike.sources.replay import replay_client
from metaspike.sources.wikimedia import WIKIMEDIA_PAGEVIEWS_POLICY, WikimediaPageviewsAdapter
from metaspike.storage.base import ObservationStore, StorageCorruptionError, StoragePolicyError
from metaspike.storage.local import LocalObservationStore
from tests.conftest import (
    GAME_ID,
    RECORDED_END,
    RECORDINGS,
    RUN_TIME,
    mock_client,
    series_of,
    spiking,
)

WIKIMEDIA = WIKIMEDIA_PAGEVIEWS_POLICY


def policy(**changes: Any) -> SourcePolicy:
    return WIKIMEDIA.model_copy(update=changes)


def with_lag(seconds: int) -> SourcePolicy:
    operations = WIKIMEDIA.operations.model_copy(
        update={"expected_publication_lag_seconds": seconds}
    )
    return policy(operations=operations)


def always(source_policy: SourcePolicy) -> Callable[[str], SourcePolicy]:
    return lambda source: source_policy


def registry_for(client: httpx.Client, source_policy: SourcePolicy) -> SourceRegistry:
    registry = SourceRegistry()
    registry.register(WikimediaPageviewsAdapter(client), source_policy)
    return registry


# --- rate limits and quota ---------------------------------------------------------


def test_wikimedia_policy_carries_documented_rate_metadata() -> None:
    limit = WIKIMEDIA.operations.rate_limit
    assert (limit.max_requests, limit.per_seconds, limit.max_concurrency) == (200, 60, 1)
    assert limit.basis is LimitBasis.DOCUMENTED
    assert limit.min_interval_seconds == pytest.approx(0.3)
    assert WIKIMEDIA.operations.quota is None
    assert WIKIMEDIA.operations.cache_max_age_seconds is None
    assert any("Rate_limits" in reference for reference in WIKIMEDIA.references)


def test_a_source_with_a_daily_quota_and_cache_limit_is_representable() -> None:
    operations = WIKIMEDIA.operations.model_copy(
        update={
            "rate_limit": RateLimit(
                max_requests=4, per_seconds=1, max_concurrency=2, basis=LimitBasis.DOCUMENTED
            ),
            "quota": QuotaBudget(
                limit=10_000, unit="units", period=QuotaPeriod.DAY, basis=LimitBasis.DOCUMENTED
            ),
            "cache_max_age_seconds": 86_400,
        }
    )
    restricted = policy(source="quota_source", operations=operations)

    assert restricted.operations.rate_limit.min_interval_seconds == 0.25
    assert restricted.operations.quota is not None
    assert (restricted.operations.quota.limit, restricted.operations.quota.period) == (
        10_000,
        QuotaPeriod.DAY,
    )
    assert restricted.operations.cache_max_age_seconds == 86_400


def test_operational_limits_are_mandatory_and_validated() -> None:
    without_operations = WIKIMEDIA.model_dump()
    del without_operations["operations"]
    with pytest.raises(ValueError, match="operations"):
        SourcePolicy.model_validate(without_operations)
    for field in ("max_requests", "per_seconds", "max_concurrency"):
        with pytest.raises(ValueError):
            RateLimit.model_validate(WIKIMEDIA.operations.rate_limit.model_dump() | {field: 0})
    with pytest.raises(ValueError):
        QuotaBudget(limit=0, unit="requests", period=QuotaPeriod.DAY, basis=LimitBasis.OBSERVED)


def test_methodology_publishes_the_operational_policy(artifacts_dir: Path) -> None:
    with replay_client(RECORDINGS) as client:
        run_pipeline(
            load_seed_catalogue(),
            registry_for(client, WIKIMEDIA),
            window_ending(RECORDED_END),
            artifacts_dir,
            clock=lambda: RUN_TIME,
        )
    published = json.loads((artifacts_dir / "methodology.json").read_text())["sources"][0]
    assert published["signals"] == [{"metric": "pageviews", "granularity": "daily"}]
    operations = published["policy"]["operations"]
    assert operations["rate_limit"]["max_requests"] == 200
    assert operations["expected_publication_lag_seconds"] == 86_400
    assert operations["publication_lag_basis"] == "observed"
    assert published["policy"]["field_rules"][0]["field"] == "pageviews"


# --- commercial posture ------------------------------------------------------------


def test_commercial_production_requires_an_explicit_commercial_permission() -> None:
    assert WIKIMEDIA.commercial_production_permitted
    for uncertain in (Permission.UNKNOWN, Permission.RESTRICTED, Permission.PROHIBITED):
        unclear = policy(commercial_use_allowed=uncertain)
        assert unclear.collection_permitted  # may still be studied privately
        assert not unclear.commercial_production_permitted
    pending = policy(approval_required=Requirement.REQUIRED, approval_status=ApprovalStatus.PENDING)
    assert not pending.commercial_production_permitted


# --- source-specific expected publication lag --------------------------------------


@pytest.mark.parametrize(
    ("granularity", "lag", "observed_at", "expected"),
    [
        # One hour after the 4th ended: with a 24h lag only the 3rd is due yet.
        (
            Granularity.DAILY,
            timedelta(hours=24),
            datetime(2026, 10, 5, 0, 55, tzinfo=UTC),
            datetime(2026, 10, 3, tzinfo=UTC),
        ),
        (
            Granularity.DAILY,
            timedelta(0),
            datetime(2026, 10, 5, 0, 55, tzinfo=UTC),
            datetime(2026, 10, 4, tzinfo=UTC),
        ),
        (
            Granularity.DAILY,
            timedelta(hours=6),
            datetime(2026, 10, 5, 5, 59, tzinfo=UTC),
            datetime(2026, 10, 3, tzinfo=UTC),
        ),
        (
            Granularity.DAILY,
            timedelta(hours=6),
            datetime(2026, 10, 5, 6, 0, tzinfo=UTC),
            datetime(2026, 10, 4, tzinfo=UTC),
        ),
        (
            Granularity.HOURLY,
            timedelta(minutes=20),
            datetime(2026, 10, 5, 14, 10, tzinfo=UTC),
            datetime(2026, 10, 5, 12, tzinfo=UTC),
        ),
        (
            Granularity.FIFTEEN_MINUTES,
            timedelta(minutes=5),
            datetime(2026, 10, 5, 14, 20, tzinfo=UTC),
            datetime(2026, 10, 5, 14, 0, tzinfo=UTC),
        ),
    ],
)
def test_a_period_is_due_once_it_has_ended_and_the_lag_has_passed(
    granularity: Granularity, lag: timedelta, observed_at: datetime, expected: datetime
) -> None:
    assert latest_due_period(granularity, observed_at, lag) == expected


def collect_with_latest_day_unpublished(source_policy: SourcePolicy) -> Any:
    """Collect at 00:55 UTC on the 5th, when the source has data only up to the 3rd."""
    observed_at = datetime(2026, 10, 5, 0, 55, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        items = [
            {"timestamp": f"{day:%Y%m%d}00", "granularity": "daily", "views": 100 + day.day}
            for day in (date(2026, 7, 7) + timedelta(days=offset) for offset in range(89))
        ]
        return httpx.Response(200, json={"items": items})

    report = collect(
        load_seed_catalogue(),
        registry_for(mock_client(handler), source_policy),
        window_ending(date(2026, 10, 4)),
        observed_at,
        clock=lambda: observed_at,
    )
    signal = report.signals_for(GAME_ID).get(V0_INPUT_SIGNAL)
    assert signal is not None and signal.series is not None
    return signal.series


def test_normal_publication_lag_is_not_counted_as_stale() -> None:
    series = collect_with_latest_day_unpublished(WIKIMEDIA)  # 24h expected lag
    features = compute_attention_features(series)

    assert series.window_end == start_of_day(date(2026, 10, 4))
    assert series.latest_due == start_of_day(date(2026, 10, 3))
    assert features.as_of_day == date(2026, 10, 3)
    assert features.staleness_days == 0
    assert confidence_v0(features, 1).factors.freshness == 1.0


def test_the_same_data_is_stale_for_a_source_expected_to_publish_immediately() -> None:
    series = collect_with_latest_day_unpublished(with_lag(0))
    features = compute_attention_features(series)

    assert series.latest_due == start_of_day(date(2026, 10, 4))
    assert features.staleness_days == 1
    assert confidence_v0(features, 1).factors.freshness == 0.75


def test_data_later_than_its_expected_lag_is_still_reported_stale() -> None:
    values: list[float | None] = [*spiking()[3:], None, None, None]
    features = compute_attention_features(series_of(values))
    assert features.staleness_days == 3


# --- field-level and display rules -------------------------------------------------


def test_field_rules_can_only_narrow_the_source_policy() -> None:
    assert WIKIMEDIA.field_display_permitted("pageviews")
    assert WIKIMEDIA.field_storage_permitted("pageviews")
    assert WIKIMEDIA.field_display_permitted("a_field_with_no_rule")

    hidden = policy(
        field_rules=(
            FieldRule(
                field="pageviews",
                storage_allowed=Permission.ALLOWED,
                display_allowed=Permission.RESTRICTED,
            ),
        )
    )
    assert hidden.field_storage_permitted("pageviews")
    assert not hidden.field_display_permitted("pageviews")

    no_redistribution = policy(redistribution_allowed=Permission.UNKNOWN)
    assert not no_redistribution.field_display_permitted("pageviews")
    with pytest.raises(ValueError, match="same field"):
        SourcePolicy.model_validate(
            WIKIMEDIA.model_dump() | {"field_rules": [WIKIMEDIA.field_rules[0]] * 2}
        )


def test_a_display_restriction_withholds_values_but_not_the_score(artifacts_dir: Path) -> None:
    hidden = policy(
        field_rules=(
            FieldRule(
                field="pageviews",
                storage_allowed=Permission.ALLOWED,
                display_allowed=Permission.UNKNOWN,
            ),
        )
    )
    with replay_client(RECORDINGS) as client:
        run_pipeline(
            load_seed_catalogue(),
            registry_for(client, hidden),
            window_ending(RECORDED_END),
            artifacts_dir,
            clock=lambda: RUN_TIME,
        )
    game = json.loads((artifacts_dir / "games" / "minecraft.json").read_text())
    assert game["history"] == []
    assert game["score"]["value"] == 12.2


def test_display_requirements_are_recorded() -> None:
    assert WIKIMEDIA.display.link_back_required is Requirement.NOT_REQUIRED
    assert WIKIMEDIA.display.branding_required is Requirement.NOT_REQUIRED
    assert "endorsement" in WIKIMEDIA.display.note


# --- storage -----------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> LocalObservationStore:
    return LocalObservationStore(tmp_path / "store", always(WIKIMEDIA))


def window_of(days: int) -> TimeWindow:
    return TimeWindow.of_days(RECORDED_END - timedelta(days=days - 1), RECORDED_END)


def test_local_store_satisfies_the_storage_interface(store: LocalObservationStore) -> None:
    interface: ObservationStore = store
    assert interface.signals(GAME_ID) == ()


def test_stored_series_reads_back_identically(store: LocalObservationStore) -> None:
    original = series_of(spiking())
    assert store.write(original) == 90

    restored = store.read(GAME_ID, V0_INPUT_SIGNAL, window_of(90), as_of=RUN_TIME)
    assert restored.values() == original.values()
    assert compute_attention_features(restored) == compute_attention_features(original)
    assert store.signals(GAME_ID) == (V0_INPUT_SIGNAL,)


def test_writing_the_same_data_again_stores_nothing(store: LocalObservationStore) -> None:
    store.write(series_of(spiking()))
    path = next((store._root).rglob("*.jsonl"))
    before = path.read_bytes()

    assert store.write(series_of(spiking())) == 0
    assert path.read_bytes() == before


def test_missing_periods_are_not_stored_and_come_back_as_missing(
    store: LocalObservationStore,
) -> None:
    values = spiking()
    values[40] = None
    assert store.write(series_of(values)) == 89
    restored = store.read(GAME_ID, V0_INPUT_SIGNAL, window_of(90), as_of=RUN_TIME)
    assert restored.values()[40] is None and restored.values() == values


def test_a_revision_is_kept_beside_the_original_for_point_in_time_reads(
    store: LocalObservationStore,
) -> None:
    store.write(series_of([100.0, 200.0]))
    later = RUN_TIME + timedelta(days=1)
    revised = series_of([100.0, 250.0])
    revised = revised.model_copy(
        update={
            "points": tuple(p.model_copy(update={"observed_at": later}) for p in revised.points)
        }
    )
    assert store.write(revised) == 1  # only the changed period gains a version

    as_first_seen = store.read(GAME_ID, V0_INPUT_SIGNAL, window_of(2), as_of=RUN_TIME)
    as_revised = store.read(GAME_ID, V0_INPUT_SIGNAL, window_of(2), as_of=later)
    before_any = store.read(
        GAME_ID, V0_INPUT_SIGNAL, window_of(2), as_of=RUN_TIME - timedelta(hours=1)
    )
    assert as_first_seen.values() == [100.0, 200.0]
    assert as_revised.values() == [100.0, 250.0]
    assert before_any.values() == [None, None]


def test_storage_is_refused_when_the_policy_does_not_allow_history(tmp_path: Path) -> None:
    for refusing in (
        policy(historical_storage_allowed=Permission.UNKNOWN),
        policy(
            field_rules=(
                FieldRule(
                    field="pageviews",
                    storage_allowed=Permission.PROHIBITED,
                    display_allowed=Permission.PROHIBITED,
                ),
            )
        ),
    ):
        refused = LocalObservationStore(tmp_path / "refused", always(refusing))
        with pytest.raises(StoragePolicyError, match="does not permit storing"):
            refused.write(series_of(spiking()))
    assert not (tmp_path / "refused").exists()


def test_corrupt_or_unsafe_storage_is_reported_not_guessed(store: LocalObservationStore) -> None:
    store.write(series_of(spiking()))
    path = next(store._root.rglob("*.jsonl"))
    path.write_text(path.read_text() + '{"truncated": ')
    with pytest.raises(StorageCorruptionError, match="line 91"):
        store.read(GAME_ID, V0_INPUT_SIGNAL, window_of(90), as_of=RUN_TIME)

    escaping = SignalKey(source="../outside", metric="pageviews", granularity=Granularity.DAILY)
    with pytest.raises(ValueError, match="unsafe storage path"):
        store.read(GAME_ID, escaping, window_of(2), as_of=RUN_TIME)


def test_cli_can_persist_a_run_to_the_local_store(tmp_path: Path) -> None:
    args = [
        "run",
        "--replay",
        str(RECORDINGS),
        "--as-of",
        RECORDED_END.isoformat(),
        "--artifacts-dir",
        str(tmp_path / "artifacts"),
        "--store-dir",
        str(tmp_path / "store"),
    ]
    assert cli.main(args) == cli.EXIT_OK
    assert cli.main(args) == cli.EXIT_OK

    files = sorted(p.name for p in (tmp_path / "store").rglob("*.jsonl"))
    minecraft = canonical_id_for_legacy("msg_0011")
    assert len(files) == 6 and f"{minecraft}.jsonl" in files
    stored = (
        tmp_path / "store" / "wikimedia_pageviews" / "pageviews" / "daily" / f"{minecraft}.jsonl"
    )
    assert len(stored.read_text().splitlines()) == 90  # the second run added nothing
