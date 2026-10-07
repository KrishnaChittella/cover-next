{#- Expected-slot reconstruction: one row per slot a target was expected to have,
    whether or not anything was attempted in it.

    A target is expected from its first sampled slot. A source is considered to have
    been collecting up to the latest slot in which it attempted anything, so a gap in
    the middle is "not sampled" while time after collection stopped is not expected
    at all. A slot with no attempt gets state 'not_sampled' HERE, on read, and
    nowhere else: this macro is only ever used in a view or inside an aggregate, so a
    not-sampled slot is never a stored row.

    `start` is the first moment to reconstruct from. -#}
{% macro ms_slot_timeline(start) %}
with step_minutes as (

    select '15min' as granularity, 15 as minutes
    union all select 'hourly', 60
    union all select 'daily', 1440

),

collecting as (

    select source, metric, granularity, max(slot_start) as latest_slot_start
    from {{ ref('resolved_sample_slots') }}
    where slot_start >= {{ start }}
    group by source, metric, granularity

),

expected_ranges as (

    select
        targets.source,
        targets.metric,
        targets.source_native_id,
        targets.granularity,
        step_minutes.minutes,
        greatest(targets.first_slot_start, {{ start }}) as range_start,
        collecting.latest_slot_start as range_end
    from {{ ref('sample_targets') }} as targets
    inner join collecting
        on targets.source = collecting.source
        and targets.metric = collecting.metric
        and targets.granularity = collecting.granularity
    inner join step_minutes
        on targets.granularity = step_minutes.granularity
    where targets.first_slot_start <= collecting.latest_slot_start

),

expected as (

    select
        source,
        metric,
        source_native_id,
        granularity,
        {{ ms_add_minutes('range_start', 'step * minutes') }} as slot_start
    from expected_ranges
    {{ ms_steps(ms_int_div(ms_minutes_between('range_start', 'range_end'), 'minutes')) }}
    where range_start <= range_end

)

select
    expected.source,
    expected.metric,
    expected.source_native_id,
    expected.granularity,
    expected.slot_start,
    coalesce(resolved.state, 'not_sampled') as state,
    resolved.value,
    resolved.selected_attempt_id,
    coalesce(resolved.attempts, 0) as attempts,
    coalesce(resolved.duplicate_successes, 0) as duplicate_successes,
    coalesce(resolved.late_attempts, 0) as late_attempts
from expected
left join {{ ref('resolved_sample_slots') }} as resolved
    on expected.source = resolved.source
    and expected.metric = resolved.metric
    and expected.source_native_id = resolved.source_native_id
    and expected.granularity = resolved.granularity
    and expected.slot_start = resolved.slot_start
    and resolved.slot_start >= {{ start }}
{% endmacro %}
