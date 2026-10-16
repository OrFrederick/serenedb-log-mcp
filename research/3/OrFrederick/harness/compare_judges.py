"""Agreement between two judges on the same answers (per criterion): raw agreement and Cohen's kappa.

  python compare_judges.py a.judged.jsonl b.judged.jsonl
"""
import json
import sys
from collections import defaultdict

from judge import agreement

a, b = ([json.loads(line) for line in open(p)] for p in sys.argv[1:3])
pairs = defaultdict(lambda: ([], []))
for ra, rb in zip(a, b):
    assert (ra["id"], ra["trial"]) == (rb["id"], rb["trial"]), "files must hold the same trials in the same order"
    for c, va in ra.get("judge", {}).items():
        vb = rb.get("judge", {}).get(c)
        if vb and {va["verdict"], vb["verdict"]} <= {"PASS", "FAIL"}:
            pairs[c][0].append(va["verdict"] == "PASS")
            pairs[c][1].append(vb["verdict"] == "PASS")
print("| Criterion | n | Agreement | Cohen's kappa |\n|---|---|---|---|")
for c, (x, y) in sorted(pairs.items()):
    m = agreement(x, y)
    print(f"| {c} | {m['n']} | {m['accuracy']:.0%} | {m['kappa']:.2f} |")
