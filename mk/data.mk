# Data targets (T4, T9, T26): download the Base Corpus, build the canonical tables, score and embed the Patterns.

# Which adapter maps the loader's raw tables to the canonical tables: dev (the throwaway dev
# loader) or official (the organisers' loader, TODO(organiser), T25).
ADAPTER ?= dev

.PHONY: fetch-data derive score-patterns embed-patterns

fetch-data: ## Download BGL, OpenStack and Loghub-2.0 into SLM_DATA_DIR (idempotent, verifies sizes)
	$(UV) run python scripts/fetch_data.py

derive: ## Build canonical tables, Patterns, Ownership stub and indexes in SLM_SCHEMA (ADAPTER=dev|official)
	$(UV) run python scripts/derive.py --adapter $(ADAPTER)

score-patterns: ## Score the Tier 1 Patterns in SLM_SCHEMA against the Loghub-2.0 labels (gate 99.99%)
	$(UV) run python scripts/score_patterns.py

embed-patterns: ## Optional, for find_patterns: embed the Pattern catalogue with fastembed (needs `uv sync --extra vectors`)
	$(UV) run --extra vectors python scripts/embed_patterns.py
