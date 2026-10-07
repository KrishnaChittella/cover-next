{#- Models go to the dataset they name (ms_staging, ms_marts), not to a name built
    from the connection's default dataset. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
  {{ (custom_schema_name or target.schema) | trim }}
{%- endmacro %}

{#- Lets a generic test bound the rows it reads: `where: "slot_start >= __window_start__"`. -#}
{% macro get_where_subquery(relation) -%}
  {% set where = config.get('where', '') %}
  {% if where %}
    {% set where = where | replace('__window_start__', ms_window_start(true)) %}
    {%- set filtered -%}
      (select * from {{ relation }} where {{ where }}) dbt_subquery
    {%- endset -%}
    {% do return(filtered) %}
  {% else %}
    {% do return(relation) %}
  {% endif %}
{%- endmacro %}
