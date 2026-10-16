import json, glob, collections, sys
for m in ("haiku", "sonnet"):
    seqs, costs, durs = [], [], []
    for f in sorted(glob.glob(f"var/{m}_*.jsonl")):
        calls = []; r = {}
        for line in open(f):
            x = json.loads(line)
            if x.get("type") == "assistant":
                calls += [b["name"].split("__")[-1] + ("(" + ",".join(f"{k}={v}" for k, v in b["input"].items() if k in ("group_by","bucket","query")) + ")") for b in x["message"]["content"] if b.get("type") == "tool_use"]
            if x.get("type") == "result": r = x
        seqs.append(calls); costs.append(r.get("total_cost_usd") or 0); durs.append(r.get("duration_ms") or 0)
        print(m, f.split("/")[-1], len(calls), " -> ".join(calls))
    first = collections.Counter(s[0].split("(")[0] if s else None for s in seqs)
    used_both = sum(1 for s in seqs if any(c.startswith("count_by(group_by=component") for c in s) and any(c.startswith("trend") for c in s))
    print(f"  {m}: mean calls {sum(map(len,seqs))/len(seqs):.1f}, distinct sequences {len(set(map(tuple,seqs)))}/{len(seqs)}, first tool {dict(first)}, count_by(component)+trend in {used_both}/{len(seqs)}, mean cost ${sum(costs)/len(costs):.4f}, mean dur {sum(durs)/len(durs)/1000:.1f}s")
