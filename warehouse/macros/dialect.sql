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

{% macro ms_add_minutes(ts, minutes) -%}
  {{ adapter.dispatch('ms_add_minutes')(ts, minutes) }}
{%- endmacro %}
{% macro bigquery__ms_add_minutes(ts, minutes) -%}
  timestamp_add({{ ts }}, interval {{ minutes }} minute)
{%- endmacro %}
{% macro duckdb__ms_add_minutes(ts, minutes) -%}
  ({{ ts }} + ({{ minutes }}) * interval 1 minute)
{%- endmacro %}

{% macro ms_minutes_between(earlier, later) -%}
  {{ adapter.dispatch('ms_minutes_between')(earlier, later) }}
{%- endmacro %}
{% macro bigquery__ms_minutes_between(earlier, later) -%}
  timestamp_diff({{ later }}, {{ earlier }}, minute)
{%- endmacro %}
{% macro duckdb__ms_minutes_between(earlier, later) -%}
  date_diff('minute', {{ earlier }}, {{ later }})
{%- endmacro %}

{% macro ms_has_flag(flags, flag) -%} {{ adapter.dispatch('ms_has_flag')(flags, flag) }} {%- endmacro %}
{% macro bigquery__ms_has_flag(flags, flag) -%} ('{{ flag }}' in unnest({{ flags }})) {%- endmacro %}
{% macro duckdb__ms_has_flag(flags, flag) -%} list_contains({{ flags }}, '{{ flag }}') {%- endmacro %}

{#- One row per integer 0..count, named `step`, for each row of the table it follows. -#}
{% macro ms_steps(count) -%} {{ adapter.dispatch('ms_steps')(count) }} {%- endmacro %}
{% macro bigquery__ms_steps(count) -%}
  cross join unnest(generate_array(0, {{ count }})) as step
{%- endmacro %}
{% macro duckdb__ms_steps(count) -%}
  cross join unnest(generate_series(0, {{ count }})) as steps(step)
{%- endmacro %}

{% macro ms_count_if(condition) -%} sum(case when {{ condition }} then 1 else 0 end) {%- endmacro %}

{#- Whole-number division. `/` is floating-point in both engines. -#}
{% macro ms_int_div(numerator, denominator) -%}
  {{ adapter.dispatch('ms_int_div')(numerator, denominator) }}
{%- endmacro %}
{% macro bigquery__ms_int_div(numerator, denominator) -%}
  div({{ numerator }}, {{ denominator }})
{%- endmacro %}
{% macro duckdb__ms_int_div(numerator, denominator) -%}
  ({{ numerator }} // {{ denominator }})
{%- endmacro %}
