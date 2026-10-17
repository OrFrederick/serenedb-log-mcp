# Log template research — ugnmura's Codex, issue #6

## Recommendation

Use the **full Loghub-2.0 template dictionaries as deterministic matchers** for the fixed BGL/OpenStack Base Corpus, added as a sidecar enrichment after the supplied loader. Keep unmatched, ambiguous and empty-content Log Entries in explicit diagnostic partitions. Do not row-join annotated CSVs onto raw files. Freeze a checksum-versioned catalogue; use a full hash of Dataset, Component, parser version and template text as the durable id. Use Drain3 as **discovery** for new sources/unmatched messages, with review before promotion into stable trends.

This is a recommendation from measured raw-corpus coverage, and from actual over-merging in Drain3 output. It is not a claim that catalogue match coverage equals semantic correctness. In particular, broad wildcard templates can group normal and abnormal variable values; the original level, label, status, latency and identifiers must remain independently filterable.

## Scope and reproducibility

All code and outputs are in `research/6/ugnmura/`, branch `research/6-ugnmura`. The benchmark streams compressed archives without extracting or writing multi-million-row outputs. Hardware observed with `sysctl`: Apple A18 Pro, 8,589,934,592 bytes memory; macOS 26.6.2 arm64. Python 3.12.13. Dependencies pinned in `requirements.txt`: Drain3 0.9.11, cachetools 4.2.1, jsonpickle 1.5.1. One process executes each method sequentially; background machine activity is not isolated, so these are reproducible commands and observed wall times, not a controlled cross-machine performance guarantee. Downloads/install are excluded, compressed I/O, header extraction and counting are included. Regex matching uses a bounded 50,000-message LRU, whose hits/misses are recorded. Drain uses `sim_th=.5`, depth4 BGL/depth5 OpenStack, Component partition, no model eviction, our shared typed pre-mask and frozen `match(...,full_search_strategy='fallback')`.

From a clean checkout:

```sh
python3 research/6/ugnmura/fetch_data.py --data /tmp/serenedb-data > /tmp/manifest.json
uv venv /tmp/serenedb-parser-venv --python 3.12
uv pip install --python /tmp/serenedb-parser-venv/bin/python -r research/6/ugnmura/requirements.txt
/tmp/serenedb-parser-venv/bin/python research/6/ugnmura/benchmark.py --data /tmp/serenedb-data
/tmp/serenedb-parser-venv/bin/python research/6/ugnmura/benchmark.py --data /tmp/serenedb-data --quality
```

Authoritative download URLs, archive MD5 verified against Zenodo, archive SHA-256 and sizes are in `results/corpus-manifest.json`. Per-source decompressed SHA-256, line counts and stream-order SHA-256 are in each `results/*-raw.json`. No clean row join exists between raw and annotated corpora: raw BGL4,747,963/OpenStack207,820 versus annotated BGL4,631,261/OpenStack207,632. Quality evaluation reads annotated Content directly, separately from raw coverage/throughput. Catalogue-versus-annotation scores are in-domain consistency checks using the dictionaries that generated those annotations, not held-out accuracy or evidence of generalisation. Annotated CSVs contain only LineId, Content, EventId, EventTemplate; the annotated quality experiment therefore uses **one unpartitioned miner**, not the Component-partitioned raw benchmark.

## Coverage and timing

See `results/BGL-raw.json` and `results/OpenStack-raw.json`; final concise tables are generated into `comment.md`. Every physical log line is in the denominator. Empty Content is tracked explicitly; it does not receive a fabricated curated template. Header failures are auxiliary flags and are not interchangeable with unmatched-template counts. Whole raw lines that cannot have their headers parsed are retained for diagnostics, so their discovery templates are not trustworthy message-only templates.

`catalogue.py` provides an importable matcher returning a typed `Assignment`. Default mode gives an id only for a unique match. Optional `resolve_specificity=True` implements Loghub's published priority (more static characters, then fewer wildcards); score ties remain ambiguous. The benchmark records overlap counts **before** this resolution so uncertainty cannot disappear in a claimed coverage percentage. It uses literal-escaped regex with `<*>` translated to `.*?` and full-message matching, not unrestricted interpolation of template text as executable regex.

## Published accuracy, without mixing populations

The [Loghub-2.0 RQ2 CSV](https://github.com/logpai/loghub-2.0/blob/ac4aad2ea86f561e5a0acbc6587fa28b487259f9/RQs_experiments/RQ2/effectiveness_results.csv) reports each value as `2k / full`; table below uses **full** values. GA counts Log Entries only when the predicted and true groups contain exactly the same entries; PA checks exact template/parameter extraction; FGA gives equal importance to templates through precision/recall. These address different failure modes. The [paper](https://arxiv.org/html/2308.10828v2) defines these metrics and warns about frequent patterns hiding rare-pattern failure.

| Parser | BGL GA / PA / FGA | OpenStack GA / PA / FGA |
|---|---|---|
| Drain | .919 / .407 / .624 | .752 / .029 / .007 |
| AEL | .915 / .406 / .587 | .743 / .029 / .682 |
| IPLoM | .896 / .338 / .692 | .375 / .000 / .029 |
| Spell | .677 / .235 / .016 | .781 / .000 / .001 |
| LenMa | not completed in reported full benchmark | .851 / .019 / .395 |
| LogMine | .644 / .097 / .247 | not completed in reported full benchmark |
| UniParser | .549 / .949 / .641 | 1.000 / .516 / 1.000 |
| LogPPT | .311 / .855 / .519 | .534 / .408 / .929 |
| SLCT | not completed in reported full benchmark | 1.000 / .128 / .946 |

The same benchmark's **average** full GA leader is AEL(.860), PA/FGA leader is LogPPT(.760/.590), but their BGL/OpenStack tradeoffs differ sharply. These are tuned original Drain results, not measured Drain3 results and not our pre-mask variant. Full unpartitioned annotation measurements for our exact code are separate in `*-annotation-quality.json`; reported PA there is a normalized-exact proxy converting typed masks to `<*>` and normalizing whitespace, not the authors' full correction pipeline. There is no accuracy claim for unannotated raw-only lines.

[Brain's official logparser README](https://github.com/logpai/logparser/tree/d9d4180784cde9afef990eeeb458591011933f9b/logparser/Brain) reports **2k** grouping accuracy BGL .986/OpenStack1.0; [ULP](https://github.com/logpai/logparser/tree/d9d4180784cde9afef990eeeb458591011933f9b/logparser/ULP) reports2k BGL .930/OpenStack .4915. Neither is present in that full15-parser CSV, so these are not substituted for full-corpus GA/PA/FGA.

A selected published [LILAC full-run artefact](https://github.com/logpai/LILAC/blob/main/result/LILAC_full_complex%3D0_frequent%3D0_32_3_gpt-3.5-turbo-0613.csv) reports BGL GA .894/PA .958/FGA .859 and OpenStack1.0/1.0/1.0. It uses GPT-3.5-turbo-0613,32 examples, selected setting3; it is neither our benchmark nor proof of current API reproducibility. High published PA does not remove API dependency or ground-truth-version differences.

## Options compared

| Option | Mechanism and fit | Determinism, maintenance, dependency/licence | Decision |
|---|---|---|---|
| Full catalogue regex | Known-domain pattern assignment; real raw coverage measured | Standard-library classifier; immutable catalogue and specificity version; Loghub research/academic citation conditions | Recommended baseline for fixed Base Corpus; visible unmatched/ambiguity |
| Deterministic masking | UUID/IP/long hex/numeric normalization and exact grouping | Stable rules; no ML dependencies; counts every line but may merge error/status codes and fragment paths/words | Baseline/fallback candidates, not trusted semantic parsing |
| Drain / Drain3 | Fixed-depth tree, online generalization, frozen matching | Original Drain Apache2; Drain3 MIT,3 installed distributions; PyPI .9.11 stale release but repository pushed2025-02-04 | Good unseen-pattern discovery; measured over-merging blocks sole truth source |
| Brain | Bidirectional tree around frequent words | Apache2; logparser ecosystem and batch/pandas/numerical dependency chain; 2k evidence strong, full evidence not established here | Plausible research alternative, no need to discover known catalogue again |
| Spell | Online longest-common-subsequence grouping | Apache2; logparser implementation; weak full rare-pattern FGA here | Reject for Base Corpus primary key |
| IPLoM | Iterative partitioning by length/token positions | Apache2; batch framework; BGL FGA better than Drain but OpenStack weak | Reject combined-corpus primary parser |
| LenMa | Word-length vector similarity clustering | BSD; numerical/scientific stack; incomplete full BGL benchmark | Reject primary parser |
| AEL | Heuristic abstraction and merging | Apache2; strong mean GA, weak exact templates; output depends on rules | Better cheap competitor than assuming Drain wins; still below known-catalogue fit |
| LogMine | Hierarchical similarity clustering/alignment | GPL; numerical dependencies, missing full OpenStack result | Poor reproducibility/performance evidence for this deadline |
| ULP | Sample-derived groups, local frequency analysis | MIT, scientific Python; weak OpenStack2k grouping evidence | Not primary choice |
| UniParser / LogPPT | Learned token classifications/prompt fine-tuning | Training artefacts and large model frameworks; ground-truth/seed dependence | Published full leaders vary by metric; unnecessary setup for known dictionaries |
| LILAC / modern LLM parsers | LLM template proposal + cache; semantic distinction and unseen vocab | API/model/version and stochastic output; extra caches/sample selection; LILAC GitHub licence unspecified in inspected API metadata | Optional offline reviewer of candidates; commit deterministic generated catalogue only |
| ByteBrain-LogParser | Hierarchical clustering, deduplication, positional similarity and hash encoding | Primary2025 paper exists; public licensed implementation not located in our search; do not quote throughput as local measurement | Promising industrial design, not a reproducible dependency yet |
| SereneDB numeric masking | Regex normalization then aggregate | Native regex replacement still must verify against docs and actual query; same semantic loss as Python masks | Possible move of reviewed rules into DB after loader/schema arrives |
| SereneDB n-grams/clustering | Similarity retrieval and candidate grouping | N-gram search does not establish a stable equivalence relation/template; no native template-miner function verified here | Useful discovery/retrieval, not a measured replacement for catalogue assignment |

Original mechanisms are sourced in the [logparser project](https://github.com/logpai/logparser) and [Loghub benchmark paper](https://arxiv.org/html/2308.10828v2). Individual licences are documented in [LICENSE.md](https://github.com/logpai/logparser/blob/d9d4180784cde9afef990eeeb458591011933f9b/LICENSE.md). Drain3 behavior, mutable training vs perfect frozen matching and optional persistence are in its [README](https://github.com/logpai/Drain3/blob/0fb6d6e1828838fa05c8f6fa5cda878e02ea79c7/README.md); the installed distribution is version-pinned, not latest GitHub source. Current metadata snapshots are in `sources/repository-metadata.json`.

For newer work, the primary [ByteBrain2025 paper](https://arxiv.org/html/2504.09113v1), [LogParser-LLM2024](https://arxiv.org/abs/2408.13727), [LibreLog2024](https://arxiv.org/abs/2408.01585), [LUNAR2025](https://github.com/logpai/LUNAR), [PIPLUP2025](https://arxiv.org/abs/2508.09366), and [SCOPE2026](https://arxiv.org/abs/2603.27247) establish active alternatives. LUNAR searches contrasting messages and requests semantic templates without labelled demonstrations; SCOPE combines POS-level matching with LLM fallback; PIPLUP is configuration-free statistical parsing. These are candidate-generator alternatives, not locally measured winners. LogParser-LLM and LibreLog reduce LLM work through grouping/memory, but using an API does not become deterministic merely because it has a cache. No unsupported universal2026 leaderboard claim is made.

## Integration, new patterns and limits

`pattern-tool-design.md` gives the issue#2 interface/schema recommendations. Keep SQL aggregations and trends in SereneDB, run enrichment after the official loader, preserve the original raw entry id, and join dictionary assignments by that id. Storage logical fields: `log_entry_id, dataset, component, parser_version, template_id, assignment_status, candidate_count`; dictionary stores `template_text` once. Exact SQL waits for the supplied schema and SereneDB docs MCP verification. No invented SQL appears in this report.

Use model/version-aware ids, never raw Drain cluster numbers. Frozen matching must not mutate counts/history. New or unknown OTel patterns go into a separate Drain3 candidate stream, with novelty counts, first seen and example entry ids; review and freeze a new catalogue version before comparing historical trends. Resetting/retraining a miner in different order may change both clusters and ids; keeping config, input ordering, state snapshots and exact source checksums is necessary if Drain is ever promoted.

Risk: a template can group different severities or parameter values. Keep error/status/latency predicates separate; preserve such values in fields even if the template treats them as variables. Inspect every top recurring **problem** template against raw examples from each Dataset and time period. Unmatched lines include valuable traceback diagnostics and must not disappear. One template count is a count of Log Entries, not incidents or downtime.

Dataset usage requires Loghub-2.0 ISSTA2024/Loghub ISSRE2023 citations and the included notice `sources/Loghub2-LICENSE.txt`; BGL also cites Oliner & Stearley, DSN2007 per project brief. Do not assume that research/academic dataset permission grants unrestricted production redistribution. LILAC's inspected repo declares no licence; model service costs and judge connectivity are unmeasured. The independent [SereneDB integration](https://github.com/OrFrederick/serenedb-log-mcp/blob/research/2-ugnmura/research/2/ugnmura/pattern-sql-results.json) now confirms these strict assignment partitions and GROUP BY joins over all 4,955,783 Base Corpus Log Entries. Python extraction timers remain distinct from SereneDB query latency.

## Open human decisions

1. Accept catalogue-first enrichment for the fixed Base Corpus, with unresolved/empty entries explicit and independently counted.
2. Decide whether to enable the documented specificity resolver for overlap cases, after its diagnostics and examples are reviewed.
3. Confirm how the official loader identifies rows and represents empty Content/continuation lines before production enrichment.

Supplementary2025–2026 source verification: [PIPLUP's official repository](https://github.com/mooselab/PIPLUP-A-Configuration-Free-Statistic-Based-Log-Parser) resolves (last pushed2026-02-12, GitHub licence unspecified); [SCOPE's](https://github.com/Fan-Dongyi/scopeLog) resolves (MIT, pushed2026-01-29). [LogBatcher](https://github.com/LogIntelligence/LogBatcher) uses batching/cache and an API; parent independently verified MIT and a GPT-3.5-turbo-0125 default. Cross-paper Drain OpenStack numbers differ (PIPLUP PA.020, original benchmark PA.029; SCOPE GA.224, original GA.752), so a universal leaderboard from mixed tables would be invalid. Paper throughput claims remain separate from our measured execution. ByteBrain code/licence remain unknown; absence from one GitHub search is not proof that code is closed source.

SereneDB native-source verification supplied by parent: docs MCP read [fuzzy search](https://serenedb.com/docs/cookbook/search/fuzzy-search#levenshtein-matching-with-ts_levenshtein). `ts_ngram` requires single-size n-grams (`min_gram=max_gram`), default threshold0.7; it retrieves similar text, rather than assigning stable templates. No native template-miner/clustering function was located; this is a search outcome, not a claim that none exists.

Ecosystem breadth: logparser also exposes Logram (n-gram dictionaries, Apache2), NuLog (learned masked-language modelling, BSD3), SHISO, SLCT, LFA, LogCluster, LogSig and MoLFI; its root README is a benchmark wrapper, not a single production streaming dependency or a uniform licence. The published full CSV already records Logram/SHISO/SLCT and several failures/timeouts, so bundling every implementation adds dependency/licence weight without establishing better Base Corpus grouping. LogPAI's separate `logmatch` can match known templates; our small classifier makes explicit the unresolved/empty/versioned-id contract needed by the MCP server, rather than adding the whole batch framework. These are source survey observations from the pinned project tree/licence and benchmark, not additional executed parser benchmarks.
