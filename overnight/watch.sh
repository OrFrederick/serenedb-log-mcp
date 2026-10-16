#!/usr/bin/env bash
# Background watcher. Exits as soon as anything relevant on GitHub changes, so the harness wakes the orchestrator.
# Watches: open PRs (head sha, reviews, comments), open issues labelled research/overnight/ticket (comment counts, labels).
# Usage (run_in_background): overnight/watch.sh [interval_s=90] [max_s=1800]
set -uo pipefail
INTERVAL="${1:-90}"; MAX="${2:-1800}"
snap() {
  {
    gh pr list --state open --limit 100 --json number,headRefOid,reviewDecision,isDraft,author \
      --jq '.[] | "pr \(.number) \(.headRefOid) \(.reviewDecision) \(.isDraft) \(.author.login)"'
    gh pr list --state open --limit 100 --json number,reviews,comments \
      --jq '.[] | "prc \(.number) \(.reviews|length) \(.comments|length)"'
    for l in research overnight ticket follow-up; do
      gh issue list --state all --label "$l" --limit 200 --json number,state,comments,labels,assignees \
        --jq '.[] | "is \(.number) \(.state) \(.comments|length) \([.labels[].name]|sort|join(",")) \([.assignees[].login]|join(","))"'
    done
  } 2>/dev/null | sort | shasum | cut -d' ' -f1
}
start="$(snap)"; elapsed=0
while [ "$elapsed" -lt "$MAX" ]; do
  sleep "$INTERVAL"; elapsed=$((elapsed + INTERVAL))
  now="$(snap)"
  if [ -n "$now" ] && [ "$now" != "$start" ]; then echo "CHANGED after ${elapsed}s"; exit 0; fi
done
echo "TIMEOUT after ${elapsed}s (no change)"
