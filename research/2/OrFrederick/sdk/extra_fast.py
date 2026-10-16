import sys, anyio
sys.path.insert(0, __file__.rsplit("/",1)[0])
from fastmcp import FastMCP, Client
from fastmcp.tools import ToolResult
import srv_fast
m = FastMCP("masked", mask_error_details=True)
m.mount(srv_fast.mcp)
@srv_fast.mcp.tool
def count_compact() -> srv_fast.CountResult:
    r = srv_fast.CountResult(evidence_id="ev_x", rows=[srv_fast.Row(key="KERNEL", count=42)], truncated=False)
    return ToolResult(content="KERNEL=42 (ev_x)", structured_content=r.model_dump())
async def main():
    async with Client(m) as c:
        r = await c.call_tool("count_by", {"dimension":"level","limit":777}, raise_on_error=False)
        print("masked:", r.content[0].text)
        r = await c.call_tool("count_compact", {})
        print("compact:", r.content[0].text, r.structured_content)
anyio.run(main)
