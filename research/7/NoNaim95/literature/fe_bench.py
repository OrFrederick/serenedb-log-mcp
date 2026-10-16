import sys, time, csv, os
from fastembed import TextEmbedding
tpl=[r['EventTemplate'] for r in csv.DictReader(open(sys.argv[1]))]
print("templates", len(tpl))
for name in sys.argv[2:]:
    t0=time.time(); m=TextEmbedding(name, cache_dir="/tmp/r7lit/fe/models"); t1=time.time()
    e=list(m.embed(tpl)); t2=time.time()
    qs=["memory problems"]*1; t3=time.time()
    for _ in range(20): list(m.embed(qs))
    t4=time.time()
    print(f"{name}: load+download {t1-t0:.1f}s, embed {len(tpl)} templates {t2-t1:.2f}s, dim {len(e[0])}, single query {(t4-t3)/20*1000:.1f} ms")
