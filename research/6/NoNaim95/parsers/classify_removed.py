"""Classify BGL raw lines that are absent from Loghub-2.0 BGL_full.log (multiset difference).
usage: python -I classify_removed.py RAW_BGL LH2_BGL_FULL"""
import sys, hashlib, collections, re
h = lambda l: hashlib.blake2b(l, digest_size=8).digest()
cl = collections.Counter(h(l.rstrip(b'\r\n')) for l in open(sys.argv[2], 'rb'))
# message shapes present in lh2 (digits -> #, first 40 chars)
lh_shapes = collections.Counter()
for l in open(sys.argv[2], 'rb'):
    p = l.rstrip(b'\r\n').split(b' ', 9); m = p[9] if len(p) > 9 else b''
    lh_shapes[re.sub(rb'[0-9a-fx]+\b', b'#', m)[:40]] += 1
cat = collections.Counter(); shapes = collections.Counter(); n = 0
for l in open(sys.argv[1], 'rb'):
    l = l.rstrip(b'\r\n'); k = h(l)
    if cl.get(k, 0) > 0: cl[k] -= 1; continue
    n += 1
    p = l.split(b' ', 9); m = p[9] if len(p) > 9 else b''
    if not m.strip(): c = 'empty message (9-field line)'
    elif not re.search(rb'[A-Za-z]', m): c = 'no alphabetic character'
    elif re.fullmatch(rb'[0-9a-fA-Fx:\s]+', m): c = 'hex/register dump only (letters are hex digits)'
    else:
        s = re.sub(rb'[0-9a-fx]+\b', b'#', m)[:40]
        c = 'has words; same 40-char shape exists in lh2' if lh_shapes.get(s) else 'has words; shape absent from lh2'
        shapes[(c, re.sub(rb'\d+', b'#', m)[:100])] += 1
    cat[c] += 1
print('removed lines', n)
for c, v in cat.most_common(): print(f'  {v:>7}  {c}')
print('top worded removed shapes:')
for (c, s), v in shapes.most_common(20): print(f'  {v:>6}  [{c[11:]}] {s.decode(errors="replace")}')
