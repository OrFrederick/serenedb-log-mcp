"""Probe MCP server (official `mcp` 2.3.0, MCPServer) to measure what Claude Code passes to the
model for different tool-result shapes. Each tool puts distinct marker strings in `content`
and `structuredContent` so the stream-json transcript shows which channel reached the model.

Run by Claude Code via probe_mcp.json (stdio).
"""
from pydantic import BaseModel, Field

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations

mcp = MCPServer(
    "probe",
    instructions="Probe server for measuring tool-result handling. Tools return marker strings.",
)
RO = ToolAnnotations(read_only_hint=True, open_world_hint=False)


class Evidence(BaseModel):
    evidence_id: str = Field(description="Ledger id, cite as [E<n>].")
    marker: str
    truncated: bool
    note: str


@mcp.tool(annotations=RO)
def probe_model() -> Evidence:
    """Returns a Pydantic model (SDK auto-generates outputSchema; text = JSON of the model)."""
    return Evidence(evidence_id="E1", marker="MODEL-STRUCT-MARKER", truncated=True,
                    note="NOTE-INSIDE-STRUCTURED: call again with cursor=abc for more")


@mcp.tool(annotations=RO)
def probe_text_default() -> str:
    """Returns a markdown str with default settings (SDK wraps it as {'result': ...})."""
    return "## TEXT-DEFAULT-MARKER\n- item one\n- item two"


@mcp.tool(annotations=RO, structured_output=False)
def probe_text_plain() -> str:
    """Returns a markdown str with structured_output=False (text only)."""
    return "## TEXT-PLAIN-MARKER\n- item one\n- item two"


@mcp.tool(annotations=RO)
def probe_mixed() -> CallToolResult:
    """Returns a hand-built CallToolResult with DIFFERENT content and structuredContent."""
    return CallToolResult(
        content=[TextContent(type="text", text="MIXED-CONTENT-MARKER (markdown table here)"),
                 TextContent(type="text", text="MIXED-SECOND-BLOCK-MARKER (truncation notice)")],
        structured_content={"marker": "MIXED-STRUCT-MARKER"},
    )


@mcp.tool(annotations=RO)
def probe_error_raise(field: str = "time_from") -> str:
    """Raises an exception with an actionable message (expected: isError=true)."""
    raise ValueError(f"RAISE-ERROR-MARKER: parameter '{field}' must be ISO-8601, e.g. 2005-06-03T00:00:00. "
                     "Call describe_corpus to see the valid range.")


@mcp.tool(annotations=RO)
def probe_error_struct() -> CallToolResult:
    """Returns isError=true with text AND structuredContent carrying different markers."""
    return CallToolResult(
        is_error=True,
        content=[TextContent(type="text", text="ERRTEXT-MARKER: unknown component 'KERNAL', did you mean 'KERNEL'?")],
        structured_content={"code": "VALIDATION_FAILED", "marker": "ERRSTRUCT-MARKER"},
    )


@mcp.tool(annotations=RO, structured_output=False)
def probe_large(chars: int = 60000) -> str:
    """Returns a text of the given number of characters (tests the 50,000-char persist-to-disk rule)."""
    head = "LARGE-HEAD-MARKER "
    tail = " LARGE-TAIL-MARKER"
    return head + ("x" * max(0, chars - len(head) - len(tail))) + tail


@mcp.tool(annotations=RO)
def probe_error_toolerror(field: str = "time_from") -> str:
    """Raises mcp.server.mcpserver.exceptions.ToolError (anticipated failure)."""
    from mcp.server.mcpserver.exceptions import ToolError
    raise ToolError(f"TOOLERROR-MARKER: parameter '{field}' must be ISO-8601.")


if __name__ == "__main__":
    mcp.run()  # stdio
