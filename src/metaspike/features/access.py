"""The one place operational consumers read features from.

Some features are computed and stored but are not yet fit to act on. Under
`baseline_rules_v1` the change-point detector is uncalibrated: on noisy data it
confirms shifts that are not there. Its output is kept so it can be studied against
real history. It is SHADOW-ONLY: nothing may score, promote, notify or publish from
it.

That rule is enforced here instead of being left to memory. Every consumer that is
not purely diagnostic names itself and asks this module for a feature. A shadow-only
feature of an uncalibrated rule version is refused, and so is anything from an
internal rule version when the consumer is a public artifact.
"""

from enum import StrEnum

from metaspike.domain.baseline_rules import BASELINE_RULES_VERSION
from metaspike.domain.model import FrozenModel
from metaspike.features.engine import ChangePointView, FeatureValue
from metaspike.features.normalise import NormalisedSignal
from metaspike.features.semantics import SignalV2


class RuleStatus(StrEnum):
    UNCALIBRATED = "uncalibrated"
    CALIBRATED = "calibrated"


class RuleVisibility(StrEnum):
    INTERNAL = "internal"
    PUBLISHABLE = "publishable"


class RuleMetadata(FrozenModel):
    rule_version: str
    status: RuleStatus
    visibility: RuleVisibility
    reproducible: bool
    # Features that are recorded but may not be acted on while the rule is uncalibrated.
    shadow_only: tuple[str, ...] = ()
    note: str = ""


RULE_METADATA: dict[str, RuleMetadata] = {
    BASELINE_RULES_VERSION: RuleMetadata(
        rule_version=BASELINE_RULES_VERSION,
        status=RuleStatus.UNCALIBRATED,
        visibility=RuleVisibility.INTERNAL,
        reproducible=True,
        shadow_only=("change_point",),
        note=(
            "Internal, reproducible and uncalibrated. Its constants are never changed; a "
            "recalibration is a new rule version. The first decision on a v2 is taken no "
            "earlier than eight weeks of real history; 28 days is a sanity check only."
        ),
    )
}


class Consumer(StrEnum):
    """Who is asking. Only the last two may see shadow-only features."""

    OPPORTUNITY_SCORE = "opportunity_score"
    EVENT_HEAT = "event_heat"
    MONITORING_TIER = "monitoring_tier"
    NOTIFICATION = "notification"
    PUBLIC_ARTIFACT = "public_artifact"
    INTERNAL_DIAGNOSTIC = "internal_diagnostic"
    CALIBRATION = "calibration"


OPERATIONAL_CONSUMERS = frozenset(
    {
        Consumer.OPPORTUNITY_SCORE,
        Consumer.EVENT_HEAT,
        Consumer.MONITORING_TIER,
        Consumer.NOTIFICATION,
        Consumer.PUBLIC_ARTIFACT,
    }
)

# Every feature a normalised signal carries. Anything not listed cannot be read here.
READABLE_FEATURES = (
    "current_level",
    "bucket_level",
    "expected_level",
    "absolute_change",
    "log_lift",
    "percentage_lift",
    "robust_z",
    "trailing_percentile",
    "anomaly_strength",
    "lift_fast",
    "lift_slow",
    "velocity",
    "acceleration",
    "short_trend",
    "long_trend",
    "change_point",
)


class FeatureAccessError(Exception):
    """A consumer asked for a feature it is not allowed to use."""


class ShadowFeatureError(FeatureAccessError):
    """An operational consumer asked for a shadow-only feature."""


def rule_metadata(rule_version: str) -> RuleMetadata:
    try:
        return RULE_METADATA[rule_version]
    except KeyError:
        raise FeatureAccessError(
            f"{rule_version}: no status is recorded for this rule version, so nothing "
            "computed under it may be used"
        ) from None


def access_refusal(rule_version: str, feature: str, consumer: Consumer) -> str | None:
    """Why `consumer` may not read `feature` computed under `rule_version`, or None."""
    metadata = rule_metadata(rule_version)
    if feature not in READABLE_FEATURES:
        return f"'{feature}' is not a feature of a normalised signal"
    if consumer not in OPERATIONAL_CONSUMERS:
        return None
    if (
        consumer is Consumer.PUBLIC_ARTIFACT
        and metadata.visibility is not RuleVisibility.PUBLISHABLE
    ):
        return f"{rule_version} is {metadata.visibility.value}: nothing from it is publishable"
    if feature in metadata.shadow_only and metadata.status is not RuleStatus.CALIBRATED:
        return (
            f"'{feature}' is shadow-only under {rule_version} ({metadata.status.value}) "
            f"and may not be used for {consumer.value}"
        )
    return None


def read_feature(
    signal: NormalisedSignal | SignalV2, feature: str, consumer: Consumer
) -> FeatureValue | ChangePointView:
    """The only sanctioned way for a consumer to read one feature of a signal."""
    record = signal.signal if isinstance(signal, SignalV2) else signal
    refusal = access_refusal(record.rule_version, feature, consumer)
    if refusal is not None:
        error = ShadowFeatureError if "shadow-only" in refusal else FeatureAccessError
        raise error(refusal)
    value: FeatureValue | ChangePointView = getattr(record, feature)
    return value


def usable_features(
    signal: NormalisedSignal | SignalV2, consumer: Consumer
) -> dict[str, FeatureValue | ChangePointView]:
    """Every feature `consumer` may use. Refused features are simply absent."""
    record = signal.signal if isinstance(signal, SignalV2) else signal
    return {
        feature: getattr(record, feature)
        for feature in READABLE_FEATURES
        if access_refusal(record.rule_version, feature, consumer) is None
    }
