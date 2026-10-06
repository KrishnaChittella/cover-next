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
