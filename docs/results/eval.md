# Eval results

L1 (tool choice) and L2 (grounding) results of `make eval`, one section per model and k, written by `eval/report.py`. Method: `eval/driver.py`, `eval/graders.py`, `eval/questions.yaml`.

<!-- BEGIN pending -->
## No run recorded yet

The harness is in place and tested offline (tests/unit/test_eval_*.py, with a fake `claude` and synthetic trials), but no model has been run yet: a run calls a real model and costs money. The first real run replaces this section. To produce the two runs T17 asks for, with the Base Corpus loaded in SLM_SCHEMA and Claude Code logged in (or `ANTHROPIC_API_KEY` set, then add `EVAL_ARGS=--bare`):

```bash
make eval MODEL=claude-haiku-5-5 K=3    # about $0.20 (#3 estimate)
make eval MODEL=claude-sonnet-5-5 K=1   # about $0.85 (#3 estimate)
```

Each writes `eval/runs/<model>-k<k>/report.md` and its own section of this file. `make eval-dry-run` prints every `claude -p` command and message without calling a model, and `make eval-report` grades an existing run again.
<!-- END pending -->
