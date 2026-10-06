"""baseline_rules_v1: the approved starting constants for sampled-signal features.

These are versioned internal starting values. They may be recalibrated later from
accumulated evidence under a new version; a feature always records the version it was
computed with, so recalibration never changes what a past feature meant.

Nothing here is computed yet. The constants are recorded now so that the sampling
cadence, the store and the later feature code agree on what "enough data" means.
"""

BASELINE_RULES_VERSION = "baseline_rules_v1"

# Coverage. A sampled signal is expected every 15 minutes.
EXPECTED_SAMPLES_PER_HOUR = 4
EXPECTED_SAMPLES_PER_DAY = 96
# An hour is valid with at least 3 of its 4 expected observations.
MIN_SAMPLES_PER_VALID_HOUR = 3
# A day is valid with at least 18 valid hours AND at least 72 of its 96 observations.
MIN_VALID_HOURS_PER_VALID_DAY = 18
MIN_SAMPLES_PER_VALID_DAY = 72

# Below this baseline, a percentage or ratio lift is reported as null with the reason
# "low_base". That is its ONLY effect. It never disables log lift, robust z-score,
# absolute movement, change-point detection, eligibility or opportunity consideration:
# small games stay observable.
LOW_BASE_FLOOR_PLAYERS = 50
LOW_BASE_REASON = "low_base"

# An entity with several eligible apps has no canonical combined series until an
# explicit combination rule exists. This is unresolved semantics, not missing data.
MULTI_APP_UNRESOLVED_REASON = "multi_app_combination_unresolved"

# --- Reason codes ---------------------------------------------------------------------
# Why a feature has no value. A feature is a number or a reason, never a guess.
NO_HISTORY = "no_history"
INSUFFICIENT_24H_HISTORY = "insufficient_24h_history"
INSUFFICIENT_7D_HISTORY = "insufficient_7d_history"
INSUFFICIENT_28D_HISTORY = "insufficient_28d_history"
INSUFFICIENT_SEASONALITY = "insufficient_seasonality"
INSUFFICIENT_HISTORY = "insufficient_history"
STALE = "stale"
GAP = "gap"
UNSUPPORTED_FOR_GRANULARITY = "unsupported_for_granularity"

# --- Approved values (Phase 3 design gate and Part B instructions) ----------------------
MAD_TO_SIGMA = 1.4826
SEASONAL_MIN_INSTANCES = 3  # prior valid observations of the same hour-of-week / weekday
STEAM_HOUR_OF_DAY_MIN_INSTANCES = 7
STEAM_SEASONAL_LOOKBACK_WEEKS = 6
STEAM_FAST_EWMA_HALF_LIFE_HOURS = 6
STEAM_SLOW_EWMA_HALF_LIFE_HOURS = 24
STEAM_VELOCITY_SPAN_HOURS = 3
EWMA_MIN_HALF_LIVES = 2  # valid buckets needed, in half-lives, before an EWMA is reported
CUSUM_CLIP = 4.0
CUSUM_SLACK = 0.5
CUSUM_THRESHOLD = 5.0
CUSUM_CONFIRM_OF = (3, 4)  # at least 3 of the latest 4 valid buckets on the same side
CUSUM_RESET_AFTER_HOURS = 24
SURGE_FREEZE_MAX_DAYS = 7
MIN_VALID_DAYS_7D = 5
MIN_VALID_DAYS_28D = 21
TREND_SHORT_DAYS, TREND_SHORT_MIN_POINTS = 7, 5
TREND_LONG_DAYS, TREND_LONG_MIN_POINTS = 28, 21

# --- Values introduced in Part B: internal starting points, NOT separately approved -----
# Each is needed to make an approved rule computable. All are reported for review.
SCALE_FLOOR_LOG = 0.05  # minimum robust scale, in log units (about 5%)
ANOMALY_Z_SATURATION = 7.0  # robust z at which anomaly strength reaches 1 (as in score v0)
STEAM_RESIDUAL_WINDOW_HOURS = 168  # residuals that centre and scale the robust z
STEAM_MIN_RESIDUALS = 18
STEAM_PERCENTILE_LOOKBACK_HOURS = 672
STEAM_PERCENTILE_MIN_SAMPLES = 168
STEAM_STALE_AFTER_HOURS = 2
