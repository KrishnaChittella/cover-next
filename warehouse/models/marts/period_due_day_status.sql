{{ ms_incremental('slot_start', ['source', 'metric', 'granularity', 'source_native_id', 'slot_start'], ['daily', 'periods']) }}

-- For every period the dispatcher planned (a ledger row with disposition 'planned'),
-- whether it arrived and when, judged against due_day_max_hours after the tick that
-- asked for it:
--
--   on_time  the period was first seen within the deadline
--   late     the period was first seen after the deadline: recovered, and still an SLA
--            breach. The row keeps first_observed_at, so the breach stays on record
--   missing  the deadline has passed and the period has still not been seen
--   pending  the deadline has not passed yet
--
-- Only `missing` is a failure (assert_every_due_day_arrived_in_time). A period that is
-- recovered later becomes `late` on the next build that reaches it: the warehouse job
-- rebuilds from the oldest period it loads, so a late recovery is never left out.
-- Operational, not a signal. Nothing is filled in.
with planned as (

    select source, metric, granularity, source_native_id, slot_start, due_at
    from {{ source('ms_ops', 'expected_targets') }}
    where slot_start >= {{ ms_window_start(is_incremental(), ['periods']) }}
        and mode = 'fetch_history'
        and disposition = 'planned'
        and recorded_at <= {{ ms_now() }}
    qualify row_number() over (
        partition by source, metric, granularity, source_native_id, slot_start
        order by recorded_at, loaded_at, landed_object
    ) = 1

),

arrived as (

    select
        source, metric, granularity, source_identifier, period_start,
        min(observed_at) as first_observed_at
    from {{ ref('stg_period_observations') }}
    where period_start >= {{ ms_window_start(is_incremental(), ['periods']) }}
        and observed_at <= {{ ms_now() }}
    group by source, metric, granularity, source_identifier, period_start

),

judged as (

    select
        planned.source,
        planned.metric,
        planned.granularity,
        planned.source_native_id,
        planned.slot_start,
        planned.due_at,
        {{ ms_add_hours('planned.due_at', var('due_day_max_hours')) }} as deadline_at,
        arrived.first_observed_at
    from planned
    left join arrived
        on planned.source = arrived.source
        and planned.metric = arrived.metric
        and planned.granularity = arrived.granularity
        and planned.source_native_id = arrived.source_identifier
        and planned.slot_start = arrived.period_start

)

select
    *,
    case
        when first_observed_at is not null and first_observed_at <= deadline_at then 'on_time'
        when first_observed_at is not null then 'late'
        when deadline_at < {{ ms_now() }} then 'missing'
        else 'pending'
    end as status
from judged
