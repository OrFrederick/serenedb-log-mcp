import sys, anyio
sys.path.insert(0, __file__.rsplit("/",1)[0])
from typing import Annotated
from mcp import Client, StdioServerParameters
from mcp_types import CallToolResult, TextContent
from srv_official import mcp, CountResult, Row

@mcp.tool()
def count_compact(limit: int = 10) -> Annotated[CallToolResult, CountResult]:
    res = CountResult(evidence_id="ev_x", rows=[Row(key="KERNEL", count=42)], truncated=False)
    return CallToolResult(content=[TextContent(type="text", text="KERNEL=42 (ev_x)")], structured_content=res.model_dump())

async def main():
    async with Client(mcp) as c:
        t = [t for t in (await c.list_tools()).tools if t.name=="count_compact"][0]
        print("compact outputSchema title:", (t.output_schema or {}).get("title"))
        r = await c.call_tool("count_compact", {})
        print("compact:", r.content[0].text, r.structured_content)
    async with Client(mcp, mode="legacy") as c:
        print("legacy protocol", c.protocol_version, (await c.call_tool("count_by", {"dimension":"level"})).structured_content)
    p = StdioServerParameters(command=sys.executable, args=["-I","-c","import sys;sys.path.insert(0,%r);from srv_official import mcp;mcp.run()" % __file__.rsplit("/",1)[0]])
    async with Client(p) as c:
        print("stdio", c.protocol_version, (await c.call_tool("count_by", {"dimension":"level"})).structured_content)
anyio.run(main)
