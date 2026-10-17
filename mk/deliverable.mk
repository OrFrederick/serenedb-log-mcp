# Deliverable targets (T18): the worked investigation "Start-up path failures".
#
# `make worked-investigation` runs the three Role questions through Claude Code (`claude -p`) with one
# shared Evidence Ledger and writes docs/worked-investigation.md with export_investigation. It calls a
# real model (WI_MODEL) and needs Claude Code logged in and the Base Corpus in SLM_SCHEMA.
# `make worked-investigation-export` renders the report again from the committed ledger and answers
# (no model, no database); `make worked-investigation-replay` re-runs the committed ledger against
# SereneDB and checks the numbers in the answers.

WI_MODEL ?= claude-opus-5-5
WI_DIR ?= docs/worked-investigation

.PHONY: worked-investigation worked-investigation-export worked-investigation-replay

worked-investigation: ## Run the worked investigation with Claude Code (WI_MODEL; calls a model) and write the report
	$(UV) run python scripts/run_worked_investigation.py --model $(WI_MODEL) --out $(WI_DIR)

worked-investigation-export: ## Render docs/worked-investigation.md again from the committed ledger and answers
	$(UV) run python scripts/run_worked_investigation.py --export-only --out $(WI_DIR)

worked-investigation-replay: ## Re-run the committed worked-investigation ledger against SereneDB and check its answers
	$(UV) run python scripts/replay_ledger.py $(WI_DIR)/ledger.jsonl --answers $(WI_DIR)/answers.yaml
