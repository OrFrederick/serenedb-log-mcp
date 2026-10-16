"""Parse `claude -p --output-format stream-json --verbose` output into tool-call trace + cost."""
import json, sys
for path in sys.argv[1:]:
    calls, tools_avail, servers = [], [], []
    res = {}
    for line in open(path):
        m = json.loads(line)
        if m.get("type") == "system" and m.get("subtype") == "init":
            tools_avail = [t for t in m.get("tools", []) if t.startswith("mcp__") or t == "ToolSearch"]
            servers = m.get("mcp_servers"); model = m.get("model")
            ntools = len(m.get("tools", []))
        if m.get("type") == "assistant":
            for b in m["message"]["content"]:
                if b.get("type") == "tool_use":
                    calls.append((b["name"], b["input"]))
        if m.get("type") == "result":
            res = m
    print("==", path, "model", model, "n_builtin+mcp tools", ntools, "servers", servers)
    print("  mcp/ToolSearch tools in init:", tools_avail)
    for c in calls: print("  CALL", c[0], json.dumps(c[1])[:160])
    u = res.get("usage", {})
    print("  turns", res.get("num_turns"), "dur_ms", res.get("duration_ms"), "cost_usd", res.get("total_cost_usd"))
    print("  usage", {k: u.get(k) for k in ("input_tokens","cache_creation_input_tokens","cache_read_input_tokens","output_tokens")})
    print("  modelUsage", json.dumps(res.get("modelUsage"))[:400])
    print("  answer:", (res.get("result") or "")[:300].replace("\n"," | "))
