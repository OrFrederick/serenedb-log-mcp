"""Build the retrieval corpus: 320 BGL + 48 OpenStack Loghub-2.0 templates with counts and
majority alert_tag / Component / level. LH2 *_full.log is row-aligned with *_structured.csv
(same line counts: 4,631,261 / 207,632), so we zip them line by line."""
import csv, collections, json, sys, time
D = "/Users/deni/hackathon/data/lh2"
OUT = sys.argv[1]
t0 = time.time()
rows = []
for ds, raw, st, tp in [("BGL", f"{D}/BGL/BGL_full.log", f"{D}/BGL/BGL_full.log_structured.csv", f"{D}/BGL/BGL_full.log_templates.csv"),
                        ("OpenStack", f"{D}/OpenStack/OpenStack_full.log", f"{D}/OpenStack/OpenStack_full.log_structured.csv", f"{D}/OpenStack/OpenStack_full.log_templates.csv")]:
    tags = collections.defaultdict(collections.Counter); comps = collections.defaultdict(collections.Counter)
    levels = collections.defaultdict(collections.Counter); n = collections.Counter(); sample = {}
    with open(raw, errors="replace") as fr, open(st, newline="", errors="replace") as fs:
        rd = csv.reader(fs); next(rd)
        for line, rec in zip(fr, rd):
            eid = rec[2]; p = line.split(" ")
            if ds == "BGL":
                tags[eid][p[0]] += 1; comps[eid][p[7] if len(p) > 7 else "?"] += 1; levels[eid][p[8] if len(p) > 8 else "?"] += 1
            else:
                comps[eid][p[5] if len(p) > 5 else "?"] += 1; levels[eid][p[4] if len(p) > 4 else "?"] += 1
            n[eid] += 1
            if eid not in sample: sample[eid] = rec[1]
    with open(tp, newline="") as f:
        for r in csv.DictReader(f):
            e = r["EventId"]; tg = tags[e]
            alert = sum(v for k, v in tg.items() if k != "-")
            rows.append(dict(dataset=ds, event_id=e, template=r["EventTemplate"], occurrences=int(r["Occurrences"]),
                             lines=n[e], component=comps[e].most_common(1)[0][0] if comps[e] else None,
                             components=dict(comps[e].most_common(3)), level=levels[e].most_common(1)[0][0] if levels[e] else None,
                             alert_tag=(max(((k, v) for k, v in tg.items() if k != "-"), key=lambda x: x[1])[0] if alert else "-") if ds == "BGL" else None,
                             alert_frac=round(alert / n[e], 4) if ds == "BGL" and n[e] else None,
                             sample=sample.get(e)))
json.dump(rows, open(OUT, "w"), indent=0)
print(len(rows), "templates", f"{time.time()-t0:.1f}s")
