"""Relate Loghub-2.0 BGL templates to Oliner&Stearley alert tags (col 1 of BGL_full.log),
and quantify how Loghub-2.0 BGL_full.log differs from the raw Zenodo 8196385 BGL.log."""
import csv, sys, time, hashlib, collections
LH2 = "/Users/deni/hackathon/data/lh2/BGL/"
RAW = "/Users/deni/hackathon/data/raw/BGL.log"
t0 = time.time()
csv.field_size_limit(10**9)
pair = collections.Counter(); tag_tot = collections.Counter(); tpl_tot = collections.Counter(); tpl_text = {}
with open(LH2+"BGL_full.log", encoding="utf-8", errors="replace") as fl, open(LH2+"BGL_full.log_structured.csv", newline="", encoding="utf-8", errors="replace") as fs:
    rd = csv.reader(fs); next(rd)
    lh2_hashes = collections.Counter()
    for line, row in zip(fl, rd):
        line = line.rstrip("\n")
        lh2_hashes[hashlib.md5(line.encode()).digest()] += 1
        tag = line.split(" ", 1)[0]
        eid = row[2]; tpl_text[eid] = row[3]
        pair[(eid, tag)] += 1; tag_tot[tag] += 1; tpl_tot[eid] += 1
print(f"lh2 lines={sum(tpl_tot.values())} templates={len(tpl_tot)} tags={len(tag_tot)} t={time.time()-t0:.1f}s")
alert_tags = [t for t in tag_tot if t != "-"]
print("alert lines", sum(tag_tot[t] for t in alert_tags), "distinct alert tags", len(alert_tags))
# per alert tag: how many templates, and purity
print("\nTAG | lines | #templates | top template share | top template")
for tag in sorted(alert_tags, key=lambda t: -tag_tot[t]):
    tp = [(e, c) for (e, t), c in pair.items() if t == tag]; tp.sort(key=lambda x: -x[1])
    print(f"{tag} | {tag_tot[tag]} | {len(tp)} | {tp[0][1]/tag_tot[tag]:.3f} | {tp[0][0]} {tpl_text[tp[0][0]][:70]}")
# per template: mixed templates (both alert and non-alert or multiple alert tags)
mixed = []
for e in tpl_tot:
    tags = {t: c for (ee, t), c in pair.items() if ee == e}
    if len(tags) > 1: mixed.append((e, tags))
print(f"\ntemplates with >1 tag value (incl '-'): {len(mixed)} of {len(tpl_tot)}")
for e, tags in sorted(mixed, key=lambda x: -tpl_tot[x[0]])[:15]:
    print(e, tpl_text[e][:60], dict(sorted(tags.items(), key=lambda x: -x[1])[:4]))
alert_only = [e for e in tpl_tot if all(t != "-" for (ee, t) in pair if ee == e)]
print("templates whose lines are all alert-tagged:", len(alert_only))
# raw vs lh2
t1 = time.time(); raw_n = 0; matched = 0; rem = lh2_hashes.copy(); unmatched_examples = []
with open(RAW, encoding="utf-8", errors="replace") as f:
    for line in f:
        raw_n += 1; h = hashlib.md5(line.rstrip("\n").encode()).digest()
        if rem.get(h, 0) > 0: rem[h] -= 1; matched += 1
        elif len(unmatched_examples) < 8: unmatched_examples.append(line.rstrip()[:160])
print(f"\nraw lines={raw_n} raw lines found in lh2 (multiset)={matched} raw-only={raw_n-matched} lh2-only={sum(v for v in rem.values() if v>0)} t={time.time()-t1:.1f}s")
for u in unmatched_examples: print("  RAW-ONLY:", u)
