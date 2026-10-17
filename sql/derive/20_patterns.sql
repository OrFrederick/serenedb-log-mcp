-- Pattern dictionary (T9, #6 Decision proposal, plan section 2.2). Every Log Entry gets a Pattern:
-- the message shape that Log Entries share once variable parts are masked. The work runs once per
-- distinct message, and Log Entries join to the dictionary on the message text, so nothing here
-- depends on line numbers from the loader.
--
--   Tier 1 (template_source 'loghub2'): the Loghub-2.0 template whose generated regex matches the
--          message, strict \S+ wildcards before loose .* ones, most specific first. The macros
--          pattern_tier1_<dataset>(m) return its EventId; scripts/gen_pattern_sql.py generates them
--          at derive time from the downloaded files (the pattern_tier1 placeholder below; nothing
--          is committed). With SLM_PATTERN_TIER1=0 they return NULL.
--   Tier 2 (template_source 'sqlmask'): the generic, dataset-agnostic g10 mask chain for every
--          message Tier 1 does not match.
--   NULL and blank messages get template_source 'empty' and the template text ''.
--
-- template_id is a content hash, never a parser cluster id: the first 16 hex characters of the md5
-- of '<dataset>:<template text>'. family_id is the md5 of the same key without a trailing
-- ' over <*> seconds', so the wording drift of #1 (E218/E112 and five more pairs) is one Pattern
-- Family. The step is re-runnable on its own (scripts/derive.py --only 20_patterns): it rebuilds
-- both tables and recomputes the Pattern columns of the Log Entries.

-- Tier 2: the g10 masks of #6 (research/6-NoNaim95 in_db/mask_macros.sql), applied in order. Not
-- the label-tuned v10 chain, which was fitted on the labels it is scored against.
CREATE OR REPLACE MACRO ${slm}.pattern_mask_g10(s) AS
    regexp_replace(regexp_replace(regexp_replace(regexp_replace(regexp_replace(regexp_replace(
    regexp_replace(regexp_replace(regexp_replace(regexp_replace(regexp_replace(
        s,
        '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', '<*>', 'g'),
        '\d+\.\d+\.\d+\.\d+(:\d+)?', '<*>', 'g'),
        '\b([0-9A-Fa-f]{2}:){2,}[0-9A-Fa-f]{2}\b', '<*>', 'g'),
        '\b[\w-]+(\.[\w-]+)+\.(com|net|org|edu|us|local|io)\b', '<*>', 'g'),
        '(/[^\s/:,()\[\]\"'']+)+/?', '<*>', 'g'),
        '[^\s(),=:\[\].\"'']*\d[^\s(),=:\[\].\"'']*', '<*>', 'g'),
        '<\*>([.,:]<\*>)+', '<*>', 'g'),
        '(?i)\b(true|false|null|root|admin)\b', '<*>', 'g'),
        '\.*(<\*>)+', '<*>', 'g'),
        '<\*>([.,:]<\*>)+', '<*>', 'g'),
        '<\*>( <\*>)+', '<*>', 'g');

CREATE OR REPLACE MACRO ${slm}.pattern_template_id(dataset, template_text) AS
    left(md5(dataset || ':' || template_text), 16);

CREATE OR REPLACE MACRO ${slm}.pattern_family_id(dataset, template_text) AS
    md5(dataset || ':' || regexp_replace(template_text, ' over <\*> seconds$', ''));

-- Tier 1: pattern_tier1_bgl(m), pattern_tier1_openstack(m) and the temporary table
-- pattern_tier1_template(dataset, event_id, template_text).
${pattern_tier1};

-- One row per distinct message of each Dataset.
CREATE OR REPLACE TABLE ${slm}.pattern_dict AS
WITH messages AS (
    SELECT 'bgl' AS dataset, message,
        CASE WHEN trim(message) <> '' THEN ${slm}.pattern_tier1_bgl(message) END AS event_id
    FROM (SELECT DISTINCT message FROM ${slm}.bgl_entry) AS m
    UNION ALL
    SELECT 'openstack' AS dataset, message,
        CASE WHEN trim(message) <> '' THEN ${slm}.pattern_tier1_openstack(message) END AS event_id
    FROM (SELECT DISTINCT message FROM ${slm}.os_entry) AS m
),
texts AS (
    SELECT
        m.dataset,
        m.message,
        m.event_id,
        CASE
            WHEN coalesce(trim(m.message), '') = '' THEN 'empty'
            WHEN m.event_id IS NOT NULL THEN 'loghub2'
            ELSE 'sqlmask'
        END AS template_source,
        CASE
            WHEN coalesce(trim(m.message), '') = '' THEN ''
            WHEN m.event_id IS NOT NULL THEN t.template_text
            ELSE ${slm}.pattern_mask_g10(m.message)
        END AS template_text
    FROM messages AS m
    LEFT JOIN temp.main.pattern_tier1_template AS t ON t.dataset = m.dataset AND t.event_id = m.event_id
)
SELECT
    dataset,
    message,
    ${slm}.pattern_template_id(dataset, template_text)  AS template_id,
    template_text,
    template_source,
    event_id,
    ${slm}.pattern_family_id(dataset, template_text)    AS family_id
FROM texts;

-- The Pattern columns of the Log Entries. pattern_key is the alert category for BGL alert lines
-- (set by 10_entries.sql) and the template_id everywhere else: it is replaced only where it is
-- NULL or still holds the template_id of an earlier run, so a re-run never touches an alert line.
UPDATE ${slm}.bgl_entry AS e
SET template_id = d.template_id,
    family_id = d.family_id,
    pattern_key = CASE
        WHEN e.pattern_key IS NULL OR e.pattern_key = e.template_id THEN d.template_id
        ELSE e.pattern_key
    END
FROM ${slm}.pattern_dict AS d
WHERE d.dataset = 'bgl' AND d.message = e.message;

UPDATE ${slm}.bgl_entry AS e
SET template_id = d.template_id,
    family_id = d.family_id,
    pattern_key = CASE
        WHEN e.pattern_key IS NULL OR e.pattern_key = e.template_id THEN d.template_id
        ELSE e.pattern_key
    END
FROM ${slm}.pattern_dict AS d
WHERE d.dataset = 'bgl' AND d.message IS NULL AND e.message IS NULL;

UPDATE ${slm}.os_entry AS e
SET template_id = d.template_id,
    family_id = d.family_id,
    pattern_key = CASE
        WHEN e.pattern_key IS NULL OR e.pattern_key = e.template_id THEN d.template_id
        ELSE e.pattern_key
    END
FROM ${slm}.pattern_dict AS d
WHERE d.dataset = 'openstack' AND d.message = e.message;

UPDATE ${slm}.os_entry AS e
SET template_id = d.template_id,
    family_id = d.family_id,
    pattern_key = CASE
        WHEN e.pattern_key IS NULL OR e.pattern_key = e.template_id THEN d.template_id
        ELSE e.pattern_key
    END
FROM ${slm}.pattern_dict AS d
WHERE d.dataset = 'openstack' AND d.message IS NULL AND e.message IS NULL;

-- One row per Pattern: the catalogue behind list_patterns (T14). component and level are the most
-- frequent ones among its Log Entries (ties: the first by name); first_seen and last_seen use ts
-- as logged. If a Tier 2 mask ever produced the exact text of a Tier 1 template, both would share
-- the template_id and the Pattern reports loghub2 (never the case on the Base Corpus).
CREATE OR REPLACE TABLE ${slm}.pattern AS
WITH entries AS (
    SELECT 'bgl' AS dataset, template_id, component, level, ts FROM ${slm}.bgl_entry
    UNION ALL
    SELECT 'openstack' AS dataset, template_id, component, level, ts FROM ${slm}.os_entry
),
counts AS (
    SELECT dataset, template_id, count(*) AS entries, min(ts) AS first_seen, max(ts) AS last_seen
    FROM entries
    GROUP BY dataset, template_id
),
components AS (
    SELECT dataset, template_id, component,
        row_number() OVER (
            PARTITION BY dataset, template_id ORDER BY count(*) DESC, component NULLS LAST
        ) AS rank
    FROM entries
    GROUP BY dataset, template_id, component
),
levels AS (
    SELECT dataset, template_id, level,
        row_number() OVER (
            PARTITION BY dataset, template_id ORDER BY count(*) DESC, level NULLS LAST
        ) AS rank
    FROM entries
    GROUP BY dataset, template_id, level
),
texts AS (
    SELECT dataset, template_id,
        min(template_text) AS template_text,
        min(template_source) AS template_source,
        min(event_id) AS event_id,
        min(family_id) AS family_id
    FROM ${slm}.pattern_dict
    GROUP BY dataset, template_id
)
SELECT
    t.dataset,
    t.template_id,
    t.template_text,
    t.template_source,
    t.event_id,
    t.family_id,
    c.component,
    l.level,
    n.entries,
    n.first_seen,
    n.last_seen
FROM texts AS t
JOIN counts AS n ON n.dataset = t.dataset AND n.template_id = t.template_id
LEFT JOIN components AS c ON c.dataset = t.dataset AND c.template_id = t.template_id AND c.rank = 1
LEFT JOIN levels AS l ON l.dataset = t.dataset AND l.template_id = t.template_id AND l.rank = 1;

-- When the step runs on its own, the inverted indexes already exist; publish the updated columns.
VACUUM (REFRESH_TABLE) ${slm}.bgl_entry;
VACUUM (REFRESH_TABLE) ${slm}.os_entry;
