"""Small full-Loghub catalogue matcher; no dependencies outside Python's stdlib.

No annotation row joins. Match the official loader's Content string.
Assignment identity includes Dataset, Component, catalogue sha256, and template text.
Ambiguities remain visible; specificity ranking is opt-in and explicitly marked.
"""
from __future__ import annotations
import csv, functools, hashlib, io, json, re, zipfile
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Assignment:
 template_id: str | None
 status: str
 parser_version: str
 template_text: str | None
 catalogue_event_id: str | None
 candidate_count: int

def template_identity(dataset, component, parser_version, template_text):
 payload=json.dumps([dataset,component,parser_version,template_text],ensure_ascii=False,separators=(',',':')).encode()
 return hashlib.sha256(payload).hexdigest()

class Catalogue:
 def __init__(self, archive: str | Path, dataset: str, resolve_specificity=False):
  self.dataset=dataset;self.resolve_specificity=resolve_specificity
  with zipfile.ZipFile(archive) as z:
   name=next(n for n in z.namelist() if n.endswith('_templates.csv'))
   data=z.read(name)
  self.version='loghub2-regex-v1:'+hashlib.sha256(data).hexdigest()
  self.templates=list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))
  self.patterns=[(r['EventId'],r['EventTemplate'],re.compile('^'+re.escape(r['EventTemplate']).replace(re.escape('<*>'),'.*?')+'$')) for r in self.templates]
  self.candidates=functools.lru_cache(maxsize=50000)(self._candidates)

 def _candidates(self,content):
  return tuple((event_id,template) for event_id,template,regex in self.patterns if regex.fullmatch(content))

 def classify(self,content: str,component: str='') -> Assignment:
  if not content:return Assignment(None,'empty_content',self.version,None,None,0)
  candidates=self.candidates(content)
  if not candidates:return Assignment(None,'unmatched',self.version,None,None,0)
  status='catalogue'
  if len(candidates)>1:
   if not self.resolve_specificity:return Assignment(None,'ambiguous',self.version,None,None,len(candidates))
   ranked=sorted(candidates,key=lambda x:(-len(x[1].replace('<*>','')),x[1].count('<*>'),x[0]))
   score=lambda x:(len(x[1].replace('<*>','')),-x[1].count('<*>'))
   if score(ranked[0])==score(ranked[1]):return Assignment(None,'ambiguous',self.version,None,None,len(candidates))
   event_id,template=ranked[0];status='catalogue_specificity'
  else:event_id,template=candidates[0]
  return Assignment(template_identity(self.dataset,component,self.version,template),status,self.version,template,event_id,len(candidates))
