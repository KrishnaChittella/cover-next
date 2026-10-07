{{ config(tags=['hourly']) }}

-- Every expected slot ended in exactly one state.
select *
from {{ ref('sample_coverage_daily') }}
where day >= {{ ms_window_start(true) }}
    and (
        expected_slots != observed_slots + unavailable_slots + failed_slots
            + refused_slots + not_sampled_slots
        or expected_slots < 1
        or observed_fraction < 0
        or observed_fraction > 1
    )
