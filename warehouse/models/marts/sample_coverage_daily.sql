{{ ms_incremental(
    'day',
    ['source', 'metric', 'source_native_id', 'granularity', 'day'],
    ['hourly']
) }}

-- How much of each UTC day each target was actually observed. Counts only: the
-- not-sampled slots are counted here and stored nowhere.
with timeline as (

    {{ ms_slot_timeline(ms_window_start(is_incremental())) }}

)

select
    source,
    metric,
    source_native_id,
    granularity,
    {{ ms_day('slot_start') }} as day,
    count(*) as expected_slots,
    {{ ms_count_if("state = 'observed'") }} as observed_slots,
    {{ ms_count_if("state = 'unavailable'") }} as unavailable_slots,
    {{ ms_count_if("state = 'failed'") }} as failed_slots,
    {{ ms_count_if("state = 'refused'") }} as refused_slots,
    {{ ms_count_if("state = 'not_sampled'") }} as not_sampled_slots,
    sum(attempts) as attempts,
    sum(duplicate_successes) as duplicate_successes,
    sum(late_attempts) as late_attempts,
    {{ ms_count_if("state = 'observed'") }} / count(*) as observed_fraction
from timeline
group by source, metric, source_native_id, granularity, {{ ms_day('slot_start') }}
