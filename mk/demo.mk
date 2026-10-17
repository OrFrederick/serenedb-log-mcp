# Demo targets (T24): dry-run the demo's tool calls and replay them offline (docs/demo/script.md).

# The beats of the run sheet, and the ledger demo-record writes and demo-replay reads.
DEMO_BEATS ?= docs/demo/beats.yaml
DEMO_LEDGER ?= exports/demo-ledger.jsonl
# Extra arguments for demo_replay.py, for example REPLAY_ARGS="--beat 2" or REPLAY_ARGS="--pause".
REPLAY_ARGS ?=

.PHONY: demo-record demo-replay

demo-record: ## Run and time the demo's tool calls against SereneDB into DEMO_LEDGER (warms the caches)
	$(UV) run python scripts/demo_record.py --beats $(DEMO_BEATS) --ledger $(DEMO_LEDGER) --replace

demo-replay: ## Replay DEMO_LEDGER beat by beat, offline and without SereneDB (demo insurance)
	$(UV) run python scripts/demo_replay.py $(DEMO_LEDGER) --beats $(DEMO_BEATS) $(REPLAY_ARGS)
