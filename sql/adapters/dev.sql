-- Dev adapter (T4): the throwaway dev loader's raw tables -> the two adapter views that the
-- derive steps read. This is the only SQL that knows the dev loader's table shapes; T25 writes
-- sql/adapters/official.sql for the organisers' loader with the same views and columns.
--
-- The views are TEMPORARY: they live in temp.main and die with the derive session, even if it
-- crashes. A persistent view would keep a dependency on the raw tables, and every later reload of
-- them (DROP TABLE, or the loader's atomic swap) would fail with DependentObjectsStillExist.
--
-- Raw tables (scripts/dev_loader.py) in ${raw}:
--   bgl(line_no, alert_tag, epoch, ts, node, component, level, message, raw)
--   openstack(line_no, source_file, log_file, ts, pid, level, component, request_id,
--             instance_id, message, raw)

-- BGL. ts is the local time as logged: US Pacific, with daylight saving time. The raw epoch is
-- the UTC second. The two differ by a whole number of hours (25,200 s in PDT, 28,800 s in PST),
-- so epoch_utc is the UTC second plus the logged fraction of a second. The raw '-' marks a
-- non-alert line and becomes NULL.
CREATE OR REPLACE TEMPORARY VIEW bgl_adapted AS
SELECT
    line_no                                     AS entry_id,
    ts,
    epoch + (epoch_us(ts) % 1000000) / 1e6      AS epoch_utc,
    node,
    component,
    level,
    nullif(alert_tag, '-')                      AS alert_tag,
    message
FROM ${raw}.bgl;

-- OpenStack. The loader's source_file names carry the ground-truth label of each file, so they
-- never leave this view: every file gets a neutral id, os-1, os-2, ..., in load order (the order
-- of its first line). The files give ts without a zone; it is taken as UTC.
CREATE OR REPLACE TEMPORARY VIEW os_adapted AS
WITH files AS (
    SELECT
        source_file,
        CAST('os-' || row_number() OVER (ORDER BY min(line_no)) AS VARCHAR) AS source_id
    FROM ${raw}.openstack
    GROUP BY source_file
)
SELECT
    o.line_no                                   AS entry_id,
    o.ts,
    epoch_us(o.ts) / 1e6                        AS epoch_utc,
    f.source_id,
    o.log_file,
    o.pid,
    o.component,
    o.level,
    o.request_id,
    o.instance_id,
    o.message
FROM ${raw}.openstack AS o
JOIN files AS f ON f.source_file = o.source_file;
