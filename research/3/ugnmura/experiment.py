"""No model calls or database queries. Exercises official MCP SDK and graders.
Run with mcp==2.3.0: python experiment.py [--server]
The offline fixture is real OpenStack data; tools are intentionally fixture tools.
"""
import asyncio, copy, importlib.metadata, json, re, sys, time
from pathlib import Path
from typing import Annotated
from pydantic import Field
from mcp import Client
from mcp.server import MCPServer

ROOT = Path(__file__).parent
FIXTURE = json.loads((ROOT / 'fixture.json').read_text())
CALLS = []
server = MCPServer('eval-fixture', instructions='Offline research fixture. Not SereneDB.')

@server.tool(structured_output=True)
def get_fixture_summary(component: str, limit: Annotated[int, Field(ge=1, le=50)] = 10) -> dict[str, object]:
    """Read offline OpenStack counts for one exact Component. Inputs: component, limit 1..50. Outputs: structured counts with provenance. Not a SQL query."""
    rows = [x for x in FIXTURE['counts'] if x['component'] == component]
    result = {'evidence_id': f'E{len(CALLS)+1}', 'source_kind': 'offline_fixture', 'query_sql': None,
              'parameters': {'component':component,'limit':limit}, 'data_scope': 'OpenStack raw archive; all three log files; no deduplication',
              'metrics': {'matching_entries':sum(x['count'] for x in rows)}, 'rows': rows[:limit],
              'total_groups':len(rows),'returned_count':len(rows[:limit]),'truncated':len(rows)>limit}
    CALLS.append({'name':'get_fixture_summary','arguments':{'component':component,'limit':limit},'result':result})
    return result

@server.prompt()
def engineer(question: str) -> str:
    return 'Engineer: retrieve raw lines and nearby context. Observed Facts; Hypotheses; Unknowns; Next Step. Question: '+question

@server.prompt()
def team_lead(question: str) -> str:
    return 'Team Lead: aggregate recurrence, comparable windows, Components and Ownership Table coverage. Observed Facts; Hypotheses; Unknowns; Next Step. Question: '+question

@server.prompt()
def manager(question: str) -> str:
    return 'Manager: rank measured attention needs and state a decision. No inferred financial loss or downtime. Observed Facts; Hypotheses; Unknowns; Next Step. Question: '+question


def grade_answer(answer, ledger, expected):
    """Typed claims, not bag-of-numbers equality. Numeric prose requires separate review."""
    errors=[]
    for section in ('Observed Facts','Hypotheses','Unknowns','Next Step'):
        if section not in answer['sections']:errors.append('missing_section:'+section)
    for claim in answer['claims']:
        eid=claim['evidence_id']; evidence=ledger.get(eid)
        if evidence is None:errors.append('unresolved_citation:'+eid);continue
        key=(claim['metric'],claim['scope'])
        if key not in expected:errors.append('wrong_metric_or_scope');continue
        if claim['value'] != expected[key]:errors.append('wrong_value')
        if claim['scope'] != evidence['data_scope']:errors.append('citation_scope_mismatch')
        if claim['metric'] not in evidence['metrics']:errors.append('metric_absent_from_citation')
        elif claim['value'] != evidence['metrics'][claim['metric']]:errors.append('citation_value_mismatch')
    allowed={c['evidence_id'] for c in answer['claims']}
    for eid in re.findall(r'\[(E\d+)\]',answer['text']):
        if eid not in ledger:errors.append('unresolved_text_citation:'+eid)
    if not answer['claims']:errors.append('no_typed_claims')
    # Assert machine-readable impact claims are absent. Negated/Unknown prose is
    # intentionally not banned by keyword: a semantic/human rubric reviews it.
    if answer.get('financial_loss') is not None or answer.get('downtime') is not None:errors.append('unsupported_impact')
    return sorted(set(errors))


async def exercise(mode):
    CALLS.clear(); started=time.perf_counter()
    async with Client(server, mode=mode, cache=None) as client:
        tools=await client.list_tools(); prompts=await client.list_prompts()
        assert [t.name for t in tools.tools] == ['get_fixture_summary']
        assert {p.name for p in prompts.prompts} == {'engineer','team_lead','manager'}
        t=tools.tools[0]
        assert t.input_schema['properties']['limit']['maximum']==50
        assert t.output_schema is not None
        prompt=await client.get_prompt('manager',{'question':'What should we investigate?'})
        assert 'rank measured' in prompt.messages[0].content.text
        a=await client.call_tool('get_fixture_summary',{'component':'keystonemiddleware.auth_token','limit':1})
        assert not a.is_error
        result=a.structured_content
        # MCP SDK wraps generic dict results differently across compatibility eras.
        if result is not None and 'result' in result:result=result['result']
        if result is None:result=json.loads(a.content[0].text)
        assert result['truncated'] and result['total_groups']==3 and result['returned_count']==1
        assert result['metrics']['matching_entries']==6
        invalid=await client.call_tool('get_fixture_summary',{'component':'x','limit':51})
        assert invalid.is_error
        missing=await client.call_tool('get_fixture_summary',{'limit':1})
        assert missing.is_error
        assert len(CALLS)==1, 'invalid calls must never reach body'
        return {'mode':mode,'tools':[x.name for x in tools.tools],'prompts':[x.name for x in prompts.prompts],
                'output_schema_present':t.output_schema is not None,'valid_call':result,'invalid_limit_is_error':invalid.is_error,
                'missing_component_is_error':missing.is_error,'server_execution_count':len(CALLS),'elapsed_ms':round((time.perf_counter()-started)*1000,2)}


async def main():
    modes=[]
    for mode in ['auto','legacy','2026-07-28']:modes.append(await exercise(mode))
    evidence=modes[0]['valid_call']; scope=evidence['data_scope'];expected={('matching_entries',scope):6}
    valid={'sections':{s:'' for s in ['Observed Facts','Hypotheses','Unknowns','Next Step']},
           'claims':[{'metric':'matching_entries','scope':scope,'value':6,'evidence_id':'E1'}],
           'text':'Observed Facts: 6 matching Log Entries [E1]. Unknowns: cost and downtime are unknown.'}
    assert grade_answer(valid,{'E1':evidence},expected)==[]
    mutants={}
    for name,path,value in [('wrong_value',('claims',0,'value'),7),('dangling_id',('claims',0,'evidence_id'),'E999'),
                            ('same_number_wrong_metric',('claims',0,'metric'),'incident_count'),('wrong_scope',('claims',0,'scope'),'BGL')]:
        a=copy.deepcopy(valid); d=a
        for p in path[:-1]:d=d[p]
        d[path[-1]]=value; mutants[name]=grade_answer(a,{'E1':evidence},expected);assert mutants[name]
    a=copy.deepcopy(valid); del a['sections']['Unknowns'];mutants['missing_section']=grade_answer(a,{'E1':evidence},expected);assert mutants['missing_section']
    a=copy.deepcopy(valid);a['financial_loss']=123;mutants['invented_financial_loss']=grade_answer(a,{'E1':evidence},expected);assert mutants['invented_financial_loss']
    a=copy.deepcopy(valid);a['text']+=' [E20]';mutants['dangling_text_citation']=grade_answer(a,{'E1':evidence},expected);assert mutants['dangling_text_citation']
    # Explicitly demonstrate the residual failure: extra uncaptured natural-language
    # numeric claim is not validated by this schema checker. Never sell it as all-number proof.
    a=copy.deepcopy(valid);a['text']+=' We experienced 9 incidents.'
    residual=grade_answer(a,{'E1':evidence},expected)
    assert residual==[]
    out={'mcp_version':importlib.metadata.version('mcp'),'modes':modes,'grader_positive_cases':1,'mutants_rejected':mutants,
         'residual_failure':{'text':a['text'],'grader_errors':residual,'mitigation':'render measured prose from typed validated claims or add semantic claim extraction plus human review'},
         'model_calls':0,'database_queries':0,'not_measured':['Claude tool selection','Claude answer quality','SereneDB SQL correctness','Desktop prompt UI compatibility']}
    (ROOT/'results.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))

if __name__=='__main__':
    if '--server' in sys.argv:server.run()
    else:asyncio.run(main())
