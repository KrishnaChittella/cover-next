{{ ms_incremental('slot_start', 'attempt_id', ['hourly']) }}

-- One row per attempt. A batch that was loaded twice contributes its rows twice to
-- the raw table; the attempt id says they are the same attempt, and the earliest
-- load is kept. Statuses are exactly what was landed: an unsampled slot has no row
-- here, because nothing was attempted.
select
    source,
    metric,
    source_native_id,
    granularity,
    slot_start,
    scheduled_for,
    observed_at,
    received_at,
    status,
    value,
    error_code,
    canonical_game_id,
    mapping_version,
    binding_ref,
    collection_run_id,
    attempt_id,
    {{ ms_has_flag('quality_flags', 'late') }} as is_late,
    {{ ms_has_flag('quality_flags', 'zero_reported') }} as is_zero_reported,
    {{ ms_has_flag('quality_flags', 'edge_cached') }} as is_edge_cached,
    {{ ms_has_flag('quality_flags', 'binding_unknown') }} as is_binding_unknown,
    cache_age_seconds,
    cache_ttl_seconds,
    landed_object,
    content_sha256,
    loaded_at
from {{ source('ms_raw', 'sample_attempts') }}
where slot_start >= {{ ms_window_start(is_incremental()) }}
qualify row_number() over (
    partition by attempt_id
    order by loaded_at, landed_object
) = 1
