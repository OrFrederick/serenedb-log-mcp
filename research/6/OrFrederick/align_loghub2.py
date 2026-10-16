"""Align Loghub-2.0 full logs with the raw Zenodo 8196385 corpus.

Explains the line-count mismatch and writes a per-raw-line ground-truth label
file (raw line_no -> Loghub-2.0 EventId or empty) for accuracy measurement.

Usage: python -I align_loghub2.py <data_dir> <out_dir>
Outputs (in out_dir, gitignored data dir recommended):
  bgl_gt.tsv / openstack_gt.tsv : line_no \t event_id   (raw line numbering, 1-based)
  alignment_report.txt          : counts + samples of removed raw lines
"""
import collections
import csv
import re
import sys
from pathlib import Path

csv.field_size_limit(10**9)
data = Path(sys.argv[1])
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
rep = open(out / "alignment_report.txt", "w")


def log(*a):
    print(*a)
    print(*a, file=rep)


def align(name, raw_lines, lh_log, lh_struct, gt_path):
    """Greedy in-order subsequence alignment of Loghub-2.0 lines to raw lines."""
    lh = [l.rstrip("\n") for l in open(lh_log, encoding="utf-8", errors="replace")]
    eids = []
    with open(lh_struct, newline="", encoding="utf-8", errors="replace") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            eids.append(row[2])
    assert len(eids) == len(lh), (len(eids), len(lh))
    # multiset match by exact full-line content (order-agnostic; file order differs for OpenStack)
    pool = collections.defaultdict(collections.deque)
    for line, e in zip(lh, eids):
        pool[line].append(e)
    gt = [""] * len(raw_lines)
    removed = []
    for i, line in enumerate(raw_lines):
        q = pool.get(line)
        if q:
            gt[i] = q.popleft()
        else:
            removed.append(i)
    left = sum(len(q) for q in pool.values())
    conflicts = sum(1 for q in pool.values() if len(set(q)) > 1)
    log(f"== {name}: raw={len(raw_lines):,} loghub2={len(lh):,} matched={len(raw_lines)-len(removed):,} "
        f"raw_not_in_loghub2={len(removed):,} loghub2_unmatched={left:,} dup_lines_with_conflicting_eids={conflicts}")
    # characterise removed lines
    kinds = collections.Counter()
    samples = collections.defaultdict(list)
    for i in removed:
        k = classify(name, raw_lines[i])
        kinds[k] += 1
        if len(samples[k]) < 3:
            samples[k].append((i + 1, raw_lines[i][:200]))
    for k, c in kinds.most_common():
        log(f"  {k}: {c:,}")
        for ln, s in samples[k]:
            log(f"      line {ln}: {s!r}")
    with open(gt_path, "w") as f:
        for i, e in enumerate(gt):
            f.write(f"{i+1}\t{e}\n")


BGL_RE = re.compile(r"^(\S+) (\d+) (\d{4}\.\d\d\.\d\d) (\S+) (\S+) (\S+) (\S+) (\S+) (\S+) ?(.*)$")


def classify(name, line):
    if name == "BGL":
        m = BGL_RE.match(line)
        if not m:
            return "malformed (fewer than 9 header fields)"
        level = m.group(9)
        if level not in {"INFO", "WARNING", "SEVERE", "ERROR", "FATAL", "FAILURE"}:
            return f"non-standard level field ({level[:12]})"
        if not m.group(10).strip():
            return "empty content"
        if any(ord(c) > 126 or (ord(c) < 32 and c != "\t") for c in line):
            return "non-ASCII / control characters"
        return "well-formed (other reason)"
    else:
        if not line.strip():
            return "empty line"
        parts = line.split(" ", 6)
        if len(parts) < 7:
            return "malformed"
        if any(ord(c) > 126 for c in line):
            return "non-ASCII"
        return f"well-formed (level={parts[4]}, comp={parts[5]})"


bgl_raw = [l.rstrip("\n") for l in open(data / "raw/BGL.log", encoding="utf-8", errors="replace")]
align("BGL", bgl_raw, data / "loghub2/BGL/BGL_full.log",
      data / "loghub2/BGL/BGL_full.log_structured.csv", out / "bgl_gt.tsv")
del bgl_raw

# dev_loader loads openstack files in this order (line_no continues across files)
os_raw = []
for fn in ["openstack_normal1.log", "openstack_normal2.log", "openstack_abnormal.log"]:
    os_raw += [l.rstrip("\n") for l in open(data / "raw" / fn, encoding="utf-8", errors="replace")]
align("OpenStack", os_raw, data / "loghub2/OpenStack/OpenStack_full.log",
      data / "loghub2/OpenStack/OpenStack_full.log_structured.csv", out / "openstack_gt.tsv")
