"""Toy stdio MCP server mimicking the planned serenedb-log-mcp tool surface (fake data).
Used only to measure harness mechanics: tool-call capture, token cost, tool-search deferral."""
from mcp.server.mcpserver import MCPServer

app = MCPServer("toylogs")
_n = 0
def _ev(payload):
    global _n; _n += 1
    return {"evidence_id": f"E{_n}", **payload}

@app.tool()
def search_logs(query: str, dataset: str = "bgl", start: str | None = None, end: str | None = None, limit: int = 20) -> dict:
    """Full-text search over Log Entries of one Dataset ('bgl' or 'openstack') in an optional time range.
    Returns at most `limit` matching Log Entries plus total_matches and a truncated flag. Use for 'what failed / show me the lines'."""
    return _ev({"sql": f"SELECT ... WHERE message @@ '{query}'", "dataset": dataset, "total_matches": 1523, "returned": 2, "truncated": True,
                "rows": [{"ts": "2005-06-03T15:42:50", "component": "KERNEL", "node": "R02-M1-N0-C:J12-U11", "message": "data TLB error interrupt"},
                         {"ts": "2005-06-03T15:42:51", "component": "KERNEL", "node": "R02-M1-N0-C:J12-U11", "message": "data TLB error interrupt"}]})

@app.tool()
def get_nearby_events(dataset: str, ts: str, node: str | None = None, window_seconds: int = 60) -> dict:
    """Return Log Entries within +/- window_seconds of a timestamp (optionally same node). Use for 'what happened just before and after'."""
    return _ev({"dataset": dataset, "total_matches": 7, "returned": 7, "truncated": False, "rows": []})

@app.tool()
def count_by(dataset: str, group_by: str, query: str | None = None, start: str | None = None, end: str | None = None) -> dict:
    """Count matching Log Entries grouped by 'component', 'team', 'level' or 'node' over the whole Dataset or a time range. Use for 'how often / which Components or Teams'."""
    return _ev({"dataset": dataset, "group_by": group_by, "rows": [{"key": "KERNEL", "count": 348460}, {"key": "APP", "count": 1200}]})

@app.tool()
def trend(dataset: str, bucket: str = "1 day", query: str | None = None, start: str | None = None, end: str | None = None) -> dict:
    """Count matching Log Entries per time bucket ('1 hour', '1 day', '1 week'). Use for 'is it getting worse / recurring over time'."""
    return _ev({"dataset": dataset, "bucket": bucket, "rows": [{"bucket": "2005-06-03", "count": 1523}, {"bucket": "2005-06-04", "count": 12}]})

if __name__ == "__main__":
    app.run()
