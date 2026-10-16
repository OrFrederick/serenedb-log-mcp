-- V9 gap computed on UTC epoch column instead of local ts (DST fall-back 2005-10-30)
WITH a AS (SELECT alert_label, epoch t, lag(epoch) OVER (PARTITION BY alert_label ORDER BY epoch) p FROM bgl WHERE alert_label <> '-')
SELECT count(*) FILTER (WHERE p IS NULL OR t-p > 5) t5, count(*) FILTER (WHERE p IS NULL OR t-p > 300) t300 FROM a;
-- rows whose local ts order disagrees with UTC epoch order
SELECT count(*) FROM (SELECT ts, lag(ts) OVER (ORDER BY epoch, line_no) pts FROM bgl) x WHERE ts < pts;
SELECT min(ts), max(ts), count(*) FROM bgl WHERE epoch(ts) - epoch BETWEEN -28000 AND -26000;
