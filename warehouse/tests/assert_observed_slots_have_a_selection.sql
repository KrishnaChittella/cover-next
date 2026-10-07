{{ config(tags=['hourly']) }}

-- A slot has a selected attempt and a value exactly when it is observed. A value of
-- zero is a value: only a successful answer that reports zero is a zero.
select *
from {{ ref('resolved_sample_slots') }}
where slot_start >= {{ ms_window_start(true) }}
    and (
        (state = 'observed') != (selected_attempt_id is not null)
        or (state = 'observed') != (value is not null)
        or attempts < 1
        or duplicate_successes < 0
    )
