"""Shared base for domain and artifact models."""

from pydantic import BaseModel, ConfigDict


class FrozenModel(BaseModel):
    """Immutable, closed model that rejects NaN and Infinity on every float field."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
