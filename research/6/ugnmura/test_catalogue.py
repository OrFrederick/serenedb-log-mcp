import csv,io,tempfile,unittest,zipfile
from pathlib import Path
from catalogue import Catalogue,template_identity

class CatalogueContract(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.archive=Path(self.tmp.name)/'synthetic.zip'
  text=io.StringIO();writer=csv.writer(text);writer.writerow(['EventId','EventTemplate'])
  writer.writerows([['E1','auth failure; uid=<*> ruser=<*>'],['E2','auth failure; uid=<*>'],['E3','disk.error [code=<*>]'],['E4','VM Started'],['E5','VM Stopped'],['E6','a <*> c'],['E7','a b <*>']])
  with zipfile.ZipFile(self.archive,'w') as z:z.writestr('synthetic_templates.csv',text.getvalue())
 def tearDown(self):self.tmp.cleanup()
 def test_lexical_semantics_preserved(self):
  c=Catalogue(self.archive,'Synthetic')
  self.assertNotEqual(c.classify('VM Started','compute').template_id,c.classify('VM Stopped','compute').template_id)
 def test_literal_regex_characters_not_interpreted(self):
  c=Catalogue(self.archive,'Synthetic')
  self.assertEqual(c.classify('disk.error [code=7]').status,'catalogue')
  self.assertEqual(c.classify('diskXerror [code=7]').status,'unmatched')
 def test_empty_and_new_patterns_are_not_curated(self):
  c=Catalogue(self.archive,'Synthetic')
  self.assertIsNone(c.classify('').template_id)
  self.assertEqual(c.classify('').status,'empty_content')
  self.assertEqual(c.classify('unseen timeout').status,'unmatched')
 def test_overlap_has_explicit_provenance(self):
  c=Catalogue(self.archive,'Synthetic')
  self.assertEqual(c.classify('auth failure; uid=7 ruser=root').status,'ambiguous')
  c=Catalogue(self.archive,'Synthetic',resolve_specificity=True)
  a=c.classify('auth failure; uid=7 ruser=root')
  self.assertEqual((a.catalogue_event_id,a.status,a.candidate_count),('E1','catalogue_specificity',2))
  self.assertEqual(c.classify('a b c').status,'ambiguous')
 def test_identity_does_not_alias_components_or_datasets(self):
  c=Catalogue(self.archive,'Synthetic');a=c.classify('VM Started','compute')
  self.assertEqual(a,c.classify('VM Started','compute'))
  self.assertNotEqual(a.template_id,c.classify('VM Started','scheduler').template_id)
  self.assertNotEqual(a.template_id,template_identity('Other','compute',a.parser_version,a.template_text))

if __name__=='__main__':unittest.main()
