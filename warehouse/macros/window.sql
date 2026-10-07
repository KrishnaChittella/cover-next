{#- "Now", or the fixed moment a reproducible run was given. -#}
{% macro ms_now() -%}
  {%- if var('run_as_of') -%} timestamp '{{ var("run_as_of") }}'
  {%- else -%} current_timestamp
  {%- endif -%}
{%- endmacro %}

{#- Where a model starts reading. An incremental run recomputes whole UTC days, back
    `lookback_days`; a first build or a full refresh reads ALL retained history (see
    ms_history_start). Every read of a raw table goes through this, so every read is
    bounded on the partition column.

    `groups` names the kinds of data the model is built from. When the warehouse job
    has loaded rows older than the normal window for one of them, it passes
    `rebuild_from_<group>`, and the model starts from there instead, so late data is
    never left out of what it builds. -#}
{% macro ms_window_start(incremental, groups=[]) -%}
  {%- if not incremental -%}
    {{ ms_history_start(groups) }}
  {%- else -%}
    {%- set starts = [ms_day(ms_sub_days(ms_now(), var('lookback_days')))] -%}
    {%- for group in groups -%}
      {%- if var('rebuild_from_' ~ group, none) -%}
        {%- do starts.append("timestamp '" ~ var('rebuild_from_' ~ group) ~ "'") -%}
      {%- endif -%}
    {%- endfor -%}
    {%- if starts | length > 1 -%} least({{ starts | join(', ') }})
    {%- else -%} {{ starts[0] }}
    {%- endif -%}
  {%- endif -%}
{%- endmacro %}

{#- Day-partitioned tables. On BigQuery an incremental run replaces the day partitions
    it recomputed; offline, the same rows are deleted by key and inserted. -#}
{% macro ms_incremental(partition_column, unique_key, tags) -%}
  {{ config(
      tags=tags,
      unique_key=unique_key,
      incremental_strategy=('insert_overwrite' if target.type == 'bigquery' else 'delete+insert'),
      partition_by=({'field': partition_column, 'data_type': 'timestamp', 'granularity': 'day'}
                    if target.type == 'bigquery' else none)
  ) }}
{%- endmacro %}

{#- Where retained history begins, for a first build or a full refresh.

    A full refresh must rebuild everything the raw tables hold, however old: a source
    that is fetched for past periods lands rows dated long before MetaSpike existed.
    So the boundary is not a date written here. It is read from the warehouse: the
    earliest day-partition that holds a row in any raw table the model is built from.
    The read is of partition METADATA, which needs no partition filter, so the raw
    tables keep requiring one and every data read stays bounded.

    `history_start` may be supplied to state the boundary explicitly (a deliberate
    replay from a later point). It is unset by default.

    With no raw history at all the boundary is today, and the build is simply empty. -#}
{% macro ms_history_start(groups) -%}
  {%- if var('history_start', none) -%}
    timestamp '{{ var("history_start") }}'
  {%- elif not execute -%}
    timestamp '1970-01-01 00:00:00'
  {%- else -%}
    {%- set earliest = ms_earliest_partition_day(groups) -%}
    {%- if earliest -%} timestamp '{{ earliest }} 00:00:00'
    {%- else -%} {{ ms_day(ms_now()) }}
    {%- endif -%}
  {%- endif -%}
{%- endmacro %}

{#- The raw tables behind each group of models: dataset, table, partition column. -#}
{% macro ms_raw_tables(groups) -%}
  {%- set by_group = {
      'samples': [['ms_raw', 'sample_attempts', 'slot_start'],
                  ['ms_ops', 'expected_targets', 'slot_start']],
      'periods': [['ms_raw', 'period_observations', 'period_start']],
      'records': [['ms_raw', 'source_records', 'fetched_at']],
      'runs': [['ms_ops', 'collection_runs', 'started_at']],
  } -%}
  {%- set tables = [] -%}
  {%- for group in groups -%}
    {%- if group not in by_group -%}
      {{ exceptions.raise_compiler_error("unknown model group: " ~ group) }}
    {%- endif -%}
    {%- do tables.extend(by_group[group]) -%}
  {%- endfor -%}
  {{ return(tables) }}
{%- endmacro %}

{#- 'YYYY-MM-DD' of the earliest populated day-partition among the groups' raw tables,
    or none when they are all empty. -#}
{% macro ms_earliest_partition_day(groups) -%}
  {%- set tables = ms_raw_tables(groups) -%}
  {%- if not tables -%} {{ return(none) }} {%- endif -%}
  {%- set result = run_query(adapter.dispatch('ms_earliest_partition_sql')(tables)) -%}
  {%- set value = result.columns[0].values()[0] if result and result.rows | length > 0 else none -%}
  {{ return(value if value else none) }}
{%- endmacro %}

{#- BigQuery: partition metadata. No table data is read and no partition filter applies. -#}
{% macro bigquery__ms_earliest_partition_sql(tables) -%}
  select
      concat(substr(min(partition_id), 1, 4), '-', substr(min(partition_id), 5, 2), '-',
             substr(min(partition_id), 7, 2)) as earliest_day
  from (
    {%- for dataset, table, column in tables %}
    select partition_id
    from `{{ target.project }}`.`{{ dataset }}`.INFORMATION_SCHEMA.PARTITIONS
    where table_name = '{{ table }}'
        and total_rows > 0
        and regexp_contains(partition_id, r'^[0-9]{8}$')
    {%- if not loop.last %} union all {% endif -%}
    {%- endfor %}
  )
{%- endmacro %}

{#- Offline: the stand-in tables have no partition metadata, so the column is read. -#}
{% macro duckdb__ms_earliest_partition_sql(tables) -%}
  select strftime(min(earliest), '%Y-%m-%d') as earliest_day
  from (
    {%- for dataset, table, column in tables %}
    select min({{ column }}) as earliest from {{ dataset }}.{{ table }}
    {%- if not loop.last %} union all {% endif -%}
    {%- endfor %}
  )
{%- endmacro %}
