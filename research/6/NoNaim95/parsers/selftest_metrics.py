import sys, random, pandas as pd
sys.path.insert(0, '/Users/deni/hackathon/serenedb-log-mcp/.claude/worktrees/r6/research/6/NoNaim95/parsers')
from metrics import *
D='/Users/deni/.claude/jobs/f51b810a/tmp/r6p/data'
gt = load_lines(f'{D}/os_lh2_gt_template.txt')
random.seed(1)
# synthetic parsed: perturb gt by splitting/merging some groups
u = sorted(set(gt)); merge = {t: u[0] for t in u[:5]}
for trial in range(3):
    parsed = [merge.get(t, t) + (str(random.randint(0, 1)) if t in u[10:13] else '') for t in gt]
    a = get_accuracy_orig(pd.Series(gt), pd.Series(parsed)); b = grouping_metrics(gt, parsed)
    print('orig GA,FGA', a, ' vectorised', b['GA'], b['FGA']); assert abs(a[0]-b['GA'])<1e-12 and abs(a[1]-b['FGA'])<1e-12
    random.seed(trial+5); u = random.sample(u, len(u)); merge = {t: u[0] for t in u[:3]}
print('identity', all_metrics(gt, gt, gt))
