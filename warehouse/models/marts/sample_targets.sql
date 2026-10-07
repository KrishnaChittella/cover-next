{{ config(
    tags=['hourly'],
    unique_key=['source', 'metric', 'source_native_id', 'granularity'],
    incremental_strategy=('merge' if target.type == 'bigquery' else 'delete+insert')
) }}

-- Every source-native target that has ever been sampled, and the first and latest
-- slot it was attempted in. Small (one row per target), so it is merged whole. The
-- first slot is what lets a later run know a target was already expected before the
-- window it is recomputing.
with recent as (

    select
        source,
        metric,
        source_native_id,
        granularity,
        min(slot_start) as first_slot_start,
        max(slot_start) as last_slot_start
    from {{ ref('resolved_sample_slots') }}
    where slot_start >= {{ ms_window_start(is_incremental()) }}
    group by source, metric, source_native_id, granularity

)

{% if is_incremental() %}

select
    recent.source,
    recent.metric,
    recent.source_native_id,
    recent.granularity,
    least(recent.first_slot_start, coalesce(known.first_slot_start, recent.first_slot_start))
        as first_slot_start,
    greatest(recent.last_slot_start, coalesce(known.last_slot_start, recent.last_slot_start))
        as last_slot_start
from recent
left join {{ this }} as known
    on recent.source = known.source
    and recent.metric = known.metric
    and recent.source_native_id = known.source_native_id
    and recent.granularity = known.granularity

{% else %}

select * from recent

{% endif %}
