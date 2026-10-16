"""Multiset + subsequence diff between raw Zenodo log and Loghub-2.0 *_full.log.
usage: python -I diff_raw_lh2.py RAW LH2 OUTPREFIX
Writes OUTPREFIX_only_raw.txt (sample) and prints stats."""
import sys, hashlib, collections, re
raw, lh2, out = sys.argv[1], sys.argv[2], sys.argv[3]
def h(l): return hashlib.blake2b(l, digest_size=8).digest()
cr = collections.Counter(); cl = collections.Counter()
with open(raw,'rb') as f:
    raw_lines = 0
    for l in f: cr[h(l.rstrip(b'\r\n'))]+=1; raw_lines+=1
with open(lh2,'rb') as f:
    lh_lines=0
    for l in f: cl[h(l.rstrip(b'\r\n'))]+=1; lh_lines+=1
only_raw = cr - cl; only_lh = cl - cr
print('raw lines', raw_lines, 'lh2 lines', lh_lines)
print('multiset raw-lh2 =', sum(only_raw.values()), ' lh2-raw =', sum(only_lh.values()))
# collect actual lines only in raw (consume counters)
rem = dict(only_raw); samples=[]; cat=collections.Counter(); bylabel=collections.Counter(); bymsg=collections.Counter()
dup_of_present = 0
with open(raw,'rb') as f:
    for i,l in enumerate(f,1):
        l=l.rstrip(b'\r\n'); k=h(l)
        if rem.get(k,0)>0:
            rem[k]-=1
            if k in cl: dup_of_present+=1   # line text also exists in lh2 (so removed copy = duplicate)
            parts=l.split(b' ',9)
            bylabel[parts[0]]+=1
            msg=parts[9] if len(parts)>9 else b''
            bymsg[re.sub(rb'\d+',b'#',msg)[:90]]+=1
            cat[(parts[3]==b'NULL', len(parts))]+=1
            if len(samples)<40: samples.append((i,l))
print('only-raw lines whose exact text also appears in lh2 (extra duplicate copies):', dup_of_present)
print('only-raw by (node==NULL, nfields):', cat.most_common(10))
print('only-raw by label:', bylabel.most_common(15))
print('only-raw top msg shapes:'); [print('  ',c,m) for m,c in bymsg.most_common(25)]
with open(out+'_only_raw_samples.txt','wb') as w:
    for i,l in samples: w.write(b'%d\t%s\n'%(i,l))
if only_lh:
    rem=dict(only_lh); s=[]
    with open(lh2,'rb') as f:
        for i,l in enumerate(f,1):
            l=l.rstrip(b'\r\n');k=h(l)
            if rem.get(k,0)>0:
                rem[k]-=1
                if len(s)<20: s.append((i,l))
    print('only-lh2 samples:'); [print('  ',i,l[:220]) for i,l in s]
