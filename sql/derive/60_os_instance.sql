-- OpenStack instance phases (T11, plan section 2.2): one row per instance, with the durations of
-- its start-up path and their robust z-scores, so latency_stats ranks Phase-Duration Anomalies
-- without a regex at query time (#1, #2).
--
-- The input is the canonical OpenStack table only: every Log Entry whose instance_ref names the
-- instance (the [instance: ...] compute lines, GET and DELETE /servers/<uuid> and the vif-plugged
-- event). Nothing here knows which instances are labelled; the labels are read only by
-- scripts/ground_truth.py, which scores this table afterwards.
--
-- The start-up path of an instance, as nova-compute logs it:
--   Attempting claim ... Creating image ... VM Started ... VM Paused ... (network-vif-plugged)
--   ... VM Resumed ... Took N seconds to spawn the instance ... Took N seconds to build instance
-- Columns:
--   instance_id   the instance UUID (instance_ref of its Log Entries)
--   source_id     the neutral source of its first Log Entry (an instance can span a log rotation)
--   entries       Log Entries that name it; first_ts, last_ts their first and last time
--   spawn_ts      the time of its "Took N seconds to spawn the instance" Log Entry
--   image_s       Creating image -> VM Started: the image is created and the domain boots
--   paused_s      VM Paused -> VM Resumed: the domain waits paused, for its network
--   spawn_s       nova's own "Took N seconds to spawn the instance on the hypervisor"
--   build_s       nova's own "Took N seconds to build instance"
--   <phase>_z     robust z-score: (x - median) / (1.4826 * MAD), median and MAD over every
--                 instance that has the phase; NULL when the phase is missing or the MAD is 0
-- Durations from timestamps are rounded to the millisecond, the precision of the logged times.
-- Teardown durations are not scored: their MAD is a few milliseconds, so a robust z-score would
-- call ordinary jitter an anomaly.

CREATE OR REPLACE TABLE ${slm}.os_instance AS
WITH phases AS (
    SELECT
        instance_ref                                                        AS instance_id,
        arg_min(source_id, epoch_utc)                                       AS source_id,
        count(*)                                                            AS entries,
        min(ts)                                                             AS first_ts,
        max(ts)                                                             AS last_ts,
        min(ts) FILTER (
            WHERE message LIKE '%] Took % seconds to spawn the instance on the hypervisor.')
                                                                            AS spawn_ts,
        round(
            min(epoch_utc) FILTER (WHERE message LIKE '%] VM Started (Lifecycle Event)')
            - min(epoch_utc) FILTER (WHERE message LIKE '%] Creating image'), 3)
                                                                            AS image_s,
        round(
            min(epoch_utc) FILTER (WHERE message LIKE '%] VM Resumed (Lifecycle Event)')
            - min(epoch_utc) FILTER (WHERE message LIKE '%] VM Paused (Lifecycle Event)'), 3)
                                                                            AS paused_s,
        max(TRY_CAST(nullif(regexp_extract(
            message, '\] Took ([0-9.]+) seconds to spawn the instance on the hypervisor', 1), '')
            AS DOUBLE))                                                     AS spawn_s,
        max(TRY_CAST(nullif(regexp_extract(
            message, '\] Took ([0-9.]+) seconds to build instance', 1), '')
            AS DOUBLE))                                                     AS build_s
    FROM ${slm}.os_entry
    WHERE instance_ref IS NOT NULL
    GROUP BY instance_ref
),
scale AS (
    SELECT
        median(image_s)     AS image_median,  1.4826 * mad(image_s)  AS image_scale,
        median(paused_s)    AS paused_median, 1.4826 * mad(paused_s) AS paused_scale,
        median(spawn_s)     AS spawn_median,  1.4826 * mad(spawn_s)  AS spawn_scale,
        median(build_s)     AS build_median,  1.4826 * mad(build_s)  AS build_scale
    FROM phases
)
SELECT
    p.instance_id,
    p.source_id,
    p.entries,
    p.first_ts,
    p.last_ts,
    p.spawn_ts,
    p.image_s,
    p.paused_s,
    p.spawn_s,
    p.build_s,
    (p.image_s - s.image_median) / nullif(s.image_scale, 0)                AS image_z,
    (p.paused_s - s.paused_median) / nullif(s.paused_scale, 0)             AS paused_z,
    (p.spawn_s - s.spawn_median) / nullif(s.spawn_scale, 0)                AS spawn_z,
    (p.build_s - s.build_median) / nullif(s.build_scale, 0)                AS build_z
FROM phases AS p
CROSS JOIN scale AS s;
