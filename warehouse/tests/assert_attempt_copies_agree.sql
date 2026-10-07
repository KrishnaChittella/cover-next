{{ config(tags=['hourly']) }}

-- A batch may be loaded twice; two copies of an attempt must then be the same
-- attempt. Two rows with one attempt id and different contents mean the raw table
-- can no longer be trusted, and de-duplicating would silently pick one.
select attempt_id
from {{ source('ms_raw', 'sample_attempts') }}
where slot_start >= {{ ms_window_start(true) }}
group by attempt_id
having count(distinct concat(
    source, '|', metric, '|', source_native_id, '|', status, '|',
    coalesce(cast(value as {{ dbt.type_string() }}), 'null'), '|',
    cast(observed_at as {{ dbt.type_string() }}), '|',
    cast(received_at as {{ dbt.type_string() }})
)) > 1
