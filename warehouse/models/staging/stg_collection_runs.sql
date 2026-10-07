{{ ms_incremental('started_at', ['run_id', 'phase'], ['hourly']) }}

-- One row per run and phase (a run records its start and its end separately).
select
    run_id,
    job,
    source,
    phase,
    started_at,
    finished_at,
    outcome,
    requests_sent,
    halted_by,
    policy_version,
    image_digest,
    landed_object,
    content_sha256,
    loaded_at
from {{ source('ms_ops', 'collection_runs') }}
where started_at >= {{ ms_window_start(is_incremental(), ['runs']) }}
qualify row_number() over (
    partition by run_id, phase
    order by loaded_at, landed_object
) = 1
