-- Inverted indexes (T4): the last derive step. Earlier steps rebuild the entry tables with
-- CREATE OR REPLACE, which drops their indexes, so the indexes are built after everything else.
-- Tools query them only through corpus.datasets.relation(), never by name.

DROP INDEX IF EXISTS ${slm}.bgl_entry_idx;
DROP INDEX IF EXISTS ${slm}.os_entry_idx;
DROP TEXT SEARCH DICTIONARY IF EXISTS ${slm}.log_text;

-- message: lower-case words, no stemming; frequency and norm for BM25, position for phrases,
-- offset for highlighting. scripts/derive.py picks the DDL form for the server version.
${create_log_text_dictionary};

-- Every other column is indexed verbatim (one token per value: exact, case-sensitive matches) or,
-- for numbers and times, for exact and range filters (ts_between).
CREATE INDEX bgl_entry_idx ON ${slm}.bgl_entry USING inverted (
    entry_id, message ${slm}.log_text, ts, epoch_utc,
    node, rack, midplane, nodecard, component, level, alert_tag,
    pattern_key, template_id, family_id
);

CREATE INDEX os_entry_idx ON ${slm}.os_entry USING inverted (
    entry_id, message ${slm}.log_text, ts, epoch_utc,
    source_id, log_file, pid, component, level, request_id, instance_id, instance_ref,
    endpoint, http_status, latency_s,
    pattern_key, template_id, family_id
);

-- The index is eventually consistent; publish everything now.
VACUUM (REFRESH_TABLE) ${slm}.bgl_entry;
VACUUM (REFRESH_TABLE) ${slm}.os_entry;
