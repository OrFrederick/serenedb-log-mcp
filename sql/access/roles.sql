-- Access control per Role (T22, #4 Decision proposal, docs/access-control.md): per-Role views,
-- LOGIN roles and GRANTs, as defence in depth behind the Role-scoped tools. SereneDB has no
-- row-level security, so the separation is by views: each Role gets a schema of its own,
-- <SLM_SCHEMA>_<role>, that holds a view of every derived table under the canonical name, and its
-- LOGIN role may read that schema and nothing else. corpus.datasets.relation() returns these views
-- to a Role whose DATABASE_URL_<ROLE> is set, so the tools run unchanged on them.
--
-- Run by scripts/access.py (`make access`) as a superuser, after `make derive`: it first drops the
-- views and LOGIN roles of an earlier run (access.drop_statements), then runs this file. A view
-- reads its source with its owner's privileges, so no login needs any grant on ${slm}.
--
-- Placeholders, filled by scripts/access.py:
--   ${slm}                                   the derived schema (SLM_SCHEMA), quoted
--   ${engineer_schema} ${team_lead_schema} ${manager_schema}
--                                            the views of each Role: <SLM_SCHEMA>_<role>, quoted
--   ${engineer_login} ${team_lead_login} ${manager_login}
--                                            the user= of DATABASE_URL_<ROLE>, quoted
--   ${engineer_password} ${team_lead_password} ${manager_password}
--                                            the password= of DATABASE_URL_<ROLE>, a SQL literal
--
-- Verified on SereneDB 26.10.1 (T22): one object per GRANT (GRANT ... ON a, b is a syntax error);
-- a view over a table blocks CREATE OR REPLACE TABLE on it, so scripts/derive.py drops these
-- schemas before it rebuilds the tables; a view over the canonical index relation loses BM25 and
-- the ::score(NULL) filters, so the Roles that search text get an inverted index over their own
-- entry view instead (3 s for the 4.7 million BGL rows), a snapshot taken when it is built.

CREATE ROLE ${engineer_login} LOGIN PASSWORD ${engineer_password};
CREATE ROLE ${team_lead_login} LOGIN PASSWORD ${team_lead_password};
CREATE ROLE ${manager_login} LOGIN PASSWORD ${manager_password};

-- Engineer: investigates one failure, so it reads every column, raw Log Entries included, and
-- searches the message text.
CREATE SCHEMA ${engineer_schema};
CREATE VIEW ${engineer_schema}.bgl_entry AS SELECT * FROM ${slm}.bgl_entry;
CREATE VIEW ${engineer_schema}.os_entry AS SELECT * FROM ${slm}.os_entry;
CREATE VIEW ${engineer_schema}.ownership AS SELECT * FROM ${slm}.ownership;
CREATE VIEW ${engineer_schema}.pattern AS SELECT * FROM ${slm}.pattern;
CREATE VIEW ${engineer_schema}.pattern_dict AS SELECT * FROM ${slm}.pattern_dict;
CREATE VIEW ${engineer_schema}.os_instance AS SELECT * FROM ${slm}.os_instance;
-- Incidents (T10, T27) carry counts, times and node names, no Log Entry text.
CREATE VIEW ${engineer_schema}.incident AS SELECT * FROM ${slm}.incident;
-- The same columns and dictionary as the canonical indexes (sql/derive/90_indexes.sql).
CREATE INDEX bgl_entry_idx ON ${engineer_schema}.bgl_entry USING inverted (
    entry_id, message ${slm}.log_text, ts, epoch_utc,
    node, rack, midplane, nodecard, component, level, alert_tag,
    pattern_key, template_id, family_id
);
CREATE INDEX os_entry_idx ON ${engineer_schema}.os_entry USING inverted (
    entry_id, message ${slm}.log_text, ts, epoch_utc,
    source_id, log_file, pid, component, level, request_id, instance_id, instance_ref,
    endpoint, http_status, latency_s,
    pattern_key, template_id, family_id
);
-- The Pattern catalogue index (sql/derive/20_patterns.sql), for search_patterns (T14).
CREATE INDEX pattern_idx ON ${engineer_schema}.pattern USING inverted (dataset, template_text ${slm}.pattern_text);
GRANT USAGE ON SCHEMA ${engineer_schema} TO ${engineer_login};
GRANT SELECT ON ${engineer_schema}.bgl_entry TO ${engineer_login};
GRANT SELECT ON ${engineer_schema}.os_entry TO ${engineer_login};
GRANT SELECT ON ${engineer_schema}.ownership TO ${engineer_login};
GRANT SELECT ON ${engineer_schema}.pattern TO ${engineer_login};
GRANT SELECT ON ${engineer_schema}.pattern_dict TO ${engineer_login};
GRANT SELECT ON ${engineer_schema}.os_instance TO ${engineer_login};
GRANT SELECT ON ${engineer_schema}.incident TO ${engineer_login};

-- Team Lead: looks for recurring problems and shows at most three sample Log Entries per problem
-- (roles.shape_for_role), so it needs the message text too; its views have the Engineer's columns,
-- and the sample cap stays in the Role shaping.
CREATE SCHEMA ${team_lead_schema};
CREATE VIEW ${team_lead_schema}.bgl_entry AS SELECT * FROM ${slm}.bgl_entry;
CREATE VIEW ${team_lead_schema}.os_entry AS SELECT * FROM ${slm}.os_entry;
CREATE VIEW ${team_lead_schema}.ownership AS SELECT * FROM ${slm}.ownership;
CREATE VIEW ${team_lead_schema}.pattern AS SELECT * FROM ${slm}.pattern;
CREATE VIEW ${team_lead_schema}.pattern_dict AS SELECT * FROM ${slm}.pattern_dict;
CREATE VIEW ${team_lead_schema}.os_instance AS SELECT * FROM ${slm}.os_instance;
CREATE VIEW ${team_lead_schema}.incident AS SELECT * FROM ${slm}.incident;
CREATE INDEX bgl_entry_idx ON ${team_lead_schema}.bgl_entry USING inverted (
    entry_id, message ${slm}.log_text, ts, epoch_utc,
    node, rack, midplane, nodecard, component, level, alert_tag,
    pattern_key, template_id, family_id
);
CREATE INDEX os_entry_idx ON ${team_lead_schema}.os_entry USING inverted (
    entry_id, message ${slm}.log_text, ts, epoch_utc,
    source_id, log_file, pid, component, level, request_id, instance_id, instance_ref,
    endpoint, http_status, latency_s,
    pattern_key, template_id, family_id
);
CREATE INDEX pattern_idx ON ${team_lead_schema}.pattern USING inverted (dataset, template_text ${slm}.pattern_text);
GRANT USAGE ON SCHEMA ${team_lead_schema} TO ${team_lead_login};
GRANT SELECT ON ${team_lead_schema}.bgl_entry TO ${team_lead_login};
GRANT SELECT ON ${team_lead_schema}.os_entry TO ${team_lead_login};
GRANT SELECT ON ${team_lead_schema}.ownership TO ${team_lead_login};
GRANT SELECT ON ${team_lead_schema}.pattern TO ${team_lead_login};
GRANT SELECT ON ${team_lead_schema}.pattern_dict TO ${team_lead_login};
GRANT SELECT ON ${team_lead_schema}.os_instance TO ${team_lead_login};
GRANT SELECT ON ${team_lead_schema}.incident TO ${team_lead_login};

-- Manager: decides from measured frequency and affected Components, so its views hold no Log Entry
-- text: every canonical column but message (corpus.datasets.ROLE_WITHHELD_COLUMNS), and no
-- full-text index over Log Entries. Pattern texts stay, with their catalogue index: they name the
-- problems the Manager ranks. Node names stay
-- too, because count_by and trend count distinct sources on them; the Role shaping and the Manager
-- playbook keep them out of the answer body.
CREATE SCHEMA ${manager_schema};
CREATE VIEW ${manager_schema}.bgl_entry AS SELECT
    entry_id, ts, epoch_utc, node, rack, midplane, nodecard, component, level, alert_tag,
    template_id, family_id, pattern_key
FROM ${slm}.bgl_entry;
CREATE VIEW ${manager_schema}.os_entry AS SELECT
    entry_id, ts, epoch_utc, source_id, log_file, pid, component, level, request_id, instance_id,
    instance_ref, endpoint, http_status, latency_s, template_id, family_id, pattern_key
FROM ${slm}.os_entry;
CREATE VIEW ${manager_schema}.ownership AS SELECT * FROM ${slm}.ownership;
CREATE VIEW ${manager_schema}.pattern AS SELECT * FROM ${slm}.pattern;
CREATE VIEW ${manager_schema}.pattern_dict AS SELECT
    dataset, template_id, template_text, template_source, event_id, family_id
FROM ${slm}.pattern_dict;
CREATE VIEW ${manager_schema}.os_instance AS SELECT * FROM ${slm}.os_instance;
CREATE VIEW ${manager_schema}.incident AS SELECT * FROM ${slm}.incident;
CREATE INDEX pattern_idx ON ${manager_schema}.pattern USING inverted (dataset, template_text ${slm}.pattern_text);
GRANT USAGE ON SCHEMA ${manager_schema} TO ${manager_login};
GRANT SELECT ON ${manager_schema}.bgl_entry TO ${manager_login};
GRANT SELECT ON ${manager_schema}.os_entry TO ${manager_login};
GRANT SELECT ON ${manager_schema}.ownership TO ${manager_login};
GRANT SELECT ON ${manager_schema}.pattern TO ${manager_login};
GRANT SELECT ON ${manager_schema}.pattern_dict TO ${manager_login};
GRANT SELECT ON ${manager_schema}.os_instance TO ${manager_login};
GRANT SELECT ON ${manager_schema}.incident TO ${manager_login};
