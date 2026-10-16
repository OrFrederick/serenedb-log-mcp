"""Grade eval answers with the LLM judge (default: free RWTHgpt Qwen) and print pass rates per Role x criterion.

  python run_judge.py ../out_baseline_sonnet55_k3.jsonl [--model "rwthgpt:Qwen 3.8 27B"] [--workers 4]

Writes <input>.judged.jsonl next to the input. Judge verdicts are reported, not gated, until calibrated.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

import judge

CRITERIA = ["role_fit", "hypothesis_labelled", "no_invented_impact", "next_step_actionable", "scope_stated"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--model", default="rwthgpt:Qwen 3.8 27B")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    raw = yaml.safe_load(open(Path(__file__).parent / "questions.yaml"))
    qs = {q["id"]: q for q in (raw if isinstance(raw, list) else raw["questions"])}
    rows = [json.loads(line) for line in open(a.results)]
    jobs = [(i, c) for i, r in enumerate(rows) if r.get("answer") for c in CRITERIA]

    def run(job):
        i, c = job
        r = rows[i]
        try:
            v = judge.judge(c, r["role"], qs[r["id"]]["question"], r["answer"], model=a.model)
        except Exception as e:  # noqa: BLE001 - a failed judge call is reported, not fatal
            v = {"verdict": "ERROR", "reason": str(e)[:200]}
        return i, c, v

    with ThreadPoolExecutor(a.workers) as ex:
        for i, c, v in ex.map(run, jobs):
            rows[i].setdefault("judge", {})[c] = v
    out = Path(a.results).with_suffix(".judged.jsonl")
    out.write_text("".join(json.dumps(r) + "\n" for r in rows))

    agg = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r in rows:
        for c, v in r.get("judge", {}).items():
            if v["verdict"] in ("PASS", "FAIL"):
                agg[r["role"]][c][1] += 1
                agg[r["role"]][c][0] += v["verdict"] == "PASS"
    print("| Role | " + " | ".join(CRITERIA) + " |")
    print("|---|" + "---|" * len(CRITERIA))
    for role in sorted(agg):
        cells = [f"{p}/{n}" if n else "-" for p, n in (agg[role][c] for c in CRITERIA)]
        print(f"| {role} | " + " | ".join(cells) + " |")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
