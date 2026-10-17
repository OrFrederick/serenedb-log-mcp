"""Offline regrade: re-run grade() over every saved row of a results file using that row's stored
Evidence Ledger. No model calls, no DB connection - every input to grade() (final_text, tool_calls,
ledger, golden) is reconstructed from the row exactly as the original run wrote it; only the grader
code (checks.py, now with the two fixes from #42) is different.

  python regrade.py ../out_baseline_sonnet55_k3.jsonl [--k 3] [--out <path>]

Writes two files next to --out (default: <results>.regraded.jsonl):
  <out>            - one regraded row per line (same shape as the input, with corrected
                      "checks"/"passed")
  <out ending _table.md> - the corrected pass-rate table per Role and the corrected failing-check
                      breakdown, in the same format as run_eval.py's --report
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from evtrace import ToolCall, Trace
from run_eval import grade, report

HERE = Path(__file__).parent


def _trace_from_row(row: dict) -> Trace:
    """Rebuild just enough of a Trace for grade() to run: the saved row already dropped tool
    `result` text (run_eval.py writes calls with result=None "enough to regrade offline"), and no
    check reads ToolCall.result, so nothing is lost."""
    calls = [ToolCall(c["name"], c["args"], c.get("result"), c.get("is_error", False))
             for c in row.get("calls", [])]
    return Trace(row["id"], final_text=row.get("answer") or "", tool_calls=calls,
                 ledger=row.get("ledger") or [], is_error=row.get("is_error", False),
                 error=row.get("error") or "")


def regrade_rows(rows: list[dict], qs: dict[str, dict]) -> list[dict]:
    out = []
    for row in rows:
        q = qs[row["id"]]
        tr = _trace_from_row(row)
        res = grade(q, tr, row.get("golden"))
        out.append({**row, "checks": res, "passed": all(ok for ok, _ in res.values())})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    raw = yaml.safe_load(open(HERE / "questions.yaml"))
    qs = {q["id"]: q for q in (raw if isinstance(raw, list) else raw["questions"])}
    rows = [json.loads(l) for l in open(a.results) if l.strip()]

    regraded = regrade_rows(rows, qs)

    out_path = Path(a.out) if a.out else Path(a.results).with_suffix(".regraded.jsonl")
    out_path.write_text("".join(json.dumps(r, default=str) + "\n" for r in regraded))

    table = report(regraded, a.k)
    table_path = out_path.with_name(out_path.stem + "_table.md")
    before = sum(r["passed"] for r in rows) / len(rows)
    after = sum(r["passed"] for r in regraded) / len(regraded)
    table_path.write_text(
        f"Regrade of {Path(a.results).name} using the #42 grader fixes "
        f"(no model calls; {len(rows)} trials replayed from stored ledgers).\n\n"
        f"Overall trial pass rate: raw {before:.0%} -> corrected {after:.0%}.\n\n" + table + "\n"
    )
    print(table)
    print(f"\nwrote {out_path}")
    print(f"wrote {table_path}")


if __name__ == "__main__":
    main()
