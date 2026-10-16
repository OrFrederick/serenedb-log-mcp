"""Agent-side query expansion: an LLM (no access to the template list) rewrites each question into
log keywords. Simulates Claude, the MCP agent, expanding the query before calling the BM25 tool.
Model: gpt-oss-120b via RWTHgpt/KI:connect (free). Output cached in llm_expansions.json."""
import os, json, time, yaml, httpx
L = yaml.safe_load(open("labels.yaml"))
SYS = {"bgl": "You search the RAS log of the IBM Blue Gene/L supercomputer (components KERNEL, APP, DISCOVERY, MMCS, HARDWARE, LINKCARD, MONITOR, BGLMASTER).",
       "openstack": "You search OpenStack Nova logs (nova-api, nova-compute, nova-scheduler)."}
PROMPT = "Rewrite this question into 8 to 15 single-word search keywords that are likely to appear verbatim in the log messages. Output only the keywords separated by spaces.\nQuestion: {q}"
out = json.load(open("llm_expansions.json")) if os.path.exists("llm_expansions.json") else {}
key = os.environ["RWTHGPT_API_KEY"]
lat = []
for ds in ("bgl", "openstack"):
    for item in L[ds]:
        k = f"{ds}::{item['q']}"
        if k in out: continue
        for a in range(5):
            t0 = time.time()
            r = httpx.post("https://chat.kiconnect.nrw/api/v1/chat/completions", headers={"Authorization": f"Bearer {key}"}, timeout=120,
                json={"model": "gpt-oss-120b", "temperature": 0, "messages": [{"role": "system", "content": SYS[ds]}, {"role": "user", "content": PROMPT.format(q=item["q"])}]})
            if r.status_code in (429, 500, 502, 503): time.sleep(3 * 2 ** a); continue
            r.raise_for_status(); break
        lat.append(time.time() - t0)
        out[k] = r.json()["choices"][0]["message"]["content"].strip()
        print(k, "->", out[k], flush=True)
json.dump(out, open("llm_expansions.json", "w"), indent=1)
if lat: print("median latency s", sorted(lat)[len(lat)//2])
