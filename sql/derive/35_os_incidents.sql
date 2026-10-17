-- OpenStack Request Failure Incidents (T27, #1 Decision proposal, plan section 2.2).
--
-- 30_incidents.sql builds the Incident table from BGL; this step adds the OpenStack rows to it.
-- A Request Failure (Glossary) is an OpenStack request or instance lifecycle that ended in ERROR
-- or CRITICAL, an HTTP 5xx status, or did not complete. Two kinds of row come from it:
--
--   * chained Request Failures: every Log Entry at level ERROR or CRITICAL, or with an HTTP status
--     of 500 or more, chained per Pattern (pattern_key, which T9 sets to the template id) exactly
--     like the BGL Incidents: gap_s = 300, merge_scope = all, gaps on epoch_utc, the Incident id a
--     running sum() cast to BIGINT. A 503 response shares its template with the 200 responses of
--     the same endpoint (the status is masked), so only the failing responses are chained, never
--     the Pattern as a whole.
--   * lifecycle Request Failures, one row per instance whose lifecycle never completes: its build
--     started ("Attempting claim" or "Creating image") and no "Took N seconds to build instance"
--     followed, or its teardown started ("Terminating instance") and no "Deletion of ... complete"
--     followed. They carry pattern_key 'lifecycle-incomplete' (corpus.datasets.LIFECYCLE_KEY) and
--     hold every Log Entry that names the instance. They are not chained, so gap_s and
--     merge_scope record the defaults of the table only.
--
-- Spread for OpenStack (Glossary: "instances or hosts"): the source of a Log Entry is the instance
-- it names (instance_ref), or, when it names none, its host. The logs name no machine, so the host
-- is the nova service whose log file carries the line (nova-api, nova-compute, nova-scheduler).
-- `nodes` counts distinct sources and `top_node` is the busiest one; `nodecards`, `midplanes` and
-- `racks` are BGL topology and stay NULL. `shape` stays NULL too, because the Glossary's Shape
-- labels are defined on BGL topology (node, midplane, machine-wide); list_incidents shows the
-- OpenStack Spread as instances and hosts instead.
--
-- The step is idempotent on its own: it first removes the OpenStack rows, so running it twice, or
-- after 30_incidents.sql, gives the same table.

DELETE FROM ${slm}.incident WHERE dataset = 'openstack';

INSERT INTO ${slm}.incident
WITH failing AS (
    SELECT
        entry_id, ts, epoch_utc, pattern_key,
        coalesce(instance_ref, nullif(regexp_extract(log_file, '^([A-Za-z0-9_-]+)', 1), ''), log_file)
            AS source
    FROM ${slm}.os_entry
    WHERE pattern_key IS NOT NULL
      AND (level IN ('ERROR', 'CRITICAL') OR http_status >= 500)
), gapped AS (
    SELECT
        entry_id, ts, epoch_utc, pattern_key, source,
        lag(epoch_utc) OVER (PARTITION BY pattern_key ORDER BY epoch_utc, entry_id)
            AS previous_in_pattern,
        lag(epoch_utc) OVER (PARTITION BY pattern_key, source ORDER BY epoch_utc, entry_id)
            AS previous_on_source
    FROM failing
), numbered AS (
    SELECT
        entry_id, ts, epoch_utc, pattern_key, source,
        CAST(sum(CASE WHEN previous_in_pattern IS NULL OR epoch_utc - previous_in_pattern > 300
                      THEN 1 ELSE 0 END)
             OVER (PARTITION BY pattern_key ORDER BY epoch_utc, entry_id ROWS UNBOUNDED PRECEDING)
             AS BIGINT) AS incident_id,
        CAST(sum(CASE WHEN previous_on_source IS NULL OR epoch_utc - previous_on_source > 300
                      THEN 1 ELSE 0 END)
             OVER (PARTITION BY pattern_key, source ORDER BY epoch_utc, entry_id
                   ROWS UNBOUNDED PRECEDING)
             AS BIGINT) AS burst_id
    FROM gapped
), spread AS (
    SELECT
        pattern_key, incident_id,
        min(ts) AS t0, max(ts) AS t1, min(epoch_utc) AS t0_utc, max(epoch_utc) AS t1_utc,
        count(*) AS entries,
        count(DISTINCT source) AS nodes
    FROM numbered
    GROUP BY pattern_key, incident_id
), per_source AS (
    SELECT pattern_key, incident_id, source,
           count(*) AS source_entries,
           count(DISTINCT burst_id) AS source_bursts
    FROM numbered
    GROUP BY pattern_key, incident_id, source
), bursts AS (
    SELECT pattern_key, incident_id, CAST(sum(source_bursts) AS BIGINT) AS bursts
    FROM per_source
    GROUP BY pattern_key, incident_id
), top_source AS (
    SELECT pattern_key, incident_id, source, source_entries
    FROM (
        SELECT pattern_key, incident_id, source, source_entries,
               row_number() OVER (PARTITION BY pattern_key, incident_id
                                  ORDER BY source_entries DESC, source) AS rank_in_incident
        FROM per_source
    ) ranked
    WHERE rank_in_incident = 1
), lifecycle AS (
    SELECT
        instance_ref AS instance,
        count(*) AS entries,
        min(ts) AS t0, max(ts) AS t1, min(epoch_utc) AS t0_utc, max(epoch_utc) AS t1_utc,
        count(*) FILTER (WHERE message LIKE '%] Attempting claim%'
                            OR message LIKE '%] Creating image%') AS build_started,
        count(*) FILTER (WHERE message LIKE '%] Took % seconds to build instance%') AS build_done,
        count(*) FILTER (WHERE message LIKE '%] Terminating instance%') AS teardown_started,
        count(*) FILTER (WHERE message LIKE '%] Deletion of % complete%') AS teardown_done
    FROM ${slm}.os_entry
    WHERE instance_ref IS NOT NULL
    GROUP BY instance_ref
), incomplete AS (
    SELECT
        instance, entries, t0, t1, t0_utc, t1_utc,
        CAST(row_number() OVER (ORDER BY t0_utc, instance) AS BIGINT) AS incident_id
    FROM lifecycle
    WHERE (build_started > 0 AND build_done = 0)
       OR (teardown_started > 0 AND teardown_done = 0)
)
SELECT
    'openstack' AS dataset,
    s.incident_id,
    s.pattern_key,
    300 AS gap_s,
    'all' AS merge_scope,
    s.t0, s.t1, s.t0_utc, s.t1_utc,
    s.entries,
    b.bursts,
    s.nodes,
    CAST(NULL AS BIGINT) AS nodecards,
    CAST(NULL AS BIGINT) AS midplanes,
    CAST(NULL AS BIGINT) AS racks,
    t.source AS top_node,
    CAST(t.source_entries AS DOUBLE) / s.entries AS top_node_share,
    CAST(NULL AS VARCHAR) AS shape
FROM spread s
JOIN bursts b USING (pattern_key, incident_id)
JOIN top_source t USING (pattern_key, incident_id)
UNION ALL
SELECT
    'openstack',
    incident_id,
    'lifecycle-incomplete',
    300,
    'all',
    t0, t1, t0_utc, t1_utc,
    entries,
    CAST(1 AS BIGINT),
    CAST(1 AS BIGINT),
    CAST(NULL AS BIGINT),
    CAST(NULL AS BIGINT),
    CAST(NULL AS BIGINT),
    instance,
    CAST(1.0 AS DOUBLE),
    CAST(NULL AS VARCHAR)
FROM incomplete;
