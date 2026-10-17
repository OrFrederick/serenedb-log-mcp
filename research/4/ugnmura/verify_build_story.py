"""Independent cross-check of corpus agent's build-duration story from raw archive."""
import json,re,statistics,sys,tarfile
from pathlib import Path
p=Path(__file__).parent
with tarfile.open(sys.argv[1]) as t:
    labels=set(re.findall(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',t.extractfile('anomaly_labels.txt').read().decode()))
    builds=[]; labelled_entries=[]
    for member in t:
        if not member.name.endswith('.log'):continue
        for line_no,raw in enumerate(t.extractfile(member),1):
            line=raw.decode().rstrip();instance=re.search(r'\[instance: ([0-9a-f-]+)\]',line)
            if instance and instance[1] in labels:labelled_entries.append({'file':member.name,'line_no':line_no,'instance':instance[1],'line':line})
            duration=re.search(r'Took ([0-9.]+) seconds to build instance\.',line)
            if duration:builds.append({'file':member.name,'line_no':line_no,'instance':instance[1] if instance else None,'seconds':float(duration[1]),'line':line})
labelled=[b for b in builds if b['instance'] in labels]
unlabelled=[b for b in builds if b['file']=='openstack_abnormal.log' and b['instance'] not in labels]
normal=[b for b in builds if b['file']!='openstack_abnormal.log']
x={'label_source':'anomaly_labels.txt in archive; labels indicate injected anomaly instances, not a root cause','labels':sorted(labels),
 'labelled_builds':labelled,'labelled_entries':labelled_entries,
 'unlabelled_same_file':{'n':len(unlabelled),'median_seconds':statistics.median(b['seconds'] for b in unlabelled),'max_seconds':max(b['seconds'] for b in unlabelled)},
 'normal_files':{'n':len(normal),'median_seconds':statistics.median(b['seconds'] for b in normal),'max_seconds':max(b['seconds'] for b in normal)},
 'threshold_sensitivity':[{'threshold_seconds':q,'labelled':sum(b['seconds']>q for b in labelled),'unlabelled':sum(b['seconds']>q for b in builds if b['instance'] not in labels)} for q in [22,30,35]],
 'scope':'OpenStack raw archive; exact text Took <numeric> seconds to build instance.; one matched Log Entry per emitted build duration',
 'limits':['Offline scan, not SQL Evidence','Thresholds evaluated after seeing labels; no out-of-sample detector claim','No evidence of financial loss, downtime or customer impact','Component involvement is not causal attribution']}
assert sorted(b['seconds'] for b in labelled)==[30.32,32.12,42.81,51.59]
assert len(labelled_entries)==100
assert len(unlabelled)==193 and len(normal)==1869
assert all(' INFO ' in r['line'] for r in labelled_entries)
(p/'build-story-verified.json').write_text(json.dumps(x,indent=2))
print(json.dumps({k:v for k,v in x.items() if k not in ['labelled_entries','labelled_builds']},indent=2))
