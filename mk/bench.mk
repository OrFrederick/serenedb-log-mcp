# Benchmark targets (T19, T20). They read DATABASE_URL and SLM_SCHEMA like the server.

# Extra arguments for a benchmark, for example BENCH_ARGS="--time-from 2005-11-01 --time-to 2005-11-08".
BENCH_ARGS ?=

.PHONY: bench-index

bench-index: ## Agent index trade-off: build time vs query time saved; writes docs/results/index-tradeoff.md
	$(UV) run python bench/index_tradeoff.py $(BENCH_ARGS)
