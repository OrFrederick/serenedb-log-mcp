"""Deliberation #7 (NoNaim95): run OrFrederick's exact catalogue-selection prompt (llm_select.py) with Claude
instead of gpt-oss-120b, on OrFrederick's independently written labels (of_labels.yaml, branch research/7-OrFrederick).
Metrics = OrFrederick's eval_retrieval.metrics (uncapped R@5/R@10, P@5, MRR); no_relevant queries reported separately."""
import json, os, re, subprocess, sys, statistics as st, concurrent.futures as cf, yaml
MODEL = sys.argv[1]
T = json.load(open("of_templates.json")); L = yaml.safe_load(open("of_labels.yaml"))
env = {k: v for k, v in os.environ.items() if not (k.startswith("CLAUDE_CODE_") or k == "CLAUDECODE")}
def metrics(ranked, rel):
    rel = set(rel); r5 = len(set(ranked[:5]) & rel) / len(rel); r10 = len(set(ranked[:10]) & rel) / len(rel)
    p5 = len(set(ranked[:5]) & rel) / 5; mrr = next((1 / (i + 1) for i, d in enumerate(ranked) if d in rel), 0)
    return dict(R5=r5, R10=r10, P5=p5, MRR=mrr)
items = [(ds, it) for ds in ("bgl", "openstack") for it in L[ds]]
def ask(x):
    ds, item = x
    cat = "\n".join(f"{t['id']} [{t['component']}] {t['template']}" for t in T[ds])
    msg = (f"Here is the complete list of log message templates of the {ds.upper()} log (id [component] template):\n{cat}\n\n"
           f"Question from an engineer: \"{item['q']}\"\nReturn the ids of up to 10 templates whose log lines are direct evidence for the question, "
           "most relevant first, as a JSON array of strings. Return [] if none is relevant. Output only the JSON array.")
    o = subprocess.run(["claude", "-p", msg, "--model", MODEL, "--tools", "", "--setting-sources", "", "--output-format", "json",
                        "--max-turns", "1"], capture_output=True, text=True, env=env, cwd="/Users/deni/.claude/jobs/f51b810a/tmp", timeout=300)
    try: res = json.loads(o.stdout); txt = res.get("result", ""); cost = res.get("total_cost_usd", 0)
    except Exception: txt, cost = o.stdout + o.stderr, 0
    ids = []; [ids.append(i) for i in re.findall(r"E\d+", txt) if i not in ids]
    return ds, item, ids[:10], cost
with cf.ThreadPoolExecutor(6) as ex: rows = list(ex.map(ask, items))
scored = [(ds, it, metrics(ids, it["rel"])) for ds, it, ids, c in rows if not it.get("no_relevant")]
agg = lambda l: {k: round(st.mean(m[k] for _, _, m in l), 3) for k in ("R5", "R10", "P5", "MRR")}
out = dict(model=MODEL, all=agg(scored), paraphrase=agg([s for s in scored if s[1].get("kind") == "paraphrase"]),
           n=len(scored), no_relevant={f"{ds}::{it['q']}": ids for ds, it, ids, c in rows if it.get("no_relevant")},
           cost_usd=round(sum(r[3] for r in rows), 4), runs=[dict(ds=ds, q=it["q"], ranked=ids) for ds, it, ids, c in rows])
json.dump(out, open(f"cross_eval_{MODEL}.json", "w"), indent=0); print(json.dumps({k: out[k] for k in ("model", "all", "paraphrase", "n", "no_relevant", "cost_usd")}))
