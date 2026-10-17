# Data targets (T4, T9): download the Base Corpus, build the canonical tables, score the Patterns.

# Which adapter maps the loader's raw tables to the canonical tables: dev (the throwaway dev
# loader) or official (the organisers' loader, TODO(organiser), T25).
ADAPTER ?= dev

.PHONY: fetch-data derive score-patterns

fetch-data: ## Download BGL, OpenStack and Loghub-2.0 into SLM_DATA_DIR (idempotent, verifies sizes)
	$(UV) run python scripts/fetch_data.py

derive: ## Build canonical tables, Patterns, Ownership stub and indexes in SLM_SCHEMA (ADAPTER=dev|official)
	$(UV) run python scripts/derive.py --adapter $(ADAPTER)

score-patterns: ## Score the Tier 1 Patterns in SLM_SCHEMA against the Loghub-2.0 labels (gate 99.99%)
	$(UV) run python scripts/score_patterns.py

# The remote Dataset text_bench (T20): a view over one remote Parquet file plus its facts. Not part of
# `make derive`: the SereneDB server needs outbound HTTPS, and the facts pass reads the whole file once.
HUGE_SCHEMA ?= huge

.PHONY: huge-setup

huge-setup: ## Register text_bench (remote Parquet, 1B rows) in HUGE_SCHEMA (default huge; set HUGE_SCHEMA=slm for the server)
	$(UV) run python scripts/huge_setup.py --schema $(HUGE_SCHEMA)

# The remote Dataset otel_demo (T21): views over the logs, traces and metrics of one OTel demo run, its
# facts and the per-minute metric roll-up get_trace reads. Same schema as huge-setup, so a server with
# SLM_SCHEMA=huge sees both remote Datasets. Takes about 85 s plus a minute of bloom-filter warm-up.
.PHONY: otel-setup

otel-setup: ## Register otel_demo (remote logs, traces, metrics) in HUGE_SCHEMA (default huge; set HUGE_SCHEMA=slm for the server)
	$(UV) run python scripts/otel_setup.py --schema $(HUGE_SCHEMA)
