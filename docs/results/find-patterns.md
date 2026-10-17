# find_patterns: does semantic Pattern search help? (T26, #38)

`find_patterns` is an optional tool that ranks the Pattern catalogue by meaning. The template text
of every Pattern (its most frequent Component, a colon, and the template text without the `<*>`
masks) is embedded once with fastembed and `sentence-transformers/all-MiniLM-L6-v2`. That is a local
ONNX model and needs no API key. `make embed-patterns` stores the vectors in SereneDB
(`pattern_vector`, `FLOAT[384]`). At query time the tool embeds only the question. One SQL
statement then returns every Pattern of the Dataset with:

- its exact cosine distance (`emb <=> %s::FLOAT[384]`), and
- in the default `hybrid` mode, its BM25 score from `search_patterns`' lexical match (stemming plus
  the curated synonyms).

The tool fuses the two rankings by Reciprocal Rank Fusion (k = 60).

The scope follows the maintainer's change for #38: it covers the Base Corpus catalogue (465 BGL and
73 OpenStack Patterns), not the dropped OTel track. Vectors live in SereneDB, as #7 decided. There
is no vector index, because #7 measured an exact scan of a few hundred vectors as negligible.

**Short answer.** Perhaps a little, and only in hybrid mode; the numbers are optimistic. On the
124 labelled questions of the two #7 label sets, hybrid `find_patterns` beats `search_patterns`:

- recall@10 is up by **+0.055** (95% CI [+0.016, +0.096]);
- MRR is up by **+0.071** (95% CI [+0.029, +0.117]).

**These intervals are optimistic, for three reasons:**

- **Selection bias.** We chose the embedded text, the model and the default mode on the same 124
  questions, the 16 "holdout" questions included, that the intervals are reported on. There is no
  untouched test set. With the untuned choice (raw template text), hybrid R@10 was 0.721, a gain
  of +0.028 over `search_patterns`, not +0.055 (table below).
- **The holdout disagrees.** On NoNaim95's 16 holdout questions, hybrid R@10 (0.669) is *below*
  semantic (0.691); only its MRR is higher.
- **No multiple-testing correction.** The six pooled comparisons (three pairs, two metrics) are
  each reported at 95% without correction.

Vectors alone (`mode='semantic'`) show no significant gain over `search_patterns`. Any gain is
small next to what #7 measured for Claude reading the whole catalogue with `list_patterns` (MRR
0.98 to 1.00 on the same kind of questions). So `list_patterns` stays the first choice, and
`find_patterns` stays optional.

Measured on 2026-10-18 on SereneDB 26.10.1, on schema `mainslm` (the Base Corpus derived from
`main`, Pattern Tier 1 on), fastembed 0.9.0, all-MiniLM-L6-v2.

## How to reproduce

```
uv sync --extra vectors                 # fastembed and ONNX Runtime
make embed-patterns                     # downloads the model once (~90 MB), embeds 536 Patterns, ~7 s
git show origin/research/7-OrFrederick:research/7/OrFrederick/labels.yaml > orfrederick.yaml
gh api -H 'Accept: application/vnd.github.raw' > nonaim95.yaml \
  'repos/NoNaim95/serenedb-log-mcp/contents/research/7/NoNaim95/experiments/r7e_labels.yaml?ref=research/7-NoNaim95'
make bench-find-patterns BENCH_ARGS="orfrederick.yaml nonaim95.yaml"
SLM_REQUIRE_DB=1 make test-corpus       # pins the examples below, only after the two steps above
```

`tests/corpus/test_find_patterns_corpus.py` carries the `embedding_model` marker: without
`uv sync --extra vectors` and `make embed-patterns` it skips, even with `SLM_REQUIRE_DB=1`, and
pins nothing.

`bench/find_patterns_recall.py` sends every question through the MCP tools in process, as an agent
would call them, with `top=50`. The relevant Loghub-2.0 EventIds map to our Pattern ids through
`pattern.event_id`. Two EventIds of OrFrederick's set (E9, E278) have no Log Entry in the Base
Corpus and so no Pattern; they are dropped from the relevant sets. Tier 2 Patterns, which have no
EventId, count as not relevant.

## Results

R@k is the share of the relevant Patterns found in the top k. cR@10 is the number of hits divided by
min(10, the number of relevant Patterns). Paraphrase questions are those labelled `paraphrase` or
`para`.

| Label set (questions) | Method | R@5 | R@10 | cR@10 | P@5 | MRR | R@10 paraphrase |
|---|---|---|---|---|---|---|---|
| OrFrederick (51) | search_patterns | 0.565 | 0.702 | 0.725 | 0.447 | 0.791 | 0.635 |
| | find_patterns semantic | 0.615 | 0.740 | 0.760 | 0.431 | 0.830 | 0.682 |
| | find_patterns hybrid | 0.637 | 0.755 | 0.774 | 0.475 | 0.876 | 0.706 |
| NoNaim95 main (57) | search_patterns | 0.586 | 0.699 | 0.728 | 0.477 | 0.831 | 0.633 |
| | find_patterns semantic | 0.620 | 0.721 | 0.744 | 0.449 | 0.810 | 0.591 |
| | find_patterns hybrid | 0.661 | 0.764 | 0.790 | 0.498 | 0.865 | 0.681 |
| NoNaim95 holdout (16; also used for selection) | search_patterns | 0.526 | 0.640 | 0.650 | 0.263 | 0.593 | 0.464 |
| | find_patterns semantic | 0.632 | 0.691 | 0.694 | 0.312 | 0.661 | 0.517 |
| | find_patterns hybrid | 0.595 | 0.669 | 0.677 | 0.300 | 0.752 | 0.479 |
| **Pooled (124)** | search_patterns | 0.569 | 0.693 | 0.717 | 0.437 | 0.784 | 0.608 |
| | find_patterns semantic | 0.620 | 0.725 | 0.744 | 0.424 | 0.799 | 0.623 |
| | find_patterns hybrid | **0.643** | **0.748** | **0.769** | **0.463** | **0.855** | **0.662** |
| | find_patterns hybrid, untuned (raw template text) | 0.615 | 0.721 | 0.743 | 0.453 | 0.823 | 0.633 |

The untuned row comes from the throwaway comparison script described below, before the embedded
text was chosen; its pooled values are the question-weighted means of the per-set results
(±0.001).

Paired bootstrap of the pooled differences (5,000 resamples, seed 0; six comparisons, no
multiple-testing correction, and on the questions the choices were made on):

| Comparison | R@10 | MRR |
|---|---|---|
| hybrid − search_patterns | **+0.055 [+0.016, +0.096]** | **+0.071 [+0.029, +0.117]** |
| semantic − search_patterns | +0.032 [−0.023, +0.090] | +0.015 [−0.048, +0.081] |
| hybrid − semantic | +0.023 [−0.016, +0.062] | **+0.056 [+0.007, +0.106]** |

What we chose and why (every choice below was made on these same 124 questions):

- **The embedded text.** Before writing the tool we compared three variants with a throwaway
  script (exact cosine in numpy, the same `search_patterns` calls and RRF): the raw template text,
  the text without `<*>`, and the Component plus the text without `<*>`. Hybrid R@10 over the 124
  questions rose from 0.721 to 0.739 to 0.748 across the three, and MRR from 0.823 to 0.833 to
  0.855, so we embed the Component plus the text.
- **The model.** BAAI/bge-small-en-v1.5 did no better than MiniLM (OrFrederick set, hybrid R@10
  0.749 against 0.742 with raw text), and its best similarity on the "disk full" controls was
  0.71 and 0.78, so its scores spread even less. We kept MiniLM, as #7 recommended.
- **The default mode.** Hybrid is the default because it is the only mode with a gain whose
  interval excludes 0. RRF with *plain* BM25 hurt in #7, but `search_patterns` already expands
  with synonyms, which is the condition #7 found necessary.

### Examples (tests/corpus/test_find_patterns_corpus.py)

| Dataset | Question | search_patterns, top 5 | find_patterns hybrid, top 5 |
|---|---|---|---|
| bgl | nodes overheating | five `Node card ...` / `ciod: ... node` lines, no temperature Pattern | `Temperature Over Limit on link card` first |
| bgl | power supply failure | 2 power Patterns (and idoproxy, ddr) | 4 power Patterns (`Power deactivated`, NodeCard and LinkCard power module, power module status fault) |
| bgl | storage problems | Lustre and NFS mount failures | Lustre mount failures and `unable to mount filesystem` (NFS drops to rank 7) |
| openstack | authentication | the 3 token and identity Patterns | the same 3 on top, then unrelated neighbours |
| bgl | machine got too hot | nothing relevant | nothing relevant (four `machine check` Patterns): both fail |

### Abstention: vectors always answer

On #7's "disk full" controls, where no Pattern is relevant:

- `search_patterns` matched 26 BGL and 19 OpenStack Patterns, because "full" and "disk" occur;
- the best `find_patterns` similarity was 0.37 on BGL and 0.45 on OpenStack.

That is higher than the best match of some genuine paraphrases ("storage problems" 0.36, "VM could
not boot" 0.34), so similarity cannot serve as a cut-off. The tool has no threshold. Its notes say
the rows are candidates, not matches, and repeat the catalogue rule: keep only Patterns that are
direct evidence, otherwise state an Unknown.

## Cost

- **Install:** `uv sync --extra vectors` adds fastembed with ONNX Runtime (75 MB), tokenizers (10
  MB) and their dependencies. The model is about 90 MB in `FASTEMBED_CACHE_PATH` (default
  `~/.cache/serenedb-log-mcp/fastembed`). The default install and the default test suite need
  neither: tests use a hashed bag-of-words stand-in (`tests/support/fake_embedder.py`), and the
  real model runs only in tests marked `embedding_model`, which skip without it.
- **Embedding the catalogue:** 6.9 s for 536 Patterns, including the model load.
- **Query:**
  - loading the model in the server process takes 0.6 s, once;
  - embedding a question takes a median of 1.7 ms;
  - the statement's `query_ms` was a median of 12.4 ms in hybrid mode and 9.5 ms in semantic mode,
    against 7.9 ms for `search_patterns` (15 calls each, "power supply failure", BGL).
  - The query vector is a bound parameter, stored in the Evidence for replay with four decimals.
    That adds about 3k characters to every result.
- **ONNX threads:** fastembed's default thread pool spun for minutes on 465 texts on a loaded host
  (load average around 28). One thread embeds them in under a second, so the embedder pins
  `threads=1`.

## Caveats

- **Who labelled the questions.** The labels are the #7 research labels. Agents wrote them, and the
  same agents wrote the synonym ideas behind `search_patterns`' map. So the lexical side may be
  optimistic, and no human has labelled fresh questions yet (#7's open point 3). Treat the numbers
  as indicative.
- **Which templates.** The labels name Loghub-2.0 templates. Our catalogue adds 148 BGL and 24
  OpenStack Tier 2 Patterns that no label can mark relevant. That lowers the absolute recall of
  every method alike.
- **What we did not compare.** We did not run the catalogue-reading baseline (`list_patterns` plus
  Claude) again here. #7 measured it well above every retrieval method.
