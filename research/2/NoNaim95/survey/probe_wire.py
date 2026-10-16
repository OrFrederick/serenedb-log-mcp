"""Call every probe tool through the official mcp 2.3.0 Client (stdio) and print the raw result shape."""
import anyio, json, sys
from mcp import Client, StdioServerParameters
async def main():
    params = StdioServerParameters(command=sys.executable, args=["-I", sys.argv[1]])
    async with Client(params) as c:
        print("protocol:", c.protocol_version)
        tools = await c.list_tools()
        for t in tools.tools:
            print("TOOL", t.name, "annotations=", t.annotations.model_dump(exclude_none=True) if t.annotations else None,
                  "outputSchema=", "yes" if t.output_schema else "no")
        for t in tools.tools:
            args = {"chars": 60000} if t.name == "probe_large" else {}
            r = await c.call_tool(t.name, args)
            content = [ (b.type, (b.text[:70] + f"...[{len(b.text)} chars]") if len(b.text) > 80 else b.text) for b in r.content]
            print(f"\n{t.name}: is_error={r.is_error}\n  content={content}\n  structured={json.dumps(r.structured_content)[:200] if r.structured_content is not None else None}")
anyio.run(main)
