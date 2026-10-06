"""Per-signal feature configuration, by rule version.

The feature engine is one implementation. What differs between a 15-minute sampled
player count and a daily pageview count is configuration: which windows exist, which
seasonal comparison is meaningful, and which features have a defined meaning at all.

A feature with no configuration for a signal is not computed with borrowed numbers.
It is reported as null with the reason `unsupported_for_granularity`. A six-hour
half-life has no meaning on one-day buckets, so a daily signal does not get one.

Configurations are registered under a rule version and never edited once used. A new
rule version is a new entry; the old one keeps reproducing what it always produced.
"""

import hashlib
from enum import StrEnum

from pydantic import Field

from metaspike.domain import baseline_rules as rules
from metaspike.domain.families import SourceFamily, family_of
from metaspike.domain.model import FrozenModel
from metaspike.domain.timeseries import Granularity
from metaspike.features import attention


class SeasonalityBasis(StrEnum):
    HOUR_OF_WEEK = "hour_of_week"
    HOUR_OF_DAY = "hour_of_day"
    DAY_OF_WEEK = "day_of_week"
    TRAILING_LEVEL = "trailing_level"


class BaselineWindow(FrozenModel):
    """One trailing window of buckets, ending before the bucket being evaluated."""

    name: str
    buckets: int = Field(ge=1)
    # Exactly one gate applies: valid buckets inside the window, or valid whole days.
    min_valid_buckets: int | None = None
    min_valid_days: int | None = None
    days: int | None = None
    emit_mean_std: bool = False
    unavailable_reason: str


class SeasonalProfile(FrozenModel):
    basis: SeasonalityBasis
    lookback_buckets: int = Field(ge=1)
    min_instances: int = Field(ge=1)


class EwmaConfig(FrozenModel):
    fast_half_life_buckets: float = Field(gt=0)
    slow_half_life_buckets: float = Field(gt=0)
    min_half_lives: int = Field(ge=1)


class PercentileConfig(FrozenModel):
    lookback_buckets: int = Field(ge=1)
    min_samples: int = Field(ge=1)


class TrendConfig(FrozenModel):
    days: int = Field(ge=2)
    min_points: int = Field(ge=2)


class CusumConfig(FrozenModel):
    clip: float = Field(gt=0)
    slack: float = Field(ge=0)
    threshold: float = Field(gt=0)
    confirm_count: int = Field(ge=1)
    confirm_of: int = Field(ge=1)
    reset_after_gap_buckets: int = Field(ge=1)
    freeze_max_buckets: int = Field(ge=1)
    # Change points are looked for only against these expectations. Under a coarser
    # fallback a regular weekly peak would look like a shift.
    allowed_bases: tuple[SeasonalityBasis, ...]


class SignalFeatureConfig(FrozenModel):
    rule_version: str
    source: str
    metric: str
    granularity: Granularity
    source_family: SourceFamily
    # Coverage: what makes a bucket and a day count.
    expected_samples_per_bucket: int = Field(ge=1)
    min_samples_per_valid_bucket: int = Field(ge=1)
    buckets_per_day: int = Field(ge=1)
    min_valid_buckets_per_day: int = Field(ge=1)
    min_samples_per_valid_day: int = Field(ge=1)
    # The bounded history a state carries. Nothing older is ever needed.
    history_buckets: int = Field(ge=1)
    baseline_windows: tuple[BaselineWindow, ...]
    # Preferred comparison first. The trailing level is always the last resort.
    seasonality: tuple[SeasonalProfile, ...]
    maturity_short_window: str
    maturity_long_window: str
    scale_floor: float = Field(gt=0)
    residual_window_buckets: int = Field(ge=1)
    min_residuals: int = Field(ge=2)
    # A percentage lift is reported only when the expected level exceeds this.
    low_base_floor: float = Field(ge=0)
    anomaly_z_saturation: float = Field(gt=0)
    stale_after_buckets: int = Field(ge=1)
    trend_short: TrendConfig | None = None
    trend_long: TrendConfig | None = None
    percentile: PercentileConfig | None = None
    ewma: EwmaConfig | None = None
    velocity_span_buckets: int | None = Field(default=None, ge=1)
    cusum: CusumConfig | None = None

    @property
    def fingerprint(self) -> str:
        """Identifies these exact parameters. A state continues only under the same ones."""
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()[:16]

    @property
    def unsupported(self) -> tuple[str, ...]:
        """Features that have no defined meaning for this signal at this granularity."""
        missing = []
        if self.ewma is None:
            missing += ["lift_fast", "lift_slow"]
        if self.ewma is None or self.velocity_span_buckets is None:
            missing += ["velocity", "acceleration"]
        if self.cusum is None:
            missing.append("change_point")
        if self.percentile is None:
            missing.append("trailing_percentile")
        if self.trend_short is None:
            missing.append("trend_short")
        if self.trend_long is None:
            missing.append("trend_long")
        return tuple(missing)


def _steam_hourly_v1() -> SignalFeatureConfig:
    source, metric = "steam_current_players", "concurrent_players"
    return SignalFeatureConfig(
        rule_version=rules.BASELINE_RULES_VERSION,
        source=source,
        metric=metric,
        granularity=Granularity.HOURLY,
        source_family=family_of(source, metric),
        expected_samples_per_bucket=rules.EXPECTED_SAMPLES_PER_HOUR,
        min_samples_per_valid_bucket=rules.MIN_SAMPLES_PER_VALID_HOUR,
        buckets_per_day=24,
        min_valid_buckets_per_day=rules.MIN_VALID_HOURS_PER_VALID_DAY,
        min_samples_per_valid_day=rules.MIN_SAMPLES_PER_VALID_DAY,
        history_buckets=rules.STEAM_SEASONAL_LOOKBACK_WEEKS * 7 * 24,
        baseline_windows=(
            BaselineWindow(
                name="24h",
                buckets=24,
                min_valid_buckets=rules.MIN_VALID_HOURS_PER_VALID_DAY,
                unavailable_reason=rules.INSUFFICIENT_24H_HISTORY,
            ),
            BaselineWindow(
                name="7d",
                buckets=7 * 24,
                min_valid_days=rules.MIN_VALID_DAYS_7D,
                days=7,
                unavailable_reason=rules.INSUFFICIENT_7D_HISTORY,
            ),
            BaselineWindow(
                name="28d",
                buckets=28 * 24,
                min_valid_days=rules.MIN_VALID_DAYS_28D,
                days=28,
                emit_mean_std=True,
                unavailable_reason=rules.INSUFFICIENT_28D_HISTORY,
            ),
        ),
        seasonality=(
            SeasonalProfile(
                basis=SeasonalityBasis.HOUR_OF_WEEK,
                lookback_buckets=rules.STEAM_SEASONAL_LOOKBACK_WEEKS * 7 * 24,
                min_instances=rules.SEASONAL_MIN_INSTANCES,
            ),
            SeasonalProfile(
                basis=SeasonalityBasis.HOUR_OF_DAY,
                lookback_buckets=28 * 24,
                min_instances=rules.STEAM_HOUR_OF_DAY_MIN_INSTANCES,
            ),
        ),
        maturity_short_window="7d",
        maturity_long_window="28d",
        scale_floor=rules.SCALE_FLOOR_LOG,
        residual_window_buckets=rules.STEAM_RESIDUAL_WINDOW_HOURS,
        min_residuals=rules.STEAM_MIN_RESIDUALS,
        low_base_floor=rules.LOW_BASE_FLOOR_PLAYERS,
        anomaly_z_saturation=rules.ANOMALY_Z_SATURATION,
        stale_after_buckets=rules.STEAM_STALE_AFTER_HOURS,
        trend_short=TrendConfig(
            days=rules.TREND_SHORT_DAYS, min_points=rules.TREND_SHORT_MIN_POINTS
        ),
        trend_long=TrendConfig(days=rules.TREND_LONG_DAYS, min_points=rules.TREND_LONG_MIN_POINTS),
        percentile=PercentileConfig(
            lookback_buckets=rules.STEAM_PERCENTILE_LOOKBACK_HOURS,
            min_samples=rules.STEAM_PERCENTILE_MIN_SAMPLES,
        ),
        ewma=EwmaConfig(
            fast_half_life_buckets=rules.STEAM_FAST_EWMA_HALF_LIFE_HOURS,
            slow_half_life_buckets=rules.STEAM_SLOW_EWMA_HALF_LIFE_HOURS,
            min_half_lives=rules.EWMA_MIN_HALF_LIVES,
        ),
        velocity_span_buckets=rules.STEAM_VELOCITY_SPAN_HOURS,
        cusum=CusumConfig(
            clip=rules.CUSUM_CLIP,
            slack=rules.CUSUM_SLACK,
            threshold=rules.CUSUM_THRESHOLD,
            confirm_count=rules.CUSUM_CONFIRM_OF[0],
            confirm_of=rules.CUSUM_CONFIRM_OF[1],
            reset_after_gap_buckets=rules.CUSUM_RESET_AFTER_HOURS,
            freeze_max_buckets=rules.SURGE_FREEZE_MAX_DAYS * 24,
            allowed_bases=(SeasonalityBasis.HOUR_OF_WEEK,),
        ),
    )


def _wikimedia_daily_v1() -> SignalFeatureConfig:
    """Daily pageviews: only what has a daily meaning that is already established.

    Windows, minimum samples, percentile history and anomaly saturation are the values
    score v0 already uses on this series. Day-of-week seasonality uses the approved
    minimum of three prior weekdays. No daily EWMA half-life, velocity span or
    change-point parameters have been approved, so those features are unsupported
    here instead of being computed with hourly numbers.
    """
    source, metric = "wikimedia_pageviews", "pageviews"
    return SignalFeatureConfig(
        rule_version=rules.BASELINE_RULES_VERSION,
        source=source,
        metric=metric,
        granularity=Granularity.DAILY,
        source_family=family_of(source, metric),
        expected_samples_per_bucket=1,
        min_samples_per_valid_bucket=1,
        buckets_per_day=1,
        min_valid_buckets_per_day=1,
        min_samples_per_valid_day=1,
        history_buckets=attention.HISTORY_DAYS,
        baseline_windows=(
            BaselineWindow(
                name="7d",
                buckets=attention.LONG_WINDOW_DAYS,
                min_valid_buckets=attention.MIN_LONG_SAMPLES,
                unavailable_reason=rules.INSUFFICIENT_7D_HISTORY,
            ),
            BaselineWindow(
                name="28d",
                buckets=attention.BASELINE_DAYS,
                min_valid_buckets=attention.MIN_BASELINE_SAMPLES,
                emit_mean_std=True,
                unavailable_reason=rules.INSUFFICIENT_28D_HISTORY,
            ),
        ),
        seasonality=(
            SeasonalProfile(
                basis=SeasonalityBasis.DAY_OF_WEEK,
                lookback_buckets=rules.STEAM_SEASONAL_LOOKBACK_WEEKS * 7,
                min_instances=rules.SEASONAL_MIN_INSTANCES,
            ),
        ),
        maturity_short_window="7d",
        maturity_long_window="28d",
        scale_floor=rules.SCALE_FLOOR_LOG,
        residual_window_buckets=attention.BASELINE_DAYS,
        min_residuals=attention.MIN_BASELINE_SAMPLES,
        low_base_floor=0,  # as in v0: a percentage lift needs only a baseline above zero
        anomaly_z_saturation=rules.ANOMALY_Z_SATURATION,
        stale_after_buckets=1,  # as in v0: stale as soon as a due day is missing
        trend_short=TrendConfig(
            days=rules.TREND_SHORT_DAYS, min_points=rules.TREND_SHORT_MIN_POINTS
        ),
        trend_long=TrendConfig(days=rules.TREND_LONG_DAYS, min_points=rules.TREND_LONG_MIN_POINTS),
        percentile=PercentileConfig(
            lookback_buckets=attention.HISTORY_DAYS,
            min_samples=attention.MIN_PERCENTILE_SAMPLES,
        ),
    )


class UnknownFeatureConfigError(KeyError):
    """No configuration is registered for this signal under this rule version."""


_REGISTRY: dict[tuple[str, str, str, Granularity], SignalFeatureConfig] = {}


def register_config(config: SignalFeatureConfig) -> None:
    """Add a configuration. An existing rule version can never be redefined."""
    key = (config.rule_version, config.source, config.metric, config.granularity)
    existing = _REGISTRY.get(key)
    if existing is not None and existing != config:
        raise ValueError(
            f"{config.rule_version} is already defined for {config.source}/{config.metric} "
            f"at {config.granularity.value}; a change needs a new rule version"
        )
    _REGISTRY[key] = config


def feature_config(
    source: str,
    metric: str,
    granularity: Granularity,
    rule_version: str = rules.BASELINE_RULES_VERSION,
) -> SignalFeatureConfig:
    try:
        return _REGISTRY[(rule_version, source, metric, granularity)]
    except KeyError:
        raise UnknownFeatureConfigError(
            f"{rule_version}: {source}/{metric} at {granularity.value}"
        ) from None


register_config(_steam_hourly_v1())
register_config(_wikimedia_daily_v1())
