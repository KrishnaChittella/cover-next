"""What a workload would cost a month. Arithmetic over stated inputs; nothing is fetched.

Two kinds of number are kept apart. MEASURED values come from the supervised runs of
Part C1 (one Wikimedia collection, one warehouse run, eight smoke executions).
ASSUMED values are marked as such and are replaced by measurement in C2-E. A
projection is only as good as its assumed values, and says which they are.

Free allowances belong to the billing account, so the figure that matters for a
decision is the one without them; both are reported.
"""

from collections.abc import Mapping

from pydantic import Field

from metaspike.domain.model import FrozenModel

# The cost policy. Above the review line a configuration is not activation-ready.
PREFERRED_USD = 10.0
SCRUTINY_USD = 15.0
REVIEW_USD = 20.0
DAYS = 30


class Prices(FrozenModel):
    """List prices read on 2026-10-05 for us-central1. Checked again in C2-E."""

    vcpu_second: float = 0.000018
    gib_second: float = 0.000002
    free_vcpu_seconds: float = 240_000
    free_gib_seconds: float = 450_000
    query_per_tib: float = 6.25
    free_query_tib: float = 1.0
    storage_gb_month: float = 0.02
    free_storage_gb: float = 15.0  # 5 GB Cloud Storage and 10 GB BigQuery
    registry_gb_month: float = 0.10
    free_registry_gb: float = 0.5


class Assumptions(FrozenModel):
    # ASSUMED: a job is billed at least one minute. Documented by Google; the
    # billable-time metric from C1 neither confirmed nor refuted it.
    minimum_billed_seconds: float = 60.0
    # MEASURED in S3: 62.6 s of work, about 60 s on the billable-time metric.
    warehouse_seconds: float = 65.0
    # MEASURED in S3: 14 requests in 1.3 s. ASSUMED far slower here, to be safe.
    seconds_per_request: float = 0.5
    collector_overhead_seconds: float = 5.0
    dispatcher_memory_gib: float = 0.5
    collector_memory_gib: float = 0.5
    warehouse_memory_gib: float = 1.0
    # MEASURED in S3: 41 billed statements, 136.3 MB, almost all the 10 MB minimum.
    daily_build_billed_mb: float = 136.3
    # ASSUMED: the period selection runs about nine statements, the sample selection
    # about thirty. Neither has run against BigQuery on its own.
    period_build_billed_mb: float = 90.0
    hourly_build_billed_mb: float = 110.0
    # MEASURED in S3: 528,605 landed bytes and 467,370 table bytes for 1,260 rows.
    landed_bytes_per_period_row: float = 420.0
    table_bytes_per_period_row: float = 371.0
    # ASSUMED sizes for rows not yet produced in volume.
    bytes_per_ledger_row: float = 450.0
    bytes_per_sample_row: float = 500.0
    registry_gb: float = 1.4


class Workload(FrozenModel):
    """One day of a configuration, as counted by the simulation."""

    label: str
    dispatcher_executions: int = Field(ge=0)
    # Per collector job: executions and the requests they made, per day.
    collector_executions: Mapping[str, int] = Field(default_factory=dict)
    collector_requests: Mapping[str, int] = Field(default_factory=dict)
    warehouse_daily_builds: int = Field(default=1, ge=0)
    warehouse_period_builds: int = Field(default=0, ge=0)
    warehouse_hourly_builds: int = Field(default=0, ge=0)
    period_rows: int = Field(default=0, ge=0)
    sample_rows: int = Field(default=0, ge=0)
    ledger_rows: int = Field(default=0, ge=0)
    retained_months: int = Field(default=12, ge=1)


class Projection(FrozenModel):
    label: str
    vcpu_seconds: float
    gib_seconds: float
    query_gb: float
    stored_gb_after_retention: float
    usd_without_allowances: float
    usd_with_allowances: float
    verdict: str
    breakdown: Mapping[str, float]


def project(
    workload: Workload, prices: Prices | None = None, assume: Assumptions | None = None
) -> Projection:
    prices = prices or Prices()
    assume = assume or Assumptions()
    floor = assume.minimum_billed_seconds
    dispatcher = workload.dispatcher_executions * floor * DAYS
    collectors = 0.0
    for job, runs in workload.collector_executions.items():
        if not runs:
            continue
        requests = workload.collector_requests.get(job, 0)
        work = requests * assume.seconds_per_request + runs * assume.collector_overhead_seconds
        collectors += max(runs * floor, work) * DAYS
    builds = (
        workload.warehouse_daily_builds
        + workload.warehouse_period_builds
        + workload.warehouse_hourly_builds
    )
    warehouse = builds * max(floor, assume.warehouse_seconds) * DAYS
    vcpu = dispatcher + collectors + warehouse
    gib = (
        dispatcher * assume.dispatcher_memory_gib
        + collectors * assume.collector_memory_gib
        + warehouse * assume.warehouse_memory_gib
    )
    query_mb = DAYS * (
        workload.warehouse_daily_builds * assume.daily_build_billed_mb
        + workload.warehouse_period_builds * assume.period_build_billed_mb
        + workload.warehouse_hourly_builds * assume.hourly_build_billed_mb
    )
    daily_bytes = (
        workload.period_rows
        * (assume.landed_bytes_per_period_row + assume.table_bytes_per_period_row)
        + workload.sample_rows * assume.bytes_per_sample_row * 2
        + workload.ledger_rows * assume.bytes_per_ledger_row * 2
    )
    stored_gb = daily_bytes * DAYS * workload.retained_months / 1e9
    compute = vcpu * prices.vcpu_second + gib * prices.gib_second
    free_compute = (
        max(0.0, vcpu - prices.free_vcpu_seconds) * prices.vcpu_second
        + max(0.0, gib - prices.free_gib_seconds) * prices.gib_second
    )
    query_tib = query_mb / 1024 / 1024
    query = query_tib * prices.query_per_tib
    free_query = max(0.0, query_tib - prices.free_query_tib) * prices.query_per_tib
    storage = stored_gb * prices.storage_gb_month
    free_storage = max(0.0, stored_gb - prices.free_storage_gb) * prices.storage_gb_month
    registry = assume.registry_gb * prices.registry_gb_month
    free_registry = max(0.0, assume.registry_gb - prices.free_registry_gb) * (
        prices.registry_gb_month
    )
    without = compute + query + storage + registry
    with_free = free_compute + free_query + free_storage + free_registry
    if without > REVIEW_USD:
        verdict = "NOT activation-ready: above the $20 review line"
    elif without > SCRUTINY_USD:
        verdict = "needs explicit approval"
    elif without > PREFERRED_USD:
        verdict = "acceptable with scrutiny"
    else:
        verdict = "within the preferred range"
    return Projection(
        label=workload.label,
        vcpu_seconds=round(vcpu),
        gib_seconds=round(gib),
        query_gb=round(query_mb / 1024, 2),
        stored_gb_after_retention=round(stored_gb, 3),
        usd_without_allowances=round(without, 2),
        usd_with_allowances=round(with_free, 2),
        verdict=verdict,
        breakdown={
            "compute": round(compute, 2),
            "queries": round(query, 2),
            "storage": round(storage, 2),
            "registry": round(registry, 2),
        },
    )
