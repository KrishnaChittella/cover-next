{{ config(tags=['daily', 'production_data'], enabled=var('production_data_checks')) }}

-- The dispatcher planned a daily period for a tracked article (the due day is the
-- ledger row's slot_start). Within due_day_max_hours of the tick that asked for it,
-- that day must exist for that article. A row returned here is a planned article
-- whose due day never arrived, or arrived too late.
--
-- Read from the raw ledger because the staged copy does not carry due_at or the
-- disposition. Only 'planned' is expected: a target deferred or skipped by a cap was
-- never promised. A day that is missing stays missing; nothing is filled in.
with planned as (

    select source, metric, granularity, source_native_id, slot_start, due_at
    from {{ source('ms_ops', 'expected_targets') }}
    where slot_start >= {{ ms_check_window_start(14) }}
        and mode = 'fetch_history'
        and disposition = 'planned'
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
    where period_start >= {{ ms_check_window_start(14) }}
    group by source, metric, granularity, source_identifier, period_start

)

select
    planned.source,
    planned.source_native_id,
    planned.slot_start,
    planned.due_at,
    arrived.first_observed_at
from planned
left join arrived
    on planned.source = arrived.source
    and planned.metric = arrived.metric
    and planned.granularity = arrived.granularity
    and planned.source_native_id = arrived.source_identifier
    and planned.slot_start = arrived.period_start
where planned.due_at <= {{ ms_sub_hours(ms_now(), var('due_day_max_hours')) }}
    and (
        arrived.first_observed_at is null
        or arrived.first_observed_at > {{ ms_add_hours('planned.due_at', var('due_day_max_hours')) }}
    )
