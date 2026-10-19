{{ config(tags=['daily', 'production_data'], enabled=var('production_data_checks')) }}

-- Every source that must be collected has a finished run, with an outcome that means
-- it collected something, within receipt_max_age_hours. A source with no such run at
-- all is returned too: silence is the failure this exists to catch, so it is built
-- from the list of required sources, not from the runs.
with required as (

    {% for source_id in var('receipt_required_sources') %}
    select '{{ source_id }}' as source
    {% if not loop.last %}union all{% endif %}
    {% endfor %}

),

receipts as (

    select source, max(finished_at) as latest_receipt_at
    from {{ ref('stg_collection_runs') }}
    where started_at >= {{ ms_day(ms_sub_days(ms_now(), 3)) }}
        and phase = 'finished'
        and outcome in ('succeeded', 'partial')
    group by source

)

select required.source, receipts.latest_receipt_at
from required
left join receipts on required.source = receipts.source
where receipts.latest_receipt_at is null
    or receipts.latest_receipt_at < {{ ms_sub_hours(ms_now(), var('receipt_max_age_hours')) }}
