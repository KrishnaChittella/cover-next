{{ config(tags=['daily', 'production_data'], enabled=var('production_data_checks')) }}

-- Period rows landed per UTC day (by when we observed them) for the last complete day,
-- against the median of the seven days before it. A day with no rows is a day with
-- zero rows landed: that is a fact about the pipeline, not a missing value. A median of
-- zero (a pipeline that has not collected for a week, or never) cannot be fallen below,
-- and the receipt check is the one that notices silence.
--
-- A returned row is the day that fell below row_count_min_share of the median.
with days as (

    {% for n in range(1, 9) %}
    select {{ n }} as days_ago, {{ ms_day(ms_sub_days(ms_now(), n)) }} as day
    {% if not loop.last %}union all{% endif %}
    {% endfor %}

),

landed as (

    select
        {{ ms_day('observed_at') }} as day,
        count(*) as value
    from {{ ref('stg_period_observations') }}
    where period_start >= {{ ms_day(ms_sub_days(ms_now(), 30)) }}
        and observed_at >= {{ ms_day(ms_sub_days(ms_now(), 8)) }}
    group by {{ ms_day('observed_at') }}

),

counted as (

    select days.days_ago, days.day, coalesce(landed.value, 0) as value
    from days
    left join landed on days.day = landed.day

),

baseline as (

    select value from counted where days_ago > 1

)

select
    counted.day,
    counted.value as rows_landed,
    {{ ms_middle_value('baseline') }} as weekly_median
from counted
where counted.days_ago = 1
    and counted.value < {{ var('row_count_min_share') }} * {{ ms_middle_value('baseline') }}
