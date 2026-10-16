"""Drive server.py over stdio with the official mcp client; check outputSchema, annotations,
structuredContent; measure response sizes (~tokens = chars/4) and wall time per call."""
import asyncio
import json
import sys
import time
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

HERE = Path(__file__).parent
PY = "/Users/deni/hackathon/tools/.venv/bin/python"

CALLS = [
    ("describe_corpus", {}),
    ("search_logs", {"query": "ciod failed", "dataset": "bgl", "limit": 20}),
    ("search_logs", {"query": "ciod failed", "dataset": "bgl", "limit": 50}),
    ("search_logs", {"query": "kernel panic", "mode": "phrase", "limit": 20}),
    ("search_logs", {"query": "interupt", "mode": "fuzzy", "limit": 20, "time_from": "2005-06-01", "time_to": "2005-07-01"}),
    ("search_logs", {"query": "instance", "dataset": "openstack", "levels": ["ERROR", "WARNING"], "limit": 20}),
    ("search_logs", {"query": "error", "components": ["APP"], "levels": ["FATAL"], "order": "newest", "limit": 20}),
    ("search_logs", {"query": "zzqqxx", "limit": 20}),
    ("get_context", {"dataset": "bgl", "line_no": 1577127, "before": 10, "after": 10}),
    ("get_context", {"dataset": "bgl", "line_no": 1577127, "scope": "time_window", "window_seconds": 30, "before": 25, "after": 25}),
    ("get_context", {"dataset": "openstack", "line_no": 201287, "scope": "same_instance", "before": 25, "after": 25}),
    ("get_context", {"dataset": "bgl", "line_no": 1577127, "scope": "same_request"}),  # expected teaching error
    ("count_by", {"group_by": "dataset"}),
    ("count_by", {"group_by": "component", "dataset": "all"}),
    ("count_by", {"group_by": "component", "dataset": "bgl", "query": "error"}),
    ("count_by", {"group_by": "node", "dataset": "bgl", "levels": ["FATAL", "FAILURE"], "limit": 25}),
    ("count_by", {"group_by": "rack", "dataset": "all"}),  # expected teaching error
    ("trend", {"dataset": "bgl", "bucket": "day", "levels": ["FATAL", "FAILURE"]}),
    ("trend", {"dataset": "bgl", "bucket": "hour", "query": "ciod failed"}),  # expected bucket-limit error
    ("trend", {"dataset": "openstack", "bucket": "hour", "levels": ["ERROR"]}),
    ("trend", {"dataset": "bgl", "bucket": "week", "query": "ciod failed"}),
]


async def main():
    params = StdioServerParameters(command=PY, args=[str(HERE / "server.py")], env={})
    out = []
    async with Client(params) as c:
        tl = await c.list_tools()
        listing = [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in tl.tools]
        (HERE / "tools_list.json").write_text(json.dumps(listing, indent=1))
        tl_chars = len(json.dumps(listing))
        print(f"tools/list: {len(listing)} tools, {tl_chars} chars (~{tl_chars // 4} tokens)")
        for t in listing:
            print(f"  {t['name']}: outputSchema={'outputSchema' in t} annotations={t.get('annotations')} "
                  f"desc_chars={len(t.get('description', ''))} schema_chars={len(json.dumps(t.get('inputSchema')))}")
        for name, args in CALLS:
            t = time.perf_counter()
            r = await c.call_tool(name, args)
            ms = (time.perf_counter() - t) * 1000
            d = r.model_dump(mode="json", by_alias=True, exclude_none=True)
            text = "".join(b.get("text", "") for b in d.get("content", []))
            sc = d.get("structuredContent")
            sc_chars = len(json.dumps(sc)) if sc else 0
            ev = (sc or {}).get("evidence", {})
            row = dict(tool=name, args=args, ms=round(ms), is_error=d.get("isError", False), text_chars=len(text),
                       text_tokens=len(text) // 4, sc_chars=sc_chars, sc_tokens=sc_chars // 4,
                       total=ev.get("total_count"), returned=ev.get("returned_count"), truncated=ev.get("truncated"),
                       query_ms=ev.get("query_ms"))
            out.append(row)
            print(json.dumps(row))
            if d.get("isError") or name in ("search_logs",) and args.get("limit") == 20 and args["query"] == "ciod failed":
                print("   TEXT:", text[:900].replace("\n", "\n         "))
        (HERE / "client_results.json").write_text(json.dumps(out, indent=1))
        # sample full outputs for the report
        r = await c.call_tool("get_context", {"dataset": "openstack", "line_no": 201287, "scope": "same_instance", "before": 3, "after": 3})
        (HERE / "sample_get_context.json").write_text(r.model_dump_json(by_alias=True, exclude_none=True, indent=1))


asyncio.run(main())
