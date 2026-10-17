# serenedb-log-mcp developer commands. Run `make help` for the list.
.DEFAULT_GOAL := help
SHELL := /bin/bash

UV ?= uv
SERENEDB_IMAGE ?= serenedb/serenedb:26.10.1
# 26.10.1 is published for linux/amd64 only; Apple Silicon runs it under emulation.
SERENEDB_PLATFORM ?= linux/amd64
SERENEDB_CONTAINER ?= serenedb-log-mcp-db
SERENEDB_PORT ?= 7890
# Extra arguments for the dev loader, for example LOAD_ARGS="--replace" or LOAD_ARGS="--schema scratch".
LOAD_ARGS ?=

.PHONY: help sync lint format typecheck test test-db test-corpus check run db-up db-down load-dev load-sample

help: ## List every target with a one-line description
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

sync: ## Install the project and the dev tools into .venv (uv sync)
	$(UV) sync

lint: ## Lint and check formatting (ruff check, ruff format --check)
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format: ## Apply ruff formatting and safe lint fixes
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

typecheck: ## Type-check src/ and scripts/ (mypy strict, pydantic plugin)
	$(UV) run mypy

test: ## Run the pure tests (no SereneDB needed)
	$(UV) run pytest -m "not db and not corpus"

test-db: ## Run the tests that need SereneDB at DATABASE_URL (marker db)
	$(UV) run pytest -m "db and not corpus"

test-corpus: ## Run the tests that need the full Base Corpus (marker corpus)
	$(UV) run pytest -m corpus

check: lint typecheck test ## Run lint, typecheck and the pure tests

run: ## Start the MCP server over stdio
	$(UV) run serenedb-log-mcp --transport stdio

db-up: ## Start a local SereneDB container on port 7890 (trust auth)
	docker run -d --platform $(SERENEDB_PLATFORM) --name $(SERENEDB_CONTAINER) -p $(SERENEDB_PORT):7890 \
		-e SERENEDB_HOST_AUTH_METHOD=trust $(SERENEDB_IMAGE)
	@echo "SereneDB starting at host=localhost port=$(SERENEDB_PORT) dbname=postgres user=postgres"

db-down: ## Stop and remove the local SereneDB container
	docker rm -f $(SERENEDB_CONTAINER)

load-dev: ## Load the full Base Corpus with the throwaway dev loader (SLM_DATA_DIR, DATABASE_URL)
	$(UV) run python scripts/dev_loader.py $(LOAD_ARGS)

load-sample: ## Load the Loghub 2k BGL and OpenStack samples (downloads them on first use)
	$(UV) run python scripts/dev_loader.py --sample $(LOAD_ARGS)
