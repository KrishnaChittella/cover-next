{{ config(tags=['daily', 'production_data'], enabled=var('production_data_checks')) }}

-- Several checks in one statement (each BigQuery statement is billed at least 10 MiB).
-- A row is a key that appears more than once where it must not:
--   * staged period observations and collection runs: staging de-duplicates on these
--     keys, so a repeat there means that guarantee is broken;
--   * the raw ledger and the raw period rows: a batch loaded twice is harmless, but two
--     copies of one key that disagree mean the raw table cannot be trusted, and
--     de-duplicating would silently choose one.
select 'stg_period_observations' as problem, concat(
    source, '|', metric, '|', source_identifier, '|', granularity, '|',
    cast(period_start as {{ dbt.type_string() }}), '|', cast(observed_at as {{ dbt.type_string() }})
) as duplicated_key
from {{ ref('stg_period_observations') }}
where period_start >= {{ ms_window_start(true, ['periods']) }}
group by source, metric, source_identifier, granularity, period_start, observed_at
having count(*) > 1

union all

select 'stg_collection_runs', concat(run_id, '|', phase)
from {{ ref('stg_collection_runs') }}
where started_at >= {{ ms_window_start(true, ['runs']) }}
group by run_id, phase
having count(*) > 1

union all

select 'period_observations disagree', concat(
    source, '|', metric, '|', source_identifier, '|', granularity, '|',
    cast(period_start as {{ dbt.type_string() }}), '|', cast(observed_at as {{ dbt.type_string() }})
)
from {{ source('ms_raw', 'period_observations') }}
where period_start >= {{ ms_window_start(true, ['periods']) }}
group by source, metric, source_identifier, granularity, period_start, observed_at
having count(distinct coalesce(cast(value as {{ dbt.type_string() }}), 'null')) > 1

union all

select 'expected_targets disagree', concat(
    source, '|', metric, '|', granularity, '|', source_native_id, '|',
    cast(slot_start as {{ dbt.type_string() }})
)
from {{ source('ms_ops', 'expected_targets') }}
where slot_start >= {{ ms_window_start(true, ['samples']) }}
group by source, metric, granularity, source_native_id, slot_start
having count(distinct concat(coalesce(disposition, 'null'), '|', coalesce(work_order_id, 'null'))) > 1
