"""Build template metadata (component, level, alert label distribution) from Loghub-2.0 files.
Line i of *_full.log corresponds to LineId i in *_structured.csv."""
import csv, json, collections, sys
LH = "/Users/frederick/Development/LogSearcher/data/loghub2"
out = {}
# BGL
tpl = {r["EventId"]: r for r in csv.DictReader(open(f"{LH}/BGL/BGL_full.log_templates.csv"))}
comp = collections.defaultdict(collections.Counter); lab = collections.defaultdict(collections.Counter); lev = collections.defaultdict(collections.Counter)
sample = {}
with open(f"{LH}/BGL/BGL_full.log", errors="replace") as raw, open(f"{LH}/BGL/BGL_full.log_structured.csv", newline="") as st:
    rd = csv.reader(st); next(rd)
    for line, row in zip(raw, rd):
        p = line.split(" ", 9)
        eid = row[2]
        comp[eid][p[7]] += 1; lev[eid][p[8]] += 1; lab[eid][p[0]] += 1
        sample.setdefault(eid, row[1][:200])
bgl = []
for eid, r in tpl.items():
    bgl.append(dict(id=eid, template=r["EventTemplate"], occurrences=int(r["Occurrences"]),
        component=comp[eid].most_common(1)[0][0] if comp[eid] else None,
        level=lev[eid].most_common(1)[0][0] if lev[eid] else None,
        alert_label=lab[eid].most_common(1)[0][0] if lab[eid] else None,
        alert_labels=dict(lab[eid].most_common(5)), sample=sample.get(eid)))
# OpenStack
tpl = {r["EventId"]: r for r in csv.DictReader(open(f"{LH}/OpenStack/OpenStack_full.log_templates.csv"))}
comp = collections.defaultdict(collections.Counter); lev = collections.defaultdict(collections.Counter)
with open(f"{LH}/OpenStack/OpenStack_full.log", errors="replace") as raw, open(f"{LH}/OpenStack/OpenStack_full.log_structured.csv", newline="") as st:
    rd = csv.reader(st); next(rd)
    for line, row in zip(raw, rd):
        p = line.split(" ", 6)
        comp[row[2]][p[5]] += 1; lev[row[2]][p[4]] += 1
osk = [dict(id=eid, template=r["EventTemplate"], occurrences=int(r["Occurrences"]),
        component=comp[eid].most_common(1)[0][0], level=lev[eid].most_common(1)[0][0]) for eid, r in tpl.items()]
json.dump({"bgl": bgl, "openstack": osk}, open("templates.json", "w"), indent=0)
print(len(bgl), len(osk))
