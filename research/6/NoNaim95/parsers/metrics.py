"""Loghub-2.0 metrics (Jiang et al., ISSTA 2024), ported from
github.com/logpai/loghub-2.0  benchmark/logparser/utils/evaluator.py (GA, FGA),
benchmark/evaluation/utils/PA_calculator.py (PA) and template_level_analysis.py (FTA).

Definitions (as in that code; groups are keyed by the EventTemplate *string*):
 GA  = (# lines whose ground-truth group is reproduced exactly: the GT group maps to exactly one parsed
        group and that parsed group contains no other lines) / # lines
 FGA = F1 of PGA = #correct groups / #parsed groups and RGA = #correct groups / #GT groups
 PA  = # lines whose parsed template string == GT template string / # lines
 FTA = F1 over templates: a parsed group is correct iff the set of GT templates of its lines == {its own template string}
Vectorised with pandas; `get_accuracy_orig` is the verbatim original kept for cross-checking.
"""
import numpy as np, pandas as pd

def _codes(x):
    return pd.factorize(pd.Series(x), sort=False)[0]

def grouping_metrics(gt, parsed):
    gt = np.asarray(_codes(gt)); pr = np.asarray(_codes(parsed))
    df = pd.DataFrame({'g': gt, 'p': pr})
    pair = df.groupby(['g', 'p'], sort=False).size().reset_index(name='n')
    g_nparsed = pair.groupby('g')['p'].transform('size')
    p_size = df['p'].value_counts()
    g_size = df['g'].value_counts()
    ok = pair[(g_nparsed == 1).values]
    ok = ok[ok['n'].values == p_size.loc[ok['p']].values]
    acc_events = int(ok['n'].sum()); acc_t = len(ok)
    GA = acc_events / len(df)
    PGA = acc_t / len(p_size); RGA = acc_t / len(g_size)
    FGA = 0.0 if PGA + RGA == 0 else 2 * PGA * RGA / (PGA + RGA)
    return dict(GA=GA, FGA=FGA, PGA=PGA, RGA=RGA, n_parsed=len(p_size), n_gt=len(g_size))

def pa(gt_tmpl, parsed_tmpl):
    a = np.asarray(gt_tmpl, dtype=object); b = np.asarray(parsed_tmpl, dtype=object)
    return float((a == b).sum()) / len(a)

def fta(gt_tmpl, parsed_tmpl):
    df = pd.DataFrame({'g': list(gt_tmpl), 'p': list(parsed_tmpl)})
    u = df.drop_duplicates()
    nset = u.groupby('p')['g'].size()
    single = u[u['p'].map(nset) == 1]
    correct = int((single['g'] == single['p']).sum())
    PTA = correct / len(nset); RTA = correct / df['g'].nunique()
    return 0.0 if PTA + RTA == 0 else 2 * PTA * RTA / (PTA + RTA)

def all_metrics(gt_tmpl, parsed_group, parsed_tmpl=None):
    """parsed_group: per-line group key (template string per Loghub semantics, or cluster id)."""
    m = grouping_metrics(gt_tmpl, parsed_group)
    if parsed_tmpl is not None:
        m['PA'] = pa(gt_tmpl, parsed_tmpl); m['FTA'] = fta(gt_tmpl, parsed_tmpl)
    return m

def get_accuracy_orig(series_groundtruth, series_parsedlog):
    """verbatim logic of loghub-2.0 evaluator.get_accuracy (filter_templates=None)."""
    series_groundtruth_valuecounts = series_groundtruth.value_counts()
    series_parsedlog_valuecounts = series_parsedlog.value_counts()
    df_combined = pd.concat([series_groundtruth, series_parsedlog], axis=1, keys=['groundtruth', 'parsedlog'])
    grouped_df = df_combined.groupby('groundtruth')
    accurate_events = 0; accurate_templates = 0
    for ground_truthId, group in grouped_df:
        vc = group['parsedlog'].value_counts()
        if vc.size == 1:
            parsed_eventId = vc.index[0]
            if len(group) == series_parsedlog[series_parsedlog == parsed_eventId].size:
                accurate_events += len(group); accurate_templates += 1
    GA = float(accurate_events) / len(series_groundtruth)
    PGA = float(accurate_templates) / len(series_parsedlog_valuecounts)
    RGA = float(accurate_templates) / len(series_groundtruth_valuecounts)
    FGA = 0.0 if PGA == 0 and RGA == 0 else 2 * (PGA * RGA) / (PGA + RGA)
    return GA, FGA

def load_lines(p):
    with open(p, encoding='utf-8', errors='surrogateescape') as f:
        return [l.rstrip('\n') for l in f]
