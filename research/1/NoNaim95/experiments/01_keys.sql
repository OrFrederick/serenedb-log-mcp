-- 01_keys.sql: per-Log-Entry pattern key (cheap masked message) + parsed BGL location hierarchy.
-- Location: node 'Rxx-My-Nz-C:Jaa-Ubb' -> rack Rxx, midplane Rxx-My, node card Rxx-My-Nz.
-- Non-standard node values (NULL, UNKNOWN_LOCATION, '-', link cards Rxx-My-Lz, bare Rxx-My) are kept;
-- rack/midplane are parsed where a prefix exists, otherwise NULL.
CREATE OR REPLACE TABLE r1e_bgl AS
SELECT line_no,
       ts,
       epoch_us(ts) / 1e6                                         AS t,      -- seconds, microsecond precision
       alert_tag,
       node,
       component,
       level,
       nullif(regexp_extract(node, '^(R[0-9A-F]{2})', 1), '')              AS rack,
       nullif(regexp_extract(node, '^(R[0-9A-F]{2}-M[01])', 1), '')        AS midplane,
       nullif(regexp_extract(node, '^(R[0-9A-F]{2}-M[01]-N[0-9A-F])', 1), '') AS nodecard,
       CASE WHEN node IS NULL THEN 'null'
            WHEN regexp_full_match(node, 'R[0-9A-F]{2}-M[01]-N[0-9A-F]-[CI]:J[0-9]{2}-U[0-9]{2}') THEN 'chip'
            WHEN regexp_full_match(node, 'R[0-9A-F]{2}-M[01]-N[0-9A-F]') THEN 'nodecard'
            WHEN regexp_full_match(node, 'R[0-9A-F]{2}-M[01]-L[0-9A-F].*') THEN 'linkcard'
            WHEN regexp_full_match(node, 'R[0-9A-F]{2}.*') THEN 'rack_or_midplane'
            ELSE 'nonlocation' END                                AS node_kind,
       regexp_replace(regexp_replace(regexp_replace(regexp_replace(regexp_replace(regexp_replace(regexp_replace(
           coalesce(message, ''),
           'R[0-9A-F]{2}-M[01](-[A-Z][0-9A-F](-[A-Z0-9:-]+)?)?', '<NODE>', 'g'),
           '[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}(:[0-9]+)?', '<IP>', 'g'),
           '0x[0-9a-fA-F]+', '<HEX>', 'g'),
           '\b[0-9a-fA-F]{8,}\b', '<HEX>', 'g'),
           '\b[0-9a-fA-F]*([0-9][0-9a-fA-F]*[a-fA-F]|[a-fA-F][0-9a-fA-F]*[0-9])[0-9a-fA-F]*\b', '<HEX>', 'g'),
           '[0-9]+', '<N>', 'g'),
           '\s+', ' ', 'g')                                       AS pkey
FROM bgl;

-- distinct keys: masked key, masked key restricted to alert rows, alert_tag
SELECT count(DISTINCT pkey) AS masked_keys,
       count(DISTINCT pkey) FILTER (WHERE alert_tag <> '-') AS masked_keys_on_alert_rows,
       count(DISTINCT alert_tag) FILTER (WHERE alert_tag <> '-') AS alert_tags,
       count(DISTINCT component || '|' || pkey) AS comp_masked_keys
FROM r1e_bgl;

-- key frequency distribution: how many keys cover 50/90/99% of entries
WITH k AS (SELECT pkey, count(*) n FROM r1e_bgl GROUP BY pkey),
     r AS (SELECT n, (sum(n) OVER (ORDER BY n DESC ROWS UNBOUNDED PRECEDING))::double / (sum(n) OVER ())::double AS cum, -- NB: int/int is integer division in SereneDB
                  row_number() OVER (ORDER BY n DESC) rk FROM k)
SELECT min(rk) FILTER (WHERE cum >= 0.5) k50, min(rk) FILTER (WHERE cum >= 0.9) k90,
       min(rk) FILTER (WHERE cum >= 0.99) k99, count(*) FILTER (WHERE n = 1) singletons, count(*) total_keys
FROM r;

-- how many masked keys per alert_tag (alert_tag is coarser)
SELECT alert_tag, count(*) n, count(DISTINCT pkey) masked_keys, min(pkey) example_key
FROM r1e_bgl WHERE alert_tag <> '-' GROUP BY alert_tag ORDER BY n DESC;

-- node value kinds (non-standard node values)
SELECT node_kind, count(*) entries, count(DISTINCT node) distinct_values,
       count(*) FILTER (WHERE alert_tag <> '-') alert_entries
FROM r1e_bgl GROUP BY node_kind ORDER BY entries DESC;

-- compare against Loghub-2.0 template count (320 templates per decisions.md)
SELECT count(DISTINCT event_id) lh2_templates FROM lh2_bgl;

-- top keys by volume
SELECT pkey, count(*) n, count(DISTINCT node) nodes, max(alert_tag) any_tag FROM r1e_bgl GROUP BY pkey ORDER BY n DESC LIMIT 15;
