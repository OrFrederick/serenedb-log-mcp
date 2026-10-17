"""Reproduce profiles without extracting raw corpora. Python stdlib only."""
import argparse
import collections as C
import datetime as dt
import json
import math
from pathlib import Path
from corpus import bgl_rows, openstack_rows, anomaly_ids

def quantiles(values):
    a = sorted(values)
    if not a:
        return {'n':0}
    def q(p):
        x=(len(a)-1)*p; i=math.floor(x); j=math.ceil(x)
        return a[i]+(a[j]-a[i])*(x-i)
    return dict(n=len(a), min=a[0], p50=q(.5), p95=q(.95), p99=q(.99), max=a[-1])

def counts(counter):
    return [{'key':k,'n':v} for k,v in counter.most_common()]

def openstack(path):
    ids=set(anomaly_ids(path)); rows=list(openstack_rows(path))
    result={}
    for source in sorted(set(r['source_file'] for r in rows)):
        scope=[r for r in rows if r['source_file']==source]
        http=[r for r in scope if r['latency_s'] is not None]
        result[source]=dict(n=len(scope), structured=sum(r['ts'] is not None for r in scope),
            start=min(r['ts'] for r in scope if r['ts']),end=max(r['ts'] for r in scope if r['ts']),
            levels=counts(C.Counter(r['level'] for r in scope)),components=counts(C.Counter(r['component'] for r in scope)),
            instances=len(set(r['instance_id'] for r in scope if r['instance_id'])),
            requests=len(set(r['request_id'] for r in scope if r['request_id'])),
            http_statuses=counts(C.Counter(r['http_status'] for r in http)),
            http_latency=quantiles([r['latency_s'] for r in http]),
            endpoints=[dict(endpoint=e,latency=quantiles([r['latency_s'] for r in http if r['endpoint']==e])) for e in sorted(set(r['endpoint'] for r in http))],
            durations=[dict(operation=o,stats=quantiles([r['create_seconds'] for r in scope if r['create_operation']==o])) for o in sorted(set(r['create_operation'] for r in scope if r['create_operation']))])
    result['labelled_instances']=[]
    for instance in sorted(ids):
        scope=[r for r in rows if r['instance_id']==instance]
        result['labelled_instances'].append(dict(instance_id=instance,n=len(scope),start=min(r['ts'] for r in scope),end=max(r['ts'] for r in scope),
            levels=counts(C.Counter(r['level'] for r in scope)),components=counts(C.Counter(r['component'] for r in scope)),
            durations=[dict(ts=r['ts'],operation=r['create_operation'],seconds=r['create_seconds'],entry_id=r['entry_id']) for r in scope if r['create_operation']],
            timeline=[{k:r[k] for k in ['entry_id','ts','component','level','message','request_id']} for r in scope]))
    builds=[r for r in rows if r['create_operation']=='build instance.']
    result['build_comparison']={
        'labelled':quantiles([r['create_seconds'] for r in builds if r['instance_id'] in ids]),
        'unlabelled_abnormal_file':quantiles([r['create_seconds'] for r in builds if r['source_file']=='openstack_abnormal.log' and r['instance_id'] not in ids]),
        'normal_files':quantiles([r['create_seconds'] for r in builds if r['source_file']!='openstack_abnormal.log'])}
    result['build_threshold_sensitivity']=[dict(threshold=t,
        labelled=sum(r['create_seconds']>t and r['instance_id'] in ids for r in builds),
        unlabelled=sum(r['create_seconds']>t and r['instance_id'] not in ids for r in builds)) for t in [22,23,24,25,27.5,30,35,40,50,60]]
    result['top_builds']=[{k:r[k] for k in ['ts','source_file','entry_id','instance_id','create_seconds','level']} for r in sorted(builds,key=lambda r:r['create_seconds'],reverse=True)[:20]]
    return result

def sessions(rows,gap,key):
    last={}; groups={}; out=[]
    for r in sorted(rows,key=lambda r:(r['epoch_s'],r['entry_id'])):
        k=key(r); t=r['epoch_s']
        if k not in last or t-last[k]>gap:
            g=dict(label=r['label'],start_epoch=t,end_epoch=t,start=r['ts'],end=r['ts'],n=0,nodes=C.Counter(),racks=set(),midplanes=set(),components=set(),sample=r['message'],first_entry_id=r['entry_id'])
            groups[k]=g;out.append(g)
        g=groups[k];g['end_epoch']=t;g['end']=r['ts'];g['n']+=1;g['nodes'][r['node']]+=1
        if r['rack']:g['racks'].add(r['rack'])
        if r['midplane']:g['midplanes'].add(r['midplane'])
        g['components'].add(r['component']);last[k]=t
    return out

def group_count(ordered,gap,key):
    last={};n=0
    for r in ordered:
        k=key(r);t=r['epoch_s']
        n+=k not in last or t-last[k]>gap
        last[k]=t
    return n

def compact(g):
    return {**{k:v for k,v in g.items() if k not in ['nodes','racks','midplanes','components']},
        'distinct_nodes':len(g['nodes']),'distinct_racks':len(g['racks']),'distinct_midplanes':len(g['midplanes']),
        'top_node':g['nodes'].most_common(1)[0][0], 'top_node_entries':g['nodes'].most_common(1)[0][1],
        'top_node_share':g['nodes'].most_common(1)[0][1]/g['n'],'components':sorted(g['components']),
        'duration_seconds':g['end_epoch']-g['start_epoch']}

def bgl(path):
    total=0; levels=C.Counter();components=C.Counter();labels=C.Counter();nodes=C.Counter();weekly=C.Counter();alert_weekly=C.Counter();alerts=[];date_bounds=[]
    for r in bgl_rows(path):
        total+=1; levels[r['level']]+=1; components[r['component']]+=1;labels[r['label']]+=1;nodes[r['node']]+=1
        day=dt.date.fromisoformat(r['ts'][:10]);week=(day-dt.timedelta(days=day.weekday())).isoformat();weekly[week]+=1
        if r['label']!='-':alerts.append(r);alert_weekly[(week,r['label'])]+=1
        date_bounds.append(r['ts']) if total==1 else None
        if total==1:start=end=r['ts']
        start=min(start,r['ts']);end=max(end,r['ts'])
    result=dict(n=total,start=start,end=end,alert_entries=len(alerts),levels=counts(levels),components=counts(components),labels=counts(labels),top_nodes=counts(nodes)[:20],weekly=counts(weekly),alert_weekly=[dict(week=k[0],label=k[1],n=v) for k,v in sorted(alert_weekly.items())])
    ordered=sorted(alerts,key=lambda r:(r['epoch_s'],r['entry_id']))
    def paper_count(gap,use_display=False,inclusive=False):
        last={};n=0;categories=C.Counter()
        for r in (sorted(alerts,key=lambda r:(r['ts'],r['entry_id'])) if use_display else ordered):
            t=(dt.datetime.fromisoformat(r['ts'])-dt.datetime(1970,1,1)).total_seconds() if use_display else r['epoch_s']
            delta=t-last.get(r['label'],-math.inf)
            if (delta>=gap if inclusive else delta>gap):n+=1;categories[r['label']]+=1
            last[r['label']]=t
        return dict(n=n,categories=counts(categories))
    result['published_reproduction_checks']={f'{clock}_break_{operator}_{gap}':paper_count(gap,clock=='display',operator=='gte') for clock in ['epoch','display'] for operator in ['gt','gte'] for gap in [5,300]}
    previous={};transitions=C.Counter()
    for r in ordered:
        before=previous.get(r['node'])
        if before and before['label']!=r['label'] and r['epoch_s']-before['epoch_s']<=300:
            transitions[(before['label'],r['label'])]+=1
        previous[r['node']]=r
    result['adjacent_category_transitions_300s']=[dict(before=k[0],after=k[1],pairs=v) for k,v in transitions.most_common(20)]
    result['gap_sensitivity']=[]
    for gap in [1,5,30,60,300,900,3600]:
        global_groups=sessions(alerts,gap,lambda r:r['label']);node_count=group_count(ordered,gap,lambda r:(r['label'],r['node']))
        result['gap_sensitivity'].append(dict(gap_seconds=gap,category_groups=len(global_groups),node_category_groups=node_count,by_category=counts(C.Counter(g['label'] for g in global_groups))))
        if gap in [5,300]:
            result[f'top_groups_gap_{gap}']=[compact(g) for g in sorted(global_groups,key=lambda g:g['n'],reverse=True)[:30]]
            result[f'top_spread_gap_{gap}']=[compact(g) for g in sorted(global_groups,key=lambda g:(len(g['nodes']),g['n']),reverse=True)[:20]]
    result['top_group_minute_profiles']=[]
    for g in result['top_groups_gap_300'][:10]:
        buckets=C.Counter(r['epoch_s']//60 for r in alerts if r['label']==g['label'] and g['start_epoch']<=r['epoch_s']<=g['end_epoch'])
        result['top_group_minute_profiles'].append(dict(label=g['label'],start_epoch=g['start_epoch'],entries=g['n'],nonempty_minute_bins=len(buckets),peak_minute_entries=max(buckets.values()),peak_minute_share=max(buckets.values())/g['n'],mean_entries_per_nonempty_minute=g['n']/len(buckets)))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('dataset',choices=['bgl','openstack']);p.add_argument('archive');p.add_argument('output');a=p.parse_args()
    result=globals()[a.dataset](a.archive)
    Path(a.output).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k in ['n','start','end','alert_entries','gap_sensitivity','build_comparison','build_threshold_sensitivity']},indent=2))
