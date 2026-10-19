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

{% macro ms_sub_hours(ts, hours) -%} {{ adapter.dispatch('ms_sub_hours')(ts, hours) }} {%- endmacro %}
{% macro bigquery__ms_sub_hours(ts, hours) -%}
  timestamp_sub({{ ts }}, interval {{ hours }} hour)
{%- endmacro %}
{% macro duckdb__ms_sub_hours(ts, hours) -%}
  (cast({{ ts }} as timestamp) - interval {{ hours }} hour)
{%- endmacro %}

{% macro ms_add_hours(ts, hours) -%} {{ adapter.dispatch('ms_add_hours')(ts, hours) }} {%- endmacro %}
{% macro bigquery__ms_add_hours(ts, hours) -%}
  timestamp_add({{ ts }}, interval {{ hours }} hour)
{%- endmacro %}
{% macro duckdb__ms_add_hours(ts, hours) -%}
  (cast({{ ts }} as timestamp) + interval {{ hours }} hour)
{%- endmacro %}

{% macro ms_has_flag(flags, flag) -%} {{ adapter.dispatch('ms_has_flag')(flags, flag) }} {%- endmacro %}
{% macro bigquery__ms_has_flag(flags, flag) -%} ('{{ flag }}' in unnest({{ flags }})) {%- endmacro %}
{% macro duckdb__ms_has_flag(flags, flag) -%} list_contains({{ flags }}, '{{ flag }}') {%- endmacro %}

{% macro ms_count_if(condition) -%} sum(case when {{ condition }} then 1 else 0 end) {%- endmacro %}


{#- The middle of a list of counts is not one function in both dialects, so a data test
    that needs it ranks the values and takes the middle one (or the mean of the two
    middle ones). `table` has a `value` column; every row of it is one value. This is for
    data-quality checks on row counts only: no feature or score is computed in SQL. -#}
{% macro ms_middle_value(table) -%}
  (
    select avg(ranked.value)
    from (
      select
          value,
          row_number() over (order by value) as position,
          count(*) over () as total
      from {{ table }}
    ) as ranked
    where ranked.position in (floor((ranked.total + 1) / 2), floor((ranked.total + 2) / 2))
  )
{%- endmacro %}
