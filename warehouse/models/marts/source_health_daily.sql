{{ ms_incremental('day', ['source', 'metric', 'day'], ['hourly']) }}

-- What happened when each source was asked, per UTC day: how many attempts, how they
-- ended, and why the unsuccessful ones failed. Operational counts, not a signal.
with attempts as (

    select *
    from {{ ref('stg_sample_attempts') }}
    where slot_start >= {{ ms_window_start(is_incremental(), ['samples', 'runs']) }}

),

runs as (

    select
        source,
        {{ ms_day('started_at') }} as day,
        count(distinct run_id) as runs,
        count(distinct case when halted_by is not null then run_id end) as halted_runs,
        sum(coalesce(requests_sent, 0)) as requests_sent
    from {{ ref('stg_collection_runs') }}
    where started_at >= {{ ms_window_start(is_incremental(), ['samples', 'runs']) }}
        and source is not null
        and phase = 'finished'
    group by source, {{ ms_day('started_at') }}

),

daily as (

    select
        source,
        metric,
        {{ ms_day('slot_start') }} as day,
        count(*) as attempts,
        count(distinct source_native_id) as targets,
        {{ ms_count_if("status = 'observed'") }} as observed_attempts,
        {{ ms_count_if("status = 'unavailable'") }} as unavailable_attempts,
        {{ ms_count_if("status = 'failed'") }} as failed_attempts,
        {{ ms_count_if("status = 'refused'") }} as refused_attempts,
        {{ ms_count_if("is_late") }} as late_attempts,
        {{ ms_count_if("is_zero_reported") }} as zero_reported_attempts,
        {{ ms_count_if("is_edge_cached") }} as edge_cached_attempts,
        {{ ms_count_if("is_binding_unknown") }} as binding_unknown_attempts,
        {{ ms_count_if("error_code = 'rate_limited'") }} as rate_limited,
        {{ ms_count_if("error_code = 'unauthorized'") }} as unauthorized,
        {{ ms_count_if("error_code = 'quota_exhausted'") }} as quota_exhausted,
        {{ ms_count_if("error_code = 'timeout'") }} as timeouts,
        {{ ms_count_if("error_code = 'network_error'") }} as network_errors,
        {{ ms_count_if("error_code = 'http_error'") }} as http_errors,
        {{ ms_count_if("error_code = 'invalid_response'") }} as invalid_responses,
        {{ ms_count_if("error_code = 'missing_credentials'") }} as missing_credentials,
        max(case when status = 'observed' then observed_at end) as last_observed_at,
        max(received_at) as last_received_at
    from attempts
    group by source, metric, {{ ms_day('slot_start') }}

)

select
    daily.*,
    coalesce(runs.runs, 0) as runs,
    coalesce(runs.halted_runs, 0) as halted_runs,
    coalesce(runs.requests_sent, 0) as requests_sent
from daily
left join runs
    on daily.source = runs.source
    and daily.day = runs.day
