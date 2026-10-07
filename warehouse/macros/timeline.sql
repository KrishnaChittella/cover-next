{#- Expected-slot reconstruction: one row per slot a target was EXPECTED to be sampled
    in, whether or not anything was attempted, plus any slot that was attempted
    without having been expected.

    Expectation comes from the dispatcher's ledger of intent, never from the attempts
    themselves: a target that was scheduled and never attempted is expected, and its
    slot is 'not_sampled'. A target that was not scheduled has no expected slot. That
    state is given HERE, on read, and nowhere else: this macro is only ever used in a
    view or inside an aggregate, so a not-sampled slot is never a stored row.

    Only sampled sources are considered. A source that is fetched for a period is not
    a sequence of sampling slots.

    `start` is the first moment to reconstruct from. -#}
{% macro ms_slot_timeline(start) %}
with expected as (

    select source, metric, source_native_id, granularity, slot_start, schedule_version
    from {{ ref('stg_expected_targets') }}
    where slot_start >= {{ start }}
        and mode = 'sample_now'

),

resolved as (

    select *
    from {{ ref('resolved_sample_slots') }}
    where slot_start >= {{ start }}

)

select
    coalesce(expected.source, resolved.source) as source,
    coalesce(expected.metric, resolved.metric) as metric,
    coalesce(expected.source_native_id, resolved.source_native_id) as source_native_id,
    coalesce(expected.granularity, resolved.granularity) as granularity,
    coalesce(expected.slot_start, resolved.slot_start) as slot_start,
    expected.slot_start is not null as was_expected,
    expected.schedule_version,
    coalesce(resolved.state, 'not_sampled') as state,
    resolved.value,
    resolved.selected_attempt_id,
    coalesce(resolved.attempts, 0) as attempts,
    coalesce(resolved.duplicate_successes, 0) as duplicate_successes,
    coalesce(resolved.late_attempts, 0) as late_attempts
from expected
full outer join resolved
    on expected.source = resolved.source
    and expected.metric = resolved.metric
    and expected.source_native_id = resolved.source_native_id
    and expected.granularity = resolved.granularity
    and expected.slot_start = resolved.slot_start
{% endmacro %}
