SELECT CASE WHEN nodes = 1 AND lines >= 100 THEN 'single-node storm'
            WHEN nodes = 1 THEN 'single-node isolated'
            WHEN midplanes >= 64 THEN 'machine-wide (>=64 of 128 midplanes)'
            WHEN midplanes > 1 THEN 'multi-midplane'
            ELSE 'one midplane, several nodes' END AS shape,
       count(*) incidents, sum(lines) lines, count(DISTINCT alert_label) categories
FROM r1_bgl_incidents GROUP BY 1 ORDER BY lines DESC
