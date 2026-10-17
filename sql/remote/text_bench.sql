-- text_bench (T20): about 1,000,000,000 OTel Log Entries in one remote Parquet file, queried in place.
-- Run by scripts/huge_setup.py (`make huge-setup`), never by `make derive`: it needs network access
-- from the SereneDB server, and its facts step reads the whole file once (about 75 s, 512 MiB).
--
-- The view reads exactly one file (SLM_TEXT_BENCH_URL). A list of files or a glob would make it a
-- generic view, which can be indexed but cannot return real columns ("Indexing External Data").
-- Columns are renamed to canonical names inside the view, and Timestamp is cast to TIMESTAMP; both
-- keep the view on the fast path, so an agent index over it returns real columns, and the cast keeps
-- Parquet row-group pruning on ts (timezone('UTC', ...) defeats it: a 1 s slice then scans all 1,624
-- row groups). The cast uses the session time zone, which huge_setup.py checks is UTC.
-- There is no entry_id: file_row_number in the view breaks reads through an index over it (26.10.1).
CREATE OR REPLACE VIEW ${slm}.tb_entry AS
SELECT
    CAST("Timestamp" AS TIMESTAMP) AS ts,
    "ServiceName"                  AS component,
    "SeverityText"                 AS level,
    "Body"                         AS message,
    "TraceId"                      AS trace_id,
    "SpanId"                       AS span_id
FROM read_parquet(${url});

-- The facts the tools validate filters and resolve relative times with (corpus/facts.py), in the
-- shape of its per-call statement: (kind, value, n, first, last). The time range and the row count
-- come from the Parquet footer statistics, so they cost no scan; the Components and levels need one
-- pass over the two narrow columns, done once here instead of on every call.
CREATE OR REPLACE TEMP TABLE tb_groups AS
SELECT component, level, count(*) AS n
FROM ${slm}.tb_entry
GROUP BY component, level;

CREATE OR REPLACE TABLE ${slm}.tb_facts AS
SELECT 'range' AS kind, CAST(NULL AS VARCHAR) AS value, CAST(sum(row_group_num_rows) AS BIGINT) AS n,
       CAST(min(CAST(stats_min_value AS TIMESTAMPTZ)) AS TIMESTAMP) AS first,
       CAST(max(CAST(stats_max_value AS TIMESTAMPTZ)) AS TIMESTAMP) AS last
FROM parquet_metadata(${url})
WHERE lower(path_in_schema) = 'timestamp'
UNION ALL
SELECT 'component', component, CAST(sum(n) AS BIGINT), CAST(NULL AS TIMESTAMP), CAST(NULL AS TIMESTAMP)
FROM temp.main.tb_groups WHERE component IS NOT NULL GROUP BY component
UNION ALL
SELECT 'level', level, CAST(sum(n) AS BIGINT), CAST(NULL AS TIMESTAMP), CAST(NULL AS TIMESTAMP)
FROM temp.main.tb_groups WHERE level IS NOT NULL GROUP BY level;

DROP TABLE IF EXISTS temp.main.tb_groups;
