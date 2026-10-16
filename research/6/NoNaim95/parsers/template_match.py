r"""Apply Loghub-2.0 EventTemplates as anchored regex matchers.
usage: python -I template_match.py TEMPLATES_CSV MSG_FILE OUTPREFIX [--gt GT_TEMPLATE_FILE] [--mode strict|loose] [--engine re2|re]
 strict: <*> -> \S+  (one whitespace-free token, like Loghub-2.0's get_pattern_from_template `(\S+?)`)
 loose:  <*> -> .*   (any text incl. spaces, may be empty)
 whitespace runs in the template -> \s+ ; everything else literal; anchored at both ends.
Specificity order: more literal (non-space) characters first, then fewer <*>; ties broken by EventId.
Matching: RE2 Set (all patterns at once, linear time) returns every template matching a line -> ambiguity is exact.
Distinct messages are matched once (dict cache); throughput reported both per distinct message and per line."""
import sys, os, re, time, json, csv, collections, argparse, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from metrics import all_metrics, load_lines
import re2

ap = argparse.ArgumentParser(); ap.add_argument('templates'); ap.add_argument('msgs'); ap.add_argument('out')
ap.add_argument('--gt'); ap.add_argument('--mode', default='strict'); a = ap.parse_args()
T = list(csv.DictReader(open(a.templates, newline='', encoding='utf-8')))
WILDS = {'strict': r'\S+', 'loose': r'.*'}
WILD = WILDS.get(a.mode, r'\S+')

def to_regex(t, WILD=None):
    WILD = WILD or globals()['WILD']
    out = []
    for part in re.split(r'(<\*>|\s+)', t):
        if part == '<*>': out.append(WILD)
        elif part and part.isspace(): out.append(r'\s+')
        elif part: out.append(re2.escape(part))
    return ''.join(out)

def specificity(t):
    lit = re.sub(r'<\*>|\s+', '', t)
    return (len(lit), -t.count('<*>'))

T.sort(key=lambda r: (specificity(r['EventTemplate'])[0] * -1, -specificity(r['EventTemplate'])[1], r['EventId']))
opts = re2.Options(); opts.max_mem = 1 << 30
class Tiered:
    """hybrid: try strict (<*> = one token); only if nothing matches, fall back to loose (<*> = any text)."""
    def __init__(self, wilds):
        self.sets = []
        for wd in wilds:
            st_ = re2.Set.FullMatchSet(opts)          # anchored at both ends
            for r in T: st_.Add(to_regex(r['EventTemplate'], wd))
            st_.Compile(); self.sets.append(st_)
        self.tier_hits = [0] * len(wilds)
    def Match(self, m):
        for k, st_ in enumerate(self.sets):
            ids = st_.Match(m)
            if ids: self.tier_hits[k] += 1; return ids
        return None
S = Tiered([WILDS['strict'], WILDS['loose']] if a.mode == 'hybrid' else [WILD])
with open(a.out + f'.{a.mode}.regex.tsv', 'w') as w:
    w.write('rank\tEventId\tspecificity_literal_chars\tn_wild\tregex\tEventTemplate\n')
    for i, r in enumerate(T):
        w.write(f"{i}\t{r['EventId']}\t{specificity(r['EventTemplate'])[0]}\t{r['EventTemplate'].count('<*>')}\t^{to_regex(r['EventTemplate'])}$\t{r['EventTemplate']}\n")

msgs = load_lines(a.msgs); n = len(msgs)
t0 = time.perf_counter()
cache = {}; chosen = [None] * n; nmatch = [0] * n
for i, m in enumerate(msgs):
    c = cache.get(m)
    if c is None:
        ids = S.Match(m) or []
        c = (min(ids) if ids else -1, len(ids)); cache[m] = c
    chosen[i] = c[0]; nmatch[i] = c[1]
t = time.perf_counter() - t0
tier_hits_main = list(S.tier_hits)
# pure matching speed without cache on a sample of distinct messages
uniq = list(cache)[:200000]; t1 = time.perf_counter()
for m in uniq: S.Match(m)
t_nocache = time.perf_counter() - t1
cnt = collections.Counter(nmatch)
unmatched = [i for i in range(n) if chosen[i] < 0]
st = dict(templates=a.templates, msgs=a.msgs, mode=a.mode, n_lines=n, n_distinct_msgs=len(cache), n_templates=len(T),
          t_match_s=round(t, 2), lines_per_s=round(n / t), distinct_msgs_per_s_nocache=round(len(uniq) / t_nocache),
          matched=n - len(unmatched), coverage=round((n - len(unmatched)) / n, 6), unmatched=len(unmatched),
          ambiguous_lines=sum(v for k, v in cnt.items() if k > 1), match_count_hist={str(k): v for k, v in sorted(cnt.items())},
          templates_used=len(set(chosen) - {-1}), distinct_msgs_matched_per_tier=tier_hits_main)
amb_lines = collections.Counter()
for i in range(n):
    if nmatch[i] > 1: amb_lines[tuple(sorted(S.Match(msgs[i]) or []))] += 1 if len(amb_lines) < 10**6 else 0
st['top_ambiguous_template_sets'] = [([T[j]['EventTemplate'][:100] for j in k], v) for k, v in amb_lines.most_common(8)]
shape = collections.Counter(re.sub(r'\d+', '#', msgs[i])[:120] for i in unmatched)
st['top_unmatched_shapes'] = shape.most_common(25)
if a.gt:
    gt = load_lines(a.gt)
    pred = [T[c]['EventTemplate'] if c >= 0 else '__UNMATCHED__' for c in chosen]
    st['metrics_vs_gt'] = all_metrics(gt, pred, pred)
    tid = {r['EventTemplate']: j for j, r in enumerate(T)}
    gt_in_set = sum(1 for i in range(n) if gt[i] in tid and tid[gt[i]] in ((S.Match(msgs[i]) or []) if nmatch[i] > 1 else ([chosen[i]] if chosen[i] >= 0 else [])))
    st['gt_template_regex_matches_its_line'] = round(gt_in_set / n, 6)
    st['chosen_equals_gt'] = round(sum(1 for i in range(n) if pred[i] == gt[i]) / n, 6)
    wrong = collections.Counter((gt[i][:90], pred[i][:90]) for i in range(n) if pred[i] != gt[i])
    st['top_wrong(gt,chosen)'] = wrong.most_common(10)
with open(a.out + f'.{a.mode}.unmatched_sample.txt', 'w') as w:
    seen = set()
    for i in unmatched:
        k = re.sub(r'\d+', '#', msgs[i])
        if k in seen: continue
        seen.add(k); w.write(f'{i+1}\t{msgs[i]}\n')
        if len(seen) >= 300: break
print(json.dumps(st, indent=1)); json.dump(st, open(a.out + f'.{a.mode}.json', 'w'), indent=1)
