#!/bin/sh
# Re-run every r1e_ experiment in order (needs bgl + openstack tables loaded by tools/dev_loader.py).
# Outputs (rows + median-of-3 wall times for SELECTs; DDL runs once) land in outputs/<file>.out.
set -e
cd "$(dirname "$0")"
PY=/Users/deni/hackathon/tools/.venv/bin/python
$PY run.py 01_keys.sql 02_bursts.sql 03_incidents.sql 04_oliner_filter.sql \
  05a_classify_spread_shape.sql 05b_cascade.sql 05c_chronic_new.sql 06_openstack.sql \
  07_sensitivity.sql 08_top_incidents.sql
