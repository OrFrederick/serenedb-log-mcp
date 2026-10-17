#!/usr/bin/env python3
"""Check two independently constructed classifiers on every raw Log Entry.

Measured wall time includes TWO classifier calls and output hashing; not a fair
single-classifier throughput benchmark. No assignment CSV is written.
"""
import argparse,collections,hashlib,json,time
from pathlib import Path
from benchmark import entries,json_write
from catalogue import Catalogue

def main():
 p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--dataset',choices=['BGL','OpenStack'],action='append');a=p.parse_args();out=Path(__file__).parent/'results';result={}
 for dataset in a.dataset or ['BGL','OpenStack']:
  c1=Catalogue(a.data/f'{dataset}-annotated.zip',dataset,resolve_specificity=True);c2=Catalogue(a.data/f'{dataset}-annotated.zip',dataset,resolve_specificity=True)
  counts=collections.Counter();ambiguous=[];resolved=[];digest=hashlib.sha256();n=0;start=time.perf_counter()
  for source,number,raw,component,content,valid in entries(a.data,dataset):
   x=c1.classify(content,component);y=c2.classify(content,component)
   if x!=y:raise AssertionError((dataset,source,number,x,y))
   counts[x.status]+=1;n+=1
   digest.update(json.dumps([source,number,component,x.template_id,x.status,x.candidate_count],separators=(',',':')).encode());digest.update(b'\n')
   if x.status=='ambiguous' and len(ambiguous)<10:ambiguous.append(dict(source=source,line=number,message=content,candidates=c1.candidates(content)))
   if x.status=='catalogue_specificity' and len(resolved)<10:resolved.append(dict(source=source,line=number,message=content,selected=x.template_text,candidates=c1.candidates(content)))
  result[dataset]=dict(lines=n,identical_assignments=n,status=dict(counts),seconds=time.perf_counter()-start,assignment_sequence_sha256=digest.hexdigest(),parser_version=c1.version,ambiguous_examples=ambiguous,specificity_examples=resolved)
  json_write(out/'catalogue-determinism.json',result);print(dataset,result[dataset],flush=True)
if __name__=='__main__':main()
