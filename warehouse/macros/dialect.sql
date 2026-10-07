{#- The few places BigQuery and DuckDB spell the same thing differently. -#}

{% macro ms_day(ts) -%} {{ adapter.dispatch('ms_day')(ts) }} {%- endmacro %}
{% macro bigquery__ms_day(ts) -%} timestamp_trunc({{ ts }}, day) {%- endmacro %}
{% macro duckdb__ms_day(ts) -%} date_trunc('day', {{ ts }}) {%- endmacro %}

{% macro ms_sub_days(ts, days) -%} {{ adapter.dispatch('ms_sub_days')(ts, days) }} {%- endmacro %}
{% macro bigquery__ms_sub_days(ts, days) -%}
  timestamp_sub({{ ts }}, interval {{ days }} day)
{%- endmacro %}
{% macro duckdb__ms_sub_days(ts, days) -%}
  (cast({{ ts }} as timestamp) - interval {{ days }} day)
{%- endmacro %}

{% macro ms_has_flag(flags, flag) -%} {{ adapter.dispatch('ms_has_flag')(flags, flag) }} {%- endmacro %}
{% macro bigquery__ms_has_flag(flags, flag) -%} ('{{ flag }}' in unnest({{ flags }})) {%- endmacro %}
{% macro duckdb__ms_has_flag(flags, flag) -%} list_contains({{ flags }}, '{{ flag }}') {%- endmacro %}

{% macro ms_count_if(condition) -%} sum(case when {{ condition }} then 1 else 0 end) {%- endmacro %}

