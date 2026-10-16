WITH bounds AS (SELECT max(ts) AS t_end FROM bgl),
f AS (
  SELECT alert_tag AS problem, min(component) AS component,
         count(*) AS entries,
         count(DISTINCT node) AS nodes,
         count(DISTINCT date_trunc('day', ts)) AS active_days,
         count(*) FILTER (WHERE ts >  (SELECT t_end FROM bounds) - INTERVAL 30 DAY) AS last30,
         count(*) FILTER (WHERE ts <= (SELECT t_end FROM bounds) - INTERVAL 30 DAY
                            AND ts >  (SELECT t_end FROM bounds) - INTERVAL 60 DAY) AS prev30,
         date_diff('day', max(ts), (SELECT t_end FROM bounds)) AS days_since_last
  FROM bgl WHERE alert_tag <> '-' GROUP BY alert_tag),
r AS (
  SELECT *, ln((last30 + 1.0) / (prev30 + 1.0)) AS trend_log,
    percent_rank() OVER (ORDER BY entries)      AS p_freq,
    percent_rank() OVER (ORDER BY nodes)        AS p_spread,
    percent_rank() OVER (ORDER BY active_days)  AS p_persist,
    percent_rank() OVER (ORDER BY ln((last30 + 1.0) / (prev30 + 1.0))) AS p_trend,
    percent_rank() OVER (ORDER BY days_since_last DESC) AS p_recency
  FROM f)
SELECT problem, component, entries, nodes, active_days, last30, prev30, days_since_last,
       round(p_freq,2) pf, round(p_spread,2) ps, round(p_persist,2) pp, round(p_trend,2) pt, round(p_recency,2) pr,
       round((p_freq + p_spread + p_persist + p_trend + p_recency) / 5, 3) AS score
FROM r ORDER BY score DESC LIMIT 15
