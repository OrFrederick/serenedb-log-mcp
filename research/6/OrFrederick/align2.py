"""Align Loghub-2.0 annotations with the raw Zenodo 8196385 corpus (streaming, low memory).

Replaces align_loghub2.py, which held both corpora in RAM (~4.5 GB; this host has 8 GB).

BGL: Loghub-2.0 is expected to be an in-order subsequence of the raw file, so a
two-pointer walk aligns them in O(1) memory. Consecutive misses are counted; a long
run of misses means the hypothesis is wrong and is reported as a desync.
OpenStack: Loghub-2.0 concatenates the three raw files in a different order, so the
alignment is an order-agnostic multiset match on the full line (207k lines, cheap).

Usage: python -I align2.py <data_dir> <out_dir>
Writes <out_dir>/{bgl,openstack}_gt.tsv   (raw line_no \t Loghub-2.0 EventId or empty)
and   <out_dir>/alignment_report.txt
"""
import collections
import csv
import re
import sys
from pathlib import Path

csv.field_size_limit(10 ** 9)
DATA = Path(sys.argv[1])
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
REP = open(OUT / "alignment_report.txt", "w")


def log(*a):
    print(*a, flush=True)
    print(*a, file=REP, flush=True)


def lh_pairs(logp, structp):
    """Yield (full_line, event_id) from a Loghub-2.0 .log + _structured.csv pair."""
    with open(logp, encoding="utf-8", errors="replace") as lf, \
            open(structp, newline="", encoding="utf-8", errors="replace") as sf:
        r = csv.reader(sf)
        header = next(r)
        assert header[:4] == ["LineId", "Content", "EventId", "EventTemplate"], header
        for line, row in zip(lf, r):
            yield line.rstrip("\n"), sys.intern(row[2])


BGL_RE = re.compile(r"^(\S+) (\d+) (\d{4}\.\d\d\.\d\d) (\S+) (\S+) (\S+) (\S+) (\S+) (\S+) ?(.*)$")
LEVELS = {"INFO", "WARNING", "SEVERE", "ERROR", "FATAL", "FAILURE"}


def classify_bgl(line):
    m = BGL_RE.match(line)
    if not m:
        return "malformed (fewer than 9 header fields)"
    if m.group(9) not in LEVELS:
        return f"non-standard level field ({m.group(9)[:14]})"
    if not m.group(10).strip():
        return "empty content"
    if any(ord(c) > 126 or (ord(c) < 32 and c != "\t") for c in line):
        return "non-ASCII / control characters"
    return "well-formed (other reason)"


def classify_os(line):
    if not line.strip():
        return "empty line"
    p = line.split(" ", 6)
    if len(p) < 7:
        return "malformed (fewer than 7 header fields)"
    if any(ord(c) > 126 for c in line):
        return "non-ASCII"
    return f"well-formed (other reason; level={p[3]}, comp={p[4]})"


def report_dropped(name, kinds, samples):
    log(f"  reasons raw lines are absent from Loghub-2.0 ({name}):")
    for k, c in kinds.most_common():
        log(f"    {k}: {c:,}")
        for ln, s in samples[k]:
            log(f"        line {ln}: {s[:170]!r}")


def align_subsequence(name, raw_paths, logp, structp, gtp, classify):
    """Two-pointer walk; assumes Loghub-2.0 is an in-order subsequence of raw."""
    lh = lh_pairs(logp, structp)
    cur = next(lh, None)
    matched = dropped = n_raw = lh_n = 0
    run = max_run = 0
    kinds, samples = collections.Counter(), collections.defaultdict(list)
    with open(gtp, "w") as gf:
        for p in raw_paths:
            with open(p, encoding="utf-8", errors="replace") as rf:
                for line in rf:
                    n_raw += 1
                    line = line.rstrip("\n")
                    if cur is not None and line == cur[0]:
                        gf.write(f"{n_raw}\t{cur[1]}\n")
                        matched += 1
                        lh_n += 1
                        cur = next(lh, None)
                        run = 0
                    else:
                        gf.write(f"{n_raw}\t\n")
                        dropped += 1
                        run += 1
                        max_run = max(max_run, run)
                        k = classify(line)
                        kinds[k] += 1
                        if len(samples[k]) < 3:
                            samples[k].append((n_raw, line))
    left = (1 if cur is not None else 0) + sum(1 for _ in lh)
    log(f"== {name} (in-order two-pointer): raw={n_raw:,} matched={matched:,} "
        f"raw_without_label={dropped:,} loghub2_unconsumed={left:,} "
        f"longest_consecutive_miss_run={max_run:,}")
    report_dropped(name, kinds, samples)
    return matched, n_raw


def align_multiset(name, raw_paths, logp, structp, gtp, classify):
    """Order-agnostic exact-full-line multiset match."""
    pool = collections.defaultdict(collections.deque)
    lh_n = 0
    for line, eid in lh_pairs(logp, structp):
        pool[line].append(eid)
        lh_n += 1
    conflicts = sum(1 for q in pool.values() if len(set(q)) > 1)
    dup_lines = sum(len(q) - 1 for q in pool.values() if len(q) > 1)
    matched = dropped = n_raw = 0
    kinds, samples = collections.Counter(), collections.defaultdict(list)
    with open(gtp, "w") as gf:
        for p in raw_paths:
            with open(p, encoding="utf-8", errors="replace") as rf:
                for line in rf:
                    n_raw += 1
                    line = line.rstrip("\n")
                    q = pool.get(line)
                    if q:
                        gf.write(f"{n_raw}\t{q.popleft()}\n")
                        matched += 1
                    else:
                        gf.write(f"{n_raw}\t\n")
                        dropped += 1
                        k = classify(line)
                        kinds[k] += 1
                        if len(samples[k]) < 3:
                            samples[k].append((n_raw, line))
    left = sum(len(q) for q in pool.values())
    log(f"== {name} (multiset on full line): raw={n_raw:,} loghub2={lh_n:,} matched={matched:,} "
        f"raw_without_label={dropped:,} loghub2_unmatched={left:,} "
        f"duplicate_full_lines={dup_lines:,} dup_lines_with_conflicting_eids={conflicts}")
    report_dropped(name, kinds, samples)
    return matched, n_raw


if __name__ == "__main__":
    which = sys.argv[3] if len(sys.argv) > 3 else "all"
    if which in ("all", "openstack"):
        align_multiset(
            "OpenStack",
            [DATA / f"raw/{f}" for f in
             ["openstack_normal1.log", "openstack_normal2.log", "openstack_abnormal.log"]],
            DATA / "loghub2/OpenStack/OpenStack_full.log",
            DATA / "loghub2/OpenStack/OpenStack_full.log_structured.csv",
            OUT / "openstack_gt.tsv", classify_os)
    if which in ("all", "bgl"):
        align_subsequence(
            "BGL", [DATA / "raw/BGL.log"],
            DATA / "loghub2/BGL/BGL_full.log",
            DATA / "loghub2/BGL/BGL_full.log_structured.csv",
            OUT / "bgl_gt.tsv", classify_bgl)
