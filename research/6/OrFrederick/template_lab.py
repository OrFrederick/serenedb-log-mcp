"""Template-mining lab for issue #6: run candidate parsers on the FULL raw files and
score them against Loghub-2.0 labels (aligned by align_loghub2.py).

Usage:
  python -I template_lab.py <data_dir> <method> <dataset> [--out <dir>]
  method  : mask | drain3 | lh2regex | hybrid | brain
  dataset : bgl | openstack
Prints one JSON line with throughput, #templates, coverage and accuracy, and writes
<out>/<dataset>_<method>.tsv (line_no \t template) for the SereneDB load step.
"""
import collections
import hashlib
import json
import re
import sys
import time
from pathlib import Path

DATA = Path(sys.argv[1])
METHOD = sys.argv[2]
DS = sys.argv[3]
OUT = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else DATA / "r6"

# ---------------------------------------------------------------- loading
OS_RE = re.compile(r"^(\S+) (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+) (\d+) (\w+) (\S+) (?:\[([^\]]*)\] )?(.*)$")


def load(ds):
    """Return (contents, gt) using the same field split as data/dev_loader.py."""
    contents = []
    if ds == "bgl":
        with open(DATA / "raw/BGL.log", encoding="utf-8", errors="replace") as f:
            for line in f:
                p = line.rstrip("\n").split(" ", 9)
                contents.append(p[9] if len(p) == 10 else "")
        gtf = DATA / "r6/bgl_gt.tsv"
    else:
        for fn in ["openstack_normal1.log", "openstack_normal2.log", "openstack_abnormal.log"]:
            with open(DATA / "raw" / fn, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.rstrip("\n")
                    m = OS_RE.match(line)
                    contents.append(m.group(7) if m else line)
        gtf = DATA / "r6/openstack_gt.tsv"
    gt = [l.rstrip("\n").split("\t")[1] for l in open(gtf)]
    assert len(gt) == len(contents)
    return contents, gt


# ---------------------------------------------------------------- generic masking
# Ordered, dataset-agnostic rules (pattern, replacement). Written in the common subset of
# Python `re` (ASCII mode) and RE2 (SereneDB's regex engine): no look-around, no
# back-references in patterns, \1 in replacements. The SAME list generates the SQL in
# sql_mask_expr(), so Python and SereneDB produce identical templates.
MASK_RULES = [
    (r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", "<UUID>"),
    (r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b", "<IP>"),
    (r"(^|[^\w.])\.{0,2}(?:/[^\s,;:()\[\]\"']+)+", r"\1<PATH>"),
    (r"\b0x[0-9a-fA-F]+\b", "<HEX>"),
    (r"\b[0-9a-fA-F]{8,}\b", "<HEX>"),
    (r"(\.{3,})", r"\1 "),  # split dot leaders ("depth......0") so the key word survives
    (r"[\w:\-.]*\d[\w:\-.]*", "<NUM>"),  # any remaining token containing a digit
    # collapse comma-separated lists of placeholders (e.g. X-Forwarded-For IP lists)
    (r"<(UUID|IP|PATH|HEX|NUM)>(?:,\s*<(?:UUID|IP|PATH|HEX|NUM)>)+", r"<\1>"),
    (r"\s+", " "),
]
MASKS = [(re.compile(p, re.ASCII), r) for p, r in MASK_RULES]
WS = re.compile(r"\s+")


def mask(s):
    for rx, rep in MASKS:
        s = rx.sub(rep, s)
    return s.strip()


def sql_mask_expr(col="content"):
    """The same masking as a SereneDB SQL expression (nested regexp_replace, 'g' flag)."""
    e = col
    for p, r in MASK_RULES:
        e = f"regexp_replace({e}, '{p.replace(chr(39), chr(39) * 2)}', '{r}', 'g')"
    return f"trim({e})"


def to_star(t):
    """Normalise any typed placeholder to Loghub's <*> for PA/FTA comparison."""
    return WS.sub(" ", re.sub(r"<[A-Z]+>|<\*>", "<*>", t)).strip()


# ---------------------------------------------------------------- Loghub-2.0 templates as regex
def lh2_matchers(ds):
    import csv
    name = "BGL/BGL_full" if ds == "bgl" else "OpenStack/OpenStack_full"
    rows = list(csv.DictReader(open(DATA / f"loghub2/{name}.log_templates.csv", encoding="utf-8")))
    by_first = collections.defaultdict(list)
    wild = []
    for r in rows:
        t = r["EventTemplate"]
        parts = t.split("<*>")
        pat = "^" + "(.*?)".join(re.escape(p) for p in parts) + "$"
        pat = pat.replace(r"\ ", r"\s+")
        rx = re.compile(pat, re.S)
        lit = len(t.replace("<*>", ""))
        first = t.split(" ", 1)[0]
        (wild if "<*>" in first else by_first[first]).append((lit, r["EventId"], t, rx))
    for k in by_first:
        by_first[k].sort(key=lambda x: -x[0])
    wild.sort(key=lambda x: -x[0])

    def match(s):
        first = s.split(" ", 1)[0]
        for _, eid, t, rx in by_first.get(first, ()):
            if rx.match(s):
                return eid, t
        for _, eid, t, rx in wild:
            if rx.match(s):
                return eid, t
        return None, None

    return match, len(rows)


# ---------------------------------------------------------------- metrics
def score(pred, gt, pred_tpl=None, gt_tpl=None):
    """GA, FGA (Loghub-2.0 definitions) over lines that have a Loghub-2.0 label.
    PA/FTA computed after normalising placeholders to <*> (approximate; our
    templates use different placeholder conventions)."""
    idx = [i for i, g in enumerate(gt) if g]
    n = len(idx)
    pair = collections.Counter((pred[i], gt[i]) for i in idx)
    pc = collections.Counter(pred[i] for i in idx)
    gc = collections.Counter(gt[i] for i in idx)
    correct_groups = [(p, g) for (p, g), c in pair.items() if pc[p] == c and gc[g] == c]
    ga = sum(pair[x] for x in correct_groups) / n
    prec = len(correct_groups) / len(pc)
    rec = len(correct_groups) / len(gc)
    fga = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    res = {"GA": round(ga, 4), "FGA": round(fga, 4), "pred_groups_on_labelled": len(pc),
           "gt_groups": len(gc)}
    if pred_tpl is not None:
        pa = sum(1 for i in idx if to_star(pred_tpl[i]) == to_star(gt_tpl[gt[i]])) / n
        # FTA: group correct AND template text correct
        pred_tpl_of = {pred[i]: pred_tpl[i] for i in idx}
        tp = sum(1 for p, g in correct_groups if to_star(pred_tpl_of[p]) == to_star(gt_tpl[g]))
        fp_, fr_ = tp / len(pc), tp / len(gc)
        res["PA_norm"] = round(pa, 4)
        res["FTA_norm"] = round(2 * fp_ * fr_ / (fp_ + fr_) if tp else 0.0, 4)
    return res


def gt_templates(ds):
    import csv
    name = "BGL/BGL_full" if ds == "bgl" else "OpenStack/OpenStack_full"
    return {r["EventId"]: r["EventTemplate"] for r in
            csv.DictReader(open(DATA / f"loghub2/{name}.log_templates.csv", encoding="utf-8"))}


def tid(t):
    """Template id = hash of the template with every placeholder normalised to <*>,
    so <HEX>/<NUM>/<PATH> appearing in the same slot do not split a group."""
    return hashlib.md5(to_star(t).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- methods
def run(method, contents):
    """Return list of template strings (None = unmatched)."""
    if method == "mask":
        return [mask(s) for s in contents]
    if method == "lh2regex":
        match, _ = lh2_matchers(DS)
        return [match(s)[1] for s in contents]
    if method == "hybrid":
        match, _ = lh2_matchers(DS)
        out = []
        for s in contents:
            t = match(s)[1]
            out.append(t if t is not None else "~" + mask(s))  # '~' marks fallback templates
        return out
    if method == "drain3":
        from drain3 import TemplateMiner
        from drain3.masking import MaskingInstruction
        from drain3.template_miner_config import TemplateMinerConfig
        cfg = TemplateMinerConfig()
        cfg.profiling_enabled = False
        cfg.masking_instructions = [MaskingInstruction(rx.pattern, rep.strip("<>"))
                                    for rx, rep in MASKS if rep.startswith("<")]
        cfg.mask_prefix, cfg.mask_suffix = "<", ">"
        tm = TemplateMiner(config=cfg)
        cids = [tm.add_log_message(s)["cluster_id"] for s in contents]
        # final templates (clusters evolve; read the final state, like batch Drain)
        final = {c.cluster_id: c.get_template() for c in tm.drain.clusters}
        return [final[c] for c in cids]
    if method == "brain":
        sys.path.insert(0, str(Path(__file__).parent))
        from brain_adapter import brain_parse
        return brain_parse(contents, DS)
    raise SystemExit(f"unknown method {method}")


def main():
    t0 = time.perf_counter()
    contents, gt = load(DS)
    t_load = time.perf_counter() - t0
    t1 = time.perf_counter()
    tpls = run(METHOD, contents)
    t_parse = time.perf_counter() - t1
    n = len(contents)
    matched = [t is not None for t in tpls]
    pred = [tid(t) if t is not None else f"__unmatched_{i}" for i, t in enumerate(tpls)]
    gtt = gt_templates(DS)
    res = {"dataset": DS, "method": METHOD, "lines": n, "load_s": round(t_load, 1),
           "parse_s": round(t_parse, 1), "lines_per_s": int(n / t_parse),
           "templates": len({tid(t) for t in tpls if t is not None}),
           "coverage": round(sum(matched) / n, 5),
           "unlabelled_lines(not in Loghub-2.0)": sum(1 for g in gt if not g)}
    if METHOD == "hybrid":
        fb = [t for t in tpls if t.startswith("~")]
        res["fallback_lines"] = len(fb)
        res["fallback_templates"] = len(set(fb))
    if METHOD in ("lh2regex",):
        lab = [i for i, g in enumerate(gt) if g]
        res["coverage_on_labelled"] = round(sum(matched[i] for i in lab) / len(lab), 5)
        unl = [i for i, g in enumerate(gt) if not g]
        res["coverage_on_unlabelled"] = round(sum(matched[i] for i in unl) / max(1, len(unl)), 5)
    t2 = time.perf_counter()
    res.update(score(pred, gt, [t or "" for t in tpls], gtt))
    res["score_s"] = round(time.perf_counter() - t2, 1)
    OUT.mkdir(parents=True, exist_ok=True)
    line_offset = 0
    with open(OUT / f"{DS}_{METHOD}.tsv", "w") as f:
        for i, t in enumerate(tpls):
            f.write(f"{i + 1 + line_offset}\t{tid(t) if t is not None else ''}\t"
                    f"{(t or '').replace(chr(9), ' ')}\n")
    print(json.dumps(res))


if __name__ == "__main__":
    main()
