# #7 experiments findings (subagent report + parent's catalog baseline, saved by parent)

All results are in `results/`. Labels are in `r7e_labels.yaml`; the bias notes are inside that file.

## Retrieval over the 368 Loghub-2.0 templates

Query sets:
- main: 57 queries (30 BGL, 27 OpenStack).
- holdout: 16 queries.

All retrieval scores are MRR.

| Method | main | holdout | Notes |
|---|---|---|---|
| BM25 (SereneDB) | 0.662 | 0.473 | |
| BM25 + hand synonyms | 0.909 | 0.567 | Synonyms were written with knowledge of the queries, so the main score is optimistic. |
| Vector: MiniLM / bge-small / granite | 0.866–0.877 | 0.63–0.79 | |
| RRF(BM25+syn, MiniLM) | — | 0.750 | |
| **Claude reads full catalog, Haiku 5.5** | **0.980** | **1.000** | $0.14 for 73 queries |
| **Claude reads full catalog, Sonnet 5.5** | **0.988** | **0.953** | $2.39 for 73 queries |

- Paired difference, Claude-catalog minus MiniLM (main): Haiku +0.135, CI95 [+0.053, +0.223]; Sonnet +0.144, CI95 [+0.065, +0.229]. Holdout difference: +0.34 / +0.29.
- Paraphrase queries (n = 22): Claude-catalog MRR 0.970.
- Queries with zero word overlap with their templates (n = 14): BM25 0.000, vectors 0.57–0.61.
- **Bias:** the same agent family (Claude) wrote the queries and the labels by reading the catalog. A human must label about 20 fresh queries before we quote numbers.

## Embedding infrastructure
- **IVF vs exact scan at 368 vectors:** the top-10 is identical on 57/57 queries. Exact scan takes 15.1 ms median, IVF 12.9 ms. IVF is unnecessary at this size.
- **ai_embed with Ollama all-minilm:**
  - Works. Needs `SET sdb_ai_allow_insecure_endpoint=true`.
  - base_url must be `host.docker.internal:11434` without `/v1`.
  - 71 ms per call; embedding the 368 templates takes 7.9 s.
  - Vectors are identical to fastembed (cosine ≥ 0.999998).
- **One-statement RRF hybrid in SQL:** 18.4 ms.

## Other uses
- **Problem families:** marginal.
  - BGL k-means: ARI 0.23–0.26 with embeddings vs 0.09–0.13 with TF-IDF.
  - OpenStack: Component alone gives ARI 0.62, better than embeddings.
- **Similar past Incidents:** marginal.
  - Embedding the signature text gives top-1 0.853. The structured Jaccard + Component + spread score gives 0.810 and is explainable.
  - The embedding gain comes from metadata.
- **Raw-line embeddings:** no.
  - 4.7M lines would take about 3.6 h and 7.3 GB.
  - MRR 0.790 vs 0.858 at template level.

## Gotchas
- BM25 needs the `norm` flag on the dictionary.
- `@@ 'a b'` is OR, so rare filler words dominate BM25.
