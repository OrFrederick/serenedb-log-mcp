"""Run Drain3 0.9.11 on Loghub-2.0 Content (BGL, OpenStack) and compute GA + FGA vs Loghub-2.0 labels.
Usage: python -I drain3_bench.py BGL|OpenStack [sim_th] [depth]"""
import sys, csv, time, collections, re
from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig
from drain3.masking import MaskingInstruction
ds = sys.argv[1]; st = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5; depth = int(sys.argv[3]) if len(sys.argv) > 3 else 4
csv.field_size_limit(10**9)
rows = list(csv.reader(open(f"/Users/deni/hackathon/data/lh2/{ds}/{ds}_full.log_structured.csv", newline="", errors="replace")))[1:]
cfg = TemplateMinerConfig(); cfg.drain_sim_th = st; cfg.drain_depth = depth; cfg.profiling_enabled = False
import os
extra = [MaskingInstruction(r"(?<=\s)/[^\s\"]+", "PATH")] if os.environ.get("PATHMASK") else []
cfg.masking_instructions = extra + [MaskingInstruction(r"(((\d+\.){3}\d+(:\d+)?,?)+)", "IP"), MaskingInstruction(r"0x[0-9a-fA-F]+", "HEX"),
                            MaskingInstruction(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "UUID"),
                            MaskingInstruction(r"(?<![A-Za-z])\d+(\.\d+)?(?![A-Za-z])", "NUM")]
tm = TemplateMiner(config=cfg)
t0 = time.time(); ids = [tm.add_log_message(r[1])["cluster_id"] for r in rows]; dt = time.time() - t0
gt = [r[2] for r in rows]; n = len(rows)
# GA: a line is correct iff its predicted cluster has exactly the same line set as its ground-truth group
pred_groups = collections.defaultdict(list); gt_groups = collections.defaultdict(set)
for i, (p, g) in enumerate(zip(ids, gt)): pred_groups[p].append(i); gt_groups[g].add(i)
correct_lines = 0; correct_groups = 0
for p, idx in pred_groups.items():
    g = gt[idx[0]]
    if len(gt_groups[g]) == len(idx) and all(gt[i] == g for i in idx): correct_lines += len(idx); correct_groups += 1
pga = correct_groups / len(pred_groups); rga = correct_groups / len(gt_groups)
fga = 2 * pga * rga / (pga + rga) if pga + rga else 0
print(f"{ds} drain3 st={st} depth={depth} lines={n} time={dt:.1f}s rate={n/dt:,.0f} lines/s clusters={len(pred_groups)} gt_templates={len(gt_groups)} GA={correct_lines/n:.3f} FGA={fga:.3f}")
if len(sys.argv) > 4:  # diagnostics: GT templates whose lines are not correctly grouped
    bad = collections.Counter()
    ok = set()
    for p, idx in pred_groups.items():
        g = gt[idx[0]]
        if len(gt_groups[g]) == len(idx) and all(gt[i] == g for i in idx): ok.add(g)
    tpl = {r[2]: r[3] for r in rows}
    for g, s in sorted(gt_groups.items(), key=lambda x: -len(x[1])):
        if g not in ok:
            preds = collections.Counter(ids[i] for i in s)
            print(f"  WRONG {g} n={len(s)} -> {len(preds)} pred clusters; {tpl[g][:70]} | pred tpl: {tm.drain.id_to_cluster[preds.most_common(1)[0][0]].get_template()[:80]}")
