-- otel_demo (T21): one OpenTelemetry demo run of 46 hours (2025-05-24 20:30 to 2025-05-26 18:30 UTC)
-- as remote Parquet files that share TraceId: logs (91,559,064 rows, 4.3 GB), traces (216,701,061
-- spans, 12.9 GB) and metrics (gauge, sum and histogram files, 0.9 GB). Run by scripts/otel_setup.py
-- (`make otel-setup`), never by `make derive`: the SereneDB server needs outbound HTTPS.
--
-- Each view reads exactly one file, so it stays on the fast path, and renames the OTel columns to
-- canonical names. Timestamps are cast to TIMESTAMP (microseconds): the files store nanoseconds,
-- which psycopg cannot parse, and the cast uses the session time zone, which otel_setup.py checks is
-- UTC. None of these files is sorted by time, so a time filter prunes no row group. What makes reads
-- cheap instead:
--   * otel_log.ts is TimestampTime (whole seconds, 13 MB for the whole file) rather than Timestamp
--     (305 MB); filters and buckets on ts read only that column;
--   * TraceId carries Parquet bloom filters in the log and trace files, so `trace_id = <id>` reads
--     only the row groups that hold the trace (2 to 25 of 354 for the traces we checked).
--
-- The views are dropped and created again rather than replaced, because CREATE OR REPLACE VIEW
-- cannot change a column's type. Nothing here is indexed. The tables below are small and local: the facts of the Log Entries, and
-- the per-minute metric roll-up with its baselines, so get_trace never scans a metric file.

DROP VIEW IF EXISTS ${slm}.otel_log;
CREATE VIEW ${slm}.otel_log AS
SELECT
    CAST("TimestampTime" AS TIMESTAMP) AS ts,
    CAST("Timestamp" AS TIMESTAMP)     AS ts_exact,
    "ServiceName"                      AS component,
    "SeverityText"                     AS level,
    "Body"                             AS message,
    "TraceId"                          AS trace_id,
    "SpanId"                           AS span_id
FROM read_parquet(${logs});

DROP VIEW IF EXISTS ${slm}.otel_span;
CREATE VIEW ${slm}.otel_span AS
SELECT
    CAST("Timestamp" AS TIMESTAMP) AS ts,
    "TraceId"                      AS trace_id,
    "SpanId"                       AS span_id,
    "ParentSpanId"                 AS parent_span_id,
    "ServiceName"                  AS service,
    "SpanName"                     AS operation,
    "SpanKind"                     AS kind,
    CAST("Duration" AS BIGINT)     AS duration_ns,
    "StatusCode"                   AS status,
    "StatusMessage"                AS status_message
FROM read_parquet(${traces});

-- Metric data points. A series is one metric of one service with one set of attributes and one
-- start time: sums and histograms are cumulative (AggregationTemporality 2 in every row), so their
-- per-minute increase is the difference between consecutive values of the same series.
DROP VIEW IF EXISTS ${slm}.otel_metric_gauge;
CREATE VIEW ${slm}.otel_metric_gauge AS
SELECT
    CAST("TimeUnix" AS TIMESTAMP) AS ts,
    "ServiceName"                 AS service,
    "MetricName"                  AS metric,
    "MetricUnit"                  AS unit,
    "Value"                       AS value
FROM read_parquet(${metrics_gauge});

DROP VIEW IF EXISTS ${slm}.otel_metric_sum;
CREATE VIEW ${slm}.otel_metric_sum AS
SELECT
    CAST("TimeUnix" AS TIMESTAMP) AS ts,
    "ServiceName"                 AS service,
    "MetricName"                  AS metric,
    "MetricUnit"                  AS unit,
    "Value"                       AS value,
    "IsMonotonic"                 AS monotonic,
    hash("ServiceName", "MetricName", "StartTimeUnix", "Attributes", "ResourceAttributes") AS series
FROM read_parquet(${metrics_sum});

DROP VIEW IF EXISTS ${slm}.otel_metric_histogram;
CREATE VIEW ${slm}.otel_metric_histogram AS
SELECT
    CAST("TimeUnix" AS TIMESTAMP) AS ts,
    "ServiceName"                 AS service,
    "MetricName"                  AS metric,
    "MetricUnit"                  AS unit,
    CAST("Count" AS BIGINT)       AS observations,
    "Sum"                         AS observed_sum,
    hash("ServiceName", "MetricName", "StartTimeUnix", "Attributes", "ResourceAttributes") AS series
FROM read_parquet(${metrics_histogram});

-- The facts the tools validate filters and resolve relative times with (corpus/facts.py), in the
-- shape of tb_facts: (kind, value, n, first, last). The time range and the row count come from the
-- Parquet footer statistics of TimestampTime; the Components and levels from one pass over two
-- narrow columns (about 5 MB).
CREATE OR REPLACE TEMP TABLE otel_groups AS
SELECT component, level, count(*) AS n
FROM ${slm}.otel_log
GROUP BY component, level;

CREATE OR REPLACE TABLE ${slm}.otel_facts AS
SELECT 'range' AS kind, CAST(NULL AS VARCHAR) AS value, CAST(sum(row_group_num_rows) AS BIGINT) AS n,
       CAST(min(CAST(stats_min_value AS TIMESTAMPTZ)) AS TIMESTAMP) AS first,
       CAST(max(CAST(stats_max_value AS TIMESTAMPTZ)) AS TIMESTAMP) AS last
FROM parquet_metadata(${logs})
WHERE path_in_schema = 'TimestampTime'
UNION ALL
SELECT 'component', component, CAST(sum(n) AS BIGINT), CAST(NULL AS TIMESTAMP), CAST(NULL AS TIMESTAMP)
FROM temp.main.otel_groups WHERE component IS NOT NULL GROUP BY component
UNION ALL
SELECT 'level', level, CAST(sum(n) AS BIGINT), CAST(NULL AS TIMESTAMP), CAST(NULL AS TIMESTAMP)
FROM temp.main.otel_groups WHERE level IS NOT NULL GROUP BY level;

DROP TABLE IF EXISTS temp.main.otel_groups;

-- One row per minute, service, metric and kind, for the metrics a service reports itself (cluster
-- metrics without a ServiceName are left out: no span names their node). kind and value:
--   gauge      gauges and non-monotonic sums (current levels): n points, value = their mean,
--              low and high = the smallest and largest point;
--   counter    monotonic sums: n series increases, value = the summed increase over the minute;
--   histogram  value = mean observation (sum of observed values / observations), n = observations.
-- An increase across a gap in a series counts in the minute after the gap; a negative difference
-- (a reset) is dropped.
CREATE OR REPLACE TABLE ${slm}.otel_metric_minute AS
WITH counter_last AS (
    SELECT series, service, metric, unit, date_trunc('minute', ts) AS minute, max(value) AS last_value
    FROM ${slm}.otel_metric_sum
    WHERE service <> '' AND monotonic
    GROUP BY series, service, metric, unit, date_trunc('minute', ts)
), counter_increase AS (
    SELECT service, metric, unit, minute,
           last_value - lag(last_value) OVER (PARTITION BY series ORDER BY minute) AS increase
    FROM counter_last
), histogram_last AS (
    SELECT series, service, metric, unit, date_trunc('minute', ts) AS minute,
           max(observations) AS observations, max(observed_sum) AS observed_sum
    FROM ${slm}.otel_metric_histogram
    WHERE service <> ''
    GROUP BY series, service, metric, unit, date_trunc('minute', ts)
), histogram_increase AS (
    SELECT service, metric, unit, minute,
           observations - lag(observations) OVER (PARTITION BY series ORDER BY minute) AS observations,
           observed_sum - lag(observed_sum) OVER (PARTITION BY series ORDER BY minute) AS observed_sum
    FROM histogram_last
), minutes AS (
    SELECT date_trunc('minute', ts) AS minute, service, metric, unit, 'gauge' AS kind,
           count(*) AS n, sum(value) AS total, min(value) AS low, max(value) AS high
    FROM ${slm}.otel_metric_gauge
    WHERE service <> ''
    GROUP BY date_trunc('minute', ts), service, metric, unit
    UNION ALL
    SELECT date_trunc('minute', ts), service, metric, unit, 'gauge', count(*), sum(value), min(value), max(value)
    FROM ${slm}.otel_metric_sum
    WHERE service <> '' AND NOT monotonic
    GROUP BY date_trunc('minute', ts), service, metric, unit
    UNION ALL
    SELECT minute, service, metric, unit, 'counter', count(*), sum(increase), NULL, NULL
    FROM counter_increase
    WHERE increase >= 0
    GROUP BY minute, service, metric, unit
    UNION ALL
    SELECT minute, service, metric, unit, 'histogram', sum(observations), sum(observed_sum), NULL, NULL
    FROM histogram_increase
    WHERE observations >= 0 AND observed_sum >= 0
    GROUP BY minute, service, metric, unit
)
SELECT minute, service, metric, unit, kind, CAST(n AS BIGINT) AS n, total, low, high,
       CASE WHEN kind = 'counter' THEN total ELSE total / nullif(n, 0) END AS value
FROM minutes;

-- The baseline of every series: the distribution of its per-minute values over the whole run.
CREATE OR REPLACE TABLE ${slm}.otel_metric_baseline AS
SELECT service, metric, unit, kind, count(*) AS minutes,
       quantile_cont(value, 0.05) AS p05, median(value) AS median, quantile_cont(value, 0.95) AS p95
FROM ${slm}.otel_metric_minute
WHERE value IS NOT NULL
GROUP BY service, metric, unit, kind;
