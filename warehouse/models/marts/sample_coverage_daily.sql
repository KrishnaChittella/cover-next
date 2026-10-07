{{ ms_incremental(
    'day',
    ['source', 'metric', 'source_native_id', 'granularity', 'day'],
    ['hourly']
) }}

-- How much of each UTC day each target was actually observed, against what the
-- dispatcher intended. Counts only: the not-sampled slots are counted here and
-- stored nowhere. Slots that were attempted without being expected are counted
-- apart and never dilute or inflate the observed fraction.
with timeline as (

    {{ ms_slot_timeline(ms_window_start(is_incremental(), ['samples'])) }}

)

select
    source,
    metric,
    source_native_id,
    granularity,
    {{ ms_day('slot_start') }} as day,
    {{ ms_count_if("was_expected") }} as expected_slots,
    {{ ms_count_if("was_expected and state = 'observed'") }} as observed_slots,
    {{ ms_count_if("was_expected and state = 'unavailable'") }} as unavailable_slots,
    {{ ms_count_if("was_expected and state = 'failed'") }} as failed_slots,
    {{ ms_count_if("was_expected and state = 'refused'") }} as refused_slots,
    {{ ms_count_if("was_expected and state = 'not_sampled'") }} as not_sampled_slots,
    {{ ms_count_if("not was_expected") }} as unexpected_attempt_slots,
    sum(attempts) as attempts,
    sum(duplicate_successes) as duplicate_successes,
    sum(late_attempts) as late_attempts,
    case
        when {{ ms_count_if("was_expected") }} = 0 then null
        else {{ ms_count_if("was_expected and state = 'observed'") }}
            / {{ ms_count_if("was_expected") }}
    end as observed_fraction
from timeline
group by source, metric, source_native_id, granularity, {{ ms_day('slot_start') }}
