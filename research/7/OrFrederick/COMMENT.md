## Research: OrFrederick's Claude

**Recommendation:** Do not put embeddings in the core path. Plain BM25 over templates fails on fuzzy questions: with the question passed through unchanged, recall@10 is 0.56 overall and 0.35 on paraphrases. Embeddings fix this, but so do two options that need no dependencies, and the three tie within noise.
- Vector search with a small local model scores R@10 0.77–0.78.
- Two zero-dependency options reach the same level:
  - A hand-written synonym map in front of BM25 gives 0.79.
  - Letting the agent read the whole template catalogue and pick gives 0.78, with the best precision (P@5 0.58) and MRR (0.98).

For the core tool set (#2) we should therefore:
1. Add a `list_patterns(dataset)` tool that returns the full template catalogue: 320 BGL lines, about 6.7k tokens, and 48 OpenStack lines.
2. Put BM25 with stemming and an optional synonym map behind `search_patterns`.
3. Keep counting in exact SQL by template id.

Verdicts for the four candidate uses:

| Candidate use | Verdict | Measured basis |
|---|---|---|
| Semantic pattern search | Marginal | Large gain over plain BM25 (+0.22 R@10, 95% CI [0.14, 0.32]); no significant gain over the synonym map or catalogue reading |
| Problem families | No | Clustering mostly groups templates that share wording, not problems |
| Similar past Incidents | No for finding repeats, marginal for related categories | Jaccard already finds repeats |
| Raw-line embeddings | No | 4.7M lines cost hours and GBs; distinct masked messages are cheap but add nothing over templates |

If the team wants a visible SereneDB vector feature for the judges, the only defensible form is an opt-in hybrid in `search_patterns`:
- Template vectors stored in SereneDB as `FLOAT[384]` with an IVF index.
- The query embedded locally with fastembed and all-MiniLM-L6-v2: ~103 MB of wheels plus a ~90 MB model, about 5 ms per query, no key.
- Fusion with BM25 by RRF. With the synonym map this gives the best R@10 we measured, 0.835 (+0.046 over synonyms alone, CI [0.00, 0.09]).

SereneDB `ai_embed` works, but it needs a provider endpoint and a key that judges do not have, so it must not be on the clean-checkout path.

**Confidence:** Medium-high.
- The ranking of options is stable across both Datasets and 12 embedding models.
- The main gaps are significant by paired bootstrap; the ties are genuine ties within noise.
- Caveats:
  - I wrote the labels (51 queries) and the synonym map, so the synonym result is optimistic (leakage).
  - The catalogue-reading test used gpt-oss-120b, not Claude.
  - Latency figures were taken on a shared, loaded machine.

### Options compared
| Option | Fit for our use case | Pros | Cons | Evidence |
|---|---|---|---|---|
| BM25 over templates, question passed verbatim | Keyword questions only | Native SereneDB, 8 ms, exact; returns nothing when nothing matches ("disk full" on BGL) | R@10 0.35 on paraphrases, 0.00 on the 8 queries with no shared vocabulary | `retrieval_results.json` |
| BM25 + small synonym map | Good, cheap | +0.23 R@10 over BM25 (CI [0.14, 0.33]); no dependencies | Hand-maintained; my map is optimistic (leakage) | same |
| Agent reads the whole catalogue (`list_patterns`) | **Good, zero install** | R@10 0.78, **P@5 0.58, MRR 0.98** (best); uses the agent we already have | ~6.7k prompt tokens per BGL call; ~2.9 s on gpt-oss-120b | `llm_select_gpt-oss-120b.json` |
| LLM keyword expansion without the catalogue, then BM25 | Weak | No install | R@10 0.69; generic words ("error", "node", "timeout") flood BM25 | `llm_expansions.json` |
| Vector, local (fastembed/ONNX) | Good quality, costs install | MiniLM R@10 0.765, granite-small-r2 0.783; 5–17 ms per query; no key | +103 MB wheels, +67–180 MB model, first-run download; no gain over the two rows above | `models_timing.json` |
| Vector, hosted (RWTHgpt qwen3-embedding-8b) | Best single vector, unusable for judges | R@10 0.814 (+0.03 over granite, CI [−0.01, 0.08], not significant) | Needs a key; 170–240 ms per query; 4096 dims | same |
| Hybrid RRF (BM25+synonyms, local vector) | Best measured | R@10 0.835, MRR 0.93 | Needs the local-vector install | `retrieval_results.json` |
| SereneDB `ai_embed` | Works, wrong for a clean checkout | All in SQL; verified end to end | Provider and key needed; per-row network calls | `ai_embed_test.json` |
| IVF / HNSW index at 368 vectors | Unnecessary | IVF 3.1 ms, recall 1.0 against exact | Exact scan is already 13.8 ms | `ivf_vs_exact.json` |

### Experiments run
All code and outputs are at https://github.com/OrFrederick/serenedb-log-mcp/tree/research/7-OrFrederick/research/7/OrFrederick. They ran on SereneDB 26.09.1.3 (shared container) with `r7_` objects. The unit is the set of Loghub-2.0 templates: 320 BGL and 48 OpenStack.

**1. Labelled query set (`labels.yaml`).**
- 27 BGL and 24 OpenStack questions, plus one "disk full" control per Dataset with no relevant template. Each question lists its relevant template ids and the reason.
- Relevance rule: the template is direct evidence of the asked phenomenon. Register-dump continuation lines do not count.
- 25 questions are tagged paraphrase and 26 keyword. The 8 lexical-gap queries are measured, not judged: plain BM25 retrieves no relevant template for them.

**2. Retrieval (`eval_retrieval.py`).**
- BM25 setup: an inverted index over `r7_templates.body` with a legacy dictionary, queried with `to_tsquery` (implicit OR) and ranked by `BM25(idx.tableoid)`. Median 8 ms.
  - Dictionary: `(TEMPLATE='text', locale='en_US.UTF-8', case='lower', stemming=true)`. The documented `AS split_text(...)` form is a syntax error on 26.09.1.3 (re-confirmed).
- Vectors: exact cosine search. SereneDB exact scan and numpy give the same top-10 (overlap 1.0).
- Hybrid: RRF with k = 60.
- Means over 51 queries:

| Method | R@5 | R@10 | P@5 | MRR | R@10 paraphrase | R@10 lexical gap |
|---|---|---|---|---|---|---|
| BM25 | 0.511 | 0.558 | 0.369 | 0.698 | 0.348 | 0.000 |
| BM25 + LLM keywords (no catalogue) | 0.532 | 0.685 | 0.392 | 0.669 | 0.558 | 0.433 |
| BM25 + synonyms | 0.703 | 0.789 | 0.537 | 0.859 | 0.755 | 0.850 |
| LLM selects from full catalogue | **0.749** | 0.782 | **0.584** | **0.980** | 0.744 | 0.825 |
| vec all-MiniLM-L6-v2 | 0.643 | 0.765 | 0.467 | 0.844 | 0.699 | 0.675 |
| vec granite-embedding-small-r2 | 0.670 | 0.783 | 0.490 | 0.871 | 0.686 | 0.691 |
| vec qwen3-embedding-8b (RWTHgpt) | 0.706 | 0.814 | 0.533 | 0.886 | 0.737 | 0.724 |
| RRF(BM25, granite) | 0.637 | 0.751 | 0.459 | 0.827 | 0.645 | 0.566 |
| **RRF(BM25+synonyms, granite)** | 0.726 | **0.835** | 0.541 | 0.926 | **0.790** | 0.840 |

- Paired bootstrap of R@10 differences:

| Comparison | Difference | 95% CI |
|---|---|---|
| granite − BM25 | +0.225 | [0.14, 0.32] |
| synonyms − BM25 | +0.231 | [0.14, 0.33] |
| granite − synonyms | −0.006 | [−0.08, 0.07] |
| catalogue − granite | −0.001 | [−0.08, 0.07] |

- RRF with plain BM25 is *worse* than the vector alone: BM25's noisy OR matches dilute the fusion. Hybrid only pays off once the BM25 side is expanded.
- On the "disk full" controls, BM25 returns **nothing** on BGL. That is the honest answer. Every vector model returns three neighbours, for example "unable to mount filesystem".

**3. Embedding models (`embed.py`, `models_timing.json`).** All models ran through fastembed 0.9.0 on ONNX Runtime 1.31, except the two hosted ones via RWTHgpt.

| Model | R@10 (vector) | Size | Query latency | Note |
|---|---|---|---|---|
| granite-small-r2 | 0.783 | 180 MB | 13 ms | |
| bge-base-v1.5 | 0.769 | 210 MB | 42 ms | |
| all-MiniLM-L6-v2 | 0.765 | 90 MB | 5 ms | |
| bge-small-v1.5 | 0.763 | 67 MB | 17 ms | |
| arctic-embed-s | 0.731 | 130 MB | 19 ms | |
| jina-v2-base-code | 0.727 | 640 MB | 32 ms | code/domain model, no better |
| e5-base-v2 | 0.721 | 440 MB | 50 ms | |
| nomic-embed-text-v1.5 | 0.704 | 520 MB | 52 ms | |
| e5-small-v2 | 0.692 | 134 MB | 8 ms | |
| potion-retrieval-32M | 0.647 | 130 MB | 0.1 ms | static |
| qwen3-embedding-8b, hosted | 0.814 | none | 241 ms | 4096 dims |
| e5-mistral-7b, hosted | 0.392 | none | 169 ms | probably a prompt or pooling mismatch on the endpoint; not investigated |

- Install weight of fastembed: +103 MB of wheels (onnxruntime alone is 75 MB) and the venv grew from 245 to 365 MB. On top of that comes the model download on first run, 15–30 s each.
- Ollama: per a mid-task user direction I did not use it. It was installed (51 MB) before that direction arrived.
- `ai_embed` (`ai_embed_test.py`) works end to end:
  - Secret: `CREATE SECRET r7_rwth (TYPE openai, base_url 'https://chat.kiconnect.nrw/api', api_key '…')`. It was dropped afterwards and the key was never logged.
  - First call 812 ms, then 327 ms.
  - Embedding all 368 templates into a `FLOAT[4096]` table with `ai_embed(body, 'qwen3-embedding-8b', 'r7_rwth')::FLOAT[4096]` took 10.0 s.
  - kNN in SQL with `emb <=> ai_embed(...)` took 523 ms and returned memory-manager and DDR templates for "memory problems".
  - Friction: every user needs an endpoint and a key. Hosted pay-per-use APIs were not called (user constraint).

**4. Problem families (`families.py`, `families_clusters_*.md`).**
- Method: agglomerative clustering (cosine, average linkage) of the BGL template vectors, with k equal to the number of labels.

| Method | ARI vs Component (k=14) | ARI vs alert family (KERN/APP/LINK/MON/MMCS/MAS) |
|---|---|---|
| TF-IDF | −0.09 | 0.10 |
| Best embedding | 0.38 (jina-code) | 0.23 (MiniLM, bge-small) |
| Random | 0.00 | 0.03 |

- Embeddings beat lexical clustering, but the absolute agreement is low.
- Sense-check at k = 25 with granite: one cluster holds **117 of 320** templates and mixes DDR, TLB, torus and machine-check errors. Other clusters form by surface prefix, such as everything starting with `ciod:`.
- Not useful for a Team Lead. BGL already ships expert families: 41 alert categories with prefixes. OpenStack has only 48 templates, which can be grouped by hand.

**5. Similar past Incidents (`similar_incidents.py`, `incidents.sql`).**
- Incidents were built with #1's definition (key = alert category, gap 300 s). This reproduces **1,202**.
- Signature: the template set plus Component plus Spread. For each Incident I took its nearest *earlier* Incident and counted a hit when the category matched.

| Method | Hit@1 |
|---|---|
| Jaccard over template sets | **0.984** |
| Embedding (granite) | 0.993 |
| Embedding (MiniLM) | 0.991 |
| Spread only | 0.421 |
| Random | 0.146 |

- For the 40 first-of-their-category Incidents, Jaccard correctly scores 0 ("nothing similar before"). Embeddings report a median cosine of 0.94 (granite) to an unrelated Incident: a false "look-alike".
- Across categories, embeddings do surface some sensible relatives that Jaccard cannot (Jaccard = 0 for all): KERNMNT↔KERNMNTF, KERNDTLB↔KERNTLBE, KERNPOW↔KERNSERV, MASABNORM↔MASNORM. Others are arbitrary, such as KERNSTOR→KERNPROG.
- Caveat: the categories were assigned from message text, so templates carry the label almost by construction.

**6. Raw-line cost cliff (`raw_cost.py`).**

| Dataset | Lines | Distinct raw | Distinct masked (digits/hex → #) |
|---|---|---|---|
| BGL | 4,747,963 | 358,357 | **4,216** |
| OpenStack | 207,820 | 139,789 | **2,691** |

| Target | Time | Storage |
|---|---|---|
| All 4.7M BGL lines, MiniLM local (measured 22 lines/s on a loaded host) | 59 h | 7.3 GB float32, 1.8 GB sq8 |
| All 4.7M BGL lines, RWTHgpt (37/s) | 36 h | 78 GB at 4096 dims |
| 4,216 distinct masked BGL messages, local | ~3 min | small |

- Even at an optimistic 1,000 lines/s on an idle machine, embedding every line takes 1.3 h.
- Masked messages are about 13× finer than templates but carry the same vocabulary. That changes no answer, and counting still has to be exact SQL. Not worth it.

**7. IVF versus exact at 368 vectors (`ivf_vs_exact.py`).**
- Syntax verified in the docs and by running it: `CREATE INDEX … USING inverted (dataset, template_id, emb ivf (metric = 'cosine'))`, plus `hnsw (metric = 'cosine')`.

| Method | Build | Median query | Recall@10 vs exact |
|---|---|---|---|
| IVF | 165 ms | 3.1 ms | 1.0 |
| HNSW | 108 ms | 2.8 ms | 0.96 |
| Exact (`ORDER BY emb <=> q`, sequential scan) | none | 13.8 ms | 1.0 |

- `EXPLAIN` shows `IRESEARCH_SCAN` with a Top-10 for the index and `ORDER_BY` over 368 rows for the base table.
- All three are negligible next to agent latency. If we index, use IVF, which kept full recall here while HNSW lost a little.

### Sources
- BEIR (Thakur et al., NeurIPS 2021): https://arxiv.org/abs/2104.08663. BM25 is a robust zero-shot baseline, and dense retrievers often underperform out of domain.
- LogQA (Huang et al. 2023): https://arxiv.org/abs/2303.11715. Acc@5 on logs: BM25 0.26/0.22/0.02, BERT cosine 0.61/0.45/0.14, a log-trained retriever 0.72/0.62/0.62. Jaccard beats both untrained retrievers on Spark. This is the only head-to-head log retrieval result we found.
- RRF (Cormack et al., SIGIR 2009): https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf
- NeuralLog (Le & Zhang, ASE 2021): https://arxiv.org/abs/2108.01955 ; LogBERT (Guo et al. 2021): https://arxiv.org/abs/2103.04475 ; SemParser: https://arxiv.org/abs/2112.12636 ; LILAC (FSE 2024): https://arxiv.org/abs/2310.01796 ; LogLLM: https://arxiv.org/abs/2411.08561. Semantics help detection and parsing, not keyword-versus-dense search.
- EnrichLog (2025): https://arxiv.org/abs/2512.11997. The choice of embedder barely matters for log RAG (F1 98.4–99.1 across four embedders).
- Incident linking: iPACK (ICSE 2023), https://arxiv.org/abs/2302.09520, where text-only matching misses duplicates; DiLink (2024), https://arxiv.org/abs/2403.18639, where text embeddings help only when combined with a dependency graph.
- SereneDB docs: https://serenedb.com/docs/sql/functions/ai (ai_embed, secrets), https://serenedb.com/docs/sql/indexes/inverted/vector-search (IVF, HNSW, `<=>`), https://serenedb.com/docs/configuration/secrets_manager , https://serenedb.com/docs/quick-start (hybrid example)
- Model cards: https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2 , https://huggingface.co/BAAI/bge-small-en-v1.5 , https://huggingface.co/nomic-ai/nomic-embed-text-v1.5 , https://huggingface.co/intfloat/e5-small-v2
- Loghub-2.0 templates: Zenodo 8275861. Oliner & Stearley, DSN 2007 (1,202 filtered alerts).

### Risks / unknowns
- **Label bias.** One judge (me) wrote the labels and the synonym map after seeing the templates. The synonym result is an upper bound. The vector and catalogue results do not depend on the map.
- **R@10 is capped** for broad questions: "memory problems" has 34 relevant templates. The comparisons between methods are fair, but the absolute values understate coverage.
- **Catalogue reading was tested with gpt-oss-120b**, not Claude. Guess: Claude does at least as well. It costs about 6.7k tokens per BGL catalogue read, and a session needs it once.
- **Latency on a shared host.** Other agents and Docker were running, so local embedding throughput (22 lines/s) is far below an idle machine. Relative ordering only.
- e5-mistral's poor result is probably a configuration issue at the endpoint and was not investigated.
- Retrieval quality on the official loader's templates is unknown, because we used the Loghub-2.0 templates. Our own template miner (#3/#6) may produce different text.

### Open questions for the humans
1. Should `list_patterns` (full catalogue to the agent) be the primary pattern-finding path? It ties with embeddings at zero install cost.
2. Do we want the opt-in hybrid (MiniLM via fastembed, vectors in SereneDB with IVF) purely as a "SereneDB features used" showcase? Cost: about 200 MB of extra install and a first-run download. Benefit: about +0.05 R@10 over synonyms, and a demo moment.
3. Should a curated synonym map live in the repo, given that it is the cheapest component that clearly helps (+0.23 R@10)?
4. Should the "disk full" behaviour (BM25 returns nothing, vectors always return something) go into the tool description as a reason to prefer lexical search for Evidence?
