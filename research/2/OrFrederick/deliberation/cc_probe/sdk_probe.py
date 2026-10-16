import asyncio, json, sys, importlib.util
spec = importlib.util.spec_from_file_location("ps", sys.argv[1]); ps = importlib.util.module_from_spec(spec); spec.loader.exec_module(ps)
from mcp import Client
async def main():
    async with Client(ps.mcp) as c:
        tools = await c.list_tools()
        out = {"tools": [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in tools.tools], "calls": {}}
        for t in tools.tools:
            args = {"chars": 200} if t.name == "probe_large" else {}
            r = await c.call_tool(t.name, args)
            out["calls"][t.name] = r.model_dump(mode="json", by_alias=True, exclude_none=True)
        print(json.dumps(out, indent=1))
asyncio.run(main())
