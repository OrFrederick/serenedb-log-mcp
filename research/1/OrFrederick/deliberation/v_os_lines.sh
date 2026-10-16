#!/bin/sh
# Lines per labelled OpenStack instance in the raw file: total vs [instance: uuid]-tagged, and the untagged ones.
R=/Users/frederick/Development/LogSearcher/data/raw
for u in $(grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' $R/anomaly_labels.txt); do
  echo "$u total=$(grep -c $u $R/openstack_abnormal.log) tagged=$(grep -c "\[instance: $u\]" $R/openstack_abnormal.log)"
  grep $u $R/openstack_abnormal.log | grep -v "\[instance: $u\]" | grep -oE 'nova\.[a-z_.]+|"(GET|DELETE) [^ ]+|Creating event [a-z-]+'
done
