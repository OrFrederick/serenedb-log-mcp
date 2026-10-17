# Documentation targets (T8): the tool reference generated from the live registry.

.PHONY: docs

docs: ## Regenerate docs/tools.md from the live tool registry (a test fails when it is stale)
	$(UV) run python scripts/gen_tool_docs.py
