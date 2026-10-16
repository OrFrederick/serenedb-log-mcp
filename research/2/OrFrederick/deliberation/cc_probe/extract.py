import json, sys, re
lines=[json.loads(l) for l in open(sys.argv[1]) if l.strip()]
names={}
out=[]
for m in lines:
    if m.get("type")=="system" and m.get("subtype")=="init":
        out.append({"init": {k:m.get(k) for k in ("claude_code_version","model","tools","mcp_servers")}})
    if m.get("type")=="assistant":
        for b in m["message"]["content"]:
            if b.get("type")=="tool_use": names[b["id"]]=(b["name"], b.get("input"))
            if b.get("type")=="text": out.append({"assistant_text": b["text"]})
    if m.get("type")=="user":
        c=m["message"]["content"]
        if isinstance(c,list):
            for b in c:
                if b.get("type")=="tool_result":
                    raw=json.dumps(b.get("content"))
                    rec={"tool":names.get(b["tool_use_id"]),"is_error":b.get("is_error"),
                         "markers":sorted(set(re.findall(r"[A-Z][A-Z-]*MARKER",raw))),
                         "content": b.get("content") if len(raw)<3000 else raw[:1500]+" ...[trimmed, total %d chars]"%len(raw)}
                    if "tool_use_result" in m: rec["tool_use_result_field(not sent to model)"]=json.dumps(m["tool_use_result"])[:600]
                    out.append(rec)
    if m.get("type")=="result": out.append({"result":{k:m.get(k) for k in ("subtype","num_turns","total_cost_usd","is_error")}})
json.dump(out,open(sys.argv[2],"w"),indent=1)
for o in out:
    print(json.dumps(o)[:900]); print()
