-- r4x: per-Role views + LOGIN roles experiment (own copy, does not touch shared tables' ACLs)
DROP VIEW IF EXISTS r4x_mgr_v; DROP VIEW IF EXISTS r4x_lead_v; DROP VIEW IF EXISTS r4x_eng_v;
DROP TABLE IF EXISTS r4x_bgl;
CREATE TABLE r4x_bgl AS SELECT * FROM bgl WHERE line_no <= 200000;
CREATE VIEW r4x_eng_v AS SELECT line_no, ts, node, component, level, alert_tag, message FROM r4x_bgl;
CREATE VIEW r4x_lead_v AS SELECT ts, node, component, level, alert_tag FROM r4x_bgl;
CREATE VIEW r4x_mgr_v AS SELECT date_trunc('day', ts) AS day, component, level, count(*) AS entries, count(DISTINCT node) AS nodes FROM r4x_bgl GROUP BY 1,2,3;
CREATE ROLE r4x_engineer LOGIN PASSWORD 'r4x_eng_pw';
CREATE ROLE r4x_lead LOGIN PASSWORD 'r4x_lead_pw';
CREATE ROLE r4x_manager LOGIN PASSWORD 'r4x_mgr_pw';
GRANT SELECT ON r4x_eng_v TO r4x_engineer;
GRANT SELECT ON r4x_lead_v TO r4x_lead;
GRANT SELECT ON r4x_mgr_v TO r4x_manager;
-- step 2 (run after setup): FTS through a view, and column-level grant for the Lead
CREATE TEXT SEARCH DICTIONARY r4x_dict AS split_text(case := 'lower') WITH (frequency, position);
CREATE INDEX r4x_bgl_idx ON r4x_bgl USING inverted (line_no, message r4x_dict);
VACUUM (REFRESH_TABLE) r4x_bgl;
CREATE VIEW r4x_eng_search_v AS SELECT line_no, message FROM r4x_bgl_idx;
GRANT SELECT ON r4x_eng_search_v TO r4x_engineer;
GRANT SELECT (line_no, ts, node, component, level) ON r4x_bgl TO r4x_lead;
