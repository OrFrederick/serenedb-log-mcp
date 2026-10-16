import sys, json, anyio
sys.path.insert(0, __file__.rsplit("/",1)[0])
from mcp import Client
from srv_official import mcp as server

def j(x): return json.dumps(x, default=str)
async def main(target):
    logs=[]
    async def logcb(p): logs.append(p.data if hasattr(p,'data') else p)
    async with Client(target, logging_callback=logcb, log_level="debug") as c:
        print("protocol", c.protocol_version)
        tools = await c.list_tools()
        for t in tools.tools:
            print("TOOL", t.name)
            print(" input", j(t.input_schema if hasattr(t,'input_schema') else t.inputSchema))
            print(" output", j(getattr(t,'output_schema', None) or getattr(t,'outputSchema',None)))
            print(" annotations", t.annotations.model_dump(exclude_none=True, by_alias=True))
        r = await c.call_tool("count_by", {"dimension": "component", "limit": 5})
        print("OK structured", j(r.structured_content), "isError", r.is_error)
        print("OK text", [x.text for x in r.content])
        for args in ({"dimension": "host"}, {"dimension":"level","limit":"ten"}, {}, {"dimension":"level","limit":666}, {"dimension":"level","limit":777}):
            r = await c.call_tool("count_by", args)
            print("CALL", args, "isError", r.is_error, "->", [x.text for x in r.content], "structured", r.structured_content)
        print("PROMPTS", [(p.name, [(a.name, a.required) for a in p.arguments]) for p in (await c.list_prompts()).prompts])
        gp = await c.get_prompt("engineer", {"question": "why KERNEL errors?"})
        print("GETPROMPT", [(m.role, m.content.text) for m in gp.messages])
        print("RESOURCES", [(r.uri, r.mime_type) for r in (await c.list_resources()).resources])
        rr = await c.read_resource("schema://bgl")
        print("READ", [x.text for x in rr.contents])
        print("LOGS", logs)
anyio.run(main, sys.argv[1] if len(sys.argv)>1 else server)
