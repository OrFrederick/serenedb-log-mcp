"""Independent offline raw-corpus validation and prioritisation sensitivity.
No model calls, SQL queries, invented Ownership Table, or incident inference.
python role_experiment.py /tmp/serenedb-data/OpenStack.tar.gz
"""
import collections,hashlib,json,math,re,sys,tarfile
from pathlib import Path
ROOT=Path(__file__).parent
RX=re.compile(r'^(\S+) (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+) (\d+) (\S+) (\S+) (.*)$')

def profile(path):
    counts=collections.Counter();bins=collections.defaultdict(collections.Counter); samples={};total=unmatched=0;windows={};context=[]
    with tarfile.open(path) as archive:
        for member in archive:
            if not member.name.endswith('.log'):continue
            lines=archive.extractfile(member).read().decode().splitlines(); tsvalues=[]
            for lineno,line in enumerate(lines,1):
                total+=1;m=RX.match(line)
                if not m:unmatched+=1;continue
                file,ts,pid,level,comp,msg=m.groups();tsvalues.append(ts);counts[(comp,level)]+=1;bins[(comp,level)][ts[:10]]+=1
                if level in ['ERROR','WARNING'] and comp not in samples:samples[comp]={'line':line,'file':member.name,'line_no':lineno}
                if comp=='keystonemiddleware.auth_token':
                    context.append({'file':member.name,'line_no':lineno,'line':line,'context':lines[max(0,lineno-3):lineno+2]})
            windows[member.name]={'min_timestamp':min(tsvalues),'max_timestamp':max(tsvalues),'physical_lines':len(lines)}
    return {'source':'https://zenodo.org/records/8196385/files/OpenStack.tar.gz?download=1',
      'archive_sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),'parser':'anchored header regex; unmatched lines remain counted as Log Entries',
      'total_lines':total,'parsed_lines':total-unmatched,'unmatched_lines':unmatched,'files':windows,
      'counts':[{'component':c,'level':l,'count':n,'days':dict(bins[(c,l)])} for (c,l),n in counts.most_common()],
      'first_warning_or_error_by_component':samples,'auth_context':context,
      'limits':['Offline Python scan; not a SereneDB query','Concatenated raw files may include repeated underlying entries; no deduplication performed','Calendar day slices have unequal coverage; no growth conclusion','No official Ownership Table supplied']}

def sensitivity(f):
    comps=collections.defaultdict(collections.Counter)
    for x in f['counts']:comps[x['component']][x['level']]+=x['count']
    bad={c:sum(d[s] for s in ['WARNING','ERROR','CRITICAL']) for c,d in comps.items()}
    severe={c:sum(d[s] for s in ['ERROR','CRITICAL']) for c,d in comps.items()}
    population=[c for c in comps if bad[c]]
    maxlog=max(math.log1p(bad[c]) for c in population)
    rankings={}
    for w in [0,.25,.5,.75,1]:
        scored=[{'component':c,'count':bad[c],'all_component_entries':sum(comps[c].values()),
                 'fraction':bad[c]/sum(comps[c].values()),
                 'score':w*math.log1p(bad[c])/maxlog+(1-w)*bad[c]/sum(comps[c].values())} for c in population]
        rankings[str(w)]=sorted(scored,key=lambda x:(-x['score'],x['component']))
    return {'filters':{'warnings_plus_errors':sorted([(c,bad[c]) for c in population],key=lambda x:-x[1]),
                       'error_and_critical_only':sorted([(c,severe[c]) for c in population if severe[c]],key=lambda x:-x[1])},
      'hypothetical_score':'w*log1p(flagged_count)/max(log1p(flagged_count))+(1-w)*flagged_count/all_component_entries',
      'score_is':'a sensitivity illustration, not chosen priority rule; all-component-entry fraction is not an outage or user error rate',
      'rankings':rankings,'selected_rule':'Rank exact Components by WARNING|ERROR|CRITICAL matching Log Entry count descending; retain tied rank and alphabetize display; disclose severity filter and all ties. Show severe-only comparison alongside it.',
      'limits':['No causal or financial risk ordering established','Team/spread metrics unavailable without official Ownership Table','All candidates here have affected Component count 1 by construction; spread adds no discrimination','Unmatched lines excluded from Component ranking but disclosed','No statistically valid temporal trend from unequal archive slices']}

if __name__=='__main__':
    f=profile(sys.argv[1]);s=sensitivity(f)
    (ROOT/'openstack-fixture.json').write_text(json.dumps(f,indent=2));(ROOT/'priority-sensitivity.json').write_text(json.dumps(s,indent=2))
    assert (f['total_lines'],f['parsed_lines'],f['unmatched_lines'])==(207820,207812,8)
    assert len(f['auth_context'])==6
    assert s['filters']['warnings_plus_errors'][0]==('nova.virt.libvirt.imagecache',3059)
    assert s['filters']['error_and_critical_only'][0]==('oslo_service.periodic_task',184)
    print(json.dumps({'total':f['total_lines'],'parsed':f['parsed_lines'],'unmatched':f['unmatched_lines'],'auth_lines':len(f['auth_context']),
      'top_by_weight':{w:r[0]['component'] for w,r in s['rankings'].items()},'filters':s['filters']},indent=2))
