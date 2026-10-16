#!/usr/bin/env python3
"""Minimal client for the SereneDB docs MCP (https://api.serenedb.com/mcp).
Usage: sdocs.py list | sdocs.py call <tool> '<json args>'"""
import json, sys, urllib.request
URL = "https://api.serenedb.com/mcp"
H = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
def post(body, sid=None):
    h = dict(H)
    if sid: h["Mcp-Session-Id"] = sid
    r = urllib.request.urlopen(urllib.request.Request(URL, json.dumps(body).encode(), h), timeout=120)
    sid = r.headers.get("Mcp-Session-Id") or sid
    raw = r.read().decode()
    out = None
    for line in raw.splitlines():
        if line.startswith("data:"):
            out = json.loads(line[5:])
    if out is None and raw.strip():
        out = json.loads(raw)
    return out, sid
init, sid = post({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"cli","version":"0"}}})
post({"jsonrpc":"2.0","method":"notifications/initialized"}, sid) if False else None
try: post({"jsonrpc":"2.0","method":"notifications/initialized"}, sid)
except Exception: pass
if sys.argv[1] == "list":
    res, _ = post({"jsonrpc":"2.0","id":2,"method":"tools/list"}, sid)
    for t in res["result"]["tools"]:
        print(t["name"], "::", t.get("description","")[:300]); print("  schema:", json.dumps(t.get("inputSchema",{}))[:400])
else:
    res, _ = post({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":sys.argv[2],"arguments":json.loads(sys.argv[3])}}, sid)
    for c in res.get("result",{}).get("content",[]): print(c.get("text",c))
    if "error" in res: print(res["error"])
