"""Grade recorded runs (runs/*.json) with the deterministic assertions and print a report.

python grade.py runs/            -> markdown pass-rate tables + per-run failures
python grade.py runs/ --json x   -> also dump raw results
"""
import argparse
import collections
import glob
import json
import statistics
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import assertions as A  # noqa: E402


def golden_values(q):
    return {g["name"]: A.golden_value(g["sql"]) for g in q.get("expect", {}).get("golden", [])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs")
    ap.add_argument("--questions", default=str(HERE / "questions.yaml"))
    ap.add_argument("--json")
    a = ap.parse_args()
    qs = {q["id"]: q for q in yaml.safe_load(open(a.questions))["questions"]}
    runs = [json.load(open(p)) for p in sorted(glob.glob(f"{a.runs}/*.json")) if not p.endswith(".mcp.json")]
    results = []
    for r in runs:
        q = qs[r["id"]]
        res = A.grade(r, q, golden_values(q))
        results.append({"run": r, "checks": res})

    per_check = collections.defaultdict(lambda: [0, 0])
    per_q = collections.defaultdict(list)
    for x in results:
        r = x["run"]
        hard = [c for c in x["checks"] if c.passed is not None]
        allpass = all(c.passed for c in hard)
        per_q[(r["id"], r.get("model") or "default", r["backend"])].append((allpass, r, x["checks"]))
        for c in hard:
            per_check[c.name][0] += c.passed
            per_check[c.name][1] += 1

    print("## Pass rate per assertion\n\n| Assertion | Pass | N | Rate |\n|---|---|---|---|")
    for k, (p, n) in sorted(per_check.items()):
        print(f"| {k} | {p} | {n} | {p / n:.0%} |")

    print("\n## Per question (all hard checks pass)\n\n| Question | Model | Backend | Runs | All-pass | Tool sets across repeats | Calls | $/run (mean) | Wall s (mean) |\n|---|---|---|---|---|---|---|---|---|")
    costs, walls = [], []
    for (qid, model, be), lst in sorted(per_q.items()):
        ok = sum(p for p, _, _ in lst)
        sets = sorted({",".join(sorted({A.short(c["name"]) for c in r["calls"]})) for _, r, _ in lst})
        calls = [len(r["calls"]) for _, r, _ in lst]
        c = [r["cost_usd"] or 0 for _, r, _ in lst]
        w = [r["wall_s"] for _, r, _ in lst]
        costs += c; walls += w
        print(f"| {qid} | {model} | {be} | {len(lst)} | {ok}/{len(lst)} | {' / '.join(sets)} | {calls} | {statistics.mean(c):.4f} | {statistics.mean(w):.1f} |")
    if costs:
        print(f"\nTotal runs {len(costs)}, total ${sum(costs):.4f}, mean ${statistics.mean(costs):.4f}/run, "
              f"median wall {statistics.median(walls):.1f}s, max wall {max(walls):.1f}s")

    # gate = checks we would block a release on; style = strict checks used to tune prompts/tool output
    GATE = {"tools.only_mcp", "tools.required", "tools.any_of", "tools.forbidden", "tools.args", "ledger.one_entry_per_call",
            "numbers.golden_in_answer", "numbers.grounded_or_derived", "structure.sections", "citations.resolve",
            "citations.numbers_in_cited_block", "manager.no_invented_impact", "unknowns.flagged", "answer.forbid_regex",
            "glossary.no_entry_count_as_incidents"}
    models = sorted({k[1] + "/" + k[2] for k in per_q})
    names = sorted(per_check)
    tab = collections.defaultdict(lambda: [0, 0])
    for x in results:
        r = x["run"]; m = (r.get("model") or "default") + "/" + r["backend"]
        for c in x["checks"]:
            if c.passed is not None:
                tab[(c.name, m)][0] += c.passed; tab[(c.name, m)][1] += 1
    print("\n## Pass rate per assertion x model\n\n| Assertion | gate? | " + " | ".join(models) + " |\n|---|---|" + "---|" * len(models))
    for nm in names:
        cells = [f"{tab[(nm, m)][0]}/{tab[(nm, m)][1]}" if tab[(nm, m)][1] else "-" for m in models]
        print(f"| {nm} | {'gate' if nm in GATE else 'style'} | " + " | ".join(cells) + " |")
    print("\n## Gate pass (all gate checks) per question x model, with repeat consistency\n\n| Question | " + " | ".join(models) + " |\n|---|" + "---|" * len(models))
    for qid in sorted({k[0] for k in per_q}):
        cells = []
        for m in models:
            mm, be = m.split("/")
            lst = per_q.get((qid, mm, be), [])
            if not lst:
                cells.append("-"); continue
            g = [all(c.passed for c in chk if c.passed is not None and c.name in GATE) for _, _, chk in lst]
            cells.append(f"{sum(g)}/{len(g)}" + (" (flaky)" if 0 < sum(g) < len(g) else ""))
        print(f"| {qid} | " + " | ".join(cells) + " |")
    for m in models:
        mm, be = m.split("/")
        rs = [x for x in results if (x["run"].get("model") or "default") == mm and x["run"]["backend"] == be]
        g = [all(c.passed for c in x["checks"] if c.passed is not None and c.name in GATE) for x in rs]
        cs = [x["run"]["cost_usd"] or 0 for x in rs]; ws = [x["run"]["wall_s"] for x in rs]
        nc = [len(x["run"]["calls"]) for x in rs]
        print(f"\n- **{m}**: gate pass {sum(g)}/{len(g)} ({sum(g)/len(g):.0%}); ${sum(cs):.4f} total, ${statistics.mean(cs):.4f}/run; "
              f"wall median {statistics.median(ws):.1f}s (max {max(ws):.1f}s); tool calls/run mean {statistics.mean(nc):.1f}")

    print("\n## Failures\n")
    for x in results:
        r = x["run"]
        for c in x["checks"]:
            if c.passed is False:
                print(f"- {r['id']} r{r['repeat']} {r.get('model')} {r['backend']}: **{c.name}** {json.dumps(c.detail, default=str)[:300]}")
    if a.json:
        Path(a.json).write_text(json.dumps([{"id": x["run"]["id"], "repeat": x["run"]["repeat"], "model": x["run"].get("model"),
                                             "backend": x["run"]["backend"], "cost": x["run"]["cost_usd"], "wall": x["run"]["wall_s"],
                                             "checks": [c.__dict__ for c in x["checks"]]} for x in results], indent=1, default=str))


if __name__ == "__main__":
    main()
