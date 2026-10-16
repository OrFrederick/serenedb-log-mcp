"""Baseline 'agent reads the catalogue': give an LLM the whole template list of the Dataset (320 BGL / 48 OpenStack
lines) and the question; it returns up to 10 template ids, most relevant first. Simulates Claude calling a
list_patterns tool and choosing. Model gpt-oss-120b via RWTHgpt (free). Cached in llm_select.json."""
import os, json, re, time, yaml, httpx, sys
T = json.load(open("templates.json")); L = yaml.safe_load(open("labels.yaml"))
MODEL = sys.argv[1] if len(sys.argv) > 1 else "gpt-oss-120b"
fn = f"llm_select_{re.sub(r'[^a-z0-9]+','-',MODEL.lower())}.json"
out = json.load(open(fn)) if os.path.exists(fn) else {}
key = os.environ["RWTHGPT_API_KEY"]; lat = []; ptoks = []
for ds in ("bgl", "openstack"):
    cat = "\n".join(f"{t['id']} [{t['component']}] {t['template']}" for t in T[ds])
    for item in L[ds]:
        k = f"{ds}::{item['q']}"
        if k in out: continue
        msg = (f"Here is the complete list of log message templates of the {ds.upper()} log (id [component] template):\n{cat}\n\n"
               f"Question from an engineer: \"{item['q']}\"\nReturn the ids of up to 10 templates whose log lines are direct evidence for the question, "
               "most relevant first, as a JSON array of strings. Return [] if none is relevant. Output only the JSON array.")
        for a in range(6):
            t0 = time.time()
            try:
                r = httpx.post("https://chat.kiconnect.nrw/api/v1/chat/completions", headers={"Authorization": f"Bearer {key}"}, timeout=600,
                           json={"model": MODEL, "temperature": 0, "messages": [{"role": "user", "content": msg}]})
            except httpx.TimeoutException:
                time.sleep(5); continue
            if r.status_code in (429, 500, 502, 503, 504): time.sleep(3 * 2 ** a); continue
            r.raise_for_status(); break
        lat.append(time.time() - t0); j = r.json(); ptoks.append(j.get("usage", {}).get("prompt_tokens"))
        txt = j["choices"][0]["message"]["content"]
        out[k] = re.findall(r"E\d+", txt)
        print(k, out[k], flush=True)
        json.dump(out, open(fn, "w"), indent=0)
if lat: print("median latency s", round(sorted(lat)[len(lat)//2], 2), "prompt tokens", sorted(p for p in ptoks if p)[len(ptoks)//2] if any(ptoks) else None)
