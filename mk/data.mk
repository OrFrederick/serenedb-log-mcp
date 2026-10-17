# Data targets (T4): download the Base Corpus and build the canonical tables.

# Which adapter maps the loader's raw tables to the canonical tables: dev (the throwaway dev
# loader) or official (the organisers' loader, TODO(organiser), T25).
ADAPTER ?= dev

.PHONY: fetch-data derive

fetch-data: ## Download BGL, OpenStack and Loghub-2.0 into SLM_DATA_DIR (idempotent, verifies sizes)
	$(UV) run python scripts/fetch_data.py

derive: ## Build canonical tables, Ownership stub and indexes in SLM_SCHEMA (ADAPTER=dev|official)
	$(UV) run python scripts/derive.py --adapter $(ADAPTER)

# The remote Dataset text_bench (T20): a view over one remote Parquet file plus its facts. Not part of
# `make derive`: the SereneDB server needs outbound HTTPS, and the facts pass reads the whole file once.
HUGE_SCHEMA ?= huge

.PHONY: huge-setup

huge-setup: ## Register text_bench (remote Parquet, 1B rows) in HUGE_SCHEMA (default huge; set HUGE_SCHEMA=slm for the server)
	$(UV) run python scripts/huge_setup.py --schema $(HUGE_SCHEMA)
