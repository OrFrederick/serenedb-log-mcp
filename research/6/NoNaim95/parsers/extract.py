"""Extract the message part of each Log Entry into one-message-per-line files.

BGL raw line:   Label Epoch Date Node Time NodeRepeat Type Component Level Message...
  -> message = line.split(' ', 9)[9]  (fields 10..end, original spacing kept); '' if the line has only 9 fields.
OpenStack raw: LogFile Date Time Pid Level Component [request-context] Message...
  -> message = text after the first ' [...] ' that follows the Component (regex below);
     lines without a request-context bracket (184 traceback lines) -> text after Component.
Loghub-2.0: Content column of *_full.log_structured.csv (+ EventTemplate as ground truth).

usage: python -I extract.py OUTDIR
"""
import sys, os, re, csv, time
import numpy as np
OUT = sys.argv[1]; os.makedirs(OUT, exist_ok=True)
RAW = '/Users/deni/hackathon/data/raw'; LH2 = '/Users/deni/hackathon/data/lh2'
OS_RE = re.compile(r'^(\S+) (\S+) (\S+) (\d+) (\S+) (\S+) \[([^\]]*)\] (.*)$')
OS_NOBR = re.compile(r'^(\S+) (\S+) (\S+) (\d+) (\S+) (\S+) ?(.*)$')

def bgl_msg(line):
    p = line.split(' ', 9)
    return p[9] if len(p) > 9 else ''

def os_msg(line):
    m = OS_RE.match(line)
    if m: return m.group(8)
    m = OS_NOBR.match(line)
    return m.group(7) if m else line

def write(name, msgs):
    with open(os.path.join(OUT, name), 'w', encoding='utf-8', newline='\n') as w:
        for m in msgs: w.write(m.replace('\n', ' ') + '\n')

t = time.time()
with open(f'{RAW}/BGL.log', encoding='utf-8', errors='surrogateescape') as f:
    write('bgl_raw_msg.txt', (bgl_msg(l.rstrip('\r\n')) for l in f))
print('bgl raw', time.time() - t)

t = time.time(); n = 0
def os_iter(files):
    for fn in files:
        with open(f'{RAW}/{fn}', encoding='utf-8', errors='surrogateescape') as f:
            for l in f: yield os_msg(l.rstrip('\r\n'))
write('os_raw_msg.txt', os_iter(['openstack_normal1.log', 'openstack_normal2.log', 'openstack_abnormal.log']))
print('os raw', time.time() - t)

for ds, fn in [('bgl', 'BGL/BGL_full.log_structured.csv'), ('os', 'OpenStack/OpenStack_full.log_structured.csv')]:
    t = time.time()
    contents = []; tmpls = []
    with open(f'{LH2}/{fn}', newline='', encoding='utf-8') as f:
        r = csv.DictReader(f)
        for row in r:
            contents.append(row['Content']); tmpls.append(row['EventTemplate'])
    write(f'{ds}_lh2_content.txt', contents)
    write(f'{ds}_lh2_gt_template.txt', tmpls)
    print(ds, 'lh2', len(contents), time.time() - t)

# sanity: does our raw-line extraction rule reproduce Loghub-2.0's Content column on Loghub-2.0's own raw file?
for ds, raw, fn, fx in [('bgl', 'BGL/BGL_full.log', 'bgl_lh2_content.txt', bgl_msg), ('os', 'OpenStack/OpenStack_full.log', 'os_lh2_content.txt', os_msg)]:
    same = diff = 0; ex = []
    with open(f'{LH2}/{raw}', encoding='utf-8', errors='surrogateescape') as a, open(os.path.join(OUT, fn), encoding='utf-8') as b:
        for la, lb in zip(a, b):
            ma = fx(la.rstrip('\r\n')); mb = lb.rstrip('\n')
            if ma == mb: same += 1
            elif ma.strip() == mb.strip(): same += 1  # whitespace-only difference
            else:
                diff += 1
                if len(ex) < 5: ex.append((ma[:150], mb[:150]))
    print(ds, 'extraction rule vs lh2 Content: same', same, 'diff', diff, ex)
