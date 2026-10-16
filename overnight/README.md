# Overnight run

Two Claudes finish the research deliberation (Phase 1), then plan, ticket, implement and cross-review the project (Phase 2) without human input. Roles: `NoNaim95` is the leader, `OrFrederick` the follower. The full protocol is in [PROMPT.md](PROMPT.md).

## Files
- `run.sh`: supervisor. Runs startup checks, then headless `claude -p` in a loop. It sleeps through usage limits and resumes the same session, and starts a fresh session after 3 failures in a row. It stops when `state/<login>/DONE` exists.
- `PROMPT.md`: the protocol both Claudes follow. It is re-read on every restart, so a fix pushed to `main` reaches the next session.
- `guard.py`: PreToolUse hook (overnight only). It blocks `rm` outside the repo and caches, pushes to `main`, plain force-push, `sudo`, and repo, issue and release deletes.
- `watch.sh`: exits when PRs or issues change, so a waiting Claude wakes up.
- `state/<login>/`: gitignored. Holds the session id, notes, the copied handoff, logs and generated settings.

## What the supervisor changes
- It runs Claude with `--permission-mode bypassPermissions --permission-prompts none`.
- It loads your `~/.claude/settings.json` **without** `permissions.ask`. Headless mode denies every `ask` rule, so push, PR and comment rules would otherwise stall the run. Your plugins and `deny` rules are kept, and the guard hook is added.
- It uses `--setting-sources project,local --settings state/<login>/settings.json`. Your real settings files are not modified.
- Model: Opus at effort xhigh, with Sonnet as fallback. Override with `OVERNIGHT_MODEL` and `OVERNIGHT_EFFORT`.
- It keeps the Mac awake with `caffeinate` while it runs. Keep the lid open, or use clamshell mode with power connected.

## Launch (each human, in a terminal, not the Desktop app)
1. Stop any other Claude session working in this repo.
2. Save the handoff of your running session to a file, for example `~/overnight-handoff.md`.
3. `git checkout main && git pull`
4. Optional: `OVERNIGHT_DRY_RUN=1 overnight/run.sh ~/overnight-handoff.md` runs only the checks.
5. Start the run:
   ```bash
   overnight/run.sh ~/overnight-handoff.md
   ```
   The checks print `Found handoff: ...`, the role and the settings. The first Claude output line also says `Found handoff: <path>`.

Follow progress in the `Overnight coordination` issue (label `overnight`), in `state/<login>/supervisor.log`, and with `tail -f` on the newest `state/<login>/logs/run-*.jsonl`. Stop with Ctrl-C.

## In the morning
Read `docs/overnight-report.md`, `docs/overnight-decisions.md`, and the issues and PRs labelled `needs-human-review` and `blocked-human`.
