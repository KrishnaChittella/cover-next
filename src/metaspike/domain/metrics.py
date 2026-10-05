"""Metric registry: the vocabulary of things a source can measure.

A metric says WHAT is measured, never how often. Granularity belongs to the series,
so the same metric can be collected intraday and compared daily.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints

from metaspike.domain.model import FrozenModel
from metaspike.domain.timeseries import Granularity

MetricId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]


class MetricRole(StrEnum):
    """What a metric may be used for.

    Only DEMAND metrics may ever feed the base opportunity score. CREATOR_SUPPLY
    metrics come from permission-gated sources and are reported separately, so the
    base score means the same thing for every game whether or not they exist.
    """

    DEMAND = "demand"
    CREATOR_SUPPLY = "creator_supply"


class Aggregation(StrEnum):
    """How finer periods combine into a coarser one."""

    SUM = "sum"  # a flow counted per period, e.g. pageviews
    MEAN = "mean"  # a level sampled per period, e.g. concurrent players
    MAX = "max"
    LAST = "last"


class MetricDefinition(FrozenModel):
    metric_id: MetricId
    display_name: str
    unit: str
    description: str
    role: MetricRole
    aggregation: Aggregation
    granularities: tuple[Granularity, ...] = Field(min_length=1)
    non_negative: bool = True

    def supports(self, granularity: Granularity) -> bool:
        return granularity in self.granularities


class UnknownMetricError(KeyError):
    """A metric was used that the registry does not define."""


class MetricRegistry:
    def __init__(self, definitions: tuple[MetricDefinition, ...] = ()) -> None:
        self._definitions: dict[str, MetricDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: MetricDefinition) -> None:
        if definition.metric_id in self._definitions:
            raise ValueError(f"metric already registered: {definition.metric_id}")
        self._definitions[definition.metric_id] = definition

    def get(self, metric_id: str) -> MetricDefinition:
        try:
            return self._definitions[metric_id]
        except KeyError:
            raise UnknownMetricError(metric_id) from None

    def __contains__(self, metric_id: object) -> bool:
        return metric_id in self._definitions

    def definitions(self) -> tuple[MetricDefinition, ...]:
        return tuple(self._definitions[metric_id] for metric_id in sorted(self._definitions))


PAGEVIEWS = MetricDefinition(
    metric_id="pageviews",
    display_name="Pageviews",
    unit="views",
    description="Human (non-bot) views of the page that represents the game.",
    role=MetricRole.DEMAND,
    aggregation=Aggregation.SUM,
    granularities=(Granularity.DAILY,),
)


def default_metrics() -> MetricRegistry:
    return MetricRegistry((PAGEVIEWS,))
