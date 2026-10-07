{{ config(materialized='view', tags=['hourly']) }}

-- The recent slot timeline, gaps included, for inspection and for the parity test.
-- A VIEW: its 'not_sampled' rows are reconstructed each time it is read and are
-- never written anywhere.
{{ ms_slot_timeline(ms_window_start(true)) }}
