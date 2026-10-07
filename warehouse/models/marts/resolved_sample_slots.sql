{{ ms_incremental(
    'slot_start',
    ['source', 'metric', 'source_native_id', 'granularity', 'slot_start'],
    ['hourly']
) }}

-- What each sampled slot means once all its attempts are considered. This is the
-- SQL statement of the rule in metaspike.domain.samples.resolve_slot, and a parity
-- test holds the two to the same answer on the same attempts:
--
--   a slot with a successful observation is OBSERVED, and the EARLIEST success
--   (by observed_at, then attempt_id) speaks for it;
--   otherwise the most informative outcome names it: unavailable, else failed,
--   else refused.
--
-- A slot with no attempt has no row. It is never stored as "not sampled", and its
-- absence is never a zero.
with attempts as (

    select *
    from {{ ref('stg_sample_attempts') }}
    where slot_start >= {{ ms_window_start(is_incremental(), ['samples']) }}

),

ranked as (

    select
        *,
        row_number() over (
            partition by source, metric, source_native_id, granularity, slot_start, status
            order by observed_at, attempt_id
        ) as rank_within_status
    from attempts

)

select
    source,
    metric,
    source_native_id,
    granularity,
    slot_start,
    case
        when {{ ms_count_if("status = 'observed'") }} > 0 then 'observed'
        when {{ ms_count_if("status = 'unavailable'") }} > 0 then 'unavailable'
        when {{ ms_count_if("status = 'failed'") }} > 0 then 'failed'
        else 'refused'
    end as state,
    max(case when status = 'observed' and rank_within_status = 1 then attempt_id end)
        as selected_attempt_id,
    max(case when status = 'observed' and rank_within_status = 1 then value end) as value,
    max(case when status = 'observed' and rank_within_status = 1 then observed_at end)
        as selected_observed_at,
    count(*) as attempts,
    {{ ms_count_if("status = 'observed' and rank_within_status > 1") }} as duplicate_successes,
    {{ ms_count_if("status = 'failed'") }} as failed_attempts,
    {{ ms_count_if("status = 'refused'") }} as refused_attempts,
    {{ ms_count_if("is_late") }} as late_attempts,
    min(observed_at) as first_attempt_at,
    max(received_at) as last_received_at
from ranked
group by source, metric, source_native_id, granularity, slot_start
