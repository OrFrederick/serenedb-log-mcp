#!/usr/bin/env python3
"""Download compressed corpora once into --data; verify authoritative Zenodo MD5.
Uses existing files without overwriting. Checksums are not authenticity signatures.
"""
import argparse,hashlib,json,urllib.request
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);a=p.parse_args();a.data.mkdir(parents=True,exist_ok=True);results=[]
 for record,names in [(8196385,{'BGL.zip':'BGL.zip','OpenStack.tar.gz':'OpenStack.tar.gz'}),(8275861,{'BGL.zip':'BGL-annotated.zip','OpenStack.zip':'OpenStack-annotated.zip'})]:
  meta=json.load(urllib.request.urlopen(f'https://zenodo.org/api/records/{record}'))
  for source,target in names.items():
   item=next(f for f in meta['files'] if f['key']==source);path=a.data/target
   if not path.exists():urllib.request.urlretrieve(item['links']['self'],path)
   with path.open('rb') as f:md5=hashlib.file_digest(f,'md5').hexdigest()
   if 'md5:'+md5!=item['checksum']:raise RuntimeError(f'Checksum mismatch: {path}')
   with path.open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
   results.append(dict(file=target,url=item['links']['self'],bytes=path.stat().st_size,md5=md5,sha256=sha))
 print(json.dumps(results,indent=2))
if __name__=='__main__':main()
