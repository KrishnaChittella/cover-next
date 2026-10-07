{#- "Now", or the fixed moment a reproducible run was given. -#}
{% macro ms_now() -%}
  {%- if var('run_as_of') -%} timestamp '{{ var("run_as_of") }}'
  {%- else -%} current_timestamp
  {%- endif -%}
{%- endmacro %}

{#- Where a model starts reading. An incremental run recomputes whole UTC days, back
    `lookback_days`; a first build or a full refresh reads all of history. Every read
    of a raw table goes through this, so every read is bounded on the partition column.

    `groups` names the kinds of data the model is built from. When the warehouse job
    has loaded rows older than the normal window for one of them, it passes
    `rebuild_from_<group>`, and the model starts from there instead, so late data is
    never left out of what it builds. -#}
{% macro ms_window_start(incremental, groups=[]) -%}
  {%- if not incremental -%}
    timestamp '{{ var("history_start") }}'
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
