# Quality targets (T15; T17 adds eval): the golden SQL runner.
#
# The golden tests compare tool results and canonical tables with independent SQL over the full Base
# Corpus in SLM_SCHEMA (tests/golden/, tests/corpus/; marker corpus). Unlike `make test-corpus`, a
# missing corpus fails them instead of skipping them, so a green run means they really ran.

.PHONY: test-golden

test-golden: ## Run every corpus golden test against the full Base Corpus in SLM_SCHEMA (fails, never skips)
	SLM_REQUIRE_DB=1 $(UV) run pytest -m corpus
