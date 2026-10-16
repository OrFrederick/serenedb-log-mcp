SELECT alert_label, count(*) incidents, sum(lines) lines,
  count(DISTINCT date_trunc('week', start_ts)) weeks_active,
  count(DISTINCT date_trunc('day', start_ts)) days_active,
  min(start_ts)::date first_seen, max(start_ts)::date last_seen,
  round(max(lines)::numeric / sum(lines), 2) top_incident_share,
  max(midplanes) max_midplanes
FROM r1_bgl_incidents GROUP BY 1 ORDER BY incidents DESC LIMIT 20
