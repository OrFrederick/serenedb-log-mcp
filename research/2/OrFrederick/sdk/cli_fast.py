import sys, json, anyio
sys.path.insert(0, __file__.rsplit("/",1)[0])
from fastmcp import Client
from srv_fast import mcp as server
def j(x): return json.dumps(x, default=str)
async def main(target):
    logs=[]
    async def logcb(m): logs.append(m.data)
    async with Client(target, log_handler=logcb) as c:
        for t in await c.list_tools():
            print("TOOL", t.name)
            d = t.model_dump(by_alias=True, exclude_none=True)
            print(" input", j(d.get("inputSchema")))
            print(" output", j(d.get("outputSchema")))
            print(" annotations", d.get("annotations"))
        r = await c.call_tool("count_by", {"dimension": "component", "limit": 5})
        print("OK structured", j(r.structured_content), "isError", r.is_error)
        print("OK .data", repr(r.data))
        print("OK text", [x.text for x in r.content])
        for args in ({"dimension": "host"}, {"dimension":"level","limit":"ten"}, {}, {"dimension":"level","limit":666}, {"dimension":"level","limit":777}):
            r = await c.call_tool("count_by", args, raise_on_error=False)
            print("CALL", args, "isError", r.is_error, "->", [x.text for x in r.content])
        print("PROMPTS", [(p.name, [(a.name, a.required) for a in p.arguments]) for p in await c.list_prompts()])
        gp = await c.get_prompt("engineer", {"question": "why KERNEL errors?"})
        print("GETPROMPT", [(m.role, m.content.text) for m in gp.messages])
        print("RESOURCES", [(str(r.uri), r.mime_type) for r in await c.list_resources()])
        rr = await c.read_resource("schema://bgl")
        print("READ", [x.text for x in rr])
        print("LOGS", logs)
anyio.run(main, sys.argv[1] if len(sys.argv)>1 else server)
