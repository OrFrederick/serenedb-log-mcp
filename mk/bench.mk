# Benchmark targets (T19, T20). They read DATABASE_URL and SLM_SCHEMA like the server.

# Extra arguments for a benchmark, for example BENCH_ARGS="--time-from 2005-11-01 --time-to 2005-11-08".
BENCH_ARGS ?=

.PHONY: bench-index

bench-index: ## Agent index trade-off: build time vs query time saved; writes docs/results/index-tradeoff.md
	$(UV) run python bench/index_tradeoff.py $(BENCH_ARGS)

# The huge-datasets benchmark reads the remote text_bench file in a schema of its own (default huge),
# never in the shared slm schema. `make huge-setup` (mk/data.mk) registers it there first if needed.
HUGE_SCHEMA ?= huge

.PHONY: bench-huge bench-multimodal test-remote

bench-huge: ## Huge datasets: 1B remote rows in place, slices, agent index; writes docs/results/huge-datasets.md
	SLM_SCHEMA=$(HUGE_SCHEMA) $(UV) run python bench/huge_scale.py $(BENCH_ARGS)

bench-multimodal: ## Multimodal: error Log Entry -> trace -> failing span -> metrics on otel_demo; writes docs/results/multimodal.md
	SLM_SCHEMA=$(HUGE_SCHEMA) $(UV) run python bench/multimodal.py $(BENCH_ARGS)

test-remote: ## Run the tests that read the public S3 text_bench and otel_demo files (marker remote; slow, needs network)
	SLM_REMOTE_TESTS=1 $(UV) run pytest -m remote
