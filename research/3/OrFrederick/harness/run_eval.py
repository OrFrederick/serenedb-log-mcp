"""Eval runner: questions x k trials -> results.jsonl + README pass-rate table.

  python run_eval.py --k 3 --model sonnet [--ids eng-01,adv-01] [--driver claude_p|agent_sdk]
  python run_eval.py --report results.jsonl     # re-render the table only

Each trial is isolated (fresh temp dir, fresh Evidence Ledger, fresh MCP server process).
A question passes a trial when every deterministic check passes (judge scores are reported
separately and gate only when --judge-gates is given).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import asdict
from math import comb
from pathlib import Path

import psycopg
import yaml

import checks as C
from evtrace import run_claude_p

HERE = Path(__file__).parent
DSN = os.environ.get("DATABASE_URL", "host=localhost port=7890 dbname=postgres user=postgres password=postgres")
SERVER = [sys.executable, str(HERE.parent / "stub_server.py")]
ROLE_PROMPT = (HERE / "role_prompt.txt").read_text() if (HERE / "role_prompt.txt").exists() else None


def grade(q: dict, tr, golden_value) -> dict:
    res = {}
    if tr.is_error:
        return {"run_error": (False, tr.error[:200])}
    res["tools"] = C.check_tools(tr, q.get("must_call", ()), q.get("must_not_call", ()), q.get("order", ()),
                                 q.get("args"), q.get("max_calls"))
    res["sections"] = C.check_sections(tr)
    res["no_incident_counts"] = C.check_no_incident_counts(tr)  # glossary guard on every answer
    if tr.tool_calls:
        res["citations"] = C.check_citations_resolve(tr)
        res["numbers"] = C.check_numbers_grounded(tr)
    if golden_value is not None:
        res["golden"] = C.check_golden(tr, [golden_value])
    if q["role"] == "Manager":
        res["no_invented_impact"] = C.check_no_invented_impact(tr)
    for chk in q.get("checks", []):
        if chk == "no_invented_impact":
            res[chk] = C.check_no_invented_impact(tr)
        elif chk == "no_incident_counts":
            pass
        elif "mentions" in chk:
            res[f"mentions:{chk['mentions'][:20]}"] = C.check_mentions(tr, chk["mentions"])
        elif "states_unknown" in chk:
            res["states_unknown"] = C.check_states_unknown(tr, chk["states_unknown"])
    return res


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k (Chen et al. 2021): P(at least one of k sampled trials passes)."""
    return 1.0 if n - c < k else 1 - comb(n - c, k) / comb(n, k)


def pass_hat_k(n: int, c: int, k: int) -> float:
    """pass^k (tau-bench, Yao et al. 2024): P(all k sampled trials pass) = C(c,k)/C(n,k)."""
    return comb(c, k) / comb(n, k) if c >= k else 0.0


def report(rows: list[dict], k: int) -> str:
    by_q = defaultdict(list)
    for r in rows:
        by_q[(r["role"], r["id"])].append(r)
    by_role = defaultdict(lambda: [0, 0, [], [], 0.0, []])
    lines = ["| Role | Questions | Trials | Trial pass rate | pass@%d | pass^%d | Tool-choice pass | Mean cost/trial | Mean calls |" % (k, k),
             "|---|---|---|---|---|---|---|---|---|"]
    for (role, _), trials in sorted(by_q.items()):
        n, c = len(trials), sum(t["passed"] for t in trials)
        agg = by_role[role]
        agg[0] += n; agg[1] += c
        agg[2].append(pass_at_k(n, c, min(k, n))); agg[3].append(pass_hat_k(n, c, min(k, n)))
        agg[4] += sum(t["cost_usd"] for t in trials)
        agg[5] += [(t["checks"].get("tools", [False])[0], len(t["calls"])) for t in trials]
    for role, (n, c, pa, ph, cost, tc) in by_role.items():
        lines.append(f"| {role} | {len(pa)} | {n} | {c/n:.0%} | {sum(pa)/len(pa):.0%} | {sum(ph)/len(ph):.0%} | "
                     f"{sum(x for x, _ in tc)/len(tc):.0%} | ${cost/n:.3f} | {sum(y for _, y in tc)/len(tc):.1f} |")
    fails = defaultdict(int)
    for r in rows:
        for name, (ok, _) in r["checks"].items():
            fails[name] += not ok
    lines.append("\nFailing checks (trial count): " + ", ".join(f"{k}={v}" for k, v in sorted(fails.items(), key=lambda x: -x[1]) if v))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--ids", default="")
    ap.add_argument("--out", default="results.jsonl")
    ap.add_argument("--report", default="")
    a = ap.parse_args()
    if a.report:
        rows = [json.loads(l) for l in open(a.report)]
        print(report(rows, a.k)); return
    qs = yaml.safe_load(open(HERE / "questions.yaml"))
    if a.ids:
        qs = [q for q in qs if q["id"] in a.ids.split(",")]
    db = psycopg.connect(DSN, autocommit=True)
    rows = []
    with open(a.out, "w") as out:
        for q in qs:
            golden = db.execute(q["golden_sql"]).fetchone()[0] if q.get("golden_sql") else None
            for trial in range(a.k):
                tr = run_claude_p(q["id"], f"[Role: {q['role']}] {q['question']}", server_cmd=SERVER,
                                  system_prompt=ROLE_PROMPT, model=a.model)
                res = grade(q, tr, golden)
                row = {"id": q["id"], "role": q["role"], "trial": trial, "passed": all(ok for ok, _ in res.values()),
                       "checks": res, "calls": [asdict(c) | {"result": None} for c in tr.tool_calls],
                       "answer": tr.final_text, "cost_usd": tr.cost_usd, "duration_ms": tr.duration_ms,
                       "num_turns": tr.num_turns, "model": tr.model, "golden": golden,
                       "ledger": tr.ledger, "is_error": tr.is_error, "error": tr.error}  # enough to regrade offline
                rows.append(row); out.write(json.dumps(row, default=str) + "\n"); out.flush()
                print(q["id"], trial, "PASS" if row["passed"] else "FAIL",
                      {k: v[1] for k, v in res.items() if not v[0]}, f"${tr.cost_usd:.3f}", flush=True)
    print(report(rows, a.k))


if __name__ == "__main__":
    main()
