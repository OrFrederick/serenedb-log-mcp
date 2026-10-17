#!/usr/bin/env python3
"""Render issue comment from measured JSON. Does not post anywhere."""
import json
from pathlib import Path
p=Path(__file__).parent
raw={d:json.loads((p/'results'/f'{d}-raw.json').read_text()) for d in ['BGL','OpenStack']}
quality={d:json.loads((p/'results'/f'{d}-annotation-quality.json').read_text()) for d in ['BGL','OpenStack']}
url='https://github.com/OrFrederick/serenedb-log-mcp/tree/research/6-ugnmura/research/6/ugnmura'
rows=[]
for d,j in raw.items():
 c=j['catalog'];dr=j['drain3'];m=j['mask']
 rows.append(f"| {d} | {j['lines']:,} | {c['unique_match']:,} ({100*c['unique_match_coverage']:.4f}%) | {c['no_match']:,} | {c['multiple_matches']:,} | {c['seconds']:.3f}s / {c['lines_per_second']:,.0f} | {m['seconds']:.3f}s / {m['lines_per_second']:,.0f} | {dr['total_seconds']:.3f}s / {dr['end_to_end_lines_per_second']:,.0f} |")
qrows=[]
for d,j in quality.items():
 for method in ['mask','drain3','catalogue']:
  r=j[method];qrows.append(f"| {d} | {method} | {r['lines']:,} | {r['GA']:.4f} | {r['PA']:.4f} | {r['FGA']:.4f} | {r['unknown']:,} |")
text=f'''## Research: ugnmura's Codex

**Recommendation:** Use the full Loghub-2.0 BGL/OpenStack template dictionaries as deterministic, checksum-versioned regex matchers, added **after the official loader** as a sidecar keyed by its Log Entry id. Keep empty, unmatched and ambiguous lines visible and counted; use Drain3 to discover **candidate** new patterns, including OTel, before review and catalogue promotion. Never row-join annotated CSVs onto the raw corpus. Keep Component, level, labels and parameter fields for filtering; a template count counts Log Entries, not incidents.

**Confidence:** High in the measured coverage/throughput and deterministic classifier behavior on these exact files; medium in semantic usefulness until top problem patterns and overlaps are reviewed. Match coverage does not establish semantic accuracy or future OTel coverage.

### Options compared

| Option | Fit for our use case | Pros | Cons | Evidence |
|---|---|---|---|---|
| Full annotated-catalogue regex | Best fixed-corpus baseline | Standard-library, stable ids, high raw coverage | Unseen patterns stay unmatched; broad wildcards still need parameter filters | Full raw tests below; [classifier]({url}/catalogue.py) |
| Regex masking | Useful fallback candidates | Fast and deterministic | Fragments paths/text; masking numeric status/error codes can merge meaning | Full raw test + full annotation test below |
| Drain/Drain3 | Candidate discovery | Streaming, freeze/infer, MIT Drain3, three tiny installed distributions | Mutable/order-dependent learning; measured merging of VM Started/Paused/Resumed and spawn/destroy | [Drain3 docs](https://github.com/logpai/Drain3), experiments |
| Brain, Spell, IPLoM, LenMa, AEL, LogMine, ULP | Alternatives surveyed | Different frequency/LCS/partition/similarity tradeoffs | Strong 2k scores do not establish full-corpus quality; scientific dependency stack; licences vary | [full survey]({url}/report.md), [published full results](https://github.com/logpai/loghub-2.0/blob/main/RQs_experiments/RQ2/effectiveness_results.csv) |
| LILAC, LogPPT/UniParser, LUNAR, LibreLog, LogParser-LLM, LogBatcher | Offline semantic template proposals | Published strong semantic/template results; caches reduce model calls | Models/API/artefacts, stochasticity, costs and licence checks; not locally benchmarked here | [survey and primary sources]({url}/report.md) |
| ByteBrain 2025, PIPLUP 2025, SCOPE 2026 | Active newer alternatives | Industrial hierarchy or newer statistical/semantic approaches | No same-setting local comparison; ByteBrain public code/licence not located; cross-paper tables differ | [survey]({url}/report.md) |
| SereneDB regex/n-gram/clustering | Aggregate reviewed ids/rules; discover similar text | Search and counts stay in SereneDB | N-gram similarity alone does not define a stable template; no native template-miner verified | SQL validation remains separate from Python timing |

### Experiments run

**All raw physical lines**, compressed streaming, no extracted corpus/large CSV; Apple A18 Pro, 8GB, macOS 26.6.2, CPython 3.12.13. Wall times include archive reads, header extraction and counts, exclude download/install. Background machine activity was not isolated. Catalogue matching has a bounded 50k message cache. Drain3 0.9.11 uses pre-masks, Component partition, sim_th=.5, depth4/5, then a full frozen pass with fallback search. Both raw corpora are processed in full by every method.

| Dataset | Raw lines | Unique catalogue matches | No match | Overlap | Catalogue seconds / lines/s | Mask seconds / lines/s | Drain train+infer seconds / lines/s |
|---|---:|---:|---:|---:|---:|---:|---:|
'''+'\n'.join(rows)+f'''

Overlap is counted **before** optional published longest-static/fewer-wildcard resolution; equal-specificity ties stay ambiguous. All residual lines remain queryable. BGL has{raw['BGL'].get('empty_content',0):,} empty Content rows, not fabricated curated templates. OpenStack has{raw['OpenStack']['header_unparsed']:,} unparsed physical continuations; those and unmatched-template counts are different diagnostics.

Full **annotated** Content-only quality is a separate experiment (different population; no row join). Annotated CSV lacks Component, so these quality models are **unpartitioned**, unlike the raw throughput miner. GA/FGA are exact group-set measures; PA here is a normalized-exact proxy with typed masks converted to `<*>` and whitespace normalized, not the original paper's full correction pipeline. Catalogue quality uses documented specificity resolution. Those dictionaries produced the annotations: this is an in-domain consistency check, not held-out accuracy or unseen-pattern generalisation.

| Dataset | Method | Annotated lines | GA | PA proxy | FGA | Unassigned |
|---|---|---:|---:|---:|---:|---:|
'''+'\n'.join(qrows)+f'''

For comparison, original Loghub-2.0 **published** full Drain BGL GA/PA/FGA=.919/.407/.624 and OpenStack=.752/.029/.007. AEL BGL=.915/.406/.587, OpenStack=.743/.029/.682. These are tuned original Drain/AEL under the authors' setup, not our Drain3 measurements. Brain/ULP 2k figures and newer papers are not substituted for full-corpus measurements. [Full source-backed table and caveats]({url}/report.md).

Reproduce from a clean checkout:

```sh
python3 research/6/ugnmura/fetch_data.py --data /tmp/serenedb-data
uv venv /tmp/serenedb-parser-venv --python 3.12
uv pip install --python /tmp/serenedb-parser-venv/bin/python -r research/6/ugnmura/requirements.txt
/tmp/serenedb-parser-venv/bin/python research/6/ugnmura/benchmark.py --data /tmp/serenedb-data
/tmp/serenedb-parser-venv/bin/python research/6/ugnmura/benchmark.py --data /tmp/serenedb-data --quality
```

[Code, exact JSON outputs and source snapshots]({url}); [archive URL/size/MD5/SHA256 manifest]({url}/results/corpus-manifest.json). Decompressed per-source SHA256s and line counts are in each raw JSON. Catalogue ids hash Dataset+Component+catalogue version+template text; dictionary CSV checksum forms the parser version. [Issue#2 tool/schema implications]({url}/pattern-tool-design.md). Five classifier contract tests passed (`python -m unittest discover -s research/6/ugnmura -p test_catalogue.py -v`).

### Sources

- [Loghub-2.0 paper, metrics and matching priority](https://arxiv.org/html/2308.10828v2); [full annotations](https://zenodo.org/records/8275861); [raw corpora](https://zenodo.org/records/8196385).
- [Logparser tools/licences](https://github.com/logpai/logparser), [Drain3](https://github.com/logpai/Drain3), [LILAC](https://github.com/logpai/LILAC), and the newer primary papers/repos linked in [report]({url}/report.md).
- Dataset citations: Jiang et al.,ISSTA 2024; Zhu et al.,ISSRE 2023; BGL Oliner & Stearley,DSN 2007. Included Loghub notice applies to derived catalogue artefacts.

### Risks / unknowns

- Dataset licensing is research/academic with citation/notice conditions; LILAC repo's inspected licence is unspecified. API model costs/current availability and ByteBrain code availability remain unknown.
- Full-corpus raw-only unmatched lines lack ground truth. Catalogue recall outside BGL/OpenStack is unmeasured. Drain training 100% assignment is not accuracy.
- Official loader/schema and Ownership Table are still missing. Final row keys and post-loader SQL need them. SQL/query timings must be verified through SereneDB docs and executed queries; these Python timers are not database query timings.
- Top-N tools must report `COUNT(*)` totals including unresolved lines, catalogue version, assignment method, diagnostic counts and remainder. Keep original error/status/latency values; do not infer incidents, downtime, financial or customer effects from template counts.

### Open questions for the humans

Accept catalogue-first enrichment with visible residuals? Enable documented specificity resolution after reviewing overlaps? Which official loader key identifies an original Log Entry and how are empty/continuation lines represented?
'''
(p/'comment.md').write_text(text)
print(p/'comment.md')
