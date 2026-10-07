{{ ms_incremental('fetched_at', ['source', 'source_id', 'content_hash', 'fetched_at'], ['daily']) }}

-- One row per version of a source record as it was fetched. The record itself is
-- kept whole; resolving it is Python's job, never SQL's.
select
    source,
    source_id,
    content_hash,
    title,
    product_type,
    fetched_at,
    source_updated_at,
    record,
    collection_run_id,
    landed_object,
    content_sha256,
    loaded_at
from {{ source('ms_raw', 'source_records') }}
where fetched_at >= {{ ms_window_start(is_incremental()) }}
qualify row_number() over (
    partition by source, source_id, content_hash, fetched_at
    order by loaded_at, landed_object
) = 1
