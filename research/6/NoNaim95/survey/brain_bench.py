"""Run logparser3 Brain on Loghub-2.0 full BGL/OpenStack (settings from logparser Brain/benchmark.py) and score GA/FGA.
Usage: /tmp/lp3venv/bin/python -I brain_bench.py BGL|OpenStack"""
import sys, time, csv, collections
from logparser.Brain import LogParser
ds = sys.argv[1]
S = {"BGL": dict(fmt="<Label> <Timestamp> <Date> <Node> <Time> <NodeRepeat> <Type> <Component> <Level> <Content>", rex=[r"core\.\d+"], th=6),
     "OpenStack": dict(fmt=r"<Logrecord> <Date> <Time> <Pid> <Level> <Component> \[<ADDR>\] <Content>", rex=[r"((\d+\.){3}\d+,?)+", r"/.+?\s ", r"\d+"], th=5)}[ds]
import os; indir = os.environ.get("INDIR", f"/Users/deni/hackathon/data/lh2/{ds}/"); out = f"/tmp/brain_out_{ds}/"
t0 = time.time()
LogParser(logname=ds, log_format=S["fmt"], indir=indir, outdir=out, threshold=S["th"], delimeter=[], rex=S["rex"]).parse(f"{ds}_full.log")
dt = time.time() - t0
csv.field_size_limit(10**9)
pred = [r["EventId"] for r in csv.DictReader(open(out + f"{ds}_full.log_structured.csv", errors="replace"))]
gt = [r[2] for r in list(csv.reader(open(indir + f"{ds}_full.log_structured.csv", errors="replace")))[1:]]
assert len(pred) == len(gt), (len(pred), len(gt))
pg = collections.defaultdict(list); gg = collections.defaultdict(set)
for i, (p, g) in enumerate(zip(pred, gt)): pg[p].append(i); gg[g].add(i)
cl = cg = 0
for p, idx in pg.items():
    g = gt[idx[0]]
    if len(gg[g]) == len(idx) and all(gt[i] == g for i in idx): cl += len(idx); cg += 1
P, R = cg / len(pg), cg / len(gg)
print(f"{ds} Brain(logparser3) lines={len(gt)} time={dt:.1f}s rate={len(gt)/dt:,.0f} lines/s clusters={len(pg)} GA={cl/len(gt):.3f} FGA={2*P*R/(P+R) if P+R else 0:.3f}")
