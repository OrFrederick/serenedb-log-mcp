"""Deliberation baseline (NoNaim95): Claude reads the whole template catalog of the Dataset and picks
the relevant templates itself (the `list_templates` tool idea; no embedding model).
Usage: python r7e_llm_catalog.py <model-id>   -> results/r7e_llm_catalog_<model>.json"""
import json, os, re, subprocess, sys, concurrent.futures as cf
from r7e_common import load, metrics, clean
MODEL = sys.argv[1]
R, L = load()
cat = {ds: "\n".join(f"{r['event_id']} | {r['component']} {r['level']} | {clean(r['template'])}"
                    for r in R if r["dataset"] == ds) for ds in ("BGL", "OpenStack")}
qs = [(split, ds, q) for split, key, ds in (("main","BGL","BGL"),("main","OpenStack","OpenStack"),
      ("holdout","holdout_BGL","BGL"),("holdout","holdout_OpenStack","OpenStack")) for q in L[key]]
env = {k: v for k, v in os.environ.items() if not (k.startswith("CLAUDE_CODE_") or k == "CLAUDECODE")}
def ask(item):
    split, ds, q = item
    prompt = (f"Below is the complete catalog of log message templates of the {ds} dataset "
              f"(EventId | Component Level | template text).\n\n{cat[ds]}\n\n"
              f"An engineer asks: \"{q['text']}\"\nReturn the EventIds of the templates whose Log Entries this engineer "
              f"would want to see, most relevant first, at most 10. Output ONLY a JSON array of EventId strings.")
    out = subprocess.run(["claude", "-p", prompt, "--model", MODEL, "--tools", "", "--setting-sources", "",
                          "--output-format", "json", "--max-turns", "1"], capture_output=True, text=True, env=env,
                         cwd="/Users/deni/.claude/jobs/f51b810a/tmp", timeout=300)
    try:
        res = json.loads(out.stdout); txt = res.get("result", ""); cost = res.get("total_cost_usd", 0)
    except Exception:
        txt, cost = out.stdout + out.stderr, 0
    ids = re.findall(r"E\d+", txt)
    seen = []; [seen.append(i) for i in ids if i not in seen]
    return split, ds, q["id"], q.get("type"), seen[:10], cost
with cf.ThreadPoolExecutor(6) as ex:
    rows = list(ex.map(ask, qs))
summ = {}
for split in ("main", "holdout"):
    ms = [metrics(r[4], next(q for s, d, q in qs if q["id"] == r[2])["relevant"]) for r in rows if r[0] == split]
    summ[split] = {k: round(sum(m[k] for m in ms) / len(ms), 3) for k in ("cR@5", "cR@10", "P@5", "MRR")} | {"n": len(ms)}
summ["cost_usd"] = round(sum(r[5] for r in rows), 4)
json.dump(dict(model=MODEL, summary=summ, runs=[dict(split=r[0], ds=r[1], id=r[2], type=r[3], ranked=r[4]) for r in rows]),
          open(f"results/r7e_llm_catalog_{MODEL}.json", "w"), indent=0)
print(json.dumps(summ))
