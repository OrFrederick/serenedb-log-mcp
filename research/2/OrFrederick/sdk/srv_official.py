import sys
from typing import Literal
from pydantic import BaseModel
from mcp.server.mcpserver import MCPServer, Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

class Row(BaseModel):
    key: str
    count: int

class CountResult(BaseModel):
    evidence_id: str
    rows: list[Row]
    truncated: bool

mcp = MCPServer("bgl-official")

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def count_by(dimension: Literal["component", "level"], limit: int = 10, ctx: Context = None) -> CountResult:
    """Count Log Entries grouped by a dimension."""
    if limit == 666:
        raise ToolError("Query timed out after 5s; narrow the time window.")
    if limit == 777:
        raise RuntimeError("secret internal db failure")
    await ctx.info(f"counting by {dimension}")
    await ctx.report_progress(1, 1)
    rows = [Row(key="KERNEL", count=42), Row(key="APP", count=7)][:limit]
    return CountResult(evidence_id="ev_abc123", rows=rows, truncated=False)

@mcp.prompt()
def engineer(question: str) -> str:
    """Engineer role prompt."""
    return f"You are an SRE. Answer with evidence ids: {question}"

@mcp.resource("schema://bgl")
def bgl_schema() -> str:
    """BGL table schema."""
    return "CREATE TABLE bgl (ts TIMESTAMP, level TEXT, component TEXT, msg TEXT)"

if __name__ == "__main__":
    mcp.run(transport="streamable-http", port=int(sys.argv[1]))
