{{ config(tags=['hourly']) }}

-- Stated a second way, without the ranking the model uses: no successful attempt in
-- a slot is earlier than the one that speaks for it.
select resolved.source, resolved.source_native_id, resolved.slot_start
from {{ ref('resolved_sample_slots') }} as resolved
inner join {{ ref('stg_sample_attempts') }} as attempts
    on resolved.source = attempts.source
    and resolved.metric = attempts.metric
    and resolved.source_native_id = attempts.source_native_id
    and resolved.granularity = attempts.granularity
    and resolved.slot_start = attempts.slot_start
where resolved.slot_start >= {{ ms_window_start(true) }}
    and attempts.slot_start >= {{ ms_window_start(true) }}
    and attempts.status = 'observed'
    and (
        attempts.observed_at < resolved.selected_observed_at
        or (
            attempts.observed_at = resolved.selected_observed_at
            and attempts.attempt_id < resolved.selected_attempt_id
        )
    )
