-- The default Incident table (T10, #1 Decision proposal, plan section 2.2).
--
-- An Incident is a maximal chain of Log Entries that share one Pattern, where each entry follows
-- the previous one within gap_s. This table is the default: gap_s = 300 and merge_scope = all,
-- that is chains merged across every node. A tool that needs other parameters, or Incidents over
-- a filtered set, runs the same chaining at query time over that set only; an unfiltered
-- full-corpus call reads this table instead, because chaining 4.7M rows with Spread takes tens of
-- seconds (#1).
--
-- The step reads `pattern_key` and nothing else, so it needs no change when the Pattern
-- dictionary step (20_patterns.sql, T9) fills `pattern_key` from `template_id`: the next
-- `make derive` simply adds the template-keyed Incidents. Until then only BGL alert categories
-- carry a `pattern_key`, which is what the ground-truth check needs. OpenStack Request Failure
-- Incidents are a separate class and come in T27.
--
-- Two gaps are computed, both on `epoch_utc` and never on `ts`: BGL `ts` is US Pacific local time
-- with daylight saving time (-25,200 s in summer, -28,800 s in winter), so a gap taken on `ts`
-- loses an hour at the fall-back. Chaining every BGL Log Entry at 300 s gives 8,149 chains on
-- `epoch_utc` and 8,148 on `ts`; that one chain is the whole difference, and it is pinned by a
-- corpus test.
--   * the gap within one Pattern         -> the Incident id (merge_scope = all)
--   * the gap within one Pattern and one source -> the Burst id (a Burst is the same chain on one
--     source, #1), which is what the `bursts` column counts
--
-- The running sum() is cast to BIGINT, so the id does not arrive as a DECIMAL (#1).

CREATE OR REPLACE TABLE ${slm}.incident AS
WITH gapped AS (
    SELECT
        entry_id, ts, epoch_utc, pattern_key, node, nodecard, midplane, rack,
        lag(epoch_utc) OVER (PARTITION BY pattern_key ORDER BY epoch_utc, entry_id)
            AS previous_in_pattern,
        lag(epoch_utc) OVER (PARTITION BY pattern_key, node ORDER BY epoch_utc, entry_id)
            AS previous_on_source
    FROM ${slm}.bgl_entry
    WHERE pattern_key IS NOT NULL
), numbered AS (
    SELECT
        entry_id, ts, epoch_utc, pattern_key, node, nodecard, midplane, rack,
        CAST(sum(CASE WHEN previous_in_pattern IS NULL OR epoch_utc - previous_in_pattern > 300
                      THEN 1 ELSE 0 END)
             OVER (PARTITION BY pattern_key ORDER BY epoch_utc, entry_id ROWS UNBOUNDED PRECEDING)
             AS BIGINT) AS incident_id,
        CAST(sum(CASE WHEN previous_on_source IS NULL OR epoch_utc - previous_on_source > 300
                      THEN 1 ELSE 0 END)
             OVER (PARTITION BY pattern_key, node ORDER BY epoch_utc, entry_id
                   ROWS UNBOUNDED PRECEDING)
             AS BIGINT) AS burst_id
    FROM gapped
-- Spread, per Incident: how far it reaches. A node name that carries no topology (NULL,
-- UNKNOWN_LOCATION, '-') leaves nodecard, midplane and rack NULL, and count(DISTINCT ...) skips
-- those, so such an Incident reports 0 midplanes.
), spread AS (
    SELECT
        pattern_key, incident_id,
        min(ts) AS t0, max(ts) AS t1, min(epoch_utc) AS t0_utc, max(epoch_utc) AS t1_utc,
        count(*) AS entries,
        count(DISTINCT node) AS nodes,
        count(DISTINCT nodecard) AS nodecards,
        count(DISTINCT midplane) AS midplanes,
        count(DISTINCT rack) AS racks
    FROM numbered
    GROUP BY pattern_key, incident_id
), per_source AS (
    SELECT pattern_key, incident_id, node,
           count(*) AS source_entries,
           count(DISTINCT burst_id) AS source_bursts
    FROM numbered
    GROUP BY pattern_key, incident_id, node
), bursts AS (
    SELECT pattern_key, incident_id, CAST(sum(source_bursts) AS BIGINT) AS bursts
    FROM per_source
    GROUP BY pattern_key, incident_id
-- The busiest source of an Incident. Ties are broken by node name, so a rebuild of the table
-- gives the same top_node (the derive step must be idempotent).
), top_source AS (
    SELECT pattern_key, incident_id, node, source_entries
    FROM (
        SELECT pattern_key, incident_id, node, source_entries,
               row_number() OVER (PARTITION BY pattern_key, incident_id
                                  ORDER BY source_entries DESC, node) AS rank_in_incident
        FROM per_source
    ) ranked
    WHERE rank_in_incident = 1
)
-- Shape, per Incident: the label that separates "one node is shouting" from "the machine is in
-- trouble". The thresholds are 100 Log Entries for a storm and half of BlueGene/L's 128 midplanes
-- (64 racks with two each) for machine-wide; both are stated in docs/results/ground-truth.md.
SELECT
    'bgl' AS dataset,
    s.incident_id,
    s.pattern_key,
    300 AS gap_s,
    'all' AS merge_scope,
    s.t0, s.t1, s.t0_utc, s.t1_utc,
    s.entries,
    b.bursts,
    s.nodes, s.nodecards, s.midplanes, s.racks,
    t.node AS top_node,
    CAST(t.source_entries AS DOUBLE) / s.entries AS top_node_share,
    CASE
        WHEN s.nodes = 1 AND s.entries >= 100 THEN 'single-node storm'
        WHEN s.nodes = 1                      THEN 'single-node isolated'
        WHEN s.midplanes >= 64                THEN 'machine-wide'
        WHEN s.midplanes > 1                  THEN 'multi-midplane'
        ELSE 'one midplane'
    END AS shape
FROM spread s
JOIN bursts b USING (pattern_key, incident_id)
JOIN top_source t USING (pattern_key, incident_id);
