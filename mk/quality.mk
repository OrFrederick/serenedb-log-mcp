# Quality targets (T15, T17): the golden SQL runner and the L1/L2 eval.
#
# The golden tests compare tool results and canonical tables with independent SQL over the full Base
# Corpus in SLM_SCHEMA (tests/golden/, tests/corpus/; marker corpus). Unlike `make test-corpus`, a
# missing corpus fails them instead of skipping them, so a green run means they really ran.
#
# `make eval MODEL=<pinned id> K=<trials>` drives Claude Code (`claude -p`) against the MCP server for
# every question in eval/questions.yaml, grades the trials (eval/graders.py) and writes the report to
# eval/runs/<MODEL>-k<K>/report.md and into its own section of docs/results/eval.md. It calls a real
# model and costs money (#3 estimates for 40 questions: about $0.20 for Haiku k=3, $2.50 for Sonnet
# k=3). It needs Claude Code logged in (or ANTHROPIC_API_KEY) and the Base Corpus in SLM_SCHEMA.
# Trials already in the run directory are kept, so an interrupted run resumes. `make eval-report`
# grades the run again without calling a model; `make eval-dry-run` prints the commands only.

MODEL ?= claude-haiku-5-5
K ?= 3
# Extra arguments for eval/driver.py, for example EVAL_ARGS="--ids eng-01,mgr-02" or "--split holdout".
EVAL_ARGS ?=
EVAL_BUDGET_USD ?= 0.75
EVAL_RUN ?= eval/runs/$(MODEL)-k$(K)
EVAL_RESULTS ?= docs/results/eval.md

.PHONY: test-golden eval eval-report eval-dry-run

test-golden: ## Run every corpus golden test against the full Base Corpus in SLM_SCHEMA (fails, never skips)
	SLM_REQUIRE_DB=1 $(UV) run pytest -m corpus

eval: ## Run the L1/L2 eval with Claude Code (MODEL=<pinned id> K=<trials>; costs money), write the report
	$(UV) run python eval/driver.py --model $(MODEL) --k $(K) --out $(EVAL_RUN) \
		--budget-usd $(EVAL_BUDGET_USD) $(EVAL_ARGS)
	$(UV) run python eval/report.py $(EVAL_RUN) --results $(EVAL_RESULTS)

eval-report: ## Grade an existing eval run again and rewrite its report (no model call)
	$(UV) run python eval/report.py $(EVAL_RUN) --results $(EVAL_RESULTS)

eval-dry-run: ## Print the claude -p command and messages of every eval trial (no model call)
	$(UV) run python eval/driver.py --model $(MODEL) --k $(K) --out $(EVAL_RUN) \
		--budget-usd $(EVAL_BUDGET_USD) --dry-run $(EVAL_ARGS)
