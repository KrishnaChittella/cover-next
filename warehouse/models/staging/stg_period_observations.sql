{{ ms_incremental(
    'period_start',
    ['source', 'metric', 'source_identifier', 'granularity', 'period_start', 'observed_at'],
    ['daily', 'periods']
) }}

-- One row per version of a period: what a source said a period's value was, and
-- when we saw it say so. A revision is a new version beside the old one. Only
-- observed periods exist here; a missing period is never a row.
select
    source,
    metric,
    source_identifier,
    canonical_game_id,
    granularity,
    period_start,
    value,
    status,
    observed_at,
    source_timestamp,
    collection_run_id,
    landed_object,
    content_sha256,
    loaded_at
from {{ source('ms_raw', 'period_observations') }}
where period_start >= {{ ms_window_start(is_incremental(), ['periods']) }}
qualify row_number() over (
    partition by source, metric, source_identifier, granularity, period_start, observed_at
    order by loaded_at, landed_object
) = 1
