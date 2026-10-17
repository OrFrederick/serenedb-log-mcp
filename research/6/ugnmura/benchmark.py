#!/usr/bin/env python3
"""Streaming, single-process benchmarks. No SQL; run AFTER official loading in production.

uv run --python 3.12 --with drain3==0.9.11 benchmark.py --data /tmp/serenedb-data
The default reads raw archives, trains then freezes Drain3, and tests known-template matching.
No raw corpus is extracted or committed. Results are JSON, not multi-million-row CSVs.
"""
from __future__ import annotations
import argparse, collections, csv, functools, hashlib, io, json, platform, re, resource, sys, tarfile, time, zipfile
from pathlib import Path
from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig

VERSION = 'mask-v1'
# Generic baseline deliberately masks numeric literals, including status/error codes;
# production must review masks and preserve semantically meaningful fields separately.
MASKS = [(re.compile(p), f'<{n}>') for n,p in [
 ('UUID',r'\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b'),
 ('IP',r'\b(?:\d{1,3}\.){3}\d{1,3}\b'),
 ('HEX',r'\b0[xX][0-9a-fA-F]+\b|\b[0-9a-fA-F]{16,}\b'),
 ('NUM',r'(?<![\w])[-+]?\d+(?:\.\d+)?(?![\w])'),
]]
OPENSTACK = re.compile(r'^\S+\s+\d{4}-\d\d-\d\d\s+\S+\s+\d+\s+\S+\s+(\S+)\s+(?:\[(?:req-[^\]]*|-[^\]]*)\]\s*)?(.*)$')
BGL = re.compile(r'(?:^|\s)(?:RAS|NULL) (\S+) (?:INFO|FATAL|ERROR|WARNING|SEVERE|FAILURE)(?: (.*))?$')

def mask(message):
 for regex, replacement in MASKS:
  message = regex.sub(replacement,message)
 return ' '.join(message.split())

def raw_lines(root, dataset):
 if dataset=='BGL':
  with zipfile.ZipFile(root/'BGL.zip') as z:
   for name in sorted(z.namelist()):
    if name.lower().endswith('.log'):
     with z.open(name) as f:
      for n,line in enumerate(f,1): yield name,n,line
 else:
  with tarfile.open(root/'OpenStack.tar.gz','r:gz') as t:
   # Archive order retained; source names and row number disambiguate stable keys.
   for member in t:
    if member.isfile() and member.name.endswith('.log'):
     with t.extractfile(member) as f:
      for n,line in enumerate(f,1): yield member.name,n,line

def entries(root,dataset):
 regex = BGL if dataset=='BGL' else OPENSTACK
 for source,n,line in raw_lines(root,dataset):
  text=line.decode('utf-8',errors='replace').rstrip('\r\n')
  if dataset=='BGL':
   fields=text.split(maxsplit=5)
   match=regex.search(fields[5]) if len(fields)==6 else None
  else: match=regex.fullmatch(text)
  yield (source,n,line,match.group(1) if match else '__HEADER_UNPARSED__',(match.group(2) or '') if match else text,match is not None)

def annotation_rows(root,dataset):
 with zipfile.ZipFile(root/f'{dataset}-annotated.zip') as z:
  name=next(n for n in z.namelist() if n.endswith('_structured.csv'))
  with z.open(name) as f:
   yield from csv.DictReader(io.TextIOWrapper(f,encoding='utf-8-sig',errors='replace'))

def catalog(root,dataset):
 # Obtain the exact full corpus dictionary from the archive, NOT the 2k sample.
 with zipfile.ZipFile(root/f'{dataset}-annotated.zip') as z:
  name=next(n for n in z.namelist() if n.endswith('_templates.csv'))
  with z.open(name) as f:
   return list(csv.DictReader(io.TextIOWrapper(f,encoding='utf-8-sig')))

def json_write(path,value):
 path.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')

def new_miner(dataset):
 c=TemplateMinerConfig()
 c.drain_sim_th=.5
 c.drain_depth=4 if dataset=='BGL' else 5
 c.profiling_enabled=False
 # No state persistence, no eviction, no extra delimiter, pre-mask shared with baseline.
 return TemplateMiner(config=c)

def metrics(truth,pred,overlap,correct,total):
 perfect = sum(1 for (g,p),n in overlap.items() if truth[g]==pred[p]==n)
 good_lines = sum(n for (g,p),n in overlap.items() if truth[g]==pred[p]==n)
 return dict(GA=good_lines/total,PA=correct/total,FGA=2*perfect/(len(truth)+len(pred)),truth_groups=len(truth),predicted_groups=len(pred),lines=total)

def accuracy(root,dataset,out):
 # Exact full annotated data (different population from raw corpus): not a row join.
 cached_mask=functools.lru_cache(maxsize=50000)(mask)
 miners=collections.defaultdict(lambda:new_miner(dataset))
 start=time.perf_counter()
 for line_number,row in enumerate(annotation_rows(root,dataset),1):
  miners[row.get('Component','')].add_log_message(cached_mask(row['Content']))
  if line_number%1000000==0: print(dataset,'annotation_training_progress',line_number,flush=True)
 train_seconds=time.perf_counter()-start
 result={'scope': 'full annotated Content only, no Component field exists; one unpartitioned Drain3 model; normalized-exact PA proxy, not official oracle-corrected evaluator'}
 from catalogue import Catalogue
 cat=Catalogue(root/f'{dataset}-annotated.zip',dataset,resolve_specificity=True)
 @functools.lru_cache(maxsize=50000)
 def frozen_template(comp,content):
  cluster=miners[comp].match(content,full_search_strategy='fallback')
  return cluster.get_template() if cluster else None
 for method in ['mask','drain3','catalogue']:
  truth=collections.Counter(); pred=collections.Counter(); overlap=collections.Counter(); correct=0; total=0; unknown=0
  start=time.perf_counter()
  for row in annotation_rows(root,dataset):
   comp=row.get('Component',''); content=cached_mask(row['Content']); gt=row['EventTemplate']
   if method=='catalogue':
    assignment=cat.classify(row['Content'])
    output=assignment.template_text or '__UNMATCHED__:'+row['Content']
    unknown += assignment.template_id is None
   elif method=='drain3':
    template=frozen_template(comp,content)
    output=template if template is not None else content
    unknown += template is None
   else: output=content
   # A template id is partitioned by component in the recommended production design;
   # evaluate content-only classes for comparability with published ground truth.
   g=gt; p=output
   truth[g]+=1;pred[p]+=1;overlap[g,p]+=1;total+=1
   normalized = re.sub(r'<(?:NUM|HEX|UUID|IP)>','<*>',output)
   correct += ' '.join(normalized.split())==' '.join(gt.split())
  seconds=time.perf_counter()-start
  result[method]=dict(metrics(truth,pred,overlap,correct,total),seconds=seconds,unknown=unknown)
  json_write(out/f'{dataset}-annotation-quality.json',result)
  print(dataset,'annotation_quality_progress',method,result[method],flush=True)
 result['drain_train_seconds']=train_seconds
 json_write(out/f'{dataset}-annotation-quality.json',result)
 print(dataset,'annotation-quality',result,flush=True)

def benchmark(root,dataset,out):
 metadata=dict(dataset=dataset,python=sys.version,platform=platform.platform(),processor=platform.processor(),mask_version=VERSION,
  drain3_version='0.9.11',drain_config=dict(sim_th=.5,depth=4 if dataset=='BGL' else 5,partition='Component',max_clusters='unlimited'),
  timing='single process wall clock including compressed archive reads, header parsing, hashing/counting; excludes dependency install/download; no concurrent method workers')
 archive=root/('BGL.zip' if dataset=='BGL' else 'OpenStack.tar.gz')
 metadata['archive_sha256']=hashlib.file_digest(archive.open('rb'),'sha256').hexdigest()
 counts=collections.Counter();component_counts=collections.Counter(); bad=0; empty=0; n=0; sha=hashlib.sha256(); source_hashes={}; bytes_total=0
 start=time.perf_counter()
 for source,number,line,comp,text,valid in entries(root,dataset):
  source_hashes.setdefault(source,hashlib.sha256()).update(line)
  sha.update(line);bytes_total+=len(line);n+=1;bad+=not valid;empty+=not text
  counts[comp,mask(text)]+=1;component_counts[comp]+=1
 seconds=time.perf_counter()-start
 metadata.update(lines=n,bytes=bytes_total,raw_sha256_in_stream_order=sha.hexdigest(),source_sha256={k:v.hexdigest() for k,v in source_hashes.items()},header_unparsed=bad,empty_content=empty,components=dict(component_counts))
 metadata['mask']=dict(seconds=seconds,lines_per_second=n/seconds,groups=len(counts),groups_sha256=hashlib.sha256(json.dumps(sorted((c,t,n) for (c,t),n in counts.items())).encode()).hexdigest(),coverage=1.0,top=[dict(component=c,template=t,count=n) for (c,t),n in counts.most_common(20)])
 del counts
 json_write(out/f'{dataset}-raw.json',metadata)
 print(dataset,'mask',seconds,n/seconds,flush=True)
 miners=collections.defaultdict(lambda:new_miner(dataset));created=changed=0
 start=time.perf_counter()
 for source,number,line,comp,text,valid in entries(root,dataset):
  r=miners[comp].add_log_message(mask(text))
  created+=r['change_type']=='cluster_created';changed+=r['change_type']=='cluster_template_changed'
 train_seconds=time.perf_counter()-start
 counts=collections.Counter();unknown=0
 start=time.perf_counter()
 for source,number,line,comp,text,valid in entries(root,dataset):
  text=mask(text); c=miners[comp].match(text,full_search_strategy='fallback')
  if c is None: unknown+=1;counts[comp,'__UNMATCHED__:'+text]+=1
  else: counts[comp,c.get_template()]+=1
 infer_seconds=time.perf_counter()-start
 templates=[dict(component=comp,cluster_id=c.cluster_id,template=c.get_template(),training_size=c.size) for comp,m in miners.items() for c in m.drain.clusters]
 json_write(out/f'{dataset}-drain-templates.json',templates)
 metadata['drain3']=dict(train_seconds=train_seconds,inference_seconds=infer_seconds,total_seconds=train_seconds+infer_seconds,train_lines_per_second=n/train_seconds,inference_lines_per_second=n/infer_seconds,end_to_end_lines_per_second=n/(train_seconds+infer_seconds),training_clusters=created,template_changes=changed,groups=len(counts),groups_sha256=hashlib.sha256(json.dumps(sorted((c,t,n) for (c,t),n in counts.items())).encode()).hexdigest(),unknown=unknown,frozen_coverage=(n-unknown)/n,assignment_coverage=1.0,top=[dict(component=c,template=t,count=n) for (c,t),n in counts.most_common(20)])
 json_write(out/f'{dataset}-raw.json',metadata)
 print(dataset,'drain3',metadata['drain3'],flush=True)
 # Bounded LRU avoids repeating identical full message matches. Count ambiguities explicitly.
 cats=catalog(root,dataset)
 from catalogue import Catalogue
 catalogue_version=Catalogue(root/f'{dataset}-annotated.zip',dataset).version
 patterns=[(r['EventId'],r['EventTemplate'],re.compile('^'+re.escape(r['EventTemplate']).replace(re.escape('<*>'),'.*?')+'$')) for r in cats]
 @functools.lru_cache(maxsize=50000)
 def known(text):
  return tuple(i for i,template,p in patterns if p.fullmatch(text))
 counts=collections.Counter(); unknown=ambiguous=0; unmatched=[]
 start=time.perf_counter()
 for source,number,line,comp,text,valid in entries(root,dataset):
  found=known(text)
  if not found:
   unknown+=1
   if len(unmatched)<20:unmatched.append(dict(source=source,line=number,message=text))
  elif len(found)>1:ambiguous+=1
  else:counts[found[0]]+=1
 seconds=time.perf_counter()-start
 metadata['catalog']=dict(version=catalogue_version,templates=len(cats),seconds=seconds,lines_per_second=n/seconds,no_match=unknown,multiple_matches=ambiguous,unique_match=n-unknown-ambiguous,any_match_coverage=(n-unknown)/n,unique_match_coverage=(n-unknown-ambiguous)/n,cache=known.cache_info()._asdict(),unmatched_examples=unmatched,top=dict(counts.most_common(20)))
 metadata['peak_rss_platform_units']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
 json_write(out/f'{dataset}-raw.json',metadata)
 print(dataset,'catalog',metadata['catalog'],flush=True)

def main():
 p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--dataset',choices=['BGL','OpenStack'],action='append');p.add_argument('--quality',action='store_true');p.add_argument('--out',type=Path,default=Path(__file__).parent/'results');a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
 for dataset in a.dataset or ['BGL','OpenStack']:
  (accuracy if a.quality else benchmark)(a.data,dataset,a.out)
if __name__=='__main__':main()
