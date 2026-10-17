# Benchmark targets (T19, T20, T26). They read DATABASE_URL and SLM_SCHEMA like the server.

# Extra arguments for a benchmark, for example BENCH_ARGS="--time-from 2005-11-01 --time-to 2005-11-08".
BENCH_ARGS ?=

.PHONY: bench-index bench-find-patterns

bench-index: ## Agent index trade-off: build time vs query time saved; writes docs/results/index-tradeoff.md
	$(UV) run python bench/index_tradeoff.py $(BENCH_ARGS)

bench-find-patterns: ## find_patterns vs search_patterns recall on #7's labels (BENCH_ARGS="labels.yaml ..."); see docs/results/find-patterns.md
	$(UV) run --extra vectors python bench/find_patterns_recall.py $(BENCH_ARGS)
