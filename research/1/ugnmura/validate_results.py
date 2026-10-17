"""Independent stream-versus-SereneDB consistency checks; no raw archive read."""
import json
from pathlib import Path

root=Path(__file__).parent
def read(name):return json.loads((root/name).read_text())
def evidence(name):
    data=read(name)
    errors=[r for r in data if 'error' in r]
    assert not errors,errors
    return {r['name']:r for r in data}
def mapping(counter):return {r['key']:r['n'] for r in counter}

bgl=read('bgl-profile.json'); os=read('openstack-profile.json')
sqlb=evidence('serenedb-bgl-results.json');sqlo=evidence('serenedb-openstack-results.json')
whole={r[0]:r[1] for r in sqlb['whole_base_corpus']['rows']}
assert whole=={'BGL':4747963,'OpenStack':207820},whole
assert sum(whole.values())==4955783
assert {r[0]:r[1] for r in sqlb['bgl_labels']['rows']}==mapping(bgl['labels'])
levels={}
for level,component,n in sqlb['bgl_levels_components']['rows']:levels[level]=levels.get(level,0)+n
assert levels==mapping(bgl['levels']),(levels,bgl['levels'])
for item in bgl['gap_sensitivity']:
    gap=item['gap_seconds']
    category=sqlb[f'bgl_gap_{gap}_category']['rows']
    node=sqlb[f'bgl_gap_{gap}_node_category']['rows']
    assert sum(int(r[2]) for r in category)==item['category_groups']
    assert sum(int(r[2]) for r in node)==item['node_category_groups']
    assert {r[0]:int(r[2]) for r in category}==mapping(item['by_category'])
for gap in [5,300]:
    db={(r[0],r[2],r[3]):r for r in sqlb[f'bgl_top_groups_{gap}']['rows']}
    for group in bgl[f'top_groups_gap_{gap}'][:2]:
        row=db[group['label'],group['start_epoch'],group['end_epoch']]
        assert row[6:10]==[group['n'],group['distinct_nodes'],group['distinct_racks'],group['distinct_midplanes']],row
for source,n,structured,*rest in sqlo['openstack_file_profile']['rows']:
    assert n==os[source]['n'] and structured==os[source]['structured']
for source,level,n in sqlo['openstack_levels']['rows']:
    assert n==mapping(os[source]['levels'])[level]
contexts=sqlo['labelled_instance_context']['rows']
assert len(contexts)==100 and all(r[5]=='INFO' for r in contexts)
assert len(sqlo['build_rows']['rows'])==2066
for expected in os['build_threshold_sensitivity']:
    key=f"build_threshold_{expected['threshold']}"
    assert sqlo[key]['rows']==[[expected['labelled'],expected['unlabelled']]],(key,sqlo[key]['rows'])
for row in sqlb['bgl_paper_boundary_strict_5']['rows']:
    expected=mapping(bgl['published_reproduction_checks']['epoch_break_gte_5']['categories'])
    assert int(row[1])==expected[row[0]]
print('PASS: raw corpus totals, per-label and severity counts, 14 gap definitions, top candidate spread, OpenStack sessions and 10 threshold checks agree between streamed Python and executed SereneDB SQL.')
