import collections, hashlib
D="/Users/deni/hackathon/data/"
def tags(p): 
    c=collections.Counter()
    for l in open(p,encoding="utf-8",errors="replace"): c[l.split(" ",1)[0]]+=1
    return c
r=tags(D+"raw/BGL.log"); l=tags(D+"lh2/BGL/BGL_full.log")
print("BGL alert tags raw=%d lh2=%d"%(len([t for t in r if t!='-']),len([t for t in l if t!='-'])))
print("tags absent from lh2:", {t:r[t] for t in r if t not in l})
print("tags partially removed:", {t:(r[t],l[t]) for t in l if r[t]!=l[t]})
# OpenStack
lh=[x.rstrip("\n") for x in open(D+"lh2/OpenStack/OpenStack_full.log",encoding="utf-8",errors="replace")]
lhc=collections.Counter(lh)
for f in ["openstack_normal1.log","openstack_normal2.log","openstack_abnormal.log"]:
    raw=[x.rstrip("\n") for x in open(D+"raw/"+f,encoding="utf-8",errors="replace")]
    hit=sum(1 for x in raw if lhc.get(x,0)>0)
    print(f, "lines",len(raw),"present in lh2",hit, "first raw line in lh2 at idx", lh.index(raw[0]) if raw[0] in lhc else None)
rawall=collections.Counter()
for f in ["openstack_normal1.log","openstack_normal2.log","openstack_abnormal.log"]:
    rawall.update(x.rstrip("\n") for x in open(D+"raw/"+f,encoding="utf-8",errors="replace"))
print("lh2 lines not in raw:", sum((lhc-rawall).values()), "raw lines not in lh2:", sum((rawall-lhc).values()))
ex=list((rawall-lhc).elements())[:5]
for e in ex: print("  RAW-ONLY:", e[:200])
ex=list((lhc-rawall).elements())[:3]
for e in ex: print("  LH2-ONLY:", e[:200])
