-- Canonical Log Entry tables (T4, plan section 2.2), built from the adapter's temporary views
-- (temp.main.bgl_adapted, temp.main.os_adapted) with the derived columns every tool needs, so no
-- tool runs a regex at query time (#2).
-- template_id and family_id stay NULL until the Pattern dictionary step (T9) fills them.
-- pattern_key is the key Incidents are built on (#1): the alert category for BGL alert lines,
-- NULL for non-alert lines and for OpenStack until T9 sets it to template_id.

CREATE OR REPLACE TABLE ${slm}.bgl_entry AS
SELECT
    entry_id,
    ts,
    epoch_utc,
    node,
    -- Topology from Rxx-My-Nz-... node names: rack Rxx, midplane Rxx-My, node card Rxx-My-Nz.
    -- A level is NULL when the name does not carry it: link cards (Rxx-My-Lz-...) and bare
    -- midplanes have no node card; NULL, UNKNOWN_LOCATION and '-' have none of the three.
    -- scripts/derive.py reports how many Log Entries that affects.
    nullif(regexp_extract(node, '^(R[0-9A-F]{2})(-|$)', 1), '')                AS rack,
    nullif(regexp_extract(node, '^(R[0-9A-F]{2}-M[01])(-|$)', 1), '')          AS midplane,
    nullif(regexp_extract(node, '^(R[0-9A-F]{2}-M[01]-N[0-9A-F])(-|$)', 1), '') AS nodecard,
    component,
    level,
    alert_tag,
    message,
    CAST(NULL AS VARCHAR)                       AS template_id,
    CAST(NULL AS VARCHAR)                       AS family_id,
    alert_tag                                   AS pattern_key
FROM temp.main.bgl_adapted;

-- OpenStack. API lines look like
--   10.11.10.1 "GET /v2/<tenant>/servers/detail HTTP/1.1" status: 200 len: 1893 time: 0.2477829
-- endpoint is the method and the path with ids masked: UUIDs as {uuid}, 32-hex tenant ids as
-- {tenant}, numeric segments as {n} (applied twice so adjacent numeric segments are masked too).
-- instance_ref is the instance UUID from "[instance: <uuid>]", else from "/servers/<uuid>", else
-- from "for instance <uuid>" (the network-vif-plugged events), so the API calls and events of an
-- instance can be joined to its compute lines (#1).
CREATE OR REPLACE TABLE ${slm}.os_entry AS
WITH parsed AS (
    SELECT
        *,
        nullif(regexp_extract(
            message, '"([A-Z]+) ([^ ?"]+)[^"]*" status: ([0-9]+) len: [0-9]+ time: ([0-9.]+)', 1), '') AS method,
        nullif(regexp_extract(
            message, '"([A-Z]+) ([^ ?"]+)[^"]*" status: ([0-9]+) len: [0-9]+ time: ([0-9.]+)', 2), '') AS path,
        nullif(regexp_extract(
            message, '"([A-Z]+) ([^ ?"]+)[^"]*" status: ([0-9]+) len: [0-9]+ time: ([0-9.]+)', 3), '') AS status,
        nullif(regexp_extract(
            message, '"([A-Z]+) ([^ ?"]+)[^"]*" status: ([0-9]+) len: [0-9]+ time: ([0-9.]+)', 4), '') AS seconds
    FROM temp.main.os_adapted
)
SELECT
    entry_id,
    ts,
    epoch_utc,
    source_id,
    log_file,
    pid,
    component,
    level,
    request_id,
    instance_id,
    coalesce(
        nullif(regexp_extract(message,
            '\[instance: ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\]', 1), ''),
        nullif(regexp_extract(message,
            '/servers/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', 1), ''),
        nullif(regexp_extract(message,
            'for instance ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', 1), '')
    )                                           AS instance_ref,
    method || ' ' || regexp_replace(regexp_replace(regexp_replace(regexp_replace(
        path,
        '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '{uuid}', 'g'),
        '[0-9a-f]{32}', '{tenant}', 'g'),
        '/[0-9]+(/|$)', '/{n}\1', 'g'),
        '/[0-9]+(/|$)', '/{n}\1', 'g')          AS endpoint,
    CAST(status AS INTEGER)                     AS http_status,
    CAST(seconds AS DOUBLE)                     AS latency_s,
    message,
    CAST(NULL AS VARCHAR)                       AS template_id,
    CAST(NULL AS VARCHAR)                       AS family_id,
    CAST(NULL AS VARCHAR)                       AS pattern_key
FROM parsed;
