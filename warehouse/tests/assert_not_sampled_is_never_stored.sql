{{ config(tags=['hourly']) }}

-- 'not_sampled' is reconstructed on read. It must not appear in any stored table.
select 'stg_sample_attempts' as found_in
from {{ ref('stg_sample_attempts') }}
where slot_start >= {{ ms_window_start(true) }} and status = 'not_sampled'
union all
select 'resolved_sample_slots'
from {{ ref('resolved_sample_slots') }}
where slot_start >= {{ ms_window_start(true) }} and state = 'not_sampled'
