-- Pattern parameters: the values behind each <*> of a Log Entry's Pattern, so that a question like
-- "which program crashed" or "which mount point failed" is a GROUP BY instead of a LIKE. The work
-- runs once per distinct message of slm.pattern_dict (like 20_patterns.sql), and Log Entries join
-- to it on the message text. The canonical entry tables are not changed.
--
--   pattern_param  one row per <*> slot of a Pattern: its position (1-based, left to right), a
--                  context (up to three words of literal text before the slot, e.g. 'for program'),
--                  and the two anchored RE2 patterns that capture only that slot.
--   entry_param    one row per value per Log Entry: (dataset, entry_id, template_id, pos, context,
--                  value). Join it to bgl_entry or os_entry on entry_id.
--
-- A slot is captured strictly (\S+, one whitespace-free token) when the whole message matches the
-- strict form of its Pattern, the way the Tier 1 matcher decides first, and loosely (.*?, any
-- text) otherwise. Tier 2 (g10 mask) Patterns go through the same code: their text is the mask of
-- the message itself, so the loose form always matches. Patterns without a <*> have no slots.
-- RE2 allows at most 9 capture groups and some Patterns have 25 slots, so each slot gets its own
-- pattern with one group.

-- One row per slot. esc is the template text as a regex: each <*> held as chr(1), RE2
-- metacharacters escaped, whitespace runs as \s+. marked turns the pos-th chr(1) into chr(2).
CREATE OR REPLACE TABLE ${slm}.pattern_param AS
WITH patterns AS (
    SELECT dataset, template_id, template_text,
        regexp_replace(regexp_replace(replace(template_text, '<*>', chr(1)),
            '([\\.^$|?*+()\[\]{}])', '\\\1', 'g'), '\s+', '\\s+', 'g') AS esc,
        (length(template_text) - length(replace(template_text, '<*>', ''))) / 3 AS slots
    FROM ${slm}.pattern
    WHERE template_text LIKE '%<*>%'
),
slots AS (
    SELECT patterns.*, unnest(generate_series(1, slots)) AS pos FROM patterns
),
marked AS (
    SELECT slots.*,
        regexp_replace(esc, '^((?:[^\x01]*\x01){' || (pos - 1) || '}[^\x01]*)\x01', '\1' || chr(2))
            AS marked,
        regexp_extract(replace(template_text, '<*>', chr(1)),
            '^(?:[^\x01]*\x01){' || (pos - 1) || '}([^\x01]*)\x01', 1) AS before
    FROM slots
)
SELECT
    dataset,
    template_id,
    pos,
    slots,
    nullif(rtrim(trim(regexp_extract(
        before, '([A-Za-z_][\w.-]*(?:\s+[A-Za-z_][\w.-]*){0,2})\W*$', 1)), '.:-'), '') AS context,
    '^' || replace(esc, chr(1), '\S+') || '$'                                   AS strict_full,
    '^' || replace(replace(marked, chr(1), '\S+'), chr(2), '(\S+)') || '$'      AS strict_re,
    '^' || replace(replace(marked, chr(1), '.*?'), chr(2), '(.*?)') || '$'      AS loose_re
FROM marked;

-- Strict or loose, once per distinct message.
CREATE OR REPLACE TEMPORARY TABLE param_message AS
SELECT d.dataset, d.message, d.template_id, regexp_full_match(d.message, p.strict_full) AS strict
FROM ${slm}.pattern_dict AS d
JOIN (SELECT dataset, template_id, min(strict_full) AS strict_full
      FROM ${slm}.pattern_param GROUP BY dataset, template_id) AS p
  ON p.dataset = d.dataset AND p.template_id = d.template_id;

-- The value of every slot of every distinct message.
CREATE OR REPLACE TEMPORARY TABLE param_value AS
SELECT m.dataset, m.message, m.template_id, p.pos, p.context,
    regexp_extract(m.message, CASE WHEN m.strict THEN p.strict_re ELSE p.loose_re END, 1) AS value
FROM param_message AS m
JOIN ${slm}.pattern_param AS p ON p.dataset = m.dataset AND p.template_id = m.template_id;

CREATE OR REPLACE TABLE ${slm}.entry_param AS
SELECT 'bgl' AS dataset, e.entry_id, v.template_id, v.pos, v.context, v.value
FROM ${slm}.bgl_entry AS e
JOIN param_value AS v ON v.dataset = 'bgl' AND v.message = e.message
UNION ALL
SELECT 'openstack' AS dataset, e.entry_id, v.template_id, v.pos, v.context, v.value
FROM ${slm}.os_entry AS e
JOIN param_value AS v ON v.dataset = 'openstack' AND v.message = e.message;

DROP TABLE param_value;
DROP TABLE param_message;
