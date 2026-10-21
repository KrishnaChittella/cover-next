{{ config(tags=['daily', 'production_data'], enabled=var('production_data_checks')) }}

-- A planned period whose deadline (due_day_max_hours after the tick that asked for it)
-- has passed and that has still not arrived. A row returned here is unresolved missing
-- data, and it fails every daily run until the period arrives or leaves the window.
--
-- A period that arrived after its deadline is `late` in period_due_day_status: the
-- breach stays on record there, and it is not failed again here once it is recovered.
select source, metric, source_native_id, slot_start, due_at, deadline_at
from {{ ref('period_due_day_status') }}
where slot_start >= {{ ms_check_window_start(14) }}
    and status = 'missing'
