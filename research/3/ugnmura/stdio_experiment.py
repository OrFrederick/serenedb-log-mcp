"""Actual subprocess/stdio protocol checks, separate from in-process adapter."""
import asyncio,importlib.metadata,json,sys,time
from pathlib import Path
from mcp import Client, StdioServerParameters
ROOT=Path(__file__).parent
async def main():
    rows=[]
    for mode in ['legacy','2026-07-28']:
        start=time.perf_counter()
        async with Client(StdioServerParameters(command=sys.executable,args=[str(ROOT/'experiment.py'),'--server']), mode=mode,cache=None) as c:
            tools=await c.list_tools();prompts=await c.list_prompts();p=await c.get_prompt('engineer',{'question':'Investigate token validation'})
            result=await c.call_tool('get_fixture_summary',{'component':'keystonemiddleware.auth_token','limit':1})
            content=result.structured_content
            if 'result' in content:content=content['result']
            assert content['metrics']['matching_entries']==6
            assert not result.is_error and len(prompts.prompts)==3 and len(tools.tools)==1
            assert 'raw lines' in p.messages[0].content.text
            rows.append({'mode':mode,'actual_subprocess':True,'tool_count':len(tools.tools),'prompt_count':len(prompts.prompts),
                         'matching_entries':content['metrics']['matching_entries'],'truncated':content['truncated'],'elapsed_ms':round((time.perf_counter()-start)*1000,2)})
    out={'mcp_version':importlib.metadata.version('mcp'),'results':rows,'model_calls':0,'database_queries':0}
    (ROOT/'stdio-results.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
asyncio.run(main())
