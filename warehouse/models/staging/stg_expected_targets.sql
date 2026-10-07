{{ ms_incremental(
    'slot_start',
    ['source', 'metric', 'granularity', 'source_native_id', 'slot_start'],
    ['hourly']
) }}

-- What the dispatcher intended to be collected: one row per target and slot. This is
-- the record of intent, written before anything was attempted, and it is what makes
-- a slot nobody attempted visible. If the same intent was recorded twice the first
-- record is kept; a later change of eligibility adds rows for later slots and never
-- alters these.
select
    source,
    metric,
    granularity,
    mode,
    source_native_id,
    slot_start,
    schedule_version,
    dispatch_run_id,
    recorded_at,
    landed_object,
    content_sha256,
    loaded_at
from {{ source('ms_ops', 'expected_targets') }}
where slot_start >= {{ ms_window_start(is_incremental(), ['samples']) }}
qualify row_number() over (
    partition by source, metric, granularity, source_native_id, slot_start
    order by recorded_at, loaded_at, landed_object
) = 1
