"""Run Drain3 0.9.11 over a one-message-per-line file.
usage: python -I run_drain3.py INPUT OUTPREFIX --config default|tuned [--dataset bgl|os] [--sim_th X] [--depth N]
       [--shuffle SEED] [--gt GT_TEMPLATE_FILE] [--rematch]
Writes OUTPREFIX.cid.npy (int32 cluster id per input line, in input order), OUTPREFIX.templates.tsv, OUTPREFIX.json (stats)."""
import sys, os, time, json, argparse, re, random, hashlib
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig
from drain3.masking import MaskingInstruction
from metrics import all_metrics, load_lines

# generic masking set, applied in order (Drain3 applies masking_instructions sequentially)
TUNED_MASKS = [
    (r'R\d{2}-M\d(?:-[A-Z0-9]+)*(?::[A-Z0-9]+(?:-[A-Z0-9]+)*)?', 'NODE'),          # BGL location ids
    (r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', 'UUID'),
    (r'req-[0-9a-fA-F-]{36}', 'REQ'),
    (r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?(?:,\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?)*', 'IP'),
    (r'0x[0-9a-fA-F]+', 'NUM'),                                                 # hex and decimal share one mask so 0x../digits at the same position do not split a template
    (r'(?<![A-Za-z0-9])[0-9a-fA-F]{8,}(?![A-Za-z0-9])', 'NUM'),                  # bare hex words (addresses, md5 ids)
    (r'(?<=core\.)\d+', 'NUM'),                                                   # Loghub BGL rule core\.\d+
    (r'(?<=[^A-Za-z0-9])-?\d+(?:\.\d+)?(?=[^A-Za-z0-9]|$)|^-?\d+(?:\.\d+)?(?=[^A-Za-z0-9]|$)', 'NUM'),
]
# per-dataset Drain params from Loghub-2.0 benchmark settings (old_benchmark/Drain_benchmark.py)
LH_PARAMS = {'bgl': dict(sim_th=0.5, depth=4), 'os': dict(sim_th=0.5, depth=5)}
MASK_TOKEN = re.compile(r'<(?:NODE|UUID|REQ|IP|HEX|NUM|\*)>')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('input'); ap.add_argument('out')
    ap.add_argument('--config', default='default'); ap.add_argument('--dataset', default='bgl')
    ap.add_argument('--sim_th', type=float); ap.add_argument('--depth', type=int)
    ap.add_argument('--shuffle', type=int); ap.add_argument('--gt'); ap.add_argument('--rematch', action='store_true')
    a = ap.parse_args()
    cfg = TemplateMinerConfig()
    if a.config == 'tuned':
        cfg.masking_instructions = [MaskingInstruction(p, m) for p, m in TUNED_MASKS]
        cfg.drain_sim_th = LH_PARAMS[a.dataset]['sim_th']; cfg.drain_depth = LH_PARAMS[a.dataset]['depth']
    if a.sim_th is not None: cfg.drain_sim_th = a.sim_th
    if a.depth is not None: cfg.drain_depth = a.depth
    tm = TemplateMiner(config=cfg)
    def stream():
        with open(a.input, encoding='utf-8', errors='surrogateescape') as f:
            for l in f: yield l.rstrip('\n')
    if a.shuffle is not None:   # order-dependence test: same lines, permuted order (needs them in memory)
        msgs = load_lines(a.input); n = len(msgs); order = list(range(n)); random.Random(a.shuffle).shuffle(order)
        cid = np.zeros(n, dtype=np.int32); t0 = time.perf_counter()
        for i in order: cid[i] = tm.add_log_message(msgs[i])['cluster_id']
        del msgs
    else:                       # streaming: memory = Drain3 state + 4 bytes/line
        ids = []; t0 = time.perf_counter()
        for m in stream(): ids.append(tm.add_log_message(m)['cluster_id'])
        cid = np.array(ids, dtype=np.int32); del ids; n = len(cid)
    t_train = time.perf_counter() - t0
    clusters = {c.cluster_id: c.get_template() for c in tm.drain.clusters}
    st = dict(input=a.input, n=n, config=a.config, sim_th=cfg.drain_sim_th, depth=cfg.drain_depth,
              shuffle=a.shuffle, t_parse_s=round(t_train, 2), lines_per_s=round(n / t_train),
              n_clusters=len(clusters), n_distinct_template_strings=len(set(clusters.values())))
    if a.rematch:  # second pass: re-assign each line to its best final cluster (Drain3 match(), inference mode)
        t1 = time.perf_counter(); miss = 0
        cid2 = np.zeros(n, dtype=np.int32)
        for i, m in enumerate(stream()):
            c = tm.match(m, full_search_strategy='fallback')
            if c is None: miss += 1; cid2[i] = cid[i]
            else: cid2[i] = c.cluster_id
        st['t_rematch_s'] = round(time.perf_counter() - t1, 2); st['rematch_none'] = miss
        st['rematch_changed'] = int((cid2 != cid).sum())
        np.save(a.out + '.cid_rematch.npy', cid2)
    np.save(a.out + '.cid.npy', cid)
    with open(a.out + '.templates.tsv', 'w') as w:
        for k in sorted(clusters): w.write(f'{k}\t{clusters[k]}\n')
    st['cid_sha1'] = hashlib.sha1(cid.tobytes()).hexdigest()
    st['templates_sha1'] = hashlib.sha1(open(a.out + '.templates.tsv', 'rb').read()).hexdigest()
    if a.gt:
        gt = load_lines(a.gt)
        tmpl = [MASK_TOKEN.sub('<*>', clusters[c]) for c in cid]           # final template string per line
        st['metrics_by_template_string'] = all_metrics(gt, tmpl, tmpl)
        st['metrics_by_cluster_id'] = all_metrics(gt, cid)
        if a.rematch:
            tmpl2 = [MASK_TOKEN.sub('<*>', clusters[c]) for c in cid2]
            st['metrics_rematch'] = all_metrics(gt, tmpl2, tmpl2)
    print(json.dumps(st, indent=1))
    json.dump(st, open(a.out + '.json', 'w'), indent=1)

if __name__ == '__main__':
    main()
